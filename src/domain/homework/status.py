"""Shared homework status classification."""

from __future__ import annotations


DONE_MARKERS = (
    "submitted", "reviewed", "completed",
    "已提交", "待批阅", "已批阅", "已互评", "已完成", "完成", "互评",
)

OPEN_MARKERS = (
    "pending", "in_progress", "expired",
    "未交", "未提交", "未完成", "进行中", "已过期",
)


def is_open_homework_status(status: str | None, raw_status: str | None = "") -> bool:
    combined = f"{status or ''} {raw_status or ''}".strip().lower()
    if any(marker.lower() in combined for marker in DONE_MARKERS):
        return False
    return any(marker.lower() in combined for marker in OPEN_MARKERS) or not combined
