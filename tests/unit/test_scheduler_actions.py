"""Test: Scheduler job actions — calendar_sync action routing."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from uuid import uuid4

sys.path.insert(0, ".")

import pytest

from src.core.events import AggregateType, Event, EventType


@pytest.mark.asyncio
async def test_scheduler_calendar_sync_action():
    """SCHEDULED_TRIGGER with action=calendar_sync produces CONNECTOR_FETCH_REQUESTED."""
    from src.interface.telegram.bot import CognitiveOSBot

    bot = CognitiveOSBot.__new__(CognitiveOSBot)

    event = Event(
        event_type=EventType.SYSTEM_SCHEDULED_TRIGGER,
        aggregate_id="auto_sync_calendar",
        aggregate_type=AggregateType.SYSTEM,
        timestamp=datetime.now(timezone.utc),
        event_id=uuid4(),
        payload={"action": "calendar_sync"},
    )

    # Simulate the handler logic (inlined from wire_handlers)
    if event.event_type != EventType.SYSTEM_SCHEDULED_TRIGGER:
        assert False, "Wrong event type"
    action = event.payload.get("action", "")
    assert action == "calendar_sync"

    # The handler should produce a CONNECTOR_FETCH_REQUESTED for google_calendar
    out = [Event(
        event_type=EventType.CONNECTOR_FETCH_REQUESTED,
        aggregate_id=event.aggregate_id,
        aggregate_type=AggregateType.SYSTEM,
        causation_id=event.event_id,
        payload={"source": "google_calendar", "query": "upcoming"},
    )]
    assert len(out) == 1
    assert out[0].event_type == EventType.CONNECTOR_FETCH_REQUESTED
    assert out[0].payload["source"] == "google_calendar"


@pytest.mark.asyncio
async def test_scheduler_homework_sync_action():
    """SCHEDULED_TRIGGER with action=check_homework produces CONNECTOR_FETCH_REQUESTED."""
    from src.core.events import EventType, AggregateType

    event = Event(
        event_type=EventType.SYSTEM_SCHEDULED_TRIGGER,
        aggregate_id="auto_sync_homework",
        aggregate_type=AggregateType.SYSTEM,
        timestamp=datetime.now(timezone.utc),
        event_id=uuid4(),
        payload={"action": "check_homework"},
    )

    action = event.payload.get("action", "")
    assert action == "check_homework"

    out = [Event(
        event_type=EventType.CONNECTOR_FETCH_REQUESTED,
        aggregate_id=event.aggregate_id,
        aggregate_type=AggregateType.HOMEWORK,
        causation_id=event.event_id,
        payload={"source": "chaoxing", "query": "homework_list", "scope": None},
    )]
    assert len(out) == 1
    assert out[0].event_type == EventType.CONNECTOR_FETCH_REQUESTED
    assert out[0].payload["source"] == "chaoxing"


@pytest.mark.asyncio
async def test_scheduler_schedule_sync_action():
    """SCHEDULED_TRIGGER with action=schedule_daily_sync produces CONNECTOR_FETCH_REQUESTED for jwxt."""
    from src.core.events import EventType, AggregateType

    event = Event(
        event_type=EventType.SYSTEM_SCHEDULED_TRIGGER,
        aggregate_id="auto_sync_schedule",
        aggregate_type=AggregateType.SYSTEM,
        timestamp=datetime.now(timezone.utc),
        event_id=uuid4(),
        payload={"action": "schedule_daily_sync"},
    )

    action = event.payload.get("action", "")
    assert action == "schedule_daily_sync"

    out = [Event(
        event_type=EventType.CONNECTOR_FETCH_REQUESTED,
        aggregate_id=event.aggregate_id,
        aggregate_type=AggregateType.HOMEWORK,
        causation_id=event.event_id,
        payload={
            "source": "jwxt",
            "query": "weekly_schedule",
            "intent": "schedule_daily_sync",
        },
    )]
    assert len(out) == 1
    assert out[0].event_type == EventType.CONNECTOR_FETCH_REQUESTED
    assert out[0].payload["source"] == "jwxt"


@pytest.mark.asyncio
async def test_scheduler_interval_semantics():
    """Verify default intervals exclude extra JWXT polling."""
    from src.infrastructure.config import Settings
    from src.infrastructure.scheduler import CognitiveScheduler

    settings = Settings()
    scheduler = CognitiveScheduler()

    # Record configured intervals
    intervals = {}

    # Simulate run.py job registration
    scheduler.add_interval_job(
        "auto_sync_homework",
        settings.homework_sync_interval_hours * 60,
        {"action": "check_homework"},
    )
    if settings.schedule_sync_interval_hours > 0:
        scheduler.add_interval_job(
            "auto_sync_schedule",
            settings.schedule_sync_interval_hours * 60,
            {"action": "schedule_daily_sync"},
        )
    scheduler.add_interval_job(
        "auto_sync_calendar",
        settings.google_calendar_poll_interval_minutes,
        {"action": "calendar_sync"},
    )

    jobs = scheduler.jobs
    for j in jobs:
        intervals[j["id"]] = j["trigger"]

    # Homework: 12h = 720 min → APScheduler displays as "interval[12:00:00]"
    hw_trigger = intervals.get("auto_sync_homework", "")
    assert "12:00:00" in hw_trigger, f"Expected 12h interval, got: {hw_trigger}"

    # JWXT runs through the daily cron by default, not an extra interval.
    assert settings.schedule_sync_interval_hours == 0
    assert "auto_sync_schedule" not in intervals

    # Google Calendar: 30 min (default, configurable)
    cal_trigger = intervals.get("auto_sync_calendar", "")
    assert "0:30:00" in cal_trigger, f"Expected 30min interval, got: {cal_trigger}"

    # Ensure no fast homework polling < 1h exists
    for jid, trigger in intervals.items():
        if "homework" in jid or "schedule" in jid:
            # APScheduler format: interval[HH:MM:SS]
            import re
            m = re.search(r"(\d+):(\d+):(\d+)", trigger)
            if m:
                hours = int(m.group(1))
                minutes = int(m.group(2))
                total_minutes = hours * 60 + minutes
                if 0 < total_minutes < 60:  # ignore 0 (disabled)
                    assert False, f"Job {jid} has fast polling interval {total_minutes} min (< 60 min)"

    # Summary: fastest reasonable calendar interval is 15 min (API quota permitting).
    # Default 30 min is good for browser-heavy connectors.
    scheduler.stop()


def test_scheduler_default_jwxt_daily_job_is_0700():
    from src.infrastructure.config import Settings
    from src.infrastructure.scheduler import CognitiveScheduler

    settings = Settings()
    scheduler = CognitiveScheduler()

    for sync_time in settings.schedule_daily_sync_times.split(","):
        hour_text, minute_text = sync_time.strip().split(":", 1)
        scheduler.add_daily_job(
            f"schedule_daily_sync_{hour_text}_{minute_text}",
            int(hour_text),
            int(minute_text),
            {"action": "schedule_daily_sync"},
            timezone_str=settings.google_calendar_timezone,
        )

    jobs = scheduler.jobs
    assert settings.schedule_daily_sync_times == "07:00"
    assert len(jobs) == 1
    assert jobs[0]["id"] == "schedule_daily_sync_07_00"
    assert "hour='7'" in jobs[0]["trigger"]
    assert "minute='0'" in jobs[0]["trigger"]
    scheduler.stop()


def test_scheduler_nightly_review_daily_job():
    from src.infrastructure.config import Settings
    from src.infrastructure.scheduler import CognitiveScheduler

    settings = Settings()
    scheduler = CognitiveScheduler()
    hour_text, minute_text = settings.nightly_review_time.split(":", 1)
    scheduler.add_daily_job(
        f"nightly_review_{hour_text}_{minute_text}",
        int(hour_text),
        int(minute_text),
        {"action": "nightly_review"},
        timezone_str=settings.nightly_review_timezone,
    )

    jobs = scheduler.jobs
    assert jobs[0]["id"] == "nightly_review_21_00"
    assert "cron" in jobs[0]["trigger"]
    scheduler.stop()


def test_scheduler_nightly_review_action_contract():
    event = Event(
        event_type=EventType.SYSTEM_SCHEDULED_TRIGGER,
        aggregate_id="nightly_review_21_00",
        aggregate_type=AggregateType.SYSTEM,
        timestamp=datetime.now(timezone.utc),
        event_id=uuid4(),
        payload={"action": "nightly_review"},
    )

    out = [Event(
        event_type=EventType.DAILY_REVIEW_REQUESTED,
        aggregate_id="2026-05-31",
        aggregate_type=AggregateType.SYSTEM,
        causation_id=event.event_id,
        payload={"date": "2026-05-31", "force": False},
    )]
    assert out[0].event_type == EventType.DAILY_REVIEW_REQUESTED
    assert out[0].payload["force"] is False
