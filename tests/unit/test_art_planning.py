"""Tests for art planning domain — planner, progress, routing, Obsidian writer.

Covers:
- "早安" routing → art_plan_greeting command
- Daily note path generation (M.D.md)
- Obsidian section upsert idempotency
- Art planner day type classification and target selection
- Managed calendar event extendedProperties markers
- Progress parsing and StateEngine update
- Insertion → replan event
- Existing test non-regression (no side effects on other domains)
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from src.core.events import Event, EventType, AggregateType
from src.core.state_engine import StateEngine
from src.infrastructure.config import Settings
from src.integrations.obsidian_daily import ObsidianDailyWriter, _daily_note_path, _upsert_section


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def settings() -> Settings:
    return Settings(
        obsidian_vault_path=str(Path.cwd() / "test_vault"),
        obsidian_daily_folder="daily",
        obsidian_daily_template_path="Templates/每日打卡模板.md",
        art_planning_enabled=True,
        art_default_target_minutes=360,
        art_minimum_keepalive_minutes=25,
        art_calendar_id="primary",
        art_managed_calendar_source="daily_art_plan",
    )


@pytest.fixture
def state_engine() -> StateEngine:
    return StateEngine()


@pytest.fixture
def obsidian_writer(settings: Settings) -> ObsidianDailyWriter:
    return ObsidianDailyWriter(settings)


@pytest.fixture
def test_date() -> datetime:
    return datetime(2026, 5, 31, 10, 0, tzinfo=ZoneInfo("Asia/Singapore"))


@pytest.fixture
def temp_vault(tmp_path: Path, settings: Settings) -> Path:
    """Create a temporary vault directory with template."""
    vault = tmp_path / "test_vault"
    daily = vault / "daily"
    templates = vault / "Templates"
    daily.mkdir(parents=True, exist_ok=True)
    templates.mkdir(parents=True, exist_ok=True)

    # Create template
    (templates / "每日打卡模板.md").write_text(
        "# {{date}} 星期{{weekday}}\n\n"
        "## 🎨 绘画训练\n\n\n"
        "## 🧾 今日事件流\n\n\n"
        "## 📝 其他\n",
        encoding="utf-8",
    )
    return vault


# ── Section upsert tests ──────────────────────────────────────────────────

class TestSectionUpsert:
    def test_upsert_existing_section(self):
        content = "## 🎨 绘画训练\n\n旧内容\n\n## 🧾 今日事件流\n"
        result = _upsert_section(content, "## 🎨 绘画训练", "新内容")
        assert "新内容" in result
        assert "旧内容" not in result
        assert "## 🧾 今日事件流" in result

    def test_upsert_new_section(self):
        content = "## 🧾 今日事件流\n\n"
        result = _upsert_section(content, "## 🎨 绘画训练", "目标：360分钟")
        assert "## 🎨 绘画训练" in result
        assert "目标：360分钟" in result
        assert "## 🧾 今日事件流" in result

    def test_upsert_idempotent(self):
        content = "## 🎨 绘画训练\n\n旧内容\n\n## 🧾 今日事件流\n"
        result1 = _upsert_section(content, "## 🎨 绘画训练", "相同内容")
        result2 = _upsert_section(result1, "## 🎨 绘画训练", "相同内容")
        assert result1 == result2

    def test_upsert_preserves_surrounding(self):
        content = "开头\n## 🎨 绘画训练\n\n中间\n## 🧾 今日事件流\n\n结尾"
        result = _upsert_section(content, "## 🎨 绘画训练", "新内容")
        assert result.startswith("开头\n")
        assert "新内容" in result
        assert result.endswith("结尾")


# ── Daily note path tests ─────────────────────────────────────────────────

class TestDailyNotePath:
    def test_path_format(self, settings: Settings):
        date = datetime(2026, 5, 31, tzinfo=ZoneInfo("Asia/Singapore"))
        path = _daily_note_path(settings, date)
        assert "桐一日" in str(path) or "test_vault" in str(path)
        assert path.name == "5.31.md"

    def test_path_single_digit_month(self, settings: Settings):
        date = datetime(2026, 3, 5, tzinfo=ZoneInfo("Asia/Singapore"))
        path = _daily_note_path(settings, date)
        assert path.name == "3.5.md"

    def test_folder_structure(self, settings: Settings):
        date = datetime(2026, 12, 25, tzinfo=ZoneInfo("Asia/Singapore"))
        path = _daily_note_path(settings, date)
        assert "daily" in str(path)
        assert path.name == "12.25.md"


# ── Obsidian writer tests ─────────────────────────────────────────────────

class TestObsidianWriter:
    def test_write_art_plan_creates_file(self, obsidian_writer: ObsidianDailyWriter, temp_vault: Path, settings: Settings):
        settings.obsidian_vault_path = str(temp_vault)
        writer = ObsidianDailyWriter(settings)
        date = datetime(2026, 5, 31, tzinfo=ZoneInfo("Asia/Singapore"))
        blocks = [
            {"title": "🎨 人体结构训练", "start": "2026-05-31T10:00:00+08:00", "end": "2026-05-31T11:30:00+08:00", "duration_min": 90},
        ]
        writer.write_art_plan(target_minutes=360, blocks=blocks, date=date)
        path = _daily_note_path(settings, date)
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "🎨 绘画训练" in content
        assert "360" in content
        assert "🎨 人体结构训练" in content

    def test_write_art_plan_idempotent(self, obsidian_writer: ObsidianDailyWriter, temp_vault: Path, settings: Settings):
        settings.obsidian_vault_path = str(temp_vault)
        writer = ObsidianDailyWriter(settings)
        date = datetime(2026, 5, 31, tzinfo=ZoneInfo("Asia/Singapore"))
        blocks = [{"title": "🎨 人体结构训练", "start": "2026-05-31T10:00:00+08:00", "end": "2026-05-31T11:30:00+08:00", "duration_min": 90}]
        writer.write_art_plan(target_minutes=360, blocks=blocks, date=date)
        content1 = _daily_note_path(settings, date).read_text(encoding="utf-8")
        writer.write_art_plan(target_minutes=360, blocks=blocks, date=date)
        content2 = _daily_note_path(settings, date).read_text(encoding="utf-8")
        assert content1 == content2

    def test_write_event_line(self, obsidian_writer: ObsidianDailyWriter, temp_vault: Path, settings: Settings):
        settings.obsidian_vault_path = str(temp_vault)
        writer = ObsidianDailyWriter(settings)
        date = datetime(2026, 5, 31, tzinfo=ZoneInfo("Asia/Singapore"))
        writer.write_event_line("测试事件", date=date)
        path = _daily_note_path(settings, date)
        content = path.read_text(encoding="utf-8")
        assert "测试事件" in content
        assert "今日事件流" in content


# ── Day type classification tests ──────────────────────────────────────────

class TestDayTypeClassification:
    def test_ideal_day(self):
        from src.domain.art.handlers import classify_day_type
        assert classify_day_type(deadline_pressure=0.1, daily_capacity_hours=10, fatigue_risk=0.1) == "ideal"

    def test_normal_day(self):
        from src.domain.art.handlers import classify_day_type
        assert classify_day_type(deadline_pressure=0.5, daily_capacity_hours=8, fatigue_risk=0.2) == "normal"

    def test_high_pressure_day(self):
        from src.domain.art.handlers import classify_day_type
        assert classify_day_type(deadline_pressure=0.8, daily_capacity_hours=5, fatigue_risk=0.3) == "high_pressure"

    def test_recovery_day_high_fatigue(self):
        from src.domain.art.handlers import classify_day_type
        assert classify_day_type(deadline_pressure=0.2, daily_capacity_hours=8, fatigue_risk=0.8) == "recovery"

    def test_recovery_day_low_mood(self):
        from src.domain.art.handlers import classify_day_type
        assert classify_day_type(mood_score=2) == "recovery"

    def test_target_minutes_by_type(self):
        from src.domain.art.handlers import compute_target_minutes, DAY_TARGETS
        for day_type, expected in DAY_TARGETS.items():
            assert compute_target_minutes(day_type) == expected


# ── Progress parsing tests ──────────────────────────────────────────────────

class TestProgressParsing:
    def test_parse_full_progress(self):
        from src.domain.art.handlers import parse_art_progress
        result = parse_art_progress("完成 画画 2小时 人体速写 12张")
        assert result is not None
        assert result["completed_minutes"] == 120
        assert result["type"] == "人体速写"
        assert result["count"] == 12
        assert not result["resistance"]

    def test_parse_percent(self):
        from src.domain.art.handlers import parse_art_progress
        result = parse_art_progress("画画完成40%")
        assert result is not None
        assert result["percent"] == 40

    def test_parse_resistance(self):
        from src.domain.art.handlers import parse_art_progress
        result = parse_art_progress("画不动")
        assert result is not None
        assert result["resistance"]
        assert result["completed_minutes"] == 0

    def test_parse_skip(self):
        from src.domain.art.handlers import parse_art_progress
        result = parse_art_progress("跳过 画画")
        assert result is not None
        assert result["resistance"]

    def test_no_match(self):
        from src.domain.art.handlers import parse_art_progress
        result = parse_art_progress("今天天气不错")
        assert result is None


# ── Router tests ────────────────────────────────────────────────────────────

class TestArtRouting:
    def test_good_morning_patterns(self):
        from src.interface.telegram.router import parse_message
        assert parse_message("早安", 123) is not None
        assert parse_message("早安", 123).command_type == "art_plan_greeting"

        cmd = parse_message("早~", 456)
        assert cmd is not None and cmd.command_type == "art_plan_greeting"

        cmd = parse_message("早上好", 789)
        assert cmd is not None and cmd.command_type == "art_plan_greeting"

    def test_art_progress_routing(self):
        from src.interface.telegram.router import parse_message
        cmd = parse_message("完成 画画 2小时 人体速写 12张", 123)
        assert cmd is not None
        assert cmd.command_type == "art_progress"

    def test_art_resistance_routing(self):
        from src.interface.telegram.router import parse_message
        cmd = parse_message("画不动", 123)
        assert cmd is not None
        assert cmd.command_type == "art_progress"

    def test_insertion_routing(self):
        from src.interface.telegram.router import parse_message
        cmd = parse_message("下午三点去办卡，大概要一小时", 123)
        assert cmd is not None
        assert cmd.command_type == "art_reality_insertion"

    def test_fitness_routing(self):
        from src.interface.telegram.router import parse_message
        cmd = parse_message("完成 健身 2小时", 123)
        assert cmd is not None
        assert cmd.command_type == "fitness_progress"

    def test_existing_commands_still_work(self):
        from src.interface.telegram.router import parse_message
        assert parse_message("/今天", 123).command_type == "show_today"
        assert parse_message("/状态", 123).command_type == "show_state"
        assert parse_message("今日状态", 123).command_type == "show_today"
        assert parse_message("/饮水 500", 123).command_type == "drink"
        assert parse_message("口述排期", 123).command_type == "verbal_scheduling"

    # ── Combined morning greeting tests ────────────────────────────────

    def test_combined_greeting_routes_to_art_plan(self):
        """Combined '早安' with content still routes to art_plan_greeting."""
        from src.interface.telegram.router import parse_message
        for msg in [
            "早安，今天下午三点健身，晚上画画，心情一般",
            "早安 今天安排：上午上课 下午画画2h 晚上健身 心情6",
            "早安，今天状态差，但想画画4小时",
            "早安 心情5 今天中午吃饭 下午色彩练习",
            "早~ 今天三点健身",
            "早上好，心情不错",
        ]:
            cmd = parse_message(msg, 123)
            assert cmd is not None, f"Failed to parse: {msg}"
            assert cmd.command_type == "art_plan_greeting", f"Wrong type for: {msg}"
            # Combined greetings should have morning_parsed in params
            assert "morning_parsed" in cmd.params, f"Missing morning_parsed for: {msg}"

    def test_pure_greeting_still_pure(self):
        """Pure '早安' without extra content should not have morning_parsed."""
        from src.interface.telegram.router import parse_message
        cmd = parse_message("早安", 123)
        assert cmd is not None
        assert cmd.command_type == "art_plan_greeting"
        assert "morning_parsed" not in cmd.params or not cmd.params.get("morning_parsed")

        cmd = parse_message("早~", 456)
        assert cmd is not None
        assert cmd.command_type == "art_plan_greeting"

        cmd = parse_message("早上好", 789)
        assert cmd is not None
        assert cmd.command_type == "art_plan_greeting"

    def test_parse_mood_number(self):
        """parse_morning_combined extracts numeric mood scores."""
        from src.interface.telegram.router import parse_morning_combined
        result = parse_morning_combined("早安，今天下午健身，心情7")
        assert result["mood_score"] == 7

        result = parse_morning_combined("早安 心情3 今天休息")
        assert result["mood_score"] == 3

        result = parse_morning_combined("早安 今天学习 心情10")
        assert result["mood_score"] == 10

    def test_parse_mood_keyword(self):
        """parse_morning_combined extracts keyword-based mood."""
        from src.interface.telegram.router import parse_morning_combined
        result = parse_morning_combined("早安，今天状态差")
        assert result["mood_score"] == 3

        result = parse_morning_combined("早安 心情不错 下午画画")
        assert result["mood_score"] == 7

        result = parse_morning_combined("早上好，心情很好")
        assert result["mood_score"] == 8

    def test_parse_arrangements(self):
        """parse_morning_combined extracts time-based arrangements."""
        from src.interface.telegram.router import parse_morning_combined
        result = parse_morning_combined("早安，今天下午三点健身，晚上画画")
        assert len(result["arrangements"]) >= 2
        # Should contain some arrangement text
        assert any("健身" in a for a in result["arrangements"])
        assert any("画画" in a for a in result["arrangements"])

    def test_parse_art_target(self):
        """parse_morning_combined extracts art time target."""
        from src.interface.telegram.router import parse_morning_combined
        result = parse_morning_combined("早安 今天画画2小时 心情6")
        assert result["art_minutes"] == 120
        assert result["mood_score"] == 6

        result = parse_morning_combined("早安，但想画画4小时")
        assert result["art_minutes"] == 240

    def test_parse_full_example(self):
        """parse_morning_combined handles the full complex input."""
        from src.interface.telegram.router import parse_morning_combined
        result = parse_morning_combined("早安 今天安排：上午上课 下午画画2h 晚上健身 心情6")
        # Should have parsed mood
        assert result["mood_score"] == 6
        # Should have parsed art target
        assert result["art_minutes"] == 120
        # Should have at least some arrangements
        assert len(result["arrangements"]) >= 1

    def test_parse_no_mood(self):
        """parse_morning_combined returns None mood when no mood present."""
        from src.interface.telegram.router import parse_morning_combined
        result = parse_morning_combined("早安 今天下午健身")
        assert result["mood_score"] is None
        assert len(result["arrangements"]) >= 1

    def test_parse_partial_understood(self):
        """parse_morning_combined does not fail on partial/ambiguous input."""
        from src.interface.telegram.router import parse_morning_combined
        # Just "早安" with no trailing content — content should be minimal
        result = parse_morning_combined("早安")
        # If no trailing content, result is still valid (empty arrays, null mood)
        # Should not crash
        assert result is not None

    # ── Regression: morning greeting quality ───────────────────────────

    def test_regression_no_greeting_in_arrangements(self):
        """'早安' prefix must NOT leak into arrangements."""
        from src.interface.telegram.router import parse_morning_combined

        # Case 1: comma-delimited with keyword mood
        r1 = parse_morning_combined("早安，今天下午三点健身，晚上画画，心情一般")
        assert r1["mood_score"] == 5, f"expected mood=5, got {r1['mood_score']}"
        assert not any("早安" in a for a in r1["arrangements"]), \
            f"arrangements contain greeting: {r1['arrangements']}"
        assert any("下午三点健身" in a for a in r1["arrangements"]), \
            f"missing 下午三点健身: {r1['arrangements']}"
        assert any("晚上画画" in a for a in r1["arrangements"]), \
            f"missing 晚上画画: {r1['arrangements']}"
        assert not any("心情" in a for a in r1["arrangements"]), \
            f"mood text leaked into arrangements: {r1['arrangements']}"

        # Case 2: space-delimited with "今天安排：" prefix and art target
        r2 = parse_morning_combined("早安 今天安排：上午上课 下午画画2h 晚上健身 心情6")
        assert r2["mood_score"] == 6, f"expected mood=6, got {r2['mood_score']}"
        assert r2["art_minutes"] == 120, f"expected art=120, got {r2['art_minutes']}"
        assert not any("早安" in a for a in r2["arrangements"]), \
            f"arrangements contain greeting: {r2['arrangements']}"
        assert not any("今天安排" in a for a in r2["arrangements"]), \
            f"arrangements contain prefix: {r2['arrangements']}"
        assert any("上午上课" in a for a in r2["arrangements"]), \
            f"missing 上午上课: {r2['arrangements']}"
        assert any("晚上健身" in a for a in r2["arrangements"]), \
            f"missing 晚上健身: {r2['arrangements']}"
        assert any("下午" in a and "画画" in a for a in r2["arrangements"]), \
            f"missing afternoon art segment: {r2['arrangements']}"

        # Case 3: keyword mood + art duration, no filler
        r3 = parse_morning_combined("早安，今天状态差，但想画画4小时")
        assert r3["mood_score"] == 3, f"expected mood=3, got {r3['mood_score']}"
        assert r3["art_minutes"] == 240, f"expected art=240, got {r3['art_minutes']}"
        assert not any("但想" in a for a in r3["arrangements"]), \
            f"arrangements contain filler: {r3['arrangements']}"
        # arrangement_text must NOT contain '早安'
        assert "早安" not in r3.get("arrangement_text", ""), \
            f"arrangement_text leaked greeting: {r3.get('arrangement_text')}"

        # Case 4: numeric mood + time activities
        r4 = parse_morning_combined("早安 心情5 今天中午吃饭 下午色彩练习")
        assert r4["mood_score"] == 5, f"expected mood=5, got {r4['mood_score']}"
        assert not any("早安" in a for a in r4["arrangements"]), \
            f"arrangements contain greeting: {r4['arrangements']}"
        assert any("中午吃饭" in a for a in r4["arrangements"]), \
            f"missing 中午吃饭: {r4['arrangements']}"
        assert any("下午色彩练习" in a for a in r4["arrangements"]), \
            f"missing 下午色彩练习: {r4['arrangements']}"


# ── StateEngine art tests ──────────────────────────────────────────────────

class TestStateEngineArt:
    @pytest.mark.asyncio
    async def test_art_plan_stored(self, state_engine: StateEngine):
        event = Event(
            event_type=EventType.ART_PLAN_CREATED,
            aggregate_id="art_today",
            aggregate_type=AggregateType.ART,
            payload={
                "date": "2026-05-31",
                "day_type": "ideal",
                "target_minutes": 360,
                "blocks": [{"title": "🎨 人体结构训练", "duration_min": 90}],
            },
        )
        await state_engine.apply(event)
        art_state = state_engine.get_view("art", "today")
        assert art_state["plan"]["day_type"] == "ideal"
        assert art_state["plan"]["target_minutes"] == 360
        assert len(art_state["plan"]["blocks"]) == 1

    @pytest.mark.asyncio
    async def test_art_progress_stored(self, state_engine: StateEngine):
        # First create plan
        plan_event = Event(
            event_type=EventType.ART_PLAN_CREATED,
            aggregate_id="art_today",
            aggregate_type=AggregateType.ART,
            payload={"date": "2026-05-31", "day_type": "normal", "target_minutes": 210, "blocks": []},
        )
        await state_engine.apply(plan_event)

        # Then record progress
        progress_event = Event(
            event_type=EventType.ART_PROGRESS_RECORDED,
            aggregate_id="art_today",
            aggregate_type=AggregateType.ART,
            payload={"completed_minutes": 90, "type": "人体速写", "count": 12, "resistance": False},
        )
        await state_engine.apply(progress_event)
        art_state = state_engine.get_view("art", "today")
        assert art_state["progress"]["completed_minutes"] == 90
        assert len(art_state["progress"]["sessions"]) == 1

    @pytest.mark.asyncio
    async def test_rebalance_recorded(self, state_engine: StateEngine):
        event = Event(
            event_type=EventType.ART_PLAN_REBALANCED,
            aggregate_id="art_today",
            aggregate_type=AggregateType.ART,
            payload={"reason": "reality_inserted"},
        )
        await state_engine.apply(event)
        art_state = state_engine.get_view("art", "today")
        assert len(art_state["rebalances"]) == 1
        assert art_state["rebalances"][0]["reason"] == "reality_inserted"


# ── Art planner handler tests ──────────────────────────────────────────────

class TestArtPlanner:
    @pytest.mark.asyncio
    async def test_plan_requested_creates_plan(self):
        from src.domain.art.handlers import handle_art_plan_requested

        event = Event(
            event_type=EventType.ART_PLAN_REQUESTED,
            aggregate_id="art_today",
            aggregate_type=AggregateType.ART,
            payload={
                "blocks": [],
                "day_type": "normal",
                "target_minutes": 210,
                "date_override": "2026-05-31",
            },
        )
        results = await handle_art_plan_requested(event)
        assert len(results) == 1
        assert results[0].event_type == EventType.ART_PLAN_CREATED
        payload = results[0].payload
        assert payload["target_minutes"] == 210
        assert payload["day_type"] == "normal"

    @pytest.mark.asyncio
    async def test_plan_with_busy_blocks_avoids_them(self):
        from src.domain.art.handlers import handle_art_plan_requested

        now = datetime(2026, 5, 31, 10, 0, tzinfo=ZoneInfo("Asia/Singapore"))
        busy_block = {
            "block_id": "class1",
            "source": "jwxt",
            "block_type": "class_lecture",
            "start": now.replace(hour=14).isoformat(),
            "end": now.replace(hour=16).isoformat(),
            "title": "重要课程",
        }
        event = Event(
            event_type=EventType.ART_PLAN_REQUESTED,
            aggregate_id="art_today",
            aggregate_type=AggregateType.ART,
            payload={
                "blocks": [busy_block],
                "day_type": "ideal",
                "target_minutes": 120,
                "date_override": "2026-05-31",
            },
        )
        results = await handle_art_plan_requested(event)
        plan = results[0].payload
        # Art blocks should not overlap with 14:00-16:00
        for b in plan.get("blocks", []):
            b_start = datetime.fromisoformat(b["start"])
            b_end = datetime.fromisoformat(b["end"])
            busy_start = now.replace(hour=14)
            busy_end = now.replace(hour=16)
            # Assert no overlap
            assert not (b_start < busy_end and busy_start < b_end)

    @pytest.mark.asyncio
    async def test_plan_avoids_jwxt_17_18(self):
        """JWXT class at 17:00-18:00 → art blocks must not overlap."""
        from src.domain.art.handlers import handle_art_plan_requested

        now = datetime(2026, 5, 31, 8, 0, tzinfo=ZoneInfo("Asia/Singapore"))
        blocks = [
            {
                "block_id": "j1",
                "source": "jwxt",
                "block_type": "class_lecture",
                "start": now.replace(hour=17).isoformat(),
                "end": now.replace(hour=18).isoformat(),
                "title": "晚课",
            },
        ]
        event = Event(
            event_type=EventType.ART_PLAN_REQUESTED,
            aggregate_id="art_today",
            aggregate_type=AggregateType.ART,
            payload={
                "blocks": blocks,
                "day_type": "ideal",
                "target_minutes": 210,
                "date_override": "2026-05-31",
            },
        )
        results = await handle_art_plan_requested(event)
        plan = results[0].payload
        for b in plan.get("blocks", []):
            b_start = datetime.fromisoformat(b["start"])
            b_end = datetime.fromisoformat(b["end"])
            assert not (b_start < now.replace(hour=18) and now.replace(hour=17) < b_end), \
                f"Block {b['title']} {b['start']}-{b['end']} overlaps JWXT 17:00-18:00"

    @pytest.mark.asyncio
    async def test_plan_avoids_google_calendar_19_20(self):
        """Google Calendar event at 19:00-20:00 → art blocks must not overlap."""
        from src.domain.art.handlers import handle_art_plan_requested

        now = datetime(2026, 5, 31, 8, 0, tzinfo=ZoneInfo("Asia/Singapore"))
        blocks = [
            {
                "block_id": "gc1",
                "source": "google_calendar",
                "block_type": "calendar_event",
                "start": now.replace(hour=19).isoformat(),
                "end": now.replace(hour=20).isoformat(),
                "title": "晚上活动",
            },
        ]
        event = Event(
            event_type=EventType.ART_PLAN_REQUESTED,
            aggregate_id="art_today",
            aggregate_type=AggregateType.ART,
            payload={
                "blocks": blocks,
                "day_type": "ideal",
                "target_minutes": 360,
                "date_override": "2026-05-31",
            },
        )
        results = await handle_art_plan_requested(event)
        plan = results[0].payload
        for b in plan.get("blocks", []):
            b_start = datetime.fromisoformat(b["start"])
            b_end = datetime.fromisoformat(b["end"])
            assert not (b_start < now.replace(hour=20) and now.replace(hour=19) < b_end), \
                f"Block {b['title']} overlaps Google Calendar 19:00-20:00"

    @pytest.mark.asyncio
    async def test_unscheduled_minutes_when_free_window_too_small(self):
        """Target 6h but only 2h free → unscheduled_minutes in payload."""
        from src.domain.art.handlers import handle_art_plan_requested

        now = datetime(2026, 5, 31, 8, 0, tzinfo=ZoneInfo("Asia/Singapore"))
        # 8:00-10:00 class, free window from 10:00-14:00 = 4h
        # Wait, we need only 2h free window.
        # 8:00-12:00 class free window only after 18:00
        blocks = [
            {"block_id": "j1", "source": "jwxt", "block_type": "class_lecture",
             "start": now.replace(hour=8).isoformat(), "end": now.replace(hour=9).isoformat(),
             "title": "c1"},
            {"block_id": "j2", "source": "jwxt", "block_type": "class_lecture",
             "start": now.replace(hour=9, minute=30).isoformat(), "end": now.replace(hour=11).isoformat(),
             "title": "c2"},
            {"block_id": "j3", "source": "jwxt", "block_type": "class_lecture",
             "start": now.replace(hour=12).isoformat(), "end": now.replace(hour=14).isoformat(),
             "title": "c3"},
            {"block_id": "j4", "source": "jwxt", "block_type": "class_lecture",
             "start": now.replace(hour=14, minute=30).isoformat(), "end": now.replace(hour=16).isoformat(),
             "title": "c4"},
            {"block_id": "j5", "source": "jwxt", "block_type": "class_lecture",
             "start": now.replace(hour=17).isoformat(), "end": now.replace(hour=19).isoformat(),
             "title": "c5"},
        ]
        event = Event(
            event_type=EventType.ART_PLAN_REQUESTED,
            aggregate_id="art_today",
            aggregate_type=AggregateType.ART,
            payload={
                "blocks": blocks,
                "day_type": "ideal",
                "target_minutes": 360,  # 6h
                "date_override": "2026-05-31",
            },
        )
        results = await handle_art_plan_requested(event)
        plan = results[0].payload
        # Free windows: 6:00-8:00 (but effective_start is 8:00 since today), 11:00-12:00, 16:00-17:00, 19:00-23:59
        # After 5-min buffer and 30-min min, the usable windows might be:
        # 11:05-11:55 (50 min), 16:05-16:55 (50 min), 19:05-23:54 (close to 5h)
        # Total planned should be less than 360, and unscheduled > 0
        assert plan["unscheduled_minutes"] > 0, \
            f"Expected unscheduled_minutes > 0, got {plan['unscheduled_minutes']}"
        total_planned = plan["total_planned_minutes"]
        assert total_planned < 360, \
            f"Expected total_planned < 360, got {total_planned}"
        assert plan["unscheduled_minutes"] == 360 - total_planned

    @pytest.mark.asyncio
    async def test_replan_after_insertion_avoids_new_blocks(self):
        """replan after an insertion does not place art blocks on the inserted block."""
        from src.domain.art.handlers import handle_art_plan_requested

        now = datetime(2026, 5, 31, 12, 0, tzinfo=ZoneInfo("Asia/Singapore"))
        # Morning class 8:00-10:00, inserted event 14:00-15:00
        blocks = [
            {"block_id": "m1", "source": "jwxt", "block_type": "class_lecture",
             "start": now.replace(hour=8).isoformat(), "end": now.replace(hour=10).isoformat(),
             "title": "Morning class"},
            {"block_id": "inserted1", "source": "manual", "block_type": "personal_task_block",
             "start": now.replace(hour=14).isoformat(), "end": now.replace(hour=15).isoformat(),
             "title": "临时办卡", "metadata": {"source": "reality_insertion"}},
        ]
        event = Event(
            event_type=EventType.ART_PLAN_REQUESTED,
            aggregate_id="art_today",
            aggregate_type=AggregateType.ART,
            payload={
                "blocks": blocks,
                "day_type": "ideal",
                "target_minutes": 210,
                "date_override": "2026-05-31",
            },
        )
        results = await handle_art_plan_requested(event)
        plan = results[0].payload
        for b in plan.get("blocks", []):
            b_start = datetime.fromisoformat(b["start"])
            b_end = datetime.fromisoformat(b["end"])
            # Should not overlap 14:00-15:00
            assert not (b_start < now.replace(hour=15) and now.replace(hour=14) < b_end), \
                f"Block {b['title']} overlaps inserted 14:00-15:00"


# ── Calendar executor managed art block tests ─────────────────────────────

class TestManagedArtBlocks:
    @pytest.mark.asyncio
    async def test_create_managed_block_mock(self):
        from src.executor.google_calendar.executor import GoogleCalendarExecutor
        executor = GoogleCalendarExecutor(use_mock=True)
        now = datetime.now(timezone.utc)
        result = await executor.create_managed_art_block(
            title="🎨 人体结构训练",
            start=now,
            end=now + timedelta(minutes=90),
            plan_id="test-plan",
            rationale="日常训练",
            target_minutes=90,
        )
        assert result["ok"] is True
        assert "mock" in result["event_id"]

    @pytest.mark.asyncio
    async def test_list_managed_blocks_mock(self):
        from src.executor.google_calendar.executor import GoogleCalendarExecutor
        executor = GoogleCalendarExecutor(use_mock=True)
        now = datetime.now(timezone.utc)
        blocks = await executor.list_managed_art_blocks(now, now + timedelta(days=7))
        assert blocks == []

    @pytest.mark.asyncio
    async def test_delete_managed_block_mock(self):
        from src.executor.google_calendar.executor import GoogleCalendarExecutor
        executor = GoogleCalendarExecutor(use_mock=True)
        result = await executor.delete_managed_art_block("mock-id")
        assert result["ok"] is True

    def test_art_block_description_includes_markers(self):
        from src.executor.google_calendar.executor import GoogleCalendarExecutor
        executor = GoogleCalendarExecutor(use_mock=True)
        desc = executor._art_block_description(plan_id="plan-123", rationale="保持练习节奏", target_minutes=90)
        assert "Cognitive OS / Art Planner" in desc
        assert "plan-123" in desc
        assert "90" in desc

    # ── Calendar write guard tests ──────────────────────────────────────

    def test_detect_overlap_rejects_overlapping_art_event(self):
        """Calendar write guard: detect_overlap rejects overlapping art event."""
        from src.domain.planning.time_windows import load_busy_intervals, detect_overlap, art_exclude_filter

        tz = ZoneInfo("Asia/Singapore")
        day_start = datetime(2026, 6, 1, 6, 0, tzinfo=tz)
        day_end = datetime(2026, 6, 1, 23, 0, tzinfo=tz)

        # Existing busy blocks: JWXT class 14:00-16:00, GC event 19:00-20:00
        busy_blocks = [
            {"block_id": "j1", "source": "jwxt", "block_type": "class_lecture",
             "start": "2026-06-01T14:00:00+08:00", "end": "2026-06-01T16:00:00+08:00", "title": "Class"},
            {"block_id": "g1", "source": "google_calendar", "block_type": "calendar_event",
             "start": "2026-06-01T19:00:00+08:00", "end": "2026-06-01T20:00:00+08:00", "title": "Event"},
        ]
        busy = load_busy_intervals(busy_blocks, day_start, day_end, exclude_filter=art_exclude_filter)

        # Art block overlapping JWXT → should be rejected
        assert detect_overlap(
            datetime(2026, 6, 1, 15, 0, tzinfo=tz),
            datetime(2026, 6, 1, 16, 30, tzinfo=tz),
            busy,
        ), "art block overlapping JWXT should be detected"

        # Art block overlapping GC event → should be rejected
        assert detect_overlap(
            datetime(2026, 6, 1, 19, 30, tzinfo=tz),
            datetime(2026, 6, 1, 20, 30, tzinfo=tz),
            busy,
        ), "art block overlapping GC event should be detected"

        # Art block in free window → should pass
        assert not detect_overlap(
            datetime(2026, 6, 1, 10, 0, tzinfo=tz),
            datetime(2026, 6, 1, 12, 0, tzinfo=tz),
            busy,
        ), "art block in free window should not be rejected"


# ── Event type registration tests ──────────────────────────────────────────

class TestEventTypes:
    def test_art_event_types_exist(self):
        assert EventType.ART_PLAN_REQUESTED == "art.plan.requested"
        assert EventType.ART_PLAN_CREATED == "art.plan.created"
        assert EventType.ART_PROGRESS_RECORDED == "art.progress.recorded"
        assert EventType.ART_PLAN_REBALANCED == "art.plan.rebalanced"
        assert EventType.ART_VIBE_CODE_WARNING == "art.vibe_code.warning"

    def test_aggregate_type_exists(self):
        assert AggregateType.ART == "art"
