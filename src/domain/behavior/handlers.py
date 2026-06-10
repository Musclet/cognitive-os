"""Behavioral feedback domain handler.

Handles user feedback events (done/skip/delay) and
emits planning.recommendation.accepted/skipped/delayed
events into the event stream.

Pure event-driven. No direct state mutation.
"""

from __future__ import annotations

from datetime import datetime, timezone

from src.core.events import Event, EventType, AggregateType


# Events that trigger behavioral feedback processing
TRIGGER_EVENTS = {
    EventType.USER_COMMAND_RECEIVED,
}


async def handle_user_feedback(event: Event) -> list[Event]:
    """Translate user feedback commands into behavioral events.

    USER_COMMAND_RECEIVED with command_type in (task_done, task_skip, task_delay)
    → planning.recommendation.accepted/skipped/delayed or planning.task.completed/abandoned.
    """
    command = event.payload.get("command", "")
    params = event.payload.get("params", {})
    args = params.get("args", "")
    user_id = event.aggregate_id

    if command == "task_done":
        # User completed a task → emit task.completed
        task_id = args if args else f"task-{event.event_id.hex[:8]}"
        return [Event(
            event_type=EventType.PLANNING_TASK_COMPLETED,
            aggregate_id=user_id,
            aggregate_type=AggregateType.USER,
            causation_id=event.event_id,
            payload={
                "task_id": task_id,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "source": "user_feedback",
            },
        )]

    if command == "task_skip":
        # User skipped a recommendation
        task_id = args if args else f"task-{event.event_id.hex[:8]}"
        return [Event(
            event_type=EventType.PLANNING_RECOMMENDATION_SKIPPED,
            aggregate_id=user_id,
            aggregate_type=AggregateType.USER,
            causation_id=event.event_id,
            payload={
                "task_id": task_id,
                "skipped_at": datetime.now(timezone.utc).isoformat(),
                "source": "user_feedback",
            },
        )]

    if command == "task_delay":
        task_id = args if args else f"task-{event.event_id.hex[:8]}"
        return [Event(
            event_type=EventType.PLANNING_RECOMMENDATION_DELAYED,
            aggregate_id=user_id,
            aggregate_type=AggregateType.USER,
            causation_id=event.event_id,
            payload={
                "task_id": task_id,
                "delayed_at": datetime.now(timezone.utc).isoformat(),
                "source": "user_feedback",
            },
        )]

    return []
