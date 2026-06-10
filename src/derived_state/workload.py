"""Workload derived state — pure function on state dict."""

from __future__ import annotations

from typing import Any

from src.domain.course_topology import is_excluded_course, normalize_course_name
from src.domain.homework.status import is_open_homework_status


def compute_workload(state: dict[str, Any]) -> dict[str, Any]:
    """Compute workload score from homework state.

    Input: state dict from StateEngine (homework aggregate view)
    Output: {total, by_course, score}
    """
    homework_state = state.get("homework", {})

    by_course: dict[str, int] = {}
    total = 0

    for agg_id, view in homework_state.items():
        course = normalize_course_name(view.get("course", ""))
        status = str(view.get("status", "pending") or "").lower()
        raw_status = str(view.get("raw_status", "") or "").lower()
        title = view.get("title")

        # Only count homework entries (not parsed views)
        if not title:
            continue
        if is_excluded_course(course):
            continue
        if not is_open_homework_status(status, raw_status):
            continue

        total += 1
        if course:
            by_course[course] = by_course.get(course, 0) + 1

    # Score: capped at 10 items = 1.0
    score = min(total / 10.0, 1.0)

    return {
        "total": total,
        "by_course": by_course,
        "score": round(score, 3),
    }
