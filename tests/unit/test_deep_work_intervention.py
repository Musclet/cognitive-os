"""Test: Deep work reminder intervention."""

from __future__ import annotations

import sys
from datetime import datetime, timezone

sys.path.insert(0, ".")

import pytest

from intervention.deep_work import evaluate_deep_work


def test_deep_work_triggers_with_focus_window():
    """Deep work intervention triggers when focus window available and no recent deep work."""
    derived = {
        "planning": {
            "focus_windows": [
                {"time": "10:00-12:00", "type": "deep_work", "quality": "high", "duration_minutes": 120},
            ],
            "recommended_windows": [
                {"time": "10:00-12:00", "type": "deep_work", "label": "深度工作"},
            ],
        },
        "cognition": {
            "fatigue_risk": 0.3,
        },
        "adaptive_planning": {
            "recommended_intensity": "normal",
        },
    }
    runtime = {
        "behavior": {"feedback_log": []},
        "temporal": {"context": {}},
    }

    result = evaluate_deep_work(derived, runtime)
    assert result is not None
    assert result.intervention_type == "deep_work_reminder"
    assert "深度工作" in result.message or "专注" in result.message


def test_deep_work_suppressed_by_fatigue():
    """Deep work suppressed when fatigue is too high."""
    derived = {
        "planning": {
            "focus_windows": [{"time": "10:00-12:00", "type": "deep_work", "quality": "high", "duration_minutes": 120}],
            "recommended_windows": [],
        },
        "cognition": {"fatigue_risk": 0.8},
        "adaptive_planning": {"recommended_intensity": "normal"},
    }
    runtime = {
        "behavior": {"feedback_log": []},
        "temporal": {"context": {}},
    }

    result = evaluate_deep_work(derived, runtime)
    assert result is None


def test_deep_work_suppressed_by_recent_completion():
    """Deep work suppressed when recent deep work was completed (<4h ago)."""
    from datetime import datetime, timedelta, timezone

    recent_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    derived = {
        "planning": {
            "focus_windows": [{"time": "10:00-12:00", "type": "deep_work", "quality": "high", "duration_minutes": 120}],
            "recommended_windows": [],
        },
        "cognition": {"fatigue_risk": 0.3},
        "adaptive_planning": {"recommended_intensity": "normal"},
    }
    runtime = {
        "behavior": {
            "feedback_log": [
                {"outcome": "completed", "outcome_timestamp": recent_ts},
            ],
        },
        "temporal": {"context": {}},
    }

    result = evaluate_deep_work(derived, runtime)
    assert result is None


def test_deep_work_suppressed_by_social_plan():
    """Deep work suppressed when user has social plans tonight."""
    derived = {
        "planning": {
            "focus_windows": [{"time": "10:00-12:00", "type": "deep_work", "quality": "high", "duration_minutes": 120}],
            "recommended_windows": [],
        },
        "cognition": {"fatigue_risk": 0.3},
        "adaptive_planning": {"recommended_intensity": "normal"},
    }
    runtime = {
        "behavior": {"feedback_log": []},
        "temporal": {"context": {"social_block_tonight": True}},
    }

    result = evaluate_deep_work(derived, runtime)
    assert result is None


def test_deep_work_no_window():
    """Deep work does not trigger without focus/recommended windows."""
    derived = {
        "planning": {"focus_windows": [], "recommended_windows": []},
        "cognition": {"fatigue_risk": 0.3},
        "adaptive_planning": {"recommended_intensity": "normal"},
    }
    runtime = {
        "behavior": {"feedback_log": []},
        "temporal": {"context": {}},
    }

    result = evaluate_deep_work(derived, runtime)
    assert result is None


def test_deep_work_light_intensity():
    """Deep work suppressed when recommended intensity is 'light'."""
    derived = {
        "planning": {
            "focus_windows": [{"time": "10:00-12:00", "type": "deep_work", "quality": "high", "duration_minutes": 120}],
            "recommended_windows": [],
        },
        "cognition": {"fatigue_risk": 0.3},
        "adaptive_planning": {"recommended_intensity": "light"},
    }
    runtime = {
        "behavior": {"feedback_log": []},
        "temporal": {"context": {}},
    }

    result = evaluate_deep_work(derived, runtime)
    assert result is None
