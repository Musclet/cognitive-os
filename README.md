# Cognitive OS

Personal event-sourced cognitive runtime with FastAPI, Telegram, React/PWA,
SQLite/PostgreSQL, JWXT, Google Calendar, and Obsidian integrations.

## Start Here

Read these files before changing code:

1. `AGENTS.md` - collaboration and ownership rules.
2. `ENGINEERING_RULES.md` - architecture and state-safety invariants.
3. `docs/ARCHITECTURE.md` - current component and event-flow map.
4. `docs/PROJECT_STATUS.md` - verified baseline, known risks, and active work.
5. `docs/HANDOFF.md` - required handoff format for agents.

## Local Runtime

```powershell
python -m pip install -e ".[dev]"
cd web
npm ci
npm run build
cd ..
python scripts/run.py
```

The local runtime starts the API, Telegram bot, scheduler, and supporting
engines. Configuration is loaded from `.env`; never commit that file.

## Verification

```powershell
python -m compileall -q src scripts
python -m pytest -q
python -m ruff check src --select E9,F821
cd web
npm run build
```

The full Ruff backlog is intentionally tracked as technical debt. Critical
syntax and undefined-name checks are blocking; broad formatting cleanup must
be handled separately from behavior changes.

## Deployment

Render uses `scripts/render_run.py` and `render.yaml`. Neon Postgres is the
shared production source of truth, and GitHub Actions calls the protected cloud
sync endpoint every day at 07:00 China time. See
[`docs/render-cloud-mode.md`](docs/render-cloud-mode.md).

Automated Google Calendar schedule writes are disabled in the blueprint until
the batch-write path is moved behind the accepted-proposal execution contract.

For local OAuth and proposal-gated event creation, see
[`docs/google-calendar-write.md`](docs/google-calendar-write.md).
