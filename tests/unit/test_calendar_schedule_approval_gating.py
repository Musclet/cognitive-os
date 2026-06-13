"""Test: calendar schedule mirror proposal gate.

sync_schedule_blocks must require an ACCEPTED proposal when
google_calendar_write_requires_acceptance is True (non-mock).
"""

from __future__ import annotations

from unittest.mock import MagicMock, AsyncMock, patch

import pytest

from src.core.proposal import Proposal, ProposalStatus, TargetSystem
from src.infrastructure.config import Settings


# ── Gate: schedule_write_enabled=false blocks regardless of proposal ────

@pytest.mark.asyncio
async def test_schedule_write_disabled_blocks_without_proposal():
    """When schedule_write_enabled=False, sync returns error regardless of proposal."""
    from src.executor.google_calendar.executor import GoogleCalendarExecutor

    settings = Settings(
        google_calendar_mock=False,
        google_calendar_schedule_write_enabled=False,
        google_calendar_write_requires_acceptance=True,
    )
    executor = GoogleCalendarExecutor(use_mock=False, settings=settings)

    result = await executor.sync_schedule_blocks([])
    assert result.get("ok") is False
    assert "schedule_calendar_write_disabled" in result.get("error", "")


@pytest.mark.asyncio
async def test_schedule_write_disabled_blocks_with_accepted_proposal():
    """When schedule_write_enabled=False, even an accepted proposal is blocked."""
    from src.executor.google_calendar.executor import GoogleCalendarExecutor

    settings = Settings(
        google_calendar_mock=False,
        google_calendar_schedule_write_enabled=False,
        google_calendar_write_requires_acceptance=True,
    )
    executor = GoogleCalendarExecutor(use_mock=False, settings=settings)

    proposal = Proposal(
        proposal_id="p-test",
        user_id="u1",
        target_system=TargetSystem.GOOGLE_CALENDAR,
    )
    proposal.status = ProposalStatus.ACCEPTED

    result = await executor.sync_schedule_blocks([], proposal=proposal)
    assert result.get("ok") is False
    assert "schedule_calendar_write_disabled" in result.get("error", "")


# ── Gate: schedule_write_enabled=True but no proposal → blocked ─────────

@pytest.mark.asyncio
async def test_schedule_write_enabled_no_proposal_blocks():
    """schedule_write_enabled=True but proposal=None → blocked."""
    from src.executor.google_calendar.executor import GoogleCalendarExecutor

    settings = Settings(
        google_calendar_mock=False,
        google_calendar_schedule_write_enabled=True,
        google_calendar_write_requires_acceptance=True,
    )
    executor = GoogleCalendarExecutor(use_mock=False, settings=settings)

    result = await executor.sync_schedule_blocks([])
    assert result.get("ok") is False
    assert "proposal_required" in result.get("error", "")


# ── Gate: schedule_write_enabled=True + proposal not ACCEPTED → blocked ─

@pytest.mark.asyncio
async def test_schedule_write_enabled_pending_proposal_blocks():
    """schedule_write_enabled=True but proposal.status=PENDING → blocked."""
    from src.executor.google_calendar.executor import GoogleCalendarExecutor

    settings = Settings(
        google_calendar_mock=False,
        google_calendar_schedule_write_enabled=True,
        google_calendar_write_requires_acceptance=True,
    )
    executor = GoogleCalendarExecutor(use_mock=False, settings=settings)

    proposal = Proposal(
        proposal_id="p-pending",
        user_id="u1",
        target_system=TargetSystem.GOOGLE_CALENDAR,
    )
    # status defaults to PENDING
    assert proposal.status == ProposalStatus.PENDING

    result = await executor.sync_schedule_blocks([], proposal=proposal)
    assert result.get("ok") is False
    assert "proposal_not_accepted" in result.get("error", "")


@pytest.mark.asyncio
async def test_schedule_write_enabled_rejected_proposal_blocks():
    """schedule_write_enabled=True but proposal.status=REJECTED → blocked."""
    from src.executor.google_calendar.executor import GoogleCalendarExecutor

    settings = Settings(
        google_calendar_mock=False,
        google_calendar_schedule_write_enabled=True,
        google_calendar_write_requires_acceptance=True,
    )
    executor = GoogleCalendarExecutor(use_mock=False, settings=settings)

    proposal = Proposal(
        proposal_id="p-rejected",
        user_id="u1",
        target_system=TargetSystem.GOOGLE_CALENDAR,
    )
    proposal.status = ProposalStatus.REJECTED

    result = await executor.sync_schedule_blocks([], proposal=proposal)
    assert result.get("ok") is False
    assert "proposal_not_accepted" in result.get("error", "")


# ── Gate: schedule_write_enabled=True + ACCEPTED proposal → allowed ─────

@pytest.mark.asyncio
async def test_schedule_write_enabled_accepted_proposal_allows_mock():
    """schedule_write_enabled=True + ACCEPTED proposal → allowed (mock mode)."""
    from src.executor.google_calendar.executor import GoogleCalendarExecutor

    settings = Settings(
        google_calendar_mock=True,
        google_calendar_schedule_write_enabled=True,
        google_calendar_write_requires_acceptance=True,
    )
    executor = GoogleCalendarExecutor(use_mock=True, settings=settings)

    proposal = Proposal(
        proposal_id="p-accepted",
        user_id="u1",
        target_system=TargetSystem.GOOGLE_CALENDAR,
    )
    proposal.status = ProposalStatus.ACCEPTED

    result = await executor.sync_schedule_blocks([], proposal=proposal)
    assert result.get("ok") is True
    assert result.get("created") is not None


# ── Gate: proposal scope — target_system must be GOOGLE_CALENDAR ────────

@pytest.mark.asyncio
async def test_schedule_write_enabled_telegram_proposal_blocks():
    """ACCEPTED TELEGRAM_REMINDER proposal → blocked with invalid_proposal_target."""
    from src.executor.google_calendar.executor import GoogleCalendarExecutor

    settings = Settings(
        google_calendar_mock=False,
        google_calendar_schedule_write_enabled=True,
        google_calendar_write_requires_acceptance=True,
    )
    executor = GoogleCalendarExecutor(use_mock=False, settings=settings)

    proposal = Proposal(
        proposal_id="p-telegram",
        user_id="u1",
        target_system=TargetSystem.TELEGRAM_REMINDER,
    )
    proposal.status = ProposalStatus.ACCEPTED

    result = await executor.sync_schedule_blocks([], proposal=proposal)
    assert result.get("ok") is False
    assert "invalid_proposal_target" in result.get("error", "")


# ── Gate: proposal scope — operation must authorize schedule mirror ─────

@pytest.mark.asyncio
async def test_schedule_write_enabled_no_operation_blocks():
    """ACCEPTED GOOGLE_CALENDAR proposal without operation → blocked."""
    from src.executor.google_calendar.executor import GoogleCalendarExecutor

    settings = Settings(
        google_calendar_mock=False,
        google_calendar_schedule_write_enabled=True,
        google_calendar_write_requires_acceptance=True,
    )
    executor = GoogleCalendarExecutor(use_mock=False, settings=settings)

    proposal = Proposal(
        proposal_id="p-no-op",
        user_id="u1",
        target_system=TargetSystem.GOOGLE_CALENDAR,
        action_payload={"title": "Test"},  # no 'operation' key
    )
    proposal.status = ProposalStatus.ACCEPTED

    result = await executor.sync_schedule_blocks([], proposal=proposal)
    assert result.get("ok") is False
    assert "invalid_proposal_operation" in result.get("error", "")


@pytest.mark.asyncio
async def test_schedule_write_enabled_wrong_operation_blocks():
    """ACCEPTED GOOGLE_CALENDAR proposal with wrong operation → blocked."""
    from src.executor.google_calendar.executor import GoogleCalendarExecutor

    settings = Settings(
        google_calendar_mock=False,
        google_calendar_schedule_write_enabled=True,
        google_calendar_write_requires_acceptance=True,
    )
    executor = GoogleCalendarExecutor(use_mock=False, settings=settings)

    proposal = Proposal(
        proposal_id="p-wrong-op",
        user_id="u1",
        target_system=TargetSystem.GOOGLE_CALENDAR,
        action_payload={"operation": "create_calendar_event"},
    )
    proposal.status = ProposalStatus.ACCEPTED

    result = await executor.sync_schedule_blocks([], proposal=proposal)
    assert result.get("ok") is False
    assert "invalid_proposal_operation" in result.get("error", "")


# ── Gate: valid proposal passes all authorization checks ────────────────

@pytest.mark.asyncio
async def test_schedule_write_enabled_valid_proposal_allows():
    """ACCEPTED GOOGLE_CALENDAR proposal + operation=sync_schedule_blocks → allowed (non-mock, patched)."""
    from unittest.mock import MagicMock
    from src.executor.google_calendar.executor import GoogleCalendarExecutor
    from src.core.temporal import TimeBlock, TemporalSource, TimeBlockType
    from datetime import datetime, timedelta, timezone

    settings = Settings(
        google_calendar_mock=False,
        google_calendar_schedule_write_enabled=True,
        google_calendar_write_requires_acceptance=True,
    )
    executor = GoogleCalendarExecutor(use_mock=False, settings=settings)

    # Patch real API calls to avoid Google auth / HTTP
    executor._calendar_service = MagicMock()
    executor._list_managed_schedule_events = MagicMock(return_value=[])
    executor._execute_with_retry = MagicMock(return_value={"id": "fake-event-1"})

    proposal = Proposal(
        proposal_id="p-valid",
        user_id="u1",
        target_system=TargetSystem.GOOGLE_CALENDAR,
        action_payload={"operation": "sync_schedule_blocks"},
    )
    proposal.status = ProposalStatus.ACCEPTED

    now = datetime.now(timezone.utc) + timedelta(hours=1)
    blocks = [
        TimeBlock("jwxt-1", TemporalSource.JWXT, TimeBlockType.CLASS_LECTURE,
                  now, now + timedelta(hours=1), "课程", "A101"),
    ]

    result = await executor.sync_schedule_blocks(blocks, proposal=proposal)
    assert result.get("ok") is True
    assert result.get("created", 0) >= 0


# ── Error string specificity ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_error_strings_are_greppable():
    """Every error path returns a specific, greppable error string."""
    from src.executor.google_calendar.executor import GoogleCalendarExecutor

    # Disabled
    settings_disabled = Settings(
        google_calendar_mock=False,
        google_calendar_schedule_write_enabled=False,
    )
    e1 = GoogleCalendarExecutor(use_mock=False, settings=settings_disabled)
    r1 = await e1.sync_schedule_blocks([])
    assert "schedule_calendar_write_disabled" == r1.get("error")

    # No proposal
    settings_enabled = Settings(
        google_calendar_mock=False,
        google_calendar_schedule_write_enabled=True,
        google_calendar_write_requires_acceptance=True,
    )
    e2 = GoogleCalendarExecutor(use_mock=False, settings=settings_enabled)
    r2 = await e2.sync_schedule_blocks([])
    assert "proposal_required" in r2.get("error", "")

    # Proposal not accepted
    proposal = Proposal(
        proposal_id="p-err",
        user_id="u1",
        target_system=TargetSystem.GOOGLE_CALENDAR,
    )
    r3 = await e2.sync_schedule_blocks([], proposal=proposal)
    assert "proposal_not_accepted" in r3.get("error", "")


# ── Consistency repair path ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_consistency_repair_skips_on_proposal_required():
    """repair_calendar_consistency skips (not fails) when proposal is required."""
    from src.core.calendar_consistency import repair_calendar_consistency
    from src.core.state_engine import StateEngine
    from src.infrastructure.config import Settings

    settings = Settings(
        google_calendar_mock=False,
        google_calendar_schedule_write_enabled=True,
        google_calendar_write_requires_acceptance=True,
    )

    # Seed state engine with a stale sync finding to trigger repair attempt
    se = StateEngine()
    temporal = se._ensure_aggregate("temporal", "projection")
    from datetime import datetime, timedelta, timezone
    old_time = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    temporal["calendar_sync"] = {
        "status": "completed",
        "count": 0,
        "completed_at": old_time,
    }

    # Create mock executor that returns proposal error
    mock_executor = MagicMock()
    mock_executor.sync_schedule_blocks = AsyncMock(return_value={
        "ok": False,
        "error": "proposal_required: schedule mirror writes require an accepted proposal",
    })

    # Findings that trigger repair attempt
    repair_findings = [
        {"severity": "warning", "message": "课表镜像不一致"},
    ]

    result = await repair_calendar_consistency(
        se, settings,
        review_findings=repair_findings,
        executor=mock_executor,
    )

    assert result["schedule_mirror"]["action"] == "skipped"
    assert "proposal_required" in result["schedule_mirror"].get("skipped_reason", "")
    assert result["overall"] != "error"


# ── Existing behavior preserved (no proposal param breakage) ────────────

@pytest.mark.asyncio
async def test_existing_mock_sync_preserved():
    """Existing mock-mode call without proposal works (spec backwards compat)."""
    from src.executor.google_calendar.executor import GoogleCalendarExecutor
    from datetime import datetime, timedelta, timezone

    executor = GoogleCalendarExecutor(use_mock=True)
    now = datetime.now(timezone.utc) + timedelta(hours=1)
    from src.core.temporal import TimeBlock, TemporalSource, TimeBlockType
    blocks = [
        TimeBlock("jwxt-1", TemporalSource.JWXT, TimeBlockType.CLASS_LECTURE,
                  now, now + timedelta(hours=1), "课程", "A101"),
    ]
    # Call without proposal keyword — should work (proposal defaults to None)
    result = await executor.sync_schedule_blocks(blocks, days=7, calendar_id="primary")
    assert result["ok"]
    assert result["created"] == 1
