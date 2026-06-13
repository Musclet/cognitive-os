# Project Status

Last audited: 2026-06-13
Audit base: `83605e0`
Working branch: `codex/project-audit-refactor`

## Verified Baseline

| Check | Result |
|---|---|
| Python compile | PASS |
| Python tests | `916 passed, 134 warnings` |
| Temporal/replay/stabilization regression | `51 passed` |
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

### P0 Contained

- Calendar schedule mirroring can insert, patch, and delete events without a
  server-side accepted proposal. The Render write gate is disabled. Do not
  re-enable it until the executor contract is fixed and covered by tests.

### P1 Next

1. Approval and recent-action undo state is partly held in interface-process
   memory.
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

Persist proposal approval and recent-action undo state in the event-sourced
model. Keep the existing API behavior stable, add restart/replay tests first,
then remove the process-memory stores and the unreachable legacy Web route
implementations in a separate cleanup commit.
