# Project Status

Last audited: 2026-06-13
Audit base: `83605e0`
Working branch: `codex/project-audit-refactor`

## Verified Baseline

| Check | Result |
|---|---|
| Python compile | PASS |
| Python tests | `900 passed` |
| Replay and stabilization | `6 passed` |
| Web TypeScript/Vite build | PASS |
| Critical Ruff (`E9,F821` in `src`) | PASS |
| Full Ruff | 228 pre-existing findings at audit start |
| npm audit | 2 dev-tool findings: Vite/esbuild, major upgrade required |

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

## Priority Risk Queue

### P0 Contained

- Calendar schedule mirroring can insert, patch, and delete events without a
  server-side accepted proposal. The Render write gate is disabled. Do not
  re-enable it until the executor contract is fixed and covered by tests.

### P1 Next

1. Derived-state replay uses wall-clock time and can produce different hashes.
2. `state_hash()` can mutate lazy derived state; reads are not observationally pure.
3. Snapshots omit temporal projections and do not form complete recovery points.
4. Google Calendar fetch start removes last-known-good blocks before success.
5. JWXT refresh adds blocks but does not reconcile removed or changed classes.
6. Render and local runtimes use different dependency composition.
7. EventBus logs and swallows handler failures; DLQ instances are inconsistent.
8. Approval and undo state is partly held in interface-process memory.

### P2 Planned

- Move blocking Google SDK calls off the asyncio event loop.
- Add a managed background-task registry and shutdown cancellation.
- Introduce typed API response models and generated TypeScript contracts.
- Split the large API/Telegram modules by use case.
- Resolve the legacy root-level derived-state engine.
- Reduce the full Ruff backlog without mixing formatting and behavior changes.
- Upgrade Vite/esbuild in a dedicated Web-toolchain compatibility batch.

## Next Safe Batch

The next agent should write failing tests for deterministic `as_of` handling
and observationally pure `state_hash()` before modifying StateEngine or
`src/derived_state/`. This is a protected-core change and requires the full
validation pipeline.
