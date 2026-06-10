"""Integration test: simulate a Telegram user message through the full pipeline.

No real bot token needed — directly tests the event chain.
"""

import asyncio
import sys
sys.path.insert(0, ".")

from src.core.events import EventType
from src.core.bus import EventBus
from src.core.pipeline import Pipeline
from src.core.state_engine import StateEngine
from src.domain.homework.handlers import handle_user_command, handle_fetch_completed
from src.connector.chaoxing.client import ChaoxingConnector
from src.interface.telegram.router import parse_message, command_to_event
from src.interface.telegram.templates import format_output


async def test_full_telegram_flow():
    """User sends /homework → full chain → get notification text."""

    # ── Setup ──────────────────────────────────────────────────────
    bus = EventBus()
    state_engine = StateEngine()
    connector = ChaoxingConnector(use_mock=True)

    bus.subscribe(EventType.USER_COMMAND_RECEIVED, handle_user_command)
    bus.subscribe(EventType.CONNECTOR_FETCH_REQUESTED, connector.handle_fetch_request)
    bus.subscribe(EventType.CONNECTOR_FETCH_COMPLETED, handle_fetch_completed)
    bus.subscribe(EventType.HOMEWORK_PARSED, state_engine.apply)
    bus.subscribe(EventType.HOMEWORK_NEW, state_engine.apply)
    bus.subscribe(EventType.NOTIFICATION_SEND, state_engine.apply)

    pipeline = Pipeline(bus)

    # ── Simulate user sending /homework ────────────────────────────
    user_id = 12345
    message_text = "/homework"

    # Step 1: Router translates message → Command
    command = parse_message(message_text, user_id)
    assert command is not None, "command should be parsed"
    assert command.command_type == "check_homework"
    print("1. Router: /homework → Command(check_homework)")

    # Step 2: Command → Event
    event = command_to_event(command)
    assert event.event_type == EventType.USER_COMMAND_RECEIVED
    print("2. Command → user.command.received event")

    # Step 3: Pipeline runs full chain
    all_events = await pipeline.run(event)
    print(f"3. Pipeline: processed {len(all_events)} events")
    for e in all_events:
        print(f"   - {e.event_type}")

    # Step 4: Extract notifications
    notifications = [e for e in all_events if e.event_type == EventType.NOTIFICATION_SEND]
    assert len(notifications) == 1
    print("4. Notification event found")

    # Step 5: Format output for Telegram
    text = format_output(notifications[0])
    assert text is not None
    assert "待完成作业" in text
    assert "第三章习题" in text
    assert "Essay" in text
    print(f"5. Formatted output:\n{text}")

    # Step 6: Verify state engine
    assert state_engine.event_count == 4  # parsed + 2×new + notification
    hw1 = state_engine.get_view("homework", "hw-001")
    assert hw1["course"] == "高等数学"
    print(f"6. State engine: {state_engine.event_count} events processed, homework data intact")

    print("\n✓ full Telegram flow end-to-end")


async def test_unknown_message_flow():
    """User sends random text → no command → no pipeline."""
    user_id = 12345

    command = parse_message("hello world", user_id)
    assert command is None
    print("✓ unknown message: router returns None, no pipeline execution")


if __name__ == "__main__":
    asyncio.run(test_full_telegram_flow())
    asyncio.run(test_unknown_message_flow())
    print("\nTelegram integration: all checks passed")
