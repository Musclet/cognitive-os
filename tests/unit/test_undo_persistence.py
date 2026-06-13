"""Test: recent-action undo cache persistence — StateEngine replay and recovery.

Verifies that undoable action metadata survives process restart via
StateEngine handlers for FINANCE_TRANSACTION_RECORDED and
FINANCE_INCOME_RECORDED events.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.events import Event, EventType, AggregateType
from src.core.state_engine import StateEngine
from src.core.proposal import Proposal, ProposalStatus, TargetSystem


# ── Helpers ────────────────────────────────────────────────────────────

def _transaction_event(
    amount: float = 50.0,
    category: str = "餐饮",
    user_id: str = "123",
    description: str = "午餐",
) -> Event:
    return Event(
        event_type=EventType.FINANCE_TRANSACTION_RECORDED,
        aggregate_id=user_id,
        aggregate_type=AggregateType.USER,
        payload={
            "amount": amount,
            "category": category,
            "user_id": user_id,
            "description": description,
        },
    )


def _income_event(
    amount: float = 1000.0,
    source: str = "工资",
    user_id: str = "123",
) -> Event:
    return Event(
        event_type=EventType.FINANCE_INCOME_RECORDED,
        aggregate_id=user_id,
        aggregate_type=AggregateType.USER,
        payload={
            "amount": amount,
            "source": source,
            "user_id": user_id,
        },
    )


def _revert_event(action_id: str, action_type: str = "finance_transaction", amount: float = 50.0, user_id: str = "123") -> Event:
    return Event(
        event_type=EventType.USER_ACTION_REVERTED,
        aggregate_id=user_id,
        aggregate_type=AggregateType.USER,
        payload={
            "action_id": action_id,
            "action_type": action_type,
            "amount": amount,
        },
    )


# ── Test 1: Transaction recorded creates undo metadata ─────────────────

@pytest.mark.asyncio
async def test_transaction_recorded_creates_undo_action():
    """FINANCE_TRANSACTION_RECORDED creates undo action in StateEngine."""
    engine = StateEngine()
    event = _transaction_event(amount=35.0, category="交通")
    await engine.apply(event)

    # Find the auto-generated undo action_id
    undo_view = engine._ensure_aggregate("undo", "actions")
    pending = undo_view.get("pending_actions", {})
    assert len(pending) >= 1

    # Get the first pending action
    action_id = list(pending.keys())[0]
    action = pending[action_id]
    assert action["action_type"] == "finance_transaction"
    assert action["params"]["amount"] == 35.0
    assert action["params"]["category"] == "交通"
    assert action["reverted"] is False


# ── Test 2: Income recorded creates undo metadata ──────────────────────

@pytest.mark.asyncio
async def test_income_recorded_creates_undo_action():
    """FINANCE_INCOME_RECORDED creates undo action in StateEngine."""
    engine = StateEngine()
    event = _income_event(amount=2000.0, source="兼职")
    await engine.apply(event)

    undo_view = engine._ensure_aggregate("undo", "actions")
    pending = undo_view.get("pending_actions", {})
    assert len(pending) >= 1

    action_id = list(pending.keys())[0]
    action = pending[action_id]
    assert action["action_type"] == "finance_income"
    assert action["params"]["amount"] == 2000.0
    assert action["reverted"] is False


# ── Test 3: Replay preserves undo metadata ─────────────────────────────

@pytest.mark.asyncio
async def test_replay_preserves_undo_metadata():
    """After replay, undo action metadata is still accessible."""
    event = _transaction_event(amount=50.0, category="餐饮")

    e1 = StateEngine()
    await e1.apply(event)
    h1 = e1.state_hash()

    # Replay
    e2 = StateEngine()
    await e2.rebuild_from_events([event])
    h2 = e2.state_hash()

    assert h1 == h2

    # Both should have undo metadata
    undo1 = e1._ensure_aggregate("undo", "actions")
    undo2 = e2._ensure_aggregate("undo", "actions")
    assert len(undo1.get("pending_actions", {})) >= 1
    assert len(undo2.get("pending_actions", {})) >= 1


# ── Test 4: get_undo_action() ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_undo_action_found():
    """get_undo_action returns the action metadata."""
    engine = StateEngine()
    event = _transaction_event(amount=25.0, category="零食")
    await engine.apply(event)

    undo_view = engine._ensure_aggregate("undo", "actions")
    action_id = list(undo_view.get("pending_actions", {}).keys())[0]

    result = engine.get_undo_action(action_id)
    assert result is not None
    assert result["params"]["amount"] == 25.0
    assert result["action_type"] == "finance_transaction"


@pytest.mark.asyncio
async def test_get_undo_action_not_found():
    """get_undo_action returns None for unknown ID."""
    engine = StateEngine()
    assert engine.get_undo_action("nonexistent") is None


# ── Test 5: Revert marks pending action as reverted ────────────────────

@pytest.mark.asyncio
async def test_revert_marks_pending_reverted():
    """USER_ACTION_REVERTED marks the pending action reverted=True."""
    engine = StateEngine()
    tx_event = _transaction_event(amount=50.0, category="餐饮")
    await engine.apply(tx_event)

    undo_view = engine._ensure_aggregate("undo", "actions")
    action_id = list(undo_view.get("pending_actions", {}).keys())[0]

    # Revert
    await engine.apply(_revert_event(action_id=action_id, amount=50.0))

    # Check pending action is marked reverted
    pending = undo_view.get("pending_actions", {})
    assert pending[action_id]["reverted"] is True
    assert "reverted_at" in pending[action_id]

    # get_undo_action should still return it (with reverted=True)
    action = engine.get_undo_action(action_id)
    assert action is not None
    assert action["reverted"] is True


# ── Test 6: No double revert ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_double_revert():
    """Reverting the same action_id twice is idempotent."""
    engine = StateEngine()
    tx_event = _transaction_event(amount=50.0, category="餐饮")
    await engine.apply(tx_event)

    undo_view = engine._ensure_aggregate("undo", "actions")
    action_id = list(undo_view.get("pending_actions", {}).keys())[0]

    # Revert twice
    revert1 = _revert_event(action_id=action_id, amount=50.0)
    await engine.apply(revert1)
    revert2 = _revert_event(action_id=action_id, amount=50.0)
    await engine.apply(revert2)

    # Should still only have one revert in history
    undo_user_view = engine._ensure_aggregate("undo", "123")
    reverted_actions = undo_user_view.get("reverted_actions", [])
    count = sum(1 for item in reverted_actions if item.get("action_id") == action_id)
    assert count == 1


# ── Test 7: get_recent_undo_actions() ──────────────────────────────────

@pytest.mark.asyncio
async def test_get_recent_undo_actions():
    """get_recent_undo_actions returns non-reverted actions."""
    engine = StateEngine()
    await engine.apply(_transaction_event(amount=30.0, category="餐饮", user_id="123"))
    await engine.apply(_income_event(amount=500.0, source="红包", user_id="123"))

    actions = engine.get_recent_undo_actions(user_id="123", limit=10)
    assert len(actions) >= 2
    # Should not include reverted actions
    for a in actions:
        assert a["reverted"] is False
        assert a["can_undo"] is True


@pytest.mark.asyncio
async def test_get_recent_undo_actions_excludes_reverted():
    """get_recent_undo_actions by default excludes reverted actions."""
    engine = StateEngine()
    await engine.apply(_transaction_event(amount=30.0, category="餐饮", user_id="123"))
    undo_view = engine._ensure_aggregate("undo", "actions")
    action_id = list(undo_view.get("pending_actions", {}).keys())[0]
    await engine.apply(_revert_event(action_id=action_id, amount=30.0))

    actions = engine.get_recent_undo_actions(user_id="123", limit=10)
    # The reverted one should not appear by default
    for a in actions:
        assert a["reverted"] is False


# ── Test 8: Web undo endpoint with StateEngine fallback ────────────────

def test_web_undo_fallback_to_state_engine():
    """Web undo endpoint uses StateEngine when process cache misses."""
    from src.interface.api.web_routes import router as web_router, COOKIE_NAME

    app = FastAPI()
    app.include_router(web_router)

    settings = MagicMock()
    settings.web_ui_pin = "1234"
    settings.web_ui_session_secret = "test-secret-key"
    settings.web_ui_session_days = 7
    settings.obsidian_vault_path = ""
    settings.telegram_allowed_users = [999]
    settings.google_calendar_mock = True
    settings.google_calendar_write_enabled = True
    app.state.settings = settings

    # Real StateEngine with a seeded undo action
    engine = StateEngine()
    import asyncio
    asyncio.run(engine.apply(_transaction_event(amount=50.0, category="餐饮", user_id="999")))
    asyncio.run(engine.apply(_income_event(amount=200.0, source="退款", user_id="999")))
    app.state.state_engine = engine

    pipeline = MagicMock()
    pipeline.run = AsyncMock(return_value=[])
    app.state.pipeline = pipeline

    client = TestClient(app)
    login_resp = client.post("/api/web/auth/login", json={"pin": "1234"})
    cookie = login_resp.cookies[COOKIE_NAME]

    # Get the action_id from StateEngine
    actions = engine.get_recent_undo_actions(user_id="999", limit=10)
    assert len(actions) >= 1
    action_id = actions[0]["action_id"]

    # Undo with that action_id (process cache doesn't have it)
    resp = client.post(
        "/api/web/actions/undo",
        json={"action_id": action_id},
        cookies={COOKIE_NAME: cookie},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True

    # Verify pipeline was called
    pipeline.run.assert_called()


# ── Test 9: Web undo with unknown action_id returns clear error ────────

def test_web_undo_unknown_action_id():
    """Web undo with unknown action_id returns a clear error."""
    from src.interface.api.web_routes import router as web_router, COOKIE_NAME

    app = FastAPI()
    app.include_router(web_router)

    settings = MagicMock()
    settings.web_ui_pin = "1234"
    settings.web_ui_session_secret = "test-secret-key"
    settings.web_ui_session_days = 7
    settings.obsidian_vault_path = ""
    settings.telegram_allowed_users = [123]
    app.state.settings = settings
    app.state.state_engine = StateEngine()

    pipeline = MagicMock()
    pipeline.run = AsyncMock(return_value=[])
    app.state.pipeline = pipeline

    client = TestClient(app)
    login_resp = client.post("/api/web/auth/login", json={"pin": "1234"})
    cookie = login_resp.cookies[COOKIE_NAME]

    resp = client.post(
        "/api/web/actions/undo",
        json={"action_id": "nonexistent"},
        cookies={COOKIE_NAME: cookie},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert "未找到" in data.get("message", "")
    assert data.get("action_id") == "nonexistent"


# ── Test 10: Duplicate undo blocked ────────────────────────────────────

def test_web_undo_duplicate_blocked():
    """Undoing an already-reverted action returns a clear error."""
    from src.interface.api.web_routes import router as web_router, COOKIE_NAME

    app = FastAPI()
    app.include_router(web_router)

    settings = MagicMock()
    settings.web_ui_pin = "1234"
    settings.web_ui_session_secret = "test-secret-key"
    settings.web_ui_session_days = 7
    settings.obsidian_vault_path = ""
    settings.telegram_allowed_users = [999]
    settings.google_calendar_mock = True
    settings.google_calendar_write_enabled = True
    app.state.settings = settings

    engine = StateEngine()
    import asyncio
    asyncio.run(engine.apply(_transaction_event(amount=50.0, category="餐饮", user_id="999")))
    # Get action_id and revert it
    undo_view = engine._ensure_aggregate("undo", "actions")
    action_id = list(undo_view.get("pending_actions", {}).keys())[0]
    asyncio.run(engine.apply(_revert_event(action_id=action_id, amount=50.0)))
    app.state.state_engine = engine

    pipeline = MagicMock()
    pipeline.run = AsyncMock(return_value=[])
    app.state.pipeline = pipeline

    client = TestClient(app)
    login_resp = client.post("/api/web/auth/login", json={"pin": "1234"})
    cookie = login_resp.cookies[COOKIE_NAME]

    # Try to undo again
    resp = client.post(
        "/api/web/actions/undo",
        json={"action_id": action_id},
        cookies={COOKIE_NAME: cookie},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    # Verify the error message mentions the action is already reverted
    assert "撤回" in data.get("message", "")


# ── Test 11: PR #3 proposal persistence still passes ──────────────────

@pytest.mark.asyncio
async def test_proposal_persistence_unchanged():
    """Proposal persistence from PR #3 still works."""
    engine = StateEngine()
    p = Proposal(proposal_id="p-undo-test", user_id="u1", target_system=TargetSystem.GOOGLE_CALENDAR)
    await engine.apply(Event(
        event_type=EventType.EXECUTION_PROPOSAL_CREATED,
        aggregate_id="p-undo-test",
        aggregate_type=AggregateType.SYSTEM,
        payload=p.to_dict(),
    ))
    assert engine.get_proposal("p-undo-test") is not None
    assert engine.get_proposal("p-undo-test")["status"] == "pending"


# ── Test 12: PR #4 proposal decision still works ───────────────────────

@pytest.mark.asyncio
async def test_proposal_decision_unchanged():
    """Web proposal decision from PR #4 still works with StateEngine lookup."""
    p = Proposal(proposal_id="p-undo-dec", user_id="123", target_system=TargetSystem.GOOGLE_CALENDAR)
    events = [
        Event(
            event_type=EventType.EXECUTION_PROPOSAL_CREATED,
            aggregate_id="p-undo-dec",
            aggregate_type=AggregateType.SYSTEM,
            payload=p.to_dict(),
        ),
        Event(
            event_type=EventType.EXECUTION_PROPOSAL_ACCEPTED,
            aggregate_id="p-undo-dec",
            aggregate_type=AggregateType.SYSTEM,
            payload=p.to_dict(),
        ),
    ]
    engine = StateEngine()
    await engine.rebuild_from_events(events)
    result = engine.get_proposal("p-undo-dec")
    assert result is not None
    assert result["status"] == "accepted"
