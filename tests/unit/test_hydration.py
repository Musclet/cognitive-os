"""Hydration intervention tests."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

from intervention.hydration import evaluate_hydration


def test_workout_within_two_hours_triggers_hydration_reminder():
    now = datetime.now(timezone.utc)
    result = evaluate_hydration(
        {},
        {
            "hydration": {
                "last_drink_at": (now - timedelta(minutes=90)).isoformat(),
                "total_ml_today": 250,
            },
            "temporal": {
                "context": {
                    "workout_block_later": True,
                    "next_workout": {
                        "title": "健身",
                        "start": (now + timedelta(minutes=90)).isoformat(),
                        "end": (now + timedelta(minutes=150)).isoformat(),
                    },
                }
            },
        },
    )
    assert result is not None
    assert result.intervention_type == "workout_hydration"
    assert "健身" in result.message


def test_workout_hydration_waits_if_recently_logged():
    now = datetime.now(timezone.utc)
    result = evaluate_hydration(
        {},
        {
            "hydration": {
                "last_drink_at": (now - timedelta(minutes=20)).isoformat(),
                "total_ml_today": 500,
            },
            "temporal": {
                "context": {
                    "workout_block_later": True,
                    "next_workout": {
                        "title": "健身",
                        "start": (now + timedelta(minutes=90)).isoformat(),
                        "end": (now + timedelta(minutes=150)).isoformat(),
                    },
                }
            },
        },
    )
    assert result is None


def test_workout_hydration_triggers_without_prior_drink_log():
    now = datetime.now(timezone.utc)
    result = evaluate_hydration(
        {},
        {
            "hydration": {},
            "temporal": {
                "context": {
                    "workout_block_later": True,
                    "next_workout": {
                        "title": "健身",
                        "start": (now + timedelta(minutes=90)).isoformat(),
                        "end": (now + timedelta(minutes=150)).isoformat(),
                    },
                }
            },
        },
    )
    assert result is not None
    assert result.intervention_type == "workout_hydration"
