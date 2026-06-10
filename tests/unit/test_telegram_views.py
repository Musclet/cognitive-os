from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from src.core.events import AggregateType, Event, EventType
from src.core.state_engine import StateEngine
from src.core.temporal import TemporalSource, TimeBlock, TimeBlockType
from src.interface.telegram.bot import _format_calendar_today, _format_temporal_response


@pytest.mark.asyncio
async def test_schedule_view_excludes_google_calendar_blocks():
    engine = StateEngine()
    today = datetime.now(ZoneInfo("Asia/Singapore")).replace(hour=10, minute=0, second=0, microsecond=0)
    jwxt = TimeBlock(
        "jwxt-1",
        TemporalSource.JWXT,
        TimeBlockType.CLASS_LECTURE,
        today,
        today + timedelta(hours=1),
        "计算机图形学",
        location="A101",
        metadata={"teacher": "王老师"},
    )
    gcal = TimeBlock(
        "gcal-1",
        TemporalSource.GOOGLE_CALENDAR,
        TimeBlockType.WORKOUT_BLOCK,
        today,
        today + timedelta(hours=1),
        "健身",
    )
    for block in (jwxt, gcal):
        await engine.apply(Event(EventType.TEMPORAL_BLOCK_ADDED, block.block_id, AggregateType.TEMPORAL, payload=block.to_dict()))

    text = _format_temporal_response("show_today", engine)
    assert "计算机图形学" in text
    assert "A101" in text
    assert "健身" not in text


@pytest.mark.asyncio
async def test_calendar_view_excludes_jwxt_blocks():
    engine = StateEngine()
    today = datetime.now(ZoneInfo("Asia/Singapore")).replace(hour=10, minute=0, second=0, microsecond=0)
    jwxt = TimeBlock("jwxt-1", TemporalSource.JWXT, TimeBlockType.CLASS_LECTURE, today, today + timedelta(hours=1), "课程", "A101")
    gcal = TimeBlock("gcal-1", TemporalSource.GOOGLE_CALENDAR, TimeBlockType.WORKOUT_BLOCK, today, today + timedelta(hours=1), "健身")
    for block in (jwxt, gcal):
        await engine.apply(Event(EventType.TEMPORAL_BLOCK_ADDED, block.block_id, AggregateType.TEMPORAL, payload=block.to_dict()))

    text = _format_calendar_today(engine)
    assert "健身" in text
    assert "课程" not in text
