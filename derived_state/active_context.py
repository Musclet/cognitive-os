
"""Active context ? pure function over state dict.

Identifies which courses are currently relevant (have pending work).
Deterministic. Replay-safe. Side-effect free.
No GPT. No LLM.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any

from src.domain.course_topology import is_excluded_course, normalize_course_name
from src.domain.homework.status import is_open_homework_status
from src.domain.homework.urgency import is_urgent_deadline


def derive_active_context(state: dict[str, Any]) -> dict[str, Any]:
    """Derive currently active/relevant courses from homework state.

    Input: raw state dict from StateEngine (read-only)
    Output: {active_course_count, active_courses, most_urgent}

    Active = has pending homework with deadline.
    Urgency determined by closest deadline.
    """
    homework_state = state.get("homework", {})
    now = datetime.now(timezone.utc)

    active: dict[str, dict] = {}

    for agg_id, view in homework_state.items():
        title = view.get("title")
        status = str(view.get("status", "pending") or "").lower()
        raw_status = str(view.get("raw_status", "") or "").lower()
        course = normalize_course_name(view.get("course", ""))
        deadline_str = view.get("deadline")

        if not title or not course or not is_open_homework_status(status, raw_status):
            continue
        if is_excluded_course(course):
            continue

        if course not in active:
            active[course] = {
                "course": course,
                "pending_count": 0,
                "closest_deadline_hours": None,
                "overdue_count": 0,
            }

        a = active[course]
        a["pending_count"] += 1

        if deadline_str:
            try:
                deadline = datetime.fromisoformat(deadline_str)
                if deadline.tzinfo is None:
                    deadline = deadline.replace(tzinfo=timezone.utc)
                diff = (deadline - now).total_seconds() / 3600
                if diff < 0:
                    a["overdue_count"] += 1
                elif is_urgent_deadline(diff) and (
                    a["closest_deadline_hours"] is None or diff < a["closest_deadline_hours"]
                ):
                    a["closest_deadline_hours"] = round(diff, 1)
            except (ValueError, TypeError):
                pass

    # Sort by urgency: overdue first, then closest deadline
    sorted_active = sorted(
        active.values(),
        key=lambda c: (
            -c["overdue_count"],
            c["closest_deadline_hours"] if c["closest_deadline_hours"] is not None else 9999,
        ),
    )

    most_urgent = None
    urgent_active = [
        course for course in sorted_active
        if is_urgent_deadline(course["closest_deadline_hours"], course["overdue_count"])
    ]
    if urgent_active:
        mu = urgent_active[0]
        most_urgent = {
            "course": mu["course"],
            "closest_hours": mu["closest_deadline_hours"],
            "overdue": mu["overdue_count"] > 0,
        }

    return {
        "active_course_count": len(active),
        "active_courses": [a["course"] for a in sorted_active],
        "most_urgent": most_urgent,
    }
