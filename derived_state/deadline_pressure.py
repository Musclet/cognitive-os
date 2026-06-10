
"""Deadline pressure ? pure function over state dict.

Deterministic. Replay-safe. Side-effect free.
No GPT. No LLM. No randomness.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.domain.homework.status import is_open_homework_status
from src.domain.homework.urgency import deadline_urgency_score

from src.domain.course_topology import is_excluded_course, normalize_course_name


def derive_deadline_pressure(state: dict[str, Any]) -> dict[str, Any]:
    """Compute deadline pressure from homework deadlines.

    Input: raw state dict from StateEngine (read-only)
    Output: {score, trend, active_courses, overdue_count, closest_hours}

    Scoring:
      overdue items ? 1.0
      closest <= 24h ? 1.0
      closest <= 72h ? elevated
      closest > 10 days ? 0.0
      multiple in 24h window ? +0.1 per extra (capped at 1.0)
    """
    homework_state = state.get("homework", {})
    now = datetime.now(timezone.utc)

    pending = []
    for agg_id, view in homework_state.items():
        title = view.get("title")
        status = str(view.get("status", "pending") or "").lower()
        raw_status = str(view.get("raw_status", "") or "").lower()
        course = normalize_course_name(view.get("course", ""))
        deadline_str = view.get("deadline")
        if not title or not is_open_homework_status(status, raw_status):
            continue
        if is_excluded_course(course):
            continue

        deadline = None
        if deadline_str:
            try:
                deadline = datetime.fromisoformat(deadline_str)
                if deadline.tzinfo is None:
                    deadline = deadline.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                pass

        pending.append({
            "title": title,
            "course": course,
            "deadline": deadline,
        })

    if not pending:
        return {
            "score": 0.0,
            "trend": "stable",
            "active_courses": 0,
            "overdue_count": 0,
            "closest_hours": None,
        }

    overdue = 0
    closest_hours = None
    within_24h = 0
    courses_active = set()

    for hw in pending:
        courses_active.add(hw["course"])
        if hw["deadline"] is None:
            continue
        diff = (hw["deadline"] - now).total_seconds() / 3600
        if diff < 0:
            overdue += 1
        else:
            if closest_hours is None or diff < closest_hours:
                closest_hours = diff
            if diff <= 24:
                within_24h += 1

    base = deadline_urgency_score(closest_hours, overdue)

    # Clustering bonus: multiple deadlines in 24h window
    clustering = max(0, within_24h - 1) * 0.10
    score = min(base + clustering, 1.0)

    # Trend
    if score >= 0.80:
        trend = "critical"
    elif score >= 0.50:
        trend = "rising"
    elif score >= 0.20:
        trend = "elevated"
    else:
        trend = "stable"

    return {
        "score": round(score, 3),
        "trend": trend,
        "active_courses": len(courses_active),
        "overdue_count": overdue,
        "closest_hours": round(closest_hours, 1) if closest_hours else None,
    }
