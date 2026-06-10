"""Manual Google Calendar OAuth login.

Run this once after placing data/google_credentials.json.
It opens a browser, completes OAuth, and writes data/google_token.json.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.connector.google_calendar.auth import GoogleCalendarAuth
from src.infrastructure.config import Settings


def main() -> int:
    settings = Settings()
    credentials_path = Path(settings.google_calendar_credentials_path)
    token_path = Path(settings.google_calendar_token_path)

    if not credentials_path.exists():
        print(f"Missing credentials: {credentials_path}")
        print("Download the OAuth Desktop JSON from Google Cloud and place it there.")
        return 1

    auth = GoogleCalendarAuth(
        credentials_path=str(credentials_path),
        token_path=str(token_path),
        scopes=["https://www.googleapis.com/auth/calendar"],
    )
    auth.authenticate(trace_id="manual-google-calendar-login")
    print(f"Google Calendar token saved: {token_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
