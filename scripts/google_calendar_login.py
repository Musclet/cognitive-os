"""Authorize Google Calendar locally without printing credential or token data."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.connector.google_calendar.auth import (
    GoogleCalendarAuth,
    GoogleCalendarAuthError,
)
from src.infrastructure.config import Settings


def _print_status(
    *,
    credentials_exists: bool,
    token_saved: bool,
    scopes_count: int,
    calendar_id: str,
    error_code: str = "",
) -> None:
    print(f"credentials_file_exists: {credentials_exists}")
    print(f"token_file_saved: {token_saved}")
    print(f"scopes_count: {scopes_count}")
    print(f"calendar_id: {calendar_id}")
    print("no_secret_printed: True")
    if error_code:
        print(f"error_code: {error_code}")


def main(argv: list[str] | None = None) -> int:
    settings = Settings()
    parser = argparse.ArgumentParser(
        description="Authorize Google Calendar through a local browser.",
    )
    parser.add_argument(
        "--credentials-file",
        default=settings.google_calendar_credentials_path,
    )
    parser.add_argument(
        "--token-file",
        default=settings.google_calendar_token_path,
    )
    parser.add_argument(
        "--calendar-id",
        default=settings.google_calendar_calendar_id,
    )
    args = parser.parse_args(argv)

    credentials_path = Path(args.credentials_file)
    token_path = Path(args.token_file)
    scopes = ["https://www.googleapis.com/auth/calendar"]

    if not credentials_path.exists():
        _print_status(
            credentials_exists=False,
            token_saved=False,
            scopes_count=len(scopes),
            calendar_id=args.calendar_id,
            error_code="google_calendar_credentials_missing",
        )
        return 1

    auth = GoogleCalendarAuth(
        credentials_path=str(credentials_path),
        token_path=str(token_path),
        scopes=scopes,
    )
    try:
        auth.authenticate(
            trace_id="manual-google-calendar-login",
            allow_interactive=True,
        )
    except GoogleCalendarAuthError as exc:
        _print_status(
            credentials_exists=True,
            token_saved=token_path.exists(),
            scopes_count=len(scopes),
            calendar_id=args.calendar_id,
            error_code=exc.error_code,
        )
        return 2
    except Exception:
        _print_status(
            credentials_exists=True,
            token_saved=token_path.exists(),
            scopes_count=len(scopes),
            calendar_id=args.calendar_id,
            error_code="google_calendar_auth_failed",
        )
        return 2

    _print_status(
        credentials_exists=True,
        token_saved=token_path.exists(),
        scopes_count=len(scopes),
        calendar_id=args.calendar_id,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
