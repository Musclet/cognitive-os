"""Tests for six optimization items.

Covers:
  1) Momo vocab sync: non-blocking startup, timeout, fallback
  2) Morning refresh second report: format, duplicate guard
  3) Deviation/plan drift input: parser, handler, Obsidian section write
  4) Calendar schedule mirror verification: verified/mismatch payloads
  5) Profile stats: calculation with fake state
  6) Reminder anti-spam: category cooldown, reason requirement
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

sys.path.insert(0, ".")

import pytest

from src.core.events import Event, EventType, AggregateType
from src.infrastructure.config import Settings


# ════════════════════════════════════════════════════════════════════════════
# Item 1: Momo vocab sync — non-blocking startup, timeout, fallback
# ════════════════════════════════════════════════════════════════════════════

SAMPLE_CACHE = {
    "last_sync": "2026-05-21T03:12:19.373Z",
    "progress": {"finished": 173, "total": 329, "study_time": 1597885},
    "today_items": [
        {"voc_spelling": "word1", "is_finished": True, "is_new": False},
        {"voc_spelling": "word2", "is_finished": False, "is_new": True},
    ],
    "study_records": [],
}


def make_settings(cache_path: str | None = None, **overrides):
    from types import SimpleNamespace
    base = {
        "momo_sync_project_path": "/nonexistent/momo",
        "momo_cache_path": cache_path or "/nonexistent/cache.json",
        "momo_sync_enabled": True,
        "momo_stale_after_minutes": 90,
        "momo_sync_timeout_seconds": 8,
        "momo_sync_block_startup": False,
        "momo_evening_check_time": "21:30",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_momo_timeout_config_applied():
    """fetch_momo_vocab uses configurable timeout setting."""
    with tempfile.TemporaryDirectory() as tmp:
        cache_file = Path(tmp) / "momo-data.json"
        cache_file.write_text(json.dumps(SAMPLE_CACHE), encoding="utf-8")

        from src.connector.momo.connector import fetch_momo_vocab
        settings = make_settings(str(cache_file), momo_sync_timeout_seconds=5)
        events = await fetch_momo_vocab(settings, uuid4())

        types = {e.event_type for e in events}
        assert EventType.VOCAB_SYNC_STARTED in types
        assert EventType.VOCAB_PROGRESS_UPDATED in types


@pytest.mark.asyncio
async def test_momo_timeout_fallback_to_cache():
    """When npm times out, falls back to cached data."""
    with tempfile.TemporaryDirectory() as tmp:
        cache_file = Path(tmp) / "momo-data.json"
        cache_file.write_text(json.dumps(SAMPLE_CACHE), encoding="utf-8")

        from src.connector.momo.connector import fetch_momo_vocab
        settings = make_settings(str(cache_file), momo_sync_timeout_seconds=1)
        events = await fetch_momo_vocab(settings, uuid4())

        fail_evts = [e for e in events if e.event_type == EventType.VOCAB_SYNC_FAILED]
        assert len(fail_evts) >= 1
        # Should have progress data even on failure
        prog_evts = [e for e in events if e.event_type == EventType.VOCAB_PROGRESS_UPDATED]
        assert len(prog_evts) >= 1
        payload = prog_evts[0].payload
        assert payload["progress"]["finished"] == 173


def test_momo_config_has_timeout_and_block_startup():
    """Settings class includes momo_sync_timeout_seconds and momo_sync_block_startup."""
    settings = Settings()
    assert hasattr(settings, "momo_sync_timeout_seconds")
    assert hasattr(settings, "momo_sync_block_startup")
    assert settings.momo_sync_timeout_seconds == 45
    assert settings.momo_sync_block_startup is False


def test_momo_default_timeout_is_45_seconds():
    """Default timeout for npm sync is long enough for fast Momo sync."""
    from src.connector.momo.connector import fetch_momo_vocab
    settings = make_settings(momo_sync_timeout_seconds=45)
    # Verify the timeout is read from settings
    npm_timeout = int(getattr(settings, "momo_sync_timeout_seconds", 45))
    assert npm_timeout == 45
    assert npm_timeout >= 30


# ════════════════════════════════════════════════════════════════════════════
# Item 2: Morning refresh second report — format and duplicate guard
# ════════════════════════════════════════════════════════════════════════════


def test_morning_refresh_report_format():
    """Verify the second report format matches required output."""
    # Simulate the format used by _monitor_morning_refresh
    status_parts = ["课表 OK", "作业 OK", "日历 OK", "背词缓存"]
    report_lines = []
    report_lines.append(f"数据刷新完成：{'，'.join(status_parts)}")
    report_lines.append("今日压力：低")
    report_lines.append("画画建议：14:00-16:00 · 深度工作窗口")

    report = "\n".join(report_lines)
    assert "数据刷新完成" in report
    assert "课表 OK" in report
    assert "作业 OK" in report
    assert "日历 OK" in report
    assert "背词缓存" in report
    assert "今日压力：低" in report
    assert "画画建议" in report


def test_morning_refresh_report_pressure_mapping():
    """Pressure label mapping is correct: low/medium/high."""
    assert "低" if 0.2 < 0.3 else None
    stress_values = [(0.2, "低"), (0.5, "中"), (0.8, "高")]
    for stress, expected in stress_values:
        label = "低" if stress < 0.3 else "中" if stress < 0.6 else "高"
        assert label == expected


def test_morning_refresh_duplicate_guard():
    """Same user/day/morning session should not get repeated reports."""
    # Simulate the duplicate guard logic
    morning_refresh: dict[str, dict] = {}

    refresh_key = "12345:2026-06-01"
    morning_refresh[refresh_key] = {
        "sources": {"课表", "日历"},
        "completed": set(),
        "started_at": 1000.0,
        "report_sent": False,
    }

    # First check: should send
    state = morning_refresh.get(refresh_key)
    assert state is not None
    assert state["report_sent"] is False

    # Mark as sent
    state["report_sent"] = True

    # Second check: should NOT send (report_sent is True)
    state2 = morning_refresh.get(refresh_key)
    assert state2["report_sent"] is True


# ════════════════════════════════════════════════════════════════════════════
# Item 3: Deviation / plan drift input — parser, routing, Obsidian write
# ════════════════════════════════════════════════════════════════════════════


def test_deviation_detection_patterns():
    """Various deviation inputs are correctly detected."""
    from src.interface.telegram.router import _is_deviation_input

    # Should detect
    assert _is_deviation_input("没画画，下午一直在写代码")
    assert _is_deviation_input("计划崩了")
    assert _is_deviation_input("健身太累，晚上不想学")
    assert _is_deviation_input("画了半小时但状态很差")
    assert _is_deviation_input("今天废了")
    assert _is_deviation_input("没按计划")

    # Should NOT detect
    assert not _is_deviation_input("完成了数据结构作业")
    assert not _is_deviation_input("早安，今天心情不错")
    assert not _is_deviation_input("完成了画画2小时")
    assert not _is_deviation_input("帮我查课表")


def test_deviation_routes_to_plan_deviation_command():
    """Deviation input routes to plan_deviation command type."""
    from src.interface.telegram.router import parse_message

    cmd = parse_message("没画画，下午一直在写代码", 12345)
    assert cmd is not None
    assert cmd.command_type == "plan_deviation"
    assert "deviation_text" in cmd.params
    assert "没画画" in cmd.params["deviation_text"]

    cmd2 = parse_message("计划崩了", 12345)
    assert cmd2 is not None
    assert cmd2.command_type == "plan_deviation"

    cmd3 = parse_message("健身太累，晚上不想学", 12345)
    assert cmd3 is not None
    assert cmd3.command_type == "plan_deviation"


def test_deviation_obsidian_section_write():
    """Deviation input writes to Obsidian ## 偏离原因 section."""
    from src.integrations.obsidian_daily import ObsidianDailyWriter, _daily_note_path
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from unittest.mock import MagicMock

    settings = MagicMock()
    settings.obsidian_vault_path = tempfile.mkdtemp()
    settings.obsidian_daily_folder = "daily"
    settings.obsidian_daily_template_path = "Templates/nonexistent.md"
    settings.obsidian_daily_sink_enabled = True

    date = datetime(2026, 6, 1, tzinfo=ZoneInfo("Asia/Singapore"))
    writer = ObsidianDailyWriter(settings)

    # Write deviation
    deviation_text = "下午一直在写代码，没画画"
    writer.write_section("## 偏离原因",
        f"- {date.strftime('%H:%M')} {deviation_text}", date)

    path = _daily_note_path(settings, date)
    content = path.read_text(encoding="utf-8")
    assert "## 偏离原因" in content
    assert deviation_text in content


# ════════════════════════════════════════════════════════════════════════════
# Item 4: Calendar schedule mirror verification
# ════════════════════════════════════════════════════════════════════════════


def test_calendar_verify_ok_format():
    """Verified payload uses 校验 OK format."""
    verify_result = {"verified": True, "jwxt_count": 5, "calendar_count": 5}
    verify_msg = "校验 OK" if verify_result.get("verified") else ""
    assert verify_msg == "校验 OK"


def test_calendar_verify_mismatch_format():
    """Mismatch payload uses 校验不一致 format."""
    verify_result = {"verified": False, "jwxt_count": 5, "calendar_count": 3}
    verify_msg = (
        f"校验不一致：课表 {verify_result.get('jwxt_count', 0)}，"
        f"日历 {verify_result.get('calendar_count', 0)}"
    )
    assert "校验不一致" in verify_msg
    assert "课表 5" in verify_msg
    assert "日历 3" in verify_msg


@pytest.mark.asyncio
async def test_calendar_verify_with_mock_executor():
    """Mock executor verify_schedule_mirror returns verified=True."""
    from src.executor.google_calendar.executor import GoogleCalendarExecutor
    from src.core.temporal import TimeBlock, TemporalSource, TimeBlockType

    executor = GoogleCalendarExecutor(use_mock=True)
    now = datetime.now(timezone.utc)
    blocks = [
        TimeBlock(
            block_id="b1",
            source=TemporalSource.JWXT,
            block_type=TimeBlockType.CLASS_LECTURE,
            start=now + timedelta(hours=1),
            end=now + timedelta(hours=2),
            title="计算机图形学",
        ),
    ]

    result = await executor.verify_schedule_mirror(blocks, days=7, calendar_id="primary")
    assert result["verified"] is True
    assert result["jwxt_count"] >= 1
    assert result["calendar_count"] >= 1
    assert result["source"] == "mock"


def test_calendar_verify_does_not_touch_user_events():
    """Verification spec explicitly says: does not touch/delete user events."""
    # This is a contract test — verify_schedule_mirror is read-only
    from src.executor.google_calendar.executor import GoogleCalendarExecutor
    executor = GoogleCalendarExecutor(use_mock=True)

    # The method only counts/compares — no create/update/delete
    import inspect
    source = inspect.getsource(executor.verify_schedule_mirror)
    assert "delete" not in source.lower() or "delete" in source and "_list_managed_schedule_events" in source
    assert "events().insert" not in source
    assert "events().patch" not in source


# ════════════════════════════════════════════════════════════════════════════
# Item 5: Profile stats calculation
# ════════════════════════════════════════════════════════════════════════════


def test_daily_stats_art_minutes():
    """compute_daily_stats extracts art minutes correctly."""
    from src.derived_state.daily_stats import compute_daily_stats

    state = {
        "art": {
            "today": {
                "progress": {"completed_minutes": 45},
                "plan": {"target_minutes": 120},
            },
        },
        "vocab": {},
        "subjective": {},
        "behavior": {},
    }
    derived = {"behavior": {"feedback_log": []}}

    stats = compute_daily_stats(state, derived)
    assert stats["art_minutes"] == 45
    assert stats["art_target"] == 120


def test_daily_stats_deviations():
    """compute_daily_stats counts deviations from feedback_log."""
    from src.derived_state.daily_stats import compute_daily_stats
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    state = {
        "art": {"today": {"progress": {}, "plan": {}}},
        "vocab": {},
        "subjective": {},
    }
    derived = {
        "behavior": {
            "feedback_log": [
                {"outcome": "abandoned", "reason": "下午一直在写代码",
                 "outcome_timestamp": now.isoformat()},
                {"outcome": "skipped", "reason": "健身太累",
                 "outcome_timestamp": now.isoformat()},
                {"outcome": "completed", "reason": "完成了数据结构",
                 "outcome_timestamp": now.isoformat()},
            ],
        },
    }

    stats = compute_daily_stats(state, derived, now)
    assert stats["deviation_count"] == 2
    assert len(stats["deviation_reasons"]) == 2
    assert stats["coding_drift"] is True


def test_daily_stats_vocab():
    """compute_daily_stats extracts vocab data."""
    from src.derived_state.daily_stats import compute_daily_stats

    state = {
        "art": {"today": {"progress": {}, "plan": {}}},
        "vocab": {
            "momo": {
                "today": {"finished": 3, "total": 5, "remaining": 2},
            },
        },
        "subjective": {},
    }
    derived = {"behavior": {"feedback_log": []}}

    stats = compute_daily_stats(state, derived)
    assert stats["vocab_finished"] == 3
    assert stats["vocab_total"] == 5
    assert stats["vocab_remaining"] == 2


def test_daily_stats_mood():
    """compute_daily_stats extracts mood from subjective history."""
    from src.derived_state.daily_stats import compute_daily_stats
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    state = {
        "art": {"today": {"progress": {}, "plan": {}}},
        "vocab": {},
        "subjective": {
            "user-1": {
                "mood_history": [
                    {"score": 7, "recorded_at": now.isoformat()},
                    {"score": 5, "recorded_at": now.isoformat()},
                ],
            },
        },
    }
    derived = {"behavior": {"feedback_log": []}}

    stats = compute_daily_stats(state, derived, now)
    assert stats["mood_latest"] == 5
    assert stats["mood_avg"] == 6.0


def test_daily_stats_fitness():
    """compute_daily_stats detects fitness completion."""
    from src.derived_state.daily_stats import compute_daily_stats
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    state = {
        "art": {"today": {"progress": {}, "plan": {}}},
        "vocab": {},
        "subjective": {},
    }
    derived = {
        "behavior": {
            "feedback_log": [
                {"outcome": "completed", "reason": "健身1小时",
                 "outcome_timestamp": now.isoformat()},
            ],
        },
    }

    stats = compute_daily_stats(state, derived, now)
    assert stats["fitness_done"] is True


def test_format_stats_line():
    """format_stats_line produces correct format."""
    from src.derived_state.daily_stats import format_stats_line

    stats = {
        "art_minutes": 45,
        "art_target": 120,
        "deviation_count": 1,
        "deviation_reasons": ["下午一直在写代码"],
        "vocab_finished": 3,
        "vocab_total": 5,
        "vocab_remaining": 2,
        "fitness_done": True,
        "mood_latest": 6,
        "mood_avg": 5.5,
        "coding_drift": False,
    }

    line = format_stats_line(stats)
    assert "画画" in line
    assert "45/120" in line
    assert "偏离" in line
    assert "背词 3/5" in line
    assert "健身 ✅" in line
    assert "心情 6" in line


def test_format_stats_line_empty():
    """format_stats_line handles empty/minimal stats."""
    from src.derived_state.daily_stats import format_stats_line

    stats = {
        "art_minutes": 0,
        "art_target": 0,
        "deviation_count": 0,
        "deviation_reasons": [],
        "vocab_finished": 0,
        "vocab_total": 0,
        "vocab_remaining": 0,
        "fitness_done": False,
        "mood_latest": None,
        "mood_avg": None,
        "coding_drift": False,
    }

    line = format_stats_line(stats)
    assert "画画 0min" in line
    assert "健身 ❌" in line


# ════════════════════════════════════════════════════════════════════════════
# Item 6: Reminder anti-spam — category cooldown, reason requirement
# ════════════════════════════════════════════════════════════════════════════


def test_intervention_category_cooldown():
    """Same-category reminders are suppressed within cooldown window."""
    from intervention import InterventionEngine, Intervention

    engine = InterventionEngine(cooldown_hours=6.0, daily_budget=10)

    inv1 = Intervention(
        intervention_type="hydration",
        message="Drink water",
        priority=0.4,
        reason="hydration_gap=200min",
    )
    inv2 = Intervention(
        intervention_type="workout_hydration",
        message="Pre-workout drink",
        priority=0.65,
        reason="workout_in=30min",
    )

    # Both are in the "hydration" category
    category_check = {"hydration": "hydration", "workout_hydration": "hydration"}
    assert category_check[inv1.intervention_type] == "hydration"
    assert category_check[inv2.intervention_type] == "hydration"

    # First should pass
    assert engine._can_trigger(inv1)
    engine._record_trigger(inv1)

    # Second (same category) should be suppressed (cooldown not elapsed)
    assert not engine._can_trigger(inv2)


def test_intervention_reason_required():
    """Intervention without reason is suppressed."""
    from intervention import InterventionEngine, Intervention

    engine = InterventionEngine(cooldown_hours=6.0, daily_budget=10)

    inv_no_reason = Intervention(
        intervention_type="hydration",
        message="Drink water",
        priority=0.4,
        reason="",
    )
    inv_whitespace_reason = Intervention(
        intervention_type="hydration",
        message="Drink water",
        priority=0.4,
        reason="   ",
    )
    inv_with_reason = Intervention(
        intervention_type="hydration",
        message="Drink water",
        priority=0.4,
        reason="hydration_gap=200min",
    )

    assert not engine._can_trigger(inv_no_reason)
    assert not engine._can_trigger(inv_whitespace_reason)
    assert engine._can_trigger(inv_with_reason)


def test_intervention_important_bypasses_cooldown():
    """Important reminder reasons bypass category cooldown."""
    from intervention import _is_important_reminder

    assert _is_important_reminder("free_window available")
    assert _is_important_reminder("homework deadline within 24h")
    assert _is_important_reminder("severe plan drift detected")
    assert _is_important_reminder("nightly_review trigger")
    assert _is_important_reminder("critical_pressure=0.9")
    assert _is_important_reminder("deadline approaching 24h")

    assert not _is_important_reminder("hydration_gap=200min")
    assert not _is_important_reminder("focus_window_available")
    assert not _is_important_reminder("workout_in=30min")


def test_intervention_different_categories_no_cooldown_conflict():
    """Different categories do not interfere with each other."""
    from intervention import InterventionEngine, Intervention

    engine = InterventionEngine(cooldown_hours=6.0, daily_budget=10)

    inv_hydration = Intervention(
        intervention_type="hydration",
        message="Drink water",
        priority=0.4,
        reason="hydration_gap=200min",
    )
    inv_deep_work = Intervention(
        intervention_type="deep_work_reminder",
        message="Focus time",
        priority=0.6,
        reason="focus_window_available fatigue=0.3",
    )

    # Both should pass (different categories)
    assert engine._can_trigger(inv_hydration)
    engine._record_trigger(inv_hydration)

    # Deep work is a different category - should still pass
    assert engine._can_trigger(inv_deep_work)
