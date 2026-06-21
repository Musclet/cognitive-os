# Google Calendar Real Write Setup

Google Calendar writes must remain proposal-gated. Creating a proposal never
writes an event. A write is allowed only after the proposal status becomes
`accepted` and the target is Google Calendar.

## 1. Create the OAuth client

1. Open Google Cloud Console.
2. Create or select a project.
3. Enable the Google Calendar API.
4. Configure the OAuth consent screen.
5. Create an OAuth client with application type **Desktop app**.
6. Download the client JSON.

Save the downloaded file locally as:

```text
data/google_credentials.json
```

The whole `data/` directory is gitignored. Never commit this file.

## 2. Authorize locally

Run:

```powershell
python scripts/google_calendar_login.py
```

The command opens a local browser window. Sign in to Google and approve the
Calendar scope. On success it saves:

```text
data/google_token.json
```

The script prints only file-existence booleans, scope count, calendar ID, and
an explicit `no_secret_printed: True` marker. It never prints credentials,
access tokens, refresh tokens, or client secrets.

## 3. Configure `.env`

Use the actual `Settings` variable names:

```dotenv
GOOGLE_CALENDAR_MOCK=false
GOOGLE_CALENDAR_WRITE_ENABLED=true
GOOGLE_CALENDAR_WRITE_REQUIRES_ACCEPTANCE=true
GOOGLE_CALENDAR_SCHEDULE_WRITE_ENABLED=false
GOOGLE_CALENDAR_CREDENTIALS_PATH=data/google_credentials.json
GOOGLE_CALENDAR_TOKEN_PATH=data/google_token.json
GOOGLE_CALENDAR_CALENDAR_ID=primary
GOOGLE_CALENDAR_TIMEZONE=Asia/Singapore
```

Keep schedule mirroring disabled unless a separate accepted schedule-mirror
proposal is implemented and reviewed.

## 4. Validate the approval gate

1. Start Cognitive OS.
2. Open the Time page.
3. Create a test schedule named `Cognitive OS Calendar Write Test`.
4. Confirm that the first action creates only a proposal.
5. Select **Accept**.
6. Confirm that the response contains an event ID and, when supplied by
   Google, an `html_link`.
7. Confirm the event appears in Google Calendar.

The executor rejects writes with stable error codes:

- `calendar_mock_enabled`
- `google_calendar_write_disabled`
- `google_calendar_proposal_required`
- `google_calendar_proposal_not_accepted`
- `google_calendar_invalid_proposal_target`
- `google_calendar_invalid_proposal_operation`
- `google_calendar_credentials_missing`
- `google_calendar_token_missing`
- `google_calendar_token_invalid`
- `google_calendar_api_error`

## 5. Safety

- Never commit `.env`.
- Never commit files under `data/`.
- Never paste or log credential JSON, access tokens, refresh tokens, or client
  secrets.
- Do not call the executor with a pending or rejected proposal.
- Delete the test event after validation if it is no longer needed.
