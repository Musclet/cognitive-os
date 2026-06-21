from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from scripts import google_calendar_login
from src.connector.google_calendar.auth import (
    GoogleCalendarAuth,
    GoogleCalendarAuthError,
)
from src.core.events import AggregateType, Event, EventType
from src.core.proposal import Proposal, ProposalStatus, ProposalType, TargetSystem
from src.domain.execution.handlers import handle_user_accepted_proposal
from src.executor.google_calendar.executor import GoogleCalendarExecutor
from src.infrastructure.config import Settings


def _proposal(
    *,
    status: ProposalStatus = ProposalStatus.ACCEPTED,
    target: TargetSystem = TargetSystem.GOOGLE_CALENDAR,
    proposal_type: ProposalType = ProposalType.CREATE_CALENDAR_BLOCK,
) -> Proposal:
    return Proposal(
        proposal_id="calendar-real-write-test",
        proposal_type=proposal_type,
        target_system=target,
        action_payload={
            "title": "Calendar write test",
            "start": "2026-06-21T20:00:00+08:00",
            "end": "2026-06-21T20:30:00+08:00",
        },
        status=status,
    )


def _real_settings(tmp_path: Path, **overrides) -> Settings:
    values = {
        "_env_file": None,
        "google_calendar_mock": False,
        "google_calendar_write_enabled": True,
        "google_calendar_write_requires_acceptance": True,
        "google_calendar_credentials_path": str(tmp_path / "credentials.json"),
        "google_calendar_token_path": str(tmp_path / "token.json"),
    }
    values.update(overrides)
    return Settings(**values)


@pytest.mark.asyncio
async def test_missing_proposal_does_not_write(tmp_path):
    executor = GoogleCalendarExecutor(False, _real_settings(tmp_path))

    result = await executor.execute(None)

    assert result.event_type == EventType.EXECUTION_FAILED
    assert result.payload["error_code"] == "google_calendar_proposal_required"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [ProposalStatus.PENDING, ProposalStatus.REJECTED])
async def test_unaccepted_proposal_does_not_write(tmp_path, status):
    executor = GoogleCalendarExecutor(False, _real_settings(tmp_path))
    executor._create_real_event = AsyncMock()

    result = await executor.execute(_proposal(status=status))

    assert result.payload["error_code"] == "google_calendar_proposal_not_accepted"
    executor._create_real_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_wrong_target_does_not_write(tmp_path):
    executor = GoogleCalendarExecutor(False, _real_settings(tmp_path))
    executor._create_real_event = AsyncMock()

    result = await executor.execute(
        _proposal(target=TargetSystem.TELEGRAM_REMINDER),
    )

    assert result.payload["error_code"] == "google_calendar_invalid_proposal_target"
    executor._create_real_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_wrong_operation_does_not_write(tmp_path):
    executor = GoogleCalendarExecutor(False, _real_settings(tmp_path))
    executor._create_real_event = AsyncMock()

    result = await executor.execute(
        _proposal(proposal_type=ProposalType.SCHEDULE_REMINDER),
    )

    assert result.payload["error_code"] == "google_calendar_invalid_proposal_operation"
    executor._create_real_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_write_disabled_does_not_write(tmp_path):
    settings = _real_settings(tmp_path, google_calendar_write_enabled=False)
    executor = GoogleCalendarExecutor(False, settings)
    executor._create_real_event = AsyncMock()

    result = await executor.execute(_proposal())

    assert result.payload["error_code"] == "google_calendar_write_disabled"
    executor._create_real_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_accepted_proposal_returns_event_id_and_html_link(tmp_path):
    executor = GoogleCalendarExecutor(False, _real_settings(tmp_path))
    executor._create_real_event = AsyncMock(return_value={
        "event_id": "event-123",
        "html_link": "https://calendar.google.com/calendar/event?eid=test",
    })

    result = await executor.execute(_proposal())

    assert result.event_type == EventType.EXECUTION_COMPLETED
    assert result.payload["event_id"] == "event-123"
    assert result.payload["html_link"].startswith("https://calendar.google.com/")
    executor._create_real_event.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_credentials_returns_stable_error(tmp_path):
    executor = GoogleCalendarExecutor(False, _real_settings(tmp_path))

    result = await executor.execute(_proposal())

    assert result.payload["error_code"] == "google_calendar_credentials_missing"


@pytest.mark.asyncio
async def test_missing_token_returns_stable_error(tmp_path):
    settings = _real_settings(tmp_path)
    Path(settings.google_calendar_credentials_path).write_text("{}", encoding="utf-8")
    executor = GoogleCalendarExecutor(False, settings)

    result = await executor.execute(_proposal())

    assert result.payload["error_code"] == "google_calendar_token_missing"


@pytest.mark.asyncio
async def test_invalid_token_returns_stable_error(tmp_path):
    settings = _real_settings(tmp_path)
    Path(settings.google_calendar_credentials_path).write_text("{}", encoding="utf-8")
    Path(settings.google_calendar_token_path).write_text("not-json", encoding="utf-8")
    executor = GoogleCalendarExecutor(False, settings)

    result = await executor.execute(_proposal())

    assert result.payload["error_code"] == "google_calendar_token_invalid"


@pytest.mark.asyncio
async def test_api_exception_returns_stable_error(tmp_path):
    executor = GoogleCalendarExecutor(False, _real_settings(tmp_path))
    executor._create_real_event = AsyncMock(side_effect=RuntimeError("private detail"))

    result = await executor.execute(_proposal())

    assert result.payload["error_code"] == "google_calendar_api_error"
    assert "private detail" not in repr(result.payload)


@pytest.mark.asyncio
async def test_delete_missing_token_returns_stable_error(tmp_path):
    settings = _real_settings(tmp_path)
    Path(settings.google_calendar_credentials_path).write_text("{}", encoding="utf-8")
    executor = GoogleCalendarExecutor(False, settings)

    result = await executor.delete_event("event-123", "primary")

    assert result["ok"] is False
    assert result["error_code"] == "google_calendar_token_missing"


def test_runtime_auth_does_not_open_browser_when_token_missing(tmp_path):
    credentials_path = tmp_path / "credentials.json"
    credentials_path.write_text("{}", encoding="utf-8")
    auth = GoogleCalendarAuth(
        credentials_path=str(credentials_path),
        token_path=str(tmp_path / "token.json"),
        scopes=["https://www.googleapis.com/auth/calendar"],
    )

    with pytest.raises(GoogleCalendarAuthError) as exc_info:
        auth.authenticate(allow_interactive=False)

    assert exc_info.value.error_code == "google_calendar_token_missing"


def test_oauth_script_missing_credentials_prints_no_secret(tmp_path, capsys):
    code = google_calendar_login.main([
        "--credentials-file",
        str(tmp_path / "missing.json"),
        "--token-file",
        str(tmp_path / "token.json"),
        "--calendar-id",
        "primary",
    ])
    output = capsys.readouterr().out

    assert code == 1
    assert "google_calendar_credentials_missing" in output
    assert "no_secret_printed: True" in output
    assert "client_secret" not in output


def test_oauth_script_success_output_is_sanitized(tmp_path, monkeypatch, capsys):
    credentials_path = tmp_path / "credentials.json"
    token_path = tmp_path / "token.json"
    credentials_path.write_text("{}", encoding="utf-8")

    def fake_authenticate(self, trace_id="", *, allow_interactive=False):
        token_path.write_text("{}", encoding="utf-8")
        return object(), []

    monkeypatch.setattr(GoogleCalendarAuth, "authenticate", fake_authenticate)

    code = google_calendar_login.main([
        "--credentials-file",
        str(credentials_path),
        "--token-file",
        str(token_path),
        "--calendar-id",
        "primary",
    ])
    output = capsys.readouterr().out

    assert code == 0
    assert "token_file_saved: True" in output
    assert "scopes_count: 1" in output
    assert "no_secret_printed: True" in output
    assert "client_secret" not in output
    assert "access_token" not in output
    assert "refresh_token" not in output


def test_interactive_auth_suppresses_authorization_url(tmp_path, monkeypatch):
    credentials_path = tmp_path / "credentials.json"
    credentials_path.write_text("{}", encoding="utf-8")
    token_path = tmp_path / "token.json"
    auth = GoogleCalendarAuth(
        credentials_path=str(credentials_path),
        token_path=str(token_path),
        scopes=["https://www.googleapis.com/auth/calendar"],
    )

    class FakeCredentials:
        valid = True
        expired = False

        def to_json(self):
            return "{}"

    class FakeFlow:
        def run_local_server(self, **kwargs):
            assert kwargs["authorization_prompt_message"] == ""
            return FakeCredentials()

    monkeypatch.setattr(
        "google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file",
        lambda *args, **kwargs: FakeFlow(),
    )

    auth.authenticate(allow_interactive=True)

    assert token_path.exists()


def test_google_calendar_private_files_are_gitignored():
    repo_root = Path(__file__).resolve().parents[2]
    paths = [
        "data/google_credentials.json",
        "data/google_token.json",
        "data/google_calendar_credentials.json",
        "data/google_calendar_token.json",
    ]

    for path in paths:
        result = subprocess.run(
            ["git", "check-ignore", "-q", "--", path],
            cwd=repo_root,
            check=False,
        )
        assert result.returncode == 0, path


@pytest.mark.asyncio
async def test_delete_proposal_is_left_for_operation_specific_route():
    proposal = _proposal(proposal_type=ProposalType.DELETE_CALENDAR_EVENT)
    proposal.action_payload = {
        "event_id": "event-123",
        "calendar_id": "primary",
        "title": "Calendar write test",
    }
    accepted = Event(
        event_type=EventType.USER_ACCEPTED_PROPOSAL,
        aggregate_id=proposal.proposal_id,
        aggregate_type=AggregateType.SYSTEM,
        payload=proposal.to_dict(),
    )

    produced = await handle_user_accepted_proposal(accepted)

    assert [event.event_type for event in produced] == [EventType.EXECUTION_REQUESTED]
