"""Telegram Bot — interface adapter.

Receives messages → emits events → pushes responses.
Stateless.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

from src.core.events import Command, EventType, Event, AggregateType
from src.core.bus import EventBus
from src.core.pipeline import Pipeline
from src.core.state_engine import StateEngine
from src.infrastructure.config import Settings
from src.interface.telegram.router import parse_message, command_to_event
from src.interface.telegram.templates import format_output, format_error, format_help
from src.core.calendar_consistency import (
    run_consistency_review,
    format_review_summary,
    format_repair_summary,
    _is_repairable_schedule_issue,
    _has_repairable_art_conflict,
)

logger = logging.getLogger(__name__)
LOCAL_TZ = ZoneInfo("Asia/Singapore")


def _format_reflection_response(state_engine) -> str:
    """Format reflection state."""
    derived = state_engine.get_all_derived()
    ref = derived.get("reflection", {})
    behavior = derived.get("behavior", {})

    if ref.get("sample_count", 0) < 5:
        return f"需要 5 条以上反馈才能生成反思。当前：{ref.get('sample_count', 0)} 条。试试 /完成、/跳过、/推迟。"

    trend_icons = {"improving": "↗", "declining": "↘", "stable": "→"}
    trend_labels = {"improving": "改善中", "declining": "下降中", "stable": "稳定"}

    lines = ["反思", ""]
    ref_names = {
        "weekly_consistency_trend": "周一致性",
        "deep_work_trend": "深度工作",
        "fatigue_trend": "疲劳度",
    }
    for key, label in ref_names.items():
        val = ref.get(key, "")
        icon = trend_icons.get(val, "")
        label_cn = trend_labels.get(val, val)
        lines.append(f"  {label}：{icon} {label_cn}")
    lines.append(f"  行为漂移：{ref.get('behavior_drift', 0) * 100:.0f}%")
    lines.append("")
    lines.append(f"  计划有效性：{ref.get('planning_effectiveness', 0) * 100:.0f}%")
    lines.append(f"  建议采纳率：{ref.get('recommendation_acceptance_rate', 0) * 100:.0f}%")
    lines.append(f"  样本：{ref.get('sample_count', 0)} 条 / {ref.get('analysis_period_days', 0)} 天")

    return "\n".join(lines)


def _format_trends_response(state_engine) -> str:
    """Format detailed trends."""
    derived = state_engine.get_all_derived()
    ref = derived.get("reflection", {})

    if ref.get("sample_count", 0) < 5:
        return f"需要 5 条以上样本。当前：{ref.get('sample_count', 0)} 条。"

    first = ref.get("first_half_metrics", {})
    second = ref.get("second_half_metrics", {})

    lines = ["趋势拆解（半期对比）：", ""]
    lines.append(f"  一致性：  {first.get('consistency', 0) * 100:.0f}% → {second.get('consistency', 0) * 100:.0f}%")
    lines.append(f"  深度工作：{first.get('deep_work_rate', 0) * 100:.0f}% → {second.get('deep_work_rate', 0) * 100:.0f}%")
    lines.append(f"  疲劳跳过：{first.get('fatigue_skip_rate', 0) * 100:.0f}% → {second.get('fatigue_skip_rate', 0) * 100:.0f}%")
    lines.append("")
    lines.append(f"周期：{ref.get('analysis_period_days', 0)} 天 | {ref.get('sample_count', 0)} 条样本")

    return "\n".join(lines)


def _format_adaptation_response(state_engine) -> str:
    """Format adaptation parameters."""
    derived = state_engine.get_all_derived()
    adp = derived.get("adaptation_params", {})
    params = adp.get("params", {})
    changes = adp.get("changes", [])
    reasons = adp.get("reasons", [])

    if not params:
        return "尚无自适应参数，需要更多反馈数据。"

    from src.derived_state.adaptation_params import get_default_params, get_param_bounds
    defaults = get_default_params()
    bounds = get_param_bounds()

    lines = ["自适应参数：", ""]
    for name, val in params.items():
        d = defaults.get(name, val)
        lo, hi = bounds.get(name, (0, 1))
        marker = "" if abs(val - d) < 0.01 else ("↑" if val > d else "↓")
        lines.append(f"  {name}：{val:.3f} {marker}（范围：{lo:.1f}–{hi:.1f}，默认：{d:.2f}）")

    if changes:
        lines.append("")
        lines.append("最近变更：")
        for c in changes:
            lines.append(f"  {c}")
    elif reasons:
        lines.append("")
        lines.append(f"  {reasons[-1]}")

    return "\n".join(lines)


def _format_adaptive_response(state_engine) -> str:
    """Format adaptive planning state as Telegram message."""
    derived = state_engine.get_all_derived()
    adaptive = derived.get("adaptive_planning", {})
    behavior = derived.get("behavior", {})

    if not adaptive:
        return "暂无自适应数据。先通过 /完成、/跳过、/推迟 提供反馈。"

    intensity = adaptive.get("recommended_intensity", "normal")
    window = adaptive.get("preferred_window_type", "standard")
    confidence = adaptive.get("adaptation_confidence", 0) * 100
    risk = adaptive.get("compliance_risk", 0) * 100
    reasons = adaptive.get("adjustment_reasons", [])

    intensity_icon = {"focused": "🎯", "normal": "✅", "reduced": "🔽", "light": "🔹"}
    intensity_label = {"focused": "聚焦", "normal": "正常", "reduced": "降低", "light": "轻量"}
    window_label = {"deep_work": "深度工作", "standard": "标准", "quick": "快速"}

    total = behavior.get("total_recommendations", 0)

    lines = [
        f"自适应规划 {intensity_icon.get(intensity, '')}",
        "",
        f"  强度：        {intensity_label.get(intensity, intensity)}",
        f"  窗口风格：    {window_label.get(window, '标准')}",
        f"  置信度：      {confidence:.0f}%（基于 {total} 条样本）",
        f"  跳过风险：    {risk:.0f}%",
    ]

    if reasons:
        lines.append("")
        lines.append("原因：")
        for r in reasons[:3]:
            lines.append(f"  {r}")

    return "\n".join(lines)


def _format_patterns_response(state_engine) -> str:
    """Format detected behavioral patterns."""
    derived = state_engine.get_all_derived()
    adaptive = derived.get("adaptive_planning", {})
    behavior = derived.get("behavior", {})

    patterns = adaptive.get("patterns_detected", [])
    total = behavior.get("total_recommendations", 0)

    if total < 3:
        return f"数据不足（{total}/3 条）。请多用几次 /完成、/跳过、/推迟。"

    if not patterns:
        return "暂无显著模式，你的行为看起来比较均衡。"

    pattern_labels = {
        "deep_work_ready": "🎯 深度工作就绪 — 你能可靠完成深度任务",
        "deep_work_resistant": "🔽 抗拒深度工作 — 短任务更适合你",
        "chronic_delayer": "⏰ 习惯性推迟 — 你倾向于延后",
        "fatigue_sensitive": "📉 疲劳敏感 — 疲劳时执行力下降",
        "unreliable_planner": "📋 规划不稳 — 执行跟进率偏低",
    }

    ec = behavior.get("execution_consistency", 0) * 100
    dt = behavior.get("delay_tendency", 0) * 100
    dw = behavior.get("deep_work_success_rate", 0) * 100

    lines = [f"检测到的模式（n={total}）：", ""]
    for p in patterns:
        label = pattern_labels.get(p, p)
        lines.append(f"  {label}")
    lines.append("")
    lines.append(f"  一致性：{ec:.0f}% | 推迟：{dt:.0f}% | 深度工作：{dw:.0f}%")

    return "\n".join(lines)


def _format_behavior_response(state_engine) -> str:
    """Format behavioral feedback state as Telegram message."""
    derived = state_engine.get_all_derived()
    behavior = derived.get("behavior", {})

    if not behavior or behavior.get("total_recommendations", 0) == 0:
        return "暂无行为数据。试试 /完成、/跳过、/推迟 提供反馈。"

    ec = behavior.get("execution_consistency", 0) * 100
    dt = behavior.get("delay_tendency", 0) * 100
    fcd = behavior.get("fatigue_compliance_drop", 0) * 100
    dw = behavior.get("deep_work_success_rate", 0) * 100
    pr = behavior.get("planning_reliability", 0) * 100

    total = behavior.get("total_recommendations", 0)
    acc = behavior.get("accepted_count", 0)
    skip = behavior.get("skipped_count", 0)
    delay = behavior.get("delayed_count", 0)
    comp = behavior.get("completed_count", 0)
    aband = behavior.get("abandoned_count", 0)

    trend = "↗" if ec > 60 else "↘" if ec < 40 else "→"
    ec_label = "高" if ec > 70 else "中" if ec > 40 else "低"

    return (
        f"行为快照 {trend}\n"
        f"  一致性：    {ec_label}（{ec:.0f}%）\n"
        f"  推迟倾向：  {dt:.0f}%\n"
        f"  疲劳下降：  {fcd:.0f}%\n"
        f"  深度工作率：{dw:.0f}%\n"
        f"  可靠性：    {pr:.0f}%\n"
        f"\n"
        f"历史：{total} 条建议 | {acc} 接受，{delay} 推迟，{skip} 跳过\n"
        f"  {comp} 完成，{aband} 放弃"
    )


def _format_cognition_response(command_type: str, state_engine) -> str:
    """Format cognition state as Telegram message."""
    derived = state_engine.get_all_derived()
    cog = derived.get("cognition", {})
    proj = derived.get("temporal_projection", {})

    if not cog:
        return "暂无认知数据，先处理一些作业事件。"

    if command_type == "show_state":
        lines = []
        lines.append("认知状态")
        lines.append(f"  压力：       {cog.get('stress_projection', 0) * 100:.0f}%")
        lines.append(f"  截止压力：   {cog.get('deadline_pressure', 0) * 100:.0f}%")
        lines.append(f"  过载：       {cog.get('workload_overload', 0) * 100:.0f}%")
        lines.append(f"  疲劳风险：   {cog.get('fatigue_risk', 0) * 100:.0f}%")
        lines.append(f"  恢复窗口：   {cog.get('recovery_window', 0):.1f}h 空闲")
        lines.append(f"  待处理：     {cog.get('pending_total', 0)} 个任务")
        lines.append(f"  48h 容量：   {cog.get('next_48h_capacity', 0) * 100:.0f}%")
        return "\n".join(lines)

    if command_type == "show_stress":
        sp = cog.get("stress_projection", 0)
        dp = cog.get("deadline_pressure", 0)
        fr = cog.get("fatigue_risk", 0)

        status = "低" if sp < 0.3 else "中等" if sp < 0.6 else "高" if sp < 0.8 else "严重"
        lines = []
        lines.append(f"压力：{status}（{sp * 100:.0f}%）")
        lines.append(f"  截止压力：{dp * 100:.0f}%")
        lines.append(f"  疲劳风险：{fr * 100:.0f}%")
        lines.append(f"  待处理：{cog.get('pending_total', 0)} 个任务")
        return "\n".join(lines)

    if command_type == "show_capacity":
        n48 = cog.get("next_48h_capacity", 0)
        rw = cog.get("recovery_window", 0)
        dc = proj.get("daily_capacity", 0)
        wl = proj.get("weekly_load", 0)

        cap_status = "充裕" if n48 < 0.7 else "紧张" if n48 < 1.0 else "过载"
        lines = []
        lines.append(f"容量：{cap_status}")
        lines.append(f"  48h 利用率：{n48 * 100:.0f}%")
        lines.append(f"  恢复窗口：  {rw:.1f}h")
        lines.append(f"  今日空闲：  {dc:.1f}h")
        lines.append(f"  周负载：    {wl * 100:.0f}%")
        return "\n".join(lines)

    return "未知命令。"


def _day_relative(date_str: str) -> str:
    """Map ISO date string to relative label: today, tomorrow, in N days."""
    from datetime import datetime, timezone
    try:
        d = datetime.fromisoformat(date_str.replace("Z", "+00:00")).date()
        today = datetime.now(timezone.utc).date()
        delta = (d - today).days
        if delta == 0:
            return "今天"
        if delta == 1:
            return "明天"
        if delta > 1:
            return f"{delta}天后"
        return date_str
    except (ValueError, TypeError):
        return date_str


def _format_planning_response(command_type: str, state_engine) -> str:
    derived = state_engine.get_all_derived()
    planning = derived.get("planning", {})

    if not planning:
        return "暂无规划数据。请先添加课表和作业。"

    if command_type == "plan_today":
        windows = planning.get("recommended_windows", [])
        advice = planning.get("planning_advice", [])
        pending = planning.get("pending_tasks", 0)

        lines = [f"今日计划（{pending} 个待处理任务）："]
        if windows:
            for w in windows:
                lines.append(f"  {w['time']} [{w['type']}] {w['label']}")
        else:
            lines.append("  未找到可用时间窗口。")

        if advice:
            lines.append("")
            for a in advice[:2]:
                lines.append(f"  {a}")

        return "\n".join(lines)

    elif command_type == "plan_tomorrow":
        overloaded = planning.get("overloaded_days", [])

        lines = ["明日展望："]
        tom = [o for o in overloaded if "tomorrow" in _day_relative(o.get("date", ""))]
        if not tom and overloaded:
            tom = overloaded[:1]

        if tom:
            for o in tom:
                lines.append(
                    f"  {o['date']}：{o.get('level', '?').upper()} "
                    f"（{o['density'] * 100:.0f}% 占用，{o['free_hours']}h 空闲）")
        else:
            lines.append("  看起来比较轻松，未检测到过载。")

        pending = planning.get("pending_tasks", 0)
        if pending:
            lines.append(f"  {pending} 个任务待处理 — 查看 /今日计划 获取建议窗口。")

        return "\n".join(lines)

    elif command_type == "focus_window":
        focus = planning.get("focus_windows", [])
        recovery = planning.get("recovery_slots", [])

        lines = ["专注与恢复："]
        if focus:
            lines.append("  专注窗口：")
            for fw in focus:
                quality_label = {"high": "高", "medium": "中", "low": "低"}.get(fw.get("quality", ""), fw.get("quality", ""))
                lines.append(f"    {fw['time']}（{fw['duration_minutes']}min）[{quality_label}]")
        else:
            lines.append("  无专注窗口 — 压力/疲劳过高。")

        if recovery:
            lines.append("  恢复时段：")
            for rs in recovery:
                lines.append(f"    {rs['time']}（{rs['duration_minutes']}min）— {rs['suggestion']}")

        return "\n".join(lines)

    else:
        return "未知命令。"


def _format_temporal_response(command_type: str, state_engine) -> str:
    blocks = state_engine.get_temporal_blocks()
    proj = state_engine.get_temporal_projection()
    free_slots = proj.get("free_slots", [])
    busy_density = proj.get("busy_density", 0)
    weekly_load = proj.get("weekly_load", 0)
    daily_capacity = proj.get("daily_capacity", 0)
    context_switching = proj.get("context_switching_score", 0)

    if command_type == "show_today":
        schedule_blocks = [
            b for b in blocks
            if str(b.source) == "jwxt"
            and str(b.block_type) in {"class_lecture", "class_lab"}
        ]
        if not schedule_blocks:
            return "暂无课表数据。先同步课表。"
        lines = ["今日："]
        now = datetime.now(LOCAL_TZ)
        today_blocks = [b for b in schedule_blocks if b.start.astimezone(LOCAL_TZ).date() == now.date()]
        if today_blocks:
            for b in today_blocks:
                start_t = b.start.astimezone(LOCAL_TZ).strftime("%H:%M")
                end_t = b.end.astimezone(LOCAL_TZ).strftime("%H:%M")
                block_type = getattr(b, "block_type", "")
                type_label = str(block_type).split(".")[-1] if "." in str(block_type) else str(block_type)
                teacher = (getattr(b, "metadata", {}) or {}).get("teacher", "")
                title = f"{b.title}（{teacher}）" if teacher and teacher not in b.title else b.title
                location = b.location or "未提供地址"
                lines.append(f"  {start_t}-{end_t} [{type_label}] {title} @ {location}")
        else:
            lines.append("  今日无课程。")
        return "\n".join(lines)

    elif command_type == "show_free_today":
        if not free_slots:
            return "暂无空闲时段数据。请先添加课表。"
        lines = ["今日空闲："]
        for s in free_slots[:6]:
            st = s["start"][11:16]
            et = s["end"][11:16]
            lines.append(f"  {st}-{et}（{s['duration_minutes']}min）")
        if busy_density > 0:
            lines.append(f"  繁忙度：{busy_density * 100:.0f}%")
        return "\n".join(lines)

    elif command_type == "show_week_load":
        lines = ["周负载："]
        lines.append(f"  周利用率：{weekly_load * 100:.0f}%")
        lines.append(f"  每日空闲容量：{daily_capacity:.1f}h")
        lines.append(f"  上下文切换：{context_switching * 100:.0f}%")
        if busy_density > 0.7:
            lines.append("  ⚠ 繁忙度偏高")
        return "\n".join(lines)

    else:
        return "未知命令。"



def _format_homework_response(state_engine) -> str:
    """Format stored homework from state engine, grouped by course."""
    from datetime import datetime, timezone
    from src.domain.course_topology import is_excluded_course, normalize_course_name

    hw_state = state_engine.get_all("homework")
    # Filter: only real homework entries with a title
    real_hw = {k: v for k, v in hw_state.items() if v.get("title")}
    pending_hw = {k: v for k, v in real_hw.items() if _is_unfinished_homework(v)}
    if not pending_hw:
        return "暂无未完成作业。"

    now = datetime.now(timezone.utc)
    by_course: dict[str, list[dict]] = {}
    for hw_id, hw in pending_hw.items():
        course = normalize_course_name(hw.get("course", "未知课程"))
        if is_excluded_course(course):
            continue
        if course not in by_course:
            by_course[course] = []
        by_course[course].append({**hw, "id": hw_id, "course": course})

    total_pending = sum(len(items) for items in by_course.values())
    if total_pending == 0:
        return "暂无未完成作业。"
    lines = [f"未完成作业：{total_pending} 条", "────────"]
    for course, items in sorted(by_course.items()):
        pending = [i for i in items if _is_unfinished_homework(i)]
        if not pending:
            continue
        lines.append("")
        lines.append(f"{course} · {len(pending)}")
        for hw in sorted(pending, key=lambda x: x.get("deadline", "") or "9999"):
            title = hw.get("title", "?")
            deadline = hw.get("deadline", "")
            dl_str = ""
            if deadline:
                try:
                    dl = datetime.fromisoformat(deadline.replace("Z", "+00:00"))
                    hours_left = (dl - now).total_seconds() / 3600
                    if hours_left < 0:
                        dl_str = f" ⚠️超期{abs(hours_left):.0f}h"
                    elif hours_left < 24:
                        dl_str = f" 🔴{hours_left:.0f}h"
                    elif hours_left < 72:
                        dl_str = f" 🟡{hours_left/24:.1f}d"
                    else:
                        dl_str = f" {hours_left/24:.1f}d"
                except Exception:
                    pass
            lines.append(f"- {title[:36]}{dl_str}")

    return "\n".join(lines)


def _is_unfinished_homework(hw: dict) -> bool:
    from src.domain.course_topology import is_excluded_course
    from src.domain.homework.status import is_open_homework_status
    if is_excluded_course(hw.get("course", "")):
        return False
    status = str(hw.get("status", "") or "").strip().lower()
    raw_status = str(hw.get("raw_status", "") or "").strip().lower()
    return is_open_homework_status(status, raw_status)


def _format_state_response(state_engine) -> str:
    """Format new derived state as Telegram message."""
    derived = state_engine.get_all_derived()
    dp = derived.get("deadline_pressure", {})
    wl = derived.get("workload_density", {})
    ac = derived.get("active_context", {})

    if not dp and not wl:
        return "暂无派生状态。首次 /同步教务 后会出现。"

    lines = ["认知状态", ""]

    if dp:
        trend_icon = {"critical": "!!", "rising": "!", "elevated": "^", "stable": "-"}.get(dp.get("trend", ""), "?")
        trend_label = {"critical": "严重", "rising": "上升", "elevated": "偏高", "stable": "稳定"}.get(dp.get("trend", ""), "?")
        lines.append(f"截止压力：{dp.get('score', 0) * 100:.0f}% {trend_icon}（{trend_label}）")
        lines.append(f"  活跃课程：{dp.get('active_courses', 0)} 门")
        if dp.get("overdue_count", 0) > 0:
            lines.append(f"  超期：{dp['overdue_count']} 条")
        if dp.get("closest_hours"):
            lines.append(f"  最近截止：{dp['closest_hours']}h")
        lines.append("")

    if wl:
        lines.append(f"负载：{wl.get('score', 0) * 100:.0f}%")
        lines.append(f"  待处理：{wl.get('total_pending', 0)} | 容量压力：{wl.get('capacity_pressure', 0) * 100:.0f}%")
        by_course = wl.get("by_course", {})
        if by_course:
            top = sorted(by_course.items(), key=lambda x: -x[1])[:5]
            lines.append(f"  前五：{', '.join(f'{k}({v})' for k, v in top)}")
        lines.append("")

    if ac:
        lines.append(f"活跃上下文：{ac.get('active_course_count', 0)} 门课程")
        urgent = ac.get("most_urgent")
        if urgent:
            ov = " ⚠超期" if urgent.get("overdue") else ""
            lines.append(f"  最紧急：{urgent.get('course', '?')}{ov}")

    return chr(10).join(lines)


def _pct(value) -> str:
    try:
        return f"{float(value) * 100:.0f}%"
    except (TypeError, ValueError):
        return "-"


def _end_of_day_iso() -> str:
    now = datetime.now(timezone.utc)
    return (now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).isoformat()


def _friendly_error(error: str) -> str:
    text = error or "未知错误"
    if "ERR_CONNECTION_CLOSED" in text or "ERR_CONNECTION_RESET" in text or "net::" in text:
        return "网络连接失败，请稍后重试。"
    if "insufficient" in text.lower() or "forbidden" in text.lower():
        return "权限不足，请重新授权或检查日历权限。"
    if "invalid_grant" in text.lower() or "token" in text.lower():
        return "授权已失效，请重新运行 Google Calendar 登录。"
    return text.splitlines()[0][:120]


def _format_today_dashboard(state_engine) -> str:
    """Compact ambient dashboard for /today."""
    derived = state_engine.get_all_derived()
    dp = derived.get("deadline_pressure", {})
    wl = derived.get("workload_density", {})
    cog = derived.get("cognition", {})
    ac = derived.get("active_context", {})
    temporal = state_engine.get_view("temporal", "projection")
    hydration = state_engine.get_all("hydration").get("current", {})
    homework = state_engine.get_all("homework")

    total_ml = int(hydration.get("total_ml_today", 0) or 0)
    hydration_state = "低" if total_ml < 800 else "正常" if total_ml < 1600 else "充足"
    mood = cog.get("subjective", {}).get("current_mood")
    context_bits = []
    if mood is not None:
        context_bits.append(f"情绪 {mood}/10")
    if cog.get("subjective", {}).get("social_plan_today"):
        context_bits.append("有社交安排")
    if cog.get("subjective", {}).get("evening_event"):
        context_bits.append("今晚占用")
    active_context = " / ".join(context_bits) if context_bits else "无显式情境"

    pending = []
    now = datetime.now(timezone.utc)
    for hw_id, hw in homework.items():
        if not hw.get("title") or not _is_unfinished_homework(hw):
            continue
        hours_left = None
        if hw.get("deadline"):
            try:
                dl = datetime.fromisoformat(hw["deadline"].replace("Z", "+00:00"))
                if dl.tzinfo is None:
                    dl = dl.replace(tzinfo=timezone.utc)
                hours_left = (dl - now).total_seconds() / 3600
            except (ValueError, TypeError):
                pass
        pending.append((99999 if hours_left is None else hours_left, hw))
    pending.sort(key=lambda item: item[0])

    if pending:
        top_lines = []
        for hours_left, hw in pending[:3]:
            due = ""
            if hours_left != 99999:
                due = f" · {hours_left:.0f}h" if hours_left >= 0 else f" · 超期{abs(hours_left):.0f}h"
            top_lines.append(f"- {hw.get('course', '未知')}：{hw.get('title', '?')[:28]}{due}")
        workload_items = "\n".join(top_lines)
    else:
        workload_items = "- 暂无待处理任务"

    urgent = ac.get("most_urgent") or {}
    next_intervention = "先处理最近截止项" if urgent else "维持同步，低强度推进"
    busy_windows = temporal.get("busy_windows", []) or []
    recovery_windows = temporal.get("recovery_windows", []) or []
    ctx = temporal.get("context", {}) or {}
    evening_capacity = wl.get("evening_capacity", 1.0)
    temporal_flags = []
    if ctx.get("social_block_tonight"):
        temporal_flags.append("今晚有社交安排")
    if ctx.get("workout_block_later"):
        temporal_flags.append("稍后有训练")
    if ctx.get("travel_block_today"):
        temporal_flags.append("今天有出行")
    temporal_constraint = " / ".join(temporal_flags) if temporal_flags else "无显式时间约束"

    cal_lines = []
    today = datetime.now(LOCAL_TZ).date()
    calendar_blocks = [
        b for b in state_engine.get_temporal_blocks()
        if str(b.source) == "google_calendar"
        and b.start.astimezone(LOCAL_TZ).date() == today
    ]
    for block in sorted(calendar_blocks, key=lambda b: b.start)[:3]:
        st = block.start.astimezone(LOCAL_TZ).strftime("%H:%M")
        et = block.end.astimezone(LOCAL_TZ).strftime("%H:%M")
        cal_lines.append(f"- {st}-{et} {block.title[:20]}")
    cal_block = "\n".join(cal_lines) if cal_lines else "- 无"
    recovery_block = "有" if recovery_windows else "无"

    # ── Vocab summary ────────────────────────────────────────────────
    vocab_state = state_engine.get_view("vocab", "momo")
    vocab_lines = []
    if vocab_state and vocab_state.get("progress"):
        prog = vocab_state.get("progress", {})
        today_v = vocab_state.get("today", {})
        finished = prog.get("finished", 0)
        total = prog.get("total", 0)
        remaining = today_v.get("remaining", 0)
        stale = vocab_state.get("stale", True)
        stale_mark = " ⚠数据过时" if stale else ""
        if total > 0:
            vocab_lines.append(
                f"背词：{today_v.get('finished', 0)}/{today_v.get('total', 0)}"
                f"{stale_mark}"
            )
            if remaining > 0:
                vocab_lines.append(f" 剩 {remaining} 个待复习")
        else:
            vocab_lines.append("背词：暂无数据")
    if vocab_lines:
        vocab_section = "\n".join(vocab_lines) + "\n\n"
    else:
        vocab_section = ""

    today_blocks = [
        b for b in state_engine.get_temporal_blocks()
        if b.start.astimezone(LOCAL_TZ).date() == today
    ]
    time_graph = _format_time_distribution(today_blocks)

    return (
        "今日安排\n"
        "────────\n"
        f"{time_graph}\n\n"
        f"截止压力：{_pct(dp.get('score', cog.get('deadline_pressure', 0)))}\n"
        f"负载密度：{_pct(wl.get('score', cog.get('workload_overload', 0)))}\n"
        f"饮水：{total_ml}ml / {hydration_state}\n"
        f"情境：{active_context}\n"
        f"晚间容量：{_pct(evening_capacity)}\n"
        f"下一时间约束：{temporal_constraint}\n"
        f"恢复窗口：{recovery_block}\n"
        f"下一步：{next_intervention}\n\n"
        "背词\n"
        f"{vocab_section}"
        "Calendar\n"
        f"{cal_block}\n\n"
        "Top workload\n"
        f"{workload_items}"
    )


def _format_time_distribution(blocks) -> str:
    buckets = {"课程": 0, "日历": 0, "画画": 0, "作业": 0, "其他": 0}
    for block in blocks:
        minutes = max(0, int((block.end - block.start).total_seconds() / 60))
        if minutes <= 0:
            continue
        source = str(block.source)
        block_type = str(block.block_type)
        metadata = getattr(block, "metadata", {}) or {}
        if source == "jwxt" and block_type in {"class_lecture", "class_lab"}:
            buckets["课程"] += minutes
        elif source == "google_calendar":
            if metadata.get("managed_by") == "cognitive_os" and metadata.get("source") == "daily_art_plan":
                buckets["画画"] += minutes
            else:
                buckets["日历"] += minutes
        elif block_type == "homework_deadline":
            buckets["作业"] += minutes
        else:
            buckets["其他"] += minutes

    total = sum(buckets.values())
    if total <= 0:
        return "时间分布：暂无显式安排"

    lines = ["时间分布"]
    for label, minutes in buckets.items():
        if minutes <= 0:
            continue
        ratio = minutes / total
        filled = max(1, round(ratio * 18))
        bar = "█" * filled + "░" * (18 - filled)
        lines.append(f"{label:<2} {bar} {minutes // 60}h{minutes % 60:02d}m")
    return "\n".join(lines)


def _supportive_line(kind: str) -> str:
    templates = {
        "arrangement": "有新安排不是打断计划，而是现实更新。今天先把时间重新摆好，保留一个能落地的小块。",
        "missed": "没按计划不等于失败，只说明原计划和当下能量不匹配。先承认现实，再把下一步缩小。",
        "completed": "已完成就是系统最重要的反馈。今天不用追求完美，继续把可重复的动作留下来。",
    }
    return templates.get(kind, templates["missed"])


def _format_completion_prompt() -> str:
    return (
        "记录完成\n"
        "你可以直接发一句自然语言，不需要记命令。\n\n"
        "例：\n"
        "完成了 数据结构作业\n"
        "完成了 英语听力30分钟\n"
        "完成了 画画 2小时 人体速写12张\n"
        "做完了 健身3小时\n\n"
        "如果是没按计划，就发：没按计划 + 原因。"
    )


def _format_current_advice(state_engine) -> str:
    derived = state_engine.get_all_derived()
    planning = derived.get("planning", {})
    adaptive = derived.get("adaptive_planning", {})
    windows = planning.get("recommended_windows", [])
    advice = planning.get("planning_advice", [])

    lines = ["当前建议", "────────"]
    if windows:
        w = windows[0]
        lines.append(f"{w.get('time', '?')} · {w.get('label', '任务窗口')}")
        lines.append(f"模式：{w.get('type', 'standard')}")
    elif advice:
        lines.append(advice[0])
    else:
        lines.append("暂无明确建议。先同步任务或打开今日状态。")

    intensity = adaptive.get("recommended_intensity")
    if intensity:
        labels = {"focused": "聚焦", "normal": "正常", "reduced": "降低", "light": "轻量"}
        lines.append(f"强度：{labels.get(intensity, intensity)}")
    return "\n".join(lines)


def _format_calendar_today(state_engine) -> str:
    blocks = [b for b in state_engine.get_temporal_blocks() if str(b.source) == "google_calendar"]
    sync = state_engine.get_view("temporal", "projection").get("calendar_sync", {})
    now = datetime.now(LOCAL_TZ).date()
    today = [b for b in blocks if b.start.astimezone(LOCAL_TZ).date() == now]
    header = "今日日历"
    if sync:
        header += f"（{sync.get('calendar_count', 0)} 个日历，最近 {sync.get('count', 0)} 条）"
    if not today:
        calendars = ", ".join(c.get("summary", c.get("id", "")) for c in sync.get("calendars", [])[:4])
        suffix = f"\n读取日历：{calendars}" if calendars else ""
        return f"{header}：无事项。{suffix}"
    lines = [f"{header}："]
    for b in sorted(today, key=lambda x: x.start)[:8]:
        st = b.start.astimezone(LOCAL_TZ).strftime("%H:%M")
        et = b.end.astimezone(LOCAL_TZ).strftime("%H:%M")
        kind = str(b.block_type).split(".")[-1]
        cal = (b.metadata or {}).get("calendar_summary", "")
        cal_text = f" · {cal}" if cal else ""
        lines.append(f"- {st}-{et} [{kind}] {b.title[:30]}{cal_text}")
    return "\n".join(lines)


def _format_calendar_context(state_engine) -> str:
    temporal = state_engine.get_view("temporal", "projection")
    ctx = temporal.get("context", {})
    blocks = [b for b in state_engine.get_temporal_blocks() if str(b.source) == "google_calendar"]
    recovery = [b for b in blocks if str(b.block_type) in {"recovery_block", "workout_block"}]
    sync = temporal.get("calendar_sync", {})
    lines = ["日历情境："]
    lines.append(f"- calendar blocks: {len(blocks)}")
    lines.append(f"- recovery windows: {len(recovery)}")
    if sync:
        lines.append(f"- calendars: {sync.get('calendar_count', 0)}")
        names = ", ".join(c.get("summary", c.get("id", "")) for c in sync.get("calendars", [])[:4])
        if names:
            lines.append(f"- read: {names}")
    lines.append(f"- social tonight: {'是' if ctx.get('social_block_tonight') else '否'}")
    lines.append(f"- workout later: {'是' if ctx.get('workout_block_later') else '否'}")
    lines.append(f"- travel today: {'是' if ctx.get('travel_block_today') else '否'}")
    lines.append(f"- meetings today: {ctx.get('meeting_blocks_today', 0)}")
    return "\n".join(lines)


# ── Plan confidence score ────────────────────────────────────────────────────


def _compute_plan_confidence(state_engine, settings) -> tuple[str, str, list[str]]:
    """Compute deterministic plan confidence score.

    Returns (level_cn, reason_text, reason_list) where:
      - level_cn: "低"/"中"/"高"
      - reason_text: comma-joined Chinese reasons
    """
    from datetime import datetime

    derived = state_engine.get_all_derived()
    cog = derived.get("cognition", {})
    planning = derived.get("planning", {})
    art_state = state_engine.get_view("art", "today")
    art_plan = art_state.get("plan", {})
    temporal = state_engine.get_view("temporal", "projection")

    reasons: list[str] = []
    score = 50  # neutral baseline

    # 1. Calendar / school load
    busy_windows = temporal.get("busy_windows", []) or []
    busy_hours = 0
    for w in busy_windows:
        try:
            start = datetime.fromisoformat(str(w.get("start", "")))
            end = datetime.fromisoformat(str(w.get("end", "")))
            busy_hours += max(0, (end - start).total_seconds() / 3600)
        except (ValueError, TypeError):
            pass
    if busy_hours > 8:
        score -= 20
        reasons.append("日历负载高")
    elif busy_hours > 5:
        score -= 10
        reasons.append("日历负载中等")
    elif busy_hours < 2:
        score += 10
        reasons.append("日历较空")

    # 2. Homework / deadline pressure
    deadline_pressure = float(cog.get("deadline_pressure", 0) or 0)
    if deadline_pressure > 0.7:
        score -= 20
        reasons.append("截止压力高")
    elif deadline_pressure > 0.4:
        score -= 10
        reasons.append("有截止压力")
    else:
        score += 10
        reasons.append("作业压力低")

    # 3. Art target
    art_target = int(art_plan.get("target_minutes", 0) or 0)
    free_slots = art_plan.get("free_slots", []) or []
    free_hours = 0
    for s in free_slots:
        if isinstance(s, (list, tuple)) and len(s) >= 2:
            try:
                fh = (datetime.fromisoformat(s[1]) - datetime.fromisoformat(s[0])).total_seconds() / 3600
                free_hours += max(0, fh)
            except (ValueError, TypeError):
                pass

    if art_target > 360:
        if free_hours < 6:
            score -= 20
            reasons.append("画画目标偏高(>6h)")
        else:
            score -= 10
            reasons.append("画画目标偏高但有时间")
    elif art_target > 240:
        if free_hours < 4:
            score -= 10
            reasons.append("画画目标中高")
        else:
            score -= 5
            reasons.append("画画目标中等")
    elif art_target > 0:
        score += 5
        reasons.append("画画目标合理")

    # 4. Mood / fatigue
    mood = cog.get("subjective", {}).get("current_mood")
    fatigue_risk = float(cog.get("fatigue_risk", 0) or 0)
    if mood is not None:
        if mood <= 3:
            score -= 15
            reasons.append("情绪偏低")
        elif mood <= 5:
            score -= 5
            reasons.append("情绪一般")
        elif mood >= 7:
            score += 10
            reasons.append("情绪好")
    if fatigue_risk > 0.7:
        score -= 15
        reasons.append("疲劳风险高")
    elif fatigue_risk > 0.4:
        score -= 5
        reasons.append("有疲劳风险")

    # 5. Free windows
    windows = planning.get("recommended_windows", []) or []
    has_deep_work = any(w.get("type") == "deep_work" for w in windows)
    if has_deep_work:
        score += 10
        reasons.append("有深度工作窗口")
    total_windows = len(windows)
    if total_windows >= 3:
        score += 5
        reasons.append("多个可用窗口")

    # Clamp to [0, 100]
    score = max(0, min(100, score))

    # Level
    if score >= 70:
        level_cn = "高"
    elif score >= 40:
        level_cn = "中"
    else:
        level_cn = "低"

    # Deduplicate and limit reasons
    seen: set[str] = set()
    unique_reasons: list[str] = []
    for r in reasons:
        if r not in seen:
            seen.add(r)
            unique_reasons.append(r)

    reason_text = "，".join(unique_reasons[:4])

    return level_cn, reason_text, unique_reasons


# Brief acknowledgments for NL intent fallback successes
_NL_FALLBACK_ACK: dict[str, str] = {
    "show_today": "已识别：查看今日安排",
    "check_homework": "已识别：查询作业",
    "sync_refresh": "已识别：同步刷新数据",
    "hydration_record": "已识别：补水记录",
    "subjective_context": "已识别：记录情境",
    "completion_record": "已识别：记录完成",
    "verbal_scheduling": "已识别：口述排期",
    "finance_transaction": "已识别：财务记录",
    "query_schedule_date": "已识别：查询课表",
    "record_school_leave": "已识别：请假记录",
}


class CognitiveOSBot:
    """Telegram Bot — interface adapter. Stateless I/O translation layer."""

    def __init__(
        self,
        settings: Settings,
        bus: EventBus,
        pipeline: Pipeline,
        state_engine: StateEngine,
        course_registry=None,
        derived_engine=None,
        intervention_engine=None,
        subjective_registry=None,
        event_store=None,
    ) -> None:
        self.settings = settings
        self.bus = bus
        self.pipeline = pipeline
        self.state_engine = state_engine
        self.course_registry = course_registry
        self.derived_engine = derived_engine
        self.intervention_engine = intervention_engine
        self.subjective_registry = subjective_registry
        self.event_store = event_store
        self._app: Application | None = None
        self._pending_proposals: dict[str, dict] = {}
        self._chaoxing = None
        self._keepalive_task: asyncio.Task | None = None
        self._last_cards: dict[tuple[int, str], int] = {}
        self._sync_started_at: dict[str, float] = {}
        self._pending_input: dict[int, str] = {}  # user_id -> pending mode
        self._morning_refresh: dict[str, dict] = {}  # f"{user_id}:{date}" -> state
        self._user_recent_actions: dict[int, list[dict]] = {}  # user_id -> list of recent undoable actions (max 20)
        self._last_consistency_review_at: float = 0.0  # monotonic timestamp for dedup
        self._last_review_error_notified: float = 0.0  # monotonic timestamp for error cooldown
        self._review_cooldown_seconds: float = 30.0  # minimum interval between auto reviews
        self._last_repair_at: float = 0.0  # monotonic timestamp for repair dedup
        self._repair_cooldown_seconds: float = 120.0  # minimum interval between auto repairs

    def _start_keepalive(self) -> None:
        """Start a background task that periodically visits Chaoxing to prevent idle timeout."""
        if self._keepalive_task is not None:
            return

        async def _keepalive_loop():
            # Wait for initial bootstrap before first keepalive
            await asyncio.sleep(120)
            while True:
                try:
                    if self._chaoxing:
                        await self._chaoxing.keepalive()
                except Exception:
                    logger.debug("[KEEPALIVE] error", exc_info=True)
                await asyncio.sleep(900)  # every 15 minutes

        self._keepalive_task = asyncio.create_task(_keepalive_loop())
        logger.info("[KEEPALIVE] started (interval=15min)")

    def _quick_reply_keyboard(self) -> ReplyKeyboardMarkup:
        return ReplyKeyboardMarkup(
            [
                [KeyboardButton("今日安排"), KeyboardButton("查课表")],
                [KeyboardButton("记录完成"), KeyboardButton("作业列表")],
                [KeyboardButton("补水记录"), KeyboardButton("状态填报")],
                [KeyboardButton("认知学习"), KeyboardButton("口述排期")],
                [KeyboardButton("同步刷新数据"), KeyboardButton("刷新按钮")],
                [KeyboardButton("本月资金"), KeyboardButton("30天要钱排期")],
            ],
            resize_keyboard=True,
            is_persistent=True,
        )

    def _hydration_keyboard(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("250ml", callback_data="hyd:250"),
                InlineKeyboardButton("500ml", callback_data="hyd:500"),
            ],
            [
                InlineKeyboardButton("750ml", callback_data="hyd:750"),
                InlineKeyboardButton("1000ml", callback_data="hyd:1000"),
            ],
        ])

    def _intervention_keyboard(self, intervention_id: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("已完成", callback_data=f"amb:done:{intervention_id}")],
            [InlineKeyboardButton("稍后30分钟", callback_data=f"amb:delay30:{intervention_id}")],
            [InlineKeyboardButton("跳过", callback_data=f"amb:skip:{intervention_id}")],
        ])

    def _context_keyboard(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("聚餐", callback_data="ctx:social_plan"),
                InlineKeyboardButton("外出", callback_data="ctx:outside"),
            ],
            [
                InlineKeyboardButton("健身", callback_data="ctx:workout"),
                InlineKeyboardButton("家庭事务", callback_data="ctx:family"),
            ],
            [InlineKeyboardButton("临时任务", callback_data="ctx:ad_hoc_task")],
        ])

    async def _reply(self, update: Update, text: str, **kwargs) -> None:
        if not update.message:
            return
        kwargs.setdefault("reply_markup", self._quick_reply_keyboard())
        await update.message.reply_text(text, **kwargs)

    async def _run_and_publish_consistency_review(self, source: str) -> dict[str, Any]:
        """Run calendar consistency review once and publish the result event."""
        review = run_consistency_review(self.state_engine, self.settings)
        event = Event(
            event_type=EventType.CALENDAR_CONSISTENCY_REVIEW_COMPLETED,
            aggregate_id="system",
            aggregate_type=AggregateType.SYSTEM,
            payload=review,
            metadata={"source": source},
        )
        if self.bus:
            await self.bus.publish(event)
        else:
            await self.state_engine.apply(event)
        self._last_consistency_review_at = time.monotonic()
        return review

    async def _run_and_publish_calendar_repair(self, review_findings: list[dict], source: str) -> dict[str, Any]:
        """Run calendar consistency repair and publish the result event.

        Respects write gates. Never touches private calendar events.
        """
        from src.core.calendar_consistency import repair_calendar_consistency
        from src.executor.google_calendar.executor import GoogleCalendarExecutor
        executor = GoogleCalendarExecutor(
            use_mock=self.settings.google_calendar_mock,
            settings=self.settings,
        )
        repair_result = await repair_calendar_consistency(
            self.state_engine, self.settings,
            review_findings=review_findings,
            executor=executor,
        )
        event = Event(
            event_type=EventType.CALENDAR_CONSISTENCY_REPAIR_COMPLETED,
            aggregate_id="system",
            aggregate_type=AggregateType.SYSTEM,
            payload=repair_result,
            metadata={"source": source},
        )
        if self.bus:
            await self.bus.publish(event)
        else:
            await self.state_engine.apply(event)
        self._last_repair_at = time.monotonic()
        return repair_result

    def _track_action(
        self,
        user_id: int,
        action_type: str,
        summary: str,
        params: dict | None = None,
        action_id: str | None = None,
    ) -> str:
        """Track a recent undoable action for this user. Returns action_id."""
        from uuid import uuid4
        aid = action_id or f"act-{uuid4().hex[:12]}"
        if not hasattr(self, "_user_recent_actions"):
            self._user_recent_actions = {}
        actions = self._user_recent_actions.setdefault(user_id, [])
        actions.append({
            "action_id": aid,
            "action_type": action_type,
            "summary": summary,
            "params": params or {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "reverted": False,
        })
        # Cap at 20 per user
        self._user_recent_actions[user_id] = actions[-20:]
        return aid

    async def _reply_with_undo(
        self, update: Update, text: str, action_id: str, **kwargs
    ) -> None:
        """Reply with an inline undo button for a tracked action."""
        if not update.message:
            return
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        kb = [[InlineKeyboardButton("↩ 撤回", callback_data=f"undo:{action_id}")]]
        kwargs.setdefault("reply_markup", InlineKeyboardMarkup(kb))
        await update.message.reply_text(text, **kwargs)

    async def _send_or_update_card(
        self,
        chat_id: int,
        card_key: str,
        text: str,
        reply_markup=None,
    ) -> None:
        if not self._app or not self._app.bot:
            return

        key = (chat_id, card_key)
        message_id = self._last_cards.get(key)
        if message_id:
            try:
                await self._app.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=text,
                    reply_markup=reply_markup,
                )
                logger.info("TELEGRAM_CARD_UPDATED user_id=%s card=%s message_id=%s", chat_id, card_key, message_id)
                return
            except Exception:
                logger.debug("card edit failed; sending a fresh card", exc_info=True)

        msg = await self._app.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup or self._quick_reply_keyboard(),
        )
        self._last_cards[key] = msg.message_id
        logger.info("TELEGRAM_CARD_UPDATED user_id=%s card=%s message_id=%s", chat_id, card_key, msg.message_id)

    async def _handle_finance_batch_intake(self, update, user_id: int, raw_text: str) -> None:
        """Parse multi-fact finance text, create draft, show confirm/discard card."""
        from src.domain.finance.batch_parser import parse_batch

        draft = parse_batch(raw_text)
        draft_id = draft["draft_id"]

        # Emit batch drafted event to store in state
        await self.pipeline.run(Event(
            event_type=EventType.FINANCE_BATCH_DRAFTED,
            aggregate_id=str(user_id),
            aggregate_type=AggregateType.FINANCE,
            causation_id=uuid4(),
            payload={
                "draft_id": draft_id,
                "raw_text": raw_text,
                "items": draft["items"],
                "questions": draft["questions"],
                "summary": draft["summary"],
            },
        ))

        # Build draft card text
        lines = [f"📋 检测到多条财务信息："]
        summary = draft["summary"]

        if summary["expense_count"] > 0:
            for item in draft["items"]:
                if item["type"] == "expense":
                    lines.append(f"  💸 {item.get('title', item.get('raw', ''))} — {item['amount']:.0f}元")
                elif item["type"] == "reimbursement":
                    gross = item.get("gross_amount", 0)
                    reimb = item.get("reimbursed_amount", 0)
                    net = item.get("net_amount", 0)
                    pct = item.get("percent", "")
                    pct_str = f"({pct}%) " if pct else ""
                    lines.append(f"  🔄 报销{pct_str}{reimb:.1f}元（原额{gross:.0f}，自付{net:.1f}）")

        if summary["reimbursement_count"] > 0 and summary["expense_count"] == 0:
            lines.append(f"  🔄 报销 {summary['reimbursement_count']} 笔，共 {summary['reimbursement_total']:.1f}元")

        if summary["debt_count"] > 0:
            for item in draft["items"]:
                if item["type"] == "partner_debt_created":
                    lines.append(f"  💳 对象欠款：{item['amount']:.0f}元（{item.get('date', '')[:10]}）")

        if summary["rule_count"] > 0:
            for item in draft["items"]:
                if item["type"] == "parent_fund_rule_configured":
                    lines.append(f"  📅 老妈要钱规则：每{item['interval_days']}天{item['amount']:.0f}元")

        if summary["pf_record_count"] > 0:
            for item in draft["items"]:
                if item["type"] == "parent_fund_request_recorded":
                    lines.append(f"  📝 已找{item.get('person', '家长')}要了{item['amount']:.0f}元")

        if summary["pf_plan_count"] > 0:
            lines.append(f"  📌 计划要钱 {summary['pf_plan_count']} 笔，共 {summary['pf_plan_total']:.0f}元：")
            for item in draft["items"]:
                if item["type"] == "parent_fund_request_planned":
                    lines.append(f"    • {item.get('description', '')}：{item['amount']:.0f}元")

        if draft["questions"]:
            lines.append("")
            for q in draft["questions"]:
                lines.append(f"  ⚠️ {q}")

        lines.append("")
        lines.append("请确认是否入账：")

        text = "\n".join(lines)

        kb = [[
            InlineKeyboardButton("✅ 确认入账", callback_data=f"batch:confirm:{draft_id}"),
            InlineKeyboardButton("🗑️ 丢弃", callback_data=f"batch:discard:{draft_id}"),
        ]]
        await update.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(kb),
        )

    async def _rebuild_state_from_events(self) -> int:
        store = self.event_store or getattr(self.bus, "_event_store", None)
        if store is None:
            return 0
        events = await store.replay_all()
        await self.state_engine.rebuild_from_events(events)
        return len(events)

    async def _notify_allowed_users(self, text: str) -> list[int]:
        chat_ids = [int(uid) for uid in (self.settings.telegram_allowed_users or [])]
        if not self._app or not self._app.bot:
            return []
        sent_to = []
        for cid in chat_ids:
            try:
                await self._app.bot.send_message(
                    chat_id=cid,
                    text=text,
                    reply_markup=self._quick_reply_keyboard(),
                )
                sent_to.append(cid)
            except Exception as exc:
                logger.error("TELEGRAM_SEND_FAILED chat_id=%s error=%s", cid, exc)
        return sent_to

    def _daily_review_date(self) -> str:
        return datetime.now(LOCAL_TZ).date().isoformat()

    def _daily_review_already_sent(self, date_str: str) -> bool:
        return bool(self.state_engine.get_view("daily_review", date_str).get("sent_at"))

    def _nightly_review_due_on_startup(self, now: datetime | None = None) -> bool:
        if not self.settings.nightly_review_enabled:
            return False
        now = now or datetime.now(LOCAL_TZ)
        try:
            hour_text, minute_text = self.settings.nightly_review_time.split(":", 1)
            due = now.replace(hour=int(hour_text), minute=int(minute_text), second=0, microsecond=0)
        except (TypeError, ValueError):
            return False
        return now >= due and not self._daily_review_already_sent(now.date().isoformat())

    async def _maybe_send_missed_nightly_review_on_startup(self) -> None:
        if not self._nightly_review_due_on_startup():
            return
        date_str = self._daily_review_date()
        review = await self._run_daily_review(date_str=date_str, force=False)
        if not review:
            return
        sent_to = await self._notify_allowed_users(review.payload.get("text", "晚间总结生成失败。"))
        await self._record_daily_review_sent(date_str, sent_to, "startup_catchup")

    async def _run_daily_review(self, *, date_str: str | None = None, force: bool = False) -> Event | None:
        date_str = date_str or self._daily_review_date()
        event = Event(
            event_type=EventType.DAILY_REVIEW_REQUESTED,
            aggregate_id=date_str,
            aggregate_type=AggregateType.SYSTEM,
            payload={"date": date_str, "force": force},
            metadata={"source": "telegram"},
        )
        events = await self.pipeline.run(event)
        for produced in events:
            if produced.event_type == EventType.DAILY_REVIEW_GENERATED:
                return produced
        return None

    def _write_daily_profile_stats(self, date_str: str | None = None) -> None:
        """Compute lightweight daily stats and write to ## 系统观察."""
        try:
            from src.derived_state.daily_stats import compute_daily_stats, format_stats_line
            from src.integrations.obsidian_daily import ObsidianDailyWriter, SECTION_HEADERS
            from datetime import datetime
            from zoneinfo import ZoneInfo

            snapshot = self.state_engine.snapshot()
            state = snapshot.get("state", {})
            derived = snapshot.get("derived", {})
            target_date = None
            if date_str:
                try:
                    target_date = datetime.fromisoformat(date_str).replace(tzinfo=ZoneInfo("Asia/Singapore"))
                except (ValueError, TypeError):
                    pass

            stats = compute_daily_stats(state, derived, target_date)
            stats_line = format_stats_line(stats)
            writer = ObsidianDailyWriter(self.settings)
            writer.write_section(SECTION_HEADERS["system_obs"], stats_line, target_date)
        except Exception as exc:
            logger.debug("daily profile stats write failed (non-fatal): %s", exc)

    async def _record_daily_review_sent(self, date_str: str, sent_to: list[int], source: str) -> None:
        if not sent_to:
            return
        await self.pipeline.run(Event(
            event_type=EventType.DAILY_REVIEW_SENT,
            aggregate_id=date_str,
            aggregate_type=AggregateType.USER,
            payload={"date": date_str, "sent_to": sent_to, "source": source},
            metadata={"source": source},
        ))

    def _format_course_state_fallback(self) -> str:
        courses = self.state_engine.get_all("course")
        active = [c for c in courses.values() if c.get("active", True)]
        if not active:
            return "暂无课程。试试 同步任务。"
        names = sorted({c.get("course_name") or "未知课程" for c in active})
        lines = [f"活跃课程（{len(names)} 门）：", ""]
        for name in names[:15]:
            lines.append(f"  {name}")
        return "\n".join(lines)

    # ── DeepSeek helpers ────────────────────────────────────────────────

    async def _deepseek_json(
        self, system_prompt: str, user_message: str
    ) -> dict | None:
        """Call DeepSeek and return parsed JSON dict, or None on failure."""
        from src.infrastructure.deepseek import DeepSeekClient

        if not self.settings.deepseek_api_key:
            return None

        client = DeepSeekClient(
            api_key=self.settings.deepseek_api_key,
            base_url=self.settings.deepseek_base_url,
            model=self.settings.deepseek_model,
            timeout_seconds=self.settings.deepseek_timeout_seconds,
        )
        try:
            return await client.chat_json(system_prompt, user_message)
        except Exception as exc:
            logger.error("DeepSeek call failed: %s", exc)
            return None

    async def _handle_nl_intent_fallback(
        self, update: Update, text: str, user_id: int
    ) -> tuple[Command | None, bool]:
        """AI fallback for unrecognized text: parse intent via DeepSeek → validate → map to Command.

        Returns (Command, handled). handled=True means the fallback already
        replied/recorded a terminal outcome and the caller should not show help.
        Always records a learning sample event.
        Also publishes NL_INTENT_EXECUTED event on success.
        """
        from src.domain.natural_language.intent import (
            validate_ai_output,
            map_to_command,
        )

        # 1) Publish parse-requested event
        request_event = Event(
            event_type=EventType.NL_INTENT_PARSE_REQUESTED,
            aggregate_id=str(user_id),
            aggregate_type=AggregateType.USER,
            payload={"raw_text": text},
        )

        if self.bus:
            await self.bus.publish(request_event)

        # 2) Call DeepSeek (or skip if no API key)
        if not self.settings.deepseek_api_key:
            return None, False

        system_prompt = (
            "You are a strict intent classifier for a personal cognitive OS. "
            "Your ONLY job is to determine what the user wants to DO. "
            "Never make up facts. Never add content not in the user's message.\n\n"
            "Output JSON with these fields:\n"
            '- "intent": one of the following intent types:\n'
            "  show_today, check_homework, sync_refresh, query_schedule_date,\n"
            "  record_school_leave, verbal_scheduling, finance_transaction,\n"
            "  completion_record, hydration_record, subjective_context, unknown\n"
            '- "params": object with intent-specific keys:\n'
            "  - query_schedule_date: {\"date\": \"YYYY-MM-DD\"}\n"
            "  - record_school_leave: {\"date\": \"YYYY-MM-DD\"}\n"
            "  - finance_transaction: {}\n"
            "  - verbal_scheduling: {}\n"
            "  - completion_record: {}\n"
            "  - hydration_record: {}\n"
            "  - subjective_context: {\"text\": \"description\"}\n"
            "  - others: {}\n"
            '- "confidence": float 0-1\n'
            '- "raw_phrase": the original user message\n'
            '- "reasoning": short explanation in Chinese\n\n'
            "Rules:\n"
            "- If the intent is not clearly one of the above, use \"unknown\".\n"
            "- Do NOT execute arbitrary commands. Do NOT guess dates — use 'unknown' if unsure.\n"
            "- For query_schedule_date and record_school_leave, prefer 'unknown' over guessing.\n"
            "- Never make up information. If you're not confident, use 'unknown'.\n"
            "- Always output valid JSON with no markdown."
        )

        result = await self._deepseek_json(system_prompt, text)

        if result is None:
            # API failure — record failed sample, return None (no help, just brief message)
            await self._publish_learning_sample(text, "api_error", success=False, error="DeepSeek API failure or timeout")
            await self._reply(update, "这句话我暂时没解析成功，已记录。")
            return None, True

        # 3) Validate against allowed intent schema
        validated = validate_ai_output(result)
        if validated is None:
            await self._publish_learning_sample(text, "validation_failed", success=False, error="AI output failed schema validation")
            await self._reply(update, "这句话我暂时没能安全转换成操作，已记录。")
            return None, True

        intent = validated["intent"]

        # 4) For unknown intent — record and return guidance
        if intent == "unknown":
            await self._publish_learning_sample(text, "unknown", success=False, error="AI classified as unknown")
            # Short guidance instead of full help
            guide = (
                "抱歉，我暂时无法理解这个指令。\n"
                "已记录你的输入，我会学习识别这类表达。\n"
                "试试用 / 命令，或点击「刷新按钮」查看可用按钮。"
            )
            await self._reply(update, guide)
            return None, True

        # 5) Map to Command
        cmd = map_to_command(validated, str(user_id))
        if cmd is None:
            return None, False

        # 6) Publish learning sample (success)
        await self._publish_learning_sample(text, intent, success=True)

        # 7) Publish NL_INTENT_EXECUTED
        exec_event = Event(
            event_type=EventType.NL_INTENT_EXECUTED,
            aggregate_id=str(user_id),
            aggregate_type=AggregateType.NL_INTENT,
            payload={
                "intent": intent,
                "command_type": cmd.command_type,
                "confidence": validated.get("confidence", 0),
                "raw_text": text,
            },
        )
        if self.bus:
            await self.bus.publish(exec_event)

        # 8) Brief user acknowledgment
        ack = _NL_FALLBACK_ACK.get(intent, "")
        if ack:
            await self._reply(update, ack)

        return cmd, False

    async def _publish_learning_sample(
        self,
        raw_text: str,
        intent: str,
        success: bool = True,
        error: str = "",
    ) -> None:
        """Publish a NL_INTENT_LEARNING_SAMPLE_RECORDED event."""
        sample_event = Event(
            event_type=EventType.NL_INTENT_LEARNING_SAMPLE_RECORDED,
            aggregate_id="nl_samples",
            aggregate_type=AggregateType.NL_INTENT,
            payload={
                "raw_text": raw_text,
                "intent": intent,
                "confidence": 0.0,
                "success": success,
                "error": error,
            },
        )
        if self.bus:
            await self.bus.publish(sample_event)

    async def _generate_nl_habit_summary(self) -> None:
        """Generate a 3-day habit summary from stored NL intent samples and publish event."""
        from datetime import datetime, timedelta, timezone
        from collections import Counter

        if not self.state_engine:
            return

        # Read stored samples from state
        samples_view = self.state_engine.get_view("nl_intent", "samples")
        samples = samples_view.get("samples", [])

        if not samples:
            logger.info("NL habit summary: no samples to summarize")
            return

        # Filter to last 3 days
        cutoff = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        recent = [s for s in samples if s.get("recorded_at", "") >= cutoff]

        if not recent:
            logger.info("NL habit summary: no recent samples (last 3 days)")
            return

        trigger_count = len(recent)
        success_count = sum(1 for s in recent if s.get("success"))
        failure_count = trigger_count - success_count

        # Top intents
        intent_counter: Counter = Counter(s.get("intent", "unknown") for s in recent)
        top_intents = dict(intent_counter.most_common(5))

        # Top raw phrases (shortened for privacy)
        phrase_counter: Counter = Counter(
            s.get("raw_text", "")[:30] for s in recent if s.get("raw_text")
        )
        top_phrases = [{"phrase": p, "count": c} for p, c in phrase_counter.most_common(5)]

        # Unknown samples for review
        unknown_samples = [
            {"raw_text": s.get("raw_text", "")[:50], "error": s.get("error", "")}
            for s in recent
            if s.get("intent") in ("unknown", "validation_failed", "api_error")
        ][:10]

        period_start = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        period_end = datetime.now(timezone.utc).isoformat()

        summary_event = Event(
            event_type=EventType.NL_INTENT_HABIT_SUMMARY_CREATED,
            aggregate_id="nl_habit_summary",
            aggregate_type=AggregateType.NL_INTENT,
            payload={
                "period_start": period_start,
                "period_end": period_end,
                "trigger_count": trigger_count,
                "success_count": success_count,
                "failure_count": failure_count,
                "top_intents": top_intents,
                "top_phrases": top_phrases,
                "unknown_samples": unknown_samples,
            },
        )

        if self.bus:
            await self.bus.publish(summary_event)

        # Also store via state engine
        await self.state_engine.apply(summary_event)

        logger.info(
            "NL habit summary: %d triggers, %d success, %d failure, top=%s",
            trigger_count,
            success_count,
            failure_count,
            list(top_intents.keys()),
        )

    async def _handle_cognitive_learning_pending(
        self, update: Update, user_id: int, text: str
    ) -> None:
        """Send user text to DeepSeek, parse as events, publish through pipeline."""
        if not self.settings.deepseek_api_key:
            await self._reply(update, "DeepSeek 未配置（deepseek_api_key 为空）。")
            self._pending_input.pop(user_id, None)
            return

        system_prompt = (
            "You are a cognitive OS memory parser. "
            "Parse the user's natural language input into structured memory events. "
            "Output JSON with a 'events' array. Each event has: "
            "'event_type' ('MEMORY_ENTRY_CREATED' or 'SUBJECTIVE_CONTEXT_ADDED'), "
            "'content' (string description), "
            "'tags' (array of strings), "
            "'kind' (for SUBJECTIVE_CONTEXT_ADDED: 'note' or 'context'). "
            "Prefer MEMORY_ENTRY_CREATED for factual entries and "
            "SUBJECTIVE_CONTEXT_ADDED for current state/feelings. "
            "Always output valid JSON with no markdown."
        )

        result = await self._deepseek_json(system_prompt, text)
        if result is None:
            await self._reply(update, "DeepSeek 解析失败，请稍后重试。")
            self._pending_input.pop(user_id, None)
            return

        events_data = result.get("events", [])
        if not events_data:
            await self._reply(update, "DeepSeek 未解析出记忆事件，请重试。")
            self._pending_input.pop(user_id, None)
            return

        produced_events = []
        for evt_data in events_data:
            event_type_str = evt_data.get("event_type", "MEMORY_ENTRY_CREATED")
            if event_type_str == "MEMORY_ENTRY_CREATED":
                event = Event(
                    event_type=EventType.MEMORY_ENTRY_CREATED,
                    aggregate_id=str(user_id),
                    aggregate_type=AggregateType.USER,
                    payload={
                        "content": evt_data.get("content", ""),
                        "tags": evt_data.get("tags", []),
                        "source": "cognitive_learning",
                    },
                )
                produced_events.append(event)
            elif event_type_str == "SUBJECTIVE_CONTEXT_ADDED":
                event = Event(
                    event_type=EventType.SUBJECTIVE_CONTEXT_ADDED,
                    aggregate_id=str(user_id),
                    aggregate_type=AggregateType.USER,
                    payload={
                        "kind": evt_data.get("kind", "note"),
                        "text": evt_data.get("content", evt_data.get("text", "")),
                        "source": "cognitive_learning",
                    },
                )
                produced_events.append(event)

        # Publish all events through pipeline (goes through EventBus)
        for event in produced_events:
            await self.pipeline.run(event)

        await self._reply(
            update,
            f"已记录 {len(produced_events)} 条记忆事件。",
        )
        self._pending_input.pop(user_id, None)

    async def _handle_completion_record_pending(
        self, update: Update, user_id: int, text: str
    ) -> str | None:
        """Record a free-form completed task through event flow.

        Returns action_id for undo tracking, or None if no action was recorded.
        """
        from src.interface.telegram.router import parse_completion_detail

        parsed = parse_completion_detail(text)

        # Fallback: use text as-is if parser fails
        if parsed is None or not parsed.get("task"):
            clean = text.strip().strip(" ：:，,。")
            for prefix in ("完成了", "我完成了", "做完了", "我做完了", "已完成"):
                if clean.startswith(prefix):
                    clean = clean[len(prefix):].strip(" ：:，,。")
                    break
            if not clean:
                await self._reply(update, _format_completion_prompt())
                self._pending_input[user_id] = "completion_record"
                return None

            completed_event = Event(
                event_type=EventType.PLANNING_TASK_COMPLETED,
                aggregate_id=str(user_id),
                aggregate_type=AggregateType.USER,
                payload={
                    "task_id": clean,
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                    "source": "telegram_completion_record",
                    "text": clean,
                },
            )
            memory_event = Event(
                event_type=EventType.MEMORY_ENTRY_CREATED,
                aggregate_id=str(user_id),
                aggregate_type=AggregateType.USER,
                causation_id=completed_event.event_id,
                payload={
                    "content": f"完成：{clean}",
                    "tags": ["completion", "daily_log"],
                    "source": "telegram_completion_record",
                },
            )
            await self.pipeline.run(completed_event)
            await self.pipeline.run(memory_event)
            self._pending_input.pop(user_id, None)
            aid = self._track_action(user_id, "completion_record", clean, params={"text": clean})
            await self._reply_with_undo(
                update, f"已记录完成：{clean}\n{_supportive_line('completed')}", aid,
            )
            return aid

        task = parsed["task"]
        duration_min = parsed.get("duration_min")
        focus = parsed.get("focus")
        is_art = parsed.get("is_art", False)

        # Format duration for display
        def _fmt_dur(m: float | None) -> str:
            if m is None:
                return ""
            if m >= 60:
                h = m / 60
                return f"{h:.1f}h" if h != int(h) else f"{int(h)}h"
            return f"{int(m)}min"

        dur_str = _fmt_dur(duration_min)
        display_parts = [task]
        if dur_str:
            display_parts.append(dur_str)
        if focus:
            display_parts.append(f"（{focus}）")
        display_text = " ".join(display_parts)

        # Route art-related completions to art handler AND create memory event
        if is_art:
            # Try to normalize text for art parser: convert h/hr → 小时
            art_ok = False
            try:
                from src.domain.art.handlers import parse_art_progress

                art_normalized = re.sub(
                    r"(\d+(?:\.\d+)?)\s*h(?:rs?|ours?)?\b",
                    r"\1小时",
                    text,
                    flags=re.IGNORECASE,
                )
                if not re.search(r"(?:完成|做完)", art_normalized):
                    art_normalized = f"完成 {art_normalized}"

                art_parsed = parse_art_progress(art_normalized)
                if art_parsed is not None:
                    # Art handler can process — route to it
                    await self._handle_art_progress(update, art_normalized)
                    art_ok = True
            except Exception:
                logger.debug("art parse failed, falling back to generic", exc_info=True)

            if not art_ok:
                # Create art progress event directly with minimal data
                art_event = Event(
                    event_type=EventType.ART_PROGRESS_RECORDED,
                    aggregate_id="art_today",
                    aggregate_type=AggregateType.ART,
                    payload={
                        "completed_minutes": int(duration_min) if duration_min else None,
                        "type": task,
                        "sessions": 1,
                        "count": 0,
                        "resistance": False,
                        "focus": focus,
                    },
                )
                await self.pipeline.run(art_event)
                # Write to Obsidian
                try:
                    from src.integrations.obsidian_daily import ObsidianDailyWriter
                    writer = ObsidianDailyWriter(self.settings)
                    dur_desc = f" {dur_str}" if dur_str else ""
                    focus_desc = f"（{focus}）" if focus else ""
                    writer.write_event_line(f"完成：{task}{dur_desc}{focus_desc}")
                except Exception as exc:
                    logger.warning("Obsidian completion write failed: %s", exc)

            # Always create a memory entry for art completions
            art_memory_event = Event(
                event_type=EventType.MEMORY_ENTRY_CREATED,
                aggregate_id=str(user_id),
                aggregate_type=AggregateType.USER,
                payload={
                    "content": f"完成：{display_text}",
                    "tags": ["completion", "daily_log", "art"],
                    "source": "telegram_completion_record",
                    "duration_minutes": duration_min,
                    "focus": focus,
                },
            )
            await self.pipeline.run(art_memory_event)

            # Reply specifically (skip if _handle_art_progress already replied)
            if art_ok:
                # _handle_art_progress already replied; just pop pending
                self._pending_input.pop(user_id, None)
                return None

            reply_parts = [f"已完成：{task}"]
            if dur_str:
                reply_parts.append(f"耗时 {dur_str}")
            if focus:
                reply_parts.append(f"—— {focus}")
            aid = self._track_action(user_id, "completion_record", display_text, params={"text": text, "task": task, "duration_min": duration_min, "focus": focus})
            await self._reply_with_undo(
                update, " | ".join(reply_parts) + "\n" + _supportive_line("completed"), aid,
            )
            self._pending_input.pop(user_id, None)
            return aid

        # ── Non-art completion ──────────────────────────────────────────
        completed_event = Event(
            event_type=EventType.PLANNING_TASK_COMPLETED,
            aggregate_id=str(user_id),
            aggregate_type=AggregateType.USER,
            payload={
                "task_id": task,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "source": "telegram_completion_record",
                "text": display_text,
                "duration_minutes": duration_min,
                "focus": focus,
            },
        )
        memory_event = Event(
            event_type=EventType.MEMORY_ENTRY_CREATED,
            aggregate_id=str(user_id),
            aggregate_type=AggregateType.USER,
            causation_id=completed_event.event_id,
            payload={
                "content": f"完成：{display_text}",
                "tags": ["completion", "daily_log"],
                "source": "telegram_completion_record",
                "duration_minutes": duration_min,
                "focus": focus,
            },
        )

        await self.pipeline.run(completed_event)
        await self.pipeline.run(memory_event)
        self._pending_input.pop(user_id, None)

        reply_parts = [f"已完成：{task}"]
        if dur_str:
            reply_parts.append(f"耗时 {dur_str}")
        if focus:
            reply_parts.append(f"—— {focus}")
        reply_text = " | ".join(reply_parts) + "\n" + _supportive_line("completed")
        aid = self._track_action(user_id, "completion_record", display_text, params={"text": text, "task": task, "duration_min": duration_min, "focus": focus})
        await self._reply_with_undo(update, reply_text, aid)

    def _format_cognitive_checkin_template(self) -> str:
        return (
            "状态填报\n"
            "可只填你愿意填的几项，空着也可以。\n\n"
            "```text\n"
            "状态：\n"
            "精力：\n"
            "压力：\n"
            "心情：\n"
            "身体：\n"
            "现在在做：\n"
            "接下来安排：\n"
            "今天最该推进：\n"
            "卡住/抗拒：\n"
            "备注：\n"
            "```\n"
            "直接复制后填几行发回来。"
        )

    async def _send_cognitive_checkin_prompt(self, source: str = "manual") -> list[int]:
        sent_to = await self._notify_allowed_users(self._format_cognitive_checkin_template())
        for uid in sent_to:
            self._pending_input[int(uid)] = "cognitive_checkin"
        logger.info("COGNITIVE_CHECKIN_PROMPT_SENT source=%s targets=%s", source, sent_to)
        return sent_to

    def _fallback_parse_cognitive_checkin(self, text: str) -> dict[str, Any]:
        aliases = {
            "状态": "state",
            "精力": "energy",
            "压力": "pressure",
            "心情": "mood",
            "身体": "body",
            "现在在做": "current_activity",
            "接下来安排": "arrangements",
            "今天最该推进": "priority",
            "卡住/抗拒": "blockers",
            "卡住": "blockers",
            "抗拒": "blockers",
            "备注": "note",
        }
        fields: dict[str, str] = {}
        for raw_line in text.splitlines():
            line = raw_line.strip().strip("|").strip()
            if not line or line.startswith("```"):
                continue
            if "：" in line:
                key, value = line.split("：", 1)
            elif ":" in line:
                key, value = line.split(":", 1)
            else:
                continue
            key = key.strip()
            value = value.strip()
            if not value:
                continue
            canonical = aliases.get(key)
            if canonical:
                fields[canonical] = value
        if not fields and text.strip():
            fields["note"] = text.strip()
        return fields

    async def _handle_cognitive_checkin_pending(
        self, update: Update, user_id: int, text: str
    ) -> None:
        system_prompt = (
            "You parse a partial Cognitive OS status check-in. "
            "The user may fill only some rows. Output JSON only. "
            "Return fields object with optional keys: state, energy, pressure, mood, body, "
            "current_activity, arrangements, priority, blockers, note. "
            "Also return optional mood_score integer 1-10 if clearly inferable; otherwise null. "
            "Do not invent missing fields."
        )
        result = await self._deepseek_json(system_prompt, text) if self.settings.deepseek_api_key else None
        fields = {}
        mood_score = None
        if isinstance(result, dict):
            fields = result.get("fields", {}) or {}
            mood_score = result.get("mood_score")
        if not fields:
            fields = self._fallback_parse_cognitive_checkin(text)

        if not fields and mood_score is None:
            await self._reply(update, "收到，但没有解析出可记录字段。")
            self._pending_input.pop(user_id, None)
            return

        events: list[Event] = []
        if isinstance(mood_score, int) and 1 <= mood_score <= 10:
            events.append(Event(
                event_type=EventType.MOOD_RECORDED,
                aggregate_id=str(user_id),
                aggregate_type=AggregateType.USER,
                payload={"score": mood_score, "source": "cognitive_checkin"},
            ))

        field_labels = {
            "state": "状态",
            "energy": "精力",
            "pressure": "压力",
            "mood": "心情",
            "body": "身体",
            "current_activity": "现在在做",
            "arrangements": "接下来安排",
            "priority": "今天最该推进",
            "blockers": "卡住/抗拒",
            "note": "备注",
        }
        context_parts = []
        for key, value in fields.items():
            if value is None:
                continue
            value_text = str(value).strip()
            if not value_text:
                continue
            label = field_labels.get(key, key)
            context_parts.append(f"{label}：{value_text}")

        if context_parts:
            events.append(Event(
                event_type=EventType.SUBJECTIVE_CONTEXT_ADDED,
                aggregate_id=str(user_id),
                aggregate_type=AggregateType.USER,
                payload={
                    "kind": "context",
                    "text": "；".join(context_parts),
                    "source": "cognitive_checkin",
                    "expires_at": _end_of_day_iso(),
                },
            ))
            events.append(Event(
                event_type=EventType.MEMORY_ENTRY_CREATED,
                aggregate_id=str(user_id),
                aggregate_type=AggregateType.USER,
                payload={
                    "content": "状态填报：" + "；".join(context_parts),
                    "tags": ["cognitive_checkin", "status"],
                    "source": "cognitive_checkin",
                },
            ))

        for event in events:
            await self.pipeline.run(event)

        self._pending_input.pop(user_id, None)
        await self._reply(update, f"已吸收状态填报：{len(events)} 条事件。")

    async def _handle_verbal_scheduling_pending(
        self, update: Update, user_id: int, text: str
    ) -> None:
        """Send user text to DeepSeek, parse as calendar event, create in Google Calendar."""
        if not hasattr(self, "_user_recent_actions"):
            self._user_recent_actions = {}
        if not self.settings.deepseek_api_key:
            await self._reply(update, "DeepSeek 未配置（deepseek_api_key 为空）。")
            self._pending_input.pop(user_id, None)
            return

        if not self.settings.google_calendar_write_enabled:
            await self._reply(update, "日历写入未开启（GOOGLE_CALENDAR_WRITE_ENABLED=false），无法创建事件。请先在环境变量中启用。")
            self._pending_input.pop(user_id, None)
            return

        now_local = datetime.now(LOCAL_TZ)

        # ── Build time-reality context from existing blocks (Task B) ──
        existing_blocks: list[dict[str, str]] = []
        if getattr(self, "state_engine", None):
            try:
                raw_blocks = self.state_engine.get_temporal_blocks()
                window_start = now_local - timedelta(hours=1)
                window_end = now_local + timedelta(days=2)
                BUSY_TYPES = {"class_lecture", "class_lab", "calendar_event",
                              "meeting_block", "social_block", "workout_block",
                              "travel_block", "personal_task_block", "busy_block"}
                BUSY_SOURCES = {"google_calendar", "jwxt"}
                for b in raw_blocks:
                    if window_start <= b.start <= window_end:
                        src = str(b.source)
                        btype = str(b.block_type)
                        if src in BUSY_SOURCES and btype in BUSY_TYPES:
                            existing_blocks.append({
                                "title": b.title,
                                "start": b.start.isoformat(),
                                "end": b.end.isoformat(),
                                "source": src,
                                "type": btype,
                            })
                existing_blocks.sort(key=lambda x: x["start"])
            except Exception:
                logger.warning("Failed to get temporal blocks for context", exc_info=True)

        # ── Build DeepSeek prompt with conflict avoidance ──
        blocks_text = ""
        if existing_blocks:
            lines = []
            for b in existing_blocks:
                s = datetime.fromisoformat(b["start"]).astimezone(LOCAL_TZ).strftime("%m-%d %H:%M")
                e = datetime.fromisoformat(b["end"]).astimezone(LOCAL_TZ).strftime("%H:%M")
                lines.append(f"- {s}~{e} {b['title']} ({b['type']})")
            blocks_text = "Existing commitments (do NOT overlap):\n" + "\n".join(lines) + "\n\n"

        system_prompt = (
            "You are a calendar scheduling assistant. Parse the user's "
            "natural language schedule instruction into a Google Calendar "
            "event payload. Output JSON with fields: "
            "'title' (string), "
            "'start' (ISO 8601 datetime string), "
            "'end' (ISO 8601 datetime string), "
            "'description' (string, optional), "
            "'location' (string, optional). "
            f"The current datetime is {now_local.isoformat()} in Asia/Singapore. "
            "Resolve relative dates such as 今天, 明天, 后天 against this current datetime. "
            "Use Asia/Singapore timezone. "
            "Never output a past datetime unless the user explicitly asks to create a past event. "
            "If the user gives a start time but no end time, default duration is 1 hour. "
            "Always output valid JSON with no markdown.\n\n"
            + blocks_text +
            "CRITICAL: Do NOT create events that overlap with existing commitments. "
            "If the user mentions 上完课/下课后/课后/下节/结束之后/待会去上课 then…, "
            "the new event must start AFTER that class/commitment ends. "
            "If the request cannot be accommodated without overlap, "
            "set 'start' and 'end' to empty strings and describe the conflict in a 'conflict' field.\n"
            "Always output valid JSON with no markdown."
        )

        result = await self._deepseek_json(system_prompt, text)
        if result is None:
            await self._reply(update, "DeepSeek 解析失败，请稍后重试。")
            self._pending_input.pop(user_id, None)
            return

        title = result.get("title", "口述排期事件")
        start = result.get("start", "")
        end = result.get("end", "")
        description = result.get("description", "")
        location = result.get("location", "")

        if not start or not end:
            conflict = result.get("conflict", "时间与现有安排冲突")
            await self._reply(update, f"⛔ 无法创建：{conflict}\n请调整时间或选择其他时段。")
            self._pending_input.pop(user_id, None)
            return

        try:
            start_dt = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
            end_dt = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=LOCAL_TZ)
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=LOCAL_TZ)
        except (TypeError, ValueError):
            await self._reply(update, "时间格式解析失败，请换一种说法。")
            self._pending_input.pop(user_id, None)
            return

        if start_dt < now_local - timedelta(minutes=5):
            relative_offset = None
            if "大后天" in text:
                relative_offset = 3
            elif "后天" in text:
                relative_offset = 2
            elif "明天" in text or "明日" in text:
                relative_offset = 1

            if relative_offset is not None and (now_local - start_dt) <= timedelta(days=3):
                target_date = (now_local + timedelta(days=relative_offset)).date()
                duration = end_dt - start_dt if end_dt > start_dt else timedelta(hours=1)
                local_start = start_dt.astimezone(LOCAL_TZ)
                start_dt = local_start.replace(
                    year=target_date.year,
                    month=target_date.month,
                    day=target_date.day,
                )
                end_dt = start_dt + duration
                start = start_dt.isoformat()
                end = end_dt.isoformat()

        if start_dt < now_local - timedelta(minutes=5):
            await self._reply(
                update,
                "解析到的是过去时间，已取消创建。请带上日期再说一次，例如：明天中午12点吃饭。",
            )
            self._pending_input.pop(user_id, None)
            return

        if end_dt <= start_dt:
            end_dt = start_dt + timedelta(hours=1)
            end = end_dt.isoformat()

        # ── Local conflict detection (Task B) ──
        for b in existing_blocks:
            b_start = datetime.fromisoformat(b["start"])
            b_end = datetime.fromisoformat(b["end"])
            if start_dt < b_end and b_start < end_dt:
                b_t = b_start.astimezone(LOCAL_TZ).strftime("%H:%M")
                b_e = b_end.astimezone(LOCAL_TZ).strftime("%H:%M")
                await self._reply(
                    update,
                    f"⛔ 这个时间和「{b['title']}」（{b_t}-{b_e}）冲突，没有创建。"
                    f"可以说「{b_e} 后安排」。",
                )
                self._pending_input.pop(user_id, None)
                return

        # ── Create Google Calendar event via executor ──
        from src.executor.google_calendar.executor import GoogleCalendarExecutor

        executor = GoogleCalendarExecutor(
            use_mock=self.settings.google_calendar_mock,
            settings=self.settings,
        )

        from src.core.proposal import Proposal, ProposalStatus, TargetSystem

        from uuid import uuid4

        proposal = Proposal(
            proposal_id=f"verbal-schedule-{uuid4().hex[:12]}",
            user_id=str(user_id),
            action_payload={
                "title": title,
                "start": start,
                "end": end,
                "description": description,
                "location": location,
            },
            target_system=TargetSystem.GOOGLE_CALENDAR,
            reason="用户口述排期",
            confidence=1.0,
        )
        proposal.status = ProposalStatus.ACCEPTED
        result_event = await executor.execute(proposal)

        if result_event.event_type == EventType.EXECUTION_COMPLETED:
            # Publish the completion through EventBus
            await self.pipeline.run(result_event)
            cal_event_id = result_event.payload.get("event_id", "")
            summary = title

            # Track with calendar metadata for undo (Task A)
            tracked_params: dict[str, Any] = {
                "event_id": cal_event_id,
                "calendar_id": self.settings.google_calendar_calendar_id or "primary",
                "title": title,
                "start": start,
                "end": end,
                "text": text,
                "source": "bot_created",
                "bot_created": True,
            }
            aid = self._track_action(
                int(user_id) if not isinstance(user_id, int) else user_id,
                "verbal_scheduling", summary,
                params=tracked_params,
            )

            # ── Consistency review (Task C) ──
            review_note = ""
            if existing_blocks:
                for b in existing_blocks:
                    b_end_dt = datetime.fromisoformat(b["end"])
                    b_start_dt = datetime.fromisoformat(b["start"])
                    gap_after = (start_dt - b_end_dt).total_seconds() / 60
                    gap_before = (b_start_dt - end_dt).total_seconds() / 60
                    if 0 <= gap_after < 30:
                        review_note = (
                            f"\n⚠️ {title} 紧接「{b['title']}」之后"
                            f"（仅间隔 {int(gap_after)} 分钟），建议留出缓冲时间。"
                        )
                        break
                    if 0 <= gap_before < 30:
                        review_note = (
                            f"\n⚠️ {title} 结束后仅 {int(gap_before)} 分钟"
                            f"就有「{b['title']}」，建议留出缓冲时间。"
                        )
                        break

            if not review_note:
                review_note = "\n✅ 未发现冲突。"

            await self._reply_with_undo(
                update,
                f"✅ 已创建日历事件：{title}\n开始：{start}\n结束：{end}{review_note}",
                aid,
            )
        else:
            error = result_event.payload.get("error", "未知错误")
            await self._reply(update, f"❌ 创建日历事件失败：{error}")

        self._pending_input.pop(user_id, None)

    # ── Date schedule formatting ────────────────────────────────────────

    def _format_schedule_date(self, date_str: str) -> str:
        """Format JWXT schedule blocks for a specified date."""
        blocks = [
            b for b in self.state_engine.get_temporal_blocks()
            if str(b.source) == "jwxt"
            and str(b.block_type) in {"class_lecture", "class_lab"}
        ]
        try:
            from datetime import datetime, timezone
            target_date = datetime.fromisoformat(date_str).date()
        except (ValueError, TypeError):
            return f"日期格式无效：{date_str}。请使用 YYYY-MM-DD 格式。"

        from zoneinfo import ZoneInfo
        local_tz = ZoneInfo("Asia/Singapore")
        day_blocks = [
            b for b in blocks
            if b.start.astimezone(local_tz).date() == target_date
        ]

        if not day_blocks:
            return f"{date_str} 无课程安排。"

        day_blocks.sort(key=lambda b: b.start)
        lines = [f"{date_str} 课表："]
        for b in day_blocks:
            start_t = b.start.astimezone(local_tz).strftime("%H:%M")
            end_t = b.end.astimezone(local_tz).strftime("%H:%M")
            block_type = getattr(b, "block_type", "")
            type_label = str(block_type).split(".")[-1] if "." in str(block_type) else str(block_type)
            teacher = (getattr(b, "metadata", {}) or {}).get("teacher", "")
            title = f"{b.title}（{teacher}）" if teacher and teacher not in b.title else b.title
            location = b.location or "未提供地址"
            lines.append(f"  {start_t}-{end_t} [{type_label}] {title} @ {location}")

        return "\n".join(lines)

    def _schedule_page_keyboard(self, date_str: str) -> InlineKeyboardMarkup:
        try:
            current = datetime.fromisoformat(date_str).date()
        except (TypeError, ValueError):
            current = datetime.now(LOCAL_TZ).date()
        today = datetime.now(LOCAL_TZ).date()
        prev_day = (current - timedelta(days=1)).isoformat()
        next_day = (current + timedelta(days=1)).isoformat()
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("前一天", callback_data=f"sch:{prev_day}"),
            InlineKeyboardButton("今天", callback_data=f"sch:{today.isoformat()}"),
            InlineKeyboardButton("后一天", callback_data=f"sch:{next_day}"),
        ]])


    def wire_handlers(self) -> None:
        """Register all domain handlers, connectors, and cognitive engines on the event bus."""
        from src.domain.homework.handlers import handle_user_command, handle_fetch_completed
        from src.connector.chaoxing.client import ChaoxingConnector
        from src.connector.jwxt.client import JwxtConnector
        from src.connector.google_calendar.client import GoogleCalendarConnector
        from src.domain.execution.handlers import (
            handle_proposal_accepted,
            handle_proposal_rejected,
            handle_user_accepted_proposal,
        )
        from src.domain.daily_review.handlers import handle_daily_review_requested
        from src.domain.finance.handlers import handle_finance_command
        from src.core.events import EventType, Event, AggregateType
        from src.core.safety import SafeHandler, DeadLetterQueue
        from derived_state import DERIVATION_TRIGGERS

        # -- Connectors (direct, no SafeHandler) --
        self._chaoxing = ChaoxingConnector(
            use_mock=self.settings.chaoxing_mock,
            event_bus=self.bus,
            course_registry=self.course_registry,
        )
        jwxt = JwxtConnector(use_mock=self.settings.jwxt_mock, settings=self.settings)
        gcal = GoogleCalendarConnector(settings=self.settings)

        # Domain handlers (SafeHandler for non-I/O handlers)
        dead_letter = DeadLetterQueue()
        safe = SafeHandler(dead_letter, timeout_seconds=30, max_retries=2)

        # -- User commands --
        self.bus.subscribe(EventType.USER_COMMAND_RECEIVED, handle_user_command)

        # -- Finance / Money Reality --
        async def on_finance_command(event):
            return await handle_finance_command(event)
        self.bus.subscribe(EventType.USER_COMMAND_RECEIVED, on_finance_command)

        for finance_state_event in (
            EventType.FINANCE_TRANSACTION_RECORDED,
            EventType.FINANCE_INCOME_RECORDED,
            EventType.FINANCE_BUDGET_UPDATED,
            EventType.PARENT_FUND_REQUEST_PLANNED,
            EventType.PARENT_FUND_REQUEST_RECORDED,
            EventType.PARENT_FUND_RECEIVED,
            EventType.PARENT_FUND_ITEM_CONFIGURED,
            EventType.FINANCE_SPENDING_WARNING_TRIGGERED,
            EventType.FINANCE_BATCH_DRAFTED,
            EventType.FINANCE_BATCH_ACCEPTED,
            EventType.FINANCE_BATCH_DISCARDED,
            EventType.FINANCE_REIMBURSEMENT_RECORDED,
            EventType.PARTNER_DEBT_CREATED,
            EventType.PARTNER_DEBT_REPAID,
            EventType.PARENT_FUND_RULE_CONFIGURED,
            EventType.PARENT_FUND_REQUEST_PLAN_CANCELLED,
        ):
            self.bus.subscribe(finance_state_event, self.state_engine.apply)

        async def on_daily_review_requested(event):
            return await handle_daily_review_requested(event, self.state_engine)
        self.bus.subscribe(EventType.DAILY_REVIEW_REQUESTED, on_daily_review_requested)

        # -- Connector subscriptions --
        self.bus.subscribe(EventType.CONNECTOR_FETCH_REQUESTED, self._chaoxing.handle_fetch_request)
        self.bus.subscribe(EventType.CONNECTOR_FETCH_REQUESTED, jwxt.handle_fetch_request)
        self.bus.subscribe(EventType.CONNECTOR_FETCH_REQUESTED, gcal.handle_fetch_request)

        # -- Momo vocabulary connector --
        async def on_momo_fetch_request(event):
            if event.payload.get("source") != "momo_vocab":
                return []
            return await fetch_momo_vocab(self.settings, event.event_id)
        from src.connector.momo.connector import fetch_momo_vocab
        self.bus.subscribe(EventType.CONNECTOR_FETCH_REQUESTED, on_momo_fetch_request)

        # -- Chaoxing session keepalive (prevents idle timeout) --
        self._start_keepalive()

        async def on_sync_started_notice(event):
            source = event.payload.get("source", "")
            if source != "chaoxing":
                return []
            self._sync_started_at[source] = time.monotonic()
            course_count = event.payload.get("course_count", "?")
            await self._notify_allowed_users(f"同步作业中：{course_count} 门课程。完成后会通知。")
            return []
        self.bus.subscribe(EventType.SYNC_STARTED, on_sync_started_notice)

        # -- Fetch completed: purity validation + record sync + domain handler --
        async def on_fetch_completed(event):
            from datetime import datetime, timezone
            src = event.payload.get("source", "")
            if src != "chaoxing":
                if src == "jwxt":
                    course_count = event.payload.get("course_count", 0)
                    block_count = event.payload.get("block_count", 0)
                    teaching_week = event.payload.get("teaching_week")
                    week_text = f"第 {teaching_week} 周，" if teaching_week else ""
                    await self._notify_allowed_users(
                        f"课表同步完成：{week_text}{course_count} 门课程，本周 {block_count} 节。"
                    )
                    return []
                if src == "google_calendar":
                    calendar_count = event.payload.get("calendar_count", 1)
                    calendars = event.payload.get("calendars", [])
                    names = "，".join(c.get("summary", c.get("id", "")) for c in calendars[:3])
                    name_text = f"（{names}）" if names else ""
                    await self._notify_allowed_users(
                        f"日历同步完成：读取 {calendar_count} 个日历{name_text}，{event.payload.get('count', 0)} 条时间块。"
                    )
                    return []
                return await safe.wrap(handle_fetch_completed)(event)

            homeworks = list(event.payload.get("homeworks", []))
            courses = event.payload.get("courses", [])
            active_names = set()
            if self.course_registry:
                active_names = set(self.course_registry.get_active_scope_names())

            # Purity validation
            purity_stats = {"total": len(homeworks), "duplicates": 0, "inactive": 0, "malformed": 0}
            existing_ids = set(self.state_engine._state.get("homework", {}).keys())
            now = datetime.now(timezone.utc)
            filtered = []

            for hw in homeworks:
                hw_id = hw.get("id", "")
                course_name = hw.get("course", "")
                deadline_str = hw.get("deadline", "")

                # Duplicate check
                if hw_id and hw_id in existing_ids:
                    purity_stats["duplicates"] += 1
                    logger.debug("[PURITY] duplicate homework skipped: %s", hw_id)
                    continue

                # Inactive course leakage check
                if active_names and course_name and course_name not in active_names:
                    purity_stats["inactive"] += 1
                    logger.warning("[PURITY] inactive course leak: %s / %s", course_name, hw.get("title", ""))
                    continue

                # Malformed deadline check
                if deadline_str:
                    try:
                        dl = datetime.fromisoformat(deadline_str.replace("Z", "+00:00"))
                        # Stale homework (deadline > 7 days ago)
                        if (now - dl).total_seconds() > 7 * 86400:
                            logger.debug("[PURITY] stale homework (deadline >7d ago): %s", hw.get("title"))
                    except (ValueError, TypeError):
                        purity_stats["malformed"] += 1
                        logger.warning("[PURITY] malformed deadline: %s / %s", hw.get("title"), deadline_str)
                        hw["deadline"] = ""

                filtered.append(hw)

            skipped = purity_stats["duplicates"] + purity_stats["inactive"]
            if skipped > 0:
                logger.info(
                    "[PURITY] filtered %d/%d homeworks: duplicates=%d inactive=%d malformed=%d",
                    skipped, purity_stats["total"],
                    purity_stats["duplicates"], purity_stats["inactive"], purity_stats["malformed"],
                )

            # Record courses to registry
            if self.course_registry:
                for c in courses:
                    cid = c.get("course_id", "")
                    name = c.get("name", "")
                    if cid:
                        self.course_registry.register(cid, name)
                        self.course_registry.record_sync(cid)

            # Reconstruct event with filtered homeworks
            filtered_payload = dict(event.payload)
            filtered_payload["homeworks"] = filtered
            filtered_payload["purity_stats"] = purity_stats
            filtered_event = Event(
                event_type=event.event_type,
                aggregate_id=event.aggregate_id,
                aggregate_type=event.aggregate_type,
                timestamp=event.timestamp,
                event_id=event.event_id,
                causation_id=event.causation_id,
                payload=filtered_payload,
                metadata=event.metadata,
            )
            started = self._sync_started_at.pop("chaoxing", None)
            if started is not None:
                duration = time.monotonic() - started
                duration_text = f"{duration:.0f}s" if duration < 90 else f"{duration / 60:.1f}min"
            else:
                duration_text = "?"
            total = len(homeworks)
            kept = len(filtered)
            errors = event.payload.get("errors", 0)
            await self._notify_allowed_users(
                f"同步完成：{kept}/{total} 条作业，错误 {errors}，耗时 {duration_text}。"
            )
            return await safe.wrap(handle_fetch_completed)(filtered_event)
        self.bus.subscribe(EventType.CONNECTOR_FETCH_COMPLETED, on_fetch_completed)

        async def on_fetch_failed_notice(event):
            source = event.payload.get("source")
            if source not in ("chaoxing", "jwxt"):
                return []
            self._sync_started_at.pop(source, None)
            error = event.payload.get("error", "未知错误")
            label = "作业" if source == "chaoxing" else "教务"
            await self._notify_allowed_users(f"{label}同步失败：{_friendly_error(error)}")
            return []
        self.bus.subscribe(EventType.CONNECTOR_FETCH_FAILED, on_fetch_failed_notice)

        # -- Homework tracking: record deadlines to registry --
        async def on_homework_new(event):
            if self.course_registry:
                from src.domain.homework.status import is_open_homework_status
                if not is_open_homework_status(event.payload.get("status"), event.payload.get("raw_status")):
                    return await self.state_engine.apply(event)
                course_name = event.payload.get("course", "")
                course_id = event.payload.get("course_id", course_name)
                deadline_str = event.payload.get("deadline", "")
                deadline_hours = None
                if deadline_str:
                    try:
                        from datetime import datetime, timezone
                        dl = datetime.fromisoformat(deadline_str.replace("Z", "+00:00"))
                        deadline_hours = (dl - datetime.now(timezone.utc)).total_seconds() / 3600
                    except (ValueError, TypeError):
                        pass
                self.course_registry.record_homework_seen(course_id, course_name, deadline_hours)
            return await self.state_engine.apply(event)
        self.bus.subscribe(EventType.HOMEWORK_NEW, on_homework_new)

        # -- State engine: core event handlers --
        self.bus.subscribe(EventType.HOMEWORK_PARSED, self.state_engine.apply)
        self.bus.subscribe(EventType.NOTIFICATION_SEND, self.state_engine.apply)
        self.bus.subscribe(EventType.SYNC_STARTED, self.state_engine.apply)
        self.bus.subscribe(EventType.SYNC_PROGRESS, self.state_engine.apply)
        self.bus.subscribe(EventType.CONNECTOR_FETCH_STARTED, self.state_engine.apply)
        self.bus.subscribe(EventType.CONNECTOR_FETCH_COMPLETED, self.state_engine.apply)
        self.bus.subscribe(EventType.CONNECTOR_FETCH_FAILED, self.state_engine.apply)
        self.bus.subscribe(EventType.HYDRATION_LOGGED, self.state_engine.apply)
        self.bus.subscribe(EventType.COGNITIVE_PROFILE_AUDITED, self.state_engine.apply)
        self.bus.subscribe(EventType.DAILY_REVIEW_GENERATED, self.state_engine.apply)
        self.bus.subscribe(EventType.DAILY_REVIEW_SENT, self.state_engine.apply)

        # -- Calendar consistency review state --
        self.bus.subscribe(EventType.CALENDAR_CONSISTENCY_REVIEW_REQUESTED, self.state_engine.apply)
        self.bus.subscribe(EventType.CALENDAR_CONSISTENCY_REVIEW_COMPLETED, self.state_engine.apply)
        self.bus.subscribe(EventType.CALENDAR_CONSISTENCY_REVIEW_FAILED, self.state_engine.apply)

        # -- Vocab state events --
        self.bus.subscribe(EventType.VOCAB_SYNC_STARTED, self.state_engine.apply)
        self.bus.subscribe(EventType.VOCAB_SYNC_COMPLETED, self.state_engine.apply)
        self.bus.subscribe(EventType.VOCAB_SYNC_FAILED, self.state_engine.apply)
        self.bus.subscribe(EventType.VOCAB_PROGRESS_UPDATED, self.state_engine.apply)
        self.bus.subscribe(EventType.VOCAB_SLACK_DETECTED, self.state_engine.apply)

        # -- Subjective reality: mood + notes + context --
        from src.domain.subjective.handlers import handle_subjective_command
        self.bus.subscribe(EventType.USER_COMMAND_RECEIVED, handle_subjective_command)

        if self.subjective_registry:
            async def on_mood(event):
                self.subjective_registry.on_mood_recorded(event)
                return await self.state_engine.apply(event)

            async def on_subjective_context(event):
                self.subjective_registry.on_subjective_context_added(event)
                return await self.state_engine.apply(event)

            self.bus.subscribe(EventType.MOOD_RECORDED, on_mood)
            self.bus.subscribe(EventType.SUBJECTIVE_CONTEXT_ADDED, on_subjective_context)
        else:
            self.bus.subscribe(EventType.MOOD_RECORDED, self.state_engine.apply)
            self.bus.subscribe(EventType.SUBJECTIVE_CONTEXT_ADDED, self.state_engine.apply)

        # -- Course lifecycle: COURSE_ACTIVATED/DEACTIVATED → registry + state --
        async def on_course_activated(event):
            if self.course_registry:
                self.course_registry.on_course_activated(event)
            return await self.state_engine.apply(event)

        async def on_course_deactivated(event):
            if self.course_registry:
                self.course_registry.on_course_deactivated(event)
            return await self.state_engine.apply(event)

        self.bus.subscribe(EventType.COURSE_ACTIVATED, on_course_activated)
        self.bus.subscribe(EventType.COURSE_DEACTIVATED, on_course_deactivated)
        self.bus.subscribe(EventType.SEMESTER_UPDATED, self.state_engine.apply)

        # -- JWXT completion: optionally mirror the schedule into Google Calendar. --
        async def on_jwxt_fetch_completed(event):
            if event.payload.get("source") != "jwxt":
                return []
            if not self.settings.google_calendar_schedule_write_enabled:
                return []
            intent = event.payload.get("intent", "")
            if intent not in ("schedule_manual", "schedule_daily_sync"):
                return []
            return [Event(
                event_type=EventType.CALENDAR_SCHEDULE_SYNC_REQUESTED,
                aggregate_id=event.aggregate_id,
                aggregate_type=AggregateType.SYSTEM,
                causation_id=event.event_id,
                payload={
                    "source": "jwxt",
                    "target": "google_calendar",
                    "days": self.settings.google_calendar_schedule_sync_days,
                    "calendar_id": self.settings.google_calendar_schedule_calendar_id,
                    "intent": intent,
                },
            )]
        self.bus.subscribe(EventType.CONNECTOR_FETCH_COMPLETED, on_jwxt_fetch_completed)

        # -- Scheduled checks: homework sync stays Chaoxing-only; schedule daily sync stays JWXT-only. --
        async def on_scheduled_check_homework(event):
            if event.event_type != EventType.SYSTEM_SCHEDULED_TRIGGER:
                return []
            action = event.payload.get("action", "")
            if action == "check_homework":
                scope = None
                if self.course_registry:
                    self.course_registry.compute_scores()
                    scope = self.course_registry.get_active_scope_names()
                return [Event(
                    event_type=EventType.CONNECTOR_FETCH_REQUESTED,
                    aggregate_id=event.aggregate_id,
                    aggregate_type=AggregateType.HOMEWORK,
                    causation_id=event.event_id,
                    payload={"source": "chaoxing", "query": "homework_list", "scope": scope},
                )]
            if action == "schedule_daily_sync":
                logger.info("[SCHEDULED] schedule_daily_sync → triggering JWXT sync")
                return [Event(
                    event_type=EventType.CONNECTOR_FETCH_REQUESTED,
                    aggregate_id=event.aggregate_id,
                    aggregate_type=AggregateType.HOMEWORK,
                    causation_id=event.event_id,
                    payload={
                        "source": "jwxt",
                        "query": "weekly_schedule",
                        "intent": "schedule_daily_sync",
                    },
                )]
            if action == "calendar_sync":
                logger.info("[SCHEDULED] calendar_sync → triggering Google Calendar sync")
                return [Event(
                    event_type=EventType.CONNECTOR_FETCH_REQUESTED,
                    aggregate_id=event.aggregate_id,
                    aggregate_type=AggregateType.SYSTEM,
                    causation_id=event.event_id,
                    payload={"source": "google_calendar", "query": "upcoming"},
                )]
            if action == "momo_vocab_sync":
                logger.info("[SCHEDULED] momo_vocab_sync → fetching vocab data")
                return [Event(
                    event_type=EventType.CONNECTOR_FETCH_REQUESTED,
                    aggregate_id=event.aggregate_id,
                    aggregate_type=AggregateType.VOCAB,
                    causation_id=event.event_id,
                    payload={"source": "momo_vocab", "query": "vocab_progress"},
                )]
            if action == "cognitive_checkin":
                logger.info("[SCHEDULED] cognitive_checkin → prompting allowed users")
                await self._send_cognitive_checkin_prompt(source="scheduler")
                return []
            if action == "nightly_review":
                if not self.settings.nightly_review_enabled:
                    return []
                date_str = self._daily_review_date()
                if self._daily_review_already_sent(date_str):
                    logger.info("[SCHEDULED] nightly_review skipped; already sent for %s", date_str)
                    return []
                self._write_daily_profile_stats(date_str)
                review = await self._run_daily_review(date_str=date_str, force=False)
                if not review:
                    return []
                sent_to = await self._notify_allowed_users(review.payload.get("text", "晚间总结生成失败。"))
                await self._record_daily_review_sent(date_str, sent_to, "scheduler")
                return []
            return []
        self.bus.subscribe(EventType.SYSTEM_SCHEDULED_TRIGGER, on_scheduled_check_homework)

        # -- Intervention: INTERVENTION_TRIGGERED → Telegram send --
        async def on_intervention_triggered(event):
            chat_ids = [int(uid) for uid in (self.settings.telegram_allowed_users or [])]
            if not chat_ids:
                logger.warning("INTERVENTION_TRIGGERED: no allowed users, skip send")
                return []
            message = event.payload.get("message", "")
            if not message:
                return []
            sent_to = []
            if not self._app or not self._app.bot:
                logger.info("INTERVENTION_TRIGGERED: bot not ready, skip startup send")
                return []
            for cid in chat_ids:
                try:
                    intervention_id = str(event.event_id)
                    card_text = f"建议卡片\n────────\n{message}"
                    await self._app.bot.send_message(
                        chat_id=cid,
                        text=card_text,
                        reply_markup=self._intervention_keyboard(intervention_id),
                    )
                    logger.info(
                        "INTERVENTION_SENT chat_id=%s type=%s cause=%s cascade_depth=%s",
                        cid, event.payload.get("intervention_type"), event.event_id,
                        event.metadata.get("cascade_depth", "?"),
                    )
                    sent_to.append(cid)
                except Exception as exc:
                    logger.error("INTERVENTION_SEND_FAILED chat_id=%s error=%s", cid, exc)
            return [Event(
                event_type=EventType.TELEGRAM_SENT,
                aggregate_id="intervention",
                aggregate_type=AggregateType.SYSTEM,
                causation_id=event.event_id,
                payload={
                    "intervention_type": event.payload.get("intervention_type"),
                    "message": message,
                    "sent_to": sent_to,
                },
            )]
        self.bus.subscribe(EventType.INTERVENTION_TRIGGERED, on_intervention_triggered)

        # -- Ambient feedback: store before derivation sees it --
        self.bus.subscribe(EventType.INTERVENTION_FEEDBACK_RECORDED, self.state_engine.apply)
        self.bus.subscribe(EventType.INTERVENTION_DELAYED, self.state_engine.apply)
        self.bus.subscribe(EventType.INTERVENTION_SKIPPED, self.state_engine.apply)

        # -- Derived State Engine --
        if self.derived_engine:
            self.bus.subscribe(EventType.SCHEDULE_TICK, self.derived_engine.on_tick)
            for dt in DERIVATION_TRIGGERS:
                self.bus.subscribe(dt, self.derived_engine.on_domain_event)
            self.bus.subscribe(EventType.DERIVED_STATE_UPDATED, self.state_engine.apply)
            self.bus.subscribe(EventType.DEADLINE_PRESSURE_UPDATED, self.state_engine.apply)

        # -- Intervention Engine --
        if self.intervention_engine:
            self.bus.subscribe(EventType.DERIVED_STATE_UPDATED, self.intervention_engine.on_derived_state)
            self.bus.subscribe(EventType.INTERVENTION_TRIGGERED, self.state_engine.apply)

        # -- Calendar Consistency Review (post-sync auto-audit) --
        async def on_consistency_review_requested(event):
            """Run consistency review and emit completed/failed event."""
            try:
                review = run_consistency_review(self.state_engine, self.settings)
                self._last_consistency_review_at = time.monotonic()
                return [Event(
                    event_type=EventType.CALENDAR_CONSISTENCY_REVIEW_COMPLETED,
                    aggregate_id="system",
                    aggregate_type=AggregateType.SYSTEM,
                    causation_id=event.event_id,
                    payload=review,
                    metadata={
                        "trace_id": event.metadata.get("trace_id", str(event.event_id)),
                        "source": event.payload.get("source", "auto"),
                    },
                )]
            except Exception as exc:
                logger.exception("[REVIEW] consistency review failed")
                return [Event(
                    event_type=EventType.CALENDAR_CONSISTENCY_REVIEW_FAILED,
                    aggregate_id="system",
                    aggregate_type=AggregateType.SYSTEM,
                    causation_id=event.event_id,
                    payload={"error": str(exc)},
                )]
        self.bus.subscribe(EventType.CALENDAR_CONSISTENCY_REVIEW_REQUESTED, on_consistency_review_requested)

        # -- Auto-trigger review after connector fetch completes (dedup'd) --
        async def on_auto_sync_review_trigger(event):
            """Emit REVIEW_REQUESTED after sync completions, with dedup."""
            source = event.payload.get("source", "")
            intent = str(event.payload.get("intent", ""))
            # Skip homework syncs — only schedule/calendar syncs affect calendar state
            if source == "chaoxing":
                return []
            # Manual sync branches run one review after the whole batch finishes.
            if "manual" in intent:
                return []
            # Dedup: skip if review was done recently (within cooldown)
            if time.monotonic() - self._last_consistency_review_at < self._review_cooldown_seconds:
                return []
            return [Event(
                event_type=EventType.CALENDAR_CONSISTENCY_REVIEW_REQUESTED,
                aggregate_id="system",
                aggregate_type=AggregateType.SYSTEM,
                causation_id=event.event_id,
                payload={"source": f"auto_{source}"},
                metadata={"trace_id": event.metadata.get("trace_id", str(event.event_id))},
            )]
        self.bus.subscribe(EventType.CONNECTOR_FETCH_COMPLETED, on_auto_sync_review_trigger)

        # -- Error cooldown for auto-sync: send short Telegram message on error severity --
        async def on_review_error_notification(event):
            """If auto review found errors, notify allowed users with cooldown."""
            if event.payload.get("overall_severity") != "error":
                return []
            # Cooldown: max one error notification per 5 minutes
            if time.monotonic() - self._last_review_error_notified < 300.0:
                return []
            self._last_review_error_notified = time.monotonic()
            findings = event.payload.get("findings", [])
            error_msgs = [f.get("message", "") for f in findings if f.get("severity") == "error"]
            warning_msgs = [f.get("message", "") for f in findings if f.get("severity") == "warning"]
            lines = ["⚠ 同步后检查发现问题"]
            if error_msgs:
                for m in error_msgs[:3]:
                    lines.append(f"✗ {m}")
            if warning_msgs:
                for m in warning_msgs[:3]:
                    lines.append(f"⚠ {m}")
            text = "\n".join(lines)
            await self._notify_allowed_users(text)
            return []
        self.bus.subscribe(EventType.CALENDAR_CONSISTENCY_REVIEW_COMPLETED, on_review_error_notification)

        # -- Auto-repair trigger after review completion (loop-guarded) --
        async def on_review_completed_auto_repair(event):
            """If review found repairable issues, emit REPAIR_REQUESTED with cooldown.

            Loop guard: skips if repair was done within cooldown, and skips
            manual-sync reviews (manual flow handles repair inline).
            """
            source_meta = event.metadata.get("source", "")
            # Manual sync handles repair inline — skip here to avoid double repair
            if source_meta.startswith("manual_"):
                return []
            findings = event.payload.get("findings", [])
            severity = event.payload.get("overall_severity", "ok")
            if severity == "ok":
                return []
            has_repairable = _is_repairable_schedule_issue(findings) or _has_repairable_art_conflict(findings)
            if not has_repairable:
                return []
            # Repair cooldown — prevents review→repair→review→repair loop
            if time.monotonic() - self._last_repair_at < self._repair_cooldown_seconds:
                return []
            return [Event(
                event_type=EventType.CALENDAR_CONSISTENCY_REPAIR_REQUESTED,
                aggregate_id="system",
                aggregate_type=AggregateType.SYSTEM,
                causation_id=event.event_id,
                payload={
                    "source": source_meta or "auto",
                    "review_findings": findings,
                },
                metadata={"trace_id": event.metadata.get("trace_id", str(event.event_id))},
            )]
        self.bus.subscribe(EventType.CALENDAR_CONSISTENCY_REVIEW_COMPLETED, on_review_completed_auto_repair)

        # -- Calendar consistency repair handler --
        async def on_calendar_repair_requested(event):
            """Run consistency repair and emit completed/failed."""
            from src.core.calendar_consistency import repair_calendar_consistency
            from src.executor.google_calendar.executor import GoogleCalendarExecutor
            try:
                executor = GoogleCalendarExecutor(
                    use_mock=self.settings.google_calendar_mock,
                    settings=self.settings,
                )
                repair_result = await repair_calendar_consistency(
                    self.state_engine, self.settings,
                    review_findings=event.payload.get("review_findings"),
                    executor=executor,
                )
                self._last_repair_at = time.monotonic()
                return [Event(
                    event_type=EventType.CALENDAR_CONSISTENCY_REPAIR_COMPLETED,
                    aggregate_id="system",
                    aggregate_type=AggregateType.SYSTEM,
                    causation_id=event.event_id,
                    payload=repair_result,
                )]
            except Exception as exc:
                logger.exception("[REPAIR] consistency repair failed")
                self._last_repair_at = time.monotonic()
                return [Event(
                    event_type=EventType.CALENDAR_CONSISTENCY_REPAIR_FAILED,
                    aggregate_id="system",
                    aggregate_type=AggregateType.SYSTEM,
                    causation_id=event.event_id,
                    payload={"error": str(exc)},
                )]
        self.bus.subscribe(EventType.CALENDAR_CONSISTENCY_REPAIR_REQUESTED, on_calendar_repair_requested)

        # -- Behavior / Feedback --
        self.bus.subscribe(EventType.PLANNING_RECOMMENDATION_ACCEPTED, self.state_engine.apply)
        self.bus.subscribe(EventType.PLANNING_RECOMMENDATION_SKIPPED, self.state_engine.apply)
        self.bus.subscribe(EventType.PLANNING_RECOMMENDATION_DELAYED, self.state_engine.apply)
        self.bus.subscribe(EventType.PLANNING_TASK_COMPLETED, self.state_engine.apply)
        self.bus.subscribe(EventType.PLANNING_TASK_ABANDONED, self.state_engine.apply)

        # -- Execution proposals --
        self.bus.subscribe(EventType.EXECUTION_PROPOSAL_ACCEPTED, handle_proposal_accepted)
        self.bus.subscribe(EventType.EXECUTION_PROPOSAL_REJECTED, handle_proposal_rejected)
        self.bus.subscribe(EventType.USER_ACCEPTED_PROPOSAL, handle_user_accepted_proposal)

        async def handle_calendar_schedule_sync(event):
            from src.executor.google_calendar.executor import GoogleCalendarExecutor
            executor = GoogleCalendarExecutor(
                use_mock=self.settings.google_calendar_mock,
                settings=self.settings,
            )
            try:
                result = await executor.sync_schedule_blocks(
                    self.state_engine.get_temporal_blocks(include_school_leave_classes=True),
                    days=int(event.payload.get("days", self.settings.google_calendar_schedule_sync_days)),
                    calendar_id=event.payload.get("calendar_id", self.settings.google_calendar_schedule_calendar_id),
                    proposal=None,
                )
                if not result.get("ok"):
                    error = result.get("error", "未知错误")
                    cal_id = result.get("calendar_id", event.payload.get("calendar_id", self.settings.google_calendar_schedule_calendar_id))
                    # Friendly Chinese failure reason
                    friendly_reasons = {
                        "schedule_calendar_write_disabled": "写入开关未开启",
                        "proposal_required: schedule mirror writes require an accepted proposal": "缺少审批提案",
                        "proposal_not_accepted: schedule mirror writes require an accepted proposal": "提案未审批",
                    }
                    reason_cn = friendly_reasons.get(error, _friendly_error(error))
                    user_msg = f"课表镜像失败：{reason_cn}，目标日历 {cal_id}"
                    failed = Event(
                        event_type=EventType.CALENDAR_SCHEDULE_SYNC_FAILED,
                        aggregate_id=event.aggregate_id,
                        aggregate_type=AggregateType.SYSTEM,
                        causation_id=event.event_id,
                        payload={**result, "calendar_id": cal_id},
                    )
                    await self._notify_allowed_users(user_msg)
                    return [failed]
                cal_id = result.get("calendar_id", event.payload.get("calendar_id", self.settings.google_calendar_schedule_calendar_id))

                # ── Verify mirror after sync ─────────────────────────────
                verify_result = await executor.verify_schedule_mirror(
                    self.state_engine.get_temporal_blocks(include_school_leave_classes=True),
                    days=int(event.payload.get("days", self.settings.google_calendar_schedule_sync_days)),
                    calendar_id=cal_id,
                )
                if verify_result.get("verified"):
                    verify_msg = "校验 OK"
                else:
                    verify_msg = (
                        f"校验不一致：课表 {verify_result.get('jwxt_count', 0)}，"
                        f"日历 {verify_result.get('calendar_count', 0)}"
                    )

                await self._notify_allowed_users(
                    f"课表镜像完成：新增 {result.get('created', 0)}，"
                    f"更新 {result.get('updated', 0)}，"
                    f"删除 {result.get('deleted', 0)}，"
                    f"目标日历 {cal_id}"
                    f"\n{verify_msg}"
                )
                return [Event(
                    event_type=EventType.CALENDAR_SCHEDULE_SYNC_COMPLETED,
                    aggregate_id=event.aggregate_id,
                    aggregate_type=AggregateType.SYSTEM,
                    causation_id=event.event_id,
                    payload={**result, "verification": verify_result},
                )]
            except Exception as exc:
                logger.exception("calendar schedule sync failed")
                cal_id = event.payload.get("calendar_id", self.settings.google_calendar_schedule_calendar_id)
                await self._notify_allowed_users(f"课表镜像失败：{_friendly_error(str(exc))}，目标日历 {cal_id}")
                return [Event(
                    event_type=EventType.CALENDAR_SCHEDULE_SYNC_FAILED,
                    aggregate_id=event.aggregate_id,
                    aggregate_type=AggregateType.SYSTEM,
                    causation_id=event.event_id,
                    payload={"ok": False, "error": str(exc), "calendar_id": cal_id},
                )]

        self.bus.subscribe(EventType.CALENDAR_SCHEDULE_SYNC_REQUESTED, handle_calendar_schedule_sync)
        self.bus.subscribe(EventType.EXECUTION_PROPOSAL_ACCEPTED, self.state_engine.apply)
        self.bus.subscribe(EventType.EXECUTION_PROPOSAL_REJECTED, self.state_engine.apply)
        self.bus.subscribe(EventType.USER_ACCEPTED_PROPOSAL, self.state_engine.apply)
        self.bus.subscribe(EventType.USER_REJECTED_PROPOSAL, self.state_engine.apply)
        self.bus.subscribe(EventType.EXECUTION_REQUESTED, self.state_engine.apply)
        self.bus.subscribe(EventType.CALENDAR_EVENT_CREATED, self.state_engine.apply)
        self.bus.subscribe(EventType.CALENDAR_SCHEDULE_SYNC_COMPLETED, self.state_engine.apply)
        self.bus.subscribe(EventType.CALENDAR_SCHEDULE_SYNC_FAILED, self.state_engine.apply)
        self.bus.subscribe(EventType.EXECUTION_PROPOSAL_EXPIRED, self.state_engine.apply)
        self.bus.subscribe(EventType.EXECUTION_COMPLETED, self.state_engine.apply)
        self.bus.subscribe(EventType.EXECUTION_FAILED, self.state_engine.apply)
        self.bus.subscribe(EventType.CALENDAR_CONSISTENCY_REPAIR_COMPLETED, self.state_engine.apply)
        self.bus.subscribe(EventType.CALENDAR_CONSISTENCY_REPAIR_FAILED, self.state_engine.apply)

        # Art planning
        from src.domain.art.handlers import (
            handle_art_plan_requested,
            handle_art_progress_recorded,
            handle_art_reality_inserted,
            handle_art_vibe_code_warning,
        )

        self.bus.subscribe(EventType.ART_PLAN_REQUESTED, handle_art_plan_requested)
        self.bus.subscribe(EventType.ART_PROGRESS_RECORDED, handle_art_progress_recorded)
        self.bus.subscribe(EventType.ART_DAILY_REALITY_INSERTED, handle_art_reality_inserted)
        self.bus.subscribe(EventType.ART_VIBE_CODE_WARNING, handle_art_vibe_code_warning)
        self.bus.subscribe(EventType.ART_PLAN_CREATED, self.state_engine.apply)
        self.bus.subscribe(EventType.ART_PLAN_UPDATED, self.state_engine.apply)
        self.bus.subscribe(EventType.ART_PROGRESS_RECORDED, self.state_engine.apply)
        self.bus.subscribe(EventType.ART_BLOCK_COMPLETED, self.state_engine.apply)
        self.bus.subscribe(EventType.ART_BLOCK_SKIPPED, self.state_engine.apply)
        self.bus.subscribe(EventType.ART_DAILY_REALITY_INSERTED, self.state_engine.apply)
        self.bus.subscribe(EventType.ART_OBSIDIAN_DAILY_UPDATED, self.state_engine.apply)
        self.bus.subscribe(EventType.ART_PLAN_REBALANCED, self.state_engine.apply)
        self.bus.subscribe(EventType.ART_VIBE_CODE_WARNING, self.state_engine.apply)

        # ── Obsidian daily sink (idempotent daily note writer) ──────────
        if self.settings.obsidian_daily_sink_enabled:
            async def _obsidian_daily_sink(event: Event) -> list[Event]:
                """Write daily-log MEMORY_ENTRY_CREATED events to the daily note."""
                tags = event.payload.get("tags", [])
                if "daily_log" not in tags:
                    return []
                try:
                    from src.integrations.obsidian_daily import (
                        ObsidianDailyWriter,
                        _daily_note_path,
                    )
                    writer = ObsidianDailyWriter(self.settings)
                    path = _daily_note_path(self.settings)

                    # Read or create daily note
                    if not path.parent.exists():
                        path.parent.mkdir(parents=True, exist_ok=True)

                    content = ""
                    if path.exists():
                        content = path.read_text(encoding="utf-8")

                    # Idempotency: skip if this event_id already written
                    event_marker = f"<!-- obsidian-sink:{event.event_id} -->"
                    if event_marker in content:
                        return []

                    # Append event line under 今日事件流 section
                    content_text = event.payload.get("content", "")
                    if content_text:
                        writer.write_event_line(content_text)

                        # Append idempotency marker after the event flow section
                        content = path.read_text(encoding="utf-8")
                        content += f"\n{event_marker}\n"
                        path.write_text(content, encoding="utf-8")
                except Exception as exc:
                    logger.warning(
                        "Obsidian daily sink write failed (non-fatal): %s", exc
                    )
                return []

            self.bus.subscribe(
                EventType.MEMORY_ENTRY_CREATED, _obsidian_daily_sink
            )
            logger.info("obsidian daily sink enabled")

        # -- NL Intent habit summary (triggered by scheduler every 3 days) --
        async def on_nl_habit_summary_scheduled(event):
            if event.aggregate_id != "nl_intent_habit_summary":
                return []
            await self._generate_nl_habit_summary()
            return []

        self.bus.subscribe(EventType.SYSTEM_SCHEDULED_TRIGGER, on_nl_habit_summary_scheduled)

        logger.info("handlers wired: connector + derived + intervention + domain")

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle incoming Telegram message."""
        logger.info("[TRACE] handle_message: entered, update_id=%s", update.update_id)
        if not update.message or not update.message.text:
            logger.info("[TRACE] handle_message: no message or no text, returning")
            return
        logger.info("[TRACE] handle_message: text=%s from user=%s", update.message.text[:50], update.effective_user.id if update.effective_user else None)

        user = update.effective_user
        if not user:
            return

        user_id = user.id
        text = update.message.text.strip()

        # Auth check
        allowed = self.settings.telegram_allowed_users
        if allowed and user_id not in allowed:
            await update.message.reply_text("⛔ 未授权")
            return

        # Pending input mode check (must happen before command parsing)
        pending_mode = self._pending_input.get(user_id)
        pending_command = parse_message(text, user_id)
        if pending_mode and pending_command is not None:
            self._pending_input.pop(user_id, None)
        elif pending_mode == "completion_record":
            await self._handle_completion_record_pending(update, user_id, text)
            return
        elif pending_mode == "cognitive_checkin":
            await self._handle_cognitive_checkin_pending(update, user_id, text)
            return
        elif pending_mode == "cognitive_learning":
            await self._handle_cognitive_learning_pending(update, user_id, text)
            return
        elif pending_mode == "verbal_scheduling":
            await self._handle_verbal_scheduling_pending(update, user_id, text)
            return
        elif pending_mode == "art_replan_reason":
            await self._handle_art_replan(update, text, reason=text.strip() or "未按计划")
            return

        # Translate message → command → event
        command = pending_command or parse_message(text, user_id)
        logger.info("[TRACE] handle_message: parse result=%s", command.command_type if command else None)
        if command is None:
            # AI-based NL fallback before showing help
            cmd, handled = await self._handle_nl_intent_fallback(update, text, user_id)
            if cmd is not None:
                command = cmd
                logger.info(
                    "[TRACE] handle_message: NL fallback resolved: %s", command.command_type
                )
            elif handled:
                return
            else:
                await self._reply(update, format_help())
                return

        event = command_to_event(command)
        logger.info("[TRACE] handle_message: event created, type=%s", event.event_type.value)

        logger.info("processing command: %s from user %d", command.command_type, user_id)

        # Run through the pipeline
        try:
            logger.info("[TRACE] handle_message: entering command dispatch block")
            # Feedback commands: translate to events before pipeline
            # help / ping: respond directly, no pipeline needed
            if command.command_type == "show_menu":
                await self._reply(update, "固定按钮已刷新。")
                return

            if command.command_type == "help":
                await self._reply(update, format_help())
                return

            if command.command_type == "ping":
                await self._reply(update, "pong")
                return

            # ── System operations ──────────────────────────────────────
            if command.command_type == "selfcheck":
                text = await self._format_selfcheck()
                await self._reply(update, text)
                return

            if command.command_type == "selftest":
                text = await self._run_selftest()
                await self._reply(update, text)
                return

            if command.command_type == "storage_status":
                text = await self._format_storage_status()
                await self._reply(update, text)
                return

            if command.command_type == "storage_vacuum":
                text = await self._run_storage_vacuum()
                await self._reply(update, text)
                return

            if command.command_type == "obsidian_status":
                from src.integrations.obsidian_daily import get_audit
                audit = get_audit()
                lines = ["Obsidian 状态", "=========="]
                lines.append(f"仓库路径: {self.settings.obsidian_vault_path}")
                lines.append(f"日常文件夹: {self.settings.obsidian_daily_folder}")
                if audit.get("last_write_path"):
                    lines.append(f"最后写入: {audit['last_write_path']}")
                    lines.append(f"最后章节: {audit.get('last_section', '?')}")
                lines.append(f"写入次数: {audit.get('write_count', 0)}")
                lines.append(f"跳过重复: {audit.get('skipped_duplicate_count', 0)}")
                if audit.get("last_error"):
                    lines.append(f"最后错误: {audit['last_error']}")
                else:
                    lines.append("错误: 无")
                await self._reply(update, "\n".join(lines))
                return

            if command.command_type == "plan_deviation":
                deviation_text = event.payload.get("params", {}).get("deviation_text", "") or text
                # Write to Obsidian ## 偏离原因
                try:
                    from src.integrations.obsidian_daily import ObsidianDailyWriter
                    writer = ObsidianDailyWriter(self.settings)
                    writer.write_section(
                        "## 偏离原因",
                        f"- {datetime.now(LOCAL_TZ).strftime('%H:%M')} {deviation_text}",
                    )
                except Exception as exc:
                    logger.warning("Obsidian deviation write failed: %s", exc)
                # Emit behavior event
                await self.pipeline.run(Event(
                    event_type=EventType.PLANNING_TASK_ABANDONED,
                    aggregate_id=str(user_id),
                    aggregate_type=AggregateType.USER,
                    payload={
                        "reason": deviation_text,
                        "source": "plan_deviation",
                        "outcome": "abandoned",
                        "outcome_timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                ))
                # Compact reply (not motivational)
                await self._reply(update, f"已记录偏移：{deviation_text[:60]}")
                return

            if command.command_type == "undo_last_action":
                await self._handle_nl_undo(update, user_id)
                return

            if command.command_type == "completion_prompt":
                self._pending_input[user_id] = "completion_record"
                await self._reply(update, _format_completion_prompt())
                return

            if command.command_type == "generic_completion":
                task_text = event.payload.get("params", {}).get("task_text") or event.payload.get("params", {}).get("args") or text
                await self._handle_completion_record_pending(update, user_id, str(task_text))
                return

            if command.command_type == "completion_record":
                raw_text = (
                    event.payload.get("params", {}).get("raw_text")
                    or command.params.get("raw_text")
                )
                source = command.source
                if raw_text and source == "nl_fallback":
                    logger.info(
                        "[TRACE] completion_record: immediate execute raw_text=%s source=%s",
                        raw_text[:60], source,
                    )
                    await self._handle_completion_record_pending(update, user_id, raw_text)
                    return
                self._pending_input[user_id] = "completion_record"
                await self._reply(update, _format_completion_prompt())
                return

            if command.command_type == "nightly_review":
                date_str = self._daily_review_date()
                self._write_daily_profile_stats(date_str)
                review = await self._run_daily_review(date_str=date_str, force=True)
                if not review:
                    await self._reply(update, "晚间总结生成失败。")
                    return
                await update.message.reply_text(review.payload.get("text", "晚间总结生成失败。"))
                await self._record_daily_review_sent(date_str, [user_id], "manual")
                return

            if command.command_type == "show_today":
                dashboard = _format_today_dashboard(self.state_engine)
                try:
                    level, reason_text, _ = _compute_plan_confidence(self.state_engine, self.settings)
                    confidence_line = f"\n\n计划可信度：{level}"
                    if reason_text:
                        confidence_line += f"（{reason_text}）"
                    dashboard += confidence_line
                except Exception:
                    pass
                await self._send_or_update_card(
                    update.effective_chat.id,
                    "today",
                    dashboard,
                )
                return

            if command.command_type == "current_advice":
                await self._send_or_update_card(
                    update.effective_chat.id,
                    "advice",
                    _format_current_advice(self.state_engine),
                    reply_markup=self._intervention_keyboard("current"),
                )
                return

            if command.command_type == "evening_plan_options":
                await self._reply(update, "今晚安排类型：", reply_markup=self._context_keyboard())
                return

            if command.command_type == "record_bad_state":
                bad_state_event = Event(
                    event_type=EventType.SUBJECTIVE_CONTEXT_ADDED,
                    aggregate_id=str(user_id),
                    aggregate_type=AggregateType.USER,
                    payload={
                        "kind": "context",
                        "text": "今天状态差",
                        "expires_at": _end_of_day_iso(),
                        "source": "telegram_reply_keyboard",
                    },
                )
                await self.pipeline.run(bad_state_event)
                await self._reply(update, "今天会降低提醒强度。")
                return

            if command.command_type == "quick_hydration":
                await self._reply(update, "选择补水量：", reply_markup=self._hydration_keyboard())
                return

            if command.command_type == "rebuild_state":
                await self._reply(update, "状态重算中。")
                count = await self._rebuild_state_from_events()
                if count <= 0:
                    await self._reply(update, "没有可重放的事件。")
                else:
                    await self._send_or_update_card(
                        update.effective_chat.id,
                        "today",
                        _format_today_dashboard(self.state_engine),
                    )
                    await self._reply(update, f"状态已重算：{count} 个事件。")
                return

            if command.command_type == "drink":
                args = event.payload.get("params", {}).get("args", "0")
                try:
                    amount = int(args) if args else 0
                except ValueError:
                    amount = 0
                if amount <= 0:
                    await self._reply(update, "用法：/饮水 500")
                    return
                drink_event = Event(
                    event_type=EventType.HYDRATION_LOGGED,
                    aggregate_id=str(user_id),
                    aggregate_type=AggregateType.USER,
                    payload={"amount_ml": amount},
                )
                await self.pipeline.run(drink_event)
                await self._reply(update, f"已记录 {amount}ml")
                return

            if command.command_type == "record_mood":
                args = event.payload.get("params", {}).get("args", "0")
                try:
                    score = int(args) if args else 0
                except (ValueError, TypeError):
                    score = 0
                if score < 1 or score > 10:
                    await self._reply(update, "用法：/情绪 1-10\n示例：/情绪 7")
                    return
                mood_event = Event(
                    event_type=EventType.MOOD_RECORDED,
                    aggregate_id=str(user_id),
                    aggregate_type=AggregateType.USER,
                    payload={"score": score},
                )
                await self.pipeline.run(mood_event)
                labels = {1: "极低", 2: "很低", 3: "偏低", 4: "略低", 5: "中性",
                          6: "略高", 7: "偏高", 8: "很高", 9: "极高", 10: "巅峰"}
                await self._reply(update, f"已记录情绪 {score}/10（{labels.get(score, '')}）")
                return

            if command.command_type == "record_note":
                args = event.payload.get("params", {}).get("args", "")
                if not args:
                    await update.message.reply_text("用法：/记录 xxx\n示例：/记录 今晚有饭局")
                    return
                note_event = Event(
                    event_type=EventType.SUBJECTIVE_CONTEXT_ADDED,
                    aggregate_id=str(user_id),
                    aggregate_type=AggregateType.USER,
                    payload={"kind": "note", "text": args},
                )
                await self.pipeline.run(note_event)
                await update.message.reply_text(f"已记录备注（今日午夜前有效）：{args[:100]}")
                return

            if command.command_type == "record_context":
                params = event.payload.get("params", {})
                args = params.get("args", "")
                # NL fallback passes "text" instead of "args"
                if not args and params.get("nl_fallback"):
                    args = params.get("text", "")
                if not args:
                    await update.message.reply_text("用法：/情境 xxx\n示例：/情境 今天下午比较疲劳")
                    return
                ctx_event = Event(
                    event_type=EventType.SUBJECTIVE_CONTEXT_ADDED,
                    aggregate_id=str(user_id),
                    aggregate_type=AggregateType.USER,
                    payload={"kind": "context", "text": args},
                )
                await self.pipeline.run(ctx_event)
                await update.message.reply_text(f"已记录情境（24h内有效）：{args[:100]}")
                return

            if command.command_type == "record_school_leave":
                from src.interface.telegram.router import _parse_date_schedule_input

                args = event.payload.get("params", {}).get("args", "")
                target_date = _parse_date_schedule_input(f"查课表 {args}") if args else None
                if not target_date:
                    target_date = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")
                leave_day = datetime.fromisoformat(target_date).replace(tzinfo=LOCAL_TZ)
                expires_at = (leave_day.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).isoformat()
                leave_event = Event(
                    event_type=EventType.SUBJECTIVE_CONTEXT_ADDED,
                    aggregate_id=str(user_id),
                    aggregate_type=AggregateType.USER,
                    payload={
                        "kind": "school_leave",
                        "text": args or "今日请假",
                        "date": target_date,
                        "expires_at": expires_at,
                    },
                )
                await self.pipeline.run(leave_event)
                await update.message.reply_text(
                    f"已记录 {target_date} 请假：当天学校课程不计入课表/压力/绘画排期。"
                )
                return

            if command.command_type == "show_registry":
                if self.course_registry:
                    self.course_registry.compute_scores()
                    courses = self.course_registry.get_all()
                    active = [(cid, c) for cid, c in courses.items() if c.active]
                    active.sort(key=lambda x: -x[1].attention_score)
                    if not active:
                        text = self._format_course_state_fallback()
                    else:
                        lines = [f"活跃课程（{len(active)} 门）：", ""]
                        for cid, c in active[:15]:
                            lines.append(f"  {c.course_name[:30]} [关注度 {c.attention_score:.2f}]")
                            lines.append(f"    待处理：{c.pending_deadlines} | 课表：{'有' if c.last_schedule_hit else '无'} | 同步：{'已' if c.last_synced else '未'}")
                        text = chr(10).join(lines)
                else:
                    text = self._format_course_state_fallback()
                await update.message.reply_text(text)
                return

            if command.command_type == "legacy_sync_tasks":
                await self._reply(update, "同步入口已拆分：请使用“同步课表”“同步作业”或“同步日历”。")
                return

            if command.command_type == "sync_refresh":
                await self._reply(update, "开始刷新：课表 / 日历 / 作业。")
                events_to_run = [
                    Event(
                        event_type=EventType.CONNECTOR_FETCH_REQUESTED,
                        aggregate_id=str(user_id),
                        aggregate_type=AggregateType.HOMEWORK,
                        payload={"source": "jwxt", "query": "weekly_schedule", "intent": "schedule_manual"},
                    ),
                    Event(
                        event_type=EventType.CONNECTOR_FETCH_REQUESTED,
                        aggregate_id=str(user_id),
                        aggregate_type=AggregateType.SYSTEM,
                        payload={"source": "google_calendar", "query": "upcoming", "intent": "calendar_manual"},
                    ),
                ]
                scope = None
                if self.course_registry:
                    self.course_registry.compute_scores()
                    scope = self.course_registry.get_active_scope_names()
                if scope:
                    events_to_run.append(Event(
                        event_type=EventType.CONNECTOR_FETCH_REQUESTED,
                        aggregate_id=str(user_id),
                        aggregate_type=AggregateType.HOMEWORK,
                        payload={"source": "chaoxing", "query": "homework_list", "scope": scope},
                    ))
                for refresh_event in events_to_run:
                    await self.pipeline.run(refresh_event)
                await self._send_or_update_card(
                    update.effective_chat.id,
                    "today",
                    _format_today_dashboard(self.state_engine),
                )
                # Post-sync calendar consistency review
                try:
                    review = await self._run_and_publish_consistency_review("manual_sync_refresh")
                    summary = format_review_summary(review, compact=True)
                    # Run repair if review found issues
                    repair_summary = ""
                    if review.get("overall_severity") != "ok":
                        try:
                            repair_result = await self._run_and_publish_calendar_repair(
                                review.get("findings", []), "manual_sync_refresh"
                            )
                            repair_summary = format_repair_summary(repair_result)
                        except Exception:
                            logger.exception("[REPAIR] post-sync repair failed")
                    text = "刷新完成。"
                    if summary:
                        text += f"\n\n{summary}"
                    if repair_summary:
                        text += f"\n\n{repair_summary}"
                    await self._reply(update, text)
                except Exception:
                    logger.exception("[REVIEW] post-sync review failed")
                    await self._reply(update, "刷新完成。")
                return

            if command.command_type in ("jwxt_sync", "sync_schedule"):
                fetch_event = Event(
                    event_type=EventType.CONNECTOR_FETCH_REQUESTED,
                    aggregate_id=str(user_id),
                    aggregate_type=AggregateType.HOMEWORK,
                    payload={
                        "source": "jwxt",
                        "query": "weekly_schedule",
                        "intent": "schedule_manual",
                    },
                )
                all_events = await self.pipeline.run(fetch_event)
                course_count = 0
                block_count = 0
                for e in all_events:
                    if e.event_type == EventType.COURSE_ACTIVATED:
                        course_count += 1
                    if e.event_type == EventType.TEMPORAL_BLOCK_ADDED:
                        block_count += 1
                # Post-sync review + repair
                try:
                    review = await self._run_and_publish_consistency_review("manual_jwxt_sync")
                    summary = format_review_summary(review, compact=True)
                    repair_summary = ""
                    if review.get("overall_severity") != "ok":
                        try:
                            repair_result = await self._run_and_publish_calendar_repair(
                                review.get("findings", []), "manual_jwxt_sync"
                            )
                            repair_summary = format_repair_summary(repair_result)
                        except Exception:
                            logger.exception("[REPAIR] post-jwxt-sync repair failed")
                    text = f"课表同步完成：{course_count} 门课程，本周 {block_count} 节。"
                    if summary:
                        text += f"\n{summary}"
                    if repair_summary:
                        text += f"\n\n{repair_summary}"
                    await self._reply(update, text)
                except Exception:
                    logger.exception("[REVIEW] post-jwxt-sync review failed")
                    await self._reply(
                        update,
                        f"课表同步完成：{course_count} 门课程，本周 {block_count} 节。"
                    )
                return

            if command.command_type == "calendar_sync":
                fetch_event = Event(
                    event_type=EventType.CONNECTOR_FETCH_REQUESTED,
                    aggregate_id=str(user_id),
                    aggregate_type=AggregateType.SYSTEM,
                    payload={"source": "google_calendar", "query": "upcoming", "intent": "calendar_manual"},
                )
                await self.pipeline.run(fetch_event)
                # Post-sync review + repair
                try:
                    review = await self._run_and_publish_consistency_review("manual_calendar_sync")
                    summary = format_review_summary(review, compact=True)
                    repair_summary = ""
                    if review.get("overall_severity") != "ok":
                        try:
                            repair_result = await self._run_and_publish_calendar_repair(
                                review.get("findings", []), "manual_calendar_sync"
                            )
                            repair_summary = format_repair_summary(repair_result)
                        except Exception:
                            logger.exception("[REPAIR] post-calendar-sync repair failed")
                    text = "日历同步完成。"
                    if summary:
                        text += f"\n{summary}"
                    if repair_summary:
                        text += f"\n\n{repair_summary}"
                    await self._reply(update, text)
                except Exception:
                    logger.exception("[REVIEW] post-calendar-sync review failed")
                    await self._reply(update, "日历同步完成。")
                return

            if command.command_type == "calendar_today":
                await self._send_or_update_card(
                    update.effective_chat.id,
                    "calendar_today",
                    _format_calendar_today(self.state_engine),
                )
                return

            if command.command_type == "calendar_context":
                await self._send_or_update_card(
                    update.effective_chat.id,
                    "calendar_context",
                    _format_calendar_context(self.state_engine),
                )
                return

            if command.command_type == "cognitive_learning":
                self._pending_input[user_id] = "cognitive_learning"
                await self._reply(update, "请发送你想记录的内容，我会用 DeepSeek 解析为记忆事件。")
                return

            if command.command_type == "cognitive_checkin":
                self._pending_input[user_id] = "cognitive_checkin"
                await self._reply(update, self._format_cognitive_checkin_template())
                return

            if command.command_type == "verbal_scheduling":
                raw_text = (
                    event.payload.get("params", {}).get("raw_text")
                    or command.params.get("raw_text")
                )
                source = command.source
                if raw_text and source in ("telegram", "nl_fallback"):
                    logger.info(
                        "[TRACE] verbal_scheduling: immediate execute raw_text=%s source=%s",
                        raw_text[:60], source,
                    )
                    await self._handle_verbal_scheduling_pending(update, user_id, raw_text)
                    return
                self._pending_input[user_id] = "verbal_scheduling"
                await self._reply(update, "请用自然语言描述你的日程安排，我会解析并创建日历事件。\n例如：明天下午3点到4点在A栋开会")
                return

            if command.command_type == "query_schedule_date":
                date_str = event.payload.get("params", {}).get("date", "")
                if date_str:
                    text = self._format_schedule_date(date_str)
                    await update.message.reply_text(text, reply_markup=self._schedule_page_keyboard(date_str))
                else:
                    text = "日期格式无效。请使用查课表 YYYY-MM-DD 格式。"
                    await update.message.reply_text(text)
                return

            if command.command_type == "sync_homework":
                args = event.payload.get("params", {}).get("args", "")
                scope = [s.strip() for s in args.split(",") if s.strip()] if args else None
                if not scope and self.course_registry:
                    self.course_registry.compute_scores()
                    scope = self.course_registry.get_active_scope_names()
                if not scope:
                    await self._reply(update, "还没有活跃课程范围。请先点“同步刷新数据”。")
                    return
                fetch_event = Event(
                    event_type=EventType.CONNECTOR_FETCH_REQUESTED,
                    aggregate_id=str(user_id),
                    aggregate_type=AggregateType.HOMEWORK,
                    payload={"source": "chaoxing", "query": "homework_list", "scope": scope},
                )
                all_events = await self.pipeline.run(fetch_event)
                # Route SYNC_STARTED and NOTIFICATION_SEND events back
                notifs = [e for e in all_events if e.event_type in (EventType.NOTIFICATION_SEND, EventType.SYNC_STARTED)]
                for n in notifs:
                    msg = n.payload.get("message", "")
                    if not msg:
                        cc = n.payload.get("course_count", "?")
                        msg = f"开始同步（{cc} 门课程）..."
                    if msg:
                        await update.message.reply_text(msg)
                if not notifs:
                    await update.message.reply_text("同步已在后台启动，请留意通知。")
                return

            if command.command_type in ("task_done", "task_skip", "task_delay"):
                from src.domain.behavior.handlers import handle_user_feedback
                feedback_events = await handle_user_feedback(event)
                for fe in feedback_events:
                    await self.pipeline.run(fe)
                await update.message.reply_text("✅ 已记录反馈")
                return

            if command.command_type == "show_behavior":
                text = _format_behavior_response(self.state_engine)
                await update.message.reply_text(text)
                return

            if command.command_type == "show_adaptive":
                text = _format_adaptive_response(self.state_engine)
                await update.message.reply_text(text)
                return

            if command.command_type == "show_patterns":
                text = _format_patterns_response(self.state_engine)
                await update.message.reply_text(text)
                return

            if command.command_type == "request_proposals":
                await self._handle_propose(update)
                return

            if command.command_type == "show_reflection":
                text = _format_reflection_response(self.state_engine)
                await update.message.reply_text(text)
                return

            if command.command_type == "show_trends":
                text = _format_trends_response(self.state_engine)
                await update.message.reply_text(text)
                return

            if command.command_type == "show_adaptation":
                text = _format_adaptation_response(self.state_engine)
                await update.message.reply_text(text)
                return

            # ── Art planning commands ──────────────────────────────────
            if command.command_type == "art_plan_greeting":
                await self._handle_art_greeting(update, text)
                return

            if command.command_type == "art_replan_prompt":
                self._pending_input[user_id] = "art_replan_reason"
                await self._reply(update, "收到。简单说一下偏离原因或现在的现实情况，我会重排今天。")
                return

            if command.command_type == "art_replan":
                args = event.payload.get("params", {}).get("args", "")
                await self._handle_art_replan(update, text, reason=args or "主动重排")
                return

            if command.command_type == "art_progress":
                await self._handle_art_progress(update, text)
                return

            if command.command_type == "fitness_progress":
                await self._handle_fitness_progress(update, text)
                return

            if command.command_type == "art_reality_insertion":
                await self._handle_art_insertion(update, text)
                return

            # ── Finance / Money Reality commands ──────────────────────────
            from src.domain.finance.handlers import (
                format_monthly_summary,
                format_outing_status,
                format_savings_progress,
                format_parent_plan,
                format_parent_30_day_schedule,
            )
            from src.domain.finance.parent_fund import (
                DEFAULT_FIXED_ITEMS,
                compute_next_safe_date,
                compute_weekly_total,
            )

            if command.command_type == "finance_help":
                await self._reply(update,
                    "💰 资金管理用法：\n\n"
                    "记一笔      — 获得输入指引\n"
                    "奶茶18     — 直接记录消费\n"
                    "本月资金    — 月资金概况\n"
                    "出去玩额度  — 约会预算\n"
                    "攒钱进度    — 储蓄目标\n"
                    "今天生活费到账1000 — 记收入\n"
                    "找爸爸要了150买画材 — 记要钱\n"
                    "想找爸爸要120买画材 — 要钱建议\n"
                    "要钱计划    — 要钱计划总览\n"
                    "30天要钱排期 — 固定项排期表"
                )
                return

            if command.command_type == "finance_batch_intake":
                await self._handle_finance_batch_intake(update, user_id, command.params.get("raw_text", ""))
                return

            if command.command_type == "finance_monthly":
                finance_state = self.state_engine.get_view("finance", "monthly")
                inflow = finance_state.get("inflow", 0)
                outflow = finance_state.get("outflow", 0)
                by_category = finance_state.get("by_category", {})
                outing_spent = finance_state.get("outing_spent", 0)
                text = format_monthly_summary(
                    inflow=inflow, outflow=outflow,
                    by_category=by_category,
                    outing_spent=outing_spent,
                    outing_budget=self.settings.finance_monthly_outing_budget,
                    savings_target=self.settings.finance_monthly_savings_target,
                )
                await self._reply(update, text)
                return

            if command.command_type == "finance_outing":
                finance_state = self.state_engine.get_view("finance", "monthly")
                outing_spent = finance_state.get("outing_spent", 0)
                text = format_outing_status(
                    outing_spent=outing_spent,
                    outing_budget=self.settings.finance_monthly_outing_budget,
                )
                await self._reply(update, text)
                return

            if command.command_type == "finance_savings":
                finance_state = self.state_engine.get_view("finance", "monthly")
                inflow = finance_state.get("inflow", 0)
                outflow = finance_state.get("outflow", 0)
                text = format_savings_progress(
                    inflow=inflow,
                    outflow=outflow,
                    savings_target=self.settings.finance_monthly_savings_target,
                )
                await self._reply(update, text)
                return

            if command.command_type == "parent_fund_plan":
                pf_state = self.state_engine.get_view("parent_funds", "current")
                request_log = pf_state.get("request_log", [])
                received_log = pf_state.get("received_log", [])
                fixed_items = pf_state.get("recurring_items", DEFAULT_FIXED_ITEMS)
                next_safe = compute_next_safe_date(
                    request_log,
                    self.settings.parent_request_safe_interval_days,
                )
                weekly_total = compute_weekly_total(request_log)
                text = format_parent_plan(
                    request_log=request_log,
                    received_log=received_log,
                    fixed_items=fixed_items,
                    next_safe_date=next_safe,
                    weekly_total=weekly_total,
                )
                await self._reply(update, text)
                return

            if command.command_type == "parent_fund_30d_schedule":
                pf_state = self.state_engine.get_view("parent_funds", "current")
                request_log = pf_state.get("request_log", [])
                received_log = pf_state.get("received_log", [])
                fixed_items = pf_state.get("recurring_items", DEFAULT_FIXED_ITEMS)
                planned_requests = pf_state.get("planned_requests", [])
                text = format_parent_30_day_schedule(
                    request_log=request_log,
                    received_log=received_log,
                    fixed_items=fixed_items,
                    planned_requests=planned_requests,
                    safe_interval_days=self.settings.parent_request_safe_interval_days,
                )
                await self._reply(update, text)
                return

            if command.command_type == "finance_transaction":
                # Natural language finance: go through pipeline, then format response
                from src.domain.finance.handlers import (
                    format_transaction_feedback,
                    format_parent_advice,
                )
                # Run through pipeline so state is updated
                all_events = await self.pipeline.run(event)

                # Determine response from produced events
                finance_events = [e for e in all_events if e.event_type in {
                    EventType.FINANCE_TRANSACTION_RECORDED,
                    EventType.FINANCE_INCOME_RECORDED,
                    EventType.PARENT_FUND_REQUEST_RECORDED,
                    EventType.PARENT_FUND_REQUEST_PLANNED,
                }]

                if not finance_events:
                    await self._reply(update, "未能解析为资金记录。试试「记一笔」看使用说明。")
                    return

                primary = finance_events[0]

                if primary.event_type == EventType.FINANCE_TRANSACTION_RECORDED:
                    finance_state = self.state_engine.get_view("finance", "monthly")
                    p = primary.payload
                    text = format_transaction_feedback(
                        amount=p["amount"],
                        category=p.get("category", "other"),
                        description=p.get("description", ""),
                        outing_spent=finance_state.get("outing_spent", 0),
                        outing_budget=self.settings.finance_monthly_outing_budget,
                        estimated_savings=max(0, finance_state.get("inflow", 0) - finance_state.get("outflow", 0)),
                        savings_target=self.settings.finance_monthly_savings_target,
                        current_month_outflow=finance_state.get("outflow", 0),
                        current_month_inflow=finance_state.get("inflow", 0),
                    )
                    aid = self._track_action(
                        user_id, "finance_transaction",
                        p.get("description", ""),
                        params={"amount": p["amount"], "category": p.get("category", "other")},
                    )
                    await self._reply_with_undo(update, text, aid)

                elif primary.event_type == EventType.FINANCE_INCOME_RECORDED:
                    finance_state = self.state_engine.get_view("finance", "monthly")
                    p = primary.payload
                    aid = self._track_action(
                        user_id, "finance_income",
                        f"收入 {p['amount']:.0f}元（{p.get('source', '其他')}）",
                        params={"amount": p["amount"], "source": p.get("source", "other")},
                    )
                    await self._reply_with_undo(update,
                        f"💰 已记录收入：{p['amount']:.0f} 元（{p.get('source', '其他')}）\n"
                        f"本月总收入：{finance_state.get('inflow', 0):.0f} 元", aid,
                    )

                elif primary.event_type == EventType.PARENT_FUND_REQUEST_RECORDED:
                    pf_state = self.state_engine.get_view("parent_funds", "current")
                    finance_state = self.state_engine.get_view("finance", "monthly")
                    request_log = pf_state.get("request_log", [])
                    next_safe = compute_next_safe_date(
                        request_log,
                        self.settings.parent_request_safe_interval_days,
                    )
                    weekly_total = compute_weekly_total(request_log)
                    p = primary.payload
                    income_event = next(
                        (e for e in finance_events if e.event_type == EventType.FINANCE_INCOME_RECORDED),
                        None,
                    )
                    aid = self._track_action(
                        user_id, "parent_fund_request",
                        f"要钱 {p['amount']:.0f}元",
                        params={
                            "amount": p["amount"],
                            "description": p.get("description", ""),
                            "income_amount": income_event.payload.get("amount") if income_event else 0,
                        },
                    )
                    income_line = ""
                    if income_event:
                        income_line = f"\n  已计入本月收入：{finance_state.get('inflow', 0):.0f} 元"
                    await self._reply_with_undo(update,
                        f"📝 已记录要钱：{p['amount']:.0f} 元\n"
                        f"  用途：{p.get('description', '')}\n"
                        f"  本周累计：{weekly_total:.0f} 元\n"
                        f"  下次安全要钱日：{next_safe.strftime('%m月%d日')}"
                        f"{income_line}", aid,
                    )

                elif primary.event_type == EventType.PARENT_FUND_REQUEST_PLANNED:
                    pf_state = self.state_engine.get_view("parent_funds", "current")
                    request_log = pf_state.get("request_log", [])
                    received_log = pf_state.get("received_log", [])
                    fixed_items = pf_state.get("recurring_items", DEFAULT_FIXED_ITEMS)

                    from src.domain.finance.parent_fund import (
                        compute_next_eligible_date,
                        schedule_request_advice,
                    )

                    last_request_date = None
                    for entry in reversed(request_log):
                        ts = entry.get("timestamp", "")
                        if ts:
                            try:
                                last_request_date = datetime.fromisoformat(ts)
                                break
                            except (ValueError, TypeError):
                                continue

                    p = primary.payload
                    last_date_str = last_request_date
                    advice = schedule_request_advice(
                        amount=p.get("amount", 0),
                        description=p.get("description", ""),
                        category=p.get("category", "other"),
                        fixed_items=fixed_items,
                        request_log=request_log,
                        received_log=received_log,
                        last_request_date=last_request_date,
                        safe_interval_days=self.settings.parent_request_safe_interval_days,
                        single_risk_threshold=self.settings.parent_request_single_risk_threshold,
                        weekly_risk_threshold=self.settings.parent_request_weekly_risk_threshold,
                    )
                    advice["requested_date"] = p.get("requested_date")
                    advice["amount"] = p.get("amount", 0)
                    advice["description"] = p.get("description", "")
                    text = format_parent_advice(advice)
                    await self._reply(update, text)

                return

            all_events = await self.pipeline.run(event)

            # Handle temporal commands directly (read-only state query)
            if command.command_type in ("show_today", "show_free_today", "show_week_load", "show_state", "show_stress", "show_capacity", "plan_today", "plan_tomorrow", "focus_window"):
                if command.command_type == "show_state":
                    text = _format_state_response(self.state_engine)
                elif command.command_type in ("show_stress", "show_capacity"):
                    text = _format_cognition_response(command.command_type, self.state_engine)
                elif command.command_type in ("plan_today", "plan_tomorrow", "focus_window"):
                    text = _format_planning_response(command.command_type, self.state_engine)
                else:
                    text = _format_temporal_response(command.command_type, self.state_engine)
                await update.message.reply_text(text)
                return

            if command.command_type in ("check_homework", "check_schedule"):
                if command.command_type == "check_schedule":
                    date_str = datetime.now(LOCAL_TZ).date().isoformat()
                    text = self._format_schedule_date(date_str)
                    await update.message.reply_text(text, reply_markup=self._schedule_page_keyboard(date_str))
                    return
                else:
                    text = _format_homework_response(self.state_engine)
                await update.message.reply_text(text)
                return

            # Extract notification events and send them back
            logger.info("[NOTIFY] checking notifications: total_events=%d", len(all_events))
            notifications = [
                e for e in all_events
                if e.event_type in (EventType.NOTIFICATION_SEND, EventType.INTERVENTION_TRIGGERED)
            ]

            if notifications:
                for notif in notifications:
                    text = format_output(notif)
                    if text:
                        await update.message.reply_text(text)
            else:
                await update.message.reply_text("✅ 完成，暂无新内容")

            logger.info("[TRACE] handle_message: pipeline + dispatch complete, checking notifications")
        except Exception as exc:
            logger.exception("[TRACE] handle_message: EXCEPTION caught")
            logger.exception("pipeline error")
            await update.message.reply_text(format_error(str(exc)))

    # ── Art planning handlers ──────────────────────────────────────────

    def _art_plan_request_event(self, reason: str) -> Event:
        """Build an ART_PLAN_REQUESTED event from current runtime state."""
        all_derived = self.state_engine.get_all_derived()
        blocks = self.state_engine.get_temporal_blocks()
        return Event(
            event_type=EventType.ART_PLAN_REQUESTED,
            aggregate_id="art_today",
            aggregate_type=AggregateType.ART,
            payload={
                "blocks": [b.to_dict() if hasattr(b, "to_dict") else b for b in blocks],
                "state": {"derived": all_derived},
                "reason": reason,
            },
        )

    async def _run_art_plan(self, reason: str) -> Event | None:
        """Run the art planning event chain and return the created plan event."""
        events = await self.pipeline.run(self._art_plan_request_event(reason))
        for event in reversed(events):
            if event.event_type == EventType.ART_PLAN_CREATED:
                return event
        return None

    async def _refresh_calendar_before_art_planning(self, reason: str) -> None:
        """Refresh Google Calendar before replanning against external edits."""
        try:
            await self.pipeline.run(Event(
                event_type=EventType.CONNECTOR_FETCH_REQUESTED,
                aggregate_id="art_calendar_refresh",
                aggregate_type=AggregateType.SYSTEM,
                payload={
                    "source": "google_calendar",
                    "query": "upcoming",
                    "intent": reason,
                },
            ))
        except Exception as exc:
            logger.warning("art planning calendar refresh failed: %s", exc)

    async def _repair_calendar_before_art_planning(self, reason: str) -> str:
        """Review and repair calendar conflicts before creating new art blocks."""
        try:
            review = await self._run_and_publish_consistency_review(reason)
            if review.get("overall_severity") == "ok":
                return ""
            repair = await self._run_and_publish_calendar_repair(review.get("findings", []), reason)
            return format_repair_summary(repair)
        except Exception as exc:
            logger.warning("art planning calendar repair failed: %s", exc)
            return ""

    def _parse_local_datetime(self, value: str) -> datetime:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return dt.replace(tzinfo=LOCAL_TZ)
        return dt.astimezone(LOCAL_TZ)

    async def _replace_managed_art_calendar_blocks(
        self,
        plan_payload: dict[str, Any],
        blocks: list[dict[str, Any]],
        plan_id: str,
    ) -> tuple[list[dict[str, Any]], dict[str, int | str]]:
        """Replace today's managed art blocks in Google Calendar.

        This only touches events marked as managed_by=cognitive_os and
        source=<art_managed_calendar_source>.

        Before creating each new block, verifies non-overlap with current
        busy blocks (JWXT, Google Calendar events, etc.). Overlapping
        blocks are skipped and reported as "overlap_skipped".
        """
        from src.executor.google_calendar.executor import GoogleCalendarExecutor

        summary: dict[str, int | str] = {"created": 0, "deleted": 0, "failed": 0, "overlap_skipped": 0}
        executor = GoogleCalendarExecutor(
            use_mock=self.settings.google_calendar_mock,
            settings=self.settings,
        )

        if not executor.use_mock and not self.settings.google_calendar_write_enabled:
            summary["skipped"] = "calendar_write_disabled"
            return blocks, summary

        date_str = plan_payload.get("date") or datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")
        day_start = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=LOCAL_TZ)
        day_end = day_start + timedelta(days=1)

        # ── Delete old managed art blocks ──────────────────────────────
        existing = await executor.list_managed_art_blocks(day_start, day_end)
        for item in existing:
            event_id = item.get("id")
            if not event_id:
                continue
            await executor.delete_managed_art_block(event_id)
            summary["deleted"] = int(summary["deleted"]) + 1

        # ── Overlap guard: load current busy blocks from state engine ──
        from src.domain.planning.time_windows import (
            load_busy_intervals,
            detect_overlap,
            art_exclude_filter,
        )

        state_blocks = self.state_engine.get_temporal_blocks()
        busy_intervals = load_busy_intervals(
            state_blocks,
            day_start,
            day_end,
            exclude_filter=art_exclude_filter,
        )

        updated_blocks: list[dict[str, Any]] = []
        target_minutes = int(plan_payload.get("target_minutes", 0) or 0)
        for block in blocks:
            next_block = dict(block)
            try:
                start = self._parse_local_datetime(next_block["start"])
                end = self._parse_local_datetime(next_block["end"])
                if end <= start:
                    raise ValueError("art block end must be after start")

                # Overlap check — second defense layer
                if detect_overlap(start, end, busy_intervals):
                    logger.warning(
                        "OVERLAP GUARD: skipping art block '%s' %s-%s (conflicts with existing busy block)",
                        next_block.get("title", "?"), start.isoformat(), end.isoformat(),
                    )
                    summary["overlap_skipped"] = int(summary["overlap_skipped"]) + 1
                    next_block["overlap_skipped"] = True
                    updated_blocks.append(next_block)
                    continue

                result = await executor.create_managed_art_block(
                    title=next_block.get("title", "绘画训练"),
                    start=start,
                    end=end,
                    plan_id=plan_id,
                    rationale=plan_payload.get("day_type", ""),
                    target_minutes=target_minutes,
                    calendar_id=self.settings.art_calendar_id,
                )
                if result.get("ok"):
                    next_block["calendar_event_id"] = result.get("event_id", "")
                    summary["created"] = int(summary["created"]) + 1
                else:
                    summary["failed"] = int(summary["failed"]) + 1
            except Exception as exc:
                logger.warning("managed art calendar block write failed: %s", exc)
                summary["failed"] = int(summary["failed"]) + 1
            updated_blocks.append(next_block)

        return updated_blocks, summary

    async def _materialize_art_plan(self, art_plan_event: Event) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Write a created art plan to managed Calendar events and Obsidian."""
        payload = art_plan_event.payload
        blocks = [dict(b) for b in payload.get("blocks", [])]
        summary: dict[str, Any] = {"calendar": {}, "obsidian_written": False}

        try:
            blocks, calendar_summary = await self._replace_managed_art_calendar_blocks(
                payload,
                blocks,
                str(art_plan_event.event_id),
            )
            summary["calendar"] = calendar_summary
            await self.pipeline.run(Event(
                event_type=EventType.ART_PLAN_UPDATED,
                aggregate_id="art_today",
                aggregate_type=AggregateType.ART,
                causation_id=art_plan_event.event_id,
                payload={
                    "target_minutes": payload.get("target_minutes"),
                    "day_type": payload.get("day_type"),
                    "blocks": blocks,
                },
            ))
        except Exception as exc:
            logger.warning("art plan calendar materialization failed: %s", exc)
            summary["calendar_error"] = str(exc)

        try:
            from src.integrations.obsidian_daily import ObsidianDailyWriter

            writer = ObsidianDailyWriter(self.settings)
            writer.write_art_plan(
                target_minutes=payload.get("target_minutes", 0),
                blocks=blocks,
            )
            summary["obsidian_written"] = True
            await self.pipeline.run(Event(
                event_type=EventType.ART_OBSIDIAN_DAILY_UPDATED,
                aggregate_id="art_today",
                aggregate_type=AggregateType.ART,
                causation_id=art_plan_event.event_id,
                payload={"section": "art_plan"},
            ))
        except Exception as exc:
            logger.warning("Obsidian write failed: %s", exc)
            summary["obsidian_error"] = str(exc)

        return blocks, summary

    def _format_art_plan_summary(self, payload: dict[str, Any], blocks: list[dict[str, Any]]) -> str:
        day_type = payload.get("day_type", "normal")
        target = payload.get("target_minutes", 0)
        planned = sum(int(b.get("duration_min", 0) or 0) for b in blocks)
        unscheduled = int(payload.get("unscheduled_minutes", 0) or 0)

        day_labels = {"ideal": "理想", "normal": "正常", "high_pressure": "高压", "recovery": "恢复"}
        lines = [
            f"今日绘画计划（{day_labels.get(day_type, day_type)}）",
            f"目标：{target} 分钟 | 已排：{planned} 分钟",
        ]

        # Warn if capacity shortage
        if unscheduled > 0:
            lines.append(
                f"⚠️ 今天只能安全安排 {planned} 分钟画画，"
                f"剩余 {unscheduled} 分钟不硬塞（空窗不足）。"
            )
        for i, b in enumerate(blocks, 1):
            start_s = b.get("start", "")[11:16]
            end_s = b.get("end", "")[11:16]
            lines.append(f"  {i}. {start_s}-{end_s} {b.get('title', '练习')}（{b.get('duration_min', 0)}min）")
        lines.append("")
        lines.append("开始练习后告诉我进度，例如「完成 画画 2小时 人体速写 12张」")
        return "\n".join(lines)

    def _format_schedule_today_and_tomorrow(self) -> str:
        today = datetime.now(LOCAL_TZ).date()
        tomorrow = today + timedelta(days=1)
        return "\n\n".join([
            self._format_schedule_date(today.isoformat()),
            self._format_schedule_date(tomorrow.isoformat()),
        ])

    async def _write_inserted_reality_to_calendar(
        self,
        parsed: dict[str, Any],
        user_id: int,
    ) -> tuple[bool, str]:
        """Create a user-inserted calendar event through the accepted proposal executor path."""
        from src.core.proposal import Proposal, ProposalStatus, ProposalType, TargetSystem
        from src.executor.google_calendar.executor import GoogleCalendarExecutor

        proposal = Proposal(
            proposal_type=ProposalType.CREATE_CALENDAR_BLOCK,
            target_system=TargetSystem.GOOGLE_CALENDAR,
            action_payload={
                "title": parsed.get("title", "临时事项"),
                "start": parsed.get("start", ""),
                "end": parsed.get("end", ""),
                "description": parsed.get("description", ""),
                "location": parsed.get("location", ""),
            },
            reason="user_natural_language_insertion",
            confidence=1.0,
            status=ProposalStatus.ACCEPTED,
            user_id=str(user_id),
        )
        executor = GoogleCalendarExecutor(
            use_mock=self.settings.google_calendar_mock,
            settings=self.settings,
        )
        result_event = await executor.execute(proposal)
        await self.pipeline.run(result_event)

        if result_event.event_type == EventType.EXECUTION_COMPLETED:
            return True, str(result_event.payload.get("event_id", ""))
        return False, str(result_event.payload.get("error", "calendar_write_failed"))

    # ── System self-check panel ────────────────────────────────────────────

    async def _format_selfcheck(self) -> str:
        """Format system self-check panel in Chinese."""
        lines = ["系统自检", "========"]

        # Bot status
        lines.append(f"Bot: 运行中")

        # Scheduler (from settings)
        interval = self.settings.schedule_check_interval_minutes
        lines.append(f"调度器: 配置 ({interval}min 检查间隔)")

        # JWXT
        schedule_view = self.state_engine.get_view("schedule", "latest")
        if schedule_view and schedule_view.get("last_parsed"):
            lines.append(f"JWXT: OK (最后同步: {str(schedule_view['last_parsed'])[:16]})")
        else:
            lines.append("JWXT: 未知 (尚无同步数据)")

        # Chaoxing
        hw_all = self.state_engine.get_all("homework")
        hw_count = len(hw_all) if hw_all else 0
        lines.append(f"超星: {'OK' if hw_count > 0 else '无数据'} ({hw_count} 条作业)")

        # Google Calendar
        temporal = self.state_engine.get_view("temporal", "projection")
        cal_sync = temporal.get("calendar_sync", {}) if temporal else {}
        if cal_sync and cal_sync.get("completed_at"):
            cal_status = cal_sync.get("status", "?")
            lines.append(f"Google 日历: {cal_status}")
            lines.append(f"  最后读取: {str(cal_sync['completed_at'])[:16]}")
        else:
            lines.append("Google 日历: 无同步数据")
        write_enabled = self.settings.google_calendar_write_enabled
        lines.append(f"  写入: {'启用' if write_enabled else '禁用'}")

        # Momo
        vocab = self.state_engine.get_view("vocab", "momo")
        if vocab:
            stale = vocab.get("stale", True)
            progress = vocab.get("progress", {})
            lines.append(f"Momo: {'缓存' if stale else 'OK'}")
            lines.append(f"  进度: {progress.get('finished', 0)}/{progress.get('total', 0)}")
        else:
            lines.append("Momo: 无数据")

        # Obsidian
        from pathlib import Path
        obsidian_path = Path(self.settings.obsidian_vault_path)
        daily_writable = os.access(str(obsidian_path), os.W_OK) if obsidian_path.exists() else False
        from src.integrations.obsidian_daily import get_audit
        audit = get_audit()
        lines.append(f"Obsidian: {'可写' if daily_writable else '不可写或不存在'}")
        if audit.get("last_write_path"):
            lines.append(f"  最后写入: {audit['last_write_path']}")
            lines.append(f"  最后章节: {audit.get('last_section', '?')}")
            lines.append(f"  写入次数: {audit.get('write_count', 0)}")
            if audit.get("skipped_duplicate_count", 0) > 0:
                lines.append(f"  跳过重复: {audit['skipped_duplicate_count']}")

        # EventStore
        if self.event_store:
            try:
                count = await self.event_store.count()
                last_seq = await self.event_store.last_sequence()
                lines.append(f"事件存储: {count} 个事件 (最后序列: {last_seq})")
            except Exception:
                lines.append("事件存储: 查询失败")
        else:
            lines.append("事件存储: 未连接")

        return "\n".join(lines)

    # ── Lightweight chain smoke test ───────────────────────────────────────

    async def _run_selftest(self) -> str:
        """Run lightweight chain smoke test."""
        import os
        from pathlib import Path
        from uuid import uuid4

        results: list[tuple[str, str]] = []

        # 1. Route parsing test
        try:
            from src.interface.telegram.router import parse_message
            cmd = parse_message("/today", 99999)
            assert cmd is not None and cmd.command_type == "show_today"
            results.append(("路由解析", "OK"))
        except Exception as e:
            results.append(("路由解析", f"FAIL: {e}"))

        # 2. Obsidian writer test (dry-run: write idempotent test line)
        try:
            from src.integrations.obsidian_daily import ObsidianDailyWriter, reset_audit
            writer = ObsidianDailyWriter(self.settings)
            test_line = f"系统自检 {datetime.now(LOCAL_TZ).isoformat()}"
            written = writer.write_event_line_idempotent(
                line=test_line,
                event_id=f"selftest-{uuid4().hex[:8]}",
            )
            results.append(("Obsidian 写入", "OK" if written else "WARN: 跳过(重复)"))
        except Exception as e:
            results.append(("Obsidian 写入", f"FAIL: {e}"))

        # 3. Google Calendar auth check (cheap: check token file)
        try:
            token_path = Path(self.settings.google_calendar_token_path)
            creds_path = Path(self.settings.google_calendar_credentials_path)
            if token_path.exists():
                import json
                token = json.loads(token_path.read_text(encoding="utf-8"))
                expiry = token.get("expiry", "")
                results.append(("Google 日历凭证", f"OK (令牌到期: {str(expiry)[:10]})"))
            elif creds_path.exists():
                results.append(("Google 日历凭证", "WARN: 有凭证文件但无令牌"))
            else:
                results.append(("Google 日历凭证", "WARN: 无凭证文件"))
        except Exception as e:
            results.append(("Google 日历凭证", f"FAIL: {e}"))

        # 4. JWXT cookie check
        try:
            cookie_path = Path(self.settings.jwxt_cookies_path)
            if cookie_path.exists() and cookie_path.stat().st_size > 10:
                results.append(("JWXT 会话", "OK"))
            else:
                results.append(("JWXT 会话", "WARN: 无有效 Cookie"))
        except Exception as e:
            results.append(("JWXT 会话", f"FAIL: {e}"))

        # 5. Chaoxing state check
        try:
            from pathlib import Path
            state_path = Path(self.settings.data_dir) / "chaoxing_state.json"
            if state_path.exists() and state_path.stat().st_size > 10:
                results.append(("超星 会话", "OK"))
            else:
                results.append(("超星 会话", "WARN: 无有效状态"))
        except Exception as e:
            results.append(("超星 会话", f"FAIL: {e}"))

        # 6. Momo cache check
        try:
            cache_path = Path(self.settings.momo_cache_path)
            if cache_path.exists():
                import json
                cache_data = json.loads(cache_path.read_text(encoding="utf-8"))
                last_sync = cache_data.get("last_sync", "")
                results.append(("Momo 缓存", f"OK (最后同步: {str(last_sync)[:16]})"))
            else:
                results.append(("Momo 缓存", "WARN: 缓存文件不存在"))
        except Exception as e:
            results.append(("Momo 缓存", f"FAIL: {e}"))

        # Format results
        lines = ["真实链路烟雾测试", "================"]
        for name, status in results:
            if status.startswith("OK"):
                icon = "OK"
            elif status.startswith("WARN"):
                icon = "WARN"
            else:
                icon = "FAIL"
            lines.append(f"[{icon}] {name}: {status}")

        ok_count = sum(1 for _, s in results if s.startswith("OK"))
        warn_count = sum(1 for _, s in results if s.startswith("WARN"))
        fail_count = sum(1 for _, s in results if s.startswith("FAIL"))
        lines.append("")
        lines.append(f"总计: {ok_count} OK, {warn_count} WARN, {fail_count} FAIL")

        return "\n".join(lines)

    # ── Storage status ─────────────────────────────────────────────────────

    async def _format_storage_status(self) -> str:
        """Report storage/database/file status in Chinese."""
        import shutil
        from pathlib import Path

        lines = ["存储状态", "========"]
        data_dir = Path(self.settings.data_dir).absolute()
        lines.append(f"数据目录: {data_dir}")

        # SQLite DB size
        db_url = self.settings.database_url
        db_path_str = db_url.replace("sqlite+aiosqlite:///", "")
        db_file = Path(db_path_str)
        if not db_file.is_absolute():
            db_file = data_dir / db_path_str
        if db_file.exists():
            size_mb = db_file.stat().st_size / (1024 * 1024)
            lines.append(f"SQLite 数据库: {size_mb:.1f} MB")
        else:
            lines.append(f"SQLite 数据库: 文件未找到 ({db_file})")

        # Event count
        if self.event_store:
            try:
                count = await self.event_store.count()
                last_seq = await self.event_store.last_sequence()
                lines.append(f"事件日志: {count} 个事件 (最后序列: {last_seq})")
            except Exception as e:
                lines.append(f"事件日志: 查询失败 ({e})")
        else:
            lines.append("事件日志: 未连接 EventStore")

        # Snapshot file
        snap_path = Path(self.settings.snapshot_path)
        if snap_path.exists():
            size_kb = snap_path.stat().st_size / 1024
            lines.append(f"状态快照: {size_kb:.1f} KB")
        else:
            lines.append("状态快照: 无文件")

        # Log files
        log_files = sorted(data_dir.glob("*.log"))
        if log_files:
            total_log_size = sum(f.stat().st_size for f in log_files) / (1024 * 1024)
            lines.append(f"日志文件: {len(log_files)} 个文件, {total_log_size:.1f} MB 总计")
            for lf in sorted(log_files, key=lambda f: -f.stat().st_size)[:3]:
                lines.append(f"  - {lf.name}: {lf.stat().st_size / 1024:.1f} KB")
        else:
            lines.append("日志文件: 无")

        # Free space
        try:
            usage = shutil.disk_usage(data_dir.anchor if data_dir.anchor else str(data_dir))
            free_gb = usage.free / (1024**3)
            total_gb = usage.total / (1024**3)
            lines.append(f"磁盘空间: {free_gb:.1f} GB 空闲 / {total_gb:.1f} GB 总计")
        except Exception:
            lines.append("磁盘空间: 无法查询")

        return "\n".join(lines)

    async def _run_storage_vacuum(self) -> str:
        """Run VACUUM on SQLite database (manual, explicit operation only)."""
        try:
            from src.storage.db import get_session
            from sqlalchemy import text
            session = await get_session()
            try:
                await session.execute(text("VACUUM"))
                await session.commit()
            finally:
                await session.close()
            # Re-check size
            db_url = self.settings.database_url
            db_path_str = db_url.replace("sqlite+aiosqlite:///", "")
            db_file = Path(db_path_str)
            if db_file.exists():
                size_mb = db_file.stat().st_size / (1024 * 1024)
                return f"VACUUM 完成。数据库大小: {size_mb:.1f} MB"
            return "VACUUM 完成。"
        except Exception as e:
            return f"VACUUM 失败: {e}"

    async def _handle_art_greeting(self, update: Update, text: str) -> None:
        """Handle '早安' — enhanced day-start entry.

        Supports combined input like:
          '早安，今天下午三点健身，晚上画画，心情一般'
          '早安 今天安排：上午上课 下午画画2h 晚上健身 心情6'

        Triggers data refresh (non-blocking), writes Obsidian daily note
        with fixed structure, and emits mood/context events.
        """
        if not update.message:
            return

        user_id = update.effective_user.id if update.effective_user else 0

        # Parse combined morning input
        from src.interface.telegram.router import parse_morning_combined, _strip_greeting_prefix

        content = _strip_greeting_prefix(text)
        parsed = parse_morning_combined(text) if content else {}
        mood_score = parsed.get("mood_score") if parsed else None
        arrangements = parsed.get("arrangements", []) if parsed else []
        art_minutes = parsed.get("art_minutes") if parsed else None

        # Prepare initial reply header
        context_bits = []
        if mood_score is not None:
            labels = {1: "极低", 2: "很低", 3: "偏低", 4: "略低", 5: "中性",
                      6: "略高", 7: "偏高", 8: "很高", 9: "极高", 10: "巅峰"}
            context_bits.append(f"情绪 {mood_score}/10（{labels.get(mood_score, '')}）")
        if arrangements:
            short = "、".join(arrangements[:3])
            if len(arrangements) > 3:
                short += f" 等{len(arrangements)}项"
            context_bits.append(f"安排：{short}")
        if art_minutes is not None:
            context_bits.append(f"画图目标{art_minutes}min")
        header = "早安！"
        if context_bits:
            header += " 已记录：" + "，".join(context_bits)
        header += " 正在规划绘画时间..."

        # Emit mood event if found
        if mood_score is not None:
            await self.pipeline.run(Event(
                event_type=EventType.MOOD_RECORDED,
                aggregate_id=str(user_id),
                aggregate_type=AggregateType.USER,
                payload={"score": mood_score},
            ))

        # Emit context events for arrangements
        if arrangements:
            arrangement_text = "、".join(arrangements)
            await self.pipeline.run(Event(
                event_type=EventType.SUBJECTIVE_CONTEXT_ADDED,
                aggregate_id=str(user_id),
                aggregate_type=AggregateType.USER,
                payload={
                    "kind": "context",
                    "text": f"今日安排：{arrangement_text}",
                    "source": "morning_greeting",
                    "expires_at": _end_of_day_iso(),
                },
            ))

        # Emit art target as context note if provided
        if art_minutes is not None:
            await self.pipeline.run(Event(
                event_type=EventType.SUBJECTIVE_CONTEXT_ADDED,
                aggregate_id=str(user_id),
                aggregate_type=AggregateType.USER,
                payload={
                    "kind": "note",
                    "text": f"今日绘画目标：{art_minutes}分钟",
                    "source": "morning_greeting",
                    "expires_at": _end_of_day_iso(),
                },
            ))

        # ── Trigger data refreshes (non-blocking) ─────────────────────────
        refresh_sources: list[str] = []
        refresh_events: list[Event] = []

        # JWXT schedule refresh
        refresh_events.append(Event(
            event_type=EventType.CONNECTOR_FETCH_REQUESTED,
            aggregate_id=str(user_id),
            aggregate_type=AggregateType.HOMEWORK,
            payload={"source": "jwxt", "query": "weekly_schedule", "intent": "schedule_manual"},
        ))
        refresh_sources.append("课表")

        # Google Calendar refresh
        refresh_events.append(Event(
            event_type=EventType.CONNECTOR_FETCH_REQUESTED,
            aggregate_id=str(user_id),
            aggregate_type=AggregateType.SYSTEM,
            payload={"source": "google_calendar", "query": "upcoming"},
        ))
        refresh_sources.append("日历")

        # Chaoxing homework refresh
        scope = None
        if self.course_registry:
            self.course_registry.compute_scores()
            scope = self.course_registry.get_active_scope_names()
        if scope:
            refresh_events.append(Event(
                event_type=EventType.CONNECTOR_FETCH_REQUESTED,
                aggregate_id=str(user_id),
                aggregate_type=AggregateType.HOMEWORK,
                payload={"source": "chaoxing", "query": "homework_list", "scope": scope},
            ))
            refresh_sources.append("作业")

        # Momo vocab refresh
        refresh_events.append(Event(
            event_type=EventType.CONNECTOR_FETCH_REQUESTED,
            aggregate_id=str(user_id),
            aggregate_type=AggregateType.VOCAB,
            payload={"source": "momo_vocab", "query": "vocab_progress", "intent": "morning_refresh"},
        ))
        refresh_sources.append("单词")

        # Fire refreshes in background (don't block Telegram response)
        for refresh_evt in refresh_events:
            try:
                asyncio.create_task(self.pipeline.run(refresh_evt))
            except Exception as exc:
                logger.warning("morning refresh task failed for %s: %s", refresh_evt.payload.get("source", "?"), exc)

        # ── Track morning refresh state for second report ─────────────────
        date_str = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")
        refresh_key = f"{user_id}:{date_str}"
        if refresh_key not in self._morning_refresh:
            self._morning_refresh[refresh_key] = {
                "sources": set(refresh_sources),
                "completed": set(),
                "started_at": time.monotonic(),
                "report_sent": False,
            }
            asyncio.create_task(self._monitor_morning_refresh(update, refresh_key, user_id, text))

        # ── Write Obsidian daily note with fixed structure ────────────────
        try:
            from src.integrations.obsidian_daily import ObsidianDailyWriter
            writer = ObsidianDailyWriter(self.settings)
            writer.write_morning_entry(
                mood_score=mood_score,
                arrangements=arrangements,
                art_target_minutes=art_minutes,
                triggered_refresh=refresh_sources,
            )
        except Exception as exc:
            logger.warning("Obsidian morning entry write failed: %s", exc)

        await self._reply(update, header)

        try:
            art_plan_event = await self._run_art_plan("morning_greeting")
            if art_plan_event is None:
                await self._reply(update, "规划失败，无法生成绘画方案。")
                return

            blocks, materialized = await self._materialize_art_plan(art_plan_event)
            reply = self._format_art_plan_summary(art_plan_event.payload, blocks)
            calendar_summary = materialized.get("calendar", {})
            if calendar_summary.get("skipped"):
                reply += "\n\n日历写入未启用，只写入了 Obsidian。"
            elif int(calendar_summary.get("created", 0) or 0) > 0:
                reply += f"\n\n已写入日历：{calendar_summary.get('created')} 个绘画块。"
            overlap_skipped = int(calendar_summary.get("overlap_skipped", 0) or 0)
            if overlap_skipped > 0:
                reply += f"\n⚠️ {overlap_skipped} 个绘画块因时间重叠被跳过（日历已有安排）。"

            # Append data refresh status
            if refresh_sources:
                reply += f"\n\n数据刷新中：{'、'.join(refresh_sources)}。若遇延迟请稍后查看。"

            # Append plan confidence
            try:
                level, reason_text, _ = _compute_plan_confidence(self.state_engine, self.settings)
                reply += f"\n\n今日计划可信度：{level}"
                if reason_text:
                    reply += f"\n原因：{reason_text}"
            except Exception:
                pass

            await self._reply(update, reply)
        except Exception as exc:
            logger.exception("art greeting failed")
            await self._reply(update, f"早安规划出错：{_friendly_error(str(exc))}")

    async def _monitor_morning_refresh(
        self, update: Update, refresh_key: str, user_id: int, original_text: str,
    ) -> None:
        """Monitor background morning refreshes and send a compact second report."""
        try:
            await asyncio.sleep(12)  # bounded wait for background refreshes
            state = self._morning_refresh.get(refresh_key)
            if not state or state["report_sent"]:
                return

            sources = state["sources"]
            derived = self.state_engine.get_all_derived()
            cognition = derived.get("cognition", {})
            planning = derived.get("planning", {})

            # Build report lines
            report_lines = []

            # Refresh status
            status_parts = []
            if "课表" in sources:
                jwxt_view = self.state_engine.get_view("schedule", "latest")
                status_parts.append(f"课表 {'OK' if jwxt_view else '缓存'}")
            if "作业" in sources:
                hw_count = len(self.state_engine.get_all("homework"))
                status_parts.append(f"作业 {'OK' if hw_count > 0 else '缓存'}")
            if "日历" in sources:
                cal_blocks = [
                    b for b in self.state_engine.get_temporal_blocks()
                    if str(b.source) == "google_calendar"
                ]
                status_parts.append(f"日历 {'OK' if cal_blocks else '缓存'}")
            if "单词" in sources:
                vocab = self.state_engine.get_view("vocab", "momo")
                stale = vocab.get("stale", True) if vocab else True
                status_parts.append(f"背词{'缓存' if stale else 'OK'}")
            report_lines.append(f"数据刷新完成：{'，'.join(status_parts)}")

            # Pressure
            stress = float(cognition.get("stress_projection", 0) or 0)
            pressure_label = "低" if stress < 0.3 else "中" if stress < 0.6 else "高"
            report_lines.append(f"今日压力：{pressure_label}")

            # Art suggestion
            windows = planning.get("recommended_windows", [])
            if windows:
                w = windows[0]
                report_lines.append(f"画画建议：{w.get('time', '?')} · {w.get('label', '可用窗口')}")
            else:
                report_lines.append("画画建议：暂无可推荐窗口，稍后重试 /今日计划")

            report = "\n".join(report_lines)
            state["report_sent"] = True

            if update.effective_chat:
                await self._app.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=report,
                )
        except Exception as exc:
            logger.debug("morning refresh monitor error (non-fatal): %s", exc)

    async def _handle_art_replan(self, update: Update, text: str, reason: str = "主动重排") -> None:
        """Record plan drift and regenerate the rest of today's art plan."""
        if not update.message:
            return

        user_id = update.effective_user.id if update.effective_user else 0
        self._pending_input.pop(user_id, None)
        clean_reason = (reason or "主动重排").strip()

        await self._reply(update, "收到，正在按当前现实重排今天。")

        try:
            await self.pipeline.run(Event(
                event_type=EventType.SUBJECTIVE_CONTEXT_ADDED,
                aggregate_id=str(user_id),
                aggregate_type=AggregateType.USER,
                payload={
                    "kind": "context",
                    "text": f"今日绘画计划偏移：{clean_reason}",
                    "source": "art_replan",
                    "expires_at": _end_of_day_iso(),
                },
            ))
            await self.pipeline.run(Event(
                event_type=EventType.ART_PLAN_REBALANCED,
                aggregate_id="art_today",
                aggregate_type=AggregateType.ART,
                payload={"reason": "user_replan", "note": clean_reason},
            ))

            try:
                from src.integrations.obsidian_daily import ObsidianDailyWriter

                ObsidianDailyWriter(self.settings).write_event_line(f"绘画计划重排：{clean_reason}")
            except Exception as exc:
                logger.warning("Obsidian replan log failed: %s", exc)

            await self._refresh_calendar_before_art_planning("art_replan")
            repair_summary = await self._repair_calendar_before_art_planning("art_replan")
            art_plan_event = await self._run_art_plan("user_replan")
            if art_plan_event is None:
                await self._reply(update, "已记录偏移，但暂时没有生成新的绘画计划。")
                return

            blocks, materialized = await self._materialize_art_plan(art_plan_event)
            reply = self._format_art_plan_summary(art_plan_event.payload, blocks)
            calendar_summary = materialized.get("calendar", {})
            if calendar_summary.get("skipped"):
                reply += "\n\n日历写入未启用，只更新了状态和 Obsidian。"
            elif int(calendar_summary.get("created", 0) or 0) > 0:
                reply += f"\n\n已重写日历绘画块：{calendar_summary.get('created')} 个。"
            overlap_skipped = int(calendar_summary.get("overlap_skipped", 0) or 0)
            if overlap_skipped > 0:
                reply += f"\n⚠️ {overlap_skipped} 个绘画块因时间重叠被跳过。"
            if repair_summary:
                reply += f"\n{repair_summary}"
            reply += f"\n\n{_supportive_line('missed')}"
            await self._reply(update, reply)
        except Exception as exc:
            logger.exception("art replan failed")
            await self._reply(update, f"重排失败：{_friendly_error(str(exc))}")

    async def _handle_art_progress(self, update: Update, text: str) -> None:
        """Handle art progress tracking message."""
        if not update.message:
            return

        from src.domain.art.handlers import parse_art_progress

        parsed = parse_art_progress(text)
        if parsed is None:
            await self._reply(update, "未能解析进度。请用例如「完成 画画 2小时 人体速写 12张」")
            return

        completed = parsed.get("completed_minutes", 0)
        resistance = parsed.get("resistance", False)
        session_type = parsed.get("type", "练习")

        progress_event = Event(
            event_type=EventType.ART_PROGRESS_RECORDED,
            aggregate_id="art_today",
            aggregate_type=AggregateType.ART,
            payload=parsed,
        )
        await self.pipeline.run(progress_event)

        # Update Obsidian
        try:
            from src.integrations.obsidian_daily import ObsidianDailyWriter
            writer = ObsidianDailyWriter(self.settings)

            art_state = self.state_engine.get_view("art", "today")
            progress = art_state.get("progress", {})
            plan = art_state.get("plan", {})
            completed_min = progress.get("completed_minutes", completed)
            target_min = plan.get("target_minutes", 0)

            sessions = progress.get("sessions", [])
            if parsed.get("count", 0) > 0:
                note = f"{session_type} {parsed['count']}张"
            elif resistance:
                note = "感觉画不动"
            else:
                note = ""

            writer.write_event_line(f"绘画进度：{completed_min}min/{target_min}min {note}")
            writer.write_progress(
                completed_minutes=completed_min,
                target_minutes=target_min,
                sessions=sessions,
            )
        except Exception as exc:
            logger.warning("Obsidian progress write failed: %s", exc)

        if resistance:
            await self._reply(update, _supportive_line("missed"))
        elif completed > 0:
            art_state = self.state_engine.get_view("art", "today")
            plan = art_state.get("plan", {})
            target = plan.get("target_minutes", 0)
            progress = art_state.get("progress", {})
            done = progress.get("completed_minutes", completed)
            pct = min(100, int(done / target * 100)) if target > 0 else 0

            note_str = f"，{parsed.get('count', 0)}张" if parsed.get("count", 0) > 0 else ""
            await self._reply(
                update,
                f"已记录：{session_type} {completed}min{note_str}\n"
                f"今日进度：{done}/{target}min（{pct}%）\n"
                f"{_supportive_line('completed')}"
            )
        else:
            await self._reply(update, "已记录。")

    async def _handle_fitness_progress(self, update: Update, text: str) -> None:
        """Handle fitness progress tracking."""
        if not update.message:
            return
        m = re.search(r"(\d+(?:\.\d+)?)\s*(小时|分钟)", text)
        if m:
            duration = float(m.group(1))
            unit = m.group(2)
            minutes = int(duration * 60 if unit == "小时" else duration)
            from src.integrations.obsidian_daily import ObsidianDailyWriter
            writer = ObsidianDailyWriter(self.settings)
            try:
                writer.write_event_line(f"健身完成：{minutes}min")
            except Exception as exc:
                logger.warning("Obsidian fitness log failed: %s", exc)
            await self._reply(update, f"健身已记录：{minutes}min 💪")
        else:
            await self._reply(update, "已记录健身进度。")

    async def _handle_art_insertion(self, update: Update, text: str) -> None:
        """Handle temporal insertion message — parse with DeepSeek if available, then replan."""
        if not update.message:
            return

        await self._reply(update, "正在解析时间安排并调整计划...")

        try:
            parsed = await self._parse_insertion_with_deepseek(text)
        except Exception as exc:
            logger.warning("DeepSeek insertion parse failed, using fallback: %s", exc)
            parsed = self._fallback_parse_insertion(text)

        if parsed is None:
            await self._reply(update, "未能解析时间，请用例如「下午三点去办卡，大概要一小时」")
            return

        # Emit reality insertion event
        insert_event = Event(
            event_type=EventType.ART_DAILY_REALITY_INSERTED,
            aggregate_id="art_today",
            aggregate_type=AggregateType.ART,
            payload=parsed,
        )
        calendar_ok, calendar_result = await self._write_inserted_reality_to_calendar(
            parsed,
            update.effective_user.id if update.effective_user else 0,
        )
        await self.pipeline.run(insert_event)
        await self._refresh_calendar_before_art_planning("art_reality_inserted")
        repair_summary = await self._repair_calendar_before_art_planning("art_reality_inserted")

        from src.integrations.obsidian_daily import ObsidianDailyWriter
        writer = ObsidianDailyWriter(self.settings)
        try:
            title = parsed.get("title", "临时事项")
            writer.write_event_line(f"插入：{title}")
        except Exception as exc:
            logger.warning("Obsidian insertion log failed: %s", exc)

        art_plan_event = await self._run_art_plan("reality_inserted")
        materialized: dict[str, Any] = {}
        if art_plan_event is not None:
            _, materialized = await self._materialize_art_plan(art_plan_event)

        calendar_line = "已写入 Google Calendar。" if calendar_ok else f"日历写入失败：{_friendly_error(calendar_result)}"
        plan_line = "已重新调整剩余绘画计划。" if art_plan_event is not None else "已记录，但暂时没有生成新的绘画计划。"
        cal_summary = materialized.get("calendar", {}) if materialized else {}
        if cal_summary.get("skipped"):
            plan_line += " 绘画块未写入日历。"
        elif int(cal_summary.get("created", 0) or 0) > 0:
            plan_line += f" 已更新 {cal_summary.get('created')} 个绘画块。"
        if repair_summary:
            plan_line += f"\n{repair_summary}"

        await self._reply(
            update,
            f"已添加：{parsed.get('title', '临时事项')} "
            f"（{parsed.get('start', '?')[11:16]}-{parsed.get('end', '?')[11:16]}）\n"
            f"{calendar_line}\n"
            f"{plan_line}\n"
            f"{_supportive_line('arrangement')}"
        )

    async def _parse_insertion_with_deepseek(self, text: str) -> dict[str, Any] | None:
        """Use DeepSeek to parse a natural language time insertion."""
        from datetime import datetime, timezone
        from zoneinfo import ZoneInfo

        import httpx

        api_key = self.settings.deepseek_api_key
        if not api_key:
            return self._fallback_parse_insertion(text)

        today = datetime.now(ZoneInfo("Asia/Singapore")).strftime("%Y-%m-%d")
        prompt = (
            f"当前日期：{today}\n"
            f"用户说：{text}\n\n"
            f"解析用户的日程安排，返回 JSON 格式（只输出 JSON，不要其他文字）：\n"
            f"{{\n"
            f'  "title": "事项名称（简短中文）",\n'
            f'  "start": "ISO 格式开始时间（含时区）",\n'
            f'  "end": "ISO 格式结束时间（含时区）",\n'
            f'  "description": "备注"\n'
            f"}}\n\n"
            f"注意：如果用户没说日期，默认为今天 {today}。如果用户只说时间段没说精确日期，用今天。"
        )

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{self.settings.deepseek_base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": self.settings.deepseek_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            import json as _json
            parsed = _json.loads(content)
            return parsed

    def _fallback_parse_insertion(self, text: str) -> dict[str, Any] | None:
        """Fallback regex-based insertion parser."""
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo

        local_tz = ZoneInfo("Asia/Singapore")
        now = datetime.now(local_tz)
        today = now.strftime("%Y-%m-%d")

        # Pattern: 下午3点 / 三点 / 15:00 到 4点 / 16:00
        m = re.search(r"(\d+)[:：点](\d+)?分?\s*(?:到|至|[-~])\s*(\d+)[:：点](\d+)?", text)
        if m:
            start_h, start_m = int(m.group(1)), int(m.group(2) or 0)
            end_h, end_m = int(m.group(3)), int(m.group(4) or 0)
            # Adjust AM/PM heuristics
            if "下午" in text or "晚上" in text:
                if start_h < 12:
                    start_h += 12
                if end_h < 12:
                    end_h += 12
            return {
                "title": self._extract_title_fallback(text),
                "start": f"{today}T{start_h:02d}:{start_m:02d}:00+08:00",
                "end": f"{today}T{end_h:02d}:{end_m:02d}:00+08:00",
                "description": text,
            }

        # Pattern: 大概要 N 小时 / N 分钟
        m = re.search(r"(?:大概|大约|大概要|要)\s*(\d+(?:\.\d+)?)\s*(小时|分钟)", text)
        if m:
            duration = float(m.group(1))
            unit = m.group(2)
            duration_min = int(duration * 60 if unit == "小时" else duration)
            # Find time mention before duration
            time_m = re.search(r"(?:下午|上午|晚上|中午|今天)?\s*(\d+)[:：点](\d+)?", text)
            if time_m:
                start_h = int(time_m.group(1))
                start_m = int(time_m.group(2) or 0)
                if "下午" in text or "晚上" in text:
                    if start_h < 12:
                        start_h += 12
                start_dt = datetime.strptime(f"{today}T{start_h:02d}:{start_m:02d}", "%Y-%m-%dT%H:%M").replace(tzinfo=local_tz)
                end_dt = start_dt + timedelta(minutes=duration_min)
                return {
                    "title": self._extract_title_fallback(text),
                    "start": start_dt.isoformat(),
                    "end": end_dt.isoformat(),
                    "description": text,
                }

        # Pattern: 下午要出门 / 下午去办卡
        time_periods = {"早上": 8, "上午": 9, "中午": 12, "下午": 14, "晚上": 18}
        for period, default_hour in time_periods.items():
            if period in text:
                start_dt = datetime.strptime(f"{today}T{default_hour:02d}:00", "%Y-%m-%dT%H:%M").replace(tzinfo=local_tz)
                end_dt = start_dt + timedelta(hours=2)
                return {
                    "title": self._extract_title_fallback(text),
                    "start": start_dt.isoformat(),
                    "end": end_dt.isoformat(),
                    "description": text,
                }

        return None

    def _extract_title_fallback(self, text: str) -> str:
        """Extract a short title from insertion text."""
        # Remove time-related words
        cleaned = re.sub(r"(下午|上午|晚上|中午|今天|明天|大概|大约|大概要|要)\s*", "", text)
        cleaned = re.sub(r"(\d+)[:：点](\d+)?分?\s*(?:到|至|[-~])?\s*(\d+)[:：点]?(\d+)?", "", cleaned)
        cleaned = re.sub(r"\d+(?:\.\d+)?\s*(小时|分钟)", "", cleaned).strip()
        # Take first meaningful segment
        cleaned = cleaned[:30]
        return cleaned if cleaned else "临时事项"

    async def _handle_propose(self, update: Update) -> None:
        """Generate and present execution proposals with inline approval."""
        from src.domain.execution.handlers import generate_proposals
        from src.core.proposal import Proposal

        if not update.message:
            return

        proposals = await generate_proposals(self.state_engine)

        if not proposals:
            await update.message.reply_text(
                "暂无建议。可能是没有待处理任务或没有可用时间窗口。\n"
                "可以先试试 /今日计划。"
            )
            return

        # Store proposals in state for callback lookup
        pending = {}
        for ev in proposals:
            p = Proposal.from_dict(ev.payload)
            pending[p.proposal_id] = p.to_dict()

        self._pending_proposals = pending

        # Send each proposal with inline buttons
        for ev in proposals:
            p = Proposal.from_dict(ev.payload)
            payload = p.action_payload

            window_label = {"deep_work": "深度工作", "standard": "标准", "quick": "快速"}
            text = (
                f"建议：{payload.get('title', '任务')}\n"
                f"  时间：{payload.get('start','?')[11:16]} → {payload.get('end','?')[11:16]}\n"
                f"  类型：{window_label.get(payload.get('window_type','standard'), '标准')}\n"
                f"  原因：{p.reason}\n"
                f"  置信度：{p.confidence*100:.0f}%\n"
                f"  过期：{p.expires_at.strftime('%H:%M')}"
            )

            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("接受并写入日历", callback_data=f"prop:accept:{p.proposal_id}"),
                InlineKeyboardButton("拒绝", callback_data=f"prop:reject:{p.proposal_id}"),
                InlineKeyboardButton("改成稍后", callback_data=f"prop:delay:{p.proposal_id}"),
            ]])

            sent = await update.message.reply_text(text, reply_markup=keyboard)
            await self.pipeline.run(Event(
                event_type=EventType.TELEGRAM_PROPOSAL_SENT,
                aggregate_id=p.proposal_id,
                aggregate_type=AggregateType.SYSTEM,
                payload={"proposal_id": p.proposal_id, "message_id": sent.message_id},
            ))

    async def _handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle inline keyboard callbacks for proposal approval."""
        query = update.callback_query
        if not query:
            return

        started = time.monotonic()
        await query.answer()

        data = query.data or ""
        user_id = update.effective_user.id if update.effective_user else 0
        trace_id = f"telegram-button-{query.id}"
        logger.info(
            "TELEGRAM_BUTTON_RECEIVED trace_id=%s causation_id=%s user_id=%s callback_data=%s processing_duration=0.000",
            trace_id, query.message.message_id if query.message else None, user_id, data,
        )

        if data.startswith("amb:"):
            await self._handle_ambient_callback(query, user_id, data, trace_id, started)
            return

        if data.startswith("ctx:"):
            await self._handle_context_callback(query, user_id, data, trace_id, started)
            return

        if data.startswith("hyd:"):
            await self._handle_hydration_callback(query, user_id, data, trace_id, started)
            return

        if data.startswith("sch:"):
            await self._handle_schedule_page_callback(query, data, trace_id, started)
            return

        if data.startswith("undo:"):
            await self._handle_undo_callback(query, user_id, data, trace_id, started)
            return

        if data.startswith("batch:"):
            await self._handle_batch_callback(query, user_id, data, trace_id, started)
            return

        if not data.startswith("prop:"):
            return

        parts = data.split(":", 2)
        if len(parts) != 3:
            return
        _, action, proposal_id = parts

        # Look up proposal
        proposal_data = self._pending_proposals.get(proposal_id, {})
        if not proposal_data:
            await query.edit_message_text("建议未找到或已过期。")
            return

        from src.core.proposal import Proposal, ProposalStatus
        from src.core.events import Event, EventType, AggregateType

        proposal = Proposal.from_dict(proposal_data)

        if action == "accept":
            proposal.status = ProposalStatus.ACCEPTED
            event = Event(
                event_type=EventType.EXECUTION_PROPOSAL_ACCEPTED,
                aggregate_id=proposal_id,
                aggregate_type=AggregateType.SYSTEM,
                payload=proposal.to_dict(),
            )

            # Run through pipeline (this triggers executor)
            try:
                await self.pipeline.run(event)
                await query.edit_message_text(
                    f"✅ 已接受：{proposal.action_payload.get('title','任务')}\n"
                    f"已提交执行请求：{proposal.action_payload.get('start','?')[11:16]}。"
                )
            except Exception as exc:
                await query.edit_message_text(f"❌ 执行失败：{exc}")

            # Clean up
            self._pending_proposals.pop(proposal_id, None)

        elif action in ("reject", "delay"):
            proposal.status = ProposalStatus.REJECTED
            event = Event(
                event_type=EventType.EXECUTION_PROPOSAL_REJECTED,
                aggregate_id=proposal_id,
                aggregate_type=AggregateType.SYSTEM,
                payload=proposal.to_dict(),
            )

            await self.pipeline.run(event)
            if action == "delay":
                await query.edit_message_text(f"⏱ 已改成稍后：{proposal.action_payload.get('title','任务')}")
            else:
                await query.edit_message_text(f"❌ 已拒绝：{proposal.action_payload.get('title','任务')}")
            self._pending_proposals.pop(proposal_id, None)

    async def _handle_ambient_callback(self, query, user_id: int, data: str, trace_id: str, started: float) -> None:
        parts = data.split(":", 2)
        if len(parts) != 3:
            return

        action, intervention_id = parts[1], parts[2]
        event_type = {
            "done": EventType.INTERVENTION_FEEDBACK_RECORDED,
            "delay30": EventType.INTERVENTION_DELAYED,
            "skip": EventType.INTERVENTION_SKIPPED,
            "bad_state": EventType.SUBJECTIVE_CONTEXT_ADDED,
        }.get(action)
        if event_type is None:
            return

        if action == "bad_state":
            payload = {
                "kind": "context",
                "text": "今天状态差",
                "expires_at": _end_of_day_iso(),
                "source": "telegram_button",
                "intervention_id": intervention_id,
            }
        else:
            payload = {
                "intervention_id": intervention_id,
                "feedback": "completed" if action == "done" else action,
                "delay_minutes": 30 if action == "delay30" else 0,
                "source": "telegram_button",
            }

        event = Event(
            event_type=event_type,
            aggregate_id=str(user_id),
            aggregate_type=AggregateType.USER,
            payload=payload,
            metadata={"trace_id": trace_id, "callback_data": data},
        )
        await self.pipeline.run(event)

        duration = time.monotonic() - started
        if event_type == EventType.SUBJECTIVE_CONTEXT_ADDED:
            logger.info(
                "CONTEXT_EVENT_PUBLISHED trace_id=%s causation_id=%s user_id=%s callback_data=%s processing_duration=%.3f",
                trace_id, intervention_id, user_id, data, duration,
            )
            confirmation = "今天会降低提醒强度。"
        else:
            logger.info(
                "FEEDBACK_EVENT_PUBLISHED trace_id=%s causation_id=%s user_id=%s callback_data=%s processing_duration=%.3f",
                trace_id, intervention_id, user_id, data, duration,
            )
            confirmation = {
                "done": "已记录。",
                "delay30": "30分钟后再提醒。",
                "skip": "已记录。",
            }.get(action, "已记录。")

        await query.edit_message_text(confirmation)
        logger.info(
            "TELEGRAM_CARD_UPDATED trace_id=%s causation_id=%s user_id=%s callback_data=%s processing_duration=%.3f",
            trace_id, intervention_id, user_id, data, time.monotonic() - started,
        )

    async def _handle_context_callback(self, query, user_id: int, data: str, trace_id: str, started: float) -> None:
        kind = data.split(":", 1)[1]
        labels = {
            "social_plan": "今晚聚餐",
            "outside": "今晚外出",
            "workout": "今晚健身",
            "family": "今晚有家庭事务",
            "ad_hoc_task": "今晚有临时任务",
        }
        text = labels.get(kind, kind)
        event = Event(
            event_type=EventType.SUBJECTIVE_CONTEXT_ADDED,
            aggregate_id=str(user_id),
            aggregate_type=AggregateType.USER,
            payload={
                "kind": kind,
                "text": text,
                "expires_at": _end_of_day_iso(),
                "source": "telegram_context_button",
            },
            metadata={"trace_id": trace_id, "callback_data": data},
        )
        await self.pipeline.run(event)
        duration = time.monotonic() - started
        logger.info(
            "CONTEXT_EVENT_PUBLISHED trace_id=%s causation_id=%s user_id=%s callback_data=%s processing_duration=%.3f",
            trace_id, query.message.message_id if query.message else None, user_id, data, duration,
        )
        await query.edit_message_text("已记录。")
        logger.info(
            "TELEGRAM_CARD_UPDATED trace_id=%s causation_id=%s user_id=%s callback_data=%s processing_duration=%.3f",
            trace_id, query.message.message_id if query.message else None, user_id, data, time.monotonic() - started,
        )

    async def _handle_batch_callback(
        self, query, user_id: int, data: str, trace_id: str, started: float
    ) -> None:
        """Handle batch intake confirm/discard callbacks."""
        parts = data.split(":", 2)
        if len(parts) < 2:
            return
        action = parts[1]  # "confirm" or "discard"
        draft_id = parts[2] if len(parts) > 2 else ""

        if not draft_id:
            await query.edit_message_text("无效的批处理请求。")
            return

        # Load draft from state engine
        pending = self.state_engine.get_view("finance_batches", "pending")
        draft = pending.get(draft_id)
        if not draft and self.event_store:
            try:
                drafted_events = await self.event_store.get_by_type(EventType.FINANCE_BATCH_DRAFTED.value)
                for drafted_event in reversed(drafted_events):
                    if str(drafted_event.payload.get("draft_id", "")) == draft_id:
                        draft = {
                            "draft_id": draft_id,
                            "raw_text": drafted_event.payload.get("raw_text", ""),
                            "items": drafted_event.payload.get("items", []),
                            "questions": drafted_event.payload.get("questions", []),
                            "summary": drafted_event.payload.get("summary", {}),
                            "status": "drafted",
                            "timestamp": drafted_event.timestamp.isoformat(),
                        }
                        break
            except Exception:
                logger.exception("BATCH_DRAFT_FALLBACK_FAILED draft_id=%s", draft_id)
        if not draft:
            await query.edit_message_text("这条批次记录未找到或已过期。")
            return

        if action == "confirm":
            # Emit batch accepted event
            accepted_event = Event(
                event_type=EventType.FINANCE_BATCH_ACCEPTED,
                aggregate_id=str(user_id),
                aggregate_type=AggregateType.FINANCE,
                causation_id=None,
                payload={"draft_id": draft_id},
            )
            await self.pipeline.run(accepted_event)
            causation_id = accepted_event.event_id

            # Emit underlying events from draft items
            items = draft.get("items", [])
            for item in items:
                itype = item.get("type", "")
                if itype == "expense":
                    await self.pipeline.run(Event(
                        event_type=EventType.FINANCE_TRANSACTION_RECORDED,
                        aggregate_id=str(user_id),
                        aggregate_type=AggregateType.FINANCE,
                        causation_id=causation_id,
                        payload={
                            "amount": item["amount"],
                            "description": item.get("title", item.get("raw", "")),
                            "category": item.get("category", "other"),
                        },
                    ))
                elif itype == "reimbursement":
                    await self.pipeline.run(Event(
                        event_type=EventType.FINANCE_REIMBURSEMENT_RECORDED,
                        aggregate_id=str(user_id),
                        aggregate_type=AggregateType.FINANCE,
                        causation_id=causation_id,
                        payload={
                            "gross_amount": item.get("gross_amount", 0),
                            "reimbursed_amount": item.get("reimbursed_amount", 0),
                            "net_amount": item.get("net_amount", 0),
                            "description": item.get("raw", ""),
                        },
                    ))
                    # Also record net expense for personal tracking
                    net = item.get("net_amount", 0)
                    if net > 0:
                        await self.pipeline.run(Event(
                            event_type=EventType.FINANCE_TRANSACTION_RECORDED,
                            aggregate_id=str(user_id),
                            aggregate_type=AggregateType.FINANCE,
                            causation_id=causation_id,
                            payload={
                                "amount": net,
                                "description": f"{item.get('raw', '')}（报销后自付）",
                                "category": "outing" if "金色印象" in item.get("raw", "") else "other",
                            },
                        ))
                elif itype == "partner_debt_created":
                    await self.pipeline.run(Event(
                        event_type=EventType.PARTNER_DEBT_CREATED,
                        aggregate_id=str(user_id),
                        aggregate_type=AggregateType.FINANCE,
                        causation_id=causation_id,
                        payload={
                            "amount": item["amount"],
                            "date": item.get("date", ""),
                            "counterparty": item.get("counterparty", "对象"),
                            "description": item.get("raw", ""),
                        },
                    ))
                elif itype == "parent_fund_rule_configured":
                    await self.pipeline.run(Event(
                        event_type=EventType.PARENT_FUND_RULE_CONFIGURED,
                        aggregate_id=str(user_id),
                        aggregate_type=AggregateType.FINANCE,
                        causation_id=causation_id,
                        payload={
                            "person": item.get("person", ""),
                            "amount": item.get("amount", 0),
                            "interval_days": item.get("interval_days", 0),
                        },
                    ))
                elif itype == "parent_fund_request_recorded":
                    person = item.get("person") or ("妈妈" if "妈妈" in item.get("raw", "") else "爸爸" if "爸爸" in item.get("raw", "") else "家庭")
                    await self.pipeline.run(Event(
                        event_type=EventType.PARENT_FUND_REQUEST_RECORDED,
                        aggregate_id=str(user_id),
                        aggregate_type=AggregateType.FINANCE,
                        causation_id=causation_id,
                        payload={
                            "amount": item["amount"],
                            "description": item.get("raw", ""),
                        },
                    ))
                    await self.pipeline.run(Event(
                        event_type=EventType.PARENT_FUND_RECEIVED,
                        aggregate_id=str(user_id),
                        aggregate_type=AggregateType.FINANCE,
                        causation_id=causation_id,
                        payload={
                            "amount": item["amount"],
                            "description": item.get("raw", ""),
                            "source": person,
                        },
                    ))
                    await self.pipeline.run(Event(
                        event_type=EventType.FINANCE_INCOME_RECORDED,
                        aggregate_id=str(user_id),
                        aggregate_type=AggregateType.FINANCE,
                        causation_id=causation_id,
                        payload={
                            "amount": item["amount"],
                            "source": person,
                            "description": item.get("raw", ""),
                        },
                    ))
                elif itype == "parent_fund_request_planned":
                    await self.pipeline.run(Event(
                        event_type=EventType.PARENT_FUND_REQUEST_PLANNED,
                        aggregate_id=str(user_id),
                        aggregate_type=AggregateType.FINANCE,
                        causation_id=causation_id,
                        payload={
                            "amount": item["amount"],
                            "description": item.get("description", item.get("raw", "")),
                            "category": "other",
                            "action": "advise",
                        },
                    ))

            await query.edit_message_text("✅ 已确认入账，所有项目已记录。")
            logger.info(
                "BATCH_CONFIRMED trace_id=%s draft_id=%s user_id=%s item_count=%d",
                trace_id, draft_id, user_id, len(items),
            )

        elif action == "discard":
            await self.pipeline.run(Event(
                event_type=EventType.FINANCE_BATCH_DISCARDED,
                aggregate_id=str(user_id),
                aggregate_type=AggregateType.FINANCE,
                causation_id=uuid4(),
                payload={"draft_id": draft_id},
            ))
            await query.edit_message_text("🗑️ 已丢弃，未创建任何记录。")
            logger.info(
                "BATCH_DISCARDED trace_id=%s draft_id=%s user_id=%s",
                trace_id, draft_id, user_id,
            )

        duration = time.monotonic() - started
        logger.info(
            "TELEGRAM_CARD_UPDATED trace_id=%s causation_id=%s user_id=%s callback_data=%s processing_duration=%.3f",
            trace_id, query.message.message_id if query.message else None, user_id, data, duration,
        )

    async def _handle_hydration_callback(self, query, user_id: int, data: str, trace_id: str, started: float) -> None:
        try:
            amount = int(data.split(":", 1)[1])
        except (IndexError, ValueError):
            amount = 250
        event = Event(
            event_type=EventType.HYDRATION_LOGGED,
            aggregate_id=str(user_id),
            aggregate_type=AggregateType.USER,
            payload={"amount_ml": amount, "source": "telegram_hydration_button"},
            metadata={"trace_id": trace_id, "callback_data": data},
        )
        await self.pipeline.run(event)
        duration = time.monotonic() - started
        logger.info(
            "HYDRATION_EVENT_PUBLISHED trace_id=%s user_id=%s amount=%s processing_duration=%.3f",
            trace_id, user_id, amount, duration,
        )
        await query.edit_message_text(f"已记录补水：{amount}ml。")

    async def _handle_schedule_page_callback(self, query, data: str, trace_id: str, started: float) -> None:
        date_str = data.split(":", 1)[1].strip() if ":" in data else ""
        try:
            datetime.fromisoformat(date_str)
        except (TypeError, ValueError):
            await query.edit_message_text("日期格式无效。")
            return

        await query.edit_message_text(
            self._format_schedule_date(date_str),
            reply_markup=self._schedule_page_keyboard(date_str),
        )
        duration = time.monotonic() - started
        logger.info(
            "TELEGRAM_CARD_UPDATED trace_id=%s causation_id=%s user_id=%s callback_data=%s processing_duration=%.3f",
            trace_id,
            query.message.message_id if query.message else None,
            query.from_user.id if query.from_user else 0,
            data,
            duration,
        )

    async def _handle_undo_callback(
        self, query, user_id: int, data: str, trace_id: str, started: float
    ) -> None:
        """Handle undo/revoke button callback."""
        action_id = data.split(":", 1)[1].strip() if ":" in data else ""
        if not action_id:
            await query.edit_message_text("无效的撤回请求。")
            return

        actions = self._user_recent_actions.get(user_id, [])
        action = next((a for a in actions if a["action_id"] == action_id), None)
        if not action:
            await query.edit_message_text("这条操作记录未找到，可能已过期。")
            return

        if action.get("reverted"):
            await query.edit_message_text("这条操作已撤回，不能重复撤回。")
            return

        action_type = action["action_type"]
        params = action.get("params", {})
        summary = action.get("summary", "")

        from src.core.events import Event, EventType, AggregateType
        if getattr(self, "pipeline", None):
            await self.pipeline.run(Event(
                event_type=EventType.USER_UNDO_REQUESTED,
                aggregate_id=str(user_id),
                aggregate_type=AggregateType.USER,
                payload={
                    "action_id": action_id,
                    "action_type": action_type,
                    "summary": summary,
                    "source": "telegram_undo",
                },
            ))

        try:
            if action_type == "finance_transaction":
                amount = float(params.get("amount", 0))
                category = params.get("category", "other")
                await self.pipeline.run(Event(
                    event_type=EventType.USER_ACTION_REVERTED,
                    aggregate_id=str(user_id),
                    aggregate_type=AggregateType.USER,
                    payload={
                        "action_type": "finance_transaction",
                        "action_id": action_id,
                        "amount": amount,
                        "category": category,
                    },
                ))
                action["reverted"] = True
                action["reverted_at"] = datetime.now(timezone.utc).isoformat()
                await query.edit_message_text(f"✅ 已撤回：{summary}（已从月度统计扣回）")

            elif action_type == "finance_income":
                amount = float(params.get("amount", 0))
                await self.pipeline.run(Event(
                    event_type=EventType.USER_ACTION_REVERTED,
                    aggregate_id=str(user_id),
                    aggregate_type=AggregateType.USER,
                    payload={
                        "action_type": "finance_income",
                        "action_id": action_id,
                        "amount": amount,
                    },
                ))
                action["reverted"] = True
                action["reverted_at"] = datetime.now(timezone.utc).isoformat()
                await query.edit_message_text(f"✅ 已撤回收入记录：{summary}")

            elif action_type == "parent_fund_request":
                income_amount = float(params.get("income_amount", 0) or 0)
                await self.pipeline.run(Event(
                    event_type=EventType.USER_ACTION_REVERTED,
                    aggregate_id=str(user_id),
                    aggregate_type=AggregateType.USER,
                    payload={
                        "action_type": "parent_fund_request",
                        "action_id": action_id,
                    },
                ))
                if income_amount > 0:
                    await self.pipeline.run(Event(
                        event_type=EventType.USER_ACTION_REVERTED,
                        aggregate_id=str(user_id),
                        aggregate_type=AggregateType.USER,
                        payload={
                            "action_type": "finance_income",
                            "action_id": action_id,
                            "amount": income_amount,
                        },
                    ))
                action["reverted"] = True
                action["reverted_at"] = datetime.now(timezone.utc).isoformat()
                suffix = "（收入已扣回）" if income_amount > 0 else ""
                await query.edit_message_text(f"✅ 已撤回：{summary}{suffix}")

            elif action_type == "completion_record":
                await self.pipeline.run(Event(
                    event_type=EventType.USER_ACTION_REVERTED,
                    aggregate_id=str(user_id),
                    aggregate_type=AggregateType.USER,
                    payload={
                        "action_type": "completion_record",
                        "action_id": action_id,
                    },
                ))
                action["reverted"] = True
                action["reverted_at"] = datetime.now(timezone.utc).isoformat()
                await query.edit_message_text(f"✅ 已撤回完成记录：{summary}")

            elif action_type == "verbal_scheduling":
                cal_event_id = params.get("event_id", "")
                cal_calendar_id = params.get("calendar_id", "") or "primary"
                is_bot_created = params.get("bot_created", False)

                if cal_event_id and is_bot_created:
                    from src.executor.google_calendar.executor import GoogleCalendarExecutor
                    del_executor = GoogleCalendarExecutor(
                        use_mock=self.settings.google_calendar_mock,
                        settings=self.settings,
                    )
                    del_result = await del_executor.delete_event(
                        event_id=cal_event_id,
                        calendar_id=cal_calendar_id,
                    )
                    if del_result.get("ok"):
                        # External deletion succeeded — publish both events
                        await self.pipeline.run(Event(
                            event_type=EventType.CALENDAR_EVENT_DELETED,
                            aggregate_id=str(user_id),
                            aggregate_type=AggregateType.USER,
                            payload={
                                "event_id": cal_event_id,
                                "calendar_id": cal_calendar_id,
                                "action_id": action_id,
                                "reason": "user_undo",
                            },
                        ))
                        await self.pipeline.run(Event(
                            event_type=EventType.USER_ACTION_REVERTED,
                            aggregate_id=str(user_id),
                            aggregate_type=AggregateType.USER,
                            payload={
                                "action_type": "verbal_scheduling",
                                "action_id": action_id,
                                "external_deleted": True,
                            },
                        ))
                        action["reverted"] = True
                        action["reverted_at"] = datetime.now(timezone.utc).isoformat()
                        await query.edit_message_text(
                            f"✅ 已撤回：{summary}（日历事件已自动删除）"
                        )
                    else:
                        # Delete not possible (e.g. write disabled) — report
                        error_msg = del_result.get("error", "删除失败")
                        await self.pipeline.run(Event(
                            event_type=EventType.USER_ACTION_REVERT_FAILED,
                            aggregate_id=str(user_id),
                            aggregate_type=AggregateType.USER,
                            payload={
                                "action_id": action_id,
                                "action_type": "verbal_scheduling",
                                "error": f"calendar_delete_failed: {error_msg}",
                            },
                        ))
                        await query.edit_message_text(
                            f"⚠️ 撤回失败：无法删除日历事件（{error_msg}）"
                        )
                        # Do NOT mark reverted
                else:
                    # No calendar event to delete, just mark
                    await self.pipeline.run(Event(
                        event_type=EventType.USER_ACTION_REVERTED,
                        aggregate_id=str(user_id),
                        aggregate_type=AggregateType.USER,
                        payload={
                            "action_type": "verbal_scheduling",
                            "action_id": action_id,
                        },
                    ))
                    action["reverted"] = True
                    action["reverted_at"] = datetime.now(timezone.utc).isoformat()
                    await query.edit_message_text(f"✅ 已撤回：{summary}")

            else:
                await query.edit_message_text(f"这条操作（{action_type}）暂不支持自动撤回。")
                return

        except Exception as exc:
            logger.exception("undo callback failed for action %s", action_id)
            await self.pipeline.run(Event(
                event_type=EventType.USER_ACTION_REVERT_FAILED,
                aggregate_id=str(user_id),
                aggregate_type=AggregateType.USER,
                payload={"action_id": action_id, "action_type": action_type, "error": str(exc)},
            ))
            await query.edit_message_text(f"⚠️ 撤回执行异常：{exc}")

        duration = time.monotonic() - started
        logger.info(
            "UNDO_COMPLETED trace_id=%s user_id=%s action_id=%s action_type=%s duration=%.3f",
            trace_id, user_id, action_id, action_type, duration,
        )

    async def _handle_nl_undo(self, update: Update, user_id: int) -> None:
        """Handle natural language undo request (e.g. '撤回', '撤销上一条')."""
        actions = self._user_recent_actions.get(user_id, [])
        # Find the most recent non-reverted action
        target = next((a for a in reversed(actions) if not a.get("reverted")), None)
        if not target:
            await self._reply(update, "没有可以撤回的最近操作。")
            return

        from uuid import uuid4
        aid = target["action_id"]
        # Re-use the undo callback logic by synthesizing a callback data
        import copy
        query_data = f"undo:{aid}"
        # We need a query-like object; simulate by calling the callback handler directly
        # Build a minimal mock query that edit_message_text works on
        from unittest.mock import AsyncMock
        mock_query = AsyncMock()
        mock_query.data = query_data

        # Need message context for edit_message_text - use the update message
        async def fake_edit(text: str, **kwargs):
            await self._reply(update, text)
        mock_query.edit_message_text = fake_edit

        # Also need from_user for the trace log
        mock_query.from_user = type("obj", (object,), {"id": user_id})()

        started = time.monotonic()
        await self._handle_undo_callback(mock_query, user_id, query_data, f"nl-undo-{uuid4().hex[:8]}", started)

    async def start(self) -> None:
        """Build and start the Telegram bot application."""

        self._app = Application.builder().token(
            self.settings.telegram_bot_token
        ).build()

        # Register handlers
        self._app.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND, self.handle_message
        ))
        self._app.add_handler(CommandHandler([
            "homework", "schedule", "help", "start",
            "today", "free_today", "week_load",
            "state", "stress", "capacity",
            "plan_today", "plan_tomorrow", "focus_window",
            "done", "skip", "delay",
            "behavior", "adaptive", "patterns",
            "reflection", "trends", "adaptation",
            "propose", "ping",
            "sync_homework", "jwxt_sync", "sync_schedule", "drink", "registry",
            "calendar_sync", "calendar_today", "calendar_context",
            "menu", "nightly_review",
            "mood", "note", "context",
        ], self.handle_message))

        # Callback handler for inline keyboard (proposal accept/reject)
        self._app.add_handler(CallbackQueryHandler(self._handle_callback))

        logger.info("bot starting...")
        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling()
        logger.info("bot started, polling for messages")
        await self._maybe_send_missed_nightly_review_on_startup()

    async def stop(self) -> None:
        """Graceful shutdown."""
        if self._keepalive_task:
            self._keepalive_task.cancel()
            self._keepalive_task = None
        if self._chaoxing and self._chaoxing._browser:
            try:
                await self._chaoxing._browser.save_state()
            except Exception:
                pass
            try:
                await self._chaoxing._browser.stop()
            except Exception:
                pass
        self.state_engine.save_snapshot()
        if self._app:
            for action in (
                self._app.updater.stop,
                self._app.stop,
                self._app.shutdown,
            ):
                try:
                    await action()
                except Exception:
                    logger.debug("bot shutdown step skipped", exc_info=True)
        logger.info("bot stopped")

    async def run_forever(self) -> None:
        """Start and run until interrupted."""
        while True:
            try:
                await self.start()
                break
            except (KeyboardInterrupt, asyncio.CancelledError):
                raise
            except Exception:
                logger.exception("bot startup failed; retrying in 30s")
                await self.stop()
                await asyncio.sleep(30)
        try:
            while True:
                await asyncio.sleep(1)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            await self.stop()
