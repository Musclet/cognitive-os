"""Daily review and cognitive profile audit tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.core.events import AggregateType, Event, EventType
from src.core.state_engine import StateEngine
from src.core.temporal import TemporalSource, TimeBlock, TimeBlockType
from src.derived_state.cognitive_profile import audit_cognitive_profile
from src.domain.daily_review.handlers import handle_daily_review_requested


def test_nightly_review_startup_catchup_due_logic():
    from unittest.mock import MagicMock
    from zoneinfo import ZoneInfo
    from src.interface.telegram.bot import CognitiveOSBot

    bot = CognitiveOSBot.__new__(CognitiveOSBot)
    bot.settings = MagicMock()
    bot.settings.nightly_review_enabled = True
    bot.settings.nightly_review_time = "21:00"
    bot.state_engine = StateEngine()

    tz = ZoneInfo("Asia/Singapore")
    assert not bot._nightly_review_due_on_startup(datetime(2026, 5, 31, 20, 59, tzinfo=tz))
    assert bot._nightly_review_due_on_startup(datetime(2026, 5, 31, 21, 1, tzinfo=tz))


@pytest.mark.asyncio
async def test_nightly_review_startup_catchup_not_due_after_sent():
    from unittest.mock import MagicMock
    from zoneinfo import ZoneInfo
    from src.interface.telegram.bot import CognitiveOSBot

    bot = CognitiveOSBot.__new__(CognitiveOSBot)
    bot.settings = MagicMock()
    bot.settings.nightly_review_enabled = True
    bot.settings.nightly_review_time = "21:00"
    bot.state_engine = StateEngine()
    await bot.state_engine.apply(Event(
        EventType.DAILY_REVIEW_SENT,
        "2026-05-31",
        AggregateType.USER,
        payload={"date": "2026-05-31", "sent_to": [123], "source": "test"},
    ))

    tz = ZoneInfo("Asia/Singapore")
    assert not bot._nightly_review_due_on_startup(datetime(2026, 5, 31, 21, 1, tzinfo=tz))


def test_cognitive_profile_empty_state_reports_blind_spots():
    audit = audit_cognitive_profile({}, {}, datetime(2026, 5, 31, tzinfo=timezone.utc))

    assert audit["maturity_score"] < 40
    assert "情绪样本不足" in audit["blind_spots"]
    assert "认知学习记忆为空" in audit["blind_spots"]


def test_cognitive_profile_uses_memory_mood_context_and_feedback():
    now = datetime(2026, 5, 31, 13, 0, tzinfo=timezone.utc)
    state = {
        "memory": {
            "u1": {"entries": [{"content": "画画抗拒来自启动成本", "created_at": now.isoformat()}]},
        },
        "subjective": {
            "u1": {
                "mood_history": [{"score": 6, "recorded_at": now.isoformat()}],
                "contexts": [{"kind": "context", "text": "下午健身", "created_at": now.isoformat()}],
                "notes": [],
            },
        },
        "behavior": {
            "current": {
                "feedback_log": [
                    {
                        "action": "accepted",
                        "outcome": "completed",
                        "task_id": "画画",
                        "timestamp": now.isoformat(),
                        "outcome_timestamp": now.isoformat(),
                    }
                ]
            }
        },
        "art": {"today": {"progress": {"sessions": [{"recorded_at": now.isoformat()}]}}},
    }
    derived = {"behavior": {"total_recommendations": 1, "planning_reliability": 1.0}, "reflection": {}}

    audit = audit_cognitive_profile(state, derived, now)

    assert audit["maturity_score"] >= 50
    assert "执行反馈" in audit["known_areas"]
    assert audit["sample_counts"]["art_sessions"] == 1


@pytest.mark.asyncio
async def test_daily_review_handler_emits_audit_and_generated_review():
    engine = StateEngine()
    now = datetime.now(timezone.utc).replace(hour=8, minute=0, second=0, microsecond=0)
    block = TimeBlock(
        "jwxt-review",
        TemporalSource.JWXT,
        TimeBlockType.CLASS_LECTURE,
        now,
        now + timedelta(hours=2),
        "绘画基础",
        location="画室",
    )
    await engine.apply(Event(
        EventType.TEMPORAL_BLOCK_ADDED,
        block.block_id,
        AggregateType.TEMPORAL,
        payload=block.to_dict(),
    ))
    await engine.apply(Event(
        EventType.MEMORY_ENTRY_CREATED,
        "u1",
        AggregateType.USER,
        payload={"content": "晚上效率更高", "tags": ["pattern"], "source": "test"},
    ))
    await engine.apply(Event(
        EventType.PLANNING_TASK_COMPLETED,
        "u1",
        AggregateType.USER,
        payload={"task_id": "画画", "completed_at": now.isoformat()},
    ))

    request = Event(
        EventType.DAILY_REVIEW_REQUESTED,
        now.astimezone(timezone(timedelta(hours=8))).date().isoformat(),
        AggregateType.SYSTEM,
        payload={"date": now.astimezone(timezone(timedelta(hours=8))).date().isoformat()},
    )
    produced = await handle_daily_review_requested(request, engine)

    assert [e.event_type for e in produced] == [
        EventType.COGNITIVE_PROFILE_AUDITED,
        EventType.DAILY_REVIEW_GENERATED,
    ]
    assert "晚间总结" in produced[1].payload["text"]
    assert "认知学习审查" in produced[1].payload["text"]


@pytest.mark.asyncio
async def test_state_engine_stores_daily_review_events():
    engine = StateEngine()
    date_str = "2026-05-31"
    await engine.apply(Event(
        EventType.COGNITIVE_PROFILE_AUDITED,
        date_str,
        AggregateType.USER,
        payload={"date": date_str, "maturity_score": 42},
    ))
    await engine.apply(Event(
        EventType.DAILY_REVIEW_GENERATED,
        date_str,
        AggregateType.USER,
        payload={"date": date_str, "text": "晚间总结", "audit": {"maturity_score": 42}},
    ))
    await engine.apply(Event(
        EventType.DAILY_REVIEW_SENT,
        date_str,
        AggregateType.USER,
        payload={"date": date_str, "sent_to": [123], "source": "manual"},
    ))

    assert engine.get_view("cognitive_profile", "current")["latest"]["maturity_score"] == 42
    review = engine.get_view("daily_review", date_str)
    assert review["text"] == "晚间总结"
    assert review["sent_to"] == [123]
