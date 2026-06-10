"""Test: plan confidence score computation."""
from __future__ import annotations

import sys
from unittest.mock import MagicMock

sys.path.insert(0, ".")

from src.interface.telegram.bot import _compute_plan_confidence


def _make_state_engine(
    deadline_pressure: float = 0.0,
    fatigue_risk: float = 0.0,
    mood: int | None = None,
    target_minutes: int = 0,
    free_slots: list | None = None,
    busy_windows: list | None = None,
    recommended_windows: list | None = None,
) -> MagicMock:
    """Helper to build a StateEngine mock with deterministic derived state."""
    state = MagicMock()

    derived = {
        "cognition": {
            "deadline_pressure": deadline_pressure,
            "fatigue_risk": fatigue_risk,
            "subjective": {"current_mood": mood},
        },
        "planning": {
            "recommended_windows": recommended_windows or [],
        },
        "behavior": {},
    }
    state.get_all_derived = lambda: derived

    art_today = {
        "plan": {
            "target_minutes": target_minutes,
            "free_slots": free_slots or [],
        },
    }
    temporal_projection = {
        "busy_windows": busy_windows or [],
    }

    def get_view(view_type: str, view_id: str):
        if view_type == "art" and view_id == "today":
            return art_today
        if view_type == "temporal" and view_id == "projection":
            return temporal_projection
        return {}

    state.get_view = get_view
    return state


def test_confidence_high():
    """Low pressure, good mood, small art target, free windows → high."""
    state = _make_state_engine(
        deadline_pressure=0.1,
        fatigue_risk=0.1,
        mood=8,
        target_minutes=120,
        recommended_windows=[{"type": "deep_work"}, {"type": "standard"}, {"type": "quick"}],
    )
    settings = MagicMock()
    level, reason_text, reasons = _compute_plan_confidence(state, settings)
    assert level == "高"
    assert "情绪好" in reason_text or "作业压力低" in reason_text


def test_confidence_low():
    """High pressure, bad mood, large art target, no windows → low."""
    state = _make_state_engine(
        deadline_pressure=0.8,
        fatigue_risk=0.8,
        mood=2,
        target_minutes=420,
        busy_windows=[{"start": "2026-06-01T08:00:00+08:00", "end": "2026-06-01T18:00:00+08:00"}],
        recommended_windows=[],
    )
    settings = MagicMock()
    level, reason_text, reasons = _compute_plan_confidence(state, settings)
    assert level == "低"
    assert reasons  # should have several reasons


def test_confidence_medium():
    """Mixed signals → medium confidence."""
    # Score: base 50 +10 calendar empty, -10 deadline pressure, -5 mood=5 = 45 → medium
    state = _make_state_engine(
        deadline_pressure=0.5,
        fatigue_risk=0.2,
        mood=5,
        target_minutes=0,
        busy_windows=[],
        recommended_windows=[{"type": "standard"}],
    )
    settings = MagicMock()
    level, reason_text, reasons = _compute_plan_confidence(state, settings)
    assert level == "中"
    assert reason_text


def test_confidence_score_clamped():
    """Score is bounded to [0, 100] — no ValueError from extremes."""
    state = _make_state_engine(
        deadline_pressure=0,
        fatigue_risk=0,
        mood=10,
        target_minutes=60,
        recommended_windows=[{"type": "deep_work"}, {"type": "standard"}, {"type": "quick"}],
    )
    settings = MagicMock()
    level, reason_text, reasons = _compute_plan_confidence(state, settings)
    # Everything positive → high
    assert level in ("高", "中", "低")

    # Extreme negative
    state2 = _make_state_engine(
        deadline_pressure=0.9,
        fatigue_risk=0.9,
        mood=1,
        target_minutes=600,
        busy_windows=[{"start": "2026-06-01T06:00:00+08:00", "end": "2026-06-01T23:00:00+08:00"}],
        recommended_windows=[],
    )
    level2, reason_text2, reasons2 = _compute_plan_confidence(state2, settings)
    assert level2 == "低"


def test_confidence_no_mood():
    """When mood is None, scoring still works."""
    state = _make_state_engine(
        deadline_pressure=0.2,
        fatigue_risk=0.2,
        mood=None,
        target_minutes=180,
        recommended_windows=[{"type": "deep_work"}],
    )
    settings = MagicMock()
    level, reason_text, reasons = _compute_plan_confidence(state, settings)
    assert level in ("高", "中", "低")
    # Reason should NOT mention mood
    assert all("情绪" not in r for r in reasons)


def test_confidence_format_string():
    """Output format matches expected Chinese pattern."""
    state = _make_state_engine(
        deadline_pressure=0.3,
        fatigue_risk=0.2,
        mood=7,
        target_minutes=120,
        recommended_windows=[{"type": "deep_work"}],
    )
    settings = MagicMock()
    level, reason_text, reasons = _compute_plan_confidence(state, settings)
    assert level in ("低", "中", "高")
    assert isinstance(reason_text, str)
    assert isinstance(reasons, list)
    # The reason text should not be empty for this case with mood=7
    assert reason_text
