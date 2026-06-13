"""Test: EventBus publish/subscribe round-trip."""

import asyncio
import sys
sys.path.insert(0, ".")

from src.core.events import Event, EventType, AggregateType
from src.core.bus import EventBus
from src.core.safety import DeadLetterQueue


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


async def test_handler_failure_is_visible_without_stopping_other_handlers():
    dead_letter = DeadLetterQueue()
    bus = EventBus(dead_letter=dead_letter)

    async def failing_handler(event):
        raise ValueError("broken handler")

    async def healthy_handler(event):
        return [Event(
            event_type=EventType.NOTIFICATION_SEND,
            aggregate_id=event.aggregate_id,
            aggregate_type=AggregateType.NOTIFICATION,
        )]

    bus.subscribe(EventType.SYSTEM_STARTUP, failing_handler)
    bus.subscribe(EventType.SYSTEM_STARTUP, healthy_handler)
    event = Event(
        event_type=EventType.SYSTEM_STARTUP,
        aggregate_id="failure-test",
        aggregate_type=AggregateType.SYSTEM,
    )

    produced = await bus.publish(event)

    assert {item.event_type for item in produced} == {
        EventType.SYSTEM_EVENT_FAILED,
        EventType.NOTIFICATION_SEND,
    }
    failed = next(item for item in produced if item.event_type == EventType.SYSTEM_EVENT_FAILED)
    assert failed.payload["failed_event_id"] == str(event.event_id)
    assert failed.payload["failed_event_type"] == EventType.SYSTEM_STARTUP.value
    assert failed.payload["handler"].endswith("failing_handler")
    assert failed.payload["error_type"] == "ValueError"

    entries = dead_letter.get_all()
    assert len(entries) == 1
    assert entries[0]["handler"].endswith("failing_handler")
    assert entries[0]["error_type"] == "ValueError"


async def test_cascade_adds_metadata_without_mutating_produced_event():
    bus = EventBus()
    child = Event(
        event_type=EventType.NOTIFICATION_SEND,
        aggregate_id="copy-test",
        aggregate_type=AggregateType.NOTIFICATION,
        metadata={"source": "handler"},
    )

    async def handler(event):
        return [child]

    bus.subscribe(EventType.SYSTEM_STARTUP, handler)
    root = Event(
        event_type=EventType.SYSTEM_STARTUP,
        aggregate_id="copy-test",
        aggregate_type=AggregateType.SYSTEM,
    )

    produced = await bus.publish_cascade(root)

    assert child.metadata == {"source": "handler"}
    assert produced[0] is not child
    assert produced[0].metadata["source"] == "handler"
    assert produced[0].metadata["trace_id"] == str(root.event_id)
    assert produced[0].metadata["cascade_depth"] == 1


if __name__ == "__main__":
    asyncio.run(test_basic_pubsub())
    asyncio.run(test_no_subscriber())
    asyncio.run(test_multiple_subscribers())
    print("\nEventBus: all checks passed")
