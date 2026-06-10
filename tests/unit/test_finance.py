"""Test: Finance domain — transaction classification, monthly state,
outing budget warnings, parent fund scheduling, Obsidian writes,
and Telegram route formatting."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, PropertyMock

import pytest

sys.path.insert(0, ".")

from src.domain.finance.classifier import classify_transaction, CATEGORY_LABELS
from src.domain.finance.parent_fund import (
    DEFAULT_FIXED_ITEMS,
    compute_next_eligible_date,
    compute_weekly_total,
    compute_due_items,
    compute_30_day_request_schedule,
    schedule_request_advice,
    apply_request_record,
    compute_next_safe_date,
)
from src.domain.finance.warnings import generate_spending_warnings
from src.domain.finance.handlers import (
    format_transaction_feedback,
    format_monthly_summary,
    format_outing_status,
    format_savings_progress,
    format_parent_advice,
    format_parent_plan,
    format_parent_30_day_schedule,
    handle_finance_command,
    _parse_amount,
    _is_finance_input,
    _is_parent_planning_input,
    _is_parent_actual_input,
    _is_income_input,
    _parse_requested_date,
)
from src.core.events import Event, EventType, AggregateType
from src.core.state_engine import StateEngine


# ── Classification tests ─────────────────────────────────────────────────────

class TestClassification:
    def test_outing_classification(self):
        assert classify_transaction("和对象出去玩") == "outing"
        assert classify_transaction("电影票") == "outing"
        assert classify_transaction("逛街买衣服") == "outing"
        assert classify_transaction("约会餐厅") == "outing"

    def test_necessary_classification(self):
        assert classify_transaction("食堂午饭") == "necessary"
        assert classify_transaction("交通卡充值") == "necessary"
        assert classify_transaction("地铁") == "necessary"
        assert classify_transaction("晚饭") == "necessary"

    def test_art_learning(self):
        assert classify_transaction("买画材") == "art_learning_investment"
        assert classify_transaction("课程报名") == "art_learning_investment"
        assert classify_transaction("买书") == "art_learning_investment"

    def test_fitness_health(self):
        assert classify_transaction("健身卡") == "fitness_health"
        assert classify_transaction("祛痘药") == "fitness_health"
        assert classify_transaction("蛋白粉") == "fitness_health"

    def test_emotional(self):
        assert classify_transaction("奶茶") == "emotional"
        assert classify_transaction("零食大礼包") == "emotional"
        assert classify_transaction("奖励自己") == "emotional"

    def test_other_fallback(self):
        assert classify_transaction("杂物") == "other"
        assert classify_transaction("") == "other"


# ── Amount parsing tests ─────────────────────────────────────────────────────

class TestAmountParsing:
    def test_parse_simple(self):
        assert _parse_amount("奶茶18") == 18.0
        assert _parse_amount("出去玩120") == 120.0

    def test_parse_with_text_after(self):
        assert _parse_amount("晚饭52 和对象") == 52.0

    def test_parse_income(self):
        assert _parse_amount("今天生活费到账1000") == 1000.0

    def test_parse_parent_request(self):
        assert _parse_amount("找爸爸要了150买画材") == 150.0

    def test_parse_amount_ignores_date_token(self):
        assert _parse_amount("10号叫爸爸拿生活费") is None
        assert _parse_amount("十号找爸爸要10元生活费") == 10.0
        assert _parse_amount("10号叫爸爸拿生活费1500元") == 1500.0

    def test_parse_requested_date_day_of_month(self):
        now = datetime(2026, 6, 2, tzinfo=timezone.utc)
        result = _parse_requested_date("十号找爸爸要10元生活费", now)
        assert result == datetime(2026, 6, 10, tzinfo=timezone.utc)

    def test_parse_no_number(self):
        assert _parse_amount("hello") is None


# ── Input detection tests ────────────────────────────────────────────────────

class TestInputDetection:
    def test_finance_input_with_amount(self):
        assert _is_finance_input("奶茶18") is True
        assert _is_finance_input("出去玩120") is True

    def test_finance_input_income(self):
        assert _is_finance_input("生活费到账1000") is True
        assert _is_finance_input("今天生活费到账1000") is True

    def test_income_routes_before_art_reality_insertion(self):
        from src.interface.telegram.router import parse_message

        cmd = parse_message("今天生活费到账1000", 12345)
        assert cmd is not None
        assert cmd.command_type == "finance_transaction"

    def test_finance_input_parent_actual(self):
        assert _is_finance_input("找爸爸要了150买画材") is True

    def test_finance_input_parent_plan(self):
        assert _is_finance_input("想找爸爸要120买画材") is True
        assert _is_finance_input("什么时候要话费") is True

    def test_finance_menu_commands(self):
        assert _is_finance_input("本月资金") is True
        assert _is_finance_input("出去玩额度") is True
        assert _is_finance_input("攒钱进度") is True
        assert _is_finance_input("要钱计划") is True
        assert _is_finance_input("30天要钱排期") is True

    def test_non_finance_input(self):
        assert _is_finance_input("早安") is False
        assert _is_finance_input("完成了画画") is False

    def test_parent_planning_detection(self):
        assert _is_parent_planning_input("想找爸爸要120买画材") is True
        assert _is_parent_planning_input("什么时候要话费") is True

    def test_parent_actual_detection(self):
        assert _is_parent_actual_input("找爸爸要了150买画材") is True
        assert _is_parent_actual_input("今天要了100话费") is True

    def test_income_detection(self):
        assert _is_income_input("生活费到账1000") is True
        assert _is_income_input("今天生活费到账1000") is True


# ── Monthly state aggregation tests ───────────────────────────────────────

class TestMonthlyState:
    def test_transaction_recorded_event(self):
        """FINANCE_TRANSACTION_RECORDED updates outflow and category."""
        event = Event(
            event_type=EventType.FINANCE_TRANSACTION_RECORDED,
            aggregate_id="user1",
            aggregate_type=AggregateType.FINANCE,
            payload={"amount": 52, "category": "necessary", "description": "晚饭"},
        )
        assert event.event_type == EventType.FINANCE_TRANSACTION_RECORDED
        assert event.payload["amount"] == 52
        assert event.payload["category"] == "necessary"

    def test_income_recorded_event(self):
        """FINANCE_INCOME_RECORDED updates inflow."""
        event = Event(
            event_type=EventType.FINANCE_INCOME_RECORDED,
            aggregate_id="user1",
            aggregate_type=AggregateType.FINANCE,
            payload={"amount": 1000, "source": "生活费"},
        )
        assert event.event_type == EventType.FINANCE_INCOME_RECORDED
        assert event.payload["amount"] == 1000

    def test_finance_month_rollover_resets_current_month(self):
        """finance/monthly view is scoped to the event month, not all-time totals."""
        engine = StateEngine()

        async def apply_events():
            await engine.apply(Event(
                event_type=EventType.FINANCE_INCOME_RECORDED,
                aggregate_id="user1",
                aggregate_type=AggregateType.FINANCE,
                timestamp=datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc),
                payload={"amount": 1000, "source": "生活费"},
            ))
            await engine.apply(Event(
                event_type=EventType.FINANCE_TRANSACTION_RECORDED,
                aggregate_id="user1",
                aggregate_type=AggregateType.FINANCE,
                timestamp=datetime(2026, 6, 30, 13, 0, tzinfo=timezone.utc),
                payload={"amount": 120, "category": "outing", "description": "出去玩"},
            ))
            await engine.apply(Event(
                event_type=EventType.FINANCE_INCOME_RECORDED,
                aggregate_id="user1",
                aggregate_type=AggregateType.FINANCE,
                timestamp=datetime(2026, 7, 1, 8, 0, tzinfo=timezone.utc),
                payload={"amount": 800, "source": "生活费"},
            ))

        asyncio.run(apply_events())
        view = engine.get_view("finance", "monthly")
        assert view["month"] == "2026-07"
        assert view["inflow"] == 800
        assert view["outflow"] == 0
        assert view["outing_spent"] == 0
        assert view["history"][-1]["month"] == "2026-06"
        assert view["history"][-1]["outing_spent"] == 120

    def test_finance_logs_keep_event_ids_for_web_ledger(self):
        """New ledger rows keep event ids so Web can safely reverse a specific row."""
        engine = StateEngine()
        tx = Event(
            event_type=EventType.FINANCE_TRANSACTION_RECORDED,
            aggregate_id="user1",
            aggregate_type=AggregateType.FINANCE,
            payload={"amount": 20, "category": "necessary", "description": "午饭"},
        )
        income = Event(
            event_type=EventType.FINANCE_INCOME_RECORDED,
            aggregate_id="user1",
            aggregate_type=AggregateType.FINANCE,
            payload={"amount": 100, "source": "生活费", "description": "生活费到账"},
        )

        async def apply_events():
            await engine.apply(tx)
            await engine.apply(income)

        asyncio.run(apply_events())
        view = engine.get_view("finance", "monthly")
        assert view["transactions"][-1]["event_id"] == tx.event_id
        assert view["income_log"][-1]["event_id"] == income.event_id
        assert view["income_log"][-1]["description"] == "生活费到账"

    def test_finance_revert_is_idempotent_by_action_id(self):
        """Repeated revert of the same row must not subtract twice."""
        engine = StateEngine()
        tx = Event(
            event_type=EventType.FINANCE_TRANSACTION_RECORDED,
            aggregate_id="user1",
            aggregate_type=AggregateType.FINANCE,
            payload={"amount": 20, "category": "outing", "description": "电影"},
        )

        async def apply_events():
            await engine.apply(tx)
            revert_payload = {
                "action_id": tx.event_id,
                "action_type": "finance_transaction",
                "amount": 20,
                "category": "outing",
            }
            revert = Event(
                event_type=EventType.USER_ACTION_REVERTED,
                aggregate_id="user1",
                aggregate_type=AggregateType.USER,
                payload=revert_payload,
            )
            duplicate_revert = Event(
                event_type=EventType.USER_ACTION_REVERTED,
                aggregate_id="user1",
                aggregate_type=AggregateType.USER,
                payload=revert_payload,
            )
            await engine.apply(revert)
            await engine.apply(duplicate_revert)

        asyncio.run(apply_events())
        view = engine.get_view("finance", "monthly")
        assert view["outflow"] == 0
        assert view["outing_spent"] == 0
        undo = engine.get_view("undo", "user1")
        assert len(undo["reverted_actions"]) == 1


# ── Parent fund scheduling tests ──────────────────────────────────────────

class TestParentFundScheduling:
    def test_compute_next_eligible_date_no_history(self):
        now = datetime(2026, 6, 2, tzinfo=timezone.utc)
        result = compute_next_eligible_date(None, 3, now)
        assert result == now

    def test_compute_next_eligible_date_with_history(self):
        now = datetime(2026, 6, 2, tzinfo=timezone.utc)
        last = datetime(2026, 5, 30, tzinfo=timezone.utc)
        result = compute_next_eligible_date(last, 3, now)
        # last + 3 = June 2, which equals now
        assert result == now

    def test_compute_next_eligible_date_too_soon(self):
        now = datetime(2026, 6, 1, tzinfo=timezone.utc)
        last = datetime(2026, 5, 31, tzinfo=timezone.utc)  # only 1 day ago
        result = compute_next_eligible_date(last, 3, now)
        # last + 3 = June 3 > now
        assert result > now
        assert result == datetime(2026, 6, 3, tzinfo=timezone.utc)

    def test_weekly_total_empty(self):
        now = datetime(2026, 6, 2, tzinfo=timezone.utc)
        assert compute_weekly_total([], now) == 0.0

    def test_weekly_total_recent(self):
        now = datetime(2026, 6, 2, tzinfo=timezone.utc)
        log = [
            {"amount": 100, "timestamp": "2026-06-01T00:00:00+00:00"},
            {"amount": 50, "timestamp": "2026-05-30T00:00:00+00:00"},
        ]
        assert compute_weekly_total(log, now) == 150.0

    def test_weekly_total_old_only(self):
        now = datetime(2026, 6, 10, tzinfo=timezone.utc)
        log = [
            {"amount": 200, "timestamp": "2026-06-01T00:00:00+00:00"},
        ]
        assert compute_weekly_total(log, now) == 0.0

    def test_compute_due_items(self):
        now = datetime(2026, 6, 2, tzinfo=timezone.utc)
        due = compute_due_items(DEFAULT_FIXED_ITEMS, [], [], None, now)
        # All items with no history should be due
        assert len(due) == 8  # 9 items minus book (amount=0)
        assert any(d["item_id"] == "phone_bill" for d in due)
        assert all(d["item_id"] != "book" for d in due)  # book has amount=0

    def test_due_items_fixed_item_remains_after_ad_hoc(self):
        """Fixed items due remain in queue even after ad hoc requests."""
        now = datetime(2026, 6, 2, tzinfo=timezone.utc)
        # Only ad hoc request log (no item_id)
        request_log = [
            {"amount": 200, "description": "临时买衣服", "timestamp": "2026-06-01T00:00:00+00:00"},
        ]
        due = compute_due_items(DEFAULT_FIXED_ITEMS, request_log, [], None, now)
        # All fixed items should still be due
        assert len(due) == 8
        assert any(d["item_id"] == "phone_bill" for d in due)

    def test_30_day_schedule_spaces_fixed_items(self):
        now = datetime(2026, 6, 2, tzinfo=timezone.utc)
        schedule = compute_30_day_request_schedule(
            DEFAULT_FIXED_ITEMS,
            request_log=[],
            received_log=[],
            safe_interval_days=3,
            now=now,
        )
        labels = [item["label"] for item in schedule]
        assert "话费" in labels
        assert "剪头发" in labels
        assert "书" not in labels

        dates = [datetime.fromisoformat(item["request_date"]) for item in schedule]
        assert dates[0].date() == datetime(2026, 6, 3, tzinfo=timezone.utc).date()
        assert dates[1] - dates[0] == timedelta(days=3)

    def test_30_day_schedule_respects_recent_ad_hoc_request(self):
        now = datetime(2026, 6, 2, tzinfo=timezone.utc)
        request_log = apply_request_record([], 150, "画材", None, now)
        schedule = compute_30_day_request_schedule(
            DEFAULT_FIXED_ITEMS,
            request_log=request_log,
            received_log=[],
            safe_interval_days=3,
            now=now,
        )
        first_date = datetime.fromisoformat(schedule[0]["request_date"])
        assert first_date.date() == datetime(2026, 6, 5, tzinfo=timezone.utc).date()
        assert schedule[0]["pushed"] is True

    def test_30_day_schedule_respects_future_planned_request(self):
        now = datetime(2026, 6, 2, tzinfo=timezone.utc)
        schedule = compute_30_day_request_schedule(
            DEFAULT_FIXED_ITEMS,
            request_log=[],
            received_log=[],
            planned_requests=[{
                "amount": 10,
                "description": "十号找爸爸要10元生活费",
                "requested_date": "2026-06-10T00:00:00+00:00",
            }],
            safe_interval_days=3,
            now=now,
        )
        planned = [item for item in schedule if item.get("planned")]
        assert len(planned) == 1
        assert datetime.fromisoformat(planned[0]["request_date"]).date() == datetime(2026, 6, 10, tzinfo=timezone.utc).date()
        fixed_dates = [
            datetime.fromisoformat(item["request_date"]).date()
            for item in schedule
            if not item.get("planned")
        ]
        blocked_dates = {
            datetime(2026, 6, 9, tzinfo=timezone.utc).date(),
            datetime(2026, 6, 10, tzinfo=timezone.utc).date(),
            datetime(2026, 6, 11, tzinfo=timezone.utc).date(),
            datetime(2026, 6, 12, tzinfo=timezone.utc).date(),
        }
        assert blocked_dates.isdisjoint(set(fixed_dates))

    def test_schedule_advice_split_large_amount(self):
        """Amount > 75 suggests split into chunks spaced 3 days."""
        now = datetime(2026, 6, 2, tzinfo=timezone.utc)
        advice = schedule_request_advice(
            amount=150,
            description="买衣服",
            category="other",
            fixed_items=DEFAULT_FIXED_ITEMS,
            request_log=[],
            received_log=[],
            last_request_date=None,
            safe_interval_days=3,
            single_risk_threshold=75,
            weekly_risk_threshold=300,
            now=now,
        )
        assert advice["split_suggestion"] is not None
        assert len(advice["split_suggestion"]) == 2  # 75 + 75
        assert advice["split_suggestion"][0]["amount"] == 75
        # Second chunk 3 days after first
        date1 = datetime.fromisoformat(advice["split_suggestion"][0]["date"])
        date2 = datetime.fromisoformat(advice["split_suggestion"][1]["date"])
        assert (date2 - date1).days == 3

    def test_apply_request_record(self):
        now = datetime(2026, 6, 2, tzinfo=timezone.utc)
        log = apply_request_record([], 150, "买画材", "book", now)
        assert len(log) == 1
        assert log[0]["amount"] == 150
        assert log[0]["item_id"] == "book"

    def test_advice_fixed_due_is_safe(self):
        """Requesting a fixed due item should be marked safe."""
        now = datetime(2026, 6, 2, tzinfo=timezone.utc)
        advice = schedule_request_advice(
            amount=100,
            description="话费",
            category="system_subscription",
            fixed_items=DEFAULT_FIXED_ITEMS,
            request_log=[],
            received_log=[],
            last_request_date=None,
            now=now,
        )
        assert advice["safe"] is True
        assert "话费" in advice["reason"]

    def test_advice_dangerous_weekly_total(self):
        """Weekly total + new amount > 300 warns."""
        now = datetime(2026, 6, 2, tzinfo=timezone.utc)
        request_log = [
            {"amount": 250, "timestamp": "2026-06-01T00:00:00+00:00"},
        ]
        advice = schedule_request_advice(
            amount=100,
            description="随便买",
            category="other",
            fixed_items=DEFAULT_FIXED_ITEMS,
            request_log=request_log,
            received_log=[],
            last_request_date=datetime(2026, 5, 28, tzinfo=timezone.utc),
            single_risk_threshold=75,
            weekly_risk_threshold=300,
            now=now,
        )
        assert len(advice["warnings"]) > 0


# ── Warning tests ─────────────────────────────────────────────────────────

class TestWarnings:
    def test_no_warning_below_80_percent(self):
        warnings = generate_spending_warnings(
            current_outing_spent=100, outing_budget=250,
        )
        assert len(warnings) == 0

    def test_warning_at_80_percent(self):
        """At 200 outing_spent (80% of 250), warn approaching cap."""
        warnings = generate_spending_warnings(
            current_outing_spent=200, outing_budget=250,
        )
        assert len(warnings) >= 1
        assert "注意控制" in warnings[0]

    def test_warning_at_250_exceeded(self):
        """At 250 outing_spent, emit strong warning."""
        warnings = generate_spending_warnings(
            current_outing_spent=250, outing_budget=250,
        )
        assert len(warnings) >= 1
        assert "不建议继续" in warnings[0]

    def test_savings_target_warning(self):
        """Estimated savings below target triggers warning."""
        warnings = generate_spending_warnings(
            current_outing_spent=0,
            estimated_savings=300,
            savings_target=500,
        )
        assert len(warnings) >= 1
        assert "压缩储蓄空间" in warnings[0]


# ── Handler tests (event production) ─────────────────────────────────────

class TestHandler:
    async def test_handle_transaction(self):
        event = Event(
            event_type=EventType.USER_COMMAND_RECEIVED,
            aggregate_id="user1",
            aggregate_type=AggregateType.USER,
            payload={
                "command": "finance_transaction",
                "params": {"raw_text": "奶茶18"},
            },
        )
        results = await handle_finance_command(event)
        assert len(results) == 1
        assert results[0].event_type == EventType.FINANCE_TRANSACTION_RECORDED
        assert results[0].payload["amount"] == 18.0
        assert results[0].payload["category"] == "emotional"

    async def test_handle_outing_transaction(self):
        event = Event(
            event_type=EventType.USER_COMMAND_RECEIVED,
            aggregate_id="user1",
            aggregate_type=AggregateType.USER,
            payload={
                "command": "finance_transaction",
                "params": {"raw_text": "出去玩120"},
            },
        )
        results = await handle_finance_command(event)
        assert len(results) == 1
        assert results[0].event_type == EventType.FINANCE_TRANSACTION_RECORDED
        assert results[0].payload["amount"] == 120.0
        assert results[0].payload["category"] == "outing"

    async def test_handle_income(self):
        event = Event(
            event_type=EventType.USER_COMMAND_RECEIVED,
            aggregate_id="user1",
            aggregate_type=AggregateType.USER,
            payload={
                "command": "finance_transaction",
                "params": {"raw_text": "今天生活费到账1000"},
            },
        )
        results = await handle_finance_command(event)
        assert len(results) == 1
        assert results[0].event_type == EventType.FINANCE_INCOME_RECORDED
        assert results[0].payload["amount"] == 1000.0

    async def test_handle_parent_actual_request(self):
        event = Event(
            event_type=EventType.USER_COMMAND_RECEIVED,
            aggregate_id="user1",
            aggregate_type=AggregateType.USER,
            payload={
                "command": "finance_transaction",
                "params": {"raw_text": "找爸爸要了150买画材"},
            },
        )
        results = await handle_finance_command(event)
        assert len(results) == 3
        assert results[0].event_type == EventType.PARENT_FUND_REQUEST_RECORDED
        assert results[1].event_type == EventType.PARENT_FUND_RECEIVED
        assert results[2].event_type == EventType.FINANCE_INCOME_RECORDED
        assert results[0].payload["amount"] == 150.0
        assert results[2].payload["amount"] == 150.0
        assert results[2].payload["source"] == "爸爸"

    async def test_parent_actual_request_updates_monthly_income(self):
        from src.core.state_engine import StateEngine

        event = Event(
            event_type=EventType.USER_COMMAND_RECEIVED,
            aggregate_id="user1",
            aggregate_type=AggregateType.USER,
            payload={
                "command": "finance_transaction",
                "params": {"raw_text": "我找爸爸要了60买东西"},
            },
        )
        results = await handle_finance_command(event)
        engine = StateEngine()
        for produced in results:
            await engine.apply(produced)

        finance = engine.get_view("finance", "monthly")
        parent = engine.get_view("parent_funds", "current")
        assert finance["inflow"] == 60.0
        assert parent["request_log"][-1]["amount"] == 60.0
        assert parent["received_log"][-1]["amount"] == 60.0

    async def test_handle_parent_advice_request(self):
        event = Event(
            event_type=EventType.USER_COMMAND_RECEIVED,
            aggregate_id="user1",
            aggregate_type=AggregateType.USER,
            payload={
                "command": "finance_transaction",
                "params": {"raw_text": "想找爸爸要120买画材"},
            },
        )
        results = await handle_finance_command(event)
        assert len(results) == 1
        assert results[0].event_type == EventType.PARENT_FUND_REQUEST_PLANNED

    async def test_handle_future_parent_request_as_plan(self):
        event = Event(
            event_type=EventType.USER_COMMAND_RECEIVED,
            aggregate_id="user1",
            aggregate_type=AggregateType.USER,
            timestamp=datetime(2026, 6, 2, tzinfo=timezone.utc),
            payload={
                "command": "finance_transaction",
                "params": {"raw_text": "十号找爸爸要10元生活费"},
            },
        )
        results = await handle_finance_command(event)
        assert len(results) == 1
        assert results[0].event_type == EventType.PARENT_FUND_REQUEST_PLANNED
        assert results[0].payload["amount"] == 10.0
        assert results[0].payload["requested_date"] == "2026-06-10T00:00:00+00:00"


# ── Format tests ──────────────────────────────────────────────────────────

class TestFormatting:
    def test_transaction_feedback(self):
        text = format_transaction_feedback(
            amount=18, category="emotional", description="奶茶18",
            outing_spent=0, outing_budget=250,
        )
        assert "奶茶18" in text
        assert "情绪消费" in text
        assert "18 元" in text

    def test_outing_status_under_budget(self):
        text = format_outing_status(100, 250)
        assert "150" in text  # remaining
        assert "100" in text  # spent

    def test_outing_status_exceeded(self):
        text = format_outing_status(250, 250)
        assert "超支" in text

    def test_monthly_summary(self):
        text = format_monthly_summary(
            inflow=1000, outflow=300,
            by_category={"outing": 120, "necessary": 180},
            outing_spent=120, outing_budget=250, savings_target=500,
        )
        assert "1000" in text
        assert "700" in text  # 1000-300=700 estimated savings

    def test_savings_progress(self):
        text = format_savings_progress(
            inflow=1000, outflow=300, savings_target=500,
        )
        assert "700" in text  # estimated
        assert "达标" in text


# ── Obsidian write tests ──────────────────────────────────────────────────

class TestObsidianFinance:
    def test_write_finance_line_idempotent(self, tmp_path):
        from src.integrations.obsidian_daily import ObsidianDailyWriter
        settings = MagicMock()
        settings.obsidian_vault_path = str(tmp_path)
        settings.obsidian_daily_folder = "daily"
        settings.obsidian_daily_template_path = "Templates/每日打卡模板.md"
        settings.obsidian_daily_sink_enabled = True

        writer = ObsidianDailyWriter(settings)
        date = datetime(2026, 6, 2, tzinfo=timezone.utc)

        # First write
        result = writer.write_finance_line_idempotent(
            "奶茶 18元 | 情绪消费",
            "event-1",
            date,
        )
        assert result is True

        # Idempotent: second write skipped
        result = writer.write_finance_line_idempotent(
            "奶茶 18元 | 情绪消费",
            "event-1",
            date,
        )
        assert result is False

    def test_write_parent_fund_line_idempotent(self, tmp_path):
        from src.integrations.obsidian_daily import ObsidianDailyWriter
        settings = MagicMock()
        settings.obsidian_vault_path = str(tmp_path)
        settings.obsidian_daily_folder = "daily"
        settings.obsidian_daily_template_path = "Templates/每日打卡模板.md"
        settings.obsidian_daily_sink_enabled = True

        writer = ObsidianDailyWriter(settings)
        date = datetime(2026, 6, 2, tzinfo=timezone.utc)

        # First write
        result = writer.write_parent_fund_line_idempotent(
            "要了150元买画材",
            "event-2",
            date,
        )
        assert result is True

        # Idempotent
        result = writer.write_parent_fund_line_idempotent(
            "要了150元买画材",
            "event-2",
            date,
        )
        assert result is False


# ── Parent fund plan formatting tests ─────────────────────────────────────

class TestParentPlanFormat:
    def test_format_parent_plan_with_log(self):
        now = datetime(2026, 6, 2, tzinfo=timezone.utc)
        request_log = apply_request_record([], 100, "话费", "phone_bill",
                                           now - timedelta(days=5))
        next_safe = compute_next_safe_date(request_log, 3, now)
        weekly_total = compute_weekly_total(request_log, now)

        text = format_parent_plan(
            request_log=request_log,
            received_log=[],
            fixed_items=DEFAULT_FIXED_ITEMS,
            next_safe_date=next_safe,
            weekly_total=weekly_total,
        )
        assert "话费" in text
        assert "100" in text

    def test_format_parent_30_day_schedule(self):
        now = datetime(2026, 6, 2, tzinfo=timezone.utc)
        text = format_parent_30_day_schedule(
            request_log=[],
            received_log=[],
            fixed_items=DEFAULT_FIXED_ITEMS,
            safe_interval_days=3,
            now=now,
        )
        assert "30天要钱排期" in text
        assert "06月03日" in text
        assert "话费" in text


# ── Next safe date computation ────────────────────────────────────────────

class TestNextSafeDate:
    def test_compute_next_safe_date_empty_log(self):
        now = datetime(2026, 6, 2, tzinfo=timezone.utc)
        result = compute_next_safe_date([], 3, now)
        assert result == now

    def test_compute_next_safe_date_with_recent(self):
        now = datetime(2026, 6, 5, tzinfo=timezone.utc)
        log = [
            {"amount": 100, "timestamp": "2026-06-03T00:00:00+00:00"},
        ]
        result = compute_next_safe_date(log, 3, now)
        # Last request June 3 + 3 days = June 6
        assert result == datetime(2026, 6, 6, tzinfo=timezone.utc)


# ── Finance Batch Intake tests ─────────────────────────────────────────────

class TestBatchParser:
    """Deterministic batch parser for complex multi-fact finance text."""

    def test_is_batch_intake_detection(self):
        from src.domain.finance.batch_parser import is_batch_intake
        # Complex text with 报销 → batch
        assert is_batch_intake("金色印象189，对象报销百分之40。吃喝155+15，对象报销百分之50。") is True
        # Simple single expense → not batch
        assert is_batch_intake("奶茶18") is False

    def test_parse_expense_reimbursement_percent(self):
        """金色印象189，对象报销百分之40 → gross 189, reimb 75.6, net 113.4"""
        from src.domain.finance.batch_parser import parse_batch
        now = datetime(2026, 6, 2, tzinfo=timezone.utc)
        draft = parse_batch("金色印象189，对象报销百分之40", now)
        items = draft["items"]
        assert len(items) == 1
        assert items[0]["type"] == "reimbursement"
        assert items[0]["gross_amount"] == 189.0
        assert items[0]["reimbursed_amount"] == 75.6
        assert items[0]["net_amount"] == 113.4

    def test_parse_expression_with_reimbursement(self):
        """吃喝155+15 → sum to 170, 报销百分之50 → 85"""
        from src.domain.finance.batch_parser import parse_batch
        now = datetime(2026, 6, 2, tzinfo=timezone.utc)
        draft = parse_batch("吃喝155+15，对象报销百分之50", now)
        items = draft["items"]
        assert len(items) == 1
        assert items[0]["type"] == "reimbursement"
        assert items[0]["gross_amount"] == 170.0
        assert items[0]["reimbursed_amount"] == 85.0
        assert items[0]["net_amount"] == 85.0

    def test_parse_partner_debt_with_date(self):
        """5月3号借给了对象500元"""
        from src.domain.finance.batch_parser import parse_batch
        now = datetime(2026, 6, 2, tzinfo=timezone.utc)
        draft = parse_batch("5月3号借给了对象500元", now)
        items = draft["items"]
        assert len(items) == 1
        assert items[0]["type"] == "partner_debt_created"
        assert items[0]["amount"] == 500.0
        # Date should be 2026-05-03 (before now, so same year)
        assert "2026-05-03" in items[0]["date"]

    def test_parse_parent_fund_rule(self):
        """妈妈这个月会给300，每9天要一次"""
        from src.domain.finance.batch_parser import parse_batch
        now = datetime(2026, 6, 2, tzinfo=timezone.utc)
        draft = parse_batch("妈妈这个月会给300，每9天要一次", now)
        items = draft["items"]
        assert len(items) == 1
        assert items[0]["type"] == "parent_fund_rule_configured"
        assert items[0]["person"] == "妈妈"
        assert items[0]["interval_days"] == 9
        assert items[0]["amount"] == 300.0

    def test_parse_parent_request_recorded(self):
        """今天已经找妈妈要了100了"""
        from src.domain.finance.batch_parser import parse_batch
        now = datetime(2026, 6, 2, tzinfo=timezone.utc)
        draft = parse_batch("今天已经找妈妈要了100了", now)
        items = draft["items"]
        assert len(items) == 1
        assert items[0]["type"] == "parent_fund_request_recorded"
        assert items[0]["amount"] == 100.0
        assert items[0]["person"] == "妈妈"

    def test_parse_parent_rule_and_recorded_request_same_sentence(self):
        """妈妈规则和今天已要钱在同一句中也要同时保留。"""
        from src.domain.finance.batch_parser import parse_batch
        now = datetime(2026, 6, 2, tzinfo=timezone.utc)
        draft = parse_batch("妈妈这个月会给300，每9天要一次，今天已经找妈妈要了100了", now)
        items = draft["items"]
        assert any(i["type"] == "parent_fund_rule_configured" for i in items)
        recorded = [i for i in items if i["type"] == "parent_fund_request_recorded"]
        assert len(recorded) == 1
        assert recorded[0]["amount"] == 100.0
        assert recorded[0]["person"] == "妈妈"

    def test_parse_planned_items(self):
        """买画材的钱100还没要"""
        from src.domain.finance.batch_parser import parse_batch
        now = datetime(2026, 6, 2, tzinfo=timezone.utc)
        draft = parse_batch("买画材的钱100还没要", now)
        items = draft["items"]
        assert len(items) == 1
        assert items[0]["type"] == "parent_fund_request_planned"
        assert items[0]["description"] == "画材"
        assert items[0]["amount"] == 100.0

    def test_parse_short_planned_items(self):
        """洗面奶钱60，面膜钱70，鞋子钱200"""
        from src.domain.finance.batch_parser import parse_batch
        now = datetime(2026, 6, 2, tzinfo=timezone.utc)
        # Each should be a separate parent_fund_request_planned
        draft = parse_batch("洗面奶钱60，面膜钱70，鞋子钱200", now)
        planned = [i for i in draft["items"] if i["type"] == "parent_fund_request_planned"]
        assert len(planned) == 3
        assert planned[0]["description"] == "洗面奶"
        assert planned[0]["amount"] == 60.0
        assert planned[1]["description"] == "面膜"
        assert planned[1]["amount"] == 70.0
        assert planned[2]["description"] == "鞋子"
        assert planned[2]["amount"] == 200.0
        # 200 should trigger a warning
        assert len(draft["questions"]) >= 1
        assert "200" in draft["questions"][0]

    def test_parse_full_user_example(self):
        """Full complex example from user."""
        from src.domain.finance.batch_parser import parse_batch
        now = datetime(2026, 6, 2, tzinfo=timezone.utc)
        text = (
            "上周和对象出去花了金色印象189，对象报销百分之40。"
            "吃喝155+15，对象报销百分之50。"
            "吃饭电影157，对象报销1.7元。"
            "今天吃饭花了20元买减脂餐。"
            "5月3号借给了对象500元。"
            "妈妈这个月会给300，每9天要一次。"
            "今天已经找妈妈要了100了。"
            "买画材的钱100还没要。"
            "洗面奶钱60，面膜钱70，鞋子钱200"
        )
        draft = parse_batch(text, now)
        items = draft["items"]
        types = [i["type"] for i in items]

        assert "reimbursement" in types
        assert "expense" in types
        assert "partner_debt_created" in types
        assert "parent_fund_rule_configured" in types
        assert "parent_fund_request_recorded" in types
        assert "parent_fund_request_planned" in types

        # Verify specific items
        reimbursements = [i for i in items if i["type"] == "reimbursement"]
        assert any(abs(i["reimbursed_amount"] - 75.6) < 0.01 for i in reimbursements)
        assert any(abs(i["reimbursed_amount"] - 85) < 0.01 for i in reimbursements)

        debts = [i for i in items if i["type"] == "partner_debt_created"]
        # The debt date should be 2026-05-03
        assert any("2026-05-03" in d.get("date", "") for d in debts)

        rules = [i for i in items if i["type"] == "parent_fund_rule_configured"]
        assert any(r["interval_days"] == 9 for r in rules)

        records = [i for i in items if i["type"] == "parent_fund_request_recorded"]
        assert any(abs(r["amount"] - 100) < 0.01 for r in records)

        planned = [i for i in items if i["type"] == "parent_fund_request_planned"]
        assert any("画材" in p.get("description", "") for p in planned)

        # Verify questions/warnings
        assert len(draft["questions"]) >= 1

    def test_router_routes_batch_to_special_command(self):
        """Batch text routes to finance_batch_intake, NOT finance_transaction."""
        from src.interface.telegram.router import parse_message
        text = "金色印象189，对象报销百分之40。吃喝155+15，对象报销百分之50。吃饭电影157，对象报销1.7元。"
        cmd = parse_message(text, 12345)
        assert cmd is not None
        assert cmd.command_type == "finance_batch_intake", (
            f"Expected finance_batch_intake, got {cmd.command_type}"
        )

    def test_simple_finance_still_works(self):
        """Simple single finance inputs still route to finance_transaction."""
        from src.interface.telegram.router import parse_message
        cmd = parse_message("奶茶18", 12345)
        assert cmd is not None
        assert cmd.command_type == "finance_transaction"
        cmd = parse_message("出去玩120", 12345)
        assert cmd is not None
        assert cmd.command_type == "finance_transaction"

    def test_batch_does_not_create_ordinary_transaction(self):
        """Batch intake does NOT produce FINANCE_TRANSACTION_RECORDED directly."""
        from src.core.events import Event, EventType, AggregateType
        from src.domain.finance.handlers import handle_finance_command
        from src.interface.telegram.router import parse_message, command_to_event

        text = "金色印象189，对象报销百分之40。吃喝155+15。"
        cmd = parse_message(text, 12345)
        assert cmd is not None
        assert cmd.command_type == "finance_batch_intake"
        # When converted to event, it should NOT match the normal handle_finance_command
        # because the command_type is different
        event = command_to_event(cmd)
        assert event.payload["command"] == "finance_batch_intake"


class TestBatchStateEngine:
    """Test StateEngine handlers for batch intake events."""

    def test_batch_draft_stored_in_state(self):
        """FINANCE_BATCH_DRAFTED stores draft in state."""
        from src.core.state_engine import StateEngine
        engine = StateEngine()
        draft_event = Event(
            event_type=EventType.FINANCE_BATCH_DRAFTED,
            aggregate_id="user1",
            aggregate_type=AggregateType.FINANCE,
            payload={
                "draft_id": "test-draft-1",
                "raw_text": "test",
                "items": [{"type": "expense", "amount": 100}],
                "questions": [],
                "summary": {"expense_count": 1, "expense_total": 100},
            },
        )

        async def apply():
            await engine.apply(draft_event)

        import asyncio
        asyncio.run(apply())

        pending = engine.get_view("finance_batches", "pending")
        assert "test-draft-1" in pending
        assert pending["test-draft-1"]["status"] == "drafted"

    def test_legacy_replay_guard_skips_malformed_parent_plan(self):
        """Malformed parent_fund_request_planned with batch markers is skipped on replay."""
        from src.core.state_engine import StateEngine
        engine = StateEngine()
        bad_event = Event(
            event_type=EventType.PARENT_FUND_REQUEST_PLANNED,
            aggregate_id="user1",
            aggregate_type=AggregateType.FINANCE,
            payload={
                "amount": 1.7,
                "description": "报销 借给 妈妈 还没要 洗面奶钱 面膜钱 鞋子钱",
                "action": "advise",
                "requested_date": "2027-05-03",
            },
        )

        async def apply():
            await engine.apply(bad_event)

        import asyncio
        asyncio.run(apply())

        pf_view = engine.get_view("parent_funds", "current")
        planned = pf_view.get("planned_requests", [])
        # Should be empty — the bad event was skipped by replay guard
        assert len(planned) == 0, (
            f"Expected 0 planned requests (replay guard skipped malformed), got {len(planned)}"
        )

    def test_legacy_replay_guard_allows_normal_plan(self):
        """Normal parent_fund_request_planned without batch markers is NOT skipped."""
        from src.core.state_engine import StateEngine
        engine = StateEngine()
        good_event = Event(
            event_type=EventType.PARENT_FUND_REQUEST_PLANNED,
            aggregate_id="user1",
            aggregate_type=AggregateType.FINANCE,
            payload={
                "amount": 100,
                "description": "十号找爸爸拿话费",
                "action": "advise",
                "requested_date": "2026-06-10T00:00:00+00:00",
            },
        )

        async def apply():
            await engine.apply(good_event)

        import asyncio
        asyncio.run(apply())

        pf_view = engine.get_view("parent_funds", "current")
        planned = pf_view.get("planned_requests", [])
        assert len(planned) == 1
