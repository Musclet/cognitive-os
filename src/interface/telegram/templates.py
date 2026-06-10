"""Telegram output templates — Event → formatted message.

Stateless. Pure formatting.
"""

from __future__ import annotations

from src.core.events import Event, EventType


def format_output(event: Event) -> str | None:
    """Format a notification.send event into a Telegram message string.

    Returns None if the event should not produce a visible message.
    """
    if event.event_type != EventType.NOTIFICATION_SEND:
        return None

    payload = event.payload
    message = payload.get("message", "")
    details = payload.get("details", [])

    if not details:
        return message

    detail_lines = "\n".join(f"  • {d}" for d in details)
    return f"{message}\n\n{detail_lines}"


def format_error(error: str) -> str:
    """Format an error message."""
    return f"❌ {error}"


def format_help() -> str:
    return (
        "命令列表\n\n"
        "━━ 同步 ━━\n"
        "/同步刷新数据 — 刷新课表、作业、日历\n"
        "/同步课表 / /同步作业 / /同步日历 — 兼容入口\n"
        "(自动每12小时同步作业+课表，每30分钟同步日历)\n\n"
        "━━ 查询 ━━\n"
        "/homework — 未完成作业列表\n"
        "/today — 今日安排\n"
        "/schedule — 本周课表（兼容入口）\n"
        "/作业 — 未完成作业列表\n"
        "/查课表 — 今天和明天课表\n"
        "/查课表 YYYY-MM-DD — 查询指定日期课表\n"
        "/今天 — 今日时间分布\n"
        "/今日空闲 — 今日可支配时间\n"
        "/周负载 — 本周利用率\n"
        "/课程 — 活跃课程列表\n\n"
        "━━ 状态 ━━\n"
        "/状态 — 综合认知状态\n"
        "/压力 — 压力评分\n"
        "/容量 — 处理容量\n\n"
        "━━ 规划 ━━\n"
        "/今日计划 — 今日任务窗口\n"
        "/明日计划 — 明日展望\n"
        "/专注 — 深度工作时段\n"
        "/建议 — 生成执行建议\n\n"
        "━━ 反馈 ━━\n"
        "记录完成 — 打开完成记录引导\n"
        "完成了 xxx — 直接记录完成一件事\n"
        "/完成 — 标记任务完成\n"
        "/跳过 — 跳过建议\n"
        "/推迟 — 推迟建议\n"
        "/饮水 500 — 记录饮水量\n\n"
        "━━ 分析 ━━\n"
        "今晚总结 — 生成今日总结与认知审查\n"
        "/行为 — 行为快照\n"
        "/模式 — 行为模式\n"
        "/反思 — 长期趋势\n"
        "/趋势 — 半期对比\n"
        "/自适应 — 自适应参数\n"
        "/适应 — 参数调整记录\n\n"
        "━━ 主观 ━━\n"
        "/情绪 1-10 — 记录情绪分数\n"
        "/记录 xxx — 记录日程备注（社交/计划等）\n"
        "/情境 xxx — 记录主观情境\n\n"
        "━━ 认知 ━━\n"
        "认知学习 — 输入待记录内容，DeepSeek 解析为记忆事件\n"
        "口述排期 — 用自然语言描述日程，创建日历事件\n\n"
        "━━ 绘画训练 ━━\n"
        "早安 — 规划今日绘画训练\n"
        "完成 画画 2小时 人体速写 12张 — 记录绘画进度\n"
        "画画完成40% — 按百分比记录\n"
        "画不动 / 跳过 画画 — 跳过绘画\n"
        "下午三点去办卡，大概要一小时 — 插入临时安排\n\n"
        "━━ 系统 ━━\n"
        "/selfcheck — 系统自检面板\n"
        "/selftest — 真实链路烟测\n"
        "/storage_status — 存储状态\n"
        "/Obsidian状态 — Obsidian写入状态"
    )
