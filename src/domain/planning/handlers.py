"""Planning domain handler — scheduling suggestions from temporal + cognitive state.

Subscribes to planning-relevant events and emits planning.* events.
"""

from __future__ import annotations

from src.core.events import Event, EventType, AggregateType

TRIGGER_EVENTS = {
    EventType.TEMPORAL_BLOCK_ADDED,
    EventType.TEMPORAL_BLOCK_REMOVED,
    EventType.HOMEWORK_NEW,
    EventType.HOMEWORK_DEADLINE_APPROACHING,
}


async def handle_planning_trigger(event: Event) -> list[Event]:
    """Trigger planning recomputation when relevant events occur.

    Actual computation is done in StateEngine derived state.
    This handler emits planning.* events for the event log.
    """
    return []


async def compute_and_emit_planning(state_engine) -> list[Event]:
    """Read current state from StateEngine and emit planning events.

    Called by the bot or scheduler when planning-related commands are used.
    """
    from src.derived_state.planning import compute_planning

    blocks = state_engine.get_temporal_blocks()
    derived = state_engine.get_all_derived()
    cognition = derived.get("cognition", {})

    planning = compute_planning(blocks, cognition)

    events: list[Event] = []

    # Emit recommended windows
    for w in planning.get("recommended_windows", []):
        events.append(Event(
            event_type=EventType.PLANNING_WINDOW_RECOMMENDED,
            aggregate_id="planning",
            aggregate_type=AggregateType.SYSTEM,
            payload=w,
        ))

    # Emit overload detection
    for od in planning.get("overloaded_days", []):
        events.append(Event(
            event_type=EventType.PLANNING_OVERLOAD_DETECTED,
            aggregate_id=od["date"],
            aggregate_type=AggregateType.SYSTEM,
            payload=od,
        ))

    # Emit recovery suggestions
    for rs in planning.get("recovery_slots", []):
        events.append(Event(
            event_type=EventType.PLANNING_RECOVERY_SUGGESTED,
            aggregate_id="planning",
            aggregate_type=AggregateType.SYSTEM,
            payload=rs,
        ))

    return events
