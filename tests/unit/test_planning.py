"""Test: Assisted Planning — scheduling recommendations, overload, recovery."""

import asyncio
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, ".")

from src.core.temporal import TimeBlock, TemporalSource, TimeBlockType
from src.core.events import Event, EventType, AggregateType
from src.core.state_engine import StateEngine
from src.derived_state.planning import compute_planning, _find_free_slots, _detect_overloaded_days


# ── Free slot computation ──────────────────────────────────────────────

def test_free_slots_empty():
    now = datetime.now(timezone.utc)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    slots = _find_free_slots(today, [])
    assert len(slots) == 1
    assert slots[0]["duration_minutes"] == 1020  # 17h = 6:00-23:00
    print("✓ empty day → 1 free slot (17h)")


def test_free_slots_with_blocks():
    now = datetime.now(timezone.utc)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    blocks = [
        TimeBlock("a", TemporalSource.JWXT, TimeBlockType.CLASS_LECTURE,
                  today.replace(hour=8), today.replace(hour=10), "Class"),
        TimeBlock("b", TemporalSource.JWXT, TimeBlockType.CLASS_LAB,
                  today.replace(hour=14), today.replace(hour=16), "Lab"),
    ]
    slots = _find_free_slots(today, blocks)
    # Should have: 6:00-8:00, 10:00-14:00, 16:00-23:00
    assert len(slots) == 3
    print(f"✓ 2 blocks → {len(slots)} free slots")


# ── Planning computation ───────────────────────────────────────────────

def test_planning_empty():
    planning = compute_planning([], {"stress_projection": 0, "fatigue_risk": 0, "pending_total": 0})
    assert planning["pending_tasks"] == 0
    assert planning["recommended_windows"] == []
    assert len(planning["planning_advice"]) == 1
    print("✓ planning: empty → no windows, default advice")


def test_planning_with_tasks():
    now = datetime.now(timezone.utc)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)

    blocks = [
        TimeBlock("a", TemporalSource.JWXT, TimeBlockType.CLASS_LECTURE,
                  today.replace(hour=8), today.replace(hour=12), "Morning class"),
        TimeBlock("b", TemporalSource.JWXT, TimeBlockType.CLASS_LAB,
                  today.replace(hour=14), today.replace(hour=16), "Afternoon lab"),
    ]

    cognition = {
        "stress_projection": 0.3,
        "fatigue_risk": 0.2,
        "next_48h_capacity": 0.5,
        "pending_total": 3,
        "deadline_pressure": 0.4,
    }

    planning = compute_planning(blocks, cognition)
    assert planning["pending_tasks"] == 3
    windows = planning["recommended_windows"]
    assert len(windows) > 0

    # Should have deep work windows (low stress + long free slots)
    types = [w["type"] for w in windows]
    print(f"✓ planning: 3 tasks → {len(windows)} windows, types={types}")


def test_planning_fatigue_downgrade():
    now = datetime.now(timezone.utc)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)

    blocks = [
        TimeBlock("a", TemporalSource.JWXT, TimeBlockType.CLASS_LECTURE,
                  today.replace(hour=8), today.replace(hour=10), "Short class"),
    ]

    # High fatigue → downgrade
    cog_high_fatigue = {
        "stress_projection": 0.3,
        "fatigue_risk": 0.7,
        "pending_total": 2,
    }
    p1 = compute_planning(blocks, cog_high_fatigue)
    types1 = [w["type"] for w in p1["recommended_windows"]]
    assert "deep_work" not in types1  # fatigue should block deep work

    # Low fatigue → deep work allowed
    cog_low_fatigue = {
        "stress_projection": 0.2,
        "fatigue_risk": 0.2,
        "pending_total": 2,
    }
    p2 = compute_planning(blocks, cog_low_fatigue)
    types2 = [w["type"] for w in p2["recommended_windows"]]

    print(f"✓ planning: high fatigue types={types1}, low fatigue types={types2}")


def test_planning_deterministic():
    blocks = [TimeBlock("a", TemporalSource.JWXT, TimeBlockType.CLASS_LECTURE,
                        datetime.now(timezone.utc).replace(hour=8),
                        datetime.now(timezone.utc).replace(hour=10), "X")]
    cog = {"stress_projection": 0.3, "fatigue_risk": 0.2, "pending_total": 1}

    p1 = compute_planning(blocks, cog)
    p2 = compute_planning(blocks, cog)
    assert p1 == p2
    print("✓ planning: deterministic")


# ── Overload detection ─────────────────────────────────────────────────

def test_overload_detection():
    now = datetime.now(timezone.utc)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Pack a day with 14h of classes
    blocks = []
    for h in range(8, 22, 2):
        blocks.append(TimeBlock(
            f"b{h}", TemporalSource.JWXT, TimeBlockType.CLASS_LECTURE,
            today.replace(hour=h), today.replace(hour=h+2), f"Class {h}"
        ))

    overloaded = _detect_overloaded_days(blocks, today)
    assert len(overloaded) > 0
    assert overloaded[0]["level"] in ("critical", "high")
    print(f"✓ overload: {overloaded[0]['level']} at {overloaded[0]['density']*100:.0f}%")


def test_no_overload():
    now = datetime.now(timezone.utc)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    blocks = [
        TimeBlock("a", TemporalSource.JWXT, TimeBlockType.CLASS_LECTURE,
                  today.replace(hour=10), today.replace(hour=12), "Light day"),
    ]
    overloaded = _detect_overloaded_days(blocks, today)
    assert len(overloaded) == 0
    print("✓ no overload on light day")


# ── StateEngine integration ────────────────────────────────────────────

async def test_state_engine_planning():
    engine = StateEngine()

    # Add temporal blocks
    now = datetime.now(timezone.utc)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)

    blocks = [
        TimeBlock("b1", TemporalSource.JWXT, TimeBlockType.CLASS_LECTURE,
                  today.replace(hour=8), today.replace(hour=12), "Morning"),
        TimeBlock("b2", TemporalSource.JWXT, TimeBlockType.CLASS_LAB,
                  today.replace(hour=14), today.replace(hour=16), "Afternoon"),
    ]

    for b in blocks:
        await engine.apply(Event(
            EventType.TEMPORAL_BLOCK_ADDED, b.block_id, AggregateType.TEMPORAL,
            payload=b.to_dict(),
        ))

    # Add homework
    await engine.apply(Event(
        EventType.HOMEWORK_NEW, "hw-1", AggregateType.HOMEWORK,
        payload={"title": "Math", "status": "pending", "deadline": "2026-06-01T23:59:00Z"},
    ))

    derived = engine.get_all_derived()
    planning = derived.get("planning", {})
    assert "recommended_windows" in planning
    assert planning["pending_tasks"] == 1
    print(f"✓ StateEngine planning: {len(planning['recommended_windows'])} windows, {len(planning.get('focus_windows',[]))} focus")


async def test_planning_replay():
    now = datetime.now(timezone.utc)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    b = TimeBlock("b1", TemporalSource.JWXT, TimeBlockType.CLASS_LECTURE,
                  today.replace(hour=8), today.replace(hour=10), "Class")

    events = [
        Event(EventType.TEMPORAL_BLOCK_ADDED, b.block_id, AggregateType.TEMPORAL, payload=b.to_dict()),
        Event(EventType.HOMEWORK_NEW, "hw-1", AggregateType.HOMEWORK,
              payload={"title": "A", "status": "pending"}),
    ]

    e1 = StateEngine()
    for ev in events:
        await e1.apply(ev)
    p1 = e1.get_all_derived()["planning"]

    e2 = StateEngine()
    await e2.rebuild_from_events(events)
    p2 = e2.get_all_derived()["planning"]

    assert p1 == p2
    print("✓ planning replay: deterministic")


if __name__ == "__main__":
    print("=== Free Slots ===")
    test_free_slots_empty()
    test_free_slots_with_blocks()

    print("\n=== Planning ===")
    test_planning_empty()
    test_planning_with_tasks()
    test_planning_fatigue_downgrade()
    test_planning_deterministic()

    print("\n=== Overload ===")
    test_overload_detection()
    test_no_overload()

    print("\n=== StateEngine ===")
    asyncio.run(test_state_engine_planning())
    asyncio.run(test_planning_replay())

    print("\nAssisted Planning: all checks passed")
