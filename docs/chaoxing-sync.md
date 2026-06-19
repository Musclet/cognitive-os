# Chaoxing homework sync

Chaoxing homework sync uses a local Playwright storage-state file. The default
configuration is:

```env
CHAOXING_MOCK=false
CHAOXING_STATE_FILE=data/chaoxing_state.json
CHAOXING_SYNC_TIMEOUT_SECONDS=300
```

`CHAOXING_MOCK=true` is test/demo mode. The Web UI marks this mode explicitly;
mock homework is never reported as a successful real-data sync.

## Local login state

Real sync requires `data/chaoxing_state.json` (or the path configured by
`CHAOXING_STATE_FILE`). Generate or refresh the state with the included
standalone script:

```bash
python scripts/refresh_chaoxing_state.py
```

This opens a visible browser window. Log in to Chaoxing / 超星学习通
manually, then press Enter in the terminal to save the login state. The
script accepts optional arguments:

```bash
python scripts/refresh_chaoxing_state.py --timeout 180          # shorter timeout
python scripts/refresh_chaoxing_state.py --state-file data/cx.json  # custom path
```

If Playwright or Chromium is missing:

```bash
pip install playwright
python -m playwright install chromium
```

The state file may contain session material:

- never commit it;
- keep it under `data/`, which is ignored by Git;
- do not paste its contents into logs, issues, or pull requests.

If the state file is absent, sync returns `chaoxing_state_file_missing`.
If the session has expired, it returns `chaoxing_session_expired`; run the
refresh script to regenerate the state and retry from the Web system page.

Other error codes:

| Code | Meaning |
|------|---------|
| `chaoxing_playwright_missing` | Playwright package not installed |
| `chaoxing_browser_unavailable` | Chromium browser cannot launch |
| `chaoxing_auth_failed` | Generic authentication failure |
| `chaoxing_sync_failed` | Homework fetch failed for other reasons |

The application reports only booleans, counts, timestamps, and structured error
codes. It does not expose cookie or token values through the Web API.

## Current-semester filtering and partial results

Homework sync first reads current course names from JWXT temporal course blocks,
then falls back to legacy schedule state and recent active JWXT course state.
Chaoxing course names are matched with normalized width, whitespace and common
bracket suffixes. If current course candidates exist but none match, sync returns
`chaoxing_no_matching_current_courses` instead of scanning historical courses.

If no current course candidates exist, sync falls back to all Chaoxing courses
and reports `scanning_all_courses=true`. The Web system page reports total,
filtered, skipped and scanned course counts.

Each completed course writes its assignments into StateEngine immediately.
`CHAOXING_SYNC_TIMEOUT_SECONDS` controls the batch timeout. If the timeout is
reached after at least one course completes, the sync reports
`partial=true` and `timeout=true`, while already fetched assignments remain
available in Dashboard and Tasks.
