# ENGINEERING RULES

> **Status:** AUTHORITATIVE  
> **Phase:** Production Runtime  
> **Scope:** All future modifications to this repository  
> **Override:** This document overrides any informal convention or implicit assumption.

This is not a style guide. This is not onboarding documentation.
This is the runtime governance contract for a long-running stateful cognitive system.

---

## 1. SYSTEM PHASE

The project is **post-prototype**. It is a live stateful runtime with:

- Durable event persistence (SQLite, append-only)
- Deterministic replay from event log
- Snapshot-based recovery
- Derived state computation (10 metrics)
- Behavioral reflection with trend detection
- Bounded parameter adaptation
- Execution proposal layer with approval gating
- Telegram Bot + FastAPI inspector dual interface

The system **owns state**. State correctness is the highest priority.

---

## 2. CORE ARCHITECTURE INVARIANTS

### 2.1 The Five-Layer Boundary Contract

```
Interface (stateless) → EventBus → Domain Handlers (pure) → Connectors (read-only)
                                     ↓
                               StateEngine (only state writer)
                                              ↓
                                    Executors (approval-gated write)
```

### 2.2 Absolute Rules

| Rule | Violation Consequence |
|------|----------------------|
| Interface layer MUST NOT hold state | Replay divergence |
| Interface layer MUST NOT contain business logic | Untestable I/O coupling |
| Connectors MUST NOT write state | State corruption |
| Connectors MUST NOT call Telegram | Layer violation |
| Connectors MUST NOT contain business logic | Coupling creep |
| All state changes MUST go through `StateEngine.apply(event)` | Unlogged mutation |
| All external inputs MUST become Events before entering the system | Untraceable side effects |
| Domain handlers MUST be pure: `Event → List[Event]` | Non-determinism |
| Executors MUST NOT execute without `ProposalStatus.ACCEPTED` | Autonomous execution |
| Executors MUST NOT delete or bulk-modify external state | Irreversible damage |

### 2.3 Single Mutation Authority

`StateEngine` is the **only** component permitted to mutate `self._state` and `self._derived`. No other module may:

- Write to `state_engine._state`
- Write to `state_engine._derived`
- Modify `state_engine._applied_event_ids`
- Call `state_engine._ensure_aggregate()` from outside

Violating this breaks replay determinism.

---

## 3. REPLAY & DETERMINISM GUARANTEES

### 3.1 The Prime Invariant

```
SAME events → SAME state_hash() → SAME derived state → SAME planning output
```

This must hold for **every** event sequence, at **every** point in time.

### 3.2 Verification Procedure

After any structural change:

```bash
python tests/unit/test_replay.py
python tests/integration/test_stabilization.py
```

Both must pass with identical hashes.

### 3.3 State Hash

`StateEngine.state_hash()` computes `sha256(json.dumps({state, derived}, sort_keys=True))`. Any change that alters hash output without explicit intent is a **regression**.

### 3.4 Derived State Constraints

Every derived state function MUST:

- Accept only `state: dict` (or `state` + projection)
- Be a pure function: same input → same output
- Have zero side effects
- Use zero mutable globals
- Never call `datetime.now()` (use event timestamps)

Current derived state modules:

| Module | Function |
|--------|----------|
| `workload.py` | `compute_workload(state)` |
| `deadline_pressure.py` | `compute_deadline_pressure(state)` |
| `activity_density.py` | `compute_activity_density(state)` |
| `temporal_projection.py` | `compute_projection(blocks)` |
| `cognition.py` | `compute_cognition(state, projection)` |
| `planning.py` | `compute_planning(blocks, cognition, adaptive?)` |
| `behavior.py` | `compute_behavior(state)` |
| `reflection.py` | `compute_reflection(state)` |
| `adaptation_params.py` | `compute_adapted_params(behavior, reflection)` |
| `adaptive_planning.py` | `compute_adaptive_planning(behavior, cognition, adapted_params?)` |

---

## 4. CONNECTOR / EXECUTOR BOUNDARIES

### 4.1 Connector (READ-ONLY)

```
External World → fetch() → raw data → connector.fetch.completed event
```

Connectors:

- `connector/chaoxing/` — 学习通 homework extraction
- `connector/jwxt/` — 教务系统 schedule (Temporal Source)
- `connector/google_calendar/` — Google Calendar read

Connector constraints:
- **NEVER** write to state
- **NEVER** call Telegram
- **NEVER** send notifications
- **NEVER** call GPT or any AI
- **NEVER** contain business logic
- **ALWAYS** emit `connector.fetch.completed` with raw payload

### 4.2 Executor (APPROVAL-GATED WRITE)

```
Proposal (ACCEPTED) → Executor.execute() → external write → execution.completed
```

Executors:

- `executor/google_calendar/` — create calendar events

Executor constraints:
- **ONLY** execute after `ProposalStatus.ACCEPTED`
- **NEVER** execute autonomously
- **NEVER** delete external data
- **NEVER** bulk-modify
- **ALWAYS** check `proposal.status == ACCEPTED` before acting
- **ALWAYS** emit `execution.completed` or `execution.failed`

---

## 5. FORBIDDEN OPERATIONS

### 5.1 Absolute Prohibitions

These are never permitted under any circumstance:

- Full file rebuild during bugfix
- Architecture rewrite during recovery
- Global regex find-and-replace across the codebase
- Broad auto-formatting during corruption recovery
- Uncontrolled indentation rewrites
- Event schema mutation without explicit approval
- Changing callback registration casually
- Changing replay pipeline order casually
- Adding `datetime.now()` to derived state functions
- Writing to `state_engine._state` from outside StateEngine
- Bypassing EventBus for any data flow
- Autonomous execution (executor without ACCEPTED proposal)
- Self-modifying logic (code that rewrites code)
- Rule graph mutation at runtime
- ML/RL parameter learning (parameters adapt via bounded rules only)

### 5.2 Conditional Prohibitions

These require explicit approval:

- Adding new event types (ok if namespaced, must not change existing semantics)
- Adding new derived state modules (must be pure, deterministic)
- Adding new connectors (must follow read-only contract)
- Adding new executors (must follow approval-gated contract)
- Modifying `_DERIVED_AFFECTING_EVENTS` set
- Changing adaptation parameter bounds
- Changing snapshot interval

---

## 6. PATCH DISCIPLINE

### 6.1 Before Every Patch

1. Identify **exact** affected line range
2. State which functions are modified
3. State which event flows are impacted
4. State whether replay hash could change
5. State which integration points are affected

### 6.2 During Every Patch

- Touch only the minimum lines necessary
- Never refactor unrelated methods
- Never move method scope
- Never rename public methods without tracing all callers
- Never "optimize structure" during a bugfix

### 6.3 After Every Patch

1. `py_compile` the changed file
2. Import the module
3. Run the targeted test file
4. Run `test_replay.py`
5. Run `test_stabilization.py`
6. Verify state hash unchanged (or document why it changed)

---

## 7. RECOVERY MODE RULES

### 7.1 When in Recovery Mode

Recovery mode is triggered when:

- A file fails to compile
- A class loses its methods (indentation corruption)
- The replay hash diverges
- Event store returns inconsistent data

### 7.2 Recovery Constraints

During recovery:

- **NEVER** rebuild an entire file
- **NEVER** regenerate a complete class
- **NEVER** use `black` or `autopep8` on a structurally broken file (it cannot parse broken syntax)
- **ALWAYS** fix the minimum syntax error first, then retry compilation
- **ALWAYS** fix one error at a time, compiling between each fix
- **PREFER** `py_compile` over full import for incremental validation

### 7.3 Recovery Procedure

```
1. Identify the first SyntaxError/IndentationError
2. Fix ONLY that error (typically 1-3 lines)
3. py_compile to verify
4. Repeat until file compiles
5. Import the module
6. Run targeted tests
7. Run full regression
```

---

## 8. MANDATORY VALIDATION PIPELINE

After any change to `src/`:

```bash
# Stage 1 — Syntax
python -c "import py_compile; py_compile.compile('src/path/to/file.py', doraise=True)"

# Stage 2 — Targeted unit tests
python tests/unit/test_<affected_module>.py

# Stage 3 — Replay integrity
python tests/unit/test_replay.py

# Stage 4 — Stabilization integration
python tests/integration/test_stabilization.py

# Stage 5 — Full regression
# Run all test_*.py in tests/unit/ and tests/integration/
```

Stage 3 and 4 are **mandatory** after any change to:

- `src/core/state_engine.py`
- `src/core/events.py`
- `src/storage/event_store.py`
- `src/storage/snapshot_store.py`
- Any `src/derived_state/` module

---

## 9. PROTECTED FILES

### 9.1 HIGH RISK FILES

These files carry **maximum change risk**. Any modification requires:

- Explicit justification
- Full validation pipeline (Stages 1-5)
- Replay hash verification

| File | Risk | Reason |
|------|------|--------|
| `src/core/state_engine.py` | **CRITICAL** | Single mutation authority; replay source of truth |
| `src/core/events.py` | **CRITICAL** | Event type contract; all layers depend on it |
| `src/core/bus.py` | **HIGH** | Event distribution; persistence path |
| `src/core/pipeline.py` | **HIGH** | Event chain execution; causation tracking |
| `src/storage/event_store.py` | **CRITICAL** | Append-only log; durability foundation |
| `src/interface/telegram/bot.py` | **HIGH** | User I/O surface; callback registration |
| `src/derived_state/planning.py` | **HIGH** | Recommendation engine; adaptive integration |
| `src/derived_state/behavior.py` | **HIGH** | Feedback metrics; reflection input |
| `src/derived_state/reflection.py` | **HIGH** | Trend detection; adaptation driver |
| `src/derived_state/adaptation_params.py` | **HIGH** | Parameter bounds; adaptation safety |

### 9.2 PROTECTED DIRECTORIES

- `src/core/` — Architecture foundation
- `src/storage/` — Durability layer
- `src/derived_state/` — Deterministic computation
- `src/executor/` — Approval-gated write
- `scripts/replay.py` — Recovery tool

---

## 10. EVENT CONTRACT RULES

### 10.1 Event Immutability

Once emitted, an Event is immutable. `@dataclass(frozen=True)` enforces this at the language level.

### 10.2 Event Type Namespacing

All event types follow `domain.action` naming. 50 event types currently defined:

- `system.*` — System lifecycle
- `user.*` — User input
- `connector.*` — Data fetching
- `homework.*` — Homework domain
- `schedule.*` — Schedule domain
- `notification.*` — Output
- `temporal.*` — Time blocks
- `cognition.*` — Cognitive state
- `planning.*` — Planning recommendations
- `adaptive.*` — Adaptation signals
- `execution.*` — Proposal lifecycle

### 10.3 Adding New Event Types

1. Add to `EventType` enum in `src/core/events.py`
2. Must follow `domain.action` pattern
3. Must not change existing enum values (append-only)
4. Run full validation pipeline
5. Verify replay hash unchanged (new event types don't affect existing replay unless consumed)

### 10.4 Event Payload Contract

- Payload is `dict[str, Any]`
- All timestamps MUST be UTC ISO 8601 strings
- All IDs MUST be strings
- No binary data in payloads
- No circular references

---

## 11. ADAPTATION SAFETY RULES

### 11.1 What CAN Adapt

5 numeric parameters with hard bounds:

| Parameter | Range | Step |
|-----------|-------|------|
| `deep_work_threshold` | 0.3 – 0.8 | ±0.05 |
| `overload_sensitivity` | 0.5 – 1.5 | ±0.05 |
| `recovery_weight` | 0.3 – 1.7 | ±0.05 |
| `preferred_window_duration` | 20 – 150 | ±10 |
| `fatigue_penalty` | 0.5 – 1.5 | ±0.05 |

### 11.2 What CANNOT Adapt

- Rule graph structure
- Pipeline composition
- Event semantics
- Architecture boundaries
- Adaptation rules themselves
- Parameter bounds
- Step sizes

### 11.3 Adaptation Constraints

- All adaptation is rule-based (no ML, no RL, no GPT)
- Single-step adjustment per computation (no runaway)
- Requires 7+ feedback samples before any adaptation
- All adapted params are recomputed fresh from state each cycle
- Same events → same reflection → same adaptation → same params

---

## 12. EXECUTION SAFETY RULES

### 12.1 Proposal Lifecycle

```
PROPOSAL_CREATED → [user approves] → PROPOSAL_ACCEPTED → Executor.execute() → COMPLETED
                 → [user rejects]  → PROPOSAL_REJECTED
                 → [timeout]       → PROPOSAL_EXPIRED
```

### 12.2 Execution Constraints

- Executor MUST check `proposal.status == ACCEPTED` before acting
- Proposals expire after 2 hours by default
- Only `create_calendar_event` is implemented (no delete, no bulk modify)
- Mock executor (`use_mock=True`) is the default; real OAuth2 requires explicit opt-in
- Telegram inline keyboard provides accept/reject buttons
- Callback handler validates proposal exists before executing

### 12.3 Execution Prohibitions

- No autonomous execution
- No auto-accept
- No self-triggered proposals
- No GPT-driven execution decisions
- No execution without user interaction

---

## 13. TESTING REQUIREMENTS

### 13.1 Current Test Suite

20 test files across unit and integration:

**Unit (17 files):**
- `test_bus.py`, `test_pipeline.py`, `test_state_engine.py`
- `test_event_store.py`, `test_snapshot_store.py`
- `test_replay.py`, `test_derived_state.py`
- `test_telegram.py`, `test_observability.py`
- `test_connector_migration.py`, `test_temporal.py`
- `test_cognition.py`, `test_planning.py`
- `test_behavior.py`, `test_adaptive_planning.py`
- `test_reflection.py`, `test_execution.py`

**Integration (3 files):**
- `test_connector_flow.py`, `test_telegram_flow.py`, `test_stabilization.py`

### 13.2 Test Requirements for Changes

| Change Type | Minimum Tests |
|-------------|---------------|
| New derived state module | Targeted test + `test_replay.py` + `test_stabilization.py` |
| New event type | Targeted test + `test_state_engine.py` |
| New connector | Targeted test + `test_connector_flow.py` |
| New executor | Targeted test + `test_execution.py` |
| StateEngine change | `test_state_engine.py` + `test_replay.py` + `test_stabilization.py` |
| Bot change | `test_telegram.py` + `test_telegram_flow.py` |
| Derived state change | Targeted test + `test_replay.py` |

### 13.3 Replay Hash Test

`test_replay.py` must **always** pass. A replay hash mismatch is a **blocking regression**.

---

## 14. AI MODIFICATION GOVERNANCE

### 14.1 FORBIDDEN AI BEHAVIORS

When an AI agent modifies this codebase, it MUST NOT:

- Rewrite entire files to "simplify" or "clean up"
- Regenerate complete classes because indentation is wrong
- Apply global regex replacements across the file
- Use `black` or `autopep8` on a file that doesn't compile
- Change method signatures without tracing all callers
- Move methods between modules casually
- Add `datetime.now()` to deterministic functions
- Propose architecture changes during bugfix sessions
- Delete "unused" code without confirming it's truly dead
- Combine multiple unrelated changes in one patch
- Assume it understands the full system without reading the relevant files

### 14.2 Required AI Workflow

1. **Survey** — read affected files before proposing changes
2. **Plan** — state exact line ranges and affected functions
3. **Patch** — surgical, minimal, one concern at a time
4. **Validate** — compile + targeted test + replay test
5. **Report** — state what was changed and why

---

## 15. CHANGE RADIUS POLICY

### 15.1 Radius Classification

| Radius | Scope | Example |
|--------|-------|---------|
| **L1** | Single function body | Fixing a calculation in `compute_behavior()` |
| **L2** | Single file, multiple functions | Adding a handler to `state_engine.py` |
| **L3** | Multiple files, same layer | Adding a new derived state module + wiring |
| **L4** | Multiple layers | Adding a new event type + handler + executor + Telegram command |
| **L5** | Architecture boundary | Changing event semantics or layer contracts |

### 15.2 Approval Requirements

- **L1-L2:** Standard validation pipeline
- **L3:** + integration test run
- **L4:** + full regression + explicit documentation of new flow
- **L5:** + explicit human approval required before implementation

---

## 16. INCIDENT RECOVERY PROCEDURE

### 16.1 Symptom: File Fails to Compile

```
1. Read the exact error (line number, type)
2. View the 3 lines around the error
3. Fix ONLY the syntax/indentation on those lines
4. py_compile
5. Repeat until clean
6. NEVER preemptively fix other lines "in case they're also wrong"
```

### 16.2 Symptom: Class Methods Missing

```
1. Verify with: from module import ClassName; dir(ClassName)
2. Check if methods exist at module level instead
3. Check if class definition exists (grep "class ClassName")
4. Fix class indentation only — wrap existing methods, do not rewrite bodies
5. One method at a time, compile between each
```

### 16.3 Symptom: Replay Hash Diverges

```
1. Run test_replay.py to confirm divergence
2. Check git diff for changes to state_engine.py or any derived_state/*.py
3. Check if any derived state function now calls datetime.now()
4. Check if event handler order changed in _get_handler()
5. Check if _DERIVED_AFFECTING_EVENTS set changed
6. Revert the causal change; do NOT patch around the hash mismatch
```

### 16.4 Symptom: Bot Won't Start

```
1. Check: token set in .env? → import Settings; Settings().telegram_bot_token
2. Check: all imports work? → python -c "from src.interface.telegram.bot import CognitiveOSBot"
3. Check: port available? → netstat -ano | findstr :8081
4. Check: bot connects? → Look for "HTTP/1.1 200 OK" after getMe in logs
```

---

## APPENDIX A: Runtime Startup

```bash
cd "C:\Users\admin\Documents\New project 8"
python scripts/run.py
```

This starts:
- **Telegram Bot** — polls for messages
- **FastAPI Inspector** — `http://localhost:8081`
- **APScheduler** — interval jobs for homework/schedule checks

## APPENDIX B: Key Commands

```bash
# Run all unit tests
python tests/unit/test_bus.py
# ... (run each test_*.py)

# Run replay verification
python tests/unit/test_replay.py

# Run integration tests
python tests/integration/test_stabilization.py

# Replay from event log
python scripts/replay.py

# Inspect events
python scripts/inspect_events.py --recent 20

# Dry start (validate all components init)
python -c "import asyncio, sys; sys.path.insert(0,'.'); ..."
```

## APPENDIX C: Environment

```
# .env
TELEGRAM_BOT_TOKEN=<token>
DATABASE_URL=sqlite+aiosqlite:///data/cognitive_os.db
CHAOXING_MOCK=true
JWXT_MOCK=true
GOOGLE_CALENDAR_MOCK=true
```

---

*This document takes effect immediately. All future modifications to this repository are bound by these rules.*
