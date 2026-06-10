
"""Hydration intervention.

Triggers based on wake duration and hydration gap.
Tracks hydration via /drink command.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Any

from intervention import Intervention


def evaluate_hydration(
    derived_state: dict[str, Any],
    runtime_state: dict[str, Any],
) -> Intervention | None:
    """Evaluate hydration reminder.

    Criteria:
    - wake_duration > 4h (approximated from last hydration event)
    - hydration_gap > threshold (180 min = 3h since last drink)
    """
    hydration_view = runtime_state.get("hydration", {})
    temporal_view = runtime_state.get("temporal", {})
    temporal_context = temporal_view.get("context", {}) if isinstance(temporal_view, dict) else {}
    now = datetime.now(timezone.utc)

    # Last drink timestamp
    last_drink_str = hydration_view.get("last_drink_at", "")
    total_ml = hydration_view.get("total_ml_today", 0)

    last_drink = None
    if last_drink_str:
        try:
            last_drink = datetime.fromisoformat(last_drink_str)
            if last_drink.tzinfo is None:
                last_drink = last_drink.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            pass

    minutes_since = (now - last_drink).total_seconds() / 60 if last_drink else 9999

    workout = _next_workout(temporal_context)
    if workout is not None:
        minutes_to_workout = (workout - now).total_seconds() / 60
        if 0 <= minutes_to_workout <= 120 and minutes_since >= 60:
            return Intervention(
                intervention_type="workout_hydration",
                message=(
                    f"{int(minutes_to_workout)}分钟后有健身安排。现在补水一次，训练时会更稳。"
                ),
                priority=0.65,
                reason=f"workout_in={minutes_to_workout:.0f}min hydration_gap={minutes_since:.0f}min",
            )

    if minutes_since < 180:
        return None

    local_now = now.astimezone(ZoneInfo("Asia/Singapore"))
    if local_now.hour < 7 or local_now.hour >= 23:
        return None

    hours = int(minutes_since / 60)
    return Intervention(
        intervention_type="hydration",
        message=f"距离上次补水已经 {hours} 小时。现在喝水可以降低晚些时候的疲劳累积。",
        priority=0.4,
        reason=f"hydration_gap={minutes_since:.0f}min total_today={total_ml}ml",
    )


def _next_workout(temporal_context: dict[str, Any]) -> datetime | None:
    workout = temporal_context.get("next_workout")
    if not isinstance(workout, dict):
        return None
    start = workout.get("start")
    if not start:
        return None
    try:
        dt = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
