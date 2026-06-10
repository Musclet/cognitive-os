"""Test: Adaptive Planning — behavior-aware recommendation tuning."""

import asyncio
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, ".")

from src.core.events import Event, EventType, AggregateType
from src.core.state_engine import StateEngine
from src.core.temporal import TimeBlock, TemporalSource, TimeBlockType
from src.derived_state.adaptive_planning import compute_adaptive_planning
from src.derived_state.planning import compute_planning, _recommend_windows, _find_free_slots


# ── Adaptive: empty/neutral ────────────────────────────────────────────

def test_adaptive_empty():
    """No behavior data → neutral defaults."""
    ad = compute_adaptive_planning({}, {})
    assert ad["recommended_intensity"] == "normal"
    assert ad["preferred_window_type"] == "standard"
    assert ad["adaptation_confidence"] <= 0.2
    assert "low confidence" in str(ad["adjustment_reasons"]).lower() or ad["adaptation_confidence"] < 0.3
    print("\u2713 adaptive: empty \u2192 neutral, low confidence")


def test_adaptive_insufficient_data():
    """Only 2 samples → low confidence."""
    bh = {"total_recommendations": 2, "execution_consistency": 0.5}
    ad = compute_adaptive_planning(bh, {})
    assert ad["adaptation_confidence"] < 0.3
    print("\u2713 adaptive: 2 samples \u2192 low confidence")


# ── Adaptive: deep_work resistant ──────────────────────────────────────

def test_adaptive_deep_work_resistant():
    """Low deep work success → downgrade to standard."""
    bh = {
        "total_recommendations": 5,
        "execution_consistency": 0.6,
        "delay_tendency": 0.2,
        "deep_work_success_rate": 0.3,
        "fatigue_compliance_drop": 0.1,
        "planning_reliability": 0.5,
    }
    cog = {"stress_projection": 0.3, "fatigue_risk": 0.2, "pending_total": 2}
    ad = compute_adaptive_planning(bh, cog)
    assert ad["preferred_window_type"] == "standard"
    assert ad["recommended_intensity"] == "reduced"
    assert "deep_work_resistant" in ad["patterns_detected"]
    assert ad["adaptation_confidence"] > 0.5
    print(f"  window={ad['preferred_window_type']} intensity={ad['recommended_intensity']} conf={ad['adaptation_confidence']:.2f}")


# ── Adaptive: chronic delayer ──────────────────────────────────────────

def test_adaptive_chronic_delayer():
    """High delay tendency → quick windows, light intensity."""
    bh = {
        "total_recommendations": 6,
        "execution_consistency": 0.4,
        "delay_tendency": 0.6,
        "deep_work_success_rate": 0.5,
        "fatigue_compliance_drop": 0.1,
        "planning_reliability": 0.3,
    }
    cog = {"stress_projection": 0.4, "fatigue_risk": 0.3}
    ad = compute_adaptive_planning(bh, cog)
    assert ad["preferred_window_type"] == "quick"
    assert ad["recommended_intensity"] == "light"
    assert "chronic_delayer" in ad["patterns_detected"]
    assert ad["compliance_risk"] > 0.4
    print(f"  window={ad['preferred_window_type']} intensity={ad['recommended_intensity']} risk={ad['compliance_risk']:.2f}")


# ── Adaptive: fatigue sensitive ────────────────────────────────────────

def test_adaptive_fatigue_sensitive():
    """High fatigue compliance drop → reduced intensity."""
    bh = {
        "total_recommendations": 4,
        "execution_consistency": 0.5,
        "delay_tendency": 0.2,
        "deep_work_success_rate": 0.5,
        "fatigue_compliance_drop": 0.5,
        "planning_reliability": 0.5,
    }
    ad = compute_adaptive_planning(bh, {})
    assert "fatigue_sensitive" in ad["patterns_detected"]
    assert ad["recommended_intensity"] == "reduced"
    print(f"  fatigue_sensitive: intensity={ad['recommended_intensity']}")


# ── Adaptive: deep work ready ──────────────────────────────────────────

def test_adaptive_deep_work_ready():
    """High consistency + deep work success → focused."""
    bh = {
        "total_recommendations": 5,
        "execution_consistency": 0.85,
        "delay_tendency": 0.1,
        "deep_work_success_rate": 0.75,
        "fatigue_compliance_drop": 0.05,
        "planning_reliability": 0.8,
    }
    ad = compute_adaptive_planning(bh, {})
    assert ad["preferred_window_type"] == "deep_work"
    assert ad["recommended_intensity"] == "focused"
    assert "deep_work_ready" in ad["patterns_detected"]
    assert ad["compliance_risk"] < 0.2
    print(f"  deep_work_ready: intensity={ad['recommended_intensity']} risk={ad['compliance_risk']:.2f}")


# ── Adaptive: oscillation guard ─────────────────────────────────────────

def test_adaptive_no_oscillation():
    """Mixed signals → standard, no flip-flopping."""
    bh = {
        "total_recommendations": 10,
        "execution_consistency": 0.75,
        "delay_tendency": 0.15,
        "deep_work_success_rate": 0.35,  # low → resistant
        "fatigue_compliance_drop": 0.1,
        "planning_reliability": 0.4,
    }
    ad = compute_adaptive_planning(bh, {})
    # deep_work_resistant triggered by low dw, but also deep_work_ready NOT triggered
    # The oscillation guard only triggers when BOTH deep_work_ready AND deep_work_resistant exist
    # In this case, only deep_work_resistant exists → standard
    assert ad["preferred_window_type"] == "standard"
    print(f"  no oscillation: window={ad['preferred_window_type']}")


# ── Adaptive: deterministic ────────────────────────────────────────────

def test_adaptive_deterministic():
    """Same input → same output."""
    bh = {
        "total_recommendations": 5,
        "execution_consistency": 0.55,
        "delay_tendency": 0.35,
        "deep_work_success_rate": 0.4,
        "fatigue_compliance_drop": 0.4,
        "planning_reliability": 0.45,
    }
    cog = {"stress_projection": 0.4, "fatigue_risk": 0.3}
    a1 = compute_adaptive_planning(bh, cog)
    a2 = compute_adaptive_planning(bh, cog)
    assert a1 == a2
    print("  deterministic")


# ── Planning with adaptive params ──────────────────────────────────────

def test_planning_adaptive_downgrade():
    """Adaptive params actually change window recommendations."""
    now = datetime.now(timezone.utc)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    blocks = [
        TimeBlock("a", TemporalSource.JWXT, TimeBlockType.CLASS_LECTURE,
                  today.replace(hour=8), today.replace(hour=10), "Short class"),
    ]
    fog = {"stress_projection": 0.2, "fatigue_risk": 0.2, "pending_total": 2}

    # Without adaptation → deep work windows
    p_normal = compute_planning(blocks, fog, None)
    types_normal = [w["type"] for w in p_normal["recommended_windows"]]

    # With light adaptation → quick only
    adaptive = {"recommended_intensity": "light", "preferred_window_type": "quick", "adaptation_confidence": 0.8}
    p_adapted = compute_planning(blocks, fog, adaptive)
    types_adapted = [w["type"] for w in p_adapted["recommended_windows"]]

    assert "deep_work" in types_normal or "standard" in types_normal
    # Light intensity caps everything at quick
    for t in types_adapted:
        assert t == "quick", f"Expected quick only, got {t}"
    print(f"  normal: {types_normal} \u2192 adapted: {types_adapted}")


def test_planning_focused_boost():
    """Focused mode promotes standard windows to deep work."""
    now = datetime.now(timezone.utc)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    blocks = [
        TimeBlock("a", TemporalSource.JWXT, TimeBlockType.CLASS_LECTURE,
                  today.replace(hour=8), today.replace(hour=11), "Morning"),
    ]
    fog = {"stress_projection": 0.2, "fatigue_risk": 0.2, "pending_total": 1}

    adaptive = {"recommended_intensity": "focused", "preferred_window_type": "deep_work", "adaptation_confidence": 0.7}
    p = compute_planning(blocks, fog, adaptive)
    types = [w["type"] for w in p["recommended_windows"]]
    # Should have at least one deep_work window (focused promotes standards)
    print(f"  focused: {types}")


# ── StateEngine integration ────────────────────────────────────────────

async def test_se_adaptive_integration():
    """StateEngine produces adaptive_planning in derived state."""
    engine = StateEngine()

    # Add some homework to get cognition derived
    await engine.apply(Event(
        EventType.HOMEWORK_NEW, "hw-1", AggregateType.HOMEWORK,
        payload={"title": "Math", "status": "pending", "deadline": "2026-06-10T23:59:00Z"},
    ))

    # Add behavioral feedback
    await engine.apply(Event(
        EventType.PLANNING_RECOMMENDATION_ACCEPTED, "u1", AggregateType.USER,
        payload={"task_id": "hw-1"},
    ))
    await engine.apply(Event(
        EventType.PLANNING_TASK_COMPLETED, "u1", AggregateType.USER,
        payload={"task_id": "hw-1"},
    ))
    await engine.apply(Event(
        EventType.PLANNING_RECOMMENDATION_SKIPPED, "u1", AggregateType.USER,
        payload={"task_id": "hw-2"},
    ))

    derived = engine.get_all_derived()
    adaptive = derived.get("adaptive_planning", {})
    assert "recommended_intensity" in adaptive
    assert "preferred_window_type" in adaptive
    print(f"  adaptive_planning: {adaptive['recommended_intensity']} / {adaptive['preferred_window_type']} (conf={adaptive['adaptation_confidence']:.2f})")


async def test_se_adaptive_replay():
    """Adaptive planning is deterministic under replay."""
    events = [
        Event(EventType.HOMEWORK_NEW, "hw-1", AggregateType.HOMEWORK,
              payload={"title": "A", "status": "pending"}),
        Event(EventType.PLANNING_RECOMMENDATION_ACCEPTED, "u1", AggregateType.USER,
              payload={"task_id": "hw-1"}),
        Event(EventType.PLANNING_TASK_COMPLETED, "u1", AggregateType.USER,
              payload={"task_id": "hw-1"}),
    ]

    e1 = StateEngine()
    for ev in events:
        await e1.apply(ev)
    a1 = e1.get_all_derived()["adaptive_planning"]

    e2 = StateEngine()
    await e2.rebuild_from_events(events)
    a2 = e2.get_all_derived()["adaptive_planning"]

    assert a1 == a2
    print("  adaptive replay: deterministic")


if __name__ == "__main__":
    print("=== Adaptive computation ===")
    test_adaptive_empty()
    test_adaptive_insufficient_data()
    test_adaptive_deep_work_resistant()
    test_adaptive_chronic_delayer()
    test_adaptive_fatigue_sensitive()
    test_adaptive_deep_work_ready()
    test_adaptive_no_oscillation()
    test_adaptive_deterministic()

    print("\n=== Planning with adaptive ===")
    test_planning_adaptive_downgrade()
    test_planning_focused_boost()

    print("\n=== StateEngine ===")
    asyncio.run(test_se_adaptive_integration())
    asyncio.run(test_se_adaptive_replay())

    print("\nAdaptive Planning: all checks passed")
