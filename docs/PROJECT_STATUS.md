# Project Status

Last audited: 2026-06-18
Audit base: `5c072af`
Working branch: `codex/takeover-baseline`

## Verified Baseline

| Check | Result |
|---|---|
| Python compile | PASS |
| Python tests | `976 passed, 142 warnings` |
| Snapshot/replay determinism | PASS |
| Web TypeScript/Vite build | PASS |
| Critical Ruff (`E9,F821` in `src`) | PASS |
| Full Ruff | 228 pre-existing findings at audit start |
| npm install audit | 2 findings: 1 moderate, 1 high |

The Web production bundle is about 993 KB minified and should later be split
by route and explicit icon imports.

The npm findings affect the Vite/esbuild development toolchain. The available
automatic fix upgrades Vite across a major-version boundary, so it is tracked
as a dedicated migration rather than being forced into this behavior/safety
batch.

## Completed In This Branch

- Fixed Windows shortcut interpreter selection and verified the desktop
  shortcut starts both backend and Web UI.
- Made the launcher prefer the real Python installation used by the shortcut.
- Made the stop script terminate only the recorded launcher process trees.
- Made snapshot lifecycle events inherit the triggering event timestamp and
  causation ID, restoring snapshot/replay hash determinism.
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

1. Recent-action undo state is partly held in interface-process memory.
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

Rebase PR #5 onto the current master and finish persisting recent-action undo
state in the event-sourced model. Keep existing API behavior stable and verify
restart/replay behavior before merging.
