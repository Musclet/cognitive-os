# Chaoxing homework sync

Chaoxing homework sync uses a local Playwright storage-state file. The default
configuration is:

```env
CHAOXING_MOCK=false
CHAOXING_STATE_FILE=data/chaoxing_state.json
```

`CHAOXING_MOCK=true` is test/demo mode. The Web UI marks this mode explicitly;
mock homework is never reported as a successful real-data sync.

## Local login state

Real sync requires `data/chaoxing_state.json` (or the path configured by
`CHAOXING_STATE_FILE`). The repository contains the interactive helper
`login_and_save_state()` in `src/connector/chaoxing/browser.py`, but currently
does not provide a standalone login script. Generate or refresh the state from
a trusted local process before enabling real sync.

The state file may contain session material:

- never commit it;
- keep it under `data/`, which is ignored by Git;
- do not paste its contents into logs, issues, or pull requests.

If the file is absent, the sync status is `chaoxing_state_file_missing`. If the
session has expired, the status is `chaoxing_session_expired`; refresh the local
login state and retry from the Web system page.

The application reports only booleans, counts, timestamps, and structured error
codes. It does not expose cookie or token values through the Web API.
