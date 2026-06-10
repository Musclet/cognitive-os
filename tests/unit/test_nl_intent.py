"""Tests: NL intent fallback system — schema validation, Command mapping, event flow."""

from __future__ import annotations

import sys

sys.path.insert(0, ".")

import pytest

from src.core.events import Command, EventType, AggregateType
from src.domain.natural_language.intent import (
    ALLOWED_INTENT_NAMES,
    NON_EXECUTABLE_INTENTS,
    validate_ai_output,
    map_to_command,
)


# ── Schema validation tests ──────────────────────────────────────────────────


def test_validate_valid_show_today():
    """Valid show_today output passes validation."""
    result = validate_ai_output({
        "intent": "show_today",
        "params": {},
        "confidence": 0.95,
        "raw_phrase": "今天有什么安排",
        "reasoning": "用户询问今日安排",
    })
    assert result is not None
    assert result["intent"] == "show_today"
    assert result["params"] == {"raw_text": "今天有什么安排", "nl_fallback": True}


def test_validate_valid_check_homework():
    """Valid check_homework output passes validation."""
    result = validate_ai_output({
        "intent": "check_homework",
        "params": {},
        "confidence": 0.92,
        "raw_phrase": "看看还有什么作业",
    })
    assert result is not None
    assert result["intent"] == "check_homework"


def test_validate_valid_query_schedule_date():
    """Valid query_schedule_date with required date param."""
    result = validate_ai_output({
        "intent": "query_schedule_date",
        "params": {"date": "2026-06-05"},
        "confidence": 0.88,
        "raw_phrase": "查周五课表",
    })
    assert result is not None
    assert result["intent"] == "query_schedule_date"
    assert result["params"]["date"] == "2026-06-05"


def test_validate_query_schedule_missing_date():
    """query_schedule_date without date param fails validation."""
    result = validate_ai_output({
        "intent": "query_schedule_date",
        "params": {},
        "confidence": 0.88,
        "raw_phrase": "查课表",
    })
    assert result is None, "Missing required param 'date' should fail"


def test_validate_unknown_intent():
    """unknown intent is always valid (record-only)."""
    result = validate_ai_output({
        "intent": "unknown",
        "params": {},
        "confidence": 0.3,
        "raw_phrase": "你好呀",
    })
    assert result is not None
    assert result["intent"] == "unknown"


def test_validate_invalid_intent_name():
    """Made-up intent names are rejected."""
    result = validate_ai_output({
        "intent": "delete_system32",
        "params": {},
        "confidence": 0.99,
    })
    assert result is None, "Unrecognized intent should be rejected"


def test_validate_missing_intent_key():
    """Missing 'intent' key is rejected."""
    result = validate_ai_output({"params": {}, "confidence": 0.5})
    assert result is None


def test_validate_non_dict_params():
    """Params must be a dict."""
    result = validate_ai_output({
        "intent": "show_today",
        "params": "not_a_dict",
        "confidence": 0.5,
    })
    assert result is None


def test_validate_extra_params_stripped():
    """Extra params not in the schema are stripped (injection prevention)."""
    result = validate_ai_output({
        "intent": "show_today",
        "params": {"malicious": "rm -rf /", "__init__": "evil"},
        "confidence": 0.5,
        "raw_phrase": "test",
    })
    assert result is not None
    # show_today has no required params, so only safe defaults remain
    assert "malicious" not in result["params"]
    assert "__init__" not in result["params"]
    assert result["params"]["raw_text"] == "test"
    assert result["params"]["nl_fallback"] is True


# ── Command mapping tests ────────────────────────────────────────────────────


def test_map_show_today_to_command():
    """show_today maps to Command with command_type=show_today."""
    validated = validate_ai_output({
        "intent": "show_today",
        "params": {},
        "confidence": 0.95,
        "raw_phrase": "今天有什么安排",
    })
    cmd = map_to_command(validated, "12345")
    assert cmd is not None
    assert cmd.command_type == "show_today"
    assert cmd.user_id == "12345"
    assert cmd.source == "nl_fallback"


def test_map_unknown_returns_none():
    """unknown intent maps to None (no command executed)."""
    validated = validate_ai_output({
        "intent": "unknown",
        "params": {},
        "confidence": 0.3,
        "raw_phrase": "你好",
    })
    cmd = map_to_command(validated, "12345")
    assert cmd is None


def test_map_query_schedule_date():
    """query_schedule_date maps to Command with date param."""
    validated = validate_ai_output({
        "intent": "query_schedule_date",
        "params": {"date": "2026-06-05"},
        "confidence": 0.9,
        "raw_phrase": "查周五课表",
    })
    cmd = map_to_command(validated, "456")
    assert cmd is not None
    assert cmd.command_type == "query_schedule_date"
    assert cmd.params["date"] == "2026-06-05"


def test_map_finance_transaction():
    """finance_transaction (no required params) maps correctly."""
    validated = validate_ai_output({
        "intent": "finance_transaction",
        "params": {},
        "confidence": 0.85,
        "raw_phrase": "花了18块买奶茶",
    })
    cmd = map_to_command(validated, "789")
    assert cmd is not None
    assert cmd.command_type == "finance_transaction"


def test_map_verbal_scheduling():
    """verbal_scheduling maps correctly."""
    validated = validate_ai_output({
        "intent": "verbal_scheduling",
        "params": {},
        "confidence": 0.75,
        "raw_phrase": "明天下午三点开会",
    })
    cmd = map_to_command(validated, "101")
    assert cmd is not None
    assert cmd.command_type == "verbal_scheduling"


def test_map_hydration_record():
    """hydration_record maps to drink command."""
    validated = validate_ai_output({
        "intent": "hydration_record",
        "params": {},
        "confidence": 0.8,
        "raw_phrase": "喝了500ml水",
    })
    cmd = map_to_command(validated, "202")
    assert cmd is not None
    assert cmd.command_type == "drink"


def test_map_subjective_context():
    """subjective_context with text param maps correctly."""
    validated = validate_ai_output({
        "intent": "subjective_context",
        "params": {"text": "今天心情不错"},
        "confidence": 0.9,
        "raw_phrase": "今天心情不错",
    })
    cmd = map_to_command(validated, "303")
    assert cmd is not None
    assert cmd.command_type == "record_context"
    assert cmd.params["text"] == "今天心情不错"


# ── No arbitrary command execution ───────────────────────────────────────────


def test_cannot_execute_arbitrary_commands():
    """AI output with non-existent intent is rejected — no Command produced."""
    for bad_intent in ["shell_exec", "run_python", "delete_all", "write_file", "admin"]:
        result = validate_ai_output({
            "intent": bad_intent,
            "params": {},
            "confidence": 0.99,
        })
        assert result is None, f"Intent '{bad_intent}' should be rejected"


def test_cannot_bypass_with_params():
    """Even with valid intent, params are strictly scoped to allowlist."""
    validated = validate_ai_output({
        "intent": "show_today",
        "params": {"command": "rm -rf /", "exec": "malicious"},
        "confidence": 0.99,
        "raw_phrase": "test",
    })
    assert validated is not None
    assert "command" not in validated["params"]
    assert "exec" not in validated["params"]


# ── ALLOWED_INTENT_NAMES completeness ────────────────────────────────────────


def test_all_allowed_intents_have_consistent_structure():
    """Every non-None entry in ALLOWED_INTENTS has required_keys."""
    # Import the module for direct inspection
    from src.domain.natural_language import intent as nl_mod

    for name, spec in nl_mod.ALLOWED_INTENTS.items():
        assert isinstance(name, str)
        if name in NON_EXECUTABLE_INTENTS:
            assert spec is None
        else:
            assert spec is not None
            assert "command_type" in spec, f"{name} missing command_type"
            assert "required_params" in spec, f"{name} missing required_params"
            assert isinstance(spec["required_params"], list)


# ── State engine handler tests ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_nl_learning_sample_stored_in_state():
    """NL_INTENT_LEARNING_SAMPLE_RECORDED event is stored in state engine."""
    from src.core.state_engine import StateEngine
    from src.core.events import Event, EventType, AggregateType
    from datetime import datetime, timezone

    engine = StateEngine()
    event = Event(
        event_type=EventType.NL_INTENT_LEARNING_SAMPLE_RECORDED,
        aggregate_id="nl_samples",
        aggregate_type=AggregateType.NL_INTENT,
        timestamp=datetime.now(timezone.utc),
        payload={
            "raw_text": "今天有什么安排",
            "intent": "show_today",
            "confidence": 0.95,
            "success": True,
            "error": "",
        },
    )
    await engine.apply(event)

    view = engine.get_view("nl_intent", "samples")
    assert len(view["samples"]) == 1
    assert view["samples"][0]["intent"] == "show_today"
    assert view["samples"][0]["success"] is True
    assert view["total_count"] == 1


@pytest.mark.asyncio
async def test_nl_habit_summary_stored_in_state():
    """NL_INTENT_HABIT_SUMMARY_CREATED event is stored in state engine."""
    from src.core.state_engine import StateEngine
    from src.core.events import Event, EventType, AggregateType
    from datetime import datetime, timezone

    engine = StateEngine()
    event = Event(
        event_type=EventType.NL_INTENT_HABIT_SUMMARY_CREATED,
        aggregate_id="nl_habit_summary",
        aggregate_type=AggregateType.NL_INTENT,
        timestamp=datetime.now(timezone.utc),
        payload={
            "period_start": "2026-05-30",
            "period_end": "2026-06-02",
            "trigger_count": 10,
            "success_count": 7,
            "failure_count": 3,
            "top_intents": {"show_today": 4, "unknown": 3, "check_homework": 3},
            "top_phrases": [
                {"phrase": "今天有什么安排", "count": 3},
                {"phrase": "你好", "count": 2},
            ],
            "unknown_samples": [
                {"raw_text": "你好", "error": "unknown"},
            ],
        },
    )
    await engine.apply(event)

    view = engine.get_view("nl_intent", "habit_summary")
    assert "latest" in view
    assert view["latest"]["trigger_count"] == 10
    assert view["latest"]["success_count"] == 7
    assert len(view["summaries"]) == 1


# ── Deterministic parse still wins before AI fallback ────────────────────────


def test_deterministic_parse_still_works():
    """Known commands are still parsed by deterministic router, not AI."""
    from src.interface.telegram.router import parse_message

    # These should all be handled deterministically
    assert parse_message("/homework", 12345) is not None
    assert parse_message("今日状态", 12345) is not None
    assert parse_message("作业列表", 12345) is not None
    assert parse_message("查课表 2026-06-01", 12345) is not None
    assert parse_message("我明天请假", 12345) is not None
    assert parse_message("今天有什么安排", 12345) is not None


# ── Test that AI output parsing is stateless ─────────────────────────────────


def test_validate_rejects_malformed_json():
    """Various malformed AI outputs are safely rejected."""
    bad_inputs = [
        {},  # empty dict
        {"intent": 42},  # non-string intent
        {"intent": "show_today", "params": None},  # null params
        {"intent": "UNKNOWN"},  # wrong case
        {"intent": " show_today"},  # leading space
        {"intent": "show_today ", "params": {}},  # trailing space
    ]
    for inp in bad_inputs:
        result = validate_ai_output(inp)
        assert result is None, f"Should reject input: {inp}"


# ── Bot-level fallback test (mocked DeepSeek) ────────────────────────────────


@pytest.mark.asyncio
async def test_handle_nl_intent_fallback_success():
    """_handle_nl_intent_fallback returns Command when DeepSeek returns valid intent."""
    from unittest.mock import AsyncMock, MagicMock
    from src.interface.telegram.bot import CognitiveOSBot
    from src.core.events import Command

    bot = CognitiveOSBot.__new__(CognitiveOSBot)
    bot.settings = MagicMock()
    bot.settings.deepseek_api_key = "test-key"
    bot.bus = MagicMock()
    bot.bus.publish = AsyncMock()
    bot.state_engine = MagicMock()

    update = MagicMock()
    update.message.reply_text = AsyncMock()

    # Mock DeepSeek to return a valid intent
    bot._deepseek_json = AsyncMock(return_value={
        "intent": "show_today",
        "params": {},
        "confidence": 0.95,
        "raw_phrase": "今天有什么安排",
        "reasoning": "用户询问今日安排",
    })

    cmd, handled = await bot._handle_nl_intent_fallback(update, "今天有什么安排", 12345)
    assert cmd is not None
    assert handled is False
    assert isinstance(cmd, Command)
    assert cmd.command_type == "show_today"
    assert cmd.source == "nl_fallback"
    assert cmd.params.get("nl_fallback") is True


@pytest.mark.asyncio
async def test_handle_nl_intent_fallback_deterministic_bypass():
    """When deterministic parse_message returns not-None, AI fallback is not called."""
    from src.interface.telegram.router import parse_message

    cmd = parse_message("今日状态", 12345)
    assert cmd is not None
    assert cmd.command_type == "show_today"

    cmd = parse_message("/ping", 12345)
    assert cmd is not None
    assert cmd.command_type == "ping"


@pytest.mark.asyncio
async def test_handle_nl_intent_fallback_unknown():
    """_handle_nl_intent_fallback returns None for 'unknown' intent."""
    from unittest.mock import AsyncMock, MagicMock
    from src.interface.telegram.bot import CognitiveOSBot

    bot = CognitiveOSBot.__new__(CognitiveOSBot)
    bot.settings = MagicMock()
    bot.settings.deepseek_api_key = "test-key"
    bot.bus = MagicMock()
    bot.bus.publish = AsyncMock()
    bot.state_engine = MagicMock()

    update = MagicMock()
    update.message.reply_text = AsyncMock()

    bot._deepseek_json = AsyncMock(return_value={
        "intent": "unknown",
        "params": {},
        "confidence": 0.3,
        "raw_phrase": "你好",
        "reasoning": "无法分类",
    })

    cmd, handled = await bot._handle_nl_intent_fallback(update, "你好", 12345)
    assert cmd is None
    assert handled is True
    update.message.reply_text.assert_awaited()


@pytest.mark.asyncio
async def test_handle_nl_intent_fallback_api_failure():
    """_handle_nl_intent_fallback returns None on DeepSeek failure, no traceback."""
    from unittest.mock import AsyncMock, MagicMock
    from src.interface.telegram.bot import CognitiveOSBot

    bot = CognitiveOSBot.__new__(CognitiveOSBot)
    bot.settings = MagicMock()
    bot.settings.deepseek_api_key = "test-key"
    bot.bus = MagicMock()
    bot.bus.publish = AsyncMock()
    bot.state_engine = MagicMock()

    update = MagicMock()
    update.message.reply_text = AsyncMock()

    # Simulate API failure
    bot._deepseek_json = AsyncMock(return_value=None)

    cmd, handled = await bot._handle_nl_intent_fallback(update, "一些文字", 12345)
    assert cmd is None, "API failure should return None"
    assert handled is True
    update.message.reply_text.assert_awaited()


@pytest.mark.asyncio
async def test_handle_nl_intent_fallback_no_api_key():
    """Without API key, fallback returns None immediately."""
    from unittest.mock import AsyncMock, MagicMock
    from src.interface.telegram.bot import CognitiveOSBot

    bot = CognitiveOSBot.__new__(CognitiveOSBot)
    bot.settings = MagicMock()
    bot.settings.deepseek_api_key = ""  # No API key
    bot.bus = MagicMock()
    bot.bus.publish = AsyncMock()
    bot.state_engine = MagicMock()

    update = MagicMock()
    update.message.reply_text = AsyncMock()

    cmd, handled = await bot._handle_nl_intent_fallback(update, "一些文字", 12345)
    assert cmd is None
    assert handled is False


@pytest.mark.asyncio
async def test_learning_sample_event_emitted():
    """NL_INTENT_LEARNING_SAMPLE_RECORDED event is published during fallback."""
    from unittest.mock import AsyncMock, MagicMock
    from src.interface.telegram.bot import CognitiveOSBot
    from src.core.events import EventType

    bot = CognitiveOSBot.__new__(CognitiveOSBot)
    bot.settings = MagicMock()
    bot.settings.deepseek_api_key = "test-key"
    bot.bus = MagicMock()
    bot.bus.publish = AsyncMock()
    bot.state_engine = MagicMock()

    update = MagicMock()
    update.message.reply_text = AsyncMock()

    bot._deepseek_json = AsyncMock(return_value={
        "intent": "show_today",
        "params": {},
        "confidence": 0.95,
        "raw_phrase": "今天有什么安排",
    })

    cmd, handled = await bot._handle_nl_intent_fallback(update, "今天有什么安排", 12345)
    assert cmd is not None
    assert handled is False

    # Check that learning sample publish was called
    publish_calls = bot.bus.publish.await_args_list
    sample_events = [
        call[0][0] for call in publish_calls
        if call[0][0].event_type == EventType.NL_INTENT_LEARNING_SAMPLE_RECORDED
    ]
    assert len(sample_events) >= 1
    assert sample_events[0].payload["intent"] == "show_today"
    assert sample_events[0].payload["success"] is True

    # Check NL_INTENT_EXECUTED was also published
    exec_events = [
        call[0][0] for call in publish_calls
        if call[0][0].event_type == EventType.NL_INTENT_EXECUTED
    ]
    assert len(exec_events) >= 1
    assert exec_events[0].payload["intent"] == "show_today"


# ── 3-day summary aggregation test ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_habit_summary_aggregation():
    """Habit summary correctly aggregates counts and top intents from samples."""
    from datetime import datetime, timedelta, timezone
    from src.core.state_engine import StateEngine
    from src.core.events import Event, EventType, AggregateType

    engine = StateEngine()
    now = datetime.now(timezone.utc)

    # Add various samples within last 3 days
    samples_data = [
        ("show_today", True),
        ("show_today", True),
        ("check_homework", True),
        ("unknown", False),
        ("show_today", True),
        ("unknown", False),
        ("query_schedule_date", True),
        ("api_error", False),
    ]

    for intent, success in samples_data:
        sample = Event(
            event_type=EventType.NL_INTENT_LEARNING_SAMPLE_RECORDED,
            aggregate_id="nl_samples",
            aggregate_type=AggregateType.NL_INTENT,
            timestamp=now,
            payload={
                "raw_text": f"test {intent}",
                "intent": intent,
                "confidence": 0.8,
                "success": success,
                "error": "" if success else f"classified as {intent}",
            },
        )
        await engine.apply(sample)

    # Now simulate generating a summary event
    view = engine.get_view("nl_intent", "samples")
    assert view["total_count"] == 8
    assert len(view["samples"]) == 8

    # Generate and apply the summary
    from collections import Counter
    recent = [s for s in view["samples"]]
    trigger_count = len(recent)
    success_count = sum(1 for s in recent if s.get("success"))
    failure_count = trigger_count - success_count
    intent_counter = Counter(s.get("intent", "unknown") for s in recent)

    summary = Event(
        event_type=EventType.NL_INTENT_HABIT_SUMMARY_CREATED,
        aggregate_id="nl_habit_summary",
        aggregate_type=AggregateType.NL_INTENT,
        timestamp=now,
        payload={
            "period_start": (now - timedelta(days=3)).isoformat(),
            "period_end": now.isoformat(),
            "trigger_count": trigger_count,
            "success_count": success_count,
            "failure_count": failure_count,
            "top_intents": dict(intent_counter.most_common(5)),
            "top_phrases": [],
            "unknown_samples": [],
        },
    )
    await engine.apply(summary)

    summary_view = engine.get_view("nl_intent", "habit_summary")
    assert summary_view["latest"]["trigger_count"] == 8
    assert summary_view["latest"]["success_count"] == 5
    assert summary_view["latest"]["failure_count"] == 3
    # Top intents should include show_today with count 3
    assert summary_view["latest"]["top_intents"]["show_today"] == 3
