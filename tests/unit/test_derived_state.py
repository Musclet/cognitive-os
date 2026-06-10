"""Test: Derived State — deterministic computation from events."""

import asyncio
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, ".")

from src.core.events import Event, EventType, AggregateType
from src.core.state_engine import StateEngine
from src.derived_state.workload import compute_workload
from src.derived_state.deadline_pressure import compute_deadline_pressure
from src.derived_state.activity_density import compute_activity_density
from derived_state.active_context import derive_active_context


def make_state_with_homework(count: int, courses: list[str] = None) -> dict:
    """Build a state dict with N homework entries."""
    if courses is None:
        courses = ["数学"] * count
    state = {"homework": {}}
    for i in range(count):
        state["homework"][f"hw-{i}"] = {
            "title": f"作业{i}",
            "course": courses[i] if i < len(courses) else "未知",
            "status": "pending",
            "deadline": "2026-06-01T23:59:00Z",
        }
    return state


# ── Workload ──────────────────────────────────────────────────────────

def test_workload_empty():
    result = compute_workload({})
    assert result["total"] == 0
    assert result["score"] == 0.0
    print("✓ workload: empty state → 0")


def test_workload_capped():
    state = make_state_with_homework(20)
    result = compute_workload(state)
    assert result["total"] == 20
    assert result["score"] == 1.0  # capped
    print("✓ workload: capped at 1.0")


def test_workload_by_course():
    state = make_state_with_homework(4, ["数学", "数学", "英语", "英语"])
    result = compute_workload(state)
    assert result["by_course"] == {"数学": 2, "英语": 2}
    assert result["score"] == 0.4  # 4/10
    print("✓ workload: by_course aggregation")


def test_workload_deterministic():
    state = make_state_with_homework(3)
    r1 = compute_workload(state)
    r2 = compute_workload(state)
    assert r1 == r2
    print("✓ workload: deterministic")


# ── Deadline Pressure ─────────────────────────────────────────────────

def test_deadline_pressure_empty():
    result = compute_deadline_pressure({})
    assert result["score"] == 0.0
    assert result["overdue_count"] == 0
    print("✓ deadline_pressure: empty → 0")


def test_deadline_pressure_overdue():
    state = {"homework": {
        "hw-1": {
            "title": "过期作业",
            "course": "数学",
            "status": "pending",
            "deadline": "2020-01-01T00:00:00Z",  # way in the past
        }
    }}
    result = compute_deadline_pressure(state)
    assert result["overdue_count"] == 1
    assert result["score"] == 1.0
    print("✓ deadline_pressure: overdue → 1.0")


def test_deadline_pressure_deterministic():
    state = make_state_with_homework(2)
    r1 = compute_deadline_pressure(state)
    r2 = compute_deadline_pressure(state)
    assert r1 == r2
    print("✓ deadline_pressure: deterministic")


def test_deadline_pressure_ignores_items_beyond_10_days():
    deadline = (datetime.now(timezone.utc) + timedelta(days=11)).isoformat()
    state = {"homework": {
        "hw-1": {
            "title": "远期作业",
            "course": "数学",
            "status": "pending",
            "deadline": deadline,
        }
    }}
    result = compute_deadline_pressure(state)
    assert result["score"] == 0.0


def test_deadline_pressure_24h_is_super_urgent():
    deadline = (datetime.now(timezone.utc) + timedelta(hours=23)).isoformat()
    state = {"homework": {
        "hw-1": {
            "title": "明天交",
            "course": "数学",
            "status": "pending",
            "deadline": deadline,
        }
    }}
    result = compute_deadline_pressure(state)
    assert result["score"] == 1.0


def test_active_context_does_not_mark_11_day_item_most_urgent():
    deadline = (datetime.now(timezone.utc) + timedelta(days=11)).isoformat()
    state = {"homework": {
        "hw-1": {
            "title": "远期作业",
            "course": "数学",
            "status": "pending",
            "deadline": deadline,
        }
    }}
    result = derive_active_context(state)
    assert result["active_course_count"] == 1
    assert result["most_urgent"] is None


# ── Activity Density ──────────────────────────────────────────────────

def test_activity_density_empty():
    result = compute_activity_density({})
    assert result["events_last_hour"] == 0
    assert result["score"] == 0.0
    print("✓ activity_density: empty → 0")


def test_activity_density_deterministic():
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    state = {"notification": {
        "user-1": {
            "history": [
                {"message": "test", "sent_at": now.isoformat()},
                {"message": "test2", "sent_at": now.isoformat()},
            ]
        }
    }}
    r1 = compute_activity_density(state)
    r2 = compute_activity_density(state)
    assert r1 == r2
    print("✓ activity_density: deterministic")


# ── Integration: StateEngine derived state ────────────────────────────

async def test_state_engine_derived_state():
    """StateEngine computes derived state after applying events."""
    engine = StateEngine()

    events = [
        Event(EventType.HOMEWORK_NEW, "hw-1", AggregateType.HOMEWORK,
              payload={"title": "A", "course": "数学", "status": "pending"}),
        Event(EventType.HOMEWORK_NEW, "hw-2", AggregateType.HOMEWORK,
              payload={"title": "B", "course": "英语", "status": "pending"}),
    ]

    for e in events:
        await engine.apply(e)

    derived = engine.get_all_derived()
    assert derived["workload"]["total"] == 2
    assert derived["workload"]["score"] == 0.2
    print("✓ state_engine computes derived state")


async def test_derived_state_replay_consistency():
    """Rebuild produces same derived state."""
    events = [
        Event(EventType.HOMEWORK_NEW, "hw-1", AggregateType.HOMEWORK,
              payload={"title": "A", "course": "数学", "status": "pending"}),
        Event(EventType.HOMEWORK_NEW, "hw-2", AggregateType.HOMEWORK,
              payload={"title": "B", "course": "英语", "status": "pending"}),
    ]

    engine1 = StateEngine()
    for e in events:
        await engine1.apply(e)
    d1 = engine1.get_all_derived()

    engine2 = StateEngine()
    await engine2.rebuild_from_events(events)
    d2 = engine2.get_all_derived()

    assert d1 == d2
    print("✓ derived state consistent after replay")


if __name__ == "__main__":
    test_workload_empty()
    test_workload_capped()
    test_workload_by_course()
    test_workload_deterministic()
    test_deadline_pressure_empty()
    test_deadline_pressure_overdue()
    test_deadline_pressure_deterministic()
    test_activity_density_empty()
    test_activity_density_deterministic()
    asyncio.run(test_state_engine_derived_state())
    asyncio.run(test_derived_state_replay_consistency())
    print("\nDerived State: all checks passed")
