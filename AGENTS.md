# Cognitive OS Collaboration Rules

## User Collaboration Shape

The user is vision-driven and expects implementation momentum. Preserve abstraction continuity, infer omitted context from the active project, and prefer landing concrete results over teaching background concepts.

Default behavior:

- Convert broad system intent into executable changes.
- Keep explanations compact and high-density.
- Avoid beginner-style tutorials unless explicitly requested.
- Maintain the existing runtime architecture unless the user explicitly asks to redesign it.

## Codex / Claude Code Split

Use Codex as the controller and Claude Code as the executor for broad development tasks.

Codex responsibilities:

- Compress the user request into a precise implementation prompt.
- Launch Claude Code for large or uncertain work.
- Read Claude's short JSON summary only.
- Request narrow follow-up details only when needed.
- Review results, run minimal verification, restart runtime if needed, and report final status.

Claude Code responsibilities:

- Inspect the repo incrementally.
- Modify code.
- Add or update tests.
- Run relevant tests.
- Return a short JSON summary, not full files, full diffs, or long logs.

## When Codex Should Fix Directly

Codex may directly fix small, deterministic runtime bugs without invoking Claude when all are true:

- The failure is already localized by logs or tests.
- The change is one-file or very small.
- The architectural intent is clear.
- Calling Claude would add delay without reducing risk.

Example:

- Google Calendar read uses `selected` as an internal aggregate meaning, but write API calls require a real calendar ID. If logs show `calendars/selected/events` 404, Codex should directly patch executor write target fallback to `primary`, run focused tests, and restart runtime.

For broad features, multi-module refactors, or unclear failures, hand the task to Claude Code.

## Claude Invocation Policy

Preferred mode:

- Use Claude Code for large tasks.
- Give it a bounded task prompt.
- Require short JSON output.
- Set a 10 minute timeout for waiting.
- During wait, only check result file/process status; do not repeatedly scan the repo.

If Windows Terminal invocation is unreliable, direct `claude -p` is acceptable after verifying the CLI responds.

Claude's response must be summarized as:

- PASS / NEEDS CHANGES
- change summary
- tests run
- risks
- minimal rework instruction

## Runtime Safety

Do not bypass EventBus/StateEngine/DerivedStateEngine/InterventionEngine for state changes.

External writes, such as Google Calendar writes, must use the appropriate executor and config gates.

Do not print secrets from `.env` or logs.

## Storage Placement

Keep large or fast-growing artifacts off the C drive.

- Use `D:\CognitiveOSRuntime\New project 8\` for large runtime data, logs, caches, temporary artifacts, and Git object storage.
- Current repo junctions:
  - `data` -> `D:\CognitiveOSRuntime\New project 8\data`
  - `.git\objects` -> `D:\CognitiveOSRuntime\New project 8\git-objects`
- Do not create new large files under the repo root on C unless they are intentionally small source/test files.
