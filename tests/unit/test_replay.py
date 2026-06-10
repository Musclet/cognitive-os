"""Test: Replay — deterministic state rebuild."""

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, ".")

from src.core.events import Event, EventType, AggregateType
from src.core.state_engine import StateEngine
from src.storage.event_store import EventStore
from src.storage.snapshot_store import SnapshotStore
from src.storage.db import init_db, close_db


def make_homework_events() -> list[Event]:
    """Create a deterministic set of homework events."""
    return [
        Event(EventType.HOMEWORK_NEW, "hw-1", AggregateType.HOMEWORK,
              payload={"title": "数学作业", "course": "数学", "deadline": "2026-06-01T23:59:00Z"}),
        Event(EventType.HOMEWORK_NEW, "hw-2", AggregateType.HOMEWORK,
              payload={"title": "英语作文", "course": "英语", "deadline": "2026-05-28T23:59:00Z"}),
        Event(EventType.HOMEWORK_PARSED, "user-1", AggregateType.HOMEWORK,
              payload={"count": 2, "source": "chaoxing"}),
        Event(EventType.NOTIFICATION_SEND, "user-1", AggregateType.NOTIFICATION,
              payload={"message": "你有2个作业"}),
    ]


async def test_replay_deterministic():
    """Build state, hash, rebuild, hash must match."""
    events = make_homework_events()

    engine1 = StateEngine()
    for e in events:
        await engine1.apply(e)
    hash1 = engine1.state_hash()

    engine2 = StateEngine()
    await engine2.rebuild_from_events(events)
    hash2 = engine2.state_hash()

    assert hash1 == hash2, f"Hash mismatch: {hash1} != {hash2}"
    assert engine2.event_count == len(events)
    print("✓ replay deterministic — state hash matches")


async def test_replay_no_duplicates():
    """Replaying same events twice should not double-count."""
    events = make_homework_events()

    engine = StateEngine()
    await engine.rebuild_from_events(events)
    count1 = engine.event_count

    # Replay same events
    await engine.rebuild_from_events(events)
    count2 = engine.event_count

    assert count1 == count2 == len(events)
    print("✓ replay does not duplicate events")


async def test_replay_state_content():
    """Specific values survive rebuild."""
    events = make_homework_events()

    engine = StateEngine()
    await engine.rebuild_from_events(events)

    hw1 = engine.get_view("homework", "hw-1")
    assert hw1["title"] == "数学作业"
    assert hw1["course"] == "数学"
    assert hw1["deadline"] == "2026-06-01T23:59:00Z"

    parsed = engine.get_view("homework", "user-1")
    assert parsed["count"] == 2

    print("✓ replay preserves state content")


async def test_replay_with_snapshot():
    """Rebuild using snapshot + remaining events."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "test.db")
        db_url = f"sqlite+aiosqlite:///{db_path}"
        await init_db(db_url)

        try:
            event_store = EventStore()
            snapshot_store = SnapshotStore()

            # Build state and snapshot at event 2
            events = make_homework_events()

            engine1 = StateEngine()
            for i, e in enumerate(events):
                await engine1.apply(e)
                if i == 1:  # After 2nd event
                    await snapshot_store.save(engine1._state, i + 1)
                    await event_store.append(events[i])
                else:
                    await event_store.append(e) if i > 1 else await event_store.append(e)

            # Fix: actually let's redo this properly
            await close_db()
        finally:
            await close_db()

    # Simpler approach: test rebuild_with_snapshot directly
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "test2.db")
        db_url = f"sqlite+aiosqlite:///{db_path}"
        await init_db(db_url)

        try:
            event_store = EventStore()
            snapshot_store = SnapshotStore()

            events = make_homework_events()

            # Persist all events
            for e in events:
                await event_store.append(e)

            # Full build to get reference hash
            engine_ref = StateEngine()
            await engine_ref.rebuild_from_events(events)
            ref_hash = engine_ref.state_hash()

            # Save snapshot after 2nd event
            snapshot_state = {}
            temp_engine = StateEngine()
            for e in events[:2]:
                await temp_engine.apply(e)
            snapshot_state = temp_engine._state
            await snapshot_store.save(snapshot_state, 2)

            # Now rebuild_with_snapshot
            engine2 = StateEngine()
            hash2 = await engine2.rebuild_with_snapshot(event_store, snapshot_store)

            assert hash2 == ref_hash, f"Snapshot rebuild hash mismatch: {hash2} != {ref_hash}"
            print("✓ rebuild with snapshot matches full replay")

        finally:
            await close_db()


async def test_snapshot_fallback_on_corruption():
    """If snapshot is missing, rebuild still works via full replay."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "test3.db")
        db_url = f"sqlite+aiosqlite:///{db_path}"
        await init_db(db_url)

        try:
            event_store = EventStore()
            # Don't create any snapshot
            snapshot_store = SnapshotStore()

            events = make_homework_events()
            for e in events:
                await event_store.append(e)

            engine = StateEngine()
            hash_val = await engine.rebuild_with_snapshot(event_store, snapshot_store)
            assert hash_val  # should produce a hash (full replay fallback)

            hw1 = engine.get_view("homework", "hw-1")
            assert hw1["title"] == "数学作业"
            print("✓ fallback to full replay when no snapshot")

        finally:
            await close_db()


async def run_tests():
    await test_replay_deterministic()
    await test_replay_no_duplicates()
    await test_replay_state_content()
    await test_replay_with_snapshot()
    await test_snapshot_fallback_on_corruption()
    print("\nReplay: all checks passed")


if __name__ == "__main__":
    asyncio.run(run_tests())
