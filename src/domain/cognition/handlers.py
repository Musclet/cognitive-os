"""Cognition domain handler — recommendation engine.

Subscribes to cognition-relevant events and emits
cognition.pressure.updated + cognition.recommendation.generated.

Pure event-driven. No direct state mutation.
"""

from __future__ import annotations

from datetime import datetime, timezone

from src.core.events import Event, EventType, AggregateType
from src.derived_state.cognition import compute_cognition


# Events that trigger cognition recalculation
TRIGGER_EVENTS = {
    EventType.HOMEWORK_NEW,
    EventType.HOMEWORK_DEADLINE_APPROACHING,
    EventType.TEMPORAL_BLOCK_ADDED,
    EventType.TEMPORAL_BLOCK_REMOVED,
    EventType.TEMPORAL_PROJECTION_UPDATED,
}

# Anti-spam: don't emit two recommendations within this many seconds
_MIN_RECOMMENDATION_INTERVAL = 60
_last_recommendation_time: datetime | None = None
_last_pressure: dict | None = None


def _significant_change(old: dict | None, new: dict) -> bool:
    """Check if stress projection changed significantly."""
    if old is None:
        return True
    return abs(old.get("stress_projection", 0) - new.get("stress_projection", 0)) > 0.15


async def handle_cognition_trigger(event: Event) -> list[Event]:
    """Main cognition handler — computes cognition and emits recommendation.

    This is an EventBus-compatible handler registered for TRIGGER_EVENTS.
    """
    global _last_recommendation_time, _last_pressure

    # We need state — this handler is called with access to state_engine
    # via the event metadata or a closure. For now, emit pressure event
    # and let the pipeline route to recommendation generation.

    return []


async def handle_pressure_update(
    event: Event,
    state_engine=None,
) -> list[Event]:
    """Handle cognition.pressure.updated → check if recommendation needed.

    Args:
        event: The pressure.updated event.
        state_engine: Injected StateEngine for reading current state.
    """
    global _last_recommendation_time, _last_pressure

    cognition = event.payload
    if not cognition:
        return []

    # Anti-spam: throttle recommendations
    now = datetime.now(timezone.utc)
    if _last_recommendation_time:
        elapsed = (now - _last_recommendation_time).total_seconds()
        if elapsed < _MIN_RECOMMENDATION_INTERVAL:
            return []

    # Only recommend if significant change
    if not _significant_change(_last_pressure, cognition):
        return []

    _last_pressure = cognition

    # Generate recommendation
    recs = _generate_recommendations(cognition)
    if not recs:
        return []

    _last_recommendation_time = now

    return [Event(
        event_type=EventType.COGNITION_RECOMMENDATION_GENERATED,
        aggregate_id=event.aggregate_id,
        aggregate_type=AggregateType.SYSTEM,
        causation_id=event.event_id,
        payload={
            "recommendations": recs,
            "cognition": cognition,
        },
    )]


def _generate_recommendations(cognition: dict) -> list[str]:
    """Generate human-readable recommendations from cognitive state.

    Rule-based only. No AI/LLM.
    """
    recs = []

    sp = cognition.get("stress_projection", 0)
    dp = cognition.get("deadline_pressure", 0)
    wo = cognition.get("workload_overload", 0)
    fr = cognition.get("fatigue_risk", 0)
    rw = cognition.get("recovery_window", 0)
    n48 = cognition.get("next_48h_capacity", 0)
    pending = cognition.get("pending_total", 0)

    # Rule 1: Capacity crisis
    if n48 > 1.2 and pending > 0:
        recs.append(
            f"Next 48h capacity overloaded ({n48*100:.0f}%). "
            f"{pending} pending. Start highest-priority task now."
        )
    elif n48 > 0.9 and pending > 2:
        recs.append(
            f"Next 48h is tight ({n48*100:.0f}% utilized). "
            f"Consider completing at least one task today."
        )

    # Rule 2: Recovery crisis
    if rw < 2.0 and sp > 0.5:
        recs.append(
            f"Recovery window is low ({rw:.1f}h free). "
            f"Avoid starting new tasks tonight."
        )

    # Rule 3: High fatigue
    if fr > 0.6:
        recs.append(
            f"Fatigue risk elevated ({fr*100:.0f}%). "
            f"Consider taking a break before continuing."
        )

    # Rule 4: Deadline urgency
    if dp > 0.8 and pending > 0:
        recs.append(
            f"Deadline pressure is high ({dp*100:.0f}%). "
            f"Focus on the closest deadline first."
        )

    # Rule 5: All clear
    if not recs and sp < 0.3:
        recs.append("Stress levels are low. Good time for deep work or review.")

    return recs
