"""Test: EventBus publish/subscribe round-trip."""

import asyncio
import sys
sys.path.insert(0, ".")

from src.core.events import Event, EventType, AggregateType
from src.core.bus import EventBus


async def test_basic_pubsub():
    bus = EventBus()
    received: list[Event] = []

    async def handler(event: Event) -> list[Event]:
        received.append(event)
        # Handler produces a new event
        return [Event(
            event_type=EventType.NOTIFICATION_SEND,
            aggregate_id=event.aggregate_id,
            aggregate_type=AggregateType.NOTIFICATION,
            causation_id=event.event_id,
            payload={"message": "handler processed"},
        )]

    bus.subscribe(EventType.SYSTEM_STARTUP, handler)

    event = Event(
        event_type=EventType.SYSTEM_STARTUP,
        aggregate_id="test-user",
        aggregate_type=AggregateType.SYSTEM,
    )

    produced = await bus.publish(event)

    assert len(received) == 1, f"Expected 1 received, got {len(received)}"
    assert received[0].event_type == EventType.SYSTEM_STARTUP
    assert len(produced) == 1, f"Expected 1 produced, got {len(produced)}"
    assert produced[0].event_type == EventType.NOTIFICATION_SEND
    assert produced[0].causation_id == event.event_id
    print("✓ basic pub/sub works")


async def test_no_subscriber():
    bus = EventBus()
    event = Event(
        event_type=EventType.SYSTEM_SHUTDOWN,
        aggregate_id="x",
        aggregate_type=AggregateType.SYSTEM,
    )
    produced = await bus.publish(event)
    assert produced == []
    print("✓ no-subscriber case returns empty")


async def test_multiple_subscribers():
    bus = EventBus()
    calls = []

    async def h1(event):
        calls.append("h1")
        return []

    async def h2(event):
        calls.append("h2")
        return [Event(
            event_type=EventType.NOTIFICATION_SEND,
            aggregate_id="x",
            aggregate_type=AggregateType.NOTIFICATION,
        )]

    bus.subscribe(EventType.SYSTEM_STARTUP, h1)
    bus.subscribe(EventType.SYSTEM_STARTUP, h2)

    event = Event(
        event_type=EventType.SYSTEM_STARTUP,
        aggregate_id="x",
        aggregate_type=AggregateType.SYSTEM,
    )
    produced = await bus.publish(event)

    assert "h1" in calls and "h2" in calls
    assert len(produced) == 1
    print("✓ multiple subscribers work")


if __name__ == "__main__":
    asyncio.run(test_basic_pubsub())
    asyncio.run(test_no_subscriber())
    asyncio.run(test_multiple_subscribers())
    print("\nEventBus: all checks passed")
