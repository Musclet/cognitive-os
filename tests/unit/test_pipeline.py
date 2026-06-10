"""Test: Pipeline routes events through handlers."""

import asyncio
import sys
sys.path.insert(0, ".")

from src.core.events import Event, EventType, AggregateType
from src.core.bus import EventBus
from src.core.pipeline import Pipeline
from src.domain.homework.handlers import handle_user_command


async def test_handler_produces_event():
    """Handler receives command → produces connector.fetch.requested."""
    bus = EventBus()
    bus.subscribe(EventType.USER_COMMAND_RECEIVED, handle_user_command)
    pipeline = Pipeline(bus)

    event = Event(
        event_type=EventType.USER_COMMAND_RECEIVED,
        aggregate_id="user-1",
        aggregate_type=AggregateType.USER,
        payload={"command": "check_homework"},
        metadata={"source": "telegram"},
    )

    all_events = await pipeline.run(event)

    # Should have: initial + produced fetch event
    assert len(all_events) == 2, f"Expected 2 events, got {len(all_events)}"
    produced = all_events[1]
    assert produced.event_type == EventType.CONNECTOR_FETCH_REQUESTED
    assert produced.causation_id == event.event_id
    assert produced.payload["source"] == "chaoxing"
    print("✓ handler produces correct event")


async def test_handler_ignores_unknown_command():
    bus = EventBus()
    bus.subscribe(EventType.USER_COMMAND_RECEIVED, handle_user_command)
    pipeline = Pipeline(bus)

    event = Event(
        event_type=EventType.USER_COMMAND_RECEIVED,
        aggregate_id="user-1",
        aggregate_type=AggregateType.USER,
        payload={"command": "unknown"},
    )

    all_events = await pipeline.run(event)
    assert len(all_events) == 1  # only the initial event
    print("✓ unknown command produces no events")


async def test_multi_step_chain():
    """Two handlers chained: command → fetch_request → notification."""
    bus = EventBus()

    async def fake_connector(event: Event) -> list[Event]:
        """Simulates a connector: fetch_request → fetch_completed."""
        if event.payload.get("source") == "chaoxing":
            return [Event(
                event_type=EventType.CONNECTOR_FETCH_COMPLETED,
                aggregate_id=event.aggregate_id,
                aggregate_type=AggregateType.HOMEWORK,
                causation_id=event.event_id,
                payload={"raw_data": [{"title": "数学作业", "deadline": "2026-06-01"}]},
            )]
        return []

    async def notification_handler(event: Event) -> list[Event]:
        return [Event(
            event_type=EventType.NOTIFICATION_SEND,
            aggregate_id=event.aggregate_id,
            aggregate_type=AggregateType.NOTIFICATION,
            causation_id=event.event_id,
            payload={"message": f"fetch completed: {len(event.payload.get('raw_data', []))} items"},
        )]

    bus.subscribe(EventType.USER_COMMAND_RECEIVED, handle_user_command)
    bus.subscribe(EventType.CONNECTOR_FETCH_REQUESTED, fake_connector)
    bus.subscribe(EventType.CONNECTOR_FETCH_COMPLETED, notification_handler)

    pipeline = Pipeline(bus)

    event = Event(
        event_type=EventType.USER_COMMAND_RECEIVED,
        aggregate_id="user-1",
        aggregate_type=AggregateType.USER,
        payload={"command": "check_homework"},
    )

    all_events = await pipeline.run(event)

    assert len(all_events) == 4  # command → fetch_request → fetch_completed → notification
    assert all_events[1].event_type == EventType.CONNECTOR_FETCH_REQUESTED
    assert all_events[2].event_type == EventType.CONNECTOR_FETCH_COMPLETED
    assert all_events[3].event_type == EventType.NOTIFICATION_SEND

    # Check causation chain
    assert all_events[1].causation_id == all_events[0].event_id
    assert all_events[2].causation_id == all_events[1].event_id
    assert all_events[3].causation_id == all_events[2].event_id

    print("✓ multi-step chain with causation tracking works")


if __name__ == "__main__":
    asyncio.run(test_handler_produces_event())
    asyncio.run(test_handler_ignores_unknown_command())
    asyncio.run(test_multi_step_chain())
    print("\nPipeline + Handler: all checks passed")
