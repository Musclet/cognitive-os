"""Runtime health diagnostic tool.

Usage: python scripts/diagnose.py [--db DATABASE_URL]

Reads event store + snapshot to produce a runtime health report.
Does NOT start any services.
"""

import asyncio
import logging
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.WARNING)


async def diagnose(database_url: str | None = None):
    from src.storage.db import init_db
    from src.storage.event_store import EventStore
    from src.storage.snapshot_store import SnapshotStore

    if database_url is None:
        db_dir = Path("data")
        db_dir.mkdir(exist_ok=True)
        database_url = f"sqlite+aiosqlite:///{db_dir / 'cognitive_os.db'}"

    await init_db(database_url)
    store = EventStore()
    snap_store = SnapshotStore()

    print("=" * 60)
    print("RUNTIME HEALTH DIAGNOSIS")
    print(f"Ran at: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)
    print()

    # 1. Event Store
    print("── Event Store ──")
    total = await store.count()
    print(f"  Total events: {total}")

    recent = await store.get_recent(20)
    if recent:
        print(f"  Latest 5 events:")
        for e in recent[-5:]:
            ts = e.timestamp.strftime("%H:%M:%S") if hasattr(e.timestamp, 'strftime') else str(e.timestamp)[:19]
            print(f"    [{ts}] {e.event_type.value:40s} {e.aggregate_id[:12]}")
    else:
        print("  No events in store")
    print()

    # 2. Heartbeat check
    print("── Heartbeat Check ──")
    hb_events = await store.get_by_type("system.runtime.heartbeat")
    print(f"  Heartbeat events found: {len(hb_events)}")

    if hb_events:
        last_hb = hb_events[-1]
        last_time = last_hb.timestamp
        now = datetime.now(timezone.utc)
        age = (now - last_time).total_seconds()
        print(f"  Last heartbeat: {last_time.isoformat()}")
        print(f"  Age: {age:.0f}s ago")
        if age > 30:
            print(f"  ⚠ EVENT LOOP LIKELY STALLED (last hb {age:.0f}s ago)")
        elif age > 15:
            print(f"  ⚠ HEARTBEAT DELAYED ({age:.0f}s)")
        else:
            print(f"  ✓ Heartbeat healthy")

        # Check for gaps
        expected_interval = 5.0
        gaps = []
        for i in range(1, len(hb_events)):
            diff = (hb_events[i].timestamp - hb_events[i-1].timestamp).total_seconds()
            if diff > expected_interval * 2:
                gaps.append((hb_events[i-1].timestamp, hb_events[i].timestamp, diff))
        if gaps:
            print(f"  ⚠ Gaps detected ({len(gaps)}):")
            for start, end, dur in gaps[-5:]:
                print(f"    {start.strftime('%H:%M:%S')} → {end.strftime('%H:%M:%S')} ({dur:.0f}s)")
    else:
        print("  No heartbeats — watchdog not yet deployed or not running")
    print()

    # 3. Dead letter queue
    print("── Dead Letter Queue ──")
    dead_events = await store.get_by_type("system.event.failed")
    timeout_events = await store.get_by_type("system.connector.timeout")
    print(f"  Failed events: {len(dead_events)}")
    print(f"  Connector timeouts: {len(timeout_events)}")
    if dead_events:
        print("  Recent failures:")
        for de in dead_events[-5:]:
            orig = de.payload.get("original_event_type", "?")
            err = de.payload.get("error", "?")[:80]
            print(f"    {orig}: {err}")
    if timeout_events:
        print("  Recent timeouts:")
        for te in timeout_events[-3:]:
            orig = te.payload.get("original_event_type", "?")
            print(f"    {orig}: {te.payload.get('timeout_s', '?')}s timeout")
    print()

    # 4. Scheduler ticks
    print("── Scheduler Activity ──")
    tick_events = await store.get_by_type("schedule.tick")
    trigger_events = await store.get_by_type("system.scheduled_trigger")
    print(f"  Schedule ticks: {len(tick_events)}")
    print(f"  Scheduled triggers: {len(trigger_events)}")
    if tick_events:
        last_tick = tick_events[-1]
        age = (datetime.now(timezone.utc) - last_tick.timestamp).total_seconds()
        print(f"  Last tick: {age:.0f}s ago")
    if trigger_events:
        last_trig = trigger_events[-1]
        print(f"  Last trigger: {last_trig.aggregate_id} @ {last_trig.timestamp.strftime('%H:%M:%S')}")
    print()

    # 5. Event type distribution
    print("── Event Type Distribution ──")
    all_events = await store.replay_all()
    type_counts: dict[str, int] = {}
    for e in all_events:
        t = e.event_type.value
        type_counts[t] = type_counts.get(t, 0) + 1
    for t, c in sorted(type_counts.items(), key=lambda x: -x[1])[:15]:
        bar = "█" * min(c, 30)
        print(f"  {t:45s} {c:4d} {bar}")
    print()

    # 6. Snapshot state
    print("── Snapshot ──")
    snap = await snap_store.get_latest()
    if snap:
        state, seq = snap
        print(f"  Latest snapshot: sequence {seq}")
        print(f"  Has state: {bool(state)}")
    else:
        print("  No snapshots found")
    print()

    # 7. Connector fetch stats
    print("── Connector Activity ──")
    requested = await store.get_by_type("connector.fetch.requested")
    completed = await store.get_by_type("connector.fetch.completed")
    failed = await store.get_by_type("connector.fetch.failed")
    print(f"  Fetches requested: {len(requested)}")
    print(f"  Fetches completed: {len(completed)}")
    print(f"  Fetches failed:    {len(failed)}")

    if completed:
        last_comp = completed[-1]
        age = (datetime.now(timezone.utc) - last_comp.timestamp).total_seconds()
        print(f"  Last successful fetch: {age:.0f}s ago")
        print(f"    Source: {last_comp.payload.get('source', '?')}")
        print(f"    Courses: {last_comp.payload.get('total_courses', '?')}")
        print(f"    Assignments: {last_comp.payload.get('total_assignments', '?')}")
        print(f"    Errors: {last_comp.payload.get('errors', 0)}")
    print()

    # 8. Overall health
    print("── OVERALL HEALTH ──")
    issues = []

    if not hb_events:
        issues.append("No heartbeats — runtime may not have watchdog enabled")
    elif (datetime.now(timezone.utc) - hb_events[-1].timestamp).total_seconds() > 30:
        issues.append("EVENT LOOP STALLED — heartbeats stopped")

    if len(timeout_events) > len(completed) * 0.5 and len(completed) > 0:
        issues.append(f"High timeout rate: {len(timeout_events)} timeouts vs {len(completed)} successes")

    if len(dead_events) > 0:
        issues.append(f"{len(dead_events)} dead-letter events present")

    if total == 0:
        issues.append("Event store is empty — system may not have run yet")

    if not issues:
        print("✓ No critical issues detected")
    else:
        for issue in issues:
            print(f"  ⚠ {issue}")

    print()
    print("=" * 60)


if __name__ == "__main__":
    db_url = sys.argv[2] if len(sys.argv) > 2 and sys.argv[1] == "--db" else None
    asyncio.run(diagnose(db_url))
