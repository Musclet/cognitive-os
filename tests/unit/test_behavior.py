"""Test: Behavioral Feedback — action tracking, behavior metrics, replay."""

import asyncio
import sys
from datetime import datetime, timezone

sys.path.insert(0, ".")

from src.core.events import Event, EventType, AggregateType
from src.core.state_engine import StateEngine
from src.derived_state.behavior import compute_behavior
from src.domain.behavior.handlers import handle_user_feedback


def _make_behavior_state(feedbacks):
    """Helper: wrap feedback list in correct state nesting."""
    return {"behavior": {"current": {"feedback_log": feedbacks}}}


# ── Behavior computation: empty ────────────────────────────────────────

def test_behavior_empty():
    bh = compute_behavior({})
    assert bh["execution_consistency"] == 1.0
    assert bh["delay_tendency"] == 0.0
    assert bh["total_recommendations"] == 0
    print("\u2713 behavior: empty \u2192 defaults")


def test_behavior_with_feedback():
    fb = [
        {"action": "accepted", "outcome": "completed", "cognition_at_time": {"fatigue_risk": 0.2}},
        {"action": "skipped",  "outcome": "abandoned", "cognition_at_time": {"fatigue_risk": 0.8}},
        {"action": "accepted", "outcome": "completed", "cognition_at_time": {"fatigue_risk": 0.3}},
        {"action": "delayed",  "outcome": "completed", "cognition_at_time": {"fatigue_risk": 0.5}},
        {"action": "accepted", "outcome": "completed", "cognition_at_time": {"fatigue_risk": 0.1}},
    ]
    bh = compute_behavior(_make_behavior_state(fb))
    assert bh["total_recommendations"] == 5
    assert bh["accepted_count"] == 3
    assert bh["skipped_count"] == 1
    assert bh["delayed_count"] == 1
    assert bh["completed_count"] == 4
    assert 0.5 < bh["execution_consistency"] < 0.8
    assert bh["delay_tendency"] == 0.25
    print(f"  consistency={bh['execution_consistency']:.3f} delay={bh['delay_tendency']:.3f}")


def test_fatigue_compliance_drop():
    fb = [
        {"action": "accepted", "cognition_at_time": {"fatigue_risk": 0.1}},
        {"action": "accepted", "cognition_at_time": {"fatigue_risk": 0.2}},
        {"action": "skipped",  "cognition_at_time": {"fatigue_risk": 0.8}},
        {"action": "skipped",  "cognition_at_time": {"fatigue_risk": 0.9}},
    ]
    bh = compute_behavior(_make_behavior_state(fb))
    assert bh["fatigue_compliance_drop"] > 0.5
    print(f"  fatigue drop = {bh['fatigue_compliance_drop']:.3f}")


def test_deep_work_success():
    fb = [
        {"action": "accepted", "outcome": "completed", "window_type": "deep_work"},
        {"action": "accepted", "outcome": "completed", "window_type": "deep_work"},
        {"action": "accepted", "outcome": "abandoned", "window_type": "deep_work"},
        {"action": "accepted", "outcome": "completed", "window_type": "standard"},
    ]
    bh = compute_behavior(_make_behavior_state(fb))
    assert abs(bh["deep_work_success_rate"] - 2/3) < 0.001
    print(f"  deep work rate = {bh['deep_work_success_rate']:.3f}")


def test_behavior_deterministic():
    fb = [
        {"action": "accepted", "outcome": "completed"},
        {"action": "skipped",  "outcome": "abandoned"},
    ]
    s = _make_behavior_state(fb)
    assert compute_behavior(s) == compute_behavior(s)
    print("  deterministic")


# ── Handler ────────────────────────────────────────────────────────────

async def test_done():
    ev = Event(EventType.USER_COMMAND_RECEIVED, "u1", AggregateType.USER,
               payload={"command": "task_done", "params": {"args": "hw-math"}})
    r = await handle_user_feedback(ev)
    assert len(r) == 1 and r[0].event_type == EventType.PLANNING_TASK_COMPLETED
    print("  /done \u2192 task.completed")

async def test_skip():
    ev = Event(EventType.USER_COMMAND_RECEIVED, "u1", AggregateType.USER,
               payload={"command": "task_skip", "params": {"args": "hw-en"}})
    r = await handle_user_feedback(ev)
    assert len(r) == 1 and r[0].event_type == EventType.PLANNING_RECOMMENDATION_SKIPPED
    print("  /skip \u2192 recommendation.skipped")

async def test_delay():
    ev = Event(EventType.USER_COMMAND_RECEIVED, "u1", AggregateType.USER,
               payload={"command": "task_delay", "params": {"args": ""}})
    r = await handle_user_feedback(ev)
    assert len(r) == 1 and r[0].event_type == EventType.PLANNING_RECOMMENDATION_DELAYED
    print("  /delay \u2192 recommendation.delayed")


# ── StateEngine ────────────────────────────────────────────────────────

async def test_se_tracking():
    engine = StateEngine()
    await engine.apply(Event(EventType.PLANNING_RECOMMENDATION_ACCEPTED, "u1", AggregateType.USER, payload={"task_id": "hw-1"}))
    await engine.apply(Event(EventType.PLANNING_TASK_COMPLETED, "u1", AggregateType.USER, payload={"task_id": "hw-1"}))
    await engine.apply(Event(EventType.PLANNING_RECOMMENDATION_SKIPPED, "u1", AggregateType.USER, payload={"task_id": "hw-2"}))
    bh = engine.get_all_derived()["behavior"]
    assert bh["total_recommendations"] == 2
    assert bh["accepted_count"] == 1
    assert bh["completed_count"] == 1
    print(f"  {bh['total_recommendations']} recs, {bh['accepted_count']} accepted")


async def test_se_replay():
    events = [
        Event(EventType.PLANNING_RECOMMENDATION_ACCEPTED, "u1", AggregateType.USER, payload={"task_id": "hw-1"}),
        Event(EventType.PLANNING_RECOMMENDATION_SKIPPED, "u1", AggregateType.USER, payload={"task_id": "hw-2"}),
        Event(EventType.PLANNING_TASK_COMPLETED, "u1", AggregateType.USER, payload={"task_id": "hw-1"}),
    ]
    e1 = StateEngine()
    for ev in events:
        await e1.apply(ev)
    b1 = e1.get_all_derived()["behavior"]

    e2 = StateEngine()
    await e2.rebuild_from_events(events)
    b2 = e2.get_all_derived()["behavior"]
    assert b1 == b2
    print("  replay deterministic")


async def test_idempotent():
    engine = StateEngine()
    ev = Event(EventType.PLANNING_RECOMMENDATION_ACCEPTED, "u1", AggregateType.USER, payload={"task_id": "hw-1"})
    await engine.apply(ev)
    await engine.apply(ev)  # duplicate
    bh = engine.get_all_derived()["behavior"]
    assert bh["total_recommendations"] == 1
    print("  idempotent (no double-count)")


if __name__ == "__main__":
    print("=== Behavior ===")
    test_behavior_empty()
    test_behavior_with_feedback()
    test_fatigue_compliance_drop()
    test_deep_work_success()
    test_behavior_deterministic()

    print("\n=== Handler ===")
    asyncio.run(test_done())
    asyncio.run(test_skip())
    asyncio.run(test_delay())

    print("\n=== StateEngine ===")
    asyncio.run(test_se_tracking())
    asyncio.run(test_se_replay())
    asyncio.run(test_idempotent())

    print("\nBehavioral Feedback: all checks passed")
