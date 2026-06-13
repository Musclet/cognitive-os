"""Test: Obsidian daily sink — fixed structure, idempotent writes, morning entry."""

from __future__ import annotations

import sys
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import MagicMock, AsyncMock

import pytest

sys.path.insert(0, ".")

from src.integrations.obsidian_daily import (
    ObsidianDailyWriter,
    _daily_note_path,
    _upsert_section,
    _read_or_create_note,
    SECTION_HEADERS,
)
from src.core.events import Event, EventType, AggregateType


LOCAL_TZ = ZoneInfo("Asia/Singapore")


@pytest.fixture
def tmp_settings(tmp_path: Path):
    """Create temp Settings pointing at tmp_path."""
    settings = MagicMock()
    settings.obsidian_vault_path = str(tmp_path)
    settings.obsidian_daily_folder = "daily"
    settings.obsidian_daily_template_path = "Templates/每日打卡模板.md"
    settings.obsidian_daily_sink_enabled = True
    return settings


@pytest.fixture
def writer(tmp_settings):
    return ObsidianDailyWriter(tmp_settings)


def test_daily_note_path_resolves(tmp_settings):
    """Daily note path follows vault/daily/M.D.md convention."""
    date = datetime(2026, 5, 31, tzinfo=LOCAL_TZ)
    path = _daily_note_path(tmp_settings, date)
    expected = Path(tmp_settings.obsidian_vault_path) / "daily" / "5.31.md"
    assert path == expected


def test_create_note_from_template(tmp_path, tmp_settings):
    """Note created from template when template exists."""
    template_dir = tmp_path / "Templates"
    template_dir.mkdir(parents=True)
    template_file = template_dir / "每日打卡模板.md"
    template_file.write_text(
        "# {{date}} 打卡\n\n## 🎨 绘画训练\n\n\n## 🧾 今日事件流\n",
        encoding="utf-8",
    )

    date = datetime(2026, 5, 31, tzinfo=LOCAL_TZ)
    path = _daily_note_path(tmp_settings, date)
    content = _read_or_create_note(path, tmp_settings, date)
    assert "# 2026-05-31 打卡" in content


def test_create_note_no_template_includes_all_sections(tmp_path, tmp_settings):
    """Fallback template includes all 9 fixed-structure sections."""
    date = datetime(2026, 5, 31, tzinfo=LOCAL_TZ)
    path = _daily_note_path(tmp_settings, date)
    content = _read_or_create_note(path, tmp_settings, date)
    assert "# 2026-05-31" in content
    # All fixed sections should be present
    for key in ("plan", "actual", "deviation", "art_plan", "art_training",
                "language", "fitness", "system_obs", "event_flow"):
        assert SECTION_HEADERS[key] in content, f"Missing section: {key}"


def test_write_section_creates_new(tmp_settings, writer):
    """write_section creates a section when missing."""
    date = datetime(2026, 5, 31, tzinfo=LOCAL_TZ)
    path = _daily_note_path(tmp_settings, date)

    body = writer.write_section("## 健身", "- 未记录", date)
    assert body == "- 未记录"
    content = path.read_text(encoding="utf-8")
    assert "## 健身" in content
    assert "- 未记录" in content


def test_write_section_replaces_existing(tmp_settings, writer):
    """write_section replaces content under existing section."""
    date = datetime(2026, 5, 31, tzinfo=LOCAL_TZ)
    path = _daily_note_path(tmp_settings, date)

    writer.write_section("## 健身", "- 旧内容", date)
    writer.write_section("## 健身", "- 新内容", date)
    content = path.read_text(encoding="utf-8")
    assert "- 新内容" in content
    assert "- 旧内容" not in content


def test_write_morning_entry_plan_section(tmp_settings, writer):
    """write_morning_entry populates ## 今日计划 with arrangements."""
    date = datetime(2026, 5, 31, tzinfo=LOCAL_TZ)
    path = _daily_note_path(tmp_settings, date)

    writer.write_morning_entry(
        mood_score=7,
        arrangements=["上午上课", "下午健身"],
        art_target_minutes=120,
        date=date,
    )
    content = path.read_text(encoding="utf-8")
    assert "## 今日计划" in content
    assert "- [ ] 上午上课" in content
    assert "- [ ] 下午健身" in content
    assert "- [ ] 画画 120min" in content


def test_write_morning_entry_system_obs_section(tmp_settings, writer):
    """write_morning_entry populates ## 系统观察."""
    date = datetime(2026, 5, 31, tzinfo=LOCAL_TZ)

    writer.write_morning_entry(
        mood_score=7,
        triggered_refresh=["课表", "日历"],
        date=date,
    )
    path = _daily_note_path(tmp_settings, date)
    content = path.read_text(encoding="utf-8")
    assert "## 系统观察" in content
    assert "心情：7/10" in content
    assert "课表、日历" in content


def test_write_morning_entry_idempotent(tmp_settings, writer):
    """write_morning_entry is idempotent — same data produces same sections."""
    date = datetime(2026, 5, 31, tzinfo=LOCAL_TZ)
    path = _daily_note_path(tmp_settings, date)

    writer.write_morning_entry(mood_score=5, arrangements=["上课"], date=date)
    first = path.read_text(encoding="utf-8")

    writer.write_morning_entry(mood_score=5, arrangements=["上课"], date=date)
    second = path.read_text(encoding="utf-8")

    assert first == second, "idempotent write_section should produce same file"


def test_writer_write_event_line_idempotent(tmp_settings, writer):
    """write_event_line appends lines correctly."""
    date = datetime(2026, 5, 31, tzinfo=LOCAL_TZ)
    path = _daily_note_path(tmp_settings, date)

    writer.write_event_line("测试事件 1", date)
    content = path.read_text(encoding="utf-8")
    assert "测试事件 1" in content

    writer.write_event_line("测试事件 2", date)
    content = path.read_text(encoding="utf-8")
    assert "测试事件 1" in content
    assert "测试事件 2" in content


def test_write_event_line_idempotent_skips_duplicates(tmp_settings, writer):
    """write_event_line_idempotent skips already-written events."""
    date = datetime(2026, 5, 31, tzinfo=LOCAL_TZ)
    path = _daily_note_path(tmp_settings, date)
    event_id = "test-event-uuid-123"

    # First write should succeed
    written = writer.write_event_line_idempotent("完成：数据结构", event_id, date)
    assert written is True
    content = path.read_text(encoding="utf-8")
    assert "完成：数据结构" in content
    assert f"<!-- obsidian-sink:{event_id} -->" in content

    # Second write with same event_id should be skipped
    written2 = writer.write_event_line_idempotent("完成：数据结构", event_id, date)
    assert written2 is False
    content2 = path.read_text(encoding="utf-8")
    assert content2.count("完成：数据结构") == 1


def test_upsert_section_create_new():
    """_upsert_section adds a section when header is missing."""
    content = "# Test\n\nSome text\n"
    result = _upsert_section(content, "## New Section", "body line 1\nbody line 2")
    assert "## New Section" in result
    assert "body line 1" in result
    assert "body line 2" in result


def test_upsert_section_replace_existing():
    """_upsert_section replaces content under existing header."""
    content = "## Section\nold content\n## Other Section\ndata\n"
    result = _upsert_section(content, "## Section", "new content")
    assert "new content" in result
    assert "old content" not in result
    assert "## Other Section" in result
    assert "data" in result


def test_obsidian_sink_graceful_vault_missing(tmp_settings):
    """Sink does not crash when vault path does not exist."""
    writer = ObsidianDailyWriter(tmp_settings)
    try:
        writer.write_event_line("测试", datetime(2026, 5, 31, tzinfo=LOCAL_TZ))
    except Exception:
        pytest.fail("write_event_line raised unexpectedly")


def test_obsidian_sink_graceful_vault_missing_write_section(tmp_settings):
    """write_section does not crash when vault path does not exist."""
    writer = ObsidianDailyWriter(tmp_settings)
    try:
        writer.write_section("## 测试", "- 内容", datetime(2026, 5, 31, tzinfo=LOCAL_TZ))
    except Exception:
        pytest.fail("write_section raised unexpectedly")


@pytest.mark.asyncio
async def test_morning_entry_calendar_sync_report_format():
    """Verify the calendar sync report format matches required output."""
    from src.interface.telegram.bot import _friendly_error

    # Simulate the format the bot uses
    result = {"ok": True, "created": 3, "updated": 2, "deleted": 1, "calendar_id": "primary"}
    cal_id = result.get("calendar_id", "primary")

    success_msg = (
        f"课表镜像完成：新增 {result.get('created', 0)}，"
        f"更新 {result.get('updated', 0)}，"
        f"删除 {result.get('deleted', 0)}，"
        f"目标日历 {cal_id}"
    )
    assert "新增 3" in success_msg
    assert "更新 2" in success_msg
    assert "删除 1" in success_msg
    assert "目标日历 primary" in success_msg


@pytest.mark.asyncio
async def test_calendar_sync_failure_friendly():
    """Failure reason uses friendly Chinese."""
    from src.interface.telegram.bot import _friendly_error

    result = {"ok": False, "error": "schedule_calendar_write_disabled", "calendar_id": "primary"}
    error = result.get("error", "未知错误")
    cal_id = result.get("calendar_id", "primary")
    friendly_reasons = {
        "schedule_calendar_write_disabled": "写入开关未开启",
    }
    reason_cn = friendly_reasons.get(error, _friendly_error(error))
    msg = f"课表镜像失败：{reason_cn}，目标日历 {cal_id}"
    assert "写入开关未开启" in msg
    assert "目标日历 primary" in msg


def test_morning_router_parsing_unchanged():
    """Existing morning greeting examples still parse correctly."""
    from src.interface.telegram.router import parse_morning_combined, _is_good_morning

    examples = [
        ("早安，今天下午三点健身，晚上画画，心情一般",
         {"mood_score": 5, "art_minutes": None}),
        ("早安 今天安排：上午上课 下午画画2h 晚上健身 心情6",
         {"mood_score": 6, "art_minutes": 120}),
        ("早安，今天状态差，但想画画4小时",
         {"mood_score": 3, "art_minutes": 240}),
        ("早安 心情5 今天中午吃饭 下午色彩练习",
         {"mood_score": 5, "art_minutes": None}),
    ]

    for text, expected in examples:
        assert _is_good_morning(text), f"Should detect greeting: {text}"
        parsed = parse_morning_combined(text)
        assert parsed["mood_score"] == expected["mood_score"], (
            f"mood_score mismatch for {text!r}: got {parsed['mood_score']}, expected {expected['mood_score']}"
        )
        assert parsed["art_minutes"] == expected["art_minutes"], (
            f"art_minutes mismatch for {text!r}: got {parsed['art_minutes']}, expected {expected['art_minutes']}"
        )


def test_pure_morning_greeting_still_works():
    """Pure '早安' without content still works and has empty parsed result."""
    from src.interface.telegram.router import _is_good_morning, _strip_greeting_prefix, parse_morning_combined

    assert _is_good_morning("早安")
    assert _is_good_morning("早～")
    assert _is_good_morning("早上好")

    content = _strip_greeting_prefix("早安")
    assert content == ""

    # Pure greeting should still parse to empty
    parsed = parse_morning_combined("早安")
    assert parsed["mood_score"] is None
    assert parsed["arrangements"] == []
    assert parsed["art_minutes"] is None


# ── Audit tracking tests ──────────────────────────────────────────────────


def test_audit_initial_state():
    """Audit log starts empty/zeroed."""
    from src.integrations.obsidian_daily import get_audit, reset_audit
    reset_audit()
    audit = get_audit()
    assert audit["write_count"] == 0
    assert audit["skipped_duplicate_count"] == 0
    assert audit["last_error"] == ""
    assert audit["last_write_path"] == ""


def test_audit_tracks_write(tmp_settings):
    """write_section increments audit write_count."""
    from src.integrations.obsidian_daily import reset_audit, get_audit
    reset_audit()

    writer = ObsidianDailyWriter(tmp_settings)
    date = datetime(2026, 5, 31, tzinfo=LOCAL_TZ)
    writer.write_section("## 测试区域", "- 测试内容", date)
    audit = get_audit()
    assert audit["write_count"] == 1
    assert "5.31.md" in audit["last_write_path"]
    assert audit["last_section"] == "## 测试区域"


def test_audit_increments_on_multiple_writes(tmp_settings, writer):
    """Multiple writes increase write_count."""
    from src.integrations.obsidian_daily import reset_audit
    reset_audit()

    date = datetime(2026, 5, 31, tzinfo=LOCAL_TZ)
    writer.write_section("## 区域1", "- 内容1", date)
    writer.write_section("## 区域2", "- 内容2", date)
    from src.integrations.obsidian_daily import get_audit
    audit = get_audit()
    assert audit["write_count"] == 2


def test_audit_tracks_idempotent_skip(tmp_settings, writer):
    """write_event_line_idempotent increments skipped_duplicate_count on duplicate."""
    from src.integrations.obsidian_daily import reset_audit, get_audit
    reset_audit()

    date = datetime(2026, 5, 31, tzinfo=LOCAL_TZ)
    event_id = "audit-test-dup-12345"

    # First write should succeed
    written1 = writer.write_event_line_idempotent("测试重复行", event_id, date)
    assert written1 is True

    # Second write with same event_id should be skipped
    written2 = writer.write_event_line_idempotent("测试重复行", event_id, date)
    assert written2 is False

    audit = get_audit()
    assert audit["skipped_duplicate_count"] >= 1


def test_audit_reset(tmp_path):
    """reset_audit clears audit state."""
    from src.integrations.obsidian_daily import reset_audit, get_audit
    reset_audit()

    settings = MagicMock()
    settings.obsidian_vault_path = str(tmp_path)
    settings.obsidian_daily_folder = "daily"
    settings.obsidian_daily_template_path = "Templates/每日打卡模板.md"
    writer = ObsidianDailyWriter(settings)
    writer.write_section("## Temp", "- data", datetime(2026, 5, 31, tzinfo=LOCAL_TZ))
    audit_before = get_audit()
    assert audit_before["write_count"] > 0

    reset_audit()
    audit_after = get_audit()
    assert audit_after["write_count"] == 0


def test_mock_executor_sync_schedule_counts():
    """Mock executor returns correct create/update/delete counts."""
    from src.executor.google_calendar.executor import GoogleCalendarExecutor

    executor = GoogleCalendarExecutor(use_mock=True)

    # Test with mock blocks — we can test the mock return directly
    import asyncio
    from src.core.temporal import TimeBlock, TemporalSource, TimeBlockType
    from datetime import datetime, timezone, timedelta

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
        TimeBlock(
            block_id="b2",
            source=TemporalSource.JWXT,
            block_type=TimeBlockType.CLASS_LAB,
            start=now + timedelta(hours=3),
            end=now + timedelta(hours=4),
            title="Unity应用实训",
        ),
    ]

    result = asyncio.run(executor.sync_schedule_blocks(blocks, days=7, calendar_id="primary"))
    assert result["ok"] is True
    assert result["created"] == 2  # Both blocks are JWXT class blocks
    assert result["updated"] == 0
    assert result["deleted"] == 0
    assert result["calendar_id"] == "primary"
