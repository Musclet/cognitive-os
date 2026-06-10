"""Test: Reflection-guided Adaptation — trends, params, replay."""

import asyncio
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, ".")

from src.core.events import Event, EventType, AggregateType
from src.core.state_engine import StateEngine
from src.derived_state.reflection import compute_reflection
from src.derived_state.adaptation_params import compute_adapted_params, get_default_params, get_param_bounds
from src.derived_state.adaptive_planning import compute_adaptive_planning


# ── Helpers ────────────────────────────────────────────────────────────

def _make_feedback(actions_outcomes, base_ts=None):
    """Build feedback log entries with timestamps."""
    if base_ts is None:
        base_ts = datetime(2026, 5, 20, 10, 0, 0, tzinfo=timezone.utc)
    log = []
    for i, (action, outcome, fatigue) in enumerate(actions_outcomes):
        ts = base_ts + timedelta(days=i)
        log.append({
            "action": action,
            "outcome": outcome,
            "timestamp": ts.isoformat(),
            "window_type": "standard",
            "cognition_at_time": {"fatigue_risk": fatigue},
        })
    return {"behavior": {"current": {"feedback_log": log}}}


# ── Reflection: insufficient data ──────────────────────────────────────

def test_reflection_insufficient():
    ref = compute_reflection({})
    assert ref["weekly_consistency_trend"] == "insufficient_data"
    assert ref["sample_count"] == 0
    print("\u2713 reflection: empty \u2192 insufficient")

    state = _make_feedback([("accepted", "completed", 0.2)] * 3)
    ref2 = compute_reflection(state)
    assert ref2["sample_count"] == 3
    assert ref2["weekly_consistency_trend"] == "insufficient_data"
    print("\u2713 reflection: 3 samples \u2192 insufficient")


# ── Reflection: stable trend ───────────────────────────────────────────

def test_reflection_stable():
    fb = [("accepted", "completed", 0.3)] * 6
    state = _make_feedback(fb)
    ref = compute_reflection(state)
    assert ref["weekly_consistency_trend"] == "stable"
    assert ref["deep_work_trend"] == "stable"
    assert ref["planning_effectiveness"] == 1.0
    print(f"  trend: {ref['weekly_consistency_trend']}, eff={ref['planning_effectiveness']:.2f}")


# ── Reflection: declining trend ────────────────────────────────────────

def test_reflection_declining():
    fb = [
        ("accepted", "completed", 0.2),
        ("accepted", "completed", 0.2),
        ("accepted", "completed", 0.2),
        ("skipped", "abandoned", 0.2),
        ("skipped", "abandoned", 0.2),
        ("skipped", "abandoned", 0.2),
        ("skipped", "abandoned", 0.2),
    ]
    state = _make_feedback(fb)
    ref = compute_reflection(state)
    # 3 completed / 7 total = 0.429
    assert ref["planning_effectiveness"] < 0.5
    # First half (4): 3 acc+compl, 1 skip. Second half (3): all skip. => declining
    assert ref["weekly_consistency_trend"] == "declining"
    print(f"  trend: {ref['weekly_consistency_trend']}, eff={ref['planning_effectiveness']:.2f}")


# ── Reflection: improving trend ────────────────────────────────────────

def test_reflection_improving():
    fb = [
        ("skipped", "abandoned", 0.2),
        ("skipped", "abandoned", 0.2),
        ("delayed", "completed", 0.2),
        ("accepted", "completed", 0.2),
        ("accepted", "completed", 0.2),
        ("accepted", "completed", 0.2),
    ]
    state = _make_feedback(fb)
    ref = compute_reflection(state)
    assert ref["weekly_consistency_trend"] == "improving"
    print(f"  trend: {ref['weekly_consistency_trend']}")


# ── Reflection: deterministic ──────────────────────────────────────────

def test_reflection_deterministic():
    fb = [
        ("accepted", "completed", 0.3),
        ("skipped", "abandoned", 0.8),
        ("accepted", "completed", 0.2),
        ("delayed", "completed", 0.5),
        ("accepted", "completed", 0.1),
    ]
    state = _make_feedback(fb)
    r1 = compute_reflection(state)
    r2 = compute_reflection(state)
    assert r1 == r2
    print("  deterministic")


# ── Adaptation params: defaults ─────────────────────────────────────────

def test_adaptation_defaults():
    params = compute_adapted_params({}, {})
    assert params["params"] == get_default_params()
    assert "Need" in params["reasons"][0]
    print("\u2713 adaptation: insufficient data \u2192 defaults")


# ── Adaptation params: deep work threshold ─────────────────────────────

def test_adaptation_dw_threshold_up():
    bh = {
        "total_recommendations": 8,
        "deep_work_success_rate": 0.3,
        "fatigue_compliance_drop": 0.1,
        "delay_tendency": 0.2,
        "execution_consistency": 0.5,
    }
    ref = {
        "deep_work_trend": "declining",
        "fatigue_trend": "stable",
        "behavior_drift": 0.1,
    }
    adp = compute_adapted_params(bh, ref)
    assert adp["params"]["deep_work_threshold"] > get_default_params()["deep_work_threshold"]
    print(f"  dw_threshold: {get_default_params()['deep_work_threshold']} \u2192 {adp['params']['deep_work_threshold']}")


def test_adaptation_dw_threshold_down():
    bh = {
        "total_recommendations": 8,
        "deep_work_success_rate": 0.75,
        "fatigue_compliance_drop": 0.1,
        "delay_tendency": 0.1,
        "execution_consistency": 0.8,
    }
    ref = {
        "deep_work_trend": "improving",
        "fatigue_trend": "stable",
        "behavior_drift": 0.05,
    }
    adp = compute_adapted_params(bh, ref)
    assert adp["params"]["deep_work_threshold"] < get_default_params()["deep_work_threshold"]
    print(f"  dw_threshold: {get_default_params()['deep_work_threshold']} \u2192 {adp['params']['deep_work_threshold']}")


# ── Adaptation params: fatigue penalty ─────────────────────────────────

def test_adaptation_fatigue_penalty_up():
    bh = {
        "total_recommendations": 8,
        "deep_work_success_rate": 0.5,
        "fatigue_compliance_drop": 0.6,
        "delay_tendency": 0.2,
        "execution_consistency": 0.5,
    }
    ref = {"deep_work_trend": "stable", "fatigue_trend": "stable", "behavior_drift": 0.1}
    adp = compute_adapted_params(bh, ref)
    assert adp["params"]["fatigue_penalty"] > get_default_params()["fatigue_penalty"]
    print(f"  fatigue_penalty: {get_default_params()['fatigue_penalty']} \u2192 {adp['params']['fatigue_penalty']}")


# ── Adaptation: bounded ────────────────────────────────────────────────

def test_adaptation_bounded():
    """Parameters never exceed safe bounds."""
    bh = {
        "total_recommendations": 20,
        "deep_work_success_rate": 0.01,  # Extremely low
        "fatigue_compliance_drop": 0.99,
        "delay_tendency": 0.99,
        "execution_consistency": 0.01,
    }
    ref = {
        "deep_work_trend": "declining",
        "fatigue_trend": "declining",
        "behavior_drift": 0.5,
    }
    adp = compute_adapted_params(bh, ref)
    bounds = get_param_bounds()
    for name, val in adp["params"].items():
        lo, hi = bounds[name]
        assert lo <= val <= hi, f"{name}: {val} outside [{lo}, {hi}]"
    print(f"  all params within bounds")
    # Single-step adaptation from default (0.5) with dw=0.01 + declining
    assert adp["params"]["deep_work_threshold"] >= 0.55
    print(f"  dw_threshold capped at {adp['params']['deep_work_threshold']:.2f} (max 0.8)")


# ── Adaptation: deterministic ──────────────────────────────────────────

def test_adaptation_deterministic():
    bh = {
        "total_recommendations": 8,
        "deep_work_success_rate": 0.4,
        "fatigue_compliance_drop": 0.5,
        "delay_tendency": 0.3,
        "execution_consistency": 0.5,
    }
    ref = {
        "deep_work_trend": "declining",
        "fatigue_trend": "declining",
        "behavior_drift": 0.3,
    }
    a1 = compute_adapted_params(bh, ref)
    a2 = compute_adapted_params(bh, ref)
    assert a1 == a2
    print("  deterministic")


# ── Adaptive_planning with adapted params ──────────────────────────────

def test_adaptive_with_params():
    """Adaptive planning uses adapted thresholds."""
    bh = {
        "total_recommendations": 8,
        "execution_consistency": 0.6,
        "delay_tendency": 0.2,
        "deep_work_success_rate": 0.45,
        "fatigue_compliance_drop": 0.1,
        "planning_reliability": 0.5,
    }
    cog = {"stress_projection": 0.3, "fatigue_risk": 0.2}

    # With default threshold (0.5) → dw=0.45 < 0.5 → resistant
    ad1 = compute_adaptive_planning(bh, cog, None)

    # With lowered threshold (0.3) → dw=0.45 > 0.3 → NOT resistant
    lowered_params = {"params": {"deep_work_threshold": 0.3, "fatigue_penalty": 1.0}}
    ad2 = compute_adaptive_planning(bh, cog, lowered_params)

    # With default thresh, we should see resistant pattern
    assert "deep_work_resistant" in ad1["patterns_detected"]
    # With lowered thresh, resistant should NOT trigger
    assert "deep_work_resistant" not in ad2["patterns_detected"]
    print(f"  default threshold: {ad1['patterns_detected']}")
    print(f"  lowered threshold: {ad2['patterns_detected']}")


# ── StateEngine integration ────────────────────────────────────────────

async def test_se_reflection():
    engine = StateEngine()

    # Add many feedback entries to get reflection
    for i in range(6):
        await engine.apply(Event(
            EventType.PLANNING_RECOMMENDATION_ACCEPTED, "u1", AggregateType.USER,
            payload={"task_id": f"hw-{i}"},
        ))
        await engine.apply(Event(
            EventType.PLANNING_TASK_COMPLETED, "u1", AggregateType.USER,
            payload={"task_id": f"hw-{i}"},
        ))

    derived = engine.get_all_derived()
    assert "reflection" in derived
    assert "adaptation_params" in derived
    ref = derived["reflection"]
    assert ref["sample_count"] >= 5
    print(f"  reflection: {ref['weekly_consistency_trend']}, samples={ref['sample_count']}")
    print(f"  adaptation: {derived['adaptation_params']['reasons'][:1]}")


async def test_se_replay_deterministic():
    events = [
        Event(EventType.HOMEWORK_NEW, "hw-1", AggregateType.HOMEWORK,
              payload={"title": "A", "status": "pending"}),
    ]
    for i in range(7):
        events.append(Event(
            EventType.PLANNING_RECOMMENDATION_ACCEPTED, "u1", AggregateType.USER,
            payload={"task_id": f"hw-{i}"},
        ))
        events.append(Event(
            EventType.PLANNING_TASK_COMPLETED, "u1", AggregateType.USER,
            payload={"task_id": f"hw-{i}"},
        ))

    e1 = StateEngine()
    for ev in events:
        await e1.apply(ev)
    d1 = e1.get_all_derived()

    e2 = StateEngine()
    await e2.rebuild_from_events(events)
    d2 = e2.get_all_derived()

    assert d1["reflection"] == d2["reflection"]
    assert d1["adaptation_params"] == d2["adaptation_params"]
    assert d1["adaptive_planning"] == d2["adaptive_planning"]
    print("  replay: reflection + adaptation + adaptive_planning all deterministic")


if __name__ == "__main__":
    print("=== Reflection ===")
    test_reflection_insufficient()
    test_reflection_stable()
    test_reflection_declining()
    test_reflection_improving()
    test_reflection_deterministic()

    print("\n=== Adaptation ===")
    test_adaptation_defaults()
    test_adaptation_dw_threshold_up()
    test_adaptation_dw_threshold_down()
    test_adaptation_fatigue_penalty_up()
    test_adaptation_bounded()
    test_adaptation_deterministic()

    print("\n=== Adaptive + Params ===")
    test_adaptive_with_params()

    print("\n=== StateEngine ===")
    asyncio.run(test_se_reflection())
    asyncio.run(test_se_replay_deterministic())

    print("\nReflection-guided Adaptation: all checks passed")
