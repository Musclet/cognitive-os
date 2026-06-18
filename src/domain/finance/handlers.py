"""Finance domain event handlers.

Processes USER_COMMAND_RECEIVED events for finance/parent-fund commands
and produces domain events.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from src.core.events import Event, EventType, AggregateType
from src.domain.finance.classifier import classify_transaction, CATEGORY_LABELS
from src.domain.finance.parent_fund import (
    DEFAULT_FIXED_ITEMS,
    apply_request_record,
    compute_next_safe_date,
    compute_due_items,
    compute_30_day_request_schedule,
    compute_weekly_total,
    schedule_request_advice,
)
from src.domain.finance.warnings import generate_spending_warnings

TRIGGER_EVENTS = {
    EventType.USER_COMMAND_RECEIVED,
}

LOCAL_TZ = timezone.utc

_CN_DAY_DIGITS = {
    "零": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}


def _parse_chinese_day(value: str) -> int | None:
    """Parse small Chinese day numerals such as 十, 十五, 二十一."""
    value = value.strip()
    if not value:
        return None
    if value.isdigit():
        return int(value)
    if value == "十":
        return 10
    if "十" in value:
        left, _, right = value.partition("十")
        tens = _CN_DAY_DIGITS.get(left, 1) if left else 1
        ones = _CN_DAY_DIGITS.get(right, 0) if right else 0
        return tens * 10 + ones
    return _CN_DAY_DIGITS.get(value)


def _strip_date_tokens(text: str) -> str:
    """Remove date-like tokens before amount parsing."""
    cleaned = text
    cleaned = re.sub(r"\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*[号日]?", "", cleaned)
    cleaned = re.sub(r"\d{1,2}\s*月\s*\d{1,2}\s*[号日]?", "", cleaned)
    cleaned = re.sub(r"\d{1,2}\s*[号日]", "", cleaned)
    cleaned = re.sub(r"[一二两三四五六七八九十]{1,3}\s*[号日]", "", cleaned)
    return cleaned


def _parse_requested_date(text: str, now: datetime | None = None) -> datetime | None:
    """Parse an intended request date from finance text."""
    if now is None:
        now = datetime.now(LOCAL_TZ)

    text = text.strip()
    if "明天" in text:
        return (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    if "后天" in text:
        return (now + timedelta(days=2)).replace(hour=0, minute=0, second=0, microsecond=0)

    m = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*[号日]?", text)
    if m:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=now.tzinfo)

    m = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[号日]?", text)
    if m:
        year = now.year
        candidate = datetime(year, int(m.group(1)), int(m.group(2)), tzinfo=now.tzinfo)
        if candidate.date() < now.date():
            candidate = datetime(year + 1, int(m.group(1)), int(m.group(2)), tzinfo=now.tzinfo)
        return candidate

    m = re.search(r"(\d{1,2}|[一二两三四五六七八九十]{1,3})\s*[号日]", text)
    if m:
        day = _parse_chinese_day(m.group(1))
        if day and 1 <= day <= 31:
            year = now.year
            month = now.month
            candidate = datetime(year, month, day, tzinfo=now.tzinfo)
            if candidate.date() < now.date():
                if month == 12:
                    candidate = datetime(year + 1, 1, day, tzinfo=now.tzinfo)
                else:
                    candidate = datetime(year, month + 1, day, tzinfo=now.tzinfo)
            return candidate

    return None


def _parse_amount(text: str) -> float | None:
    """Extract the leading number from a Chinese spending text.

    '奶茶18' -> 18.0
    '出去玩120' -> 120.0
    '今天生活费到账1000' -> 1000.0
    """
    text_without_dates = _strip_date_tokens(text)
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:元|块钱|块|人民币|rmb|RMB)", text_without_dates)
    if m:
        return float(m.group(1))
    m = re.search(r"([一二两三四五六七八九十]{1,3})\s*(?:元|块钱|块)", text_without_dates)
    if m:
        parsed = _parse_chinese_day(m.group(1))
        if parsed is not None:
            return float(parsed)
    m = re.search(r"(\d+(?:\.\d+)?)", text_without_dates)
    if m:
        return float(m.group(1))
    return None


def _is_finance_input(text: str) -> bool:
    """Check if text looks like a finance transaction or parent fund input."""
    # Transaction: number embedded in text (e.g. 奶茶18, 出去玩120)
    has_amount = bool(re.search(r"\d+", text))

    # Income keywords
    income_kw = {"生活费到账", "生活费", "到账", "发生活费", "收入"}

    # Parent request keywords
    parent_kw = {"找爸爸", "找妈妈", "跟爸爸", "跟妈妈", "爸爸给", "妈妈给", "要了", "给了"}

    # Parent planning keywords
    parent_plan_kw = {"想找爸爸", "想跟爸爸", "什么时候要", "要钱计划", "下次提醒我", "要钱", "问爸爸", "问妈妈"}

    for kw in income_kw:
        if kw in text:
            return True
    for kw in parent_kw:
        if kw in text:
            return True
    for kw in parent_plan_kw:
        if kw in text:
            return True

    # Recognized menu commands (no number needed)
    if text in {"本月资金", "出去玩额度", "攒钱进度", "要钱计划", "30天要钱排期", "要钱排期", "记一笔"}:
        return True

    # General transaction: has amount + no command prefix
    if has_amount and not text.startswith("/"):
        # It has a number and isn't an art pattern → likely transaction
        if not any(p in text for p in ("早安", "早~", "完成", "做完了", "完成了")):
            # Check it actually has a number with amount context
            m = _parse_amount(text)
            if m is not None and m > 0:
                return True

    return False


def _is_parent_planning_input(text: str) -> bool:
    """Check if text is asking about parent fund planning/advice."""
    plan_kw = {"想找爸爸", "想找妈妈", "什么时候要", "要钱计划", "下次提醒我", "想跟爸爸", "想跟妈妈", "问爸爸", "问妈妈"}
    for kw in plan_kw:
        if kw in text:
            return True
    return False


def _is_parent_request_text(text: str) -> bool:
    """Check whether text is about asking a parent for money."""
    parent_kw = {"爸爸", "妈妈", "找爸爸", "找妈妈", "跟爸爸", "跟妈妈", "叫爸爸", "叫妈妈", "问爸爸", "问妈妈"}
    action_kw = {"要", "拿", "给", "转", "生活费"}
    return any(kw in text for kw in parent_kw) and any(kw in text for kw in action_kw)


def _is_parent_actual_input(text: str) -> bool:
    """Check if text is recording an actual parent fund request."""
    actual_kw = {"找爸爸要了", "跟爸爸要了", "找妈妈要了", "跟妈妈要了", "今天要了", "爸爸给了", "妈妈给了"}
    for kw in actual_kw:
        if kw in text:
            return True
    return False


def _is_income_input(text: str) -> bool:
    """Check if text is recording income."""
    income_kw = {"生活费到账", "到账"}
    for kw in income_kw:
        if kw in text:
            return True
    return False


def _extract_item_id(text: str) -> str | None:
    """Try to extract a fixed item_id from description text."""
    for item in DEFAULT_FIXED_ITEMS:
        if item["label"] in text or item["item_id"] in text:
            return item["item_id"]
    return None


async def handle_finance_command(event: Event) -> list[Event]:
    """Route finance-related USER_COMMAND_RECEIVED to events.

    Uses classification + amount extraction + parent fund scheduling logic
    in a deterministic pipeline. No IO.
    """
    command = event.payload.get("command", "")
    params = event.payload.get("params", {})
    raw_text = params.get("raw_text", "")

    if not raw_text:
        return []

    # ── Direct command routes ───────────────────────────────────────────
    if raw_text == "本月资金":
        return [
            Event(
                event_type=EventType.FINANCE_BUDGET_UPDATED,
                aggregate_id=event.aggregate_id,
                aggregate_type=AggregateType.FINANCE,
                causation_id=event.event_id,
                payload={"action": "show_monthly"},
            )
        ]

    if raw_text == "出去玩额度":
        return [
            Event(
                event_type=EventType.FINANCE_BUDGET_UPDATED,
                aggregate_id=event.aggregate_id,
                aggregate_type=AggregateType.FINANCE,
                causation_id=event.event_id,
                payload={"action": "show_outing"},
            )
        ]

    if raw_text == "攒钱进度":
        return [
            Event(
                event_type=EventType.FINANCE_BUDGET_UPDATED,
                aggregate_id=event.aggregate_id,
                aggregate_type=AggregateType.FINANCE,
                causation_id=event.event_id,
                payload={"action": "show_savings"},
            )
        ]

    if raw_text == "要钱计划":
        return [
            Event(
                event_type=EventType.PARENT_FUND_REQUEST_PLANNED,
                aggregate_id=event.aggregate_id,
                aggregate_type=AggregateType.FINANCE,
                causation_id=event.event_id,
                payload={"action": "show_plan"},
            )
        ]

    if raw_text == "记一笔":
        return [
            Event(
                event_type=EventType.NOTIFICATION_SEND,
                aggregate_id=event.aggregate_id,
                aggregate_type=AggregateType.USER,
                causation_id=event.event_id,
                payload={"message": "请直接输入金额和用途，例如：\n奶茶18\n出去玩120\n晚饭52 和对象"},
            )
        ]

    requested_date = _parse_requested_date(raw_text, event.timestamp)

    # ── Parent fund future plan ─────────────────────────────────────────
    if requested_date and _is_parent_request_text(raw_text):
        amount = _parse_amount(raw_text)
        if amount is None:
            amount = 0.0
        item_id = _extract_item_id(raw_text)
        category = classify_transaction(raw_text)
        return [
            Event(
                event_type=EventType.PARENT_FUND_REQUEST_PLANNED,
                aggregate_id=event.aggregate_id,
                aggregate_type=AggregateType.FINANCE,
                causation_id=event.event_id,
                payload={
                    "amount": amount,
                    "description": raw_text,
                    "item_id": item_id,
                    "category": category,
                    "action": "advise",
                    "requested_date": requested_date.isoformat(),
                },
            )
        ]

    # ── Income recording ────────────────────────────────────────────────
    if _is_income_input(raw_text):
        amount = _parse_amount(raw_text)
        if amount and amount > 0:
            source = "生活费" if "生活费" in raw_text else "其他"
            return [
                Event(
                    event_type=EventType.FINANCE_INCOME_RECORDED,
                    aggregate_id=event.aggregate_id,
                    aggregate_type=AggregateType.FINANCE,
                    causation_id=event.event_id,
                    payload={
                        "amount": amount,
                        "source": source,
                        "description": raw_text,
                        "user_id": event.aggregate_id,
                    },
                )
            ]

    # ── Parent fund actual request ─────────────────────────────────────
    if _is_parent_actual_input(raw_text):
        amount = _parse_amount(raw_text)
        if amount and amount > 0:
            description = raw_text
            item_id = _extract_item_id(raw_text)
            person = "妈妈" if "妈妈" in raw_text else "爸爸" if "爸爸" in raw_text else "家庭"
            return [
                Event(
                    event_type=EventType.PARENT_FUND_REQUEST_RECORDED,
                    aggregate_id=event.aggregate_id,
                    aggregate_type=AggregateType.FINANCE,
                    causation_id=event.event_id,
                    payload={
                        "amount": amount,
                        "description": description,
                        "item_id": item_id,
                    },
                ),
                Event(
                    event_type=EventType.PARENT_FUND_RECEIVED,
                    aggregate_id=event.aggregate_id,
                    aggregate_type=AggregateType.FINANCE,
                    causation_id=event.event_id,
                    payload={
                        "amount": amount,
                        "description": description,
                        "item_id": item_id,
                        "source": person,
                    },
                ),
                Event(
                    event_type=EventType.FINANCE_INCOME_RECORDED,
                    aggregate_id=event.aggregate_id,
                    aggregate_type=AggregateType.FINANCE,
                    causation_id=event.event_id,
                    payload={
                        "amount": amount,
                        "source": person,
                        "description": description,
                    },
                ),
            ]

    # ── Parent fund advice/planning ────────────────────────────────────
    if _is_parent_planning_input(raw_text):
        amount = _parse_amount(raw_text)
        if amount is None:
            amount = 0.0
        description = raw_text
        item_id = _extract_item_id(raw_text)
        category = classify_transaction(raw_text)
        return [
            Event(
                event_type=EventType.PARENT_FUND_REQUEST_PLANNED,
                aggregate_id=event.aggregate_id,
                aggregate_type=AggregateType.FINANCE,
                causation_id=event.event_id,
                payload={
                    "amount": amount,
                    "description": description,
                    "item_id": item_id,
                    "category": category,
                    "action": "advise",
                    "requested_date": requested_date.isoformat() if requested_date else None,
                },
            )
        ]

    # ── Finance transaction ─────────────────────────────────────────────
    amount = _parse_amount(raw_text)
    if amount and amount > 0 and not raw_text.startswith("/"):
        category = classify_transaction(raw_text)
        description = raw_text
        return [
            Event(
                event_type=EventType.FINANCE_TRANSACTION_RECORDED,
                aggregate_id=event.aggregate_id,
                aggregate_type=AggregateType.FINANCE,
                causation_id=event.event_id,
                payload={
                    "amount": amount,
                    "description": description,
                    "category": category,
                    "user_id": event.aggregate_id,
                },
            )
        ]

    return []


# ── Output formatting helpers ────────────────────────────────────────────────


def format_transaction_feedback(
    amount: float,
    category: str,
    description: str,
    outing_spent: float | None = None,
    outing_budget: float = 250,
    estimated_savings: float | None = None,
    savings_target: float = 500,
    current_month_outflow: float = 0,
    current_month_inflow: float = 0,
) -> str:
    """Format feedback after recording a transaction.

    Includes category, amount, outing status, savings status, warnings.
    """
    category_label = CATEGORY_LABELS.get(category, "其他")
    lines: list[str] = [
        f"📝 已记录：{description}",
        f"  金额：{amount:.0f} 元",
        f"  分类：{category_label}",
    ]

    warnings = generate_spending_warnings(
        current_outing_spent=outing_spent or 0,
        outing_budget=outing_budget,
        savings_target=savings_target,
        estimated_savings=estimated_savings,
        current_month_outflow=current_month_outflow,
        current_month_inflow=current_month_inflow,
    )
    for w in warnings:
        lines.append(f"\n{w}")

    return "\n".join(lines)


def format_parent_advice(advice: dict[str, Any]) -> str:
    """Format parent fund request advice for Telegram."""
    lines: list[str] = []

    requested = advice.get("requested_date")
    if requested:
        try:
            requested_dt = datetime.fromisoformat(str(requested))
            date_text = requested_dt.strftime("%m月%d日")
        except (ValueError, TypeError):
            date_text = str(requested)
        amount = float(advice.get("amount", 0) or 0)
        description = advice.get("description", "")
        if amount > 0:
            lines.append(f"🗓️ 已作为预期要钱计划：{date_text}，{amount:.0f} 元")
        else:
            lines.append(f"🗓️ 已作为预期要钱计划：{date_text}")
        if description:
            lines.append(f"  用途：{description}")
        lines.append("  30天排期会避开这一天。")

    if advice.get("due_items"):
        lines.append("📋 到期应缴项目：")
        for item in advice["due_items"]:
            amount = item.get("amount", 0)
            label = item.get("label", item.get("item_id", ""))
            due_date = item.get("due_date", "")
            lines.append(f"  • {label}：{amount:.0f} 元（到期日 {due_date}）")

    if advice.get("safe"):
        lines.append(f"✅ {advice['reason']}")
    else:
        lines.append(f"❌ {advice['reason']}")

    recommended = advice.get("recommended_date", "")
    if recommended:
        lines.append(f"  建议请求日期：{recommended}")

    if advice.get("split_suggestion"):
        lines.append("\n📦 分笔建议：")
        for chunk in advice["split_suggestion"]:
            lines.append(f"  • {chunk['date']}：{chunk['amount']:.0f} 元 {chunk.get('label', '')}")

    for w in advice.get("warnings", []):
        lines.append(f"\n{w}")

    return "\n".join(lines)


def format_monthly_summary(
    inflow: float = 0,
    outflow: float = 0,
    by_category: dict[str, float] | None = None,
    outing_spent: float = 0,
    outing_budget: float = 250,
    savings_target: float = 500,
) -> str:
    """Format monthly financial summary."""
    estimated_savings = max(0, inflow - outflow)
    lines: list[str] = [
        f"📊 本月资金概况",
        f"  收入：{inflow:.0f} 元",
        f"  支出：{outflow:.0f} 元",
        f"  预估储蓄：{estimated_savings:.0f} 元（目标 {savings_target} 元）",
        f"  约会/出去玩：{outing_spent:.0f}/{outing_budget} 元",
    ]

    if by_category:
        lines.append("\n📂 支出分类：")
        for cat, amt in sorted(by_category.items(), key=lambda x: -x[1]):
            label = CATEGORY_LABELS.get(cat, cat)
            lines.append(f"  • {label}：{amt:.0f} 元")

    if estimated_savings < savings_target and inflow > 0:
        lines.append(f"\n⚠️ 预计储蓄 {estimated_savings:.0f} 元低于目标 {savings_target} 元，需控制支出。")
    elif inflow == 0:
        lines.append("\n💡 本月生活费尚未到账。")

    return "\n".join(lines)


def format_outing_status(outing_spent: float, outing_budget: float = 250) -> str:
    """Format outing budget status."""
    remaining = max(0, outing_budget - outing_spent)
    lines: list[str] = [
        f"🎯 约会/出去玩额度",
        f"  已用：{outing_spent:.0f} / {outing_budget} 元",
    ]
    if outing_spent >= outing_budget:
        lines.append("  状态：❌ 已超支，不建议再花。")
    elif outing_spent >= outing_budget * 0.8:
        lines.append(f"  状态：⚠️ 接近上限，剩余 {remaining:.0f} 元。")
    elif remaining > 0:
        lines.append(f"  剩余：{remaining:.0f} 元")
    return "\n".join(lines)


def format_savings_progress(
    inflow: float = 0,
    outflow: float = 0,
    savings_target: float = 500,
) -> str:
    """Format savings progress."""
    estimated = max(0, inflow - outflow)
    progress_pct = min(100, int((estimated / savings_target) * 100)) if savings_target > 0 else 0
    lines: list[str] = [
        f"💰 攒钱进度",
        f"  收入：{inflow:.0f} 元",
        f"  支出：{outflow:.0f} 元",
        f"  预计储蓄：{estimated:.0f} / {savings_target} 元（{progress_pct}%）",
    ]
    if estimated >= savings_target:
        lines.append("  状态：✅ 达标！")
    elif inflow == 0:
        lines.append("  状态：⏳ 收入未记录，先记生活费到账。")
    else:
        deficit = savings_target - estimated
        lines.append(f"  状态：⚠️ 还差 {deficit:.0f} 元")
    return "\n".join(lines)


def format_parent_plan(
    request_log: list[dict[str, Any]],
    received_log: list[dict[str, Any]],
    fixed_items: list[dict[str, Any]],
    next_safe_date: datetime | None = None,
    weekly_total: float = 0,
) -> str:
    """Format parent fund plan overview."""
    lines: list[str] = [
        "📋 要钱计划",
    ]

    if next_safe_date:
        lines.append(f"  下次安全日期：{next_safe_date.strftime('%m月%d日')}")
    lines.append(f"  本周已要：{weekly_total:.0f} 元")

    due_items = compute_due_items(fixed_items, request_log, received_log, None)
    if due_items:
        lines.append("\n📌 待缴费项目：")
        for item in due_items:
            amount = item.get("amount", 0)
            label = item.get("label", item.get("item_id", ""))
            due_date = item.get("due_date", "")
            lines.append(f"  • {label}：{amount:.0f} 元（{due_date}到期）")

    if request_log:
        lines.append("\n📜 最近请求记录：")
        for entry in request_log[-5:]:
            ts = entry.get("timestamp", "")[5:16] if entry.get("timestamp", "") else ""
            amt = entry.get("amount", 0)
            desc = entry.get("description", "")
            lines.append(f"  • {ts}：{amt:.0f} 元「{desc}」")

    return "\n".join(lines)


def format_parent_30_day_schedule(
    request_log: list[dict[str, Any]],
    received_log: list[dict[str, Any]],
    fixed_items: list[dict[str, Any]],
    planned_requests: list[dict[str, Any]] | None = None,
    safe_interval_days: int = 3,
    now: datetime | None = None,
) -> str:
    """Format the next 30 days of fixed parent-fund requests."""
    schedule = compute_30_day_request_schedule(
        fixed_items=fixed_items,
        request_log=request_log,
        received_log=received_log,
        planned_requests=planned_requests,
        safe_interval_days=safe_interval_days,
        now=now,
        horizon_days=30,
    )
    lines = [
        "📆 30天要钱排期",
        f"  安全间隔：约 {safe_interval_days} 天",
    ]

    if not schedule:
        lines.append("  未来30天暂无固定要钱项。")
        return "\n".join(lines)

    for item in schedule:
        request_date = datetime.fromisoformat(str(item["request_date"]))
        due_date = datetime.fromisoformat(str(item["due_date"]))
        amount = float(item.get("amount", 0))
        label = item.get("label", item.get("item_id", ""))
        suffix = ""
        if item.get("pushed"):
            suffix = f"（到期 {due_date.strftime('%m月%d日')}，已错开）"
        prefix = "预定 " if item.get("planned") else ""
        lines.append(f"  {request_date.strftime('%m月%d日')}：{prefix}{label} {amount:.0f} 元{suffix}")

    return "\n".join(lines)
