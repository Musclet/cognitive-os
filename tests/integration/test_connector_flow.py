"""Integration test: full event chain through handler + connector."""

import asyncio
import sys
sys.path.insert(0, ".")

from src.core.events import Event, EventType, AggregateType
from src.core.bus import EventBus
from src.core.pipeline import Pipeline
from src.domain.homework.handlers import handle_user_command, handle_fetch_completed
from src.connector.chaoxing.client import ChaoxingConnector


async def test_full_connector_flow():
    """End-to-end: command → connector → parsed → notification."""
    bus = EventBus()
    connector = ChaoxingConnector(use_mock=True)

    # Wire up handlers
    bus.subscribe(EventType.USER_COMMAND_RECEIVED, handle_user_command)
    bus.subscribe(EventType.CONNECTOR_FETCH_REQUESTED, connector.handle_fetch_request)
    bus.subscribe(EventType.CONNECTOR_FETCH_COMPLETED, handle_fetch_completed)

    pipeline = Pipeline(bus)

    event = Event(
        event_type=EventType.USER_COMMAND_RECEIVED,
        aggregate_id="user-1",
        aggregate_type=AggregateType.USER,
        payload={"command": "check_homework"},
    )

    all_events = await pipeline.run(event)

    # Expected chain:
    # 1. user.command.received
    # 2. connector.fetch.requested
    # 3. connector.fetch.completed (with raw data)
    # 4. homework.parsed
    # 5. homework.new (math)
    # 6. homework.new (english)
    # 7. notification.send

    event_types = [e.event_type for e in all_events]
    print("Event chain:")
    for i, et in enumerate(event_types):
        print(f"  {i+1}. {et}")

    assert EventType.USER_COMMAND_RECEIVED in event_types
    assert EventType.CONNECTOR_FETCH_REQUESTED in event_types
    assert EventType.CONNECTOR_FETCH_COMPLETED in event_types
    assert EventType.HOMEWORK_PARSED in event_types
    assert EventType.HOMEWORK_NEW in event_types
    assert EventType.NOTIFICATION_SEND in event_types

    # Check the fetch completed event has actual data
    fetch_completed = [e for e in all_events if e.event_type == EventType.CONNECTOR_FETCH_COMPLETED][0]
    assert len(fetch_completed.payload["homeworks"]) == 2
    assert fetch_completed.payload["homeworks"][0]["course"] == "高等数学"

    # Check homework.new events
    new_events = [e for e in all_events if e.event_type == EventType.HOMEWORK_NEW]
    assert len(new_events) == 2

    # Check notification has details
    notif = [e for e in all_events if e.event_type == EventType.NOTIFICATION_SEND][0]
    assert "2 个待完成作业" in notif.payload["message"]

    print("\n✓ full connector flow works")


async def test_connector_ignores_other_sources():
    """Connector ignores fetch requests for other sources."""
    bus = EventBus()
    connector = ChaoxingConnector(use_mock=True)
    bus.subscribe(EventType.CONNECTOR_FETCH_REQUESTED, connector.handle_fetch_request)

    event = Event(
        event_type=EventType.CONNECTOR_FETCH_REQUESTED,
        aggregate_id="user-1",
        aggregate_type=AggregateType.HOMEWORK,
        payload={"source": "other_system", "query": "stuff"},
    )

    result = await bus.publish(event)
    assert result == []  # chaoxing connector should ignore non-chaoxing requests
    print("✓ connector ignores other sources")


if __name__ == "__main__":
    asyncio.run(test_full_connector_flow())
    asyncio.run(test_connector_ignores_other_sources())
    print("\nConnector integration: all checks passed")
