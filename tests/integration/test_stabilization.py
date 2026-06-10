"""Integration test: full stabilization flow — persist, replay, snapshot, derived state."""

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, ".")

from src.core.events import Event, EventType, AggregateType
from src.core.bus import EventBus
from src.core.pipeline import Pipeline
from src.core.state_engine import StateEngine
from src.domain.homework.handlers import handle_user_command, handle_fetch_completed
from src.connector.chaoxing.client import ChaoxingConnector
from src.storage.db import init_db, close_db
from src.storage.event_store import EventStore
from src.storage.snapshot_store import SnapshotStore


async def test_durable_pipeline_with_bus():
    """Full flow: EventBus with EventStore → persist → pipeline → replay → verify."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "test.db")
        db_url = f"sqlite+aiosqlite:///{db_path}"
        await init_db(db_url)

        try:
            event_store = EventStore()
            snapshot_store = SnapshotStore()

            # Setup bus with durable store
            bus = EventBus(event_store=event_store)
            engine = StateEngine(
                snapshot_store=snapshot_store,
                snapshot_interval=3,  # small interval for testing
            )
            connector = ChaoxingConnector(use_mock=True)

            bus.subscribe(EventType.USER_COMMAND_RECEIVED, handle_user_command)
            bus.subscribe(EventType.CONNECTOR_FETCH_REQUESTED, connector.handle_fetch_request)
            bus.subscribe(EventType.CONNECTOR_FETCH_COMPLETED, handle_fetch_completed)
            bus.subscribe(EventType.HOMEWORK_PARSED, engine.apply)
            bus.subscribe(EventType.HOMEWORK_NEW, engine.apply)
            bus.subscribe(EventType.NOTIFICATION_SEND, engine.apply)

            pipeline = Pipeline(bus)

            # Simulate user command
            event = Event(
                event_type=EventType.USER_COMMAND_RECEIVED,
                aggregate_id="user-1",
                aggregate_type=AggregateType.USER,
                payload={"command": "check_homework"},
            )

            await pipeline.run(event)

            # Verify event store has all events
            stored_count = await event_store.count()
            assert stored_count >= 7, f"Expected >= 7 events in store, got {stored_count}"
            print(f"✓ {stored_count} events persisted to event store")

            # Verify state engine has correct state
            hw1 = engine.get_view("homework", "hw-001")
            assert hw1["course"] == "高等数学"

            # Get state snapshot
            hash1 = engine.state_hash()
            print(f"  state hash: {hash1[:16]}...")

            # Replay from event store into a new engine
            engine2 = StateEngine()
            await engine2.rebuild_with_snapshot(event_store, snapshot_store)
            hash2 = engine2.state_hash()

            assert hash1 == hash2, "Replay hash mismatch!"
            print(f"✓ replay hash matches: {hash2[:16]}...")

            # Verify replayed state content
            hw1_replay = engine2.get_view("homework", "hw-001")
            assert hw1_replay["course"] == "高等数学"
            print("✓ replayed state content matches original")

            # Derived state
            d1 = engine.get_all_derived()
            d2 = engine2.get_all_derived()
            assert d1 == d2, f"Derived state mismatch: {d1} != {d2}"
            print(f"✓ derived state consistent after replay")
            print(f"  workload: {d1['workload']}")
            print(f"  deadline_pressure: {d1['deadline_pressure']}")
            print(f"  activity_density: {d1['activity_density']}")

            # Query events by type
            homework_new_events = await event_store.get_by_type("homework.new")
            assert len(homework_new_events) == 2
            print(f"✓ event store query: 2 homework.new events")

            # Causation chain query
            causation_events = await event_store.get_by_causation(
                str(homework_new_events[0].causation_id)
            )
            assert len(causation_events) >= 1
            print(f"✓ causation chain query works")

        finally:
            await close_db()

    print("\nFull stabilization integration: all checks passed")


if __name__ == "__main__":
    asyncio.run(test_durable_pipeline_with_bus())
