# Project Status

Last audited: 2026-06-22
Audit base: `e0c51ed`
Working branch: `feature/cloud-sync-neon`

## Verified Baseline

| Check | Result |
|---|---|
| Python compile | PASS |
| Python tests | `1145 passed, 161 warnings` |
| Focused cloud sync/migration/Render tests | `33 passed, 3 warnings` |
| Snapshot/replay determinism | `10 passed` |
| Web TypeScript/Vite build | PASS |
| Critical Ruff (`E9,F821` in `src scripts`) | FAIL: 5 pre-existing `course_registry` F821 findings in `scripts/run_step4_connector.py` |
| Full Ruff | 228 pre-existing findings at audit start |
| npm install audit | 2 findings: 1 moderate, 1 high |

The Web production bundle is about 993 KB minified and should later be split
by route and explicit icon imports.

The npm findings affect the Vite/esbuild development toolchain. The available
automatic fix upgrades Vite across a major-version boundary, so it is tracked
as a dedicated migration rather than being forced into this behavior/safety
batch.

## Completed In This Branch

- Added a token-protected internal cloud-sync endpoint with constant-time
  authentication, single-run locking, fixed JWXT -> Chaoxing -> Google
  Calendar ordering, isolated source failures, and sanitized result summaries.
- Routed Web "sync all" through the same Pipeline-backed cloud-sync service so
  mobile and scheduled refreshes share one execution path and refresh current
  dashboard state immediately.
- Replaced the ineffective free worker/persistent-disk blueprint with a
  GitHub Actions schedule at `0 23 * * *` UTC and a cold-start-aware retry
  client. This avoids requiring payment details for a Render Cron instance.
- Moved JWXT and Chaoxing production reads to a Windows local sync agent that
  writes normal Pipeline events directly to Neon and asks Render to absorb
  newly persisted events without restarting. Secrets are stored with Windows
  DPAPI and the task runs daily at 07:00.
- Made Neon `DATABASE_URL` the documented production source of truth and
  disabled Render admin file imports.
- Added an idempotent SQLite-to-Postgres event migration tool with dry-run,
  sequence/event-ID preservation, conflict checks, and a pre-apply SQLite
  backup that does not print event payloads or database credentials.
- Made the Windows shortcut open the shared Render app by default while
  preserving an explicit local-development launch mode.
- Added System-page cloud-sync configuration and per-source status/error
  feedback without exposing authentication values.
- Enabled local Google Calendar real-write setup while retaining explicit
  proposal acceptance, stable auth/write error codes, and non-interactive
  runtime authentication.
- Added sanitized OAuth tooling output, real event ID/link propagation,
  proposal-gated deletion cleanup, and setup/acceptance documentation.
- Verified a real event was absent before Accept, created after Accept, and
  then deleted through a second accepted proposal.
- Fixed new-schedule button, date input, review stepper, and workout ring
  usability regressions in the refreshed Web UI.
- Stopped Google Calendar mock mode from reporting a non-persistent new
  schedule as a successful write.
- Restored the existing interactive JWXT cookie refresh script and surfaced
  exact sync error codes with the refresh command in the System page.
- Fixed Windows shortcut interpreter selection and verified the desktop
  shortcut starts both backend and Web UI.
- Made the launcher prefer the real Python installation used by the shortcut.
- Made the stop script terminate only the recorded launcher process trees.
- Made snapshot lifecycle events inherit the triggering event timestamp and
  causation ID, restoring snapshot/replay hash determinism.
- Persisted recent finance undo metadata through StateEngine replay so undo
  remains available after process restarts.
- Isolated JWXT transport selection behind a mockable method.
- Made Unicode test fixtures explicitly UTF-8 on Windows.
- Removed a wall-clock-expired Telegram test date.
- Fixed FastAPI app settings wiring.
- Fixed a missing `Path` import in the Telegram runtime.
- Normalized Workout mutation responses to the full session envelope.
- Stopped the Service Worker from caching authenticated API responses.
- Disabled automatic Google Calendar schedule writes in the Render blueprint.
- Added CI, repository entry documentation, and agent handoff rules.
- Made time-sensitive derived state use the latest event timestamp.
- Added replay tests for event-time deadlines and read-order independence.
- Added versioned snapshots with temporal state, applied IDs, event time, and
  real EventStore sequence metadata.
- Prevented snapshot lifecycle events from recursively creating snapshots.
- Preserved last-known-good Google Calendar blocks across failed reads.
- Made successful Google Calendar reads atomically replace the source snapshot,
  including trace isolation and pending-sync snapshot recovery.
- Added a traced JWXT fetch lifecycle and atomically reconciled successful
  timetable snapshots while preserving the last-known-good state on failure.
- Removed wall-clock fallbacks from derived-state replay paths.
- Added a shared runtime composition root for local, Render, and worker modes.
- Made EventBus handler failures durable through the shared DLQ and observable
  through `system.event_failed` events while allowing healthy handlers to run.
- Added immutable event metadata updates for cascade tracing.
- Moved active dashboard, finance command, finance undo, and calendar conflict
  behavior into domain services.
- Added typed response models for dashboard, finance, calendar proposal,
  workout, and mobile dashboard routes.

## Priority Risk Queue

### P0

- No active P0 issue is known. Calendar schedule writes are gated behind
  accepted proposals and remain covered by regression tests.

### P1 Next

1. Complete the external Neon/Render rollout and cellular-network acceptance
   using deployment secrets that are intentionally absent from the repository.
2. The Web route module still contains unreachable legacy dashboard, finance,
   and conflict implementations after the active paths moved to domain
   services.
3. Some API routes still return untyped dictionaries.

### P2 Planned

- Move blocking Google SDK calls off the asyncio event loop.
- Add a managed background-task registry and shutdown cancellation.
- Generate TypeScript contracts from the typed API response models.
- Continue splitting the large API/Telegram modules by use case.
- Resolve the legacy root-level derived-state engine.
- Reduce the full Ruff backlog without mixing formatting and behavior changes.
- Upgrade Vite/esbuild in a dedicated Web-toolchain compatibility batch.

## Next Safe Batch

Provision Neon, set the documented Render environment variables, run the
dry-run and apply migration, then validate Cron wake-up and phone refresh over
a cellular connection. Do not weaken the Google Calendar proposal/Accept gate.
