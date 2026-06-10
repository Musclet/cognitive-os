"""Replay engine — rebuild state from event log.

Usage: python scripts/replay.py [--db DB_URL] [--save-snapshot]
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.state_engine import StateEngine
from src.storage.db import init_db, close_db
from src.storage.event_store import EventStore
from src.storage.snapshot_store import SnapshotStore


async def replay(db_url: str, save_snapshot: bool = False) -> None:
    await init_db(db_url)
    try:
        event_store = EventStore()
        snapshot_store = SnapshotStore()
        engine = StateEngine()

        print(f"Database: {db_url}")
        total = await event_store.count()
        print(f"Events in log: {total}")

        if total == 0:
            print("No events to replay.")
            return

        print("\nReplaying events...\n")

        # Full replay with trace
        events = await event_store.replay_all()
        for i, event in enumerate(events):
            ts = event.timestamp.strftime("%H:%M:%S")
            print(f"  [{ts}] {event.event_type}  ({event.aggregate_id})")

        await engine.rebuild_from_events(events)

        print(f"\nState rebuilt from {len(events)} events")
        print(f"State hash: {engine.state_hash()}")

        # Derived state
        derived = engine.get_all_derived()
        print(f"\nDerived state:")
        for name, data in derived.items():
            print(f"  {name}: {data}")

        if save_snapshot:
            engine.save_snapshot()
            print("\nSnapshot saved.")

        print("\n✓ Replay complete")

    finally:
        await close_db()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Replay events to rebuild state")
    parser.add_argument("--db", default="sqlite+aiosqlite:///data/cognitive_os.db", help="Database URL")
    parser.add_argument("--save-snapshot", action="store_true", help="Save snapshot after replay")
    args = parser.parse_args()

    asyncio.run(replay(args.db, args.save_snapshot))
