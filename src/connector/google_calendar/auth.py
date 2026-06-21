"""Google Calendar OAuth auth helper."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.core.events import AggregateType, Event, EventType


class GoogleCalendarAuthError(RuntimeError):
    """Safe Google Calendar authentication failure with a stable error code."""

    def __init__(self, error_code: str) -> None:
        self.error_code = error_code
        super().__init__(error_code)


class GoogleCalendarAuth:
    """OAuth token lifecycle manager for Google Calendar."""

    def __init__(self, credentials_path: str, token_path: str, scopes: list[str]) -> None:
        self._credentials_path = Path(credentials_path)
        self._token_path = Path(token_path)
        self._scopes = scopes

    def authenticate(
        self,
        trace_id: str = "",
        *,
        allow_interactive: bool = False,
    ) -> tuple[Any, list[Event]]:
        events: list[Event] = [
            Event(
                event_type=EventType.GOOGLE_CALENDAR_AUTH_STARTED,
                aggregate_id="google_calendar",
                aggregate_type=AggregateType.SYSTEM,
                payload={"credentials_path": str(self._credentials_path), "token_path": str(self._token_path)},
                metadata={"trace_id": trace_id, "source": "google_calendar", "error_code": ""},
            )
        ]

        if not self._credentials_path.exists():
            events.append(Event(
                event_type=EventType.GOOGLE_CALENDAR_AUTH_FAILED,
                aggregate_id="google_calendar",
                aggregate_type=AggregateType.SYSTEM,
                payload={"error": "credentials missing"},
                metadata={
                    "trace_id": trace_id,
                    "source": "google_calendar",
                    "error_code": "google_calendar_credentials_missing",
                },
            ))
            raise GoogleCalendarAuthError("google_calendar_credentials_missing")

        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
        except Exception:
            events.append(Event(
                event_type=EventType.GOOGLE_CALENDAR_AUTH_FAILED,
                aggregate_id="google_calendar",
                aggregate_type=AggregateType.SYSTEM,
                payload={"error": "google_calendar_auth_failed"},
                metadata={
                    "trace_id": trace_id,
                    "source": "google_calendar",
                    "error_code": "google_calendar_auth_failed",
                },
            ))
            raise GoogleCalendarAuthError("google_calendar_auth_failed") from None

        creds = None
        if self._token_path.exists():
            try:
                creds = Credentials.from_authorized_user_file(str(self._token_path), self._scopes)
            except Exception:
                raise GoogleCalendarAuthError("google_calendar_token_invalid") from None
        elif not allow_interactive:
            raise GoogleCalendarAuthError("google_calendar_token_missing")

        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                self._token_path.write_text(creds.to_json(), encoding="utf-8")
            except Exception:
                raise GoogleCalendarAuthError("google_calendar_token_invalid") from None
            events.append(Event(
                event_type=EventType.GOOGLE_CALENDAR_TOKEN_REFRESHED,
                aggregate_id="google_calendar",
                aggregate_type=AggregateType.SYSTEM,
                payload={"token_path": str(self._token_path)},
                metadata={"trace_id": trace_id, "source": "google_calendar", "error_code": ""},
            ))
        elif creds and creds.expired and not creds.refresh_token:
            events.append(Event(
                event_type=EventType.GOOGLE_CALENDAR_TOKEN_EXPIRED,
                aggregate_id="google_calendar",
                aggregate_type=AggregateType.SYSTEM,
                payload={"token_path": str(self._token_path)},
                metadata={
                    "trace_id": trace_id,
                    "source": "google_calendar",
                    "error_code": "google_calendar_token_invalid",
                },
            ))
            raise GoogleCalendarAuthError("google_calendar_token_invalid")

        if not creds or not creds.valid:
            if not allow_interactive:
                raise GoogleCalendarAuthError(
                    "google_calendar_token_invalid"
                    if self._token_path.exists()
                    else "google_calendar_token_missing"
                )
            try:
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(self._credentials_path),
                    self._scopes,
                )
                creds = flow.run_local_server(
                    port=0,
                    authorization_prompt_message="",
                )
                self._token_path.parent.mkdir(parents=True, exist_ok=True)
                self._token_path.write_text(creds.to_json(), encoding="utf-8")
            except GoogleCalendarAuthError:
                raise
            except Exception:
                raise GoogleCalendarAuthError("google_calendar_auth_failed") from None

        events.append(Event(
            event_type=EventType.GOOGLE_CALENDAR_AUTH_COMPLETED,
            aggregate_id="google_calendar",
            aggregate_type=AggregateType.SYSTEM,
            payload={"authenticated_at": datetime.now(timezone.utc).isoformat()},
            metadata={"trace_id": trace_id, "source": "google_calendar", "error_code": ""},
        ))
        return creds, events
