"""Homework domain handler.

Pure function: Event → List[Event]. No I/O, no state.
"""

from __future__ import annotations

from src.core.events import Event, EventType, AggregateType


async def handle_user_command(event: Event) -> list[Event]:
    """Handle user.command.received → produce connector.fetch.requested."""
    command = event.payload.get("command", "")

    if command == "check_homework":
        return [Event(
            event_type=EventType.CONNECTOR_FETCH_REQUESTED,
            aggregate_id=event.aggregate_id,
            aggregate_type=AggregateType.HOMEWORK,
            causation_id=event.event_id,
            payload={"source": "chaoxing", "query": "homework_list"},
        )]

    return []


async def handle_fetch_completed(event: Event) -> list[Event]:
    """Handle connector.fetch.completed → parse data → produce homework events."""
    raw = event.payload
    homeworks = raw.get("homeworks", [])

    events: list[Event] = []

    # Emit parsed event
    events.append(Event(
        event_type=EventType.HOMEWORK_PARSED,
        aggregate_id=event.aggregate_id,
        aggregate_type=AggregateType.HOMEWORK,
        causation_id=event.event_id,
        payload={"count": len(homeworks), "source": raw.get("source")},
    ))

    # Emit individual homework.new events
    for hw in homeworks:
        events.append(Event(
            event_type=EventType.HOMEWORK_NEW,
            aggregate_id=hw.get("id", hw.get("title", "unknown")),
            aggregate_type=AggregateType.HOMEWORK,
            causation_id=event.event_id,
            payload=hw,
        ))

    # Emit notification
    if homeworks:
        events.append(Event(
            event_type=EventType.NOTIFICATION_SEND,
            aggregate_id=event.aggregate_id,
            aggregate_type=AggregateType.NOTIFICATION,
            causation_id=event.event_id,
            payload={
                "message": f"你有 {len(homeworks)} 个待完成作业",
                "details": [hw["title"] for hw in homeworks],
            },
        ))

    return events
