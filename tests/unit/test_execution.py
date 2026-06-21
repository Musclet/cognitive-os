"""Test: Semi-autonomous Execution — proposals, approval, executor, safety."""

import asyncio
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, ".")

from src.core.events import Event, EventType, AggregateType
from src.core.state_engine import StateEngine
from src.core.temporal import TimeBlock, TemporalSource, TimeBlockType
from src.core.proposal import Proposal, ProposalType, ProposalStatus, TargetSystem
from src.domain.execution.handlers import generate_proposals, handle_proposal_accepted, handle_proposal_rejected
from src.executor.google_calendar.executor import GoogleCalendarExecutor, reset_executor


# ── Proposal model ─────────────────────────────────────────────────────

def test_proposal_serialization():
    p = Proposal(
        proposal_type=ProposalType.CREATE_CALENDAR_BLOCK,
        target_system=TargetSystem.GOOGLE_CALENDAR,
        action_payload={"title": "Math", "start": "2026-06-01T19:00:00Z", "end": "2026-06-01T20:00:00Z"},
        reason="Test",
        confidence=0.7,
    )
    d = p.to_dict()
    p2 = Proposal.from_dict(d)
    assert p2.proposal_id == p.proposal_id
    assert p2.proposal_type == p.proposal_type
    assert p2.action_payload == p.action_payload
    print("\u2713 proposal: serialization roundtrip")


def test_proposal_expiration():
    past = datetime.now(timezone.utc) - timedelta(hours=2)
    p = Proposal(expires_at=past)
    assert p.is_expired()

    future = datetime.now(timezone.utc) + timedelta(hours=2)
    p2 = Proposal(expires_at=future)
    assert not p2.is_expired()
    print("\u2713 proposal: expiration logic")


# ── Proposal generation ────────────────────────────────────────────────

async def test_generate_proposals_empty():
    """No windows → no proposals."""
    engine = StateEngine()
    proposals = await generate_proposals(engine)
    assert proposals == []
    print("\u2713 generate: empty state \u2192 no proposals")


async def test_generate_proposals_with_homework():
    """Pending homework + windows → proposals."""
    engine = StateEngine()

    # Add temporal block to trigger planning
    now = datetime.now(timezone.utc)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    block = TimeBlock("b1", TemporalSource.JWXT, TimeBlockType.CLASS_LECTURE,
                      today.replace(hour=8), today.replace(hour=10), "Class")
    await engine.apply(Event(
        EventType.TEMPORAL_BLOCK_ADDED, block.block_id, AggregateType.TEMPORAL,
        payload=block.to_dict(),
    ))

    # Add homework
    await engine.apply(Event(
        EventType.HOMEWORK_NEW, "hw-1", AggregateType.HOMEWORK,
        payload={"title": "Math HW", "course": "Math", "status": "pending"},
    ))

    proposals = await generate_proposals(engine)
    assert len(proposals) > 0
    assert proposals[0].event_type == EventType.EXECUTION_PROPOSAL_CREATED
    payload = proposals[0].payload
    assert "Math HW" in payload.get("action_payload", {}).get("title", "")
    print(f"\u2713 generate: {len(proposals)} proposals for pending homework")


# ── Executor ───────────────────────────────────────────────────────────

async def test_executor_mock():
    """Mock executor creates calendar event without external calls."""
    reset_executor()
    executor = GoogleCalendarExecutor(use_mock=True)

    p = Proposal(
        proposal_type=ProposalType.CREATE_CALENDAR_BLOCK,
        target_system=TargetSystem.GOOGLE_CALENDAR,
        action_payload={"title": "Test", "start": "2026-06-01T19:00:00Z", "end": "2026-06-01T20:00:00Z"},
        status=ProposalStatus.ACCEPTED,
    )

    result = await executor.execute(p)
    assert result.event_type == EventType.EXECUTION_COMPLETED
    assert "mock-event" in result.payload.get("event_id", "")
    print("\u2713 executor: mock execution succeeds")


async def test_executor_rejects_non_accepted():
    """Executor refuses to execute non-accepted proposals."""
    reset_executor()
    executor = GoogleCalendarExecutor(use_mock=True)

    p = Proposal(status=ProposalStatus.PENDING)
    result = await executor.execute(p)
    assert result.event_type == EventType.EXECUTION_FAILED
    assert result.payload.get("error_code") == "google_calendar_proposal_not_accepted"
    print("\u2713 executor: rejects non-accepted proposal")


async def test_schedule_mirror_mock_counts_jwxt_blocks_only():
    executor = GoogleCalendarExecutor(use_mock=True)
    now = datetime.now(timezone.utc) + timedelta(hours=1)
    blocks = [
        TimeBlock("jwxt-1", TemporalSource.JWXT, TimeBlockType.CLASS_LECTURE, now, now + timedelta(hours=1), "课程", "A101"),
        TimeBlock("gcal-1", TemporalSource.GOOGLE_CALENDAR, TimeBlockType.WORKOUT_BLOCK, now, now + timedelta(hours=1), "健身"),
    ]
    result = await executor.sync_schedule_blocks(blocks, days=7, calendar_id="primary")
    assert result["ok"]
    assert result["created"] == 1
    assert result["calendar_id"] == "primary"


# ── Acceptance handler ─────────────────────────────────────────────────

async def test_handle_accepted():
    """Accepted proposal -> emit USER_ACCEPTED_PROPOSAL for gated execution path."""
    reset_executor()
    p = Proposal(
        proposal_type=ProposalType.CREATE_CALENDAR_BLOCK,
        target_system=TargetSystem.GOOGLE_CALENDAR,
        action_payload={"title": "Test", "start": "2026-06-01T19:00:00Z", "end": "2026-06-01T20:00:00Z"},
        status=ProposalStatus.ACCEPTED,
    )
    ev = Event(
        EventType.EXECUTION_PROPOSAL_ACCEPTED, p.proposal_id, AggregateType.SYSTEM,
        payload=p.to_dict(),
    )
    results = await handle_proposal_accepted(ev)
    assert len(results) == 1
    assert results[0].event_type == EventType.USER_ACCEPTED_PROPOSAL
    print("\u2713 handler: accepted \u2192 user accepted event")


async def test_handle_rejected():
    """Rejected proposal -> emit USER_REJECTED_PROPOSAL."""
    ev = Event(
        EventType.EXECUTION_PROPOSAL_REJECTED, "p1", AggregateType.SYSTEM,
        payload={"proposal_id": "p1"},
    )
    results = await handle_proposal_rejected(ev)
    assert len(results) == 1
    assert results[0].event_type == EventType.USER_REJECTED_PROPOSAL
    print("\u2713 handler: rejected \u2192 user rejected event")


# ── StateEngine proposal tracking ──────────────────────────────────────

async def test_se_proposal_tracking():
    engine = StateEngine()

    p = Proposal(
        proposal_type=ProposalType.CREATE_CALENDAR_BLOCK,
        target_system=TargetSystem.GOOGLE_CALENDAR,
        action_payload={"title": "Test"},
    )

    # Create proposal
    await engine.apply(Event(
        EventType.EXECUTION_PROPOSAL_CREATED, p.proposal_id, AggregateType.SYSTEM,
        payload=p.to_dict(),
    ))

    view = engine.get_view("proposal", "active")
    pending = view.get("pending_proposals", {})
    assert p.proposal_id in pending
    print(f"\u2713 SE: proposal tracked in pending")

    # Accept it
    p2 = Proposal.from_dict(p.to_dict())
    p2.status = ProposalStatus.ACCEPTED
    await engine.apply(Event(
        EventType.EXECUTION_PROPOSAL_ACCEPTED, p.proposal_id, AggregateType.SYSTEM,
        payload=p2.to_dict(),
    ))

    view2 = engine.get_view("proposal", "active")
    pending2 = view2.get("pending_proposals", {})
    accepted = view2.get("accepted_proposals", {})
    assert p.proposal_id not in pending2
    assert p.proposal_id in accepted
    print(f"\u2713 SE: accepted proposal moved to accepted")


# ── Deterministic replay ───────────────────────────────────────────────

async def test_execution_replay():
    p = Proposal(
        proposal_type=ProposalType.CREATE_CALENDAR_BLOCK,
        target_system=TargetSystem.GOOGLE_CALENDAR,
        action_payload={"title": "Test"},
    )
    events = [
        Event(EventType.EXECUTION_PROPOSAL_CREATED, p.proposal_id, AggregateType.SYSTEM,
              payload=p.to_dict()),
        Event(EventType.EXECUTION_PROPOSAL_ACCEPTED, p.proposal_id, AggregateType.SYSTEM,
              payload=p.to_dict()),
    ]

    e1 = StateEngine()
    for ev in events:
        await e1.apply(ev)
    s1 = e1.state_hash()

    e2 = StateEngine()
    await e2.rebuild_from_events(events)
    s2 = e2.state_hash()

    assert s1 == s2
    print("\u2713 execution replay: deterministic")


# ── Duplicate protection ───────────────────────────────────────────────

async def test_duplicate_proposal_protection():
    """Same proposal accepted twice → idempotent."""
    engine = StateEngine()

    p = Proposal(
        proposal_type=ProposalType.CREATE_CALENDAR_BLOCK,
        action_payload={"title": "Test"},
    )

    await engine.apply(Event(
        EventType.EXECUTION_PROPOSAL_CREATED, p.proposal_id, AggregateType.SYSTEM,
        payload=p.to_dict(),
    ))

    await engine.apply(Event(
        EventType.EXECUTION_PROPOSAL_ACCEPTED, p.proposal_id, AggregateType.SYSTEM,
        payload=p.to_dict(),
    ))

    # Accept again → idempotent (same event_id would be caught by dedup)
    # But different event with same proposal_id should still work
    await engine.apply(Event(
        EventType.EXECUTION_PROPOSAL_ACCEPTED, p.proposal_id, AggregateType.SYSTEM,
        payload=p.to_dict(),
    ))

    history = engine.get_view("proposal", "active").get("acceptance_history", [])
    assert len(history) == 2  # Both accepted events recorded
    print("\u2713 duplicate: both accept events recorded in history")


if __name__ == "__main__":
    print("=== Proposal Model ===")
    test_proposal_serialization()
    test_proposal_expiration()

    print("\n=== Generation ===")
    asyncio.run(test_generate_proposals_empty())
    asyncio.run(test_generate_proposals_with_homework())

    print("\n=== Executor ===")
    asyncio.run(test_executor_mock())
    asyncio.run(test_executor_rejects_non_accepted())

    print("\n=== Handlers ===")
    asyncio.run(test_handle_accepted())
    asyncio.run(test_handle_rejected())

    print("\n=== StateEngine ===")
    asyncio.run(test_se_proposal_tracking())

    print("\n=== Safety ===")
    asyncio.run(test_execution_replay())
    asyncio.run(test_duplicate_proposal_protection())

    print("\nSemi-autonomous Execution: all checks passed")
