"""Test: Schedule date formatting — JWXT blocks for specified date."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, ".")

import pytest

from src.core.events import AggregateType, Event, EventType
from src.core.state_engine import StateEngine
from src.core.temporal import TemporalSource, TimeBlock, TimeBlockType


def _make_bot_for_test(engine):
    """Create a minimal CognitiveOSBot with mocked settings for testing."""
    from src.infrastructure.config import Settings
    from src.interface.telegram.bot import CognitiveOSBot
    bot = CognitiveOSBot.__new__(CognitiveOSBot)
    bot.state_engine = engine
    bot.settings = Settings()
    return bot


@pytest.mark.asyncio
async def test_format_schedule_date_shows_only_jwxt():
    """_format_schedule_date only shows JWXT blocks for the given date."""
    engine = StateEngine()
    today = datetime.now(timezone.utc).replace(hour=8, minute=0, second=0, microsecond=0)

    jwxt = TimeBlock(
        "jwxt-1", TemporalSource.JWXT, TimeBlockType.CLASS_LECTURE,
        today, today + timedelta(hours=1),
        "计算机图形学", location="A101",
        metadata={"teacher": "王老师"},
    )
    gcal = TimeBlock(
        "gcal-1", TemporalSource.GOOGLE_CALENDAR, TimeBlockType.WORKOUT_BLOCK,
        today, today + timedelta(hours=1),
        "健身",
    )
    for block in (jwxt, gcal):
        await engine.apply(Event(EventType.TEMPORAL_BLOCK_ADDED, block.block_id, AggregateType.TEMPORAL, payload=block.to_dict()))

    bot = _make_bot_for_test(engine)
    date_str = today.strftime("%Y-%m-%d")
    text = bot._format_schedule_date(date_str)

    assert "计算机图形学" in text
    assert "健身" not in text
    assert "A101" in text


@pytest.mark.asyncio
async def test_format_schedule_date_no_blocks():
    """_format_schedule_date returns '无课程安排' for date with no blocks."""
    engine = StateEngine()
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")

    bot = _make_bot_for_test(engine)
    text = bot._format_schedule_date(tomorrow)
    assert "无课程安排" in text


@pytest.mark.asyncio
async def test_format_schedule_date_invalid_date():
    """_format_schedule_date returns error for invalid date."""
    engine = StateEngine()
    bot = _make_bot_for_test(engine)
    text = bot._format_schedule_date("not-a-date")
    assert "日期格式无效" in text


def test_schedule_page_keyboard_has_daily_pagination():
    engine = StateEngine()
    bot = _make_bot_for_test(engine)
    markup = bot._schedule_page_keyboard("2026-06-01")
    labels = [button.text for row in markup.inline_keyboard for button in row]
    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]

    assert labels == ["前一天", "今天", "后一天"]
    assert "sch:2026-05-31" in callbacks
    assert "sch:2026-06-02" in callbacks


@pytest.mark.asyncio
async def test_format_schedule_date_shows_teacher():
    """_format_schedule_date includes teacher name when present."""
    engine = StateEngine()
    today = datetime.now(timezone.utc).replace(hour=10, minute=0, second=0, microsecond=0)

    jwxt = TimeBlock(
        "jwxt-2", TemporalSource.JWXT, TimeBlockType.CLASS_LECTURE,
        today, today + timedelta(hours=1),
        "大学英语", location="B202",
        metadata={"teacher": "李老师"},
    )
    await engine.apply(Event(EventType.TEMPORAL_BLOCK_ADDED, jwxt.block_id, AggregateType.TEMPORAL, payload=jwxt.to_dict()))

    bot = _make_bot_for_test(engine)
    date_str = today.strftime("%Y-%m-%d")
    text = bot._format_schedule_date(date_str)

    assert "大学英语" in text
    assert "李老师" in text
    assert "B202" in text


@pytest.mark.asyncio
async def test_school_leave_hides_jwxt_classes_but_keeps_calendar_blocks():
    engine = StateEngine()
    today = datetime.now(timezone.utc).replace(hour=8, minute=0, second=0, microsecond=0)
    date_str = today.strftime("%Y-%m-%d")

    jwxt = TimeBlock(
        "jwxt-leave", TemporalSource.JWXT, TimeBlockType.CLASS_LECTURE,
        today, today + timedelta(hours=2),
        "影视特效技术", location="A101",
    )
    gcal = TimeBlock(
        "gcal-art", TemporalSource.GOOGLE_CALENDAR, TimeBlockType.CALENDAR_EVENT,
        today + timedelta(hours=3), today + timedelta(hours=5),
        "画画",
    )
    for block in (jwxt, gcal):
        await engine.apply(Event(EventType.TEMPORAL_BLOCK_ADDED, block.block_id, AggregateType.TEMPORAL, payload=block.to_dict()))

    await engine.apply(Event(
        EventType.SUBJECTIVE_CONTEXT_ADDED,
        "user-1",
        AggregateType.USER,
        payload={"kind": "school_leave", "text": "请假", "date": date_str},
    ))

    effective = engine.get_temporal_blocks()
    raw = engine.get_temporal_blocks(include_school_leave_classes=True)
    assert jwxt.block_id in {b.block_id for b in raw}
    assert jwxt.block_id not in {b.block_id for b in effective}
    assert gcal.block_id in {b.block_id for b in effective}

    bot = _make_bot_for_test(engine)
    text = bot._format_schedule_date(date_str)
    assert "无课程安排" in text
    assert "画画" not in text
