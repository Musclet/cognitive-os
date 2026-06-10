"""Test: EventStore — append, replay, immutability."""

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, ".")

from src.core.events import Event, EventType, AggregateType
from src.storage.event_store import EventStore
from src.storage.db import init_db, close_db


async def with_temp_db(test_fn):
    """Run a test with a fresh isolated SQLite database."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "test.db")
        db_url = f"sqlite+aiosqlite:///{db_path}"
        await init_db(db_url)
        try:
            await test_fn()
        finally:
            await close_db()


async def test_append_and_retrieve():
    store = EventStore()
    event = Event(
        event_type=EventType.HOMEWORK_NEW,
        aggregate_id="hw-1",
        aggregate_type=AggregateType.HOMEWORK,
        payload={"title": "测试作业", "course": "数学"},
    )

    seq = await store.append(event)
    assert seq == 1

    events = await store.replay_all()
    assert len(events) == 1
    assert events[0].event_type == EventType.HOMEWORK_NEW
    assert events[0].payload["title"] == "测试作业"
    assert events[0].event_id == event.event_id
    print("✓ append and retrieve")


async def test_unique_constraint():
    store = EventStore()
    event = Event(
        event_type=EventType.HOMEWORK_NEW,
        aggregate_id="hw-2",
        aggregate_type=AggregateType.HOMEWORK,
    )

    await store.append(event)

    try:
        await store.append(event)
        assert False, "should have raised"
    except Exception:
        pass
    print("✓ unique constraint on event_id")


async def test_replay_order():
    store = EventStore()
    e1 = Event(EventType.HOMEWORK_NEW, "a", AggregateType.HOMEWORK, payload={"n": 1})
    e2 = Event(EventType.HOMEWORK_NEW, "b", AggregateType.HOMEWORK, payload={"n": 2})
    e3 = Event(EventType.HOMEWORK_NEW, "c", AggregateType.HOMEWORK, payload={"n": 3})

    await store.append(e1)
    await store.append(e2)
    await store.append(e3)

    events = await store.replay_all()
    assert len(events) == 3
    assert events[0].payload["n"] == 1
    assert events[1].payload["n"] == 2
    assert events[2].payload["n"] == 3
    print("✓ replay order by sequence")


async def test_causation_query():
    store = EventStore()
    root = Event(EventType.USER_COMMAND_RECEIVED, "u1", AggregateType.USER)
    await store.append(root)

    child = Event(
        EventType.CONNECTOR_FETCH_REQUESTED, "u1", AggregateType.HOMEWORK,
        causation_id=root.event_id,
    )
    await store.append(child)

    results = await store.get_by_causation(str(root.event_id))
    assert len(results) == 1
    assert results[0].event_id == child.event_id
    print("✓ causation_id query")


async def test_reopen_persistence():
    """Simulate process restart: write, close DB, reopen, read."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "persist.db")
        db_url = f"sqlite+aiosqlite:///{db_path}"

        await init_db(db_url)
        store1 = EventStore()
        event = Event(EventType.HOMEWORK_NEW, "hw-persist", AggregateType.HOMEWORK, payload={"msg": "survives"})
        await store1.append(event)
        await close_db()

        # Reopen
        await init_db(db_url)
        store2 = EventStore()
        events = await store2.replay_all()
        found = [e for e in events if e.aggregate_id == "hw-persist"]
        assert len(found) == 1
        assert found[0].payload["msg"] == "survives"
        await close_db()
    print("✓ events survive process restart")


async def test_count_and_last_sequence():
    store = EventStore()
    initial = await store.count()
    seq_before = await store.last_sequence()

    e = Event(EventType.HOMEWORK_NEW, "count-test", AggregateType.HOMEWORK)
    await store.append(e)

    assert await store.count() == initial + 1
    assert await store.last_sequence() == seq_before + 1
    print("✓ count and last_sequence")


async def run_tests():
    await with_temp_db(test_append_and_retrieve)
    await with_temp_db(test_unique_constraint)
    await with_temp_db(test_replay_order)
    await with_temp_db(test_causation_query)
    await test_reopen_persistence()  # manages its own DB
    await with_temp_db(test_count_and_last_sequence)
    print("\nEventStore: all checks passed")


if __name__ == "__main__":
    asyncio.run(run_tests())
