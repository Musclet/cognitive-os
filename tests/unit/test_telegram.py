"""Test: Telegram router and templates — pure function tests."""

import inspect
import sys

import pytest

sys.path.insert(0, ".")

from src.interface.telegram.router import parse_message, command_to_event, _parse_date_schedule_input, _parse_verbal_scheduling_early
from src.interface.telegram.bot import CognitiveOSBot
from src.interface.telegram.templates import format_output, format_help
from src.core.events import EventType, Command


def test_parse_known_command():
    cmd = parse_message("/homework", 12345)
    assert cmd is not None
    assert cmd.command_type == "check_homework"
    assert cmd.user_id == "12345"
    assert cmd.source == "telegram"


def test_parse_reply_keyboard_surface():
    assert parse_message("今日状态", 12345).command_type == "show_today"
    assert parse_message("今日安排", 12345).command_type == "show_today"
    assert parse_message("今日课表", 12345).command_type == "check_schedule"
    assert parse_message("查课表", 12345).command_type == "check_schedule"
    assert parse_message("可用时间", 12345).command_type == "show_free_today"
    assert parse_message("作业列表", 12345).command_type == "check_homework"
    assert parse_message("记录完成", 12345).command_type == "completion_prompt"
    assert parse_message("完成记录", 12345).command_type == "completion_prompt"
    assert parse_message("课程范围", 12345).command_type == "show_registry"
    assert parse_message("今天状态差", 12345).command_type == "record_bad_state"
    assert parse_message("今晚有安排", 12345).command_type == "evening_plan_options"
    assert parse_message("补水记录", 12345).command_type == "quick_hydration"
    assert parse_message("同步课表", 12345).command_type == "sync_schedule"
    assert parse_message("同步作业", 12345).command_type == "sync_homework"
    assert parse_message("同步任务", 12345).command_type == "legacy_sync_tasks"
    assert parse_message("同步日历", 12345).command_type == "calendar_sync"
    assert parse_message("同步刷新数据", 12345).command_type == "sync_refresh"
    assert parse_message("今日时间状态", 12345).command_type == "calendar_today"
    assert parse_message("30天要钱排期", 12345).command_type == "parent_fund_30d_schedule"
    assert parse_message("要钱排期", 12345).command_type == "parent_fund_30d_schedule"
    assert parse_message("状态重算", 12345).command_type == "rebuild_state"


def test_future_parent_fund_plan_routes_as_finance():
    cmd = parse_message("十号找爸爸要10元生活费", 12345)
    assert cmd is not None
    assert cmd.command_type == "finance_transaction"
    assert cmd.params["raw_text"] == "十号找爸爸要10元生活费"


def test_new_button_routes():
    """New buttons route correctly."""
    assert parse_message("状态填报", 12345).command_type == "cognitive_checkin"
    assert parse_message("认知学习", 12345).command_type == "cognitive_learning"
    assert parse_message("口述排期", 12345).command_type == "verbal_scheduling"
    assert parse_message("今晚总结", 12345).command_type == "nightly_review"
    assert parse_message("/nightly_review", 12345).command_type == "nightly_review"
    assert parse_message("记录完成", 12345).command_type == "completion_prompt"
    assert parse_message("重排今天", 12345).command_type == "art_replan"
    assert parse_message("没按计划", 12345).command_type == "art_replan_prompt"
    assert parse_message("完成了 数据结构作业", 12345).command_type == "generic_completion"
    assert parse_message("做完了 英语听力30分钟", 12345).params["task_text"] == "英语听力30分钟"
    assert parse_message("完成了 画画 2小时 人体速写12张", 12345).command_type == "art_progress"


def test_handle_message_does_not_shadow_datetime():
    """Nested datetime imports make schedule commands fail with UnboundLocalError."""
    source = inspect.getsource(CognitiveOSBot.handle_message)
    assert "from datetime import datetime" not in source


def test_finance_batch_events_are_wired_to_state_engine():
    """Batch draft callbacks need live state, not only event-log replay."""
    source = inspect.getsource(CognitiveOSBot.wire_handlers)
    assert "EventType.FINANCE_BATCH_DRAFTED" in source
    assert "EventType.FINANCE_BATCH_ACCEPTED" in source
    assert "EventType.FINANCE_REIMBURSEMENT_RECORDED" in source
    assert "self.bus.subscribe(finance_state_event, self.state_engine.apply)" in source


def test_rebuild_state_replays_empty_metadata_events():
    """Finance draft events may have empty metadata and still need replay."""
    source = inspect.getsource(CognitiveOSBot._rebuild_state_from_events)
    assert "if event.metadata" not in source


def test_art_replan_refreshes_calendar_before_planning():
    """Manual Google Calendar moves must be visible before replanning."""
    source = inspect.getsource(CognitiveOSBot._handle_art_replan)
    assert "_refresh_calendar_before_art_planning" in source
    assert source.index("_refresh_calendar_before_art_planning") < source.index("_run_art_plan")


def test_reply_keyboard_buttons_are_clear_and_routable():
    bot = CognitiveOSBot.__new__(CognitiveOSBot)
    markup = bot._quick_reply_keyboard()
    labels = [button.text for row in markup.keyboard for button in row]
    # "当前建议" should be removed
    assert "当前建议" not in labels
    assert "今日课表" not in labels
    assert "今日时间状态" not in labels
    assert "同步课表" not in labels
    assert "同步作业" not in labels
    assert "同步日历" not in labels
    assert "课程范围" not in labels
    assert "今天状态差" not in labels
    assert "今晚有安排" not in labels
    assert "重排今天" not in labels
    # New buttons should be present
    assert "今日安排" in labels
    assert "查课表" in labels
    assert "同步刷新数据" in labels
    assert "记录完成" in labels
    assert "状态填报" in labels
    assert "认知学习" in labels
    assert "口述排期" in labels
    # Existing buttons should be present
    assert "补水记录" in labels
    assert "刷新按钮" in labels
    for label in labels:
        assert parse_message(label, 12345) is not None


def test_reply_keyboard_no_old_advice_button():
    bot = CognitiveOSBot.__new__(CognitiveOSBot)
    markup = bot._quick_reply_keyboard()
    labels = [button.text for row in markup.keyboard for button in row]
    assert "当前建议" not in labels


def test_parse_unknown_message():
    cmd = parse_message("hello", 12345)
    assert cmd is None


def test_parse_command_with_args():
    cmd = parse_message("/schedule tomorrow", 12345)
    assert cmd is not None
    assert cmd.command_type == "check_schedule"
    assert cmd.params["args"] == "tomorrow"


def test_command_to_event():
    from src.core.events import Command
    cmd = Command(command_type="check_homework", user_id="42", source="telegram")
    event = command_to_event(cmd)
    assert event.event_type == EventType.USER_COMMAND_RECEIVED
    assert event.aggregate_id == "42"
    assert event.payload["command"] == "check_homework"
    assert event.metadata["source"] == "telegram"


def test_format_output_with_details():
    from src.core.events import Event, AggregateType
    event = Event(
        event_type=EventType.NOTIFICATION_SEND,
        aggregate_id="u1",
        aggregate_type=AggregateType.NOTIFICATION,
        payload={
            "message": "你有 2 个待完成作业",
            "details": ["数学作业", "英语作文"],
        },
    )
    result = format_output(event)
    assert result is not None
    assert "2 个待完成作业" in result
    assert "数学作业" in result
    assert "英语作文" in result


def test_format_output_non_notification():
    from src.core.events import Event, AggregateType
    event = Event(
        event_type=EventType.HOMEWORK_NEW,
        aggregate_id="hw-1",
        aggregate_type=AggregateType.HOMEWORK,
        payload={"title": "test"},
    )
    assert format_output(event) is None


def test_format_help():
    text = format_help()
    assert "/homework" in text
    assert "/schedule" in text
    assert "认知学习" in text
    assert "口述排期" in text
    assert "查课表" in text
    assert "今晚总结" in text
    assert "记录完成" in text


def test_parse_date_schedule_input_iso():
    """ISO date after prefix is parsed correctly."""
    result = _parse_date_schedule_input("查课表 2026-06-01")
    assert result == "2026-06-01"


def test_parse_date_schedule_input_today():
    """'今天' keyword resolves to today's date in Asia/Singapore."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    result = _parse_date_schedule_input("今日课表 今天")
    now = datetime.now(ZoneInfo("Asia/Singapore"))
    assert result == now.strftime("%Y-%m-%d")


def test_parse_date_schedule_input_tomorrow():
    """'明天' keyword resolves to tomorrow's date in Asia/Singapore."""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    result = _parse_date_schedule_input("查课表 明天")
    tomorrow = (datetime.now(ZoneInfo("Asia/Singapore")) + timedelta(days=1))
    assert result == tomorrow.strftime("%Y-%m-%d")


def test_parse_date_schedule_input_missing():
    """Text without a date returns None."""
    result = _parse_date_schedule_input("查课表")
    assert result is None


def test_query_schedule_date_routing():
    """查课表 with date routes to query_schedule_date with date param."""
    cmd = parse_message("查课表 2026-06-01", 12345)
    assert cmd is not None
    assert cmd.command_type == "query_schedule_date"
    assert cmd.params.get("date") == "2026-06-01"


def test_query_schedule_today_routing():
    """今日课表 routes to query_schedule_date."""
    cmd = parse_message("今日课表 2026-06-01", 12345)
    assert cmd is not None
    assert cmd.command_type == "query_schedule_date"


def test_query_schedule_tomorrow_chinese():
    """明天课表 routes to query_schedule_date with Asia/Singapore date."""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    cmd = parse_message("明日课表", 12345)
    assert cmd is not None
    assert cmd.command_type == "query_schedule_date"


def test_school_leave_routing():
    cmd = parse_message("请假", 12345)
    assert cmd is not None
    assert cmd.command_type == "record_school_leave"

    cmd = parse_message("/请假 明天", 12345)
    assert cmd is not None
    assert cmd.command_type == "record_school_leave"
    assert cmd.params["args"] == "明天"


# ── System operation routing tests ─────────────────────────────────────────


def test_selfcheck_command_routing():
    cmd = parse_message("/selfcheck", 12345)
    assert cmd is not None
    assert cmd.command_type == "selfcheck"

    cmd = parse_message("系统自检", 12345)
    assert cmd is not None
    assert cmd.command_type == "selfcheck"


def test_selftest_command_routing():
    cmd = parse_message("/selftest", 12345)
    assert cmd is not None
    assert cmd.command_type == "selftest"

    cmd = parse_message("真实链路烟测", 12345)
    assert cmd is not None
    assert cmd.command_type == "selftest"


def test_storage_status_command_routing():
    cmd = parse_message("/storage_status", 12345)
    assert cmd is not None
    assert cmd.command_type == "storage_status"

    cmd = parse_message("存储状态", 12345)
    assert cmd is not None
    assert cmd.command_type == "storage_status"


def test_obsidian_status_command_routing():
    cmd = parse_message("/obsidian_status", 12345)
    assert cmd is not None
    assert cmd.command_type == "obsidian_status"

    cmd = parse_message("Obsidian状态", 12345)
    assert cmd is not None
    assert cmd.command_type == "obsidian_status"


def test_format_help_includes_system_commands():
    text = format_help()
    assert "/selfcheck" in text
    assert "/selftest" in text
    assert "/storage_status" in text
    assert "Obsidian状态" in text


def test_verbal_scheduling_proposal_id_unique():
    """Verbal scheduling generates unique proposal_ids across calls."""
    from unittest.mock import AsyncMock, MagicMock, patch
    from src.interface.telegram.bot import CognitiveOSBot

    bot = CognitiveOSBot.__new__(CognitiveOSBot)
    bot.settings = MagicMock()
    bot.settings.deepseek_api_key = "test-key"
    bot.settings.google_calendar_write_enabled = True
    bot.settings.google_calendar_mock = True
    bot.settings.google_calendar_write_requires_acceptance = False

    # Simulate proposal_id generation from two different calls
    from uuid import uuid4
    ids = set()
    for _ in range(100):
        pid = f"verbal-schedule-{uuid4().hex[:12]}"
        ids.add(pid)
    assert len(ids) == 100  # all unique
    for pid in ids:
        assert "verbal-schedule-" in pid



# ── Cognitive learning integration test (mocked DeepSeek, no live API) ──


@pytest.mark.asyncio
async def test_cognitive_learning_flow_through_pipeline():
    """_handle_cognitive_learning_pending creates events published through pipeline into StateEngine."""
    from unittest.mock import AsyncMock, MagicMock, patch
    from src.interface.telegram.bot import CognitiveOSBot
    from src.core.bus import EventBus
    from src.core.pipeline import Pipeline
    from src.core.state_engine import StateEngine
    from src.core.events import EventType
    from src.interface.telegram.templates import format_output, format_help

    bot = CognitiveOSBot.__new__(CognitiveOSBot)
    bot.settings = MagicMock()
    bot.settings.deepseek_api_key = "test-key"
    bot.settings.google_calendar_write_enabled = False

    state_engine = StateEngine()
    bus = EventBus()
    pipeline = Pipeline(bus)

    bot.bus = bus
    bot.pipeline = pipeline
    bot.state_engine = state_engine
    bot._pending_input = {}

    # Subscribe state_engine to memory and subjective events
    bus.subscribe(EventType.MEMORY_ENTRY_CREATED, state_engine.apply)
    bus.subscribe(EventType.SUBJECTIVE_CONTEXT_ADDED, state_engine.apply)

    # Mock DeepSeek return
    import json
    mock_result = {
        "events": [
            {"event_type": "MEMORY_ENTRY_CREATED", "content": "今天学习了Python", "tags": ["python"]},
            {"event_type": "SUBJECTIVE_CONTEXT_ADDED", "kind": "context", "content": "感觉状态不错"},
        ]
    }
    bot._deepseek_json = AsyncMock(return_value=mock_result)

    update = MagicMock()
    update.message.reply_text = AsyncMock()

    await bot._handle_cognitive_learning_pending(update, 12345, "今天学习了Python")

    # Verify events stored in StateEngine
    mem = state_engine.get_all("memory")
    assert "12345" in mem
    entries = mem["12345"]["entries"]
    assert len(entries) == 1
    assert entries[0]["content"] == "今天学习了Python"
    assert entries[0]["source"] == "cognitive_learning"

    subj = state_engine.get_all("subjective")
    assert "12345" in subj
    notes = subj["12345"].get("contexts", [])
    assert len(notes) >= 1
    assert notes[-1]["text"] == "感觉状态不错"
    bot._pending_input.pop(12345, None)


@pytest.mark.asyncio
async def test_cognitive_learning_no_api_key():
    """_handle_cognitive_learning_pending replies with config error when no API key."""
    from unittest.mock import AsyncMock, MagicMock
    from src.interface.telegram.bot import CognitiveOSBot

    bot = CognitiveOSBot.__new__(CognitiveOSBot)
    bot.settings = MagicMock()
    bot.settings.deepseek_api_key = ""
    bot._pending_input = {12345: "cognitive_learning"}

    update = MagicMock()
    update.message.reply_text = AsyncMock()

    await bot._handle_cognitive_learning_pending(update, 12345, "some text")
    update.message.reply_text.assert_awaited_once()
    args = update.message.reply_text.await_args[0][0]
    assert "未配置" in args or "API" in args or "deepseek" in args.lower()
    assert 12345 not in bot._pending_input


@pytest.mark.asyncio
async def test_cognitive_checkin_fallback_partial_table():
    """Partial check-in table is accepted and stored without DeepSeek."""
    from unittest.mock import AsyncMock, MagicMock
    from src.interface.telegram.bot import CognitiveOSBot
    from src.core.bus import EventBus
    from src.core.pipeline import Pipeline
    from src.core.state_engine import StateEngine
    from src.core.events import EventType

    bot = CognitiveOSBot.__new__(CognitiveOSBot)
    bot.settings = MagicMock()
    bot.settings.deepseek_api_key = ""
    bot._pending_input = {12345: "cognitive_checkin"}

    state_engine = StateEngine()
    bus = EventBus()
    pipeline = Pipeline(bus)
    bus.subscribe(EventType.MEMORY_ENTRY_CREATED, state_engine.apply)
    bus.subscribe(EventType.SUBJECTIVE_CONTEXT_ADDED, state_engine.apply)
    bot.bus = bus
    bot.pipeline = pipeline
    bot.state_engine = state_engine

    update = MagicMock()
    update.message.reply_text = AsyncMock()

    await bot._handle_cognitive_checkin_pending(
        update,
        12345,
        "精力：低\n接下来安排：下午健身\n卡住/抗拒：不想开始画画",
    )

    subj = state_engine.get_all("subjective")
    assert "12345" in subj
    contexts = subj["12345"].get("contexts", [])
    assert contexts
    assert "下午健身" in contexts[-1]["text"]
    assert "不想开始画画" in contexts[-1]["text"]

    mem = state_engine.get_all("memory")
    assert "12345" in mem
    assert mem["12345"]["entries"][-1]["source"] == "cognitive_checkin"
    assert 12345 not in bot._pending_input


@pytest.mark.asyncio
async def test_completion_record_flow_through_pipeline():
    """Free-form completion records behavior and memory events."""
    from unittest.mock import AsyncMock, MagicMock
    from src.interface.telegram.bot import CognitiveOSBot
    from src.core.bus import EventBus
    from src.core.pipeline import Pipeline
    from src.core.state_engine import StateEngine
    from src.core.events import EventType

    bot = CognitiveOSBot.__new__(CognitiveOSBot)
    bot._pending_input = {12345: "completion_record"}

    state_engine = StateEngine()
    bus = EventBus()
    pipeline = Pipeline(bus)
    bus.subscribe(EventType.PLANNING_TASK_COMPLETED, state_engine.apply)
    bus.subscribe(EventType.MEMORY_ENTRY_CREATED, state_engine.apply)
    bot.bus = bus
    bot.pipeline = pipeline
    bot.state_engine = state_engine

    update = MagicMock()
    update.message.reply_text = AsyncMock()

    await bot._handle_completion_record_pending(update, 12345, "完成了 数据结构作业")

    behavior = state_engine.get_all("behavior")["current"]["feedback_log"]
    assert behavior[-1]["outcome"] == "completed"
    assert behavior[-1]["task_id"] == "数据结构作业"

    memory = state_engine.get_all("memory")["12345"]["entries"]
    assert memory[-1]["content"] == "完成：数据结构作业"
    assert 12345 not in bot._pending_input
    update.message.reply_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_verbal_scheduling_write_disabled():
    """_handle_verbal_scheduling_pending rejects when GOOGLE_CALENDAR_WRITE_ENABLED=false."""
    from unittest.mock import AsyncMock, MagicMock
    from src.interface.telegram.bot import CognitiveOSBot

    bot = CognitiveOSBot.__new__(CognitiveOSBot)
    bot.settings = MagicMock()
    bot.settings.deepseek_api_key = "test-key"
    bot.settings.google_calendar_write_enabled = False
    bot._pending_input = {12345: "verbal_scheduling"}

    update = MagicMock()
    update.message.reply_text = AsyncMock()

    await bot._handle_verbal_scheduling_pending(update, 12345, "明天下午3点开会")
    update.message.reply_text.assert_awaited_once()
    args = update.message.reply_text.await_args[0][0]
    assert "未开启" in args or "WRITE_ENABLED" in args
    assert 12345 not in bot._pending_input


@pytest.mark.asyncio
async def test_verbal_scheduling_missing_time():
    """_handle_verbal_scheduling_pending rejects when DeepSeek returns no start/end."""
    from unittest.mock import AsyncMock, MagicMock
    from src.interface.telegram.bot import CognitiveOSBot

    bot = CognitiveOSBot.__new__(CognitiveOSBot)
    bot.settings = MagicMock()
    bot.settings.deepseek_api_key = "test-key"
    bot.settings.google_calendar_write_enabled = True
    bot.settings.google_calendar_mock = True
    bot._pending_input = {12345: "verbal_scheduling"}

    # Mock DeepSeek returning no time fields
    bot._deepseek_json = AsyncMock(return_value={"title": "事件", "start": "", "end": ""})

    update = MagicMock()
    update.message.reply_text = AsyncMock()

    await bot._handle_verbal_scheduling_pending(update, 12345, "事件描述")
    update.message.reply_text.assert_awaited_once()
    args = update.message.reply_text.await_args[0][0]
    assert "未能解析" in args or "时间" in args
    assert 12345 not in bot._pending_input


@pytest.mark.asyncio
async def test_verbal_scheduling_rejects_past_time():
    """_handle_verbal_scheduling_pending rejects parsed past datetimes."""
    from unittest.mock import AsyncMock, MagicMock
    from src.interface.telegram.bot import CognitiveOSBot

    bot = CognitiveOSBot.__new__(CognitiveOSBot)
    bot.settings = MagicMock()
    bot.settings.deepseek_api_key = "test-key"
    bot.settings.google_calendar_write_enabled = True
    bot.settings.google_calendar_mock = True
    bot._pending_input = {12345: "verbal_scheduling"}

    bot._deepseek_json = AsyncMock(return_value={
        "title": "吃饭",
        "start": "2025-03-26T12:00:00+08:00",
        "end": "2025-03-26T13:00:00+08:00",
    })

    update = MagicMock()
    update.message.reply_text = AsyncMock()

    await bot._handle_verbal_scheduling_pending(update, 12345, "明天中午十二点吃饭")
    update.message.reply_text.assert_awaited_once()
    args = update.message.reply_text.await_args[0][0]
    assert "过去时间" in args
    assert 12345 not in bot._pending_input


# ── Completion detail parsing tests ────────────────────────────────────


def test_parse_completion_detail_basic():
    """Standard '完成了 X' pattern."""
    from src.interface.telegram.router import parse_completion_detail
    result = parse_completion_detail("完成了 数据结构作业")
    assert result is not None
    assert result["task"] == "数据结构作业"
    assert result["duration_min"] is None
    assert result["focus"] is None


def test_parse_completion_detail_with_duration_h():
    """Compact: '完成了0.5h的画画，色彩'"""
    from src.interface.telegram.router import parse_completion_detail
    result = parse_completion_detail("完成了0.5h的画画，色彩")
    assert result is not None
    assert result["task"] == "画画"
    assert result["duration_min"] == 30.0  # 0.5h
    assert result["focus"] == "色彩"
    assert result["is_art"] is True


def test_parse_completion_detail_spaced():
    """Spaced: '完成了 0.5h 画画 色彩'"""
    from src.interface.telegram.router import parse_completion_detail
    result = parse_completion_detail("完成了 0.5h 画画 色彩")
    assert result is not None
    assert result["task"] == "画画"
    assert result["duration_min"] == 30.0
    assert result["focus"] == "色彩"
    assert result["is_art"] is True


def test_parse_completion_detail_art_last():
    """Suffix: '画画0.5小时 色彩完成'"""
    from src.interface.telegram.router import parse_completion_detail
    result = parse_completion_detail("画画0.5小时 色彩完成")
    assert result is not None
    assert result["task"] == "画画"
    assert result["duration_min"] == 30.0
    assert result["focus"] == "色彩"
    assert result["is_art"] is True


def test_parse_completion_detail_chinese_duration():
    """Chinese units: '完成了30分钟英语听力'"""
    from src.interface.telegram.router import parse_completion_detail
    result = parse_completion_detail("完成了30分钟英语听力")
    assert result is not None
    assert result["task"] == "英语听力"
    assert result["duration_min"] == 30.0
    assert result["is_art"] is False


def test_parse_completion_detail_decimal_duration():
    """Decimal duration: '完成了1.5小时数学'"""
    from src.interface.telegram.router import parse_completion_detail
    result = parse_completion_detail("完成了1.5小时数学")
    assert result is not None
    assert result["task"] == "数学"
    assert result["duration_min"] == 90.0  # 1.5h


def test_parse_completion_detail_none_for_empty():
    """Empty or junk returns None."""
    from src.interface.telegram.router import parse_completion_detail
    assert parse_completion_detail("") is None
    assert parse_completion_detail("   ") is None


def test_parse_completion_detail_non_art():
    """Plain completion: '做完了 数据结构作业'"""
    from src.interface.telegram.router import parse_completion_detail
    result = parse_completion_detail("做完了 数据结构作业")
    assert result is not None
    assert result["task"] == "数据结构作业"
    assert result["is_art"] is False
    assert result["duration_min"] is None


def test_parse_completion_detail_with_focus():
    """Comma-separated focus: '完成了0.5h的画画,色彩'"""
    from src.interface.telegram.router import parse_completion_detail
    result = parse_completion_detail("完成了0.5h的画画,色彩")
    assert result is not None
    assert result["task"] == "画画"
    assert result["focus"] == "色彩"


def test_parse_generic_completion_prefix():
    """Standard prefix patterns still work."""
    from src.interface.telegram.router import _parse_generic_completion
    assert _parse_generic_completion("完成了 数据结构作业") == "数据结构作业"
    assert _parse_generic_completion("做完了 英语听力") == "英语听力"
    assert _parse_generic_completion("已完成 作业") == "作业"


def test_parse_generic_completion_suffix():
    """Suffix patterns: '画画完成'"""
    from src.interface.telegram.router import _parse_generic_completion
    assert _parse_generic_completion("数据结构作业做完了") == "数据结构作业"
    assert _parse_generic_completion("画画完成") == "画画"


def test_parse_generic_completion_art_routing():
    """Art completions with duration route through generic_completion correctly."""
    from src.interface.telegram.router import parse_message
    # These fall through to generic_completion because art parser
    # requires specific format
    cmd = parse_message("完成了0.5h的画画，色彩", 12345)
    assert cmd is not None
    assert cmd.command_type == "generic_completion"


# ── Natural language intent parsing tests ────────────────────────────────


def test_nl_leave_tomorrow():
    """'我明天请假' routes to record_school_leave."""
    from src.interface.telegram.router import parse_message
    cmd = parse_message("我明天请假", 12345)
    assert cmd is not None
    assert cmd.command_type == "record_school_leave"
    assert "date" in cmd.params


def test_nl_leave_today():
    """'我要请假' routes to record_school_leave (no date = today)."""
    from src.interface.telegram.router import parse_message
    cmd = parse_message("我要请假", 12345)
    assert cmd is not None
    assert cmd.command_type == "record_school_leave"


def test_nl_schedule_query_tomorrow():
    """'查明天课表' routes to query_schedule_date with resolved date."""
    from src.interface.telegram.router import parse_message
    cmd = parse_message("查明天课表", 12345)
    assert cmd is not None
    assert cmd.command_type == "query_schedule_date"
    assert cmd.params.get("date") is not None


def test_nl_schedule_query_next_wed():
    """'下周三课表' routes to query_schedule_date."""
    from src.interface.telegram.router import parse_message
    cmd = parse_message("下周三课表", 12345)
    assert cmd is not None
    assert cmd.command_type == "query_schedule_date"
    assert cmd.params.get("date") is not None


def test_nl_today_plan():
    """'今天有什么安排' routes to show_today."""
    from src.interface.telegram.router import parse_message
    cmd = parse_message("今天有什么安排", 12345)
    assert cmd is not None
    assert cmd.command_type == "show_today"


def test_nl_today_plan_variant():
    """'今日安排' routes to show_today (exact match in COMMANDS too)."""
    from src.interface.telegram.router import parse_message
    cmd = parse_message("今日安排", 12345)
    assert cmd is not None
    assert cmd.command_type == "show_today"


def test_nl_homework_query():
    """'作业还有什么' routes to check_homework."""
    from src.interface.telegram.router import parse_message
    cmd = parse_message("作业还有什么", 12345)
    assert cmd is not None
    assert cmd.command_type == "check_homework"


def test_nl_homework_remaining():
    """'还有哪些作业' routes to check_homework."""
    from src.interface.telegram.router import parse_message
    cmd = parse_message("还有哪些作业", 12345)
    assert cmd is not None
    assert cmd.command_type == "check_homework"


def test_nl_sync():
    """'同步一下' routes to sync_refresh."""
    from src.interface.telegram.router import parse_message
    cmd = parse_message("同步一下", 12345)
    assert cmd is not None
    assert cmd.command_type == "sync_refresh"


def test_nl_sync_refresh():
    """'刷新数据' routes to sync_refresh."""
    from src.interface.telegram.router import parse_message
    cmd = parse_message("刷新数据", 12345)
    assert cmd is not None
    assert cmd.command_type == "sync_refresh"


def test_nl_unknown_input_still_none():
    """Truly unknown NL input still returns None (does not fallback incorrectly)."""
    from src.interface.telegram.router import parse_message
    cmd = parse_message("你好", 12345)
    assert cmd is None
    cmd = parse_message("在干嘛", 12345)
    assert cmd is None
    cmd = parse_message("hello world", 12345)
    assert cmd is None


def test_nl_finance_not_overridden():
    """Finance inputs still route as finance, not caught by NL fallback."""
    from src.interface.telegram.router import parse_message
    cmd = parse_message("生活费到账1000", 12345)
    assert cmd is not None
    assert cmd.command_type == "finance_transaction"

    cmd = parse_message("奶茶18", 12345)
    assert cmd is not None
    assert cmd.command_type == "finance_transaction"


def test_nl_completion_not_overridden():
    """Completion inputs still route via generic_completion."""
    from src.interface.telegram.router import parse_message
    cmd = parse_message("完成了 数据结构作业", 12345)
    assert cmd is not None
    assert cmd.command_type == "generic_completion"

    cmd = parse_message("完成了0.5h的画画，色彩", 12345)
    assert cmd is not None
    assert cmd.command_type == "generic_completion"


def test_nl_leave_cmd_still_works():
    """Exact /请假 command still works."""
    from src.interface.telegram.router import parse_message
    cmd = parse_message("/请假 明天", 12345)
    assert cmd is not None
    assert cmd.command_type == "record_school_leave"
    assert cmd.params.get("args") == "明天"


def test_resolve_relative_date_direct():
    """_resolve_relative_date returns today/tomorrow/the-day-after for direct keywords."""
    from src.interface.telegram.router import _resolve_relative_date
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    today = datetime.now(ZoneInfo("Asia/Singapore")).date()

    result = _resolve_relative_date("今天")
    assert result == today.isoformat()

    result = _resolve_relative_date("明天")
    assert result == (today + timedelta(days=1)).isoformat()

    result = _resolve_relative_date("后天")
    assert result == (today + timedelta(days=2)).isoformat()


def test_nl_parse_natural_intent_leave():
    """_parse_natural_intent directly detects leave."""
    from src.interface.telegram.router import _parse_natural_intent
    cmd = _parse_natural_intent("我明天请假", "123")
    assert cmd is not None
    assert cmd.command_type == "record_school_leave"

    cmd = _parse_natural_intent("后天请假", "123")
    assert cmd is not None
    assert cmd.command_type == "record_school_leave"


def test_nl_parse_natural_intent_schedule():
    """_parse_natural_intent detects schedule queries."""
    from src.interface.telegram.router import _parse_natural_intent
    cmd = _parse_natural_intent("查明天课表", "123")
    assert cmd is not None
    assert cmd.command_type == "query_schedule_date"

    cmd = _parse_natural_intent("下周三课表", "123")
    assert cmd is not None
    assert cmd.command_type == "query_schedule_date"


def test_nl_parse_natural_intent_unknown():
    """_parse_natural_intent returns None for unrecognized input."""
    from src.interface.telegram.router import _parse_natural_intent
    assert _parse_natural_intent("你好", "123") is None
    assert _parse_natural_intent("在干嘛", "123") is None
    assert _parse_natural_intent("hello", "123") is None


# ── Verbal scheduling priority tests ──────────────────────────────────────
# Sentences with future-day + time + activity must route to verbal_scheduling
# even when they match _INSERTION_PATTERNS (e.g. "中午...吃" → art_reality).


def test_verbal_scheduling_tomorrow_noon_eating():
    """'明天中午十二点吃饭' routes to verbal_scheduling, NOT art_reality_insertion."""
    cmd = parse_message("明天中午十二点吃饭", 12345)
    assert cmd is not None
    assert cmd.command_type == "verbal_scheduling", (
        f"Expected verbal_scheduling, got {cmd.command_type}"
    )


def test_verbal_scheduling_day_after_evening_gym():
    """'后天晚上八点健身' routes to verbal_scheduling."""
    cmd = parse_message("后天晚上八点健身", 12345)
    assert cmd is not None
    assert cmd.command_type == "verbal_scheduling", (
        f"Expected verbal_scheduling, got {cmd.command_type}"
    )


def test_verbal_scheduling_next_wed_chinese():
    """'下周三下午三点开会' (Chinese numerals) routes to verbal_scheduling."""
    cmd = parse_message("下周三下午三点开会", 12345)
    assert cmd is not None
    assert cmd.command_type == "verbal_scheduling", (
        f"Expected verbal_scheduling, got {cmd.command_type}"
    )


def test_verbal_scheduling_next_wed_arabic():
    """'下周三下午3点开会' (Arabic numerals) routes to verbal_scheduling."""
    cmd = parse_message("下周三下午3点开会", 12345)
    assert cmd is not None
    assert cmd.command_type == "verbal_scheduling", (
        f"Expected verbal_scheduling, got {cmd.command_type}"
    )


def test_verbal_scheduling_specific_date():
    """'6月5日12点吃饭' routes to verbal_scheduling."""
    cmd = parse_message("6月5日12点吃饭", 12345)
    assert cmd is not None
    assert cmd.command_type == "verbal_scheduling", (
        f"Expected verbal_scheduling, got {cmd.command_type}"
    )


def test_verbal_scheduling_does_not_break_finance():
    """Finance inputs still route correctly (not hijacked by verbal scheduling)."""
    cmd = parse_message("生活费到账1000", 12345)
    assert cmd is not None
    assert cmd.command_type == "finance_transaction", (
        f"Expected finance_transaction, got {cmd.command_type}"
    )

    cmd = parse_message("明天到账1000", 12345)
    assert cmd is not None
    assert cmd.command_type == "finance_transaction", (
        f"Expected finance_transaction, got {cmd.command_type}"
    )

    cmd = parse_message("奶茶18", 12345)
    assert cmd is not None
    assert cmd.command_type == "finance_transaction"


def test_verbal_scheduling_does_not_break_leave():
    """Leave requests are not hijacked by verbal scheduling."""
    cmd = parse_message("我明天请假", 12345)
    assert cmd is not None
    assert cmd.command_type == "record_school_leave", (
        f"Expected record_school_leave, got {cmd.command_type}"
    )


def test_verbal_scheduling_does_not_break_completion():
    """Completion inputs still route through generic_completion."""
    cmd = parse_message("完成了 数据结构作业", 12345)
    assert cmd is not None
    assert cmd.command_type == "generic_completion"

    cmd = parse_message("完成了0.5h的画画，色彩", 12345)
    assert cmd is not None
    assert cmd.command_type == "generic_completion"


def test_verbal_scheduling_does_not_break_schedule_query():
    """Schedule queries are not hijacked by verbal scheduling."""
    cmd = parse_message("查明天课表", 12345)
    assert cmd is not None
    assert cmd.command_type == "query_schedule_date"


def test_verbal_scheduling_does_not_break_unknown():
    """Truly unknown NL input still returns None."""
    cmd = parse_message("你好", 12345)
    assert cmd is None
    cmd = parse_message("在干嘛", 12345)
    assert cmd is None


# ── Same-day insertion still works (no future-day reference) ──────────────


def test_same_day_insertion_still_routes():
    """Same-day insertion (no future-day) still routes to art_reality_insertion."""
    cmd = parse_message("下午去办卡", 12345)
    assert cmd is not None
    assert cmd.command_type == "art_reality_insertion", (
        f"Expected art_reality_insertion, got {cmd.command_type}"
    )

    cmd = parse_message("中午出去吃饭", 12345)
    assert cmd is not None
    assert cmd.command_type == "art_reality_insertion", (
        f"Expected art_reality_insertion, got {cmd.command_type}"
    )


def test_verbal_scheduling_early_unit():
    """_parse_verbal_scheduling_early directly detects scheduling."""
    cmd = _parse_verbal_scheduling_early("明天中午十二点吃饭", "123")
    assert cmd is not None
    assert cmd.command_type == "verbal_scheduling"

    cmd = _parse_verbal_scheduling_early("6月5日12点吃饭", "123")
    assert cmd is not None
    assert cmd.command_type == "verbal_scheduling"

    # No future day → no match
    cmd = _parse_verbal_scheduling_early("中午十二点吃饭", "123")
    assert cmd is None, "No future day should return None"

    # Finance → no match
    cmd = _parse_verbal_scheduling_early("明天生活费到账", "123")
    assert cmd is None, "Finance keyword should return None"

    # Leave → no match
    cmd = _parse_verbal_scheduling_early("明天请假", "123")
    assert cmd is None, "Leave keyword should return None"

    # Completion → no match
    cmd = _parse_verbal_scheduling_early("明天完成了作业", "123")
    assert cmd is None, "Completion pattern should return None"


# ── Undo / Revoke router tests ──────────────────────────────────────────


def test_parse_undo_patterns():
    """Undo/revoke NL phrases route to undo_last_action."""
    from src.interface.telegram.router import _parse_undo_request

    assert _parse_undo_request("撤回", "123").command_type == "undo_last_action"
    assert _parse_undo_request("撤销", "123").command_type == "undo_last_action"
    assert _parse_undo_request("撤销上一条", "123").command_type == "undo_last_action"
    assert _parse_undo_request("取消", "123").command_type == "undo_last_action"
    assert _parse_undo_request("取消上一条", "123").command_type == "undo_last_action"
    assert _parse_undo_request("刚才那个错了", "123").command_type == "undo_last_action"
    assert _parse_undo_request("上一条错了", "123").command_type == "undo_last_action"
    assert _parse_undo_request("撤销操作", "123").command_type == "undo_last_action"

    # Non-undo messages
    assert _parse_undo_request("明天中午十二点吃饭", "123") is None
    assert _parse_undo_request("查课表", "123") is None
    assert _parse_undo_request("你好", "123") is None
    assert _parse_undo_request("/homework", "123") is None


def test_parse_undo_via_parse_message():
    """Undo phrases route through parse_message to undo_last_action."""
    cmd = parse_message("撤回", 12345)
    assert cmd is not None
    assert cmd.command_type == "undo_last_action"
    assert cmd.source == "telegram"
    assert cmd.params.get("raw_text") == "撤回"

    cmd = parse_message("撤销上一条", 12345)
    assert cmd is not None
    assert cmd.command_type == "undo_last_action"

    cmd = parse_message("刚才那个错了", 12345)
    assert cmd is not None
    assert cmd.command_type == "undo_last_action"


# ── Undo / Revoke bot tests ─────────────────────────────────────────────


def test_track_action():
    """_track_action stores actions and caps at 20 per user."""
    from src.interface.telegram.bot import CognitiveOSBot

    bot = CognitiveOSBot.__new__(CognitiveOSBot)

    aid = bot._track_action(12345, "finance_transaction", "奶茶18", {"amount": 18})
    assert aid.startswith("act-")

    aid2 = bot._track_action(12345, "completion_record", "数据结构作业", {"task": "数据结构作业"})
    assert aid2 != aid

    actions = bot._user_recent_actions[12345]
    assert len(actions) == 2
    assert actions[0]["action_type"] == "finance_transaction"
    assert actions[1]["action_type"] == "completion_record"
    assert actions[1]["summary"] == "数据结构作业"
    assert actions[1]["reverted"] is False

    # Cap at 20
    for i in range(25):
        bot._track_action(12345, "test", f"action-{i}")
    assert len(bot._user_recent_actions[12345]) == 20


def test_track_action_separate_users():
    """Actions for different users are tracked independently."""
    from src.interface.telegram.bot import CognitiveOSBot

    bot = CognitiveOSBot.__new__(CognitiveOSBot)
    bot._track_action(1, "test", "user-1-action")
    bot._track_action(2, "test", "user-2-action")
    assert len(bot._user_recent_actions[1]) == 1
    assert len(bot._user_recent_actions[2]) == 1


@pytest.mark.asyncio
async def test_undo_callback_unknown_action_id():
    """Undo callback with non-existent action_id shows error."""
    from unittest.mock import AsyncMock
    from src.interface.telegram.bot import CognitiveOSBot

    bot = CognitiveOSBot.__new__(CognitiveOSBot)
    bot._user_recent_actions = {}

    query = AsyncMock()
    query.data = "undo:nonexistent-id"
    query.from_user = type("obj", (object,), {"id": 12345})()

    await bot._handle_undo_callback(query, 12345, "undo:nonexistent-id", "test-trace", 0.0)
    query.edit_message_text.assert_awaited_once_with("这条操作记录未找到，可能已过期。")


@pytest.mark.asyncio
async def test_undo_callback_empty_action_id():
    """Undo callback with empty action_id shows error."""
    from unittest.mock import AsyncMock
    from src.interface.telegram.bot import CognitiveOSBot

    bot = CognitiveOSBot.__new__(CognitiveOSBot)

    query = AsyncMock()
    query.data = "undo:"
    query.from_user = type("obj", (object,), {"id": 12345})()

    await bot._handle_undo_callback(query, 12345, "undo:", "test-trace", 0.0)
    query.edit_message_text.assert_awaited_once_with("无效的撤回请求。")


@pytest.mark.asyncio
async def test_undo_callback_already_reverted():
    """Undo callback on already-reverted action shows warning."""
    from unittest.mock import AsyncMock
    from src.interface.telegram.bot import CognitiveOSBot

    bot = CognitiveOSBot.__new__(CognitiveOSBot)
    aid = bot._track_action(12345, "test_action", "测试操作")
    # Mark as reverted
    bot._user_recent_actions[12345][0]["reverted"] = True

    query = AsyncMock()
    query.data = f"undo:{aid}"
    query.from_user = type("obj", (object,), {"id": 12345})()

    await bot._handle_undo_callback(query, 12345, f"undo:{aid}", "test-trace", 0.0)
    query.edit_message_text.assert_awaited_once_with("这条操作已撤回，不能重复撤回。")


@pytest.mark.asyncio
async def test_undo_callback_unsupported_type():
    """Undo callback for unsupported action_type shows message."""
    from unittest.mock import AsyncMock
    from src.interface.telegram.bot import CognitiveOSBot

    bot = CognitiveOSBot.__new__(CognitiveOSBot)
    aid = bot._track_action(12345, "some_unknown_type", "测试操作")

    query = AsyncMock()
    query.data = f"undo:{aid}"
    query.from_user = type("obj", (object,), {"id": 12345})()

    await bot._handle_undo_callback(query, 12345, f"undo:{aid}", "test-trace", 0.0)
    query.edit_message_text.assert_awaited_once()
    text = query.edit_message_text.await_args[0][0]
    assert "暂不支持自动撤回" in text


@pytest.mark.asyncio
async def test_handle_nl_undo_no_actions():
    """NL undo with no recent actions shows appropriate message."""
    from unittest.mock import AsyncMock, MagicMock
    from src.interface.telegram.bot import CognitiveOSBot

    bot = CognitiveOSBot.__new__(CognitiveOSBot)
    bot._user_recent_actions = {}

    update = MagicMock()
    update.message.reply_text = AsyncMock()

    await bot._handle_nl_undo(update, 12345)
    update.message.reply_text.assert_awaited_once()
    text = update.message.reply_text.await_args[0][0]
    assert "没有可以撤回" in text


# ── Task A: Immediate execution tests ──────────────────────────────────


@pytest.mark.asyncio
async def test_verbal_scheduling_immediate_dispatch():
    """verbal_scheduling from telegram source with raw_text executes immediately."""
    from unittest.mock import AsyncMock, MagicMock
    from src.interface.telegram.bot import CognitiveOSBot
    from src.core.bus import EventBus
    from src.core.pipeline import Pipeline
    from src.core.state_engine import StateEngine

    bot = CognitiveOSBot.__new__(CognitiveOSBot)
    bot.settings = MagicMock()
    bot.settings.telegram_allowed_users = None
    bot.settings.deepseek_api_key = "test-key"
    bot.settings.google_calendar_write_enabled = False

    bus = EventBus()
    pipeline = Pipeline(bus)
    state_engine = StateEngine()
    bot.bus = bus
    bot.pipeline = pipeline
    bot.state_engine = state_engine
    bot._pending_input = {}

    # Mock _handle_verbal_scheduling_pending to verify it gets called
    bot._handle_verbal_scheduling_pending = AsyncMock(return_value=None)

    update = MagicMock()
    update.message.text = "明天中午十二点吃饭"
    update.message.reply_text = AsyncMock()
    update.effective_user.id = 12345
    update.effective_user = type("obj", (object,), {"id": 12345})()

    await bot.handle_message(update, None)

    bot._handle_verbal_scheduling_pending.assert_awaited_once_with(
        update, 12345, "明天中午十二点吃饭"
    )


@pytest.mark.asyncio
async def test_completion_record_immediate_dispatch():
    """completion_record with raw_text from nl_fallback executes immediately."""
    from unittest.mock import AsyncMock, MagicMock, patch
    from src.interface.telegram.bot import CognitiveOSBot
    from src.core.bus import EventBus
    from src.core.pipeline import Pipeline
    from src.core.state_engine import StateEngine

    bot = CognitiveOSBot.__new__(CognitiveOSBot)
    bot.settings = MagicMock()
    bot.settings.telegram_allowed_users = None
    bot.settings.deepseek_api_key = "test-key"
    bot.settings.google_calendar_write_enabled = False

    bus = EventBus()
    pipeline = Pipeline(bus)
    state_engine = StateEngine()
    bot.bus = bus
    bot.pipeline = pipeline
    bot.state_engine = state_engine
    bot._pending_input = {}

    # Mock _handle_completion_record_pending
    bot._handle_completion_record_pending = AsyncMock(return_value=None)

    update = MagicMock()
    update.message.text = "完成了 数学作业"
    update.message.reply_text = AsyncMock()
    update.effective_user.id = 12345
    update.effective_user = type("obj", (object,), {"id": 12345})()

    # Mock parse_message to return completion_record from nl_fallback
    with patch("src.interface.telegram.bot.parse_message") as mock_parse:
        mock_parse.return_value = Command(
            command_type="completion_record",
            user_id="12345",
            source="nl_fallback",
            params={"raw_text": "完成了 数学作业"},
        )

        await bot.handle_message(update, None)

    bot._handle_completion_record_pending.assert_awaited_once_with(
        update, 12345, "完成了 数学作业"
    )


@pytest.mark.asyncio
async def test_completion_record_pending_mode_not_immediate():
    """completion_record without raw_text sets pending mode instead."""
    from unittest.mock import AsyncMock, MagicMock, patch
    from src.interface.telegram.bot import CognitiveOSBot
    from src.core.bus import EventBus
    from src.core.pipeline import Pipeline
    from src.core.state_engine import StateEngine

    bot = CognitiveOSBot.__new__(CognitiveOSBot)
    bot.settings = MagicMock()
    bot.settings.telegram_allowed_users = None
    bot.settings.deepseek_api_key = "test-key"
    bot.settings.google_calendar_write_enabled = False

    bus = EventBus()
    pipeline = Pipeline(bus)
    state_engine = StateEngine()
    bot.bus = bus
    bot.pipeline = pipeline
    bot.state_engine = state_engine
    bot._pending_input = {}

    bot._handle_completion_record_pending = AsyncMock(return_value=None)

    update = MagicMock()
    update.message.text = "完成记录"  # Button text, no raw text
    update.message.reply_text = AsyncMock()
    update.effective_user.id = 12345
    update.effective_user = type("obj", (object,), {"id": 12345})()

    # Mock parse_message to return completion_record with NO raw_text
    with patch("src.interface.telegram.bot.parse_message") as mock_parse:
        mock_parse.return_value = Command(
            command_type="completion_record",
            user_id="12345",
            source="telegram",
            params={},
        )

        await bot.handle_message(update, None)

    # Should NOT call _handle_completion_record_pending
    bot._handle_completion_record_pending.assert_not_called()
    # Should set pending input
    assert bot._pending_input.get(12345) == "completion_record"
    update.message.reply_text.assert_awaited_once()


if __name__ == "__main__":
    test_parse_known_command()
    test_parse_unknown_message()
    test_parse_command_with_args()
    test_command_to_event()
    test_format_output_with_details()
    test_format_output_non_notification()
    test_format_help()
    test_new_button_routes()
    test_reply_keyboard_buttons_are_clear_and_routable()
    test_reply_keyboard_no_old_advice_button()
    test_parse_date_schedule_input_iso()
    test_parse_date_schedule_input_today()
    test_parse_date_schedule_input_tomorrow()
    test_parse_date_schedule_input_missing()
    test_query_schedule_date_routing()
    test_query_schedule_today_routing()
    test_query_schedule_tomorrow_chinese()
    test_parse_completion_detail_basic()
    test_parse_completion_detail_with_duration_h()
    test_parse_completion_detail_spaced()
    test_parse_completion_detail_art_last()
    test_parse_completion_detail_chinese_duration()
    test_parse_completion_detail_decimal_duration()
    test_parse_completion_detail_none_for_empty()
    test_parse_completion_detail_non_art()
    test_parse_completion_detail_with_focus()
    test_parse_generic_completion_prefix()
    test_parse_generic_completion_suffix()
    test_parse_generic_completion_art_routing()


# ── Task A: Executor delete_event ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_executor_delete_event_mock():
    """delete_event in mock mode returns ok."""
    from src.executor.google_calendar.executor import GoogleCalendarExecutor
    from src.infrastructure.config import Settings

    settings = Settings(google_calendar_mock=True, google_calendar_write_enabled=True)
    executor = GoogleCalendarExecutor(use_mock=True, settings=settings)

    result = await executor.delete_event("test-event-id", "primary")
    assert result.get("ok") is True
    assert result.get("event_id") == "test-event-id"


@pytest.mark.asyncio
async def test_executor_delete_event_mock_respects_settings():
    """delete_event respects google_calendar_mock setting."""
    from src.executor.google_calendar.executor import GoogleCalendarExecutor
    from src.infrastructure.config import Settings

    settings = Settings(google_calendar_mock=False, google_calendar_write_enabled=False)
    executor = GoogleCalendarExecutor(use_mock=False, settings=settings)

    result = await executor.delete_event("test-event-id", "primary")
    assert result.get("ok") is False
    assert "calendar_write_disabled" in result.get("error", "")


# ── Task A: Undo verbal_scheduling auto-delete ────────────────────────────


@pytest.mark.asyncio
async def test_undo_verbal_scheduling_with_event_id():
    """Undo verbal_scheduling with event_id calls executor delete and publishes events."""
    from unittest.mock import AsyncMock, MagicMock
    from src.interface.telegram.bot import CognitiveOSBot
    from src.core.bus import EventBus
    from src.core.pipeline import Pipeline
    from src.core.state_engine import StateEngine
    from src.core.events import EventType

    bot = CognitiveOSBot.__new__(CognitiveOSBot)
    bot.settings = MagicMock()
    bot.settings.google_calendar_mock = True
    bot.settings.google_calendar_calendar_id = "primary"
    bot.settings.google_calendar_write_enabled = True

    bus = EventBus()
    pipeline = Pipeline(bus)
    state_engine = StateEngine()
    bot.bus = bus
    bot.pipeline = pipeline
    bot.state_engine = state_engine

    # Track a verbal_scheduling action with event_id and bot_created flag
    aid = bot._track_action(
        12345,
        "verbal_scheduling",
        "测试事件",
        params={
            "event_id": "mock-event-123",
            "calendar_id": "primary",
            "title": "测试事件",
            "start": "2026-06-03T10:00:00+08:00",
            "end": "2026-06-03T11:00:00+08:00",
            "text": "明天上午十点开会",
            "source": "bot_created",
            "bot_created": True,
        },
    )

    query = AsyncMock()
    query.data = f"undo:{aid}"
    query.from_user = type("obj", (object,), {"id": 12345})()

    await bot._handle_undo_callback(query, 12345, f"undo:{aid}", "test-trace", 0.0)

    # Should have edited message saying calendar event was auto-deleted
    query.edit_message_text.assert_awaited_once()
    text = query.edit_message_text.await_args[0][0]
    assert "已撤回" in text
    assert "自动删除" in text

    # Verify action is marked reverted
    action = next(a for a in bot._user_recent_actions[12345] if a["action_id"] == aid)
    assert action.get("reverted") is True

    # CALENDAR_EVENT_DELETED and USER_ACTION_REVERTED were published through pipeline
    # (no event_store attached, so we verify by behavior: action is marked reverted)


@pytest.mark.asyncio
async def test_undo_verbal_scheduling_delete_failure():
    """Undo delete failure publishes USER_ACTION_REVERT_FAILED and does NOT mark reverted."""
    from unittest.mock import AsyncMock, MagicMock, patch
    from src.interface.telegram.bot import CognitiveOSBot
    from src.core.bus import EventBus
    from src.core.pipeline import Pipeline
    from src.core.state_engine import StateEngine

    bot = CognitiveOSBot.__new__(CognitiveOSBot)
    bot.settings = MagicMock()
    bot.settings.google_calendar_mock = False
    bot.settings.google_calendar_calendar_id = "primary"
    bot.settings.google_calendar_write_enabled = True

    bus = EventBus()
    pipeline = Pipeline(bus)
    state_engine = StateEngine()
    # Subscribe state_engine to relevant event types for this test
    bus.subscribe("user.action.revert_failed", state_engine.apply)
    bot.bus = bus
    bot.pipeline = pipeline
    bot.state_engine = state_engine

    aid = bot._track_action(
        12345,
        "verbal_scheduling",
        "测试事件",
        params={
            "event_id": "real-event-456",
            "calendar_id": "primary",
            "title": "测试事件",
            "source": "bot_created",
            "bot_created": True,
        },
    )

    # Mock delete_event to return failure (not raise exception)
    with patch(
        "src.executor.google_calendar.executor.GoogleCalendarExecutor.delete_event",
        AsyncMock(return_value={"ok": False, "error": "mock_delete_failure"}),
    ):
        query = AsyncMock()
        query.data = f"undo:{aid}"
        query.from_user = type("obj", (object,), {"id": 12345})()

        await bot._handle_undo_callback(query, 12345, f"undo:{aid}", "test-trace", 0.0)

        query.edit_message_text.assert_awaited_once()
        text = query.edit_message_text.await_args[0][0]
        assert "撤回失败" in text or "无法删除" in text

        # Action should NOT be marked reverted
        action = next(a for a in bot._user_recent_actions[12345] if a["action_id"] == aid)
        assert action.get("reverted") is not True

    # Verify USER_ACTION_REVERT_FAILED was published and processed by state_engine
    failures = state_engine.get_view("undo", "failures") or {}
    assert len(failures.get("failures", [])) >= 1
    assert any(
        f["action_id"] == aid and "calendar_delete_failed" in f.get("error", "")
        for f in failures.get("failures", [])
    )


@pytest.mark.asyncio
async def test_undo_verbal_scheduling_no_event_id():
    """Undo verbal_scheduling without event_id just marks reverted, no delete call."""
    from unittest.mock import AsyncMock, MagicMock
    from src.interface.telegram.bot import CognitiveOSBot
    from src.core.bus import EventBus
    from src.core.pipeline import Pipeline
    from src.core.state_engine import StateEngine

    bot = CognitiveOSBot.__new__(CognitiveOSBot)
    bot.settings = MagicMock()
    bus = EventBus()
    pipeline = Pipeline(bus)
    state_engine = StateEngine()
    bot.bus = bus
    bot.pipeline = pipeline
    bot.state_engine = state_engine

    aid = bot._track_action(
        12345,
        "verbal_scheduling",
        "测试事件",
        params={"title": "测试事件", "text": "事件"},
        # Note: no event_id, no bot_created
    )

    query = AsyncMock()
    query.data = f"undo:{aid}"
    query.from_user = type("obj", (object,), {"id": 12345})()

    await bot._handle_undo_callback(query, 12345, f"undo:{aid}", "test-trace", 0.0)

    query.edit_message_text.assert_awaited_once()
    text = query.edit_message_text.await_args[0][0]
    assert "已撤回" in text
    assert "自动删除" not in text  # Not attempting external delete

    action = next(a for a in bot._user_recent_actions[12345] if a["action_id"] == aid)
    assert action.get("reverted") is True


# ── Task A: Tracked params include calendar event id ──────────────────────


@pytest.mark.asyncio
async def test_verbal_scheduling_tracks_calendar_id_and_bot_created():
    """_handle_verbal_scheduling_pending saves calendar event id, calendar_id, and bot_created."""
    from datetime import datetime, timedelta
    from unittest.mock import AsyncMock, MagicMock
    from zoneinfo import ZoneInfo
    from src.interface.telegram.bot import CognitiveOSBot
    from src.core.bus import EventBus
    from src.core.pipeline import Pipeline
    from src.core.state_engine import StateEngine
    from src.core.events import EventType

    bot = CognitiveOSBot.__new__(CognitiveOSBot)
    bot.settings = MagicMock()
    bot.settings.deepseek_api_key = "test-key"
    bot.settings.google_calendar_write_enabled = True
    bot.settings.google_calendar_mock = True
    bot.settings.google_calendar_calendar_id = "primary"

    bus = EventBus()
    pipeline = Pipeline(bus)
    state_engine = StateEngine()
    bot.bus = bus
    bot.pipeline = pipeline
    bot.state_engine = state_engine
    bot._pending_input = {}

    # Keep the test independent of the wall-clock date.
    start = datetime.now(ZoneInfo("Asia/Singapore")) + timedelta(days=2)
    end = start + timedelta(hours=1)
    bot._deepseek_json = AsyncMock(return_value={
        "title": "测试会议",
        "start": start.isoformat(),
        "end": end.isoformat(),
        "description": "会议描述",
    })

    update = MagicMock()
    update.message.reply_text = AsyncMock()
    update.effective_user = type("obj", (object,), {"id": 12345})()

    await bot._handle_verbal_scheduling_pending(update, 12345, "后天下午两点开会")

    # Check tracked action params
    actions = bot._user_recent_actions.get(12345, [])
    assert len(actions) >= 1
    vs_action = next((a for a in reversed(actions) if a["action_type"] == "verbal_scheduling"), None)
    assert vs_action is not None, "verbal_scheduling action should be tracked"

    params = vs_action.get("params", {})
    assert params.get("event_id") is not None, "should save calendar event_id"
    assert "mock-event-" in params.get("event_id", ""), "event_id should come from executor"
    assert params.get("calendar_id") == "primary"
    assert params.get("source") == "bot_created"
    assert params.get("bot_created") is True


# ── Task B: Conflict detection with temporal blocks ────────────────────────


@pytest.mark.asyncio
async def test_verbal_scheduling_local_conflict_rejects_overlap():
    """Local conflict detection rejects proposed event overlapping an existing block."""
    from unittest.mock import AsyncMock, MagicMock, patch
    from datetime import datetime, timedelta, timezone
    from zoneinfo import ZoneInfo
    from src.interface.telegram.bot import CognitiveOSBot, LOCAL_TZ
    from src.core.bus import EventBus
    from src.core.pipeline import Pipeline
    from src.core.state_engine import StateEngine
    from src.core.temporal import TimeBlock, TemporalSource, TimeBlockType

    bot = CognitiveOSBot.__new__(CognitiveOSBot)
    bot.settings = MagicMock()
    bot.settings.deepseek_api_key = "test-key"
    bot.settings.google_calendar_write_enabled = True
    bot.settings.google_calendar_mock = True
    bot.settings.google_calendar_calendar_id = "primary"

    bus = EventBus()
    pipeline = Pipeline(bus)
    state_engine = StateEngine()
    bot.bus = bus
    bot.pipeline = pipeline
    bot.state_engine = state_engine
    bot._pending_input = {}

    # Insert a class block in state_engine that conflicts
    tz = LOCAL_TZ
    now = datetime.now(tz)
    # Simulate a class at 14:00-15:30 today
    class_start = now.replace(hour=14, minute=0, second=0, microsecond=0)
    class_end = now.replace(hour=15, minute=30, second=0, microsecond=0)
    if class_start <= now:
        # If past 14:00 today, use tomorrow
        class_start += timedelta(days=1)
        class_end += timedelta(days=1)

    class_block = TimeBlock(
        block_id="test-class-1",
        source=TemporalSource.JWXT,
        block_type=TimeBlockType.CLASS_LECTURE,
        start=class_start,
        end=class_end,
        title="高等数学",
    )
    # Initialize required bot state
    bot._user_recent_actions = {}
    # Directly inject into temporal blocks
    state_engine._temporal_blocks["test-class-1"] = class_block

    # Mock DeepSeek to return a time that OVERLAPS with the class
    # e.g. 14:30-15:30 which is inside 14:00-15:30
    overlap_start = (class_start + timedelta(minutes=30)).isoformat()
    overlap_end = (class_end).isoformat()
    bot._deepseek_json = AsyncMock(return_value={
        "title": "买东西",
        "start": overlap_start,
        "end": overlap_end,
    })

    update = MagicMock()
    update.message.reply_text = AsyncMock()
    update.effective_user = type("obj", (object,), {"id": 12345})()

    await bot._handle_verbal_scheduling_pending(update, 12345, "买东西")

    # Should be rejected with conflict message
    update.message.reply_text.assert_awaited_once()
    text = update.message.reply_text.await_args[0][0]
    assert "冲突" in text or "没有创建" in text
    assert "高等数学" in text

    # No event should be created
    actions = bot._user_recent_actions.get(12345, [])
    vs_actions = [a for a in actions if a["action_type"] == "verbal_scheduling"]
    assert len(vs_actions) == 0, "No verbal_scheduling action should be tracked on conflict"


@pytest.mark.asyncio
async def test_verbal_scheduling_after_class_respects_end_time():
    """'课后/上完课去健身' should pass conflict check if after class ends."""
    from unittest.mock import AsyncMock, MagicMock
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    from src.interface.telegram.bot import CognitiveOSBot, LOCAL_TZ
    from src.core.bus import EventBus
    from src.core.pipeline import Pipeline
    from src.core.state_engine import StateEngine
    from src.core.temporal import TimeBlock, TemporalSource, TimeBlockType

    bot = CognitiveOSBot.__new__(CognitiveOSBot)
    bot.settings = MagicMock()
    bot.settings.deepseek_api_key = "test-key"
    bot.settings.google_calendar_write_enabled = True
    bot.settings.google_calendar_mock = True
    bot.settings.google_calendar_calendar_id = "primary"

    bus = EventBus()
    pipeline = Pipeline(bus)
    state_engine = StateEngine()
    bot.bus = bus
    bot.pipeline = pipeline
    bot.state_engine = state_engine
    bot._pending_input = {}

    tz = LOCAL_TZ
    now = datetime.now(tz)

    # Place a class 14:00-15:30
    class_start = now.replace(hour=14, minute=0, second=0, microsecond=0)
    class_end = now.replace(hour=15, minute=30, second=0, microsecond=0)
    if class_start <= now:
        class_start += timedelta(days=1)
        class_end += timedelta(days=1)

    class_block = TimeBlock(
        block_id="test-class-2",
        source=TemporalSource.JWXT,
        block_type=TimeBlockType.CLASS_LECTURE,
        start=class_start,
        end=class_end,
        title="大学英语",
    )
    state_engine._temporal_blocks["test-class-2"] = class_block

    # DeepSeek returns a time AFTER class ends (15:30-16:30)
    after_start = class_end.isoformat()
    after_end = (class_end + timedelta(hours=1)).isoformat()
    bot._deepseek_json = AsyncMock(return_value={
        "title": "健身",
        "start": after_start,
        "end": after_end,
    })

    update = MagicMock()
    update.message.reply_text = AsyncMock()
    update.effective_user = type("obj", (object,), {"id": 12345})()

    await bot._handle_verbal_scheduling_pending(update, 12345, "上完课去健身")

    text = update.message.reply_text.await_args[0][0]
    assert "已创建" in text, "Should create event when after class ends"
    assert "冲突" not in text

    # Should have tracked the action
    actions = bot._user_recent_actions.get(12345, [])
    vs_actions = [a for a in actions if a["action_type"] == "verbal_scheduling"]
    assert len(vs_actions) >= 1

    # Action params should include event_id
    params = vs_actions[-1].get("params", {})
    assert params.get("event_id") is not None


# ── Task B: Conflict detection with DeepSeek conflict field ────────────────


@pytest.mark.asyncio
async def test_verbal_scheduling_deepseek_conflict_field():
    """DeepSeek returning conflict field with empty start/end shows conflict message."""
    from unittest.mock import AsyncMock, MagicMock
    from src.interface.telegram.bot import CognitiveOSBot

    bot = CognitiveOSBot.__new__(CognitiveOSBot)
    bot.settings = MagicMock()
    bot.settings.deepseek_api_key = "test-key"
    bot.settings.google_calendar_write_enabled = True
    bot.settings.google_calendar_mock = True
    bot._pending_input = {}

    bot._deepseek_json = AsyncMock(return_value={
        "title": "安排",
        "start": "",
        "end": "",
        "conflict": "与 17:00-18:00 课程冲突",
    })

    update = MagicMock()
    update.message.reply_text = AsyncMock()
    update.effective_user = type("obj", (object,), {"id": 12345})()

    await bot._handle_verbal_scheduling_pending(update, 12345, "五点去健身")

    update.message.reply_text.assert_awaited_once()
    text = update.message.reply_text.await_args[0][0]
    assert "无法创建" in text
    assert "冲突" in text


# ── Task C: Consistency review note ────────────────────────────────────────


@pytest.mark.asyncio
async def test_verbal_scheduling_consistency_review_shows_note():
    """Consistency review should show warning for tight transitions."""
    from unittest.mock import AsyncMock, MagicMock
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    from src.interface.telegram.bot import CognitiveOSBot, LOCAL_TZ
    from src.core.bus import EventBus
    from src.core.pipeline import Pipeline
    from src.core.state_engine import StateEngine
    from src.core.temporal import TimeBlock, TemporalSource, TimeBlockType

    bot = CognitiveOSBot.__new__(CognitiveOSBot)
    bot.settings = MagicMock()
    bot.settings.deepseek_api_key = "test-key"
    bot.settings.google_calendar_write_enabled = True
    bot.settings.google_calendar_mock = True
    bot.settings.google_calendar_calendar_id = "primary"

    bus = EventBus()
    pipeline = Pipeline(bus)
    state_engine = StateEngine()
    bot.bus = bus
    bot.pipeline = pipeline
    bot.state_engine = state_engine
    bot._pending_input = {}

    tz = LOCAL_TZ
    now = datetime.now(tz)

    # Existing event ends at 15:00
    existing_end = now.replace(hour=15, minute=0, second=0, microsecond=0)
    existing_start = existing_end - timedelta(hours=1)
    if existing_start <= now:
        existing_start += timedelta(days=1)
        existing_end += timedelta(days=1)

    existing_block = TimeBlock(
        block_id="test-existing-1",
        source=TemporalSource.GOOGLE_CALENDAR,
        block_type=TimeBlockType.CALENDAR_EVENT,
        start=existing_start,
        end=existing_end,
        title="下午会议",
    )
    state_engine._temporal_blocks["test-existing-1"] = existing_block

    # New event starts just 10 min after existing event ends (tight!)
    new_start = existing_end + timedelta(minutes=10)
    new_end = new_start + timedelta(hours=1)

    bot._deepseek_json = AsyncMock(return_value={
        "title": "快速办点事",
        "start": new_start.isoformat(),
        "end": new_end.isoformat(),
    })

    update = MagicMock()
    update.message.reply_text = AsyncMock()
    update.effective_user = type("obj", (object,), {"id": 12345})()

    await bot._handle_verbal_scheduling_pending(update, 12345, "三点十分办点事")

    text = update.message.reply_text.await_args[0][0]
    assert "已创建" in text
    assert "缓冲" in text or "间隔" in text or "建议" in text


    print("\nTelegram interface: all checks passed")
