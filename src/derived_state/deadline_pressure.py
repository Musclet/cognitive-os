"""Deadline pressure derived state — pure function on state dict."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.domain.course_topology import is_excluded_course
from src.domain.homework.status import is_open_homework_status
from src.domain.homework.urgency import deadline_urgency_score


def compute_deadline_pressure(
    state: dict[str, Any],
    *,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Compute deadline pressure from homework deadlines.

    Input: state dict from StateEngine
    Output: {closest_deadline_hours, overdue_count, score}
    """
    homework_state = state.get("homework", {})
    now = as_of or datetime.now(timezone.utc)

    closest_hours: float | None = None
    overdue_count = 0

    for agg_id, view in homework_state.items():
        deadline_str = view.get("deadline")
        title = view.get("title")
        status = str(view.get("status", "pending") or "").lower()
        raw_status = str(view.get("raw_status", "") or "").lower()

        if not title or not deadline_str or not is_open_homework_status(status, raw_status):
            continue
        if is_excluded_course(view.get("course", "")):
            continue

        try:
            deadline = datetime.fromisoformat(deadline_str)
            # Ensure timezone-aware
            if deadline.tzinfo is None:
                from datetime import timezone as tz
                deadline = deadline.replace(tzinfo=tz.utc)
        except (ValueError, TypeError):
            continue

        diff_hours = (deadline - now).total_seconds() / 3600.0

        if diff_hours < 0:
            overdue_count += 1
        elif closest_hours is None or diff_hours < closest_hours:
            closest_hours = diff_hours

    score = deadline_urgency_score(closest_hours, overdue_count)

    return {
        "closest_deadline_hours": round(closest_hours, 1) if closest_hours else None,
        "overdue_count": overdue_count,
        "score": round(score, 3),
    }
