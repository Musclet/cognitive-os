"""Finance command validation and event construction."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from src.core.events import AggregateType, Event, EventType


@dataclass(frozen=True)
class FinanceCommandError(Exception):
    message: str
    status_code: int = 400


def build_finance_events(
    action: str,
    body: dict[str, Any],
    user_id: str,
    metadata: dict[str, Any],
) -> list[Event]:
    """Validate one Web finance action and return canonical domain events."""
    if action == "expense":
        amount = _positive_amount(body)
        category = str(body.get("category", "other")).strip() or "other"
        description = str(body.get("description", "")).strip()
        return [_event(
            EventType.FINANCE_TRANSACTION_RECORDED,
            user_id,
            {
                "amount": amount,
                "category": category,
                "description": description or f"消费{amount}",
                "user_id": user_id,
            },
            metadata,
        )]

    if action == "income":
        amount = _positive_amount(body)
        source = str(body.get("source", "other")).strip() or "other"
        description = str(body.get("description", "")).strip()
        return [_event(
            EventType.FINANCE_INCOME_RECORDED,
            user_id,
            {
                "amount": amount,
                "source": source,
                "description": description or f"收入{amount}",
                "user_id": user_id,
            },
            metadata,
        )]

    if action == "parent_received":
        amount = _positive_amount(body)
        person = str(body.get("person", "家庭")).strip() or "家庭"
        description = str(body.get("description", "")).strip()
        item_id = body.get("item_id")
        return [
            _event(
                EventType.PARENT_FUND_REQUEST_RECORDED,
                user_id,
                {"amount": amount, "description": description, "item_id": item_id},
                metadata,
            ),
            _event(
                EventType.PARENT_FUND_RECEIVED,
                user_id,
                {
                    "amount": amount,
                    "description": description,
                    "item_id": item_id,
                    "source": person,
                },
                metadata,
            ),
            _event(
                EventType.FINANCE_INCOME_RECORDED,
                user_id,
                {"amount": amount, "source": person, "description": description},
                metadata,
            ),
        ]

    if action == "parent_plan":
        amount = _positive_amount(body)
        requested_date = str(body.get("requested_date", "")).strip()
        if not requested_date:
            raise FinanceCommandError("requested_date is required")
        _validate_date(requested_date, "requested_date")
        return [_event(
            EventType.PARENT_FUND_REQUEST_PLANNED,
            user_id,
            {
                "amount": amount,
                "person": str(body.get("person", "")).strip(),
                "description": str(body.get("description", "")).strip(),
                "requested_date": requested_date,
                "item_id": body.get("item_id"),
                "category": str(body.get("category", "other")).strip(),
                "action": "advise",
            },
            metadata,
        )]

    if action == "partner_debt":
        amount = _positive_amount(body)
        date_value = str(body.get("date", "")).strip()
        if date_value:
            _validate_date(date_value, "date")
        return [_event(
            EventType.PARTNER_DEBT_CREATED,
            user_id,
            {
                "amount": amount,
                "description": str(body.get("description", "")).strip(),
                "date": date_value,
                "counterparty": str(body.get("counterparty", "对象")).strip(),
            },
            metadata,
        )]

    raise FinanceCommandError(f"unknown action: {action}")


def _positive_amount(body: dict[str, Any]) -> float:
    try:
        amount = float(body.get("amount", 0))
    except (TypeError, ValueError) as exc:
        raise FinanceCommandError("amount must be > 0") from exc
    if amount <= 0:
        raise FinanceCommandError("amount must be > 0")
    return amount


def _validate_date(value: str, field: str) -> None:
    try:
        date.fromisoformat(value[:10])
    except ValueError as exc:
        raise FinanceCommandError(f"{field} is invalid") from exc


def _event(
    event_type: EventType,
    user_id: str,
    payload: dict[str, Any],
    metadata: dict[str, Any],
) -> Event:
    return Event(
        event_type=event_type,
        aggregate_id=user_id,
        aggregate_type=AggregateType.FINANCE,
        payload=payload,
        metadata=dict(metadata),
    )
