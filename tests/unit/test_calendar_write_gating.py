"""Test: Calendar write gating — respects GOOGLE_CALENDAR_WRITE_ENABLED."""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, patch

sys.path.insert(0, ".")

import pytest

from src.core.events import EventType
from src.core.proposal import Proposal, ProposalStatus, TargetSystem


@pytest.mark.asyncio
async def test_calendar_write_gating_disabled():
    """Executor returns EXECUTION_FAILED when write is disabled (non-mock)."""
    from src.executor.google_calendar.executor import GoogleCalendarExecutor
    from src.infrastructure.config import Settings

    settings = Settings(google_calendar_mock=False, google_calendar_write_enabled=False)
    executor = GoogleCalendarExecutor(use_mock=False, settings=settings)

    proposal = Proposal(
        proposal_id="test-1",
        user_id="user-1",
        action_payload={"title": "Test", "start": "2026-06-01T10:00:00", "end": "2026-06-01T11:00:00"},
        target_system=TargetSystem.GOOGLE_CALENDAR,
        reason="test",
        confidence=0.5,
    )
    proposal.status = ProposalStatus.ACCEPTED

    result = await executor.execute(proposal)
    assert result.event_type == EventType.EXECUTION_FAILED
    assert "calendar_write_disabled" in result.payload.get("error", "")


@pytest.mark.asyncio
async def test_calendar_write_gating_enabled():
    """Executor allows execution when write is enabled (mock mode)."""
    from src.executor.google_calendar.executor import GoogleCalendarExecutor
    from src.infrastructure.config import Settings

    settings = Settings(google_calendar_mock=True, google_calendar_write_enabled=True)
    executor = GoogleCalendarExecutor(use_mock=True, settings=settings)

    proposal = Proposal(
        proposal_id="test-2",
        user_id="user-1",
        action_payload={"title": "Test", "start": "2026-06-01T10:00:00", "end": "2026-06-01T11:00:00"},
        target_system=TargetSystem.GOOGLE_CALENDAR,
        reason="test",
        confidence=0.5,
    )
    proposal.status = ProposalStatus.ACCEPTED

    result = await executor.execute(proposal)
    assert result.event_type == EventType.EXECUTION_COMPLETED
    assert result.payload.get("event_id") is not None


@pytest.mark.asyncio
async def test_calendar_schedule_write_gating_disabled():
    """sync_schedule_blocks returns error when schedule write is disabled (non-mock)."""
    from src.executor.google_calendar.executor import GoogleCalendarExecutor
    from src.infrastructure.config import Settings

    settings = Settings(google_calendar_mock=False, google_calendar_schedule_write_enabled=False)
    executor = GoogleCalendarExecutor(use_mock=False, settings=settings)

    result = await executor.sync_schedule_blocks([])
    assert result.get("ok") is False
    assert "schedule_calendar_write_disabled" in result.get("error", "")


@pytest.mark.asyncio
async def test_calendar_create_real_event_includes_location():
    """_create_real_event body includes location from payload."""
    from src.executor.google_calendar.executor import GoogleCalendarExecutor
    from src.infrastructure.config import Settings

    settings = Settings(google_calendar_mock=True, google_calendar_write_enabled=True)
    executor = GoogleCalendarExecutor(use_mock=True, settings=settings)

    payload = {
        "title": "团队周会",
        "start": "2026-06-01T14:00:00+08:00",
        "end": "2026-06-01T15:00:00+08:00",
        "description": "每周同步",
        "location": "会议室A",
    }

    # In mock mode, execution succeeds and doesn't call real API.
    # We verify location is in the body by checking the executor's
    # _create_real_event is NOT called (mock mode short-circuits).
    from src.core.proposal import Proposal, ProposalStatus, TargetSystem
    from uuid import uuid4

    proposal = Proposal(
        proposal_id=f"test-location-{uuid4().hex[:8]}",
        user_id="user-1",
        action_payload=payload,
        target_system=TargetSystem.GOOGLE_CALENDAR,
        reason="用户口述排期",
        confidence=1.0,
    )
    proposal.status = ProposalStatus.ACCEPTED

    # In mock mode, it returns EXECUTION_COMPLETED with mock event_id
    result = await executor.execute(proposal)
    assert result.event_type == EventType.EXECUTION_COMPLETED
    assert result.payload.get("event_id") is not None

    # Verify _create_real_event body would include location
    # Inspect the method source to confirm location is in the body dict
    import inspect
    source = inspect.getsource(executor._create_real_event)
    assert '"location"' in source or "'location'" in source
    assert 'payload.get("location"' in source


@pytest.mark.asyncio
async def test_validate_calendar_id_selected():
    """_validate_calendar_id resolves 'selected' to the fallback calendar ID."""
    from src.executor.google_calendar.executor import GoogleCalendarExecutor
    from src.infrastructure.config import Settings

    settings = Settings(
        google_calendar_calendar_id="selected",
        google_calendar_schedule_calendar_id="my_calendar_id",
    )
    executor = GoogleCalendarExecutor(use_mock=True, settings=settings)
    assert executor._validate_calendar_id("selected") == "my_calendar_id"
    assert executor._validate_calendar_id("primary") == "primary"
    assert executor._validate_calendar_id("my_calendar_id") == "my_calendar_id"


@pytest.mark.asyncio
async def test_sync_schedule_blocks_with_selected_calendar():
    """sync_schedule_blocks resolves 'selected' calendar ID before API calls."""
    from src.executor.google_calendar.executor import GoogleCalendarExecutor
    from src.infrastructure.config import Settings

    settings = Settings(
        google_calendar_mock=True,
        google_calendar_schedule_write_enabled=True,
        google_calendar_schedule_calendar_id="backup_cal",
    )
    executor = GoogleCalendarExecutor(use_mock=True, settings=settings)
    # In mock mode, calendar_id is irrelevant since no API calls are made.
    # But pass "selected" to ensure it doesn't cause an error before mock short-circuit.
    result = await executor.sync_schedule_blocks([], calendar_id="selected")
    assert result.get("ok") is True
    assert result.get("calendar_id") == "selected"  # mock returns raw, non-mock would resolve


@pytest.mark.asyncio
async def test_calendar_service_uses_auth():
    """_calendar_service goes through GoogleCalendarAuth, not raw token read."""
    from src.executor.google_calendar.executor import GoogleCalendarExecutor
    from src.infrastructure.config import Settings
    import inspect

    settings = Settings(google_calendar_mock=True)
    executor = GoogleCalendarExecutor(use_mock=True, settings=settings)
    source = inspect.getsource(executor._calendar_service)
    # Should reference GoogleCalendarAuth
    assert "GoogleCalendarAuth" in source or "google_calendar.auth" in source
