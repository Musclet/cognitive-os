"""Test: SnapshotStore — save, retrieve, fallback."""

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, ".")

from src.storage.snapshot_store import SnapshotStore
from src.storage.db import init_db, close_db


async def with_temp_db(test_fn):
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "test.db")
        db_url = f"sqlite+aiosqlite:///{db_path}"
        await init_db(db_url)
        try:
            await test_fn()
        finally:
            await close_db()


async def test_save_and_get_latest():
    store = SnapshotStore()
    state = {"homework": {"hw-1": {"title": "A"}}}
    sid = await store.save(state, 10)
    assert sid is not None

    latest = await store.get_latest()
    assert latest is not None
    loaded_state, seq = latest
    assert seq == 10
    assert loaded_state["homework"]["hw-1"]["title"] == "A"
    print("✓ save and get_latest")


async def test_get_latest_returns_newest():
    store = SnapshotStore()
    await store.save({"v": 1}, 10)
    await store.save({"v": 2}, 20)
    await store.save({"v": 3}, 30)

    latest = await store.get_latest()
    assert latest is not None
    state, seq = latest
    assert seq == 30
    assert state["v"] == 3
    print("✓ get_latest returns newest")


async def test_no_snapshot_returns_none():
    store = SnapshotStore()
    latest = await store.get_latest()
    assert latest is None
    print("✓ no snapshot returns None")


async def test_delete_older_than():
    store = SnapshotStore()
    await store.save({"v": 1}, 10)
    await store.save({"v": 2}, 20)
    await store.save({"v": 3}, 30)

    deleted = await store.delete_older_than(20)
    assert deleted == 1  # only seq 10 deleted

    latest = await store.get_latest()
    assert latest is not None
    _, seq = latest
    assert seq == 30
    print("✓ delete_older_than")


async def run_tests():
    await with_temp_db(test_save_and_get_latest)
    await with_temp_db(test_get_latest_returns_newest)
    await with_temp_db(test_no_snapshot_returns_none)
    await with_temp_db(test_delete_older_than)
    print("\nSnapshotStore: all checks passed")


if __name__ == "__main__":
    asyncio.run(run_tests())
