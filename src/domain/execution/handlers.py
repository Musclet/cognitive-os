"""Execution domain handler — proposal generation + approval pipeline.

Generates executable proposals from planning recommendations.
Handles user approval/rejection.
Triggers executor on acceptance.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

from src.core.events import Event, EventType, AggregateType
from src.core.proposal import Proposal, ProposalType, ProposalStatus, TargetSystem
from src.executor.google_calendar.executor import get_executor
from src.domain.homework.status import is_open_homework_status
from src.infrastructure.config import Settings


async def generate_proposals(state_engine) -> list[Event]:
    """Generate execution proposals from current planning state.

    Reads planning derived state → creates Proposal events.
    Called when user requests /propose or after planning update.
    """
    derived = state_engine.get_all_derived()
    planning = derived.get("planning", {})
    cognition = derived.get("cognition", {})
    adaptive = derived.get("adaptive_planning", {})

    windows = planning.get("recommended_windows", [])
    pending = planning.get("pending_tasks", 0)

    if not windows or pending == 0:
        return []

    # Get pending homework from state
    homework = state_engine.get_all("homework")
    pending_hw = [
        (hw_id, hw) for hw_id, hw in homework.items()
        if hw.get("title") and is_open_homework_status(hw.get("status"), hw.get("raw_status"))
    ]

    events: list[Event] = []
    now = datetime.now(timezone.utc)
    confidence = adaptive.get("adaptation_confidence", 0.5)
    intensity = adaptive.get("recommended_intensity", "normal")

    # Match windows to pending homework (1:1, best-effort)
    for i, window in enumerate(windows[:3]):  # max 3 proposals
        if i >= len(pending_hw):
            break

        hw_id, hw = pending_hw[i]
        title = hw.get("title", f"Task {i+1}")
        course = hw.get("course", "")

        # Parse window time
        time_range = window.get("time", "")
        parts = time_range.split("-")
        start_time = parts[0] if parts else ""
        end_time = parts[1] if len(parts) > 1 else ""

        # Create calendar block proposal
        today_str = now.strftime("%Y-%m-%d")
        proposal = Proposal(
            proposal_type=ProposalType.CREATE_CALENDAR_BLOCK,
            target_system=TargetSystem.GOOGLE_CALENDAR,
            action_payload={
                "title": f"{title}" + (f" ({course})" if course else ""),
                "start": f"{today_str}T{start_time}:00Z",
                "end": f"{today_str}T{end_time}:00Z",
                "description": window.get("reason", ""),
                "window_type": window.get("type", "standard"),
                "homework_id": hw_id,
            },
            reason=window.get("reason", "Planning recommendation"),
            confidence=confidence,
            expires_at=now + timedelta(hours=2),
            user_id="default",
        )

        events.append(Event(
            event_type=EventType.EXECUTION_PROPOSAL_CREATED,
            aggregate_id=proposal.proposal_id,
            aggregate_type=AggregateType.SYSTEM,
            payload=proposal.to_dict(),
        ))

    return events


async def handle_proposal_accepted(event: Event) -> list[Event]:
    """Handle accepted proposal → trigger executor."""
    return [Event(
        event_type=EventType.USER_ACCEPTED_PROPOSAL,
        aggregate_id=event.aggregate_id,
        aggregate_type=AggregateType.SYSTEM,
        causation_id=event.event_id,
        payload=event.payload,
    )]


async def handle_proposal_rejected(event: Event) -> list[Event]:
    """Handle rejected proposal — log, no further action."""
    return [Event(
        event_type=EventType.USER_REJECTED_PROPOSAL,
        aggregate_id=event.aggregate_id,
        aggregate_type=AggregateType.SYSTEM,
        causation_id=event.event_id,
        payload=event.payload,
    )]


async def handle_user_accepted_proposal(event: Event) -> list[Event]:
    """Accepted proposal => execution requested => executor run."""
    proposal = Proposal.from_dict(event.payload)
    proposal.status = ProposalStatus.ACCEPTED

    exec_requested = Event(
        event_type=EventType.EXECUTION_REQUESTED,
        aggregate_id=proposal.proposal_id,
        aggregate_type=AggregateType.SYSTEM,
        causation_id=event.event_id,
        payload=proposal.to_dict(),
    )

    settings = Settings()
    executor = get_executor(use_mock=settings.google_calendar_mock, settings=settings)
    result = await executor.execute(proposal)
    out = [exec_requested]
    if result.event_type == EventType.EXECUTION_COMPLETED:
        out.append(Event(
            event_type=EventType.CALENDAR_EVENT_CREATED,
            aggregate_id=proposal.proposal_id,
            aggregate_type=AggregateType.SYSTEM,
            causation_id=exec_requested.event_id,
            payload={
                "proposal_id": proposal.proposal_id,
                "calendar_id": settings.google_calendar_calendar_id,
                "event_id": result.payload.get("event_id", ""),
            },
        ))
    out.append(result.with_causation(exec_requested.event_id))
    return out


async def check_expired_proposals(state_engine) -> list[Event]:
    """Check for expired proposals and emit expiration events.

    Called periodically by scheduler.
    """
    proposals_data = state_engine.get_view("proposal", "active")
    pending = proposals_data.get("pending_proposals", {})

    events: list[Event] = []
    now = datetime.now(timezone.utc)

    for pid, pdata in list(pending.items()):
        expires_str = pdata.get("expires_at", "")
        try:
            expires = datetime.fromisoformat(expires_str)
        except (ValueError, TypeError):
            continue

        if now > expires:
            events.append(Event(
                event_type=EventType.EXECUTION_PROPOSAL_EXPIRED,
                aggregate_id=pid,
                aggregate_type=AggregateType.SYSTEM,
                payload={"proposal_id": pid, "expired_at": now.isoformat()},
            ))

    return events
