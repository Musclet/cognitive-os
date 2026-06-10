"""Daily review generation.

Pure event-driven shell around deterministic summary functions. It reads the
StateEngine snapshot, then emits review/audit events for the rest of runtime.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from src.core.events import AggregateType, Event, EventType
from src.derived_state.cognitive_profile import audit_cognitive_profile
from src.domain.course_topology import is_excluded_course, normalize_course_name
from src.domain.homework.status import is_open_homework_status

LOCAL_TZ = ZoneInfo("Asia/Singapore")


async def handle_daily_review_requested(event: Event, state_engine) -> list[Event]:
    if event.event_type != EventType.DAILY_REVIEW_REQUESTED:
        return []

    requested_date = event.payload.get("date")
    target_date = _parse_date(requested_date) or datetime.now(LOCAL_TZ).date()
    state_snapshot = state_engine.snapshot()
    state = state_snapshot.get("state", {})
    derived = state_snapshot.get("derived", {})

    audit = audit_cognitive_profile(state, derived, datetime.now(timezone.utc))
    card = build_daily_review_card(state_engine, state, derived, audit, target_date)

    audit_event = Event(
        event_type=EventType.COGNITIVE_PROFILE_AUDITED,
        aggregate_id=target_date.isoformat(),
        aggregate_type=AggregateType.USER,
        causation_id=event.event_id,
        payload={"date": target_date.isoformat(), **audit},
        metadata={"source": "daily_review"},
    )
    review_event = Event(
        event_type=EventType.DAILY_REVIEW_GENERATED,
        aggregate_id=target_date.isoformat(),
        aggregate_type=AggregateType.USER,
        causation_id=event.event_id,
        payload={
            "date": target_date.isoformat(),
            "text": card,
            "audit": audit,
            "force": bool(event.payload.get("force", False)),
        },
        metadata={"source": "daily_review"},
    )
    return [audit_event, review_event]


def build_daily_review_card(
    state_engine,
    state: dict[str, Any],
    derived: dict[str, Any],
    audit: dict[str, Any],
    target_date,
) -> str:
    schedule_blocks, calendar_blocks = _day_blocks(state_engine, target_date)
    homework_items = _open_homework(state)
    completions = _day_completions(state, target_date)
    art = state.get("art", {}).get("today", {})
    art_progress = art.get("progress", {})
    art_plan = art.get("plan", {})
    hydration = state.get("hydration", {}).get("current", {})
    vocab = state.get("vocab", {}).get("momo", {})
    cognition = derived.get("cognition", {})
    behavior = derived.get("behavior", {})
    reflection = derived.get("reflection", {})

    lines = [
        f"晚间总结 {target_date.isoformat()}",
        "────────",
        f"课程：{len(schedule_blocks)} 节 / 日历：{len(calendar_blocks)} 项 / 完成：{len(completions)} 条",
        f"压力：{_pct(cognition.get('stress_projection', 0))} / 疲劳：{_pct(cognition.get('fatigue_risk', 0))} / 负载：{_pct(cognition.get('workload_overload', 0))}",
        f"饮水：{int(hydration.get('total_ml_today', 0) or 0)}ml",
    ]

    vocab_today = vocab.get("today", {}) if isinstance(vocab, dict) else {}
    if vocab_today:
        lines.append(
            f"背词：{vocab_today.get('finished', 0)}/{vocab_today.get('total', 0)}"
            f"，剩 {vocab_today.get('remaining', 0)}"
        )

    target = int(art_plan.get("target_minutes", 0) or 0)
    done = int(art_progress.get("completed_minutes", 0) or 0)
    if target or done:
        pct = int(done / target * 100) if target else 0
        lines.append(f"画画：{done}/{target}min（{pct}%）")

    lines.append("")
    lines.append("实际发生")
    lines.extend(_format_blocks(schedule_blocks, "课") or ["- 今日无学校课程"])
    lines.extend(_format_blocks(calendar_blocks, "日") or ["- 今日无日历安排"])

    lines.append("")
    lines.append("完成与偏离")
    if completions:
        for item in completions[:5]:
            lines.append(f"- {item.get('task_id') or item.get('text') or '完成项'}")
    else:
        lines.append("- 今天还没有明确完成记录")

    lines.append("")
    lines.append("作业压力")
    if homework_items:
        for item in homework_items[:4]:
            due = _deadline_label(item.get("deadline"))
            lines.append(f"- {item.get('course', '未知')}：{item.get('title', '?')[:28]}{due}")
    else:
        lines.append("- 暂无未完成作业")

    lines.append("")
    lines.append("认知学习审查")
    lines.append(f"成熟度：{audit.get('maturity_score', 0)}/100")
    lines.append(audit.get("conclusion", "证据不足。"))
    blind = audit.get("blind_spots", [])
    if blind:
        lines.append("盲区：" + "；".join(blind[:3]))

    lines.append("")
    lines.append("明日安排倾向")
    lines.append(_tomorrow_tendency(cognition, behavior, reflection, homework_items))
    return "\n".join(lines)


def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)).date()
    except (TypeError, ValueError):
        return None


def _day_blocks(state_engine, target_date):
    schedule = []
    calendar = []
    for block in state_engine.get_temporal_blocks():
        if block.start.astimezone(LOCAL_TZ).date() != target_date:
            continue
        if str(block.source) == "jwxt" and str(block.block_type) in {"class_lecture", "class_lab"}:
            schedule.append(block)
        elif str(block.source) == "google_calendar":
            calendar.append(block)
    return sorted(schedule, key=lambda b: b.start), sorted(calendar, key=lambda b: b.start)


def _format_blocks(blocks, prefix: str) -> list[str]:
    lines = []
    for block in blocks[:6]:
        start = block.start.astimezone(LOCAL_TZ).strftime("%H:%M")
        end = block.end.astimezone(LOCAL_TZ).strftime("%H:%M")
        location = f" @ {block.location}" if getattr(block, "location", "") else ""
        lines.append(f"- [{prefix}] {start}-{end} {block.title[:24]}{location}")
    return lines


def _open_homework(state: dict[str, Any]) -> list[dict[str, Any]]:
    items = []
    for hw_id, hw in state.get("homework", {}).items():
        if not hw.get("title"):
            continue
        course = normalize_course_name(hw.get("course", ""))
        if is_excluded_course(course):
            continue
        status = str(hw.get("status", "") or "")
        raw_status = str(hw.get("raw_status", "") or "")
        if not is_open_homework_status(status, raw_status):
            continue
        items.append({**hw, "id": hw_id, "course": course})
    return sorted(items, key=lambda h: h.get("deadline") or "9999")


def _day_completions(state: dict[str, Any], target_date) -> list[dict[str, Any]]:
    feedbacks = state.get("behavior", {}).get("current", {}).get("feedback_log", [])
    completions = []
    for item in feedbacks:
        if item.get("outcome") != "completed":
            continue
        ts = _parse_datetime(item.get("outcome_timestamp") or item.get("timestamp"))
        if ts and ts.astimezone(LOCAL_TZ).date() == target_date:
            completions.append(item)
    return completions


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _deadline_label(value: str | None) -> str:
    dt = _parse_datetime(value)
    if not dt:
        return ""
    hours = (dt - datetime.now(timezone.utc)).total_seconds() / 3600
    if hours < 0:
        return f" · 超期{abs(hours):.0f}h"
    if hours <= 24:
        return f" · {hours:.0f}h"
    if hours <= 240:
        return f" · {hours / 24:.1f}d"
    return ""


def _pct(value) -> str:
    try:
        return f"{float(value) * 100:.0f}%"
    except (TypeError, ValueError):
        return "0%"


def _tomorrow_tendency(cognition, behavior, reflection, homework_items) -> str:
    stress = float(cognition.get("stress_projection", 0) or 0)
    fatigue = float(cognition.get("fatigue_risk", 0) or 0)
    reliability = float(behavior.get("planning_reliability", 1.0) or 1.0)
    trend = reflection.get("weekly_consistency_trend", "stable")
    if stress >= 0.75 or fatigue >= 0.7:
        return "明天先保留低切换任务，把画画拆成短块，避免把压力继续滚大。"
    if homework_items:
        return "明天先清最近截止项，再安排一段完整画画时间。"
    if reliability < 0.5 or trend == "declining":
        return "明天计划要缩小颗粒度：先安排可完成的小块，再逐步拉长。"
    return "明天可以安排一个深度画画窗口，并用碎片时间处理背词和小作业。"
