"""Test: proposal approval persistence — StateEngine replay and recovery.

Verifies that proposal created/accepted/rejected/expired state is fully
recoverable from the event log via StateEngine handlers.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.core.events import Event, EventType, AggregateType
from src.core.proposal import Proposal, ProposalStatus, TargetSystem
from src.core.state_engine import StateEngine


# ── Helpers ────────────────────────────────────────────────────────────

def _make_proposal(
    proposal_id: str = "p-test",
    title: str = "Test Task",
    user_id: str = "u1",
    target_system: TargetSystem = TargetSystem.GOOGLE_CALENDAR,
) -> Proposal:
    return Proposal(
        proposal_id=proposal_id,
        user_id=user_id,
        target_system=target_system,
        action_payload={"title": title, "operation": "create_calendar_event"},
        reason="test",
        confidence=0.9,
    )


def _created_event(proposal: Proposal) -> Event:
    return Event(
        event_type=EventType.EXECUTION_PROPOSAL_CREATED,
        aggregate_id=proposal.proposal_id,
        aggregate_type=AggregateType.SYSTEM,
        payload=proposal.to_dict(),
    )


def _accepted_event(proposal: Proposal) -> Event:
    p_dict = proposal.to_dict()
    p_dict["status"] = "accepted"
    return Event(
        event_type=EventType.EXECUTION_PROPOSAL_ACCEPTED,
        aggregate_id=proposal.proposal_id,
        aggregate_type=AggregateType.SYSTEM,
        payload=p_dict,
    )


def _rejected_event(proposal: Proposal) -> Event:
    p_dict = proposal.to_dict()
    p_dict["status"] = "rejected"
    return Event(
        event_type=EventType.EXECUTION_PROPOSAL_REJECTED,
        aggregate_id=proposal.proposal_id,
        aggregate_type=AggregateType.SYSTEM,
        payload=p_dict,
    )


def _expired_event(proposal_id: str) -> Event:
    return Event(
        event_type=EventType.EXECUTION_PROPOSAL_EXPIRED,
        aggregate_id=proposal_id,
        aggregate_type=AggregateType.SYSTEM,
        payload={"proposal_id": proposal_id, "expired_at": datetime.now(timezone.utc).isoformat()},
    )


# ── Test 1: created → pending_proposals ────────────────────────────────

@pytest.mark.asyncio
async def test_created_event_stores_in_pending():
    """EXECUTION_PROPOSAL_CREATED → pending_proposals with full data."""
    engine = StateEngine()
    p = _make_proposal("p-1")
    await engine.apply(_created_event(p))

    view = engine.get_view("proposal", "active")
    pending = view.get("pending_proposals", {})
    assert "p-1" in pending
    assert pending["p-1"]["status"] == "pending"
    assert pending["p-1"]["target_system"] == "google_calendar"
    assert pending["p-1"]["proposal_type"] == "create_calendar_block"
    assert "action_payload" in pending["p-1"]
    assert pending["p-1"]["action_payload"]["title"] == "Test Task"
    assert "created_at" in pending["p-1"]


# ── Test 2: created + accepted → accepted_proposals ────────────────────

@pytest.mark.asyncio
async def test_created_accepted_moves_to_accepted():
    """created + accepted → not in pending, in accepted_proposals, status=accepted."""
    engine = StateEngine()
    p = _make_proposal("p-2")
    await engine.apply(_created_event(p))
    await engine.apply(_accepted_event(p))

    view = engine.get_view("proposal", "active")
    pending = view.get("pending_proposals", {})
    accepted = view.get("accepted_proposals", {})

    assert "p-2" not in pending
    assert "p-2" in accepted
    assert accepted["p-2"]["status"] == "accepted"
    assert "accepted_at" in accepted["p-2"]
    assert accepted["p-2"]["target_system"] == "google_calendar"
    assert accepted["p-2"]["action_payload"]["title"] == "Test Task"


# ── Test 3: created + rejected → rejected_proposals ────────────────────

@pytest.mark.asyncio
async def test_created_rejected_moves_to_rejected():
    """created + rejected → not in pending, in rejected_proposals, status=rejected."""
    engine = StateEngine()
    p = _make_proposal("p-3")
    await engine.apply(_created_event(p))
    await engine.apply(_rejected_event(p))

    view = engine.get_view("proposal", "active")
    pending = view.get("pending_proposals", {})
    rejected = view.get("rejected_proposals", {})

    assert "p-3" not in pending
    assert "p-3" in rejected
    assert rejected["p-3"]["status"] == "rejected"
    assert "rejected_at" in rejected["p-3"]


# ── Test 4: created + expired → expired_proposals ──────────────────────

@pytest.mark.asyncio
async def test_created_expired_moves_to_expired():
    """created + expired → in expired_proposals, status=expired, expired_at present."""
    engine = StateEngine()
    p = _make_proposal("p-4")
    await engine.apply(_created_event(p))
    await engine.apply(_expired_event("p-4"))

    view = engine.get_view("proposal", "active")
    pending = view.get("pending_proposals", {})
    expired = view.get("expired_proposals", {})

    assert "p-4" not in pending
    assert "p-4" in expired
    assert expired["p-4"]["status"] == "expired"
    assert "expired_at" in expired["p-4"]


# ── Test 5: accepted upsert when pending missing ───────────────────────

@pytest.mark.asyncio
async def test_accepted_upsert_without_pending():
    """accepted event with full payload upserts even when pending is empty."""
    engine = StateEngine()
    p = _make_proposal("p-upsert")
    # Do NOT apply created event — simulate missing pending
    await engine.apply(_accepted_event(p))

    view = engine.get_view("proposal", "active")
    accepted = view.get("accepted_proposals", {})

    assert "p-upsert" in accepted
    assert accepted["p-upsert"]["status"] == "accepted"
    assert "accepted_at" in accepted["p-upsert"]
    assert accepted["p-upsert"]["target_system"] == "google_calendar"
    assert accepted["p-upsert"]["action_payload"]["title"] == "Test Task"


@pytest.mark.asyncio
async def test_rejected_upsert_without_pending():
    """rejected event with full payload upserts even when pending is empty."""
    engine = StateEngine()
    p = _make_proposal("p-upsert-r")
    await engine.apply(_rejected_event(p))

    view = engine.get_view("proposal", "active")
    rejected = view.get("rejected_proposals", {})

    assert "p-upsert-r" in rejected
    assert rejected["p-upsert-r"]["status"] == "rejected"
    assert "rejected_at" in rejected["p-upsert-r"]


# ── Test 6: expired upsert when pending missing ────────────────────────

@pytest.mark.asyncio
async def test_expired_upsert_without_pending():
    """expired event upserts even when pending does not contain the proposal."""
    engine = StateEngine()
    # Build expired event with full payload
    p = _make_proposal("p-exp-upsert")
    p_dict = p.to_dict()
    p_dict["status"] = "expired"
    event = Event(
        event_type=EventType.EXECUTION_PROPOSAL_EXPIRED,
        aggregate_id="p-exp-upsert",
        aggregate_type=AggregateType.SYSTEM,
        payload=p_dict,
    )
    await engine.apply(event)

    view = engine.get_view("proposal", "active")
    expired = view.get("expired_proposals", {})

    assert "p-exp-upsert" in expired
    assert expired["p-exp-upsert"]["status"] == "expired"
    assert "expired_at" in expired["p-exp-upsert"]


# ── Test 7: replay determinism ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_proposal_replay_deterministic():
    """Same proposal events → same state hash regardless of apply order."""
    p = _make_proposal("p-replay")
    events = [
        _created_event(p),
        _accepted_event(p),
    ]

    e1 = StateEngine()
    for ev in events:
        await e1.apply(ev)
    h1 = e1.state_hash()

    e2 = StateEngine()
    await e2.rebuild_from_events(events)
    h2 = e2.state_hash()

    assert h1 == h2


# ── Test 8: accepted proposal reconstructs via Proposal.from_dict() ────

@pytest.mark.asyncio
async def test_accepted_proposal_reconstructs_from_state_engine():
    """After replay, Proposal.from_dict() on accepted_proposals entry works."""
    p = _make_proposal("p-recon", title="Reconstruct Me")
    events = [
        _created_event(p),
        _accepted_event(p),
    ]

    engine = StateEngine()
    await engine.rebuild_from_events(events)

    view = engine.get_view("proposal", "active")
    accepted = view.get("accepted_proposals", {})
    proposal_dict = accepted.get("p-recon")
    assert proposal_dict is not None

    reconstructed = Proposal.from_dict(proposal_dict)
    assert reconstructed.status == ProposalStatus.ACCEPTED
    assert reconstructed.target_system == TargetSystem.GOOGLE_CALENDAR
    assert reconstructed.action_payload.get("title") == "Reconstruct Me"
    assert reconstructed.proposal_id == "p-recon"


# ── Test 9: get_proposal() ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_proposal_finds_in_pending():
    engine = StateEngine()
    p = _make_proposal("p-gp-1")
    await engine.apply(_created_event(p))
    result = engine.get_proposal("p-gp-1")
    assert result is not None
    assert result["status"] == "pending"


@pytest.mark.asyncio
async def test_get_proposal_finds_in_accepted():
    engine = StateEngine()
    p = _make_proposal("p-gp-2")
    await engine.apply(_created_event(p))
    await engine.apply(_accepted_event(p))
    result = engine.get_proposal("p-gp-2")
    assert result is not None
    assert result["status"] == "accepted"


@pytest.mark.asyncio
async def test_get_proposal_unknown():
    engine = StateEngine()
    assert engine.get_proposal("nonexistent") is None


# ── Test 10: PR #2 gate test still passes (smoke) ─────────────────────

@pytest.mark.asyncio
async def test_schedule_mirror_gate_unchanged():
    """Verify PR #2 gate still blocks without proposal."""
    from src.executor.google_calendar.executor import GoogleCalendarExecutor
    from src.infrastructure.config import Settings

    settings = Settings(
        google_calendar_mock=False,
        google_calendar_schedule_write_enabled=True,
        google_calendar_write_requires_acceptance=True,
    )
    executor = GoogleCalendarExecutor(use_mock=False, settings=settings)
    result = await executor.sync_schedule_blocks([])
    assert result.get("ok") is False
    assert "proposal_required" in result.get("error", "")


# ── Test 11: multiple proposals independent ────────────────────────────

@pytest.mark.asyncio
async def test_multiple_proposals_independent():
    """Multiple proposals tracked independently."""
    engine = StateEngine()
    p_a = _make_proposal("p-a")
    p_b = _make_proposal("p-b")
    p_c = _make_proposal("p-c", title="Task C")

    await engine.apply(_created_event(p_a))
    await engine.apply(_created_event(p_b))
    await engine.apply(_created_event(p_c))
    await engine.apply(_accepted_event(p_a))
    await engine.apply(_rejected_event(p_b))

    assert engine.get_proposal("p-a")["status"] == "accepted"
    assert engine.get_proposal("p-b")["status"] == "rejected"
    assert engine.get_proposal("p-c")["status"] == "pending"


# ── Test 12: acceptance_history recorded ───────────────────────────────

@pytest.mark.asyncio
async def test_acceptance_history_recorded():
    """acceptance_history records accept and reject actions."""
    engine = StateEngine()
    p = _make_proposal("p-hist")
    await engine.apply(_created_event(p))
    await engine.apply(_accepted_event(p))

    view = engine.get_view("proposal", "active")
    history = view.get("acceptance_history", [])
    assert len(history) >= 1
    assert history[-1]["proposal_id"] == "p-hist"
    assert history[-1]["action"] == "accepted"
