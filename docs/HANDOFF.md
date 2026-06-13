# Agent Handoff

Every agent that changes the repository must leave a handoff entry in the pull
request or commit message body using this structure.

## Template

```text
Objective:
Base SHA:
Branch:
Initial dirty files intentionally left untouched:
Owned files:
Change radius: L1/L2/L3/L4/L5

Changes:
- ...

Contracts or schemas changed:
- none / details

Validation:
- command -> result

Known risks:
- ...

Next action:
- ...

Acceptance check for next agent:
- ...
```

## Coordination Rules

- Do not stage unrelated untracked files.
- Declare file ownership before parallel edits.
- Parallel workers must have disjoint write sets.
- Never rewrite protected core files wholesale.
- Update `docs/PROJECT_STATUS.md` when baseline results or priority risks change.
- Use one concern per commit where practical.
- Record exact failing and passing commands, not only "tests pass".
- External writes and deployment changes require explicit mention in handoff.

## Current Workspace Note

This checkout contains unrelated untracked scripts, backups, PDFs, screenshots,
an iOS directory, and local Web work. They are not part of the audit/refactor
branch unless explicitly staged by their owner.
