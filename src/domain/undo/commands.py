"""Undo event construction."""

from __future__ import annotations

from typing import Any

from src.core.events import AggregateType, Event, EventType


def build_finance_revert_events(
    *,
    user_id: str,
    action_id: str,
    action_type: str,
    amount: float,
    category: str,
    metadata: dict[str, Any],
) -> tuple[Event, Event]:
    """Build the canonical request and revert event pair for a ledger row."""
    requested = Event(
        event_type=EventType.USER_UNDO_REQUESTED,
        aggregate_id=user_id,
        aggregate_type=AggregateType.USER,
        payload={
            "action_id": action_id,
            "action_type": action_type,
            "source": "web_finance_ledger",
        },
        metadata=dict(metadata),
    )
    payload: dict[str, Any] = {
        "action_type": action_type,
        "action_id": action_id,
        "amount": amount,
    }
    if action_type == "finance_transaction":
        payload["category"] = category
    reverted = Event(
        event_type=EventType.USER_ACTION_REVERTED,
        aggregate_id=user_id,
        aggregate_type=AggregateType.USER,
        causation_id=requested.event_id,
        payload=payload,
        metadata=dict(metadata),
    )
    return requested, reverted
