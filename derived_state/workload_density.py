
"""Workload density ? pure function over state dict.

Combines pending homework count with schedule density.
Deterministic. Replay-safe. Side-effect free.
No GPT. No LLM.
"""

from __future__ import annotations

from typing import Any

from src.domain.course_topology import is_excluded_course, normalize_course_name
from src.domain.homework.status import is_open_homework_status


def derive_workload_density(state: dict[str, Any]) -> dict[str, Any]:
    """Compute workload density from pending homework.

    Input: raw state dict from StateEngine (read-only)
    Output: {score, total_pending, by_course, capacity_pressure}

    Scoring:
      pending / 10 ? capped at 1.0
      blended with schedule density if temporal state exists
    """
    homework_state = state.get("homework", {})
    temporal_state = state.get("temporal", {})
    temporal_context = temporal_state.get("context", {})

    by_course: dict[str, int] = {}
    total = 0

    for agg_id, view in homework_state.items():
        title = view.get("title")
        status = str(view.get("status", "pending") or "").lower()
        raw_status = str(view.get("raw_status", "") or "").lower()
        course = normalize_course_name(view.get("course", ""))

        if not title or not is_open_homework_status(status, raw_status):
            continue
        if is_excluded_course(course):
            continue

        total += 1
        if course:
            by_course[course] = by_course.get(course, 0) + 1

    # Count factor from pending homework
    count_factor = min(total / 10.0, 1.0)

    # Schedule density from temporal projection
    schedule_density = 0.0
    projection_view = temporal_state.get("projection", {})
    if projection_view:
        schedule_density = projection_view.get("busy_density", 0.0)

    # Blended score
    score = min(count_factor * 0.7 + schedule_density * 0.3, 1.0)

    # Capacity pressure: how much of daily capacity is consumed
    daily_capacity = projection_view.get("daily_capacity", 17.0)
    estimated_hours = total * 2.0  # each homework ~2h
    capacity_pressure = min(estimated_hours / max(daily_capacity, 1), 1.0)

    hydration_priority = 0.0
    workload_tolerance = 1.0
    recovery_need = 0.0
    focus_fragmentation = 0.0
    evening_capacity = 1.0
    if temporal_context.get("workout_block_later"):
        hydration_priority += 0.3
    if temporal_context.get("travel_block_today"):
        workload_tolerance -= 0.3
    if schedule_density > 0.7:
        recovery_need += 0.2
    if temporal_context.get("meeting_blocks_today", 0) >= 3:
        focus_fragmentation += 0.2
    if temporal_context.get("social_block_tonight"):
        evening_capacity -= 0.3

    return {
        "score": round(score, 3),
        "total_pending": total,
        "by_course": by_course,
        "capacity_pressure": round(capacity_pressure, 3),
        "hydration_priority": round(max(0.0, hydration_priority), 3),
        "workload_tolerance": round(max(0.0, workload_tolerance), 3),
        "recovery_need": round(max(0.0, recovery_need), 3),
        "focus_fragmentation": round(max(0.0, focus_fragmentation), 3),
        "evening_capacity": round(max(0.0, evening_capacity), 3),
    }
