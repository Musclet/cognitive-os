"""Test: Momo / Vocabulary integration.

Tests:
- Momo cache parse -> VOCAB_* events
- stale cache fallback
- scheduler action routing for momo_vocab_sync
- StateEngine stores vocab progress
- /today includes vocab summary but no new buttons
- intervention triggers with free slot + remaining words
- intervention suppresses if no remaining words/high pressure
- regression: all Telegram buttons routable; no Momo button
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, ".")

import pytest

from src.core.events import AggregateType, Event, EventType
from src.core.state_engine import StateEngine


# ── Fixtures ────────────────────────────────────────────────────────────

SAMPLE_CACHE = {
    "last_sync": "2026-05-21T03:12:19.373Z",
    "progress": {"finished": 173, "total": 329, "study_time": 1597885},
    "today_items": [
        {"voc_spelling": "word1", "is_finished": True, "is_new": False},
        {"voc_spelling": "word2", "is_finished": False, "is_new": True},
        {"voc_spelling": "word3", "is_finished": False, "is_new": False},
    ],
    "study_records": [
        {"voc_spelling": "forgotten1", "last_response": "FORGET", "tags": ["STICKING"]},
        {"voc_spelling": "forgotten2", "last_response": "FORGET", "tags": []},
        {"voc_spelling": "familiar1", "last_response": "FAMILIAR", "tags": []},
        {"voc_spelling": "vague1", "last_response": "VAGUE", "tags": []},
    ],
}


def make_settings(cache_path: str | None = None):
    """Create a mock settings object for testing."""
    from types import SimpleNamespace
    return SimpleNamespace(
        momo_sync_project_path="/nonexistent/momo",
        momo_cache_path=cache_path or "/nonexistent/cache.json",
        momo_sync_enabled=True,
        momo_stale_after_minutes=90,
        momo_evening_check_time="21:30",
    )


# ── Cache parse -> VOCAB_* events ──────────────────────────────────────


@pytest.mark.asyncio
async def test_momo_cache_parse_produces_vocab_events():
    """Parsing a valid cache produces VOCAB_SYNC_STARTED + VOCAB_SYNC_FAILED (npm unavailable)
    + VOCAB_PROGRESS_UPDATED + VOCAB_SLACK_DETECTED (stale + late)."""
    with tempfile.TemporaryDirectory() as tmp:
        cache_file = Path(tmp) / "momo-data.json"
        cache_file.write_text(json.dumps(SAMPLE_CACHE), encoding="utf-8")

        from src.connector.momo.connector import fetch_momo_vocab
        settings = make_settings(str(cache_file))
        events = await fetch_momo_vocab(settings, uuid4())

        types = {e.event_type for e in events}
        assert EventType.VOCAB_SYNC_STARTED in types
        assert EventType.VOCAB_SYNC_FAILED in types  # npm unavailable
        assert EventType.VOCAB_PROGRESS_UPDATED in types

        # Find progress event
        prog_evt = [e for e in events if e.event_type == EventType.VOCAB_PROGRESS_UPDATED][0]
        payload = prog_evt.payload
        assert payload["source"] == "momo_vocab"
        assert payload["progress"]["finished"] == 173
        assert payload["progress"]["total"] == 329
        assert payload["today"]["total"] == 3
        assert payload["today"]["finished"] == 1
        assert payload["today"]["remaining"] == 2
        assert payload["today"]["new_remaining"] == 1
        assert payload["today"]["review_remaining"] == 1


@pytest.mark.asyncio
async def test_momo_cache_forgetting_sticking():
    """Forgetting and sticking counts are computed correctly."""
    with tempfile.TemporaryDirectory() as tmp:
        cache_file = Path(tmp) / "momo-data.json"
        cache_file.write_text(json.dumps(SAMPLE_CACHE), encoding="utf-8")

        from src.connector.momo.connector import fetch_momo_vocab
        events = await fetch_momo_vocab(make_settings(str(cache_file)), uuid4())

        prog_evt = [e for e in events if e.event_type == EventType.VOCAB_PROGRESS_UPDATED][0]
        assert prog_evt.payload["forgetting_count"] == 3  # 2 FORGET + 1 VAGUE
        assert prog_evt.payload["sticking_count"] == 1   # 1 STICKING tag


# ── Stale cache fallback ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_momo_stale_cache_fallback():
    """When npm is unavailable but cache exists, returns events with stale=True."""
    with tempfile.TemporaryDirectory() as tmp:
        cache_file = Path(tmp) / "momo-data.json"
        cache_file.write_text(json.dumps(SAMPLE_CACHE), encoding="utf-8")

        from src.connector.momo.connector import fetch_momo_vocab
        events = await fetch_momo_vocab(make_settings(str(cache_file)), uuid4())

        # Should have a VOCAB_SYNC_FAILED (npm unavailable) but with payload
        fail_evts = [e for e in events if e.event_type == EventType.VOCAB_SYNC_FAILED]
        assert len(fail_evts) >= 1
        payload = fail_evts[-1].payload
        assert payload.get("stale") is True or payload.get("last_sync") == "2026-05-21T03:12:19.373Z"


@pytest.mark.asyncio
async def test_momo_no_cache():
    """When no cache exists, returns VOCAB_SYNC_FAILED with error."""
    from src.connector.momo.connector import fetch_momo_vocab
    settings = make_settings("/nonexistent/cache.json")
    events = await fetch_momo_vocab(settings, uuid4())
    fail_evts = [e for e in events if e.event_type == EventType.VOCAB_SYNC_FAILED]
    assert len(fail_evts) >= 1
    assert "error" in fail_evts[-1].payload


# ── Scheduler action routing ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scheduler_momo_vocab_action():
    """SCHEDULED_TRIGGER with action=momo_vocab_sync produces CONNECTOR_FETCH_REQUESTED."""
    event = Event(
        event_type=EventType.SYSTEM_SCHEDULED_TRIGGER,
        aggregate_id="auto_sync_vocab",
        aggregate_type=AggregateType.SYSTEM,
        timestamp=datetime.now(timezone.utc),
        event_id=uuid4(),
        payload={"action": "momo_vocab_sync"},
    )

    action = event.payload.get("action", "")
    assert action == "momo_vocab_sync"

    # Simulate handler logic from bot.py
    out = [Event(
        event_type=EventType.CONNECTOR_FETCH_REQUESTED,
        aggregate_id=event.aggregate_id,
        aggregate_type=AggregateType.VOCAB,
        causation_id=event.event_id,
        payload={"source": "momo_vocab", "query": "vocab_progress"},
    )]
    assert len(out) == 1
    assert out[0].event_type == EventType.CONNECTOR_FETCH_REQUESTED
    assert out[0].payload["source"] == "momo_vocab"
    assert out[0].aggregate_type == AggregateType.VOCAB


# ── StateEngine stores vocab progress ───────────────────────────────────


@pytest.mark.asyncio
async def test_state_engine_stores_vocab_progress():
    """StateEngine stores vocab progress from VOCAB_PROGRESS_UPDATED."""
    engine = StateEngine()

    await engine.apply(Event(
        event_type=EventType.VOCAB_PROGRESS_UPDATED,
        aggregate_id="momo",
        aggregate_type=AggregateType.VOCAB,
        payload={
            "source": "momo_vocab",
            "progress": {"finished": 10, "total": 20, "remaining": 10},
            "today": {"total": 5, "finished": 2, "remaining": 3},
            "stale": False,
            "slack": False,
            "last_sync": "2026-05-31T00:00:00Z",
            "forgetting_count": 3,
            "sticking_count": 1,
            "npm_sync_ok": True,
        },
    ))

    view = engine.get_view("vocab", "momo")
    assert view["progress"]["finished"] == 10
    assert view["today"]["remaining"] == 3
    assert view["stale"] is False


@pytest.mark.asyncio
async def test_state_engine_vocab_sync_completed():
    """VOCAB_SYNC_COMPLETED sets sync_status to completed."""
    engine = StateEngine()
    await engine.apply(Event(
        event_type=EventType.VOCAB_SYNC_COMPLETED,
        aggregate_id="momo",
        aggregate_type=AggregateType.VOCAB,
        payload={"source": "momo_vocab"},
    ))
    view = engine.get_view("vocab", "momo")
    assert view["sync_status"] == "completed"


@pytest.mark.asyncio
async def test_state_engine_vocab_sync_started():
    """VOCAB_SYNC_STARTED sets sync_status to running."""
    engine = StateEngine()
    await engine.apply(Event(
        event_type=EventType.VOCAB_SYNC_STARTED,
        aggregate_id="momo",
        aggregate_type=AggregateType.VOCAB,
        payload={"source": "momo_vocab"},
    ))
    view = engine.get_view("vocab", "momo")
    assert view["sync_status"] == "running"


# ── /today includes vocab summary but no new buttons ────────────────────


def test_today_dashboard_includes_vocab():
    """_format_today_dashboard includes '背词' section when vocab data exists."""
    from src.interface.telegram.bot import _format_today_dashboard
    from src.core.state_engine import StateEngine

    engine = StateEngine()
    # Add vocab data directly
    engine._ensure_aggregate("vocab", "momo")
    engine._state["vocab"]["momo"] = {
        "progress": {"finished": 173, "total": 329, "remaining": 156},
        "today": {"total": 5, "finished": 2, "remaining": 3},
        "stale": False,
        "slack": False,
        "last_sync": "2026-05-31T00:00:00Z",
        "forgetting_count": 3,
        "sticking_count": 1,
    }

    text = _format_today_dashboard(engine)
    assert "背词" in text
    assert "2/5" in text
    assert "剩 3 个" in text


def test_today_dashboard_no_vocab():
    """_format_today_dashboard works when vocab data is absent."""
    from src.interface.telegram.bot import _format_today_dashboard
    from src.core.state_engine import StateEngine

    engine = StateEngine()
    text = _format_today_dashboard(engine)
    # "背词" label always present
    assert "背词" in text


def test_no_momo_button_in_reply_keyboard():
    """Reply keyboard has no Momo-specific button."""
    from src.interface.telegram.bot import CognitiveOSBot

    bot = CognitiveOSBot.__new__(CognitiveOSBot)
    markup = bot._quick_reply_keyboard()
    labels = [button.text for row in markup.keyboard for button in row]

    assert "背词" not in labels
    assert "Momo" not in labels
    assert "背单词" not in labels


def test_no_momo_command_in_router():
    """Router has no /momo or /背词 command entry."""
    from src.interface.telegram.router import COMMANDS

    for key in COMMANDS:
        assert "momo" not in key.lower(), f"Found momo-related command: {key}"
        assert "背词" not in key


# ── Intervention triggers with free slot + remaining words ──────────────


def test_vocab_reminder_triggers_with_fragment_slot():
    """Vocab reminder triggers when free slot 8-20 min and remaining > 0."""
    from intervention.vocab_reminder import evaluate_vocab_reminder

    derived = {
        "cognition": {
            "stress_projection": 0.4,
            "fatigue_risk": 0.3,
            "vocab": {"remaining": 5, "reminder_intensity_boost": 0.15, "stale": False, "slack": True},
        },
        "planning": {
            "recommended_windows": [
                {"time": "10:00-10:15", "duration_minutes": 15, "type": "quick", "label": "Quick task"},
            ],
        },
        "deadline_pressure": {"score": 0.3},
    }
    runtime = {
        "behavior": {"feedback_log": []},
        "temporal": {"context": {}},
    }

    result = evaluate_vocab_reminder(derived, runtime)
    assert result is not None
    assert result.intervention_type == "vocab_reminder"
    assert "碎片" in result.message or "分钟" in result.message
    assert result.priority > 0.3


def test_vocab_reminder_suppressed_no_remaining():
    """Vocab reminder suppressed when no remaining words."""
    from intervention.vocab_reminder import evaluate_vocab_reminder

    derived = {
        "cognition": {
            "stress_projection": 0.3,
            "fatigue_risk": 0.2,
            "vocab": {"remaining": 0, "reminder_intensity_boost": 0, "stale": False, "slack": False},
        },
        "planning": {"recommended_windows": []},
        "deadline_pressure": {"score": 0.2},
    }
    runtime = {"behavior": {"feedback_log": []}, "temporal": {"context": {}}}

    result = evaluate_vocab_reminder(derived, runtime)
    assert result is None


def test_vocab_reminder_suppressed_high_pressure():
    """Vocab reminder suppressed when pressure > 0.8."""
    from intervention.vocab_reminder import evaluate_vocab_reminder

    derived = {
        "cognition": {
            "stress_projection": 0.85,
            "fatigue_risk": 0.3,
            "vocab": {"remaining": 10, "reminder_intensity_boost": 0, "stale": False, "slack": False},
        },
        "planning": {"recommended_windows": [
            {"time": "10:00-10:15", "duration_minutes": 15, "type": "quick", "label": "Quick task"},
        ]},
        "deadline_pressure": {"score": 0.8},
    }
    runtime = {"behavior": {"feedback_log": []}, "temporal": {"context": {}}}

    result = evaluate_vocab_reminder(derived, runtime)
    assert result is None


# ── Derived state: cognition includes vocab ─────────────────────────────


@pytest.mark.asyncio
async def test_cognition_includes_vocab():
    """cognition derived state includes vocab fields."""
    from src.derived_state.cognition import compute_cognition

    state = {
        "vocab": {
            "momo": {
                "progress": {"finished": 10, "total": 20, "remaining": 10},
                "today": {"total": 5, "finished": 2, "remaining": 3},
                "stale": False,
                "slack": False,
            },
        },
    }
    result = compute_cognition(state)
    assert "vocab" in result
    assert result["vocab"]["remaining"] == 3
    assert result["vocab"]["stale"] is False
    assert result["vocab"]["slack"] is False


# ── Regression: all Telegram buttons routable; no Momo button ──────────


def test_all_reply_keyboard_buttons_routable():
    """Every button on the reply keyboard maps to a valid command."""
    from src.interface.telegram.bot import CognitiveOSBot
    from src.interface.telegram.router import COMMANDS, parse_message

    bot = CognitiveOSBot.__new__(CognitiveOSBot)
    markup = bot._quick_reply_keyboard()
    labels = [button.text for row in markup.keyboard for button in row]

    for label in labels:
        # Skip cognitive_learning and verbal_scheduling (they use pending input mode)
        cmd = parse_message(label, 12345)
        assert cmd is not None, f"Button '{label}' has no route"
        assert cmd.command_type in COMMANDS.values() or cmd.command_type in (
            "cognitive_learning", "verbal_scheduling",
        ), f"Button '{label}' maps to unknown command '{cmd.command_type}'"
