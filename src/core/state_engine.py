"""StateEngine — single point of state mutation.

All state changes go through apply().
Supports snapshot and full rebuild from event log.
Durable: can rebuild from EventStore + SnapshotStore.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any
from uuid import UUID

from src.core.events import Event, EventType, AggregateType

logger = logging.getLogger(__name__)


class StateEngine:
    """Central state holder. Only component that mutates state."""

    def __init__(
        self,
        snapshot_path: str | None = None,
        snapshot_store=None,
        snapshot_interval: int = 50,
    ) -> None:
        self._state: dict[str, dict[str, dict[str, Any]]] = {}
        self._derived: dict[str, dict[str, Any]] = {}
        self._derived_dirty: bool = False
        self._temporal_blocks: dict[str, Any] = {}
        self._temporal_blocks_by_day: dict[str, list[str]] = {}
        self._active_temporal_context: dict[str, Any] = {}
        self._busy_windows: list[dict[str, Any]] = []
        self._recovery_windows: list[dict[str, Any]] = []
        self._applied_event_ids: set[UUID] = set()
        self._applied_count: int = 0
        self._snapshot_path = Path(snapshot_path) if snapshot_path else None
        self._snapshot_store = snapshot_store
        self._snapshot_interval = snapshot_interval

    # ── EventBus handler ─────────────────────────────────────────────────

    async def apply(self, event: Event) -> list[Event]:
        log = logger.debug if event.event_type == EventType.SYSTEM_RUNTIME_HEARTBEAT else logger.info
        log("[STATE] apply: type=%s id=%s count=%d", event.event_type.value, str(event.event_id)[:8], self._applied_count)
        """Apply an event to state. Returns list of system events (snapshot etc)."""
        # Skip if already applied (idempotent replay)
        if event.event_id in self._applied_event_ids:
            return []

        self._applied_event_ids.add(event.event_id)
        self._applied_count += 1

        handler = self._get_handler(event.event_type)
        if handler:
            handler(event)

        # Mark derived state dirty for relevant events
        if event.event_type in _DERIVED_AFFECTING_EVENTS:
            self._derived_dirty = True

        # Auto-snapshot at interval
        produced: list[Event] = []
        if self._snapshot_store and self._applied_count % self._snapshot_interval == 0:
            try:
                self._compute_derived_if_dirty()
                last_seq = getattr(event, '_sequence', self._applied_count)
                await self._snapshot_store.save(self._state, self._applied_count)
                produced.append(Event(
                    event_type=EventType.SYSTEM_SNAPSHOT_CREATED,
                    aggregate_id="system",
                    aggregate_type=AggregateType.SYSTEM,
                    payload={"applied_count": self._applied_count},
                ))
                logger.info("auto-snapshot at %d events", self._applied_count)
            except Exception as exc:
                logger.error("auto-snapshot failed: %s", exc)
                produced.append(Event(
                    event_type=EventType.SYSTEM_SNAPSHOT_FAILED,
                    aggregate_id="system",
                    aggregate_type=AggregateType.SYSTEM,
                    payload={"error": str(exc)},
                ))

        return produced

    # ── Read views ───────────────────────────────────────────────────────

    def get_view(self, aggregate_type: str, aggregate_id: str) -> dict[str, Any]:
        """Read current state for an aggregate."""
        return self._state.get(aggregate_type, {}).get(aggregate_id, {})

    def get_all(self, aggregate_type: str) -> dict[str, dict[str, Any]]:
        """Read all aggregates of a given type."""
        return self._state.get(aggregate_type, {})

    def get_derived_view(self, name: str) -> dict[str, Any]:
        """Read a derived state metric."""
        return self._derived.get(name, {})

    def get_all_derived(self) -> dict[str, dict[str, Any]]:
        """Read all derived state."""
        self._compute_derived_if_dirty()
        return dict(self._derived)

    # ── Hash ─────────────────────────────────────────────────────────────

    def state_hash(self) -> str:
        """Deterministic hash of all state (primary + derived)."""
        self._compute_derived_if_dirty()
        combined = {"state": self._state, "derived": self._derived}
        raw = json.dumps(combined, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()

    # ── Snapshot / Rebuild ───────────────────────────────────────────────

    def snapshot(self) -> dict[str, Any]:
        """Return a full snapshot of current state."""
        self._compute_derived_if_dirty()
        return {
            "state": self._state,
            "derived": self._derived,
            "applied_count": self._applied_count,
        }

    def save_snapshot(self) -> None:
        """Persist snapshot to JSON file (backward compat)."""
        if not self._snapshot_path:
            return
        data = self.snapshot()
        self._snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        self._snapshot_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, default=str)
        )
        logger.info("snapshot saved to %s", self._snapshot_path)

    def load_snapshot(self) -> bool:
        """Load state from a JSON snapshot file. Returns True on success."""
        if not self._snapshot_path or not self._snapshot_path.exists():
            return False
        try:
            data = json.loads(self._snapshot_path.read_text())
            self._state = data.get("state", {})
            self._derived = data.get("derived", {})
            self._applied_count = data.get("applied_count", 0)
            self._derived_dirty = False
            logger.info("snapshot loaded from %s", self._snapshot_path)
            return True
        except (json.JSONDecodeError, KeyError) as exc:
            logger.warning("snapshot corrupt, skipping: %s", exc)
            return False

    async def rebuild_from_events(self, events: list[Event]) -> None:
        """Rebuild entire state by replaying a list of events."""
        self._state = {}
        self._derived = {}
        self._derived_dirty = False
        self._temporal_blocks = {}
        self._temporal_blocks_by_day = {}
        self._active_temporal_context = {}
        self._busy_windows = []
        self._recovery_windows = []
        self._applied_event_ids = set()
        self._applied_count = 0
        for event in events:
            await self.apply(event)
        self._compute_derived_if_dirty()
        logger.info("state rebuilt from %d events", len(events))

    async def rebuild_with_snapshot(self, event_store, snapshot_store) -> str:
        """Rebuild state using latest snapshot + remaining events.

        Returns the state_hash after rebuild.
        """
        # Try snapshot first
        if snapshot_store:
            snap = await snapshot_store.get_latest()
            if snap:
                snap_state, last_seq = snap
                self._state = snap_state.get("state", snap_state)
                self._derived = snap_state.get("derived", {})
                self._applied_count = last_seq
                self._derived_dirty = True
                logger.info("loaded snapshot at sequence %d", last_seq)

                # Replay remaining events
                remaining = await event_store.replay_from(last_seq)
                for event in remaining:
                    await self.apply(event)
                self._compute_derived_if_dirty()
                return self.state_hash()

        # Fallback: full replay
        logger.info("no snapshot available, full replay")
        all_events = await event_store.replay_all()
        await self.rebuild_from_events(all_events)
        return self.state_hash()

    # ── Derived state ────────────────────────────────────────────────────

    def _compute_derived_if_dirty(self) -> None:
        """Recompute derived state if marked dirty."""
        if not self._derived_dirty:
            return
        from src.derived_state.workload import compute_workload
        from src.derived_state.deadline_pressure import compute_deadline_pressure
        from src.derived_state.activity_density import compute_activity_density
        from src.derived_state.temporal_projection import compute_projection
        from src.derived_state.cognition import compute_cognition
        from src.derived_state.planning import compute_planning
        from src.derived_state.behavior import compute_behavior
        from src.derived_state.adaptive_planning import compute_adaptive_planning
        from src.derived_state.reflection import compute_reflection
        from src.derived_state.adaptation_params import compute_adapted_params

        self._derived["workload"] = compute_workload(self._state)
        self._derived["deadline_pressure"] = compute_deadline_pressure(self._state)
        self._derived["activity_density"] = compute_activity_density(self._state)
        effective_blocks = self.get_temporal_blocks()
        proj = compute_projection(effective_blocks)
        self._derived["temporal_projection"] = proj.to_dict()
        self._derived["cognition"] = compute_cognition(self._state, proj.to_dict())
        self._derived["behavior"] = compute_behavior(self._state)
        self._derived["reflection"] = compute_reflection(self._state)
        adapted_params = compute_adapted_params(
            self._derived["behavior"],
            self._derived["reflection"],
        )
        self._derived["adaptation_params"] = adapted_params
        adaptive = compute_adaptive_planning(self._derived["behavior"], self._derived["cognition"], adapted_params)
        self._derived["adaptive_planning"] = adaptive
        self._derived["planning"] = compute_planning(
            effective_blocks,
            self._derived["cognition"],
            adaptive,
        )
        self._derived_dirty = False

    # ── Internal handlers ────────────────────────────────────────────────

    def _get_handler(self, event_type: EventType):
        handlers = {
            EventType.CONNECTOR_FETCH_STARTED: self._on_connector_fetch_started,
            EventType.CONNECTOR_FETCH_COMPLETED: self._on_connector_fetch_completed,
            EventType.CONNECTOR_FETCH_FAILED: self._on_connector_fetch_failed,
            EventType.TEMPORAL_BLOCK_ADDED: self._on_temporal_block_added,
            EventType.TEMPORAL_BLOCK_UPDATED: self._on_temporal_block_updated,
            EventType.TEMPORAL_BLOCK_CANCELLED: self._on_temporal_block_cancelled,
            EventType.HOMEWORK_PARSED: self._on_homework_parsed,
            EventType.HOMEWORK_NEW: self._on_homework_new,
            EventType.SCHEDULE_PARSED: self._on_schedule_parsed,
            EventType.SCHEDULE_UPDATED: self._on_schedule_updated,
            EventType.NOTIFICATION_SEND: self._on_notification_send,
            EventType.PLANNING_RECOMMENDATION_ACCEPTED: self._on_recommendation_feedback,
            EventType.PLANNING_RECOMMENDATION_SKIPPED: self._on_recommendation_feedback,
            EventType.PLANNING_RECOMMENDATION_DELAYED: self._on_recommendation_feedback,
            EventType.PLANNING_TASK_COMPLETED: self._on_task_outcome,
            EventType.PLANNING_TASK_ABANDONED: self._on_task_outcome,
            EventType.EXECUTION_PROPOSAL_CREATED: self._on_proposal_created,
            EventType.EXECUTION_PROPOSAL_ACCEPTED: self._on_proposal_accepted,
            EventType.EXECUTION_PROPOSAL_REJECTED: self._on_proposal_rejected,
            EventType.EXECUTION_PROPOSAL_EXPIRED: self._on_proposal_expired,
            EventType.EXECUTION_COMPLETED: self._on_execution_completed,
            EventType.EXECUTION_FAILED: self._on_execution_failed,
            EventType.TELEGRAM_PROPOSAL_SENT: self._on_notification_send,
            EventType.DERIVED_STATE_UPDATED: self._on_derived_state_updated,
            EventType.DEADLINE_PRESSURE_UPDATED: self._on_derived_state_updated,
            EventType.HYDRATION_LOGGED: self._on_hydration_logged,
            EventType.INTERVENTION_TRIGGERED: self._on_notification_send,
            EventType.INTERVENTION_FEEDBACK_RECORDED: self._on_intervention_feedback,
            EventType.INTERVENTION_DELAYED: self._on_intervention_feedback,
            EventType.INTERVENTION_SKIPPED: self._on_intervention_feedback,
            EventType.COURSE_ACTIVATED: self._on_course_activated,
            EventType.COURSE_DEACTIVATED: self._on_course_deactivated,
            EventType.SEMESTER_UPDATED: self._on_semester_updated,
            EventType.MOOD_RECORDED: self._on_mood_recorded,
            EventType.SUBJECTIVE_CONTEXT_ADDED: self._on_subjective_context_added,
            EventType.MEMORY_ENTRY_CREATED: self._on_memory_entry_created,

            # Vocab
            EventType.VOCAB_SYNC_STARTED: self._on_vocab_sync_started,
            EventType.VOCAB_SYNC_COMPLETED: self._on_vocab_sync_completed,
            EventType.VOCAB_SYNC_FAILED: self._on_vocab_sync_completed,
            EventType.VOCAB_PROGRESS_UPDATED: self._on_vocab_progress_updated,
            EventType.VOCAB_SLACK_DETECTED: self._on_vocab_slack_detected,
            # Art
            EventType.ART_PLAN_CREATED: self._on_art_plan_created,
            EventType.ART_PLAN_UPDATED: self._on_art_plan_updated,
            EventType.ART_PROGRESS_RECORDED: self._on_art_progress_recorded,
            EventType.ART_BLOCK_COMPLETED: self._on_art_block_completed,
            EventType.ART_BLOCK_SKIPPED: self._on_art_block_skipped,
            EventType.ART_DAILY_REALITY_INSERTED: self._on_art_daily_reality_inserted,
            EventType.ART_OBSIDIAN_DAILY_UPDATED: self._on_obsidian_daily_updated,
            EventType.ART_PLAN_REBALANCED: self._on_art_plan_rebalanced,
            EventType.ART_VIBE_CODE_WARNING: self._on_art_vibe_code_warning,
            # Daily review
            EventType.COGNITIVE_PROFILE_AUDITED: self._on_cognitive_profile_audited,
            EventType.DAILY_REVIEW_GENERATED: self._on_daily_review_generated,
            EventType.DAILY_REVIEW_SENT: self._on_daily_review_sent,
            # Finance / Money Reality
            EventType.FINANCE_TRANSACTION_RECORDED: self._on_finance_transaction_recorded,
            EventType.FINANCE_INCOME_RECORDED: self._on_finance_income_recorded,
            EventType.FINANCE_BUDGET_UPDATED: self._on_finance_budget_updated,
            EventType.PARENT_FUND_REQUEST_PLANNED: self._on_parent_fund_request_planned,
            EventType.PARENT_FUND_REQUEST_RECORDED: self._on_parent_fund_request_recorded,
            EventType.PARENT_FUND_RECEIVED: self._on_parent_fund_received,
            EventType.PARENT_FUND_ITEM_CONFIGURED: self._on_parent_fund_item_configured,
            EventType.FINANCE_SPENDING_WARNING_TRIGGERED: self._on_finance_spending_warning_triggered,
            EventType.FINANCE_BATCH_DRAFTED: self._on_finance_batch_drafted,
            EventType.FINANCE_BATCH_ACCEPTED: self._on_finance_batch_accepted,
            EventType.FINANCE_BATCH_DISCARDED: self._on_finance_batch_discarded,
            EventType.FINANCE_REIMBURSEMENT_RECORDED: self._on_finance_reimbursement_recorded,
            EventType.PARTNER_DEBT_CREATED: self._on_partner_debt_created,
            EventType.PARENT_FUND_RULE_CONFIGURED: self._on_parent_fund_rule_configured,
            EventType.PARENT_FUND_REQUEST_PLAN_CANCELLED: self._on_parent_fund_request_plan_cancelled,
            # NL Intent
            EventType.NL_INTENT_LEARNING_SAMPLE_RECORDED: self._on_nl_learning_sample_recorded,
            EventType.NL_INTENT_HABIT_SUMMARY_CREATED: self._on_nl_habit_summary_created,
            EventType.NL_INTENT_EXECUTED: self._on_nl_intent_executed,
            # Undo / Revoke
            EventType.USER_ACTION_REVERTED: self._on_user_action_reverted,
            EventType.USER_ACTION_REVERT_FAILED: self._on_user_action_revert_failed,

            # Calendar consistency review
            EventType.CALENDAR_CONSISTENCY_REVIEW_REQUESTED: self._on_calendar_consistency_review_requested,
            EventType.CALENDAR_CONSISTENCY_REVIEW_COMPLETED: self._on_calendar_consistency_review_completed,
            EventType.CALENDAR_CONSISTENCY_REVIEW_FAILED: self._on_calendar_consistency_review_failed,
            # Calendar consistency repair
            EventType.CALENDAR_CONSISTENCY_REPAIR_REQUESTED: self._on_calendar_consistency_repair_requested,
            EventType.CALENDAR_CONSISTENCY_REPAIR_COMPLETED: self._on_calendar_consistency_repair_completed,
            EventType.CALENDAR_CONSISTENCY_REPAIR_FAILED: self._on_calendar_consistency_repair_failed,
        }
        return handlers.get(event_type)

    def _on_temporal_block_added(self, event: Event) -> None:
        """Store a TimeBlock in the temporal layer."""
        from src.core.temporal import TimeBlock
        from datetime import timedelta
        try:
            block = TimeBlock.from_dict(event.payload)
            if str(block.source) == "jwxt" and block.metadata.get("teaching_week"):
                week_start = block.start.date() - timedelta(days=block.start.weekday())
                week_end = week_start + timedelta(days=7)
                stale_keys = [
                    key for key, existing in self._temporal_blocks.items()
                    if str(existing.source) == "jwxt"
                    and not existing.metadata.get("teaching_week")
                    and week_start <= existing.start.date() < week_end
                ]
                for key in stale_keys:
                    self._temporal_blocks.pop(key, None)
            block_key = "|".join([
                str(block.source),
                block.title,
                block.start.isoformat(),
                block.end.isoformat(),
            ])
            self._temporal_blocks[block_key] = block
            self._refresh_temporal_views()
            self._derived_dirty = True
        except Exception as e:
            logger.warning("Failed to parse TimeBlock: %s", e)

    def _connector_sync_key(self, event: Event) -> str:
        source = str(event.payload.get("source") or event.metadata.get("source") or event.aggregate_id or "")
        if source == "momo_vocab":
            return "momo"
        return source

    def _update_sync_health(self, event: Event, status: str) -> None:
        source = self._connector_sync_key(event)
        if not source:
            return
        view = self._ensure_aggregate("sync", source)
        view["status"] = status
        view["source"] = source
        view["updated_at"] = event.timestamp.isoformat()
        if status == "running":
            view["last_sync_started"] = event.timestamp.isoformat()
        elif status == "completed":
            view["last_sync"] = event.timestamp.isoformat()
            view["last_sync_completed"] = event.timestamp.isoformat()
            view["error"] = ""
        elif status == "failed":
            view["last_sync_failed"] = event.timestamp.isoformat()
            view["error"] = event.payload.get("error", event.metadata.get("error_code", ""))

        for key in ("count", "raw_count", "block_count", "course_count", "total_assignments", "calendar_id"):
            if key in event.payload:
                view[key] = event.payload.get(key)
        if "duration_ms" in event.metadata:
            view["duration_ms"] = event.metadata.get("duration_ms")

    def _on_connector_fetch_started(self, event: Event) -> None:
        """Prepare source-owned temporal state before a fresh connector read."""
        self._update_sync_health(event, "running")
        if event.payload.get("source") != "google_calendar":
            return
        stale_keys = [
            key for key, existing in self._temporal_blocks.items()
            if str(getattr(existing, "source", "")) == "google_calendar"
        ]
        for key in stale_keys:
            self._temporal_blocks.pop(key, None)
        self._refresh_temporal_views()
        if stale_keys:
            self._derived_dirty = True
        temporal = self._ensure_aggregate("temporal", "projection")
        temporal["calendar_sync"] = {
            **temporal.get("calendar_sync", {}),
            "status": "running",
            "source": "google_calendar",
            "started_at": event.timestamp.isoformat(),
            "calendar_id": event.payload.get("calendar_id", ""),
        }

    def _on_connector_fetch_completed(self, event: Event) -> None:
        self._update_sync_health(event, "completed")
        if event.payload.get("source") != "google_calendar":
            return
        if int(event.payload.get("count", 0) or 0) == 0:
            stale_keys = [
                key for key, existing in self._temporal_blocks.items()
                if str(getattr(existing, "source", "")) == "google_calendar"
            ]
            for key in stale_keys:
                self._temporal_blocks.pop(key, None)
            if stale_keys:
                self._refresh_temporal_views()
                self._derived_dirty = True
        temporal = self._ensure_aggregate("temporal", "projection")
        temporal["calendar_sync"] = {
            "status": "completed",
            "source": "google_calendar",
            "completed_at": event.timestamp.isoformat(),
            "calendar_id": event.payload.get("calendar_id", ""),
            "calendar_count": event.payload.get("calendar_count", 1),
            "calendars": event.payload.get("calendars", []),
            "count": event.payload.get("count", 0),
            "raw_count": event.payload.get("raw_count", 0),
        }

    def _on_connector_fetch_failed(self, event: Event) -> None:
        self._update_sync_health(event, "failed")

    def _on_temporal_block_updated(self, event: Event) -> None:
        self._on_temporal_block_added(event)

    def _on_temporal_block_cancelled(self, event: Event) -> None:
        from src.core.temporal import TimeBlock
        try:
            block = TimeBlock.from_dict(event.payload)
        except Exception:
            return
        to_remove = []
        for key, existing in self._temporal_blocks.items():
            meta = getattr(existing, "metadata", {}) or {}
            if (
                str(existing.source) == str(block.source)
                and meta.get("external_id")
                and meta.get("external_id") == (block.metadata or {}).get("external_id")
            ):
                to_remove.append(key)
        for key in to_remove:
            self._temporal_blocks.pop(key, None)
        self._refresh_temporal_views()
        self._derived_dirty = True

    def get_temporal_blocks(self, include_school_leave_classes: bool = False) -> list[Any]:
        blocks = list(self._temporal_blocks.values())
        if include_school_leave_classes:
            return blocks
        leave_dates = self._school_leave_dates()
        if not leave_dates:
            return blocks
        return [
            block for block in blocks
            if not (
                str(getattr(block, "source", "")) == "jwxt"
                and str(getattr(block, "block_type", "")) in {"class_lecture", "class_lab"}
                and block.start.astimezone(__import__("zoneinfo").ZoneInfo("Asia/Singapore")).date().isoformat() in leave_dates
            )
        ]

    def _school_leave_dates(self) -> set[str]:
        """Return local dates where school classes should be ignored."""
        from datetime import datetime, timezone
        from zoneinfo import ZoneInfo

        now = datetime.now(timezone.utc)
        local_tz = ZoneInfo("Asia/Singapore")
        dates: set[str] = set()
        for view in self._state.get("subjective", {}).values():
            for item in list(view.get("notes", [])) + list(view.get("contexts", [])):
                if item.get("kind") != "school_leave":
                    continue
                expires_at = item.get("expires_at")
                if expires_at:
                    try:
                        expiry = datetime.fromisoformat(expires_at)
                        if expiry.tzinfo is None:
                            expiry = expiry.replace(tzinfo=timezone.utc)
                        if expiry <= now:
                            continue
                    except (TypeError, ValueError):
                        pass
                leave_date = item.get("date")
                if not leave_date:
                    created_at = item.get("created_at", "")
                    try:
                        created = datetime.fromisoformat(created_at)
                        if created.tzinfo is None:
                            created = created.replace(tzinfo=timezone.utc)
                        leave_date = created.astimezone(local_tz).date().isoformat()
                    except (TypeError, ValueError):
                        leave_date = now.astimezone(local_tz).date().isoformat()
                dates.add(str(leave_date))
        return dates

    def get_temporal_context(self) -> dict[str, Any]:
        return {
            "temporal_blocks_by_day": self._temporal_blocks_by_day,
            "active_temporal_context": self._active_temporal_context,
            "busy_windows": self._busy_windows,
            "recovery_windows": self._recovery_windows,
        }

    def get_temporal_projection(self) -> dict[str, Any]:
        return self._derived.get("temporal_projection", {})

    def _on_recommendation_feedback(self, event: Event) -> None:
        """Store recommendation feedback in behavior log."""
        view = self._ensure_aggregate("behavior", "current")
        log = view.get("feedback_log", [])

        action = "accepted"
        if event.event_type == EventType.PLANNING_RECOMMENDATION_SKIPPED:
            action = "skipped"
        elif event.event_type == EventType.PLANNING_RECOMMENDATION_DELAYED:
            action = "delayed"

        entry = {
            "action": action,
            "task_id": event.payload.get("task_id", ""),
            "timestamp": event.timestamp.isoformat(),
            "cognition_at_time": self._derived.get("cognition", {}),
            "window_type": self._derived.get("planning", {}).get(
                "recommended_windows", [{}])[0].get("type", "standard") if self._derived.get("planning", {}).get("recommended_windows") else "standard",
        }
        if event.payload.get("task"):
            entry["task"] = event.payload.get("task")
        if event.payload.get("text"):
            entry["text"] = event.payload.get("text")
        if event.payload.get("delay_minutes") is not None:
            entry["delay_minutes"] = event.payload.get("delay_minutes")
        if event.payload.get("delayed_until"):
            entry["delayed_until"] = event.payload.get("delayed_until")
        log.append(entry)
        view["feedback_log"] = log[-100:]  # cap at 100 entries
        self._derived_dirty = True

    def _on_proposal_created(self, event: Event) -> None:
        """Track new proposal in state."""
        view = self._ensure_aggregate("proposal", "active")
        pending = view.get("pending_proposals", {})
        pid = event.aggregate_id
        pending[pid] = event.payload
        view["pending_proposals"] = pending

    def _on_proposal_accepted(self, event: Event) -> None:
        """Mark proposal as accepted."""
        view = self._ensure_aggregate("proposal", "active")
        pending = view.get("pending_proposals", {})
        pid = event.aggregate_id
        if pid in pending:
            pending[pid]["status"] = "accepted"
            accepted = view.get("accepted_proposals", {})
            accepted[pid] = pending.pop(pid)
            view["accepted_proposals"] = accepted
            view["pending_proposals"] = pending

        # Track acceptance history
        history = view.get("acceptance_history", [])
        history.append({
            "proposal_id": pid,
            "action": "accepted",
            "timestamp": event.timestamp.isoformat(),
        })
        view["acceptance_history"] = history[-50:]

    def _on_proposal_rejected(self, event: Event) -> None:
        """Mark proposal as rejected."""
        view = self._ensure_aggregate("proposal", "active")
        pending = view.get("pending_proposals", {})
        pid = event.aggregate_id
        if pid in pending:
            pending[pid]["status"] = "rejected"
            rejected = view.get("rejected_proposals", {})
            rejected[pid] = pending.pop(pid)
            view["rejected_proposals"] = rejected
            view["pending_proposals"] = pending

        history = view.get("acceptance_history", [])
        history.append({
            "proposal_id": pid,
            "action": "rejected",
            "timestamp": event.timestamp.isoformat(),
        })
        view["acceptance_history"] = history[-50:]

    def _on_proposal_expired(self, event: Event) -> None:
        """Clean up expired proposal."""
        view = self._ensure_aggregate("proposal", "active")
        pending = view.get("pending_proposals", {})
        pid = event.aggregate_id
        if pid in pending:
            expired = view.get("expired_proposals", {})
            expired[pid] = pending.pop(pid)
            view["expired_proposals"] = expired
            view["pending_proposals"] = pending

    def _on_execution_completed(self, event: Event) -> None:
        """Track completed execution."""
        view = self._ensure_aggregate("proposal", "active")
        completed = view.get("completed_executions", {})
        pid = event.aggregate_id
        completed[pid] = event.payload
        view["completed_executions"] = completed

    def _on_execution_failed(self, event: Event) -> None:
        """Track failed execution."""
        view = self._ensure_aggregate("proposal", "active")
        failed = view.get("failed_executions", {})
        pid = event.aggregate_id
        failed[pid] = event.payload
        view["failed_executions"] = failed

    def _refresh_temporal_views(self) -> None:
        by_day: dict[str, list[str]] = {}
        busy: list[dict[str, Any]] = []
        recovery: list[dict[str, Any]] = []
        now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
        effective_block_keys = {
            "|".join([
                str(block.source),
                block.title,
                block.start.isoformat(),
                block.end.isoformat(),
            ])
            for block in self.get_temporal_blocks()
        }
        for key, block in self._temporal_blocks.items():
            if key not in effective_block_keys:
                continue
            day = block.start.date().isoformat()
            by_day.setdefault(day, []).append(key)
            hours = max((block.end - block.start).total_seconds() / 3600.0, 0.0)
            if str(block.block_type) != "free_slot" and hours > 0:
                busy.append({"start": block.start.isoformat(), "end": block.end.isoformat(), "title": block.title})
            if str(block.block_type) in {"recovery_block", "workout_block"}:
                recovery.append({"start": block.start.isoformat(), "end": block.end.isoformat(), "title": block.title})

        social_tonight = False
        workout_later = False
        next_workout = None
        travel_today = False
        meeting_count_today = 0
        for block in self.get_temporal_blocks():
            bt = str(block.block_type)
            if block.start.date() == now.date():
                if bt == "travel_block":
                    travel_today = True
                if bt == "meeting_block":
                    meeting_count_today += 1
            if block.start.date() == now.date() and block.start.hour >= 17 and bt == "social_block":
                social_tonight = True
            if block.start > now and bt == "workout_block":
                workout_later = True
                if next_workout is None or block.start < next_workout.start:
                    next_workout = block

        self._temporal_blocks_by_day = by_day
        self._busy_windows = busy
        self._recovery_windows = recovery
        self._active_temporal_context = {
            "social_block_tonight": social_tonight,
            "workout_block_later": workout_later,
            "next_workout": {
                "title": next_workout.title,
                "start": next_workout.start.isoformat(),
                "end": next_workout.end.isoformat(),
            } if next_workout else None,
            "travel_block_today": travel_today,
            "meeting_blocks_today": meeting_count_today,
            "total_blocks": len(effective_block_keys),
        }
        temporal = self._ensure_aggregate("temporal", "projection")
        temporal["by_day"] = self._temporal_blocks_by_day
        temporal["busy_windows"] = self._busy_windows
        temporal["recovery_windows"] = self._recovery_windows
        temporal["context"] = self._active_temporal_context


    def _on_derived_state_updated(self, event: Event) -> None:
        """Store derived state from DERIVED_STATE_UPDATED event.

        DerivedStateEngine computes, emits event, StateEngine stores.
        This keeps derived state deterministic and replay-safe.
        """
        payload = event.payload
        for key in ("deadline_pressure", "workload_density", "active_context"):
            if key in payload:
                self._derived[key] = payload[key]
        self._derived_dirty = False

    def _on_task_outcome(self, event: Event) -> None:
        """Store task outcome and link to last entry in feedback log."""
        view = self._ensure_aggregate("behavior", "current")
        log = view.get("feedback_log", [])

        outcome = "completed"
        if event.event_type == EventType.PLANNING_TASK_ABANDONED:
            outcome = "abandoned"

        if log:
            # Attach outcome to most recent feedback entry that has no outcome
            for entry in reversed(log):
                if "outcome" not in entry:
                    entry["outcome"] = outcome
                    entry["outcome_timestamp"] = event.timestamp.isoformat()
                    break
            else:
                # No matching entry, append standalone
                log.append({
                    "action": "unknown",
                    "outcome": outcome,
                    "outcome_timestamp": event.timestamp.isoformat(),
                    "task_id": event.payload.get("task_id", ""),
                })
        else:
            log.append({
                "action": "unknown",
                "outcome": outcome,
                "outcome_timestamp": event.timestamp.isoformat(),
                "task_id": event.payload.get("task_id", ""),
            })

        view["feedback_log"] = log[-100:]
        self._derived_dirty = True

    def _ensure_aggregate(self, agg_type: str, agg_id: str) -> dict[str, Any]:
        if agg_type not in self._state:
            self._state[agg_type] = {}
        if agg_id not in self._state[agg_type]:
            self._state[agg_type][agg_id] = {}
        return self._state[agg_type][agg_id]

    def _on_homework_parsed(self, event: Event) -> None:
        view = self._ensure_aggregate("homework", event.aggregate_id)
        view["last_parsed"] = event.timestamp.isoformat()
        view["count"] = event.payload.get("count", 0)
        view["source"] = event.payload.get("source")

    def _on_homework_new(self, event: Event) -> None:
        from src.domain.course_topology import normalize_course_name
        course = normalize_course_name(event.payload.get("course"))
        view = self._ensure_aggregate("homework", event.aggregate_id)
        view["title"] = event.payload.get("title")
        view["course"] = course
        view["deadline"] = event.payload.get("deadline")
        view["status"] = event.payload.get("status", "pending")
        view["raw_status"] = event.payload.get("raw_status", "")
        view["last_seen"] = event.timestamp.isoformat()

    def _on_schedule_parsed(self, event: Event) -> None:
        view = self._ensure_aggregate("schedule", event.aggregate_id)
        view["last_parsed"] = event.timestamp.isoformat()
        view["entries"] = event.payload.get("entries", [])

    def _on_schedule_updated(self, event: Event) -> None:
        view = self._ensure_aggregate("schedule", event.aggregate_id)
        view["last_updated"] = event.timestamp.isoformat()
        view["changes"] = event.payload.get("changes", [])

    def _on_notification_send(self, event: Event) -> None:
        view = self._ensure_aggregate("notification", event.aggregate_id)
        history = view.get("history", [])
        history.append({
            "message": event.payload.get("message"),
            "sent_at": event.timestamp.isoformat(),
        })
        view["history"] = history[-50:]


    def _on_hydration_logged(self, event: Event) -> None:
        """Track hydration intake."""
        view = self._ensure_aggregate("hydration", "current")
        amount = event.payload.get("amount_ml", 0)
        total = view.get("total_ml_today", 0) + amount
        history = view.get("history", [])
        history.append({
            "amount_ml": amount,
            "logged_at": event.timestamp.isoformat(),
        })
        view["total_ml_today"] = total
        view["last_drink_at"] = event.timestamp.isoformat()
        view["history"] = history[-100:]
        self._derived_dirty = True

    def _on_intervention_feedback(self, event: Event) -> None:
        """Track inline intervention feedback from Telegram cards."""
        view = self._ensure_aggregate("behavior", "current")
        log = view.get("feedback_log", [])

        action = "accepted"
        outcome = None
        if event.event_type == EventType.INTERVENTION_DELAYED:
            action = "delayed"
        elif event.event_type == EventType.INTERVENTION_SKIPPED:
            action = "skipped"
        elif event.payload.get("feedback") == "completed":
            outcome = "completed"

        planning = self._derived.get("planning", {})
        windows = planning.get("recommended_windows", [])
        entry = {
            "action": action,
            "task_id": event.payload.get("intervention_id", event.aggregate_id),
            "timestamp": event.timestamp.isoformat(),
            "source": "telegram_button",
            "cognition_at_time": self._derived.get("cognition", {}),
            "window_type": windows[0].get("type", "standard") if windows else "standard",
        }
        if outcome:
            entry["outcome"] = outcome
            entry["outcome_timestamp"] = event.timestamp.isoformat()

        log.append(entry)
        view["feedback_log"] = log[-100:]
        self._derived_dirty = True

    # ── Vocab handlers ────────────────────────────────────────────────────

    def _on_vocab_sync_started(self, event: Event) -> None:
        view = self._ensure_aggregate("vocab", "momo")
        view["sync_status"] = "running"
        view["last_sync_started"] = event.timestamp.isoformat()
        sync = self._ensure_aggregate("sync", "momo")
        sync["status"] = "running"
        sync["source"] = "momo"
        sync["last_sync_started"] = event.timestamp.isoformat()
        sync["updated_at"] = event.timestamp.isoformat()
        self._derived_dirty = True

    def _on_vocab_sync_completed(self, event: Event) -> None:
        view = self._ensure_aggregate("vocab", "momo")
        status = "failed" if event.event_type == EventType.VOCAB_SYNC_FAILED else "completed"
        view["sync_status"] = status
        view["last_sync_completed"] = event.timestamp.isoformat()
        payload = event.payload
        if "error" in payload:
            view["last_error"] = payload["error"]
        sync = self._ensure_aggregate("sync", "momo")
        sync["status"] = status
        sync["source"] = "momo"
        sync["updated_at"] = event.timestamp.isoformat()
        if status == "completed":
            sync["last_sync"] = event.timestamp.isoformat()
            sync["last_sync_completed"] = event.timestamp.isoformat()
            sync["error"] = ""
        else:
            sync["last_sync_failed"] = event.timestamp.isoformat()
            sync["error"] = payload.get("error", "")
        if payload.get("last_sync"):
            sync["external_last_sync"] = payload.get("last_sync")
        if "npm_sync_ok" in payload:
            sync["npm_sync_ok"] = payload.get("npm_sync_ok")
        self._derived_dirty = True

    def _on_vocab_progress_updated(self, event: Event) -> None:
        view = self._ensure_aggregate("vocab", "momo")
        payload = event.payload
        view["progress"] = payload.get("progress", {})
        view["today"] = payload.get("today", {})
        view["last_sync"] = payload.get("last_sync", "")
        view["stale"] = payload.get("stale", True)
        view["forgetting_count"] = payload.get("forgetting_count", 0)
        view["sticking_count"] = payload.get("sticking_count", 0)
        view["npm_sync_ok"] = payload.get("npm_sync_ok", False)
        view["slack"] = payload.get("slack", False)
        self._derived_dirty = True

    def _on_vocab_slack_detected(self, event: Event) -> None:
        view = self._ensure_aggregate("vocab", "momo")
        view["slack"] = True
        view["slack_detected_at"] = event.timestamp.isoformat()
        self._derived_dirty = True

    # ── Subjective reality handlers ───────────────────────────────────────

    def _on_mood_recorded(self, event: Event) -> None:
        view = self._ensure_aggregate("subjective", event.aggregate_id)
        history = view.get("mood_history", [])
        history.append({
            "score": event.payload.get("score", 0),
            "recorded_at": event.timestamp.isoformat(),
        })
        view["mood_history"] = history[-90:]
        view["current_mood"] = event.payload.get("score", 0)
        self._derived_dirty = True

    def _on_subjective_context_added(self, event: Event) -> None:
        from datetime import timedelta
        view = self._ensure_aggregate("subjective", event.aggregate_id)
        kind = event.payload.get("kind", "")
        text = event.payload.get("text", "")
        now = event.timestamp
        explicit_expiry = event.payload.get("expires_at")
        if kind in {"note", "social_plan", "outside", "workout", "family", "ad_hoc_task", "school_leave"}:
            midnight = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
            notes = view.get("notes", [])
            notes.append({
                "kind": kind,
                "text": text,
                "date": event.payload.get("date", ""),
                "created_at": now.isoformat(),
                "expires_at": explicit_expiry or midnight.isoformat(),
            })
            view["notes"] = notes[-50:]
        elif kind == "context":
            contexts = view.get("contexts", [])
            contexts.append({
                "kind": kind,
                "text": text,
                "created_at": now.isoformat(),
                "expires_at": explicit_expiry or (now + timedelta(hours=24)).isoformat(),
            })
            view["contexts"] = contexts[-50:]
        self._refresh_temporal_views()
        self._derived_dirty = True

    def _on_memory_entry_created(self, event: Event) -> None:
        view = self._ensure_aggregate("memory", event.aggregate_id)
        entries = view.get("entries", [])
        entries.append({
            "content": event.payload.get("content", ""),
            "tags": event.payload.get("tags", []),
            "source": event.payload.get("source", "unknown"),
            "created_at": event.timestamp.isoformat(),
        })
        view["entries"] = entries[-200:]
        self._derived_dirty = True

    def _on_cognitive_profile_audited(self, event: Event) -> None:
        view = self._ensure_aggregate("cognitive_profile", "current")
        audit = dict(event.payload)
        audit["event_id"] = str(event.event_id)
        view["latest"] = audit
        history = view.get("history", [])
        history.append(audit)
        view["history"] = history[-30:]

    def _on_daily_review_generated(self, event: Event) -> None:
        date_key = str(event.payload.get("date") or event.aggregate_id)
        view = self._ensure_aggregate("daily_review", date_key)
        view["date"] = date_key
        view["text"] = event.payload.get("text", "")
        view["audit"] = event.payload.get("audit", {})
        view["generated_at"] = event.timestamp.isoformat()
        view["generated_count"] = int(view.get("generated_count", 0) or 0) + 1

    def _on_daily_review_sent(self, event: Event) -> None:
        date_key = str(event.payload.get("date") or event.aggregate_id)
        view = self._ensure_aggregate("daily_review", date_key)
        view["date"] = date_key
        view["sent_at"] = event.timestamp.isoformat()
        view["sent_to"] = event.payload.get("sent_to", [])
        view["source"] = event.payload.get("source", "unknown")

    # ── Art planning handlers ─────────────────────────────────────────

    def _on_art_plan_created(self, event: Event) -> None:
        """Store today's art plan in state."""
        view = self._ensure_aggregate("art", "today")
        view["plan"] = {
            "date": event.payload.get("date"),
            "day_type": event.payload.get("day_type"),
            "target_minutes": event.payload.get("target_minutes"),
            "total_planned_minutes": event.payload.get("total_planned_minutes"),
            "blocks": event.payload.get("blocks", []),
            "free_slots": event.payload.get("free_slots", []),
            "created_at": event.timestamp.isoformat(),
        }
        view["progress"] = {
            "completed_minutes": 0,
            "sessions": [],
            "resistance": False,
        }
        self._derived_dirty = True

    def _on_art_plan_updated(self, event: Event) -> None:
        """Update art plan properties."""
        view = self._ensure_aggregate("art", "today")
        plan = view.get("plan", {})
        for key in ("target_minutes", "day_type", "blocks"):
            if key in event.payload:
                plan[key] = event.payload[key]
        view["plan"] = plan
        self._derived_dirty = True

    def _on_art_progress_recorded(self, event: Event) -> None:
        """Record art progress session."""
        view = self._ensure_aggregate("art", "today")
        progress = view.get("progress", {
            "completed_minutes": 0,
            "sessions": [],
            "resistance": False,
        })
        completed = event.payload.get("completed_minutes", 0)
        progress["completed_minutes"] = progress.get("completed_minutes", 0) + completed
        progress["resistance"] = progress.get("resistance", False) or event.payload.get("resistance", False)
        sessions = progress.get("sessions", [])
        sessions.append({
            "type": event.payload.get("type", ""),
            "duration_minutes": completed,
            "count": event.payload.get("count", 0),
            "note": event.payload.get("note", ""),
            "recorded_at": event.timestamp.isoformat(),
        })
        progress["sessions"] = sessions[-50:]
        view["progress"] = progress
        self._derived_dirty = True

    def _on_art_block_completed(self, event: Event) -> None:
        """Mark a specific art block as completed."""
        view = self._ensure_aggregate("art", "today")
        blocks = view.get("plan", {}).get("blocks", [])
        idx = event.payload.get("block_index", -1)
        if 0 <= idx < len(blocks):
            blocks[idx]["completed"] = True
            blocks[idx]["completed_at"] = event.timestamp.isoformat()
        self._derived_dirty = True

    def _on_art_block_skipped(self, event: Event) -> None:
        """Mark a specific art block as skipped."""
        view = self._ensure_aggregate("art", "today")
        blocks = view.get("plan", {}).get("blocks", [])
        idx = event.payload.get("block_index", -1)
        if 0 <= idx < len(blocks):
            blocks[idx]["skipped"] = True
            blocks[idx]["skipped_at"] = event.timestamp.isoformat()
        self._derived_dirty = True

    def _on_art_daily_reality_inserted(self, event: Event) -> None:
        """Record a reality insertion effect on art plan."""
        view = self._ensure_aggregate("art", "today")
        insertions = view.get("reality_insertions", [])
        insertions.append({
            "title": event.payload.get("title", event.payload.get("inserted_title", "")),
            "start": event.payload.get("start", event.payload.get("inserted_start", "")),
            "end": event.payload.get("end", event.payload.get("inserted_end", "")),
            "description": event.payload.get("description", ""),
            "recorded_at": event.timestamp.isoformat(),
        })
        view["reality_insertions"] = insertions[-50:]

    def _on_obsidian_daily_updated(self, event: Event) -> None:
        """Track obsidian daily note update."""
        view = self._ensure_aggregate("art", "today")
        obsidian_updates = view.get("obsidian_updates", [])
        obsidian_updates.append({
            "section": event.payload.get("section", ""),
            "timestamp": event.timestamp.isoformat(),
        })
        view["obsidian_updates"] = obsidian_updates[-20:]

    def _on_art_plan_rebalanced(self, event: Event) -> None:
        """Record rebalance event."""
        view = self._ensure_aggregate("art", "today")
        rebalances = view.get("rebalances", [])
        rebalances.append({
            "reason": event.payload.get("reason", ""),
            "timestamp": event.timestamp.isoformat(),
        })
        view["rebalances"] = rebalances[-20:]
        self._derived_dirty = True

    def _on_art_vibe_code_warning(self, event: Event) -> None:
        """Record vibe coding warning."""
        view = self._ensure_aggregate("art", "today")
        view["vibe_code_warning"] = {
            "recorded_at": event.timestamp.isoformat(),
            "art_starter_block_done": event.payload.get("art_starter_block_done", False),
        }

    # ── Finance / Money Reality handlers ────────────────────────────────

    def _ensure_finance_current_month(self, view: dict[str, Any], event: Event) -> None:
        """Keep the finance/monthly view scoped to the event's calendar month."""
        month_key = event.timestamp.strftime("%Y-%m")
        if view.get("month") == month_key:
            return

        if view.get("month"):
            history = view.get("history", [])
            history.append({
                "month": view.get("month"),
                "inflow": view.get("inflow", 0),
                "outflow": view.get("outflow", 0),
                "outing_spent": view.get("outing_spent", 0),
                "by_category": dict(view.get("by_category", {})),
                "closed_at": event.timestamp.isoformat(),
            })
            view["history"] = history[-12:]

        view["month"] = month_key
        view["transactions"] = []
        view["income_log"] = []
        view["warnings"] = []
        view["inflow"] = 0
        view["outflow"] = 0
        view["outing_spent"] = 0
        view["by_category"] = {}

    def _on_finance_transaction_recorded(self, event: Event) -> None:
        """Record a finance transaction and update monthly state."""
        view = self._ensure_aggregate("finance", "monthly")
        self._ensure_finance_current_month(view, event)
        amount = float(event.payload.get("amount", 0))
        category = str(event.payload.get("category", "other"))
        description = str(event.payload.get("description", ""))

        tx_log = view.get("transactions", [])
        tx_log.append({
            "event_id": event.event_id,
            "amount": amount,
            "category": category,
            "description": description,
            "timestamp": event.timestamp.isoformat(),
        })
        view["transactions"] = tx_log[-500:]
        view["outflow"] = view.get("outflow", 0) + amount

        by_category = dict(view.get("by_category", {}))
        by_category[category] = by_category.get(category, 0) + amount
        view["by_category"] = by_category

        if category == "outing":
            view["outing_spent"] = view.get("outing_spent", 0) + amount

        self._derived_dirty = True

    def _on_finance_income_recorded(self, event: Event) -> None:
        """Record income for the month."""
        view = self._ensure_aggregate("finance", "monthly")
        self._ensure_finance_current_month(view, event)
        amount = float(event.payload.get("amount", 0))
        source = str(event.payload.get("source", "other"))

        income_log = view.get("income_log", [])
        income_log.append({
            "event_id": event.event_id,
            "amount": amount,
            "source": source,
            "description": str(event.payload.get("description", "")),
            "timestamp": event.timestamp.isoformat(),
        })
        view["income_log"] = income_log[-100:]
        view["inflow"] = view.get("inflow", 0) + amount
        self._derived_dirty = True

    def _on_finance_budget_updated(self, event: Event) -> None:
        """Handle budget view requests (stored as event for replay)."""
        view = self._ensure_aggregate("finance", "monthly")
        self._ensure_finance_current_month(view, event)
        view["last_budget_view"] = {
            "action": event.payload.get("action", ""),
            "timestamp": event.timestamp.isoformat(),
        }

    def _on_parent_fund_request_planned(self, event: Event) -> None:
        """Store a parent fund planning request."""
        view = self._ensure_aggregate("parent_funds", "current")
        action = event.payload.get("action", "")

        # Narrow replay guard: skip events that contain batch/复杂 finance markers
        # but lack batch draft metadata. These are malformed legacy events from
        # multi-fact text that was incorrectly parsed as a single planned request.
        desc = str(event.payload.get("description", ""))
        _BATCH_MARKERS = {"报销", "借给", "对象", "还没要", "什么时候要", "每9天", "每"}
        has_batch_markers = sum(1 for m in _BATCH_MARKERS if m in desc) >= 2
        has_batch_meta = bool(event.metadata.get("batch_draft_id")) or bool(event.payload.get("batch_draft_id"))
        if has_batch_markers and not has_batch_meta:
            logger.info("REPLAY GUARD: skipping malformed parent_fund_request_planned with batch markers (event=%s)", str(event.event_id)[:8])
            return

        if action == "advise" or action == "show_plan":
            plan_requests = view.get("planned_requests", [])
            plan_requests.append({
                "amount": float(event.payload.get("amount", 0)),
                "description": str(event.payload.get("description", "")),
                "item_id": event.payload.get("item_id"),
                "category": str(event.payload.get("category", "")),
                "requested_date": event.payload.get("requested_date"),
                "timestamp": event.timestamp.isoformat(),
            })
            view["planned_requests"] = plan_requests[-50:]

    def _on_parent_fund_request_recorded(self, event: Event) -> None:
        """Record an actual parent fund request."""
        view = self._ensure_aggregate("parent_funds", "current")
        amount = float(event.payload.get("amount", 0))
        item_id = event.payload.get("item_id")

        from src.domain.finance.parent_fund import apply_request_record
        request_log = view.get("request_log", [])
        view["request_log"] = apply_request_record(
            request_log, amount,
            str(event.payload.get("description", "")),
            item_id,
            event.timestamp,
        )
        self._derived_dirty = True

    def _on_parent_fund_received(self, event: Event) -> None:
        """Record received parent fund."""
        view = self._ensure_aggregate("parent_funds", "current")
        received_log = view.get("received_log", [])
        received_log.append({
            "amount": float(event.payload.get("amount", 0)),
            "item_id": event.payload.get("item_id"),
            "description": str(event.payload.get("description", "")),
            "timestamp": event.timestamp.isoformat(),
        })
        view["received_log"] = received_log[-100:]
        self._derived_dirty = True

    def _on_parent_fund_item_configured(self, event: Event) -> None:
        """Configure a fixed recurring item."""
        view = self._ensure_aggregate("parent_funds", "current")
        recurring_items = list(view.get("recurring_items", []))

        item_id = str(event.payload.get("item_id", ""))
        # Find and update, or append
        found = False
        for i, item in enumerate(recurring_items):
            if item.get("item_id") == item_id:
                recurring_items[i] = {
                    **item,
                    **{k: v for k, v in event.payload.items() if k != "action"},
                }
                found = True
                break
        if not found:
            recurring_items.append({
                "item_id": item_id,
                "label": str(event.payload.get("label", item_id)),
                "amount": float(event.payload.get("amount", 0)),
                "interval_days": int(event.payload.get("interval_days", 30)),
            })
        view["recurring_items"] = recurring_items

    def _on_finance_spending_warning_triggered(self, event: Event) -> None:
        """Store triggered spending warnings."""
        view = self._ensure_aggregate("finance", "monthly")
        self._ensure_finance_current_month(view, event)
        warnings = view.get("warnings", [])
        warnings.append({
            "message": str(event.payload.get("message", "")),
            "timestamp": event.timestamp.isoformat(),
        })
        view["warnings"] = warnings[-50:]

    # ── Finance Batch Intake handlers ───────────────────────────────────

    def _on_finance_batch_drafted(self, event: Event) -> None:
        """Store a pending batch draft."""
        view = self._ensure_aggregate("finance_batches", "pending")
        draft_id = str(event.payload.get("draft_id", ""))
        if draft_id:
            view[draft_id] = {
                "draft_id": draft_id,
                "raw_text": event.payload.get("raw_text", ""),
                "items": event.payload.get("items", []),
                "questions": event.payload.get("questions", []),
                "summary": event.payload.get("summary", {}),
                "status": "drafted",
                "timestamp": event.timestamp.isoformat(),
            }

    def _on_finance_batch_accepted(self, event: Event) -> None:
        """Mark a batch draft as accepted."""
        view = self._ensure_aggregate("finance_batches", "pending")
        draft_id = str(event.payload.get("draft_id", ""))
        if draft_id in view:
            view[draft_id]["status"] = "accepted"
        accepted_view = self._ensure_aggregate("finance_batches", "accepted")
        if draft_id in view:
            accepted_view[draft_id] = dict(view[draft_id])

    def _on_finance_batch_discarded(self, event: Event) -> None:
        """Mark a batch draft as discarded."""
        view = self._ensure_aggregate("finance_batches", "pending")
        draft_id = str(event.payload.get("draft_id", ""))
        if draft_id in view:
            view[draft_id]["status"] = "discarded"

    def _on_finance_reimbursement_recorded(self, event: Event) -> None:
        """Store a reimbursement record."""
        view = self._ensure_aggregate("finance_reimbursements", "current")
        reimbursements = view.get("reimbursements", [])
        reimbursements.append({
            "gross_amount": float(event.payload.get("gross_amount", 0)),
            "reimbursed_amount": float(event.payload.get("reimbursed_amount", 0)),
            "net_amount": float(event.payload.get("net_amount", 0)),
            "description": str(event.payload.get("description", "")),
            "timestamp": event.timestamp.isoformat(),
        })
        view["reimbursements"] = reimbursements[-100:]
        self._derived_dirty = True

    def _on_partner_debt_created(self, event: Event) -> None:
        """Store a partner debt record."""
        view = self._ensure_aggregate("partner_debts", "current")
        debts = view.get("debts", [])
        debts.append({
            "amount": float(event.payload.get("amount", 0)),
            "date": event.payload.get("date", ""),
            "counterparty": str(event.payload.get("counterparty", "")),
            "description": str(event.payload.get("description", "")),
            "timestamp": event.timestamp.isoformat(),
        })
        view["debts"] = debts[-50:]
        view["total_outstanding"] = sum(d["amount"] for d in debts if not d.get("repaid", False))
        self._derived_dirty = True

    def _on_parent_fund_rule_configured(self, event: Event) -> None:
        """Store/update a recurring parent funding rule."""
        view = self._ensure_aggregate("parent_funds", "current")
        rules = list(view.get("recurring_rules", []))
        person = str(event.payload.get("person", ""))
        found = False
        for rule in rules:
            if rule.get("person") == person:
                rule.update({
                    "amount": float(event.payload.get("amount", 0)),
                    "interval_days": int(event.payload.get("interval_days", 0)),
                })
                found = True
                break
        if not found:
            rules.append({
                "person": person,
                "amount": float(event.payload.get("amount", 0)),
                "interval_days": int(event.payload.get("interval_days", 0)),
            })
        view["recurring_rules"] = rules

    def _on_parent_fund_request_plan_cancelled(self, event: Event) -> None:
        """Cancel a planned parent fund request by description match or source event_id."""
        view = self._ensure_aggregate("parent_funds", "current")
        planned = list(view.get("planned_requests", []))
        cancelled_event_id = event.payload.get("cancelled_event_id", "")
        cancel_desc = event.payload.get("description", "")

        if cancelled_event_id:
            planned = [p for p in planned if p.get("source_event_id") != cancelled_event_id]
        elif cancel_desc:
            planned = [p for p in planned if p.get("description") != cancel_desc]

        view["planned_requests"] = planned
        self._derived_dirty = True

    # ── NL Intent handlers ──────────────────────────────────────────────

    def _on_nl_learning_sample_recorded(self, event: Event) -> None:
        """Store NL learning samples for habit analysis."""
        view = self._ensure_aggregate("nl_intent", "samples")
        samples = view.get("samples", [])
        samples.append({
            "raw_text": event.payload.get("raw_text", ""),
            "intent": event.payload.get("intent", ""),
            "confidence": event.payload.get("confidence", 0),
            "success": event.payload.get("success", False),
            "error": event.payload.get("error", ""),
            "recorded_at": event.timestamp.isoformat(),
        })
        view["samples"] = samples[-500:]  # cap at 500
        view["total_count"] = view.get("total_count", 0) + 1
        self._derived_dirty = True

    def _on_nl_habit_summary_created(self, event: Event) -> None:
        """Store the latest NL habit summary."""
        view = self._ensure_aggregate("nl_intent", "habit_summary")
        summaries = view.get("summaries", [])
        summary = {
            "period_start": event.payload.get("period_start", ""),
            "period_end": event.payload.get("period_end", ""),
            "trigger_count": event.payload.get("trigger_count", 0),
            "success_count": event.payload.get("success_count", 0),
            "failure_count": event.payload.get("failure_count", 0),
            "top_intents": event.payload.get("top_intents", {}),
            "top_phrases": event.payload.get("top_phrases", []),
            "unknown_samples": event.payload.get("unknown_samples", []),
            "created_at": event.timestamp.isoformat(),
        }
        summaries.append(summary)
        view["summaries"] = summaries[-10:]  # keep last 10 summaries
        view["latest"] = summary

    def _on_nl_intent_executed(self, event: Event) -> None:
        """Track which NL intents were successfully executed."""
        view = self._ensure_aggregate("nl_intent", "executions")
        executions = view.get("executions", [])
        executions.append({
            "intent": event.payload.get("intent", ""),
            "command_type": event.payload.get("command_type", ""),
            "confidence": event.payload.get("confidence", 0),
            "executed_at": event.timestamp.isoformat(),
        })
        view["executions"] = executions[-200:]

    # ── Undo / Revoke handlers ──────────────────────────────────────────

    def _on_user_action_reverted(self, event: Event) -> None:
        """Handle revert of a tracked action. For finance, reverses amounts."""
        view = self._ensure_aggregate("undo", event.aggregate_id)
        action_id = event.payload.get("action_id")
        reverted = view.get("reverted_actions", [])
        if action_id and any(item.get("action_id") == action_id for item in reverted):
            return
        reverted.append({
            "action_type": event.payload.get("action_type"),
            "action_id": action_id,
            "reverted_at": event.timestamp.isoformat(),
        })
        view["reverted_actions"] = reverted[-50:]

        # Finance outflow: reverse amounts
        if event.payload.get("action_type") == "finance_transaction":
            amount = float(event.payload.get("amount", 0))
            category = event.payload.get("category", "other")
            finance_view = self._ensure_aggregate("finance", "monthly")
            if finance_view.get("month"):
                finance_view["outflow"] = finance_view.get("outflow", 0) - amount
                by_category = dict(finance_view.get("by_category", {}))
                by_category[category] = by_category.get(category, 0) - amount
                finance_view["by_category"] = by_category
                if category == "outing":
                    finance_view["outing_spent"] = finance_view.get("outing_spent", 0) - amount
            self._derived_dirty = True

        # Finance income: reverse
        if event.payload.get("action_type") == "finance_income":
            amount = float(event.payload.get("amount", 0))
            finance_view = self._ensure_aggregate("finance", "monthly")
            if finance_view.get("month"):
                finance_view["inflow"] = finance_view.get("inflow", 0) - amount
            self._derived_dirty = True

    def _on_user_action_revert_failed(self, event: Event) -> None:
        """Track a failed undo attempt."""
        view = self._ensure_aggregate("undo", "failures")
        failures = view.get("failures", [])
        failures.append({
            "action_id": event.payload.get("action_id"),
            "action_type": event.payload.get("action_type"),
            "error": event.payload.get("error", ""),
            "failed_at": event.timestamp.isoformat(),
        })
        view["failures"] = failures[-50:]

    # ── Calendar consistency review handlers ──────────────────────────────

    def _on_calendar_consistency_review_requested(self, event: Event) -> None:
        """Record that a consistency review was requested."""
        view = self._ensure_aggregate("calendar_consistency", "latest")
        view["last_requested_at"] = event.timestamp.isoformat()
        view["request_source"] = event.payload.get("source", "unknown")
        view["request_trace"] = event.metadata.get("trace_id", "")

    def _on_calendar_consistency_review_completed(self, event: Event) -> None:
        """Store the review result in state for dashboard queries."""
        view = self._ensure_aggregate("calendar_consistency", "latest")
        payload = event.payload
        view["last_completed_at"] = event.timestamp.isoformat()
        view["overall_severity"] = payload.get("overall_severity", "ok")
        view["findings"] = payload.get("findings", [])
        view["review_count"] = view.get("review_count", 0) + 1

        # Also keep a history trail
        history = view.get("history", [])
        history.append({
            "overall_severity": payload.get("overall_severity", "ok"),
            "finding_count": len(payload.get("findings", [])),
            "completed_at": event.timestamp.isoformat(),
        })
        view["history"] = history[-20:]  # keep last 20
        self._derived_dirty = True

    def _on_calendar_consistency_review_failed(self, event: Event) -> None:
        """Record a failed review."""
        view = self._ensure_aggregate("calendar_consistency", "latest")
        view["last_failed_at"] = event.timestamp.isoformat()
        view["last_error"] = event.payload.get("error", "unknown")
        history = view.get("history", [])
        history.append({
            "overall_severity": "error",
            "error": event.payload.get("error", "unknown"),
            "failed_at": event.timestamp.isoformat(),
        })
        view["history"] = history[-20:]

    # ── Calendar consistency repair handlers ──────────────────────────────

    def _on_calendar_consistency_repair_requested(self, event: Event) -> None:
        """Record that a repair was requested."""
        view = self._ensure_aggregate("calendar_consistency", "repair")
        view["last_repair_requested_at"] = event.timestamp.isoformat()
        view["request_source"] = event.payload.get("source", "unknown")
        view["request_trace"] = event.metadata.get("trace_id", "")

    def _on_calendar_consistency_repair_completed(self, event: Event) -> None:
        """Store the repair result in state for dashboard queries."""
        view = self._ensure_aggregate("calendar_consistency", "repair")
        payload = event.payload
        view["last_repair_completed_at"] = event.timestamp.isoformat()
        view["repair_results"] = {
            "schedule_mirror": payload.get("schedule_mirror", {}),
            "art_conflicts": payload.get("art_conflicts", {}),
            "sync_stale": payload.get("sync_stale", {}),
            "overall": payload.get("overall", "ok"),
        }
        view["repair_count"] = view.get("repair_count", 0) + 1

        history = view.get("history", [])
        history.append({
            "overall": payload.get("overall", "ok"),
            "schedule_mirror_action": payload.get("schedule_mirror", {}).get("action", "none"),
            "art_deleted": payload.get("art_conflicts", {}).get("deleted", 0),
            "completed_at": event.timestamp.isoformat(),
        })
        view["history"] = history[-20:]

    def _on_calendar_consistency_repair_failed(self, event: Event) -> None:
        """Record a failed repair."""
        view = self._ensure_aggregate("calendar_consistency", "repair")
        view["last_repair_failed_at"] = event.timestamp.isoformat()
        view["last_repair_error"] = event.payload.get("error", "unknown")
        history = view.get("history", [])
        history.append({
            "overall": "error",
            "error": event.payload.get("error", "unknown"),
            "failed_at": event.timestamp.isoformat(),
        })
        view["history"] = history[-20:]

    # ── Course lifecycle handlers ────────────────────────────────────────

    def _on_course_activated(self, event: Event) -> None:
        """Persist an activated course in state."""
        from src.domain.course_topology import is_excluded_course, normalize_course_name
        course_name = normalize_course_name(
            event.payload.get("course_name", event.aggregate_id),
            event.payload.get("teacher", ""),
        )
        view = self._ensure_aggregate("course", event.aggregate_id)
        view["course_name"] = course_name
        view["teacher"] = event.payload.get("teacher", "")
        view["source"] = event.payload.get("source", "unknown")
        view["semester"] = event.payload.get("semester", "current")
        view["active"] = not is_excluded_course(course_name)
        view["activated_at"] = event.timestamp.isoformat()

    def _on_course_deactivated(self, event: Event) -> None:
        """Mark a course inactive in state."""
        view = self._ensure_aggregate("course", event.aggregate_id)
        view["active"] = False
        view["deactivated_at"] = event.timestamp.isoformat()

    def _on_semester_updated(self, event: Event) -> None:
        """Mark all courses inactive on semester change."""
        all_courses = self._state.get("course", {})
        for course_id in all_courses:
            all_courses[course_id]["active"] = False
        self._derived_dirty = True

    @property
    def event_count(self) -> int:
        return self._applied_count


# Event types that affect derived state
_DERIVED_AFFECTING_EVENTS = {
    EventType.CONNECTOR_FETCH_STARTED,
    EventType.CONNECTOR_FETCH_COMPLETED,
    EventType.TEMPORAL_BLOCK_ADDED,
    EventType.TEMPORAL_BLOCK_UPDATED,
    EventType.TEMPORAL_BLOCK_CANCELLED,
    EventType.TEMPORAL_BLOCK_REMOVED,
    EventType.TEMPORAL_PROJECTION_UPDATED,
    EventType.HOMEWORK_NEW,
    EventType.HOMEWORK_PARSED,
    EventType.HOMEWORK_DEADLINE_APPROACHING,
    EventType.NOTIFICATION_SEND,
    EventType.PLANNING_RECOMMENDATION_ACCEPTED,
    EventType.PLANNING_RECOMMENDATION_SKIPPED,
    EventType.PLANNING_RECOMMENDATION_DELAYED,
    EventType.PLANNING_TASK_COMPLETED,
    EventType.PLANNING_TASK_ABANDONED,
    EventType.ADAPTIVE_RECOMMENDATION_ADJUSTED,
    EventType.ADAPTIVE_PATTERN_DETECTED,
    EventType.EXECUTION_PROPOSAL_CREATED,
    EventType.EXECUTION_PROPOSAL_ACCEPTED,
    EventType.EXECUTION_PROPOSAL_REJECTED,
    EventType.EXECUTION_PROPOSAL_EXPIRED,
    EventType.EXECUTION_COMPLETED,
    EventType.EXECUTION_FAILED,
    EventType.COURSE_ACTIVATED,
    EventType.COURSE_DEACTIVATED,
    EventType.SEMESTER_UPDATED,
    EventType.MOOD_RECORDED,
    EventType.MEMORY_ENTRY_CREATED,
    EventType.INTERVENTION_FEEDBACK_RECORDED,
    EventType.INTERVENTION_DELAYED,
    EventType.INTERVENTION_SKIPPED,
    EventType.SUBJECTIVE_CONTEXT_ADDED,
    EventType.VOCAB_SYNC_STARTED,
    EventType.VOCAB_SYNC_COMPLETED,
    EventType.VOCAB_SYNC_FAILED,
    EventType.VOCAB_PROGRESS_UPDATED,
    EventType.VOCAB_SLACK_DETECTED,
    EventType.ART_PLAN_CREATED,
    EventType.ART_PLAN_UPDATED,
    EventType.ART_PROGRESS_RECORDED,
    EventType.ART_BLOCK_COMPLETED,
    EventType.ART_BLOCK_SKIPPED,
    EventType.ART_PLAN_REBALANCED,
    # Finance
    EventType.FINANCE_TRANSACTION_RECORDED,
    EventType.FINANCE_INCOME_RECORDED,
    EventType.PARENT_FUND_REQUEST_RECORDED,
    EventType.PARENT_FUND_RECEIVED,
    EventType.PARENT_FUND_ITEM_CONFIGURED,
    EventType.FINANCE_REIMBURSEMENT_RECORDED,
    EventType.PARTNER_DEBT_CREATED,
    EventType.PARENT_FUND_REQUEST_PLAN_CANCELLED,
    # NL Intent
    EventType.NL_INTENT_LEARNING_SAMPLE_RECORDED,
    EventType.NL_INTENT_HABIT_SUMMARY_CREATED,
    EventType.NL_INTENT_EXECUTED,
    EventType.USER_ACTION_REVERTED,
    # Calendar consistency review
    EventType.CALENDAR_CONSISTENCY_REVIEW_COMPLETED,
    # Calendar consistency repair
    EventType.CALENDAR_CONSISTENCY_REPAIR_COMPLETED,
}
