"""Shared homework urgency rules."""

from __future__ import annotations


SUPER_URGENT_HOURS = 24
URGENT_HOURS = 72
URGENCY_WINDOW_HOURS = 240


def deadline_urgency_score(hours_left: float | None, overdue_count: int = 0) -> float:
    """Return deadline pressure from the nearest open homework deadline."""
    if overdue_count > 0:
        return 1.0
    if hours_left is None:
        return 0.0
    if hours_left <= SUPER_URGENT_HOURS:
        return 1.0
    if hours_left <= URGENT_HOURS:
        return 0.55
    if hours_left <= URGENCY_WINDOW_HOURS:
        return max(0.05, 0.30 * (URGENCY_WINDOW_HOURS - hours_left) / (URGENCY_WINDOW_HOURS - URGENT_HOURS))
    return 0.0


def is_urgent_deadline(hours_left: float | None, overdue_count: int = 0) -> bool:
    if overdue_count > 0:
        return True
    return hours_left is not None and hours_left <= URGENCY_WINDOW_HOURS
