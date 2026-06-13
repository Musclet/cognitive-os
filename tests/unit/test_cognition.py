"""Test: Projection Intelligence — cognition, recommendations, replay."""

import asyncio
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, ".")

from src.core.events import Event, EventType, AggregateType
from src.core.state_engine import StateEngine
from src.core.temporal import TimeBlock, TemporalSource, TimeBlockType
from src.derived_state.cognition import compute_cognition
from src.domain.cognition.handlers import (
    handle_pressure_update,
    _generate_recommendations,
)


# ── Cognition computation ──────────────────────────────────────────────

def test_cognition_empty():
    """Empty state → all zeros."""
    cog = compute_cognition({}, {})
    assert cog["deadline_pressure"] == 0.0
    assert cog["workload_overload"] == 0.0
    assert cog["fatigue_risk"] == 0.0
    assert cog["stress_projection"] == 0.0
    print("✓ cognition: empty → all zeros")


def test_cognition_with_homework():
    """Pending homework → deadline pressure + overload."""
    state = {
        "homework": {
            "hw-1": {"title": "数学作业", "status": "pending", "deadline": "2026-06-01T23:59:00Z"},
            "hw-2": {"title": "英语作文", "status": "pending", "deadline": "2026-05-28T23:59:00Z"},
        }
    }
    proj = {"busy_density": 0.3, "weekly_load": 0.4, "daily_capacity": 12.0}

    cog = compute_cognition(
        state,
        proj,
        as_of=datetime(2026, 6, 13, tzinfo=timezone.utc),
    )
    assert cog["pending_total"] == 2
    assert cog["workload_overload"] > 0.1
    assert cog["deadline_pressure"] > 0
    print(f"✓ cognition: 2 pending → overload={cog['workload_overload']:.2f}, pressure={cog['deadline_pressure']:.2f}")


def test_cognition_overdue():
    """Overdue homework → max pressure."""
    state = {
        "homework": {
            "hw-1": {"title": "过期作业", "status": "pending", "deadline": "2020-01-01T00:00:00Z"},
        }
    }
    cog = compute_cognition(
        state,
        {},
        as_of=datetime(2026, 6, 13, tzinfo=timezone.utc),
    )
    assert cog["deadline_pressure"] == 1.0
    print("✓ cognition: overdue → deadline_pressure = 1.0")


def test_cognition_deterministic():
    """Same input → same output."""
    state = {
        "homework": {
            "hw-1": {"title": "A", "status": "pending", "deadline": "2026-06-10T23:59:00Z"},
            "hw-2": {"title": "B", "status": "pending", "deadline": "2026-06-12T23:59:00Z"},
        }
    }
    proj = {"busy_density": 0.5, "weekly_load": 0.6, "daily_capacity": 8.0}

    c1 = compute_cognition(state, proj)
    c2 = compute_cognition(state, proj)
    assert c1 == c2
    print("✓ cognition: deterministic")


def test_cognition_deadline_beyond_10_days_not_pressure():
    deadline = (datetime.now(timezone.utc) + timedelta(days=11)).isoformat()
    state = {
        "homework": {
            "hw-1": {"title": "远期作业", "status": "pending", "deadline": deadline},
        }
    }
    cog = compute_cognition(state, {})
    assert cog["deadline_pressure"] == 0.0


def test_cognition_deadline_within_24h_super_urgent():
    now = datetime.now(timezone.utc)
    deadline = (now + timedelta(hours=23)).isoformat()
    state = {
        "homework": {
            "hw-1": {"title": "近期开题", "status": "pending", "deadline": deadline},
        }
    }
    cog = compute_cognition(state, {}, as_of=now)
    assert cog["deadline_pressure"] == 1.0


def test_cognition_clustered_ddls():
    """Multiple deadlines in 24h → bonus pressure."""
    now = datetime.now(timezone.utc)
    soon = (now + timedelta(hours=12)).isoformat()
    soon2 = (now + timedelta(hours=18)).isoformat()

    state = {
        "homework": {
            "hw-1": {"title": "A", "status": "pending", "deadline": soon},
            "hw-2": {"title": "B", "status": "pending", "deadline": soon2},
            "hw-3": {"title": "C", "status": "pending", "deadline": soon},
        }
    }
    cog = compute_cognition(state, {}, as_of=now)
    assert cog["deadline_pressure"] > 0.85  # close to 1.0 due to clustering
    print(f"✓ cognition: 3 DDLs in 24h → pressure={cog['deadline_pressure']:.3f}")


# ── Recommendations ────────────────────────────────────────────────────

def test_recommendation_capacity_overload():
    cog = {
        "stress_projection": 0.7,
        "deadline_pressure": 0.6,
        "workload_overload": 0.5,
        "fatigue_risk": 0.3,
        "recovery_window": 3.0,
        "next_48h_capacity": 1.3,
        "pending_total": 4,
    }
    recs = _generate_recommendations(cog)
    assert len(recs) >= 1
    assert "capacity overloaded" in recs[0].lower() or "tight" in recs[0].lower()
    print(f"✓ recommendation: capacity overload → {len(recs)} recs")


def test_recommendation_recovery_low():
    cog = {
        "stress_projection": 0.6,
        "deadline_pressure": 0.3,
        "workload_overload": 0.4,
        "fatigue_risk": 0.2,
        "recovery_window": 1.0,
        "next_48h_capacity": 0.5,
        "pending_total": 1,
    }
    recs = _generate_recommendations(cog)
    assert any("recovery" in r.lower() or "break" in r.lower() for r in recs)
    print("✓ recommendation: low recovery → avoid new tasks")


def test_recommendation_all_clear():
    cog = {
        "stress_projection": 0.1,
        "deadline_pressure": 0.0,
        "workload_overload": 0.0,
        "fatigue_risk": 0.0,
        "recovery_window": 12.0,
        "next_48h_capacity": 0.1,
        "pending_total": 0,
    }
    recs = _generate_recommendations(cog)
    assert any("low" in r.lower() or "deep work" in r.lower() for r in recs)
    print("✓ recommendation: all clear → deep work suggestion")


def test_recommendation_no_duplicates():
    """Same input twice → same output (no internal mutation)."""
    cog = {
        "stress_projection": 0.5,
        "deadline_pressure": 0.4,
        "workload_overload": 0.3,
        "fatigue_risk": 0.6,
        "recovery_window": 3.0,
        "next_48h_capacity": 0.8,
        "pending_total": 3,
    }
    r1 = _generate_recommendations(cog)
    r2 = _generate_recommendations(cog)
    assert r1 == r2
    print("✓ recommendation: deterministic (no mutation)")


# ── StateEngine integration ────────────────────────────────────────────

async def test_state_engine_cognition():
    engine = StateEngine()

    # Add homework
    await engine.apply(Event(
        EventType.HOMEWORK_NEW, "hw-1", AggregateType.HOMEWORK,
        payload={"title": "A", "course": "Math", "status": "pending", "deadline": "2026-06-01T23:59:00Z"},
    ))
    await engine.apply(Event(
        EventType.HOMEWORK_NEW, "hw-2", AggregateType.HOMEWORK,
        payload={"title": "B", "course": "English", "status": "pending", "deadline": "2026-05-30T23:59:00Z"},
    ))

    derived = engine.get_all_derived()
    cog = derived.get("cognition", {})
    assert cog["pending_total"] == 2
    assert "stress_projection" in cog
    print(f"✓ StateEngine cognition: {cog['pending_total']} pending, stress={cog['stress_projection']:.2f}")


async def test_cognition_replay():
    events = [
        Event(EventType.HOMEWORK_NEW, "hw-1", AggregateType.HOMEWORK,
              payload={"title": "A", "status": "pending", "deadline": "2026-06-01T23:59:00Z"}),
        Event(EventType.HOMEWORK_NEW, "hw-2", AggregateType.HOMEWORK,
              payload={"title": "B", "status": "pending", "deadline": "2026-05-28T23:59:00Z"}),
    ]

    e1 = StateEngine()
    for ev in events:
        await e1.apply(ev)
    c1 = e1.get_all_derived()["cognition"]

    e2 = StateEngine()
    await e2.rebuild_from_events(events)
    c2 = e2.get_all_derived()["cognition"]

    assert c1 == c2
    print("✓ cognition replay: deterministic")


if __name__ == "__main__":
    print("=== Cognition ===")
    test_cognition_empty()
    test_cognition_with_homework()
    test_cognition_overdue()
    test_cognition_deterministic()
    test_cognition_clustered_ddls()

    print("\n=== Recommendations ===")
    test_recommendation_capacity_overload()
    test_recommendation_recovery_low()
    test_recommendation_all_clear()
    test_recommendation_no_duplicates()

    print("\n=== StateEngine ===")
    asyncio.run(test_state_engine_cognition())
    asyncio.run(test_cognition_replay())

    print("\nProjection Intelligence: all checks passed")
