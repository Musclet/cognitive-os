"""Event inspector — query and display events from the log.

Usage:
  python scripts/inspect.py --recent 10
  python scripts/inspect.py --aggregate hw-001
  python scripts/inspect.py --type homework.new
  python scripts/inspect.py --causation <event_id>
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.storage.db import init_db, close_db
from src.storage.event_store import EventStore


def _format_payload(payload: dict, indent: int = 2) -> str:
    """Format a payload dict for display."""
    prefix = " " * indent
    lines = []
    for key, value in payload.items():
        if isinstance(value, (list, dict)):
            import json
            value = json.dumps(value, ensure_ascii=False)
        lines.append(f"{prefix}{key}: {value}")
    return "\n".join(lines)


def _display_event(event, show_causation: bool = True, depth: int = 0) -> None:
    """Display a single event."""
    indent = "  " * depth
    ts = event.timestamp.strftime("%H:%M:%S")
    print(f"{indent}[{ts}] {event.event_type}  ({event.aggregate_id})")

    if show_causation and event.causation_id:
        print(f"{indent}  caused by: {event.causation_id}")

    if event.payload:
        print(f"{indent}  payload:")
        print(_format_payload(event.payload, indent=4))


async def _print_causation_chain(event_store: EventStore, event_id: str) -> None:
    """Print the full causation chain for an event."""
    current_id = event_id
    depth = 0

    while current_id:
        event = await event_store.get_by_event_id(current_id)
        if event is None:
            print(f"  Event {current_id} not found")
            break

        _display_event(event, show_causation=False, depth=depth)
        if depth > 0 and event.causation_id:
            print(f"{'  ' * depth}  caused by:")

        if event.causation_id:
            current_id = str(event.causation_id)
            depth += 1
        else:
            break


async def inspect(args) -> None:
    db_url = args.db
    await init_db(db_url)
    try:
        store = EventStore()

        if args.recent:
            print(f"Recent {args.recent} events:\n")
            events = await store.get_recent(args.recent)
            for event in events:
                _display_event(event)
                print()

        elif args.aggregate:
            print(f"Events for aggregate '{args.aggregate}':\n")
            events = await store.get_by_aggregate(args.aggregate)
            for event in events:
                _display_event(event)
                print()
            print(f"Total: {len(events)} events")

        elif args.type:
            print(f"Events of type '{args.type}':\n")
            events = await store.get_by_type(args.type)
            for event in events:
                _display_event(event)
                print()
            print(f"Total: {len(events)} events")

        elif args.causation:
            print(f"Causation chain for event '{args.causation}':\n")
            await _print_causation_chain(store, args.causation)

        else:
            # Default: show recent 20
            print(f"Recent 20 events:\n")
            events = await store.get_recent(20)
            for event in events:
                _display_event(event)
                print()

        total = await store.count()
        print(f"---\nTotal events in log: {total}")

    finally:
        await close_db()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Inspect events in the event log")
    parser.add_argument("--db", default="sqlite+aiosqlite:///data/cognitive_os.db", help="Database URL")
    parser.add_argument("--recent", type=int, help="Show N most recent events")
    parser.add_argument("--aggregate", help="Filter by aggregate_id")
    parser.add_argument("--type", help="Filter by event_type")
    parser.add_argument("--causation", help="Show causation chain for event_id")
    args = parser.parse_args()

    asyncio.run(inspect(args))
