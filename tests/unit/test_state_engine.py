"""Test: StateEngine apply, snapshot, rebuild."""

import asyncio
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, ".")

from src.core.events import Event, EventType, AggregateType
from src.core.state_engine import StateEngine


async def test_apply_updates_state():
    engine = StateEngine()

    e1 = Event(
        event_type=EventType.HOMEWORK_NEW,
        aggregate_id="hw-001",
        aggregate_type=AggregateType.HOMEWORK,
        payload={"title": "习题一", "course": "数学", "deadline": "2026-06-01"},
    )

    await engine.apply(e1)

    view = engine.get_view("homework", "hw-001")
    assert view["title"] == "习题一"
    assert view["course"] == "数学"
    assert engine.event_count == 1
    print("✓ apply updates state")


async def test_apply_multiple_events():
    engine = StateEngine()

    await engine.apply(Event(
        event_type=EventType.HOMEWORK_PARSED,
        aggregate_id="user-1",
        aggregate_type=AggregateType.HOMEWORK,
        payload={"count": 3, "source": "chaoxing"},
    ))

    for i in range(3):
        await engine.apply(Event(
            event_type=EventType.HOMEWORK_NEW,
            aggregate_id=f"hw-{i}",
            aggregate_type=AggregateType.HOMEWORK,
            payload={"title": f"作业{i}", "course": "数学"},
        ))

    parsed = engine.get_view("homework", "user-1")
    assert parsed["count"] == 3

    for i in range(3):
        hw = engine.get_view("homework", f"hw-{i}")
        assert hw["title"] == f"作业{i}"

    assert engine.event_count == 4
    print("✓ apply multiple events")


async def test_snapshot_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        snap_path = Path(tmp) / "state.json"
        engine = StateEngine(snapshot_path=str(snap_path))

        await engine.apply(Event(
            event_type=EventType.HOMEWORK_NEW,
            aggregate_id="hw-1",
            aggregate_type=AggregateType.HOMEWORK,
            payload={"title": "快照测试", "course": "测试学"},
        ))
        start = datetime(2026, 6, 15, 8, 0, tzinfo=timezone.utc)
        await engine.apply(Event(
            event_type=EventType.TEMPORAL_BLOCK_ADDED,
            aggregate_id="snapshot-block",
            aggregate_type=AggregateType.TEMPORAL,
            timestamp=start,
            payload={
                "block_id": "snapshot-block",
                "source": "jwxt",
                "block_type": "class_lecture",
                "start": start.isoformat(),
                "end": (start + timedelta(hours=1)).isoformat(),
                "title": "快照课程",
            },
        ))

        engine.save_snapshot()
        assert snap_path.exists()
        raw = json.loads(snap_path.read_text(encoding="utf-8"))
        assert raw["version"] == 2
        assert raw["applied_count"] == 2
        assert raw["temporal_blocks"]

        engine2 = StateEngine(snapshot_path=str(snap_path))
        assert engine2.load_snapshot()
        view = engine2.get_view("homework", "hw-1")
        assert view["title"] == "快照测试"
        assert len(engine2.get_temporal_blocks()) == 1
        assert engine2.get_temporal_blocks()[0].title == "快照课程"
        assert engine2.state_hash() == engine.state_hash()

        print("✓ snapshot roundtrip")


async def test_snapshot_roundtrip_preserves_pending_calendar_sync():
    with tempfile.TemporaryDirectory() as tmp:
        snap_path = Path(tmp) / "state.json"
        engine = StateEngine(snapshot_path=str(snap_path))
        start = datetime(2026, 6, 15, 8, 0, tzinfo=timezone.utc)

        await engine.apply(Event(
            event_type=EventType.CONNECTOR_FETCH_STARTED,
            aggregate_id="calendar-sync",
            aggregate_type=AggregateType.SYSTEM,
            payload={"source": "google_calendar", "calendar_id": "primary"},
            metadata={"trace_id": "snapshot-sync"},
        ))
        await engine.apply(Event(
            event_type=EventType.TEMPORAL_BLOCK_ADDED,
            aggregate_id="snapshot-calendar-block",
            aggregate_type=AggregateType.TEMPORAL,
            payload={
                "block_id": "snapshot-calendar-block",
                "source": "google_calendar",
                "block_type": "calendar_event",
                "start": start.isoformat(),
                "end": (start + timedelta(hours=1)).isoformat(),
                "title": "Pending calendar event",
            },
            metadata={"trace_id": "snapshot-sync"},
        ))
        engine.save_snapshot()

        restored = StateEngine(snapshot_path=str(snap_path))
        assert restored.load_snapshot()
        assert restored.get_temporal_blocks() == []
        await restored.apply(Event(
            event_type=EventType.CONNECTOR_FETCH_COMPLETED,
            aggregate_id="calendar-sync",
            aggregate_type=AggregateType.SYSTEM,
            payload={"source": "google_calendar", "count": 1},
            metadata={"trace_id": "snapshot-sync"},
        ))

        assert [block.title for block in restored.get_temporal_blocks()] == [
            "Pending calendar event"
        ]


async def test_intervention_button_feedback_updates_behavior():
    engine = StateEngine()

    await engine.apply(Event(
        event_type=EventType.INTERVENTION_FEEDBACK_RECORDED,
        aggregate_id="user-1",
        aggregate_type=AggregateType.USER,
        payload={"intervention_id": "iv-1", "feedback": "completed"},
    ))

    behavior = engine.get_all("behavior")["current"]["feedback_log"]
    assert behavior[-1]["action"] == "accepted"
    assert behavior[-1]["outcome"] == "completed"
    assert behavior[-1]["source"] == "telegram_button"


async def test_connector_fetch_lifecycle_updates_sync_projection():
    engine = StateEngine()

    await engine.apply(Event(
        event_type=EventType.CONNECTOR_FETCH_STARTED,
        aggregate_id="sync-1",
        aggregate_type=AggregateType.SYSTEM,
        payload={"source": "jwxt"},
    ))
    sync = engine.get_all("sync")
    assert sync["jwxt"]["status"] == "running"
    assert sync["jwxt"]["last_sync_started"]

    await engine.apply(Event(
        event_type=EventType.CONNECTOR_FETCH_COMPLETED,
        aggregate_id="sync-1",
        aggregate_type=AggregateType.SYSTEM,
        payload={"source": "jwxt", "block_count": 8},
    ))
    sync = engine.get_all("sync")
    assert sync["jwxt"]["status"] == "completed"
    assert sync["jwxt"]["last_sync"]
    assert sync["jwxt"]["block_count"] == 8

    await engine.apply(Event(
        event_type=EventType.CONNECTOR_FETCH_FAILED,
        aggregate_id="sync-2",
        aggregate_type=AggregateType.SYSTEM,
        payload={"source": "chaoxing", "error": "auth failed"},
    ))
    sync = engine.get_all("sync")
    assert sync["chaoxing"]["status"] == "failed"
    assert sync["chaoxing"]["error"] == "auth failed"


async def test_vocab_sync_lifecycle_updates_sync_projection():
    engine = StateEngine()

    await engine.apply(Event(
        event_type=EventType.VOCAB_SYNC_STARTED,
        aggregate_id="momo",
        aggregate_type=AggregateType.VOCAB,
        payload={"source": "momo_vocab"},
    ))
    assert engine.get_all("sync")["momo"]["status"] == "running"

    await engine.apply(Event(
        event_type=EventType.VOCAB_SYNC_COMPLETED,
        aggregate_id="momo",
        aggregate_type=AggregateType.VOCAB,
        payload={"source": "momo_vocab", "last_sync": "2026-06-05T00:00:00Z", "npm_sync_ok": True},
    ))
    sync = engine.get_all("sync")["momo"]
    assert sync["status"] == "completed"
    assert sync["external_last_sync"] == "2026-06-05T00:00:00Z"
    assert sync["npm_sync_ok"] is True


async def test_recommendation_delay_preserves_delay_metadata():
    engine = StateEngine()

    await engine.apply(Event(
        event_type=EventType.PLANNING_RECOMMENDATION_DELAYED,
        aggregate_id="user-1",
        aggregate_type=AggregateType.USER,
        payload={
            "task_id": "hw-1",
            "task": "数学作业",
            "delay_minutes": 30,
            "delayed_until": "2026-06-05T00:30:00+00:00",
        },
    ))

    behavior = engine.get_all("behavior")["current"]["feedback_log"]
    assert behavior[-1]["action"] == "delayed"
    assert behavior[-1]["task_id"] == "hw-1"
    assert behavior[-1]["task"] == "数学作业"
    assert behavior[-1]["delay_minutes"] == 30
    assert behavior[-1]["delayed_until"] == "2026-06-05T00:30:00+00:00"


async def test_context_button_kind_expires_end_of_day_shape():
    engine = StateEngine()

    await engine.apply(Event(
        event_type=EventType.SUBJECTIVE_CONTEXT_ADDED,
        aggregate_id="user-1",
        aggregate_type=AggregateType.USER,
        payload={"kind": "social_plan", "text": "今晚聚餐", "expires_at": "2026-05-30T00:00:00+00:00"},
    ))

    notes = engine.get_all("subjective")["user-1"]["notes"]
    assert notes[-1]["kind"] == "social_plan"
    assert notes[-1]["text"] == "今晚聚餐"
    assert notes[-1]["expires_at"] == "2026-05-30T00:00:00+00:00"


async def test_rebuild_from_events():
    engine1 = StateEngine()
    events = [
        Event(EventType.HOMEWORK_NEW, aggregate_id="hw-1", aggregate_type=AggregateType.HOMEWORK, payload={"title": "A"}),
        Event(EventType.HOMEWORK_NEW, aggregate_id="hw-2", aggregate_type=AggregateType.HOMEWORK, payload={"title": "B"}),
        Event(EventType.HOMEWORK_PARSED, aggregate_id="u1", aggregate_type=AggregateType.HOMEWORK, payload={"count": 2}),
    ]

    for e in events:
        await engine1.apply(e)

    engine2 = StateEngine()
    await engine2.rebuild_from_events(events)

    assert engine2.event_count == engine1.event_count
    assert engine2.get_view("homework", "hw-1")["title"] == "A"
    assert engine2.get_view("homework", "hw-2")["title"] == "B"
    assert engine2.get_view("homework", "u1")["count"] == 2
    print("✓ rebuild from events")


async def test_stateengine_on_eventbus():
    from src.core.bus import EventBus
    from src.core.pipeline import Pipeline
    from src.domain.homework.handlers import handle_user_command, handle_fetch_completed
    from src.connector.chaoxing.client import ChaoxingConnector

    bus = EventBus()
    engine = StateEngine()
    connector = ChaoxingConnector(use_mock=True)

    bus.subscribe(EventType.USER_COMMAND_RECEIVED, handle_user_command)
    bus.subscribe(EventType.CONNECTOR_FETCH_REQUESTED, connector.handle_fetch_request)
    bus.subscribe(EventType.CONNECTOR_FETCH_COMPLETED, handle_fetch_completed)
    bus.subscribe(EventType.HOMEWORK_PARSED, engine.apply)
    bus.subscribe(EventType.HOMEWORK_NEW, engine.apply)
    bus.subscribe(EventType.NOTIFICATION_SEND, engine.apply)

    pipeline = Pipeline(bus)

    event = Event(
        event_type=EventType.USER_COMMAND_RECEIVED,
        aggregate_id="user-1",
        aggregate_type=AggregateType.USER,
        payload={"command": "check_homework"},
    )

    await pipeline.run(event)

    assert engine.event_count >= 4

    parsed_view = engine.get_view("homework", "user-1")
    assert parsed_view["count"] == 2
    assert parsed_view["source"] == "chaoxing"

    hw1 = engine.get_view("homework", "hw-001")
    assert hw1["title"] == "第三章习题"
    assert hw1["course"] == "高等数学"

    print("✓ stateengine on eventbus")
    print(f"  Total state events processed: {engine.event_count}")


async def test_state_hash():
    engine = StateEngine()
    await engine.apply(Event(
        EventType.HOMEWORK_NEW, "hw-1", AggregateType.HOMEWORK,
        payload={"title": "A"},
    ))
    h1 = engine.state_hash()
    assert len(h1) == 64  # SHA256 hex
    print("✓ state_hash produces sha256")


async def test_memory_entry_stored_and_derived_dirty():
    """MEMORY_ENTRY_CREATED stores entry and marks derived dirty."""
    engine = StateEngine()
    await engine.apply(Event(
        event_type=EventType.MEMORY_ENTRY_CREATED,
        aggregate_id="user-1",
        aggregate_type=AggregateType.USER,
        payload={"content": "学习Python", "tags": ["python"], "source": "cognitive_learning"},
    ))

    mem = engine.get_all("memory")["user-1"]["entries"]
    assert len(mem) == 1
    assert mem[0]["content"] == "学习Python"
    assert mem[0]["tags"] == ["python"]
    assert mem[0]["source"] == "cognitive_learning"
    assert engine._derived_dirty is True
    print("✓ memory entry stored and derived dirty")


async def test_memory_entry_is_derived_affecting():
    """MEMORY_ENTRY_CREATED is in _DERIVED_AFFECTING_EVENTS."""
    from src.core.state_engine import _DERIVED_AFFECTING_EVENTS
    assert EventType.MEMORY_ENTRY_CREATED in _DERIVED_AFFECTING_EVENTS
    print("✓ MEMORY_ENTRY_CREATED is derived-affecting")


async def test_memory_multiple_entries_capped_at_200():
    """Memory entries are capped at 200."""
    engine = StateEngine()
    for i in range(210):
        await engine.apply(Event(
            event_type=EventType.MEMORY_ENTRY_CREATED,
            aggregate_id=f"user-1",
            aggregate_type=AggregateType.USER,
            payload={"content": f"entry-{i}", "tags": [], "source": "test"},
        ))
    mem = engine.get_all("memory")["user-1"]["entries"]
    assert len(mem) <= 200
    # Should keep the last 200
    assert mem[0]["content"] in ("entry-10", "entry-11")  # first ~10 evicted
    print("✓ memory entries capped at 200")


# ── Undo / Revoke tests ─────────────────────────────────────────────────


async def test_user_action_reverted_finance():
    """USER_ACTION_REVERTED reverses finance outflow and category amounts."""
    engine = StateEngine()

    # First, record a finance transaction
    await engine.apply(Event(
        event_type=EventType.FINANCE_TRANSACTION_RECORDED,
        aggregate_id="monthly",
        aggregate_type=AggregateType.FINANCE,
        payload={"amount": 50, "category": "food", "description": "午餐", "month": "2026-06"},
    ))
    await engine.apply(Event(
        event_type=EventType.FINANCE_TRANSACTION_RECORDED,
        aggregate_id="monthly",
        aggregate_type=AggregateType.FINANCE,
        payload={"amount": 30, "category": "outing", "description": "电影", "month": "2026-06"},
    ))

    finance = engine.get_view("finance", "monthly")
    assert finance["outflow"] == 80
    assert finance["by_category"]["food"] == 50
    assert finance["by_category"]["outing"] == 30
    assert finance["outing_spent"] == 30

    # Revert the outing transaction
    await engine.apply(Event(
        event_type=EventType.USER_ACTION_REVERTED,
        aggregate_id="user-1",
        aggregate_type=AggregateType.USER,
        payload={
            "action_type": "finance_transaction",
            "action_id": "act-test123",
            "amount": 30,
            "category": "outing",
        },
    ))

    finance = engine.get_view("finance", "monthly")
    assert finance["outflow"] == 50  # 80 - 30
    assert finance["by_category"]["outing"] == 0  # 30 - 30
    assert finance["outing_spent"] == 0  # 30 - 30
    assert finance["by_category"]["food"] == 50  # unchanged

    # Undo state tracked
    undo = engine.get_view("undo", "user-1")
    assert undo["reverted_actions"][-1]["action_type"] == "finance_transaction"
    assert undo["reverted_actions"][-1]["action_id"] == "act-test123"

    assert engine._derived_dirty is True
    print("✓ user_action_reverted finance")


async def test_user_action_reverted_income():
    """USER_ACTION_REVERTED reverses finance income."""
    engine = StateEngine()
    await engine.apply(Event(
        event_type=EventType.FINANCE_INCOME_RECORDED,
        aggregate_id="monthly",
        aggregate_type=AggregateType.FINANCE,
        payload={"amount": 1000, "source": "生活费", "month": "2026-06"},
    ))

    finance = engine.get_view("finance", "monthly")
    assert finance["inflow"] == 1000

    await engine.apply(Event(
        event_type=EventType.USER_ACTION_REVERTED,
        aggregate_id="user-1",
        aggregate_type=AggregateType.USER,
        payload={
            "action_type": "finance_income",
            "action_id": "act-income-1",
            "amount": 1000,
        },
    ))

    finance = engine.get_view("finance", "monthly")
    assert finance["inflow"] == 0
    print("✓ user_action_reverted income")


async def test_user_action_revert_failed():
    """USER_ACTION_REVERT_FAILED tracks failed undo attempts."""
    engine = StateEngine()
    await engine.apply(Event(
        event_type=EventType.USER_ACTION_REVERT_FAILED,
        aggregate_id="user-1",
        aggregate_type=AggregateType.USER,
        payload={
            "action_id": "act-bad-1",
            "action_type": "finance_transaction",
            "error": "Simulated failure",
        },
    ))

    failures = engine.get_view("undo", "failures")
    assert len(failures["failures"]) == 1
    assert failures["failures"][0]["action_id"] == "act-bad-1"
    assert failures["failures"][0]["error"] == "Simulated failure"
    print("✓ user_action_revert_failed")


async def test_user_action_reverted_is_derived_affecting():
    """USER_ACTION_REVERTED is in _DERIVED_AFFECTING_EVENTS."""
    from src.core.state_engine import _DERIVED_AFFECTING_EVENTS
    assert EventType.USER_ACTION_REVERTED in _DERIVED_AFFECTING_EVENTS
    print("✓ USER_ACTION_REVERTED is derived-affecting")


if __name__ == "__main__":
    asyncio.run(test_apply_updates_state())
    asyncio.run(test_apply_multiple_events())
    asyncio.run(test_snapshot_roundtrip())
    asyncio.run(test_rebuild_from_events())
    asyncio.run(test_stateengine_on_eventbus())
    asyncio.run(test_state_hash())
    print("\nStateEngine: all checks passed")
