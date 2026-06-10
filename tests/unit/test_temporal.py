"""Test: Temporal Unification — TimeBlock, connectors, projection, replay."""

import asyncio
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, ".")

from src.core.temporal import TimeBlock, TemporalSource, TimeBlockType, TemporalProjection
from src.core.events import Event, EventType, AggregateType
from src.core.state_engine import StateEngine
from src.derived_state.temporal_projection import compute_projection
from src.connector.jwxt.client import JwxtConnector
from src.connector.jwxt.client import _jcs_to_time
from src.connector.google_calendar.client import GoogleCalendarConnector
from src.connector.google_calendar.client import _resolve_calendars


# ── TimeBlock model ────────────────────────────────────────────────────

def test_timeblock_serialization():
    now = datetime.now(timezone.utc)
    block = TimeBlock(
        block_id="test-1",
        source=TemporalSource.JWXT,
        block_type=TimeBlockType.CLASS_LECTURE,
        start=now,
        end=now + timedelta(hours=1, minutes=40),
        title="高等数学",
        location="A301",
    )
    d = block.to_dict()
    b2 = TimeBlock.from_dict(d)
    assert b2.block_id == "test-1"
    assert b2.source == TemporalSource.JWXT
    assert b2.title == "高等数学"
    assert b2.duration_minutes == 100
    print("✓ TimeBlock serialization roundtrip")


def test_timeblock_overlap():
    now = datetime.now(timezone.utc)
    b1 = TimeBlock("a", TemporalSource.JWXT, TimeBlockType.CLASS_LECTURE,
                   now, now + timedelta(hours=1), "A")
    b2 = TimeBlock("b", TemporalSource.JWXT, TimeBlockType.CLASS_LECTURE,
                   now + timedelta(minutes=30), now + timedelta(hours=2), "B")
    b3 = TimeBlock("c", TemporalSource.JWXT, TimeBlockType.CLASS_LECTURE,
                   now + timedelta(hours=2), now + timedelta(hours=3), "C")

    assert b1.overlaps(b2)
    assert not b1.overlaps(b3)
    assert b1.contains(now + timedelta(minutes=30))
    assert not b1.contains(now + timedelta(hours=2))
    print("✓ TimeBlock overlap and contains")


def test_timeblock_make_deadline():
    deadline = datetime(2026, 6, 15, 23, 59, tzinfo=timezone.utc)
    b = TimeBlock.make_deadline("作业提交", deadline, "数学")
    assert b.block_type == TimeBlockType.HOMEWORK_DEADLINE
    assert b.source == TemporalSource.CHAOXING_HOMEWORK
    assert b.metadata["course"] == "数学"
    print("✓ TimeBlock.make_deadline factory")


def test_jwxt_period_time_calibration():
    assert _jcs_to_time("1-2") == ("08:20", "09:50")
    assert _jcs_to_time("1-4") == ("08:20", "11:40")
    assert _jcs_to_time("9-10") == ("18:40", "20:05")
    assert _jcs_to_time("11-12") == ("20:15", "21:40")


# ── Connectors ─────────────────────────────────────────────────────────

async def test_jwxt_connector():
    conn = JwxtConnector(use_mock=True)
    ok = await conn.authenticate()
    assert ok

    data = await conn.fetch({"query": "weekly_schedule"})
    assert data["source"] == "jwxt"
    assert len(data["blocks"]) == 9

    # Verify all are TimeBlocks
    for bd in data["blocks"]:
        b = TimeBlock.from_dict(bd)
        assert b.source == TemporalSource.JWXT
    print("✓ JWXT connector returns 9 TimeBlocks")


async def test_jwxt_real_schedule_includes_future_window():
    from unittest.mock import AsyncMock
    from src.infrastructure.config import Settings

    local_today = datetime.now(timezone(timedelta(hours=8))).date()
    week_start = local_today - timedelta(days=local_today.weekday())
    settings = Settings(
        jwxt_mock=False,
        jwxt_semester_start=week_start.isoformat(),
        jwxt_schedule_window_days=14,
    )
    conn = JwxtConnector(use_mock=False, settings=settings)
    conn._fetch_schedule_api = AsyncMock(return_value={
        "kbList": [
            {
                "kcmc": "下周课程",
                "xm": "测试老师",
                "zcd": "2周",
                "xqjmc": "星期一",
                "jcs": "1-2",
                "cdmc": "测试教室",
            }
        ]
    })

    data = await conn.fetch({"query": "weekly_schedule"})
    blocks = [TimeBlock.from_dict(b) for b in data["blocks"]]

    assert len(blocks) == 1
    assert blocks[0].start.date() == week_start + timedelta(days=7)
    assert blocks[0].metadata["teaching_week"] == 2


async def test_jwxt_handle_fetch_request():
    conn = JwxtConnector(use_mock=True)
    event = Event(
        event_type=EventType.CONNECTOR_FETCH_REQUESTED,
        aggregate_id="user-1",
        aggregate_type=AggregateType.USER,
        payload={"source": "jwxt", "query": "weekly_schedule"},
    )
    result = await conn.handle_fetch_request(event)
    block_events = [e for e in result if e.event_type == EventType.TEMPORAL_BLOCK_ADDED]
    course_events = [e for e in result if e.event_type == EventType.COURSE_ACTIVATED]
    completed_events = [e for e in result if e.event_type == EventType.CONNECTOR_FETCH_COMPLETED]
    assert len(block_events) == 9
    assert len(course_events) == 6
    assert len(completed_events) == 1
    print("✓ JWXT handle_fetch_request emits schedule, course activation, and completion events")


async def test_google_calendar_connector():
    conn = GoogleCalendarConnector(use_mock=True)
    ok = await conn.authenticate()
    assert ok

    data = await conn.fetch({"query": "upcoming"})
    assert data["source"] == "google_calendar"
    assert len(data["blocks"]) == 3
    print("✓ Google Calendar connector returns 3 TimeBlocks")


def test_google_calendar_selected_calendar_resolution():
    class _Call:
        def execute(self):
            return {
                "items": [
                    {"id": "primary-id", "summary": "Primary", "primary": True},
                    {"id": "family-id", "summary": "Family", "selected": True},
                    {"id": "hidden-id", "summary": "Hidden", "selected": False},
                ]
            }

    class _CalendarList:
        def list(self, maxResults=100):
            return _Call()

    class _Service:
        def calendarList(self):
            return _CalendarList()

    calendars = _resolve_calendars(_Service(), "selected")
    assert calendars == [
        {"id": "primary-id", "summary": "Primary"},
        {"id": "family-id", "summary": "Family"},
    ]


# ── Temporal Projection ───────────────────────────────────────────────

def test_projection_empty():
    proj = compute_projection([])
    assert proj.total_blocks == 0
    assert proj.busy_density == 0.0
    assert proj.weekly_load == 0.0
    assert len(proj.free_slots) > 0  # entire day is free
    print("✓ projection: empty → full free day")


def test_projection_busy_density():
    now = datetime.now(timezone.utc)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # 8h of classes today
    blocks = [
        TimeBlock("a", TemporalSource.JWXT, TimeBlockType.CLASS_LECTURE,
                  today.replace(hour=8), today.replace(hour=12), "Morning class"),
        TimeBlock("b", TemporalSource.JWXT, TimeBlockType.CLASS_LAB,
                  today.replace(hour=14), today.replace(hour=18), "Afternoon lab"),
    ]

    proj = compute_projection(blocks)
    assert proj.busy_density > 0.4  # 8h / 17h ≈ 0.47
    assert proj.busy_density < 0.6
    assert proj.daily_capacity < 10  # 17-8 = 9h free
    print(f"✓ projection: 8h classes → density={proj.busy_density:.2f}, capacity={proj.daily_capacity}h")


def test_projection_context_switching():
    now = datetime.now(timezone.utc)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Mix of sources and types
    blocks = [
        TimeBlock("a", TemporalSource.JWXT, TimeBlockType.CLASS_LECTURE,
                  today.replace(hour=8), today.replace(hour=9), "Math"),
        TimeBlock("b", TemporalSource.GOOGLE_CALENDAR, TimeBlockType.CALENDAR_EVENT,
                  today.replace(hour=10), today.replace(hour=11), "Meeting"),
        TimeBlock("c", TemporalSource.JWXT, TimeBlockType.CLASS_LAB,
                  today.replace(hour=14), today.replace(hour=16), "Lab"),
        TimeBlock("d", TemporalSource.CHAOXING_HOMEWORK, TimeBlockType.HOMEWORK_DEADLINE,
                  today.replace(hour=18), today.replace(hour=18), "DDL"),
    ]

    proj = compute_projection(blocks)
    assert proj.context_switching_score > 0.2  # several transitions
    assert proj.source_breakdown["jwxt"] == 2
    assert proj.source_breakdown["google_calendar"] == 1
    assert proj.source_breakdown["chaoxing"] == 1
    print(f"✓ projection: context_switching={proj.context_switching_score:.2f}, breakdown={proj.source_breakdown}")


def test_projection_deterministic():
    now = datetime.now(timezone.utc)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)

    blocks = [
        TimeBlock("a", TemporalSource.JWXT, TimeBlockType.CLASS_LECTURE,
                  today.replace(hour=8), today.replace(hour=10), "Class"),
    ]

    p1 = compute_projection(blocks)
    p2 = compute_projection(blocks)
    assert p1.to_dict() == p2.to_dict()
    print("✓ projection: deterministic — same input → same output")


# ── StateEngine integration ────────────────────────────────────────────

async def test_state_engine_temporal_integration():
    engine = StateEngine()

    # Simulate JWXT connector producing blocks
    conn = JwxtConnector(use_mock=True)
    fetch_event = Event(
        event_type=EventType.CONNECTOR_FETCH_REQUESTED,
        aggregate_id="user-1",
        aggregate_type=AggregateType.USER,
        payload={"source": "jwxt", "query": "weekly_schedule"},
    )
    block_events = await conn.handle_fetch_request(fetch_event)

    # Apply all block events to StateEngine
    for e in block_events:
        await engine.apply(e)

    # Verify temporal blocks stored
    temporal_blocks = engine.get_temporal_blocks()
    assert len(temporal_blocks) == 9

    # Verify derived state has temporal_projection
    derived = engine.get_all_derived()
    proj = derived.get("temporal_projection", {})
    assert proj["total_blocks"] == 9
    assert "busy_density" in proj
    assert "free_slots" in proj
    print(f"✓ StateEngine stores {len(temporal_blocks)} temporal blocks")
    print(f"  temporal_projection: density={proj['busy_density']}, load={proj['weekly_load']}")


async def test_google_calendar_zero_sync_clears_stale_blocks():
    engine = StateEngine()
    now = datetime.now(timezone.utc)
    block = TimeBlock(
        "gcal-stale",
        TemporalSource.GOOGLE_CALENDAR,
        TimeBlockType.WORKOUT_BLOCK,
        now + timedelta(hours=1),
        now + timedelta(hours=2),
        "Gym",
    )
    await engine.apply(Event(
        event_type=EventType.TEMPORAL_BLOCK_ADDED,
        aggregate_id=block.block_id,
        aggregate_type=AggregateType.TEMPORAL,
        payload=block.to_dict(),
    ))
    await engine.apply(Event(
        event_type=EventType.CONNECTOR_FETCH_COMPLETED,
        aggregate_id="calendar-sync",
        aggregate_type=AggregateType.SYSTEM,
        payload={
            "source": "google_calendar",
            "calendar_id": "selected",
            "calendar_count": 2,
            "calendars": [],
            "count": 0,
            "raw_count": 0,
        },
    ))
    assert [
        b for b in engine.get_temporal_blocks()
        if str(getattr(b, "source", "")) == "google_calendar"
    ] == []


async def test_temporal_context_tracks_next_workout():
    engine = StateEngine()
    now = datetime.now(timezone.utc)
    workout = TimeBlock(
        "workout-1",
        TemporalSource.GOOGLE_CALENDAR,
        TimeBlockType.WORKOUT_BLOCK,
        now + timedelta(minutes=90),
        now + timedelta(minutes=150),
        "健身",
    )
    await engine.apply(Event(
        event_type=EventType.TEMPORAL_BLOCK_ADDED,
        aggregate_id=workout.block_id,
        aggregate_type=AggregateType.TEMPORAL,
        payload=workout.to_dict(),
    ))
    ctx = engine.get_temporal_context()["active_temporal_context"]
    assert ctx["workout_block_later"] is True
    assert ctx["next_workout"]["title"] == "健身"


async def test_temporal_replay_deterministic():
    conn = JwxtConnector(use_mock=True)
    fetch_event = Event(
        event_type=EventType.CONNECTOR_FETCH_REQUESTED,
        aggregate_id="user-1",
        aggregate_type=AggregateType.USER,
        payload={"source": "jwxt", "query": "weekly_schedule"},
    )
    block_events = await conn.handle_fetch_request(fetch_event)

    engine1 = StateEngine()
    for e in block_events:
        await engine1.apply(e)
    h1 = engine1.state_hash()

    engine2 = StateEngine()
    await engine2.rebuild_from_events(block_events)
    h2 = engine2.state_hash()

    assert h1 == h2
    p1 = engine1.get_temporal_projection()
    p2 = engine2.get_temporal_projection()
    assert p1 == p2
    print("✓ temporal replay: deterministic hash + projection match")


if __name__ == "__main__":
    print("=== TimeBlock Model ===")
    test_timeblock_serialization()
    test_timeblock_overlap()
    test_timeblock_make_deadline()

    print("\n=== Connectors ===")
    asyncio.run(test_jwxt_connector())
    asyncio.run(test_jwxt_handle_fetch_request())
    asyncio.run(test_google_calendar_connector())

    print("\n=== Temporal Projection ===")
    test_projection_empty()
    test_projection_busy_density()
    test_projection_context_switching()
    test_projection_deterministic()

    print("\n=== StateEngine Integration ===")
    asyncio.run(test_state_engine_temporal_integration())
    asyncio.run(test_temporal_replay_deterministic())

    print("\nTemporal Unification: all checks passed")
