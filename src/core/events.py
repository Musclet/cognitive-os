"""Core event types — the type contract for the entire system.

All data flows through Event and Command. Nothing else carries data between layers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4


# ── Event Types ───────────────────────────────────────────────────────────────────────────

class EventType(StrEnum):
    """All valid event_type values. Namespaced by domain."""

    # System lifecycle
    SYSTEM_STARTUP = "system.startup"
    SYSTEM_SHUTDOWN = "system.shutdown"
    SYSTEM_SCHEDULED_TRIGGER = "system.scheduled_trigger"
    SYSTEM_FEATURE_FLAG_CHANGED = "system.feature_flag.changed"
    SYSTEM_SNAPSHOT_CREATED = "system.snapshot.created"
    SYSTEM_SNAPSHOT_FAILED = "system.snapshot.failed"
    SYSTEM_REPLAY_STARTED = "system.replay.started"
    SYSTEM_REPLAY_COMPLETED = "system.replay.completed"
    SYSTEM_EVENT_FAILED = "system.event.failed"
    SYSTEM_CONNECTOR_TIMEOUT = "system.connector.timeout"
    SYSTEM_RUNTIME_HEARTBEAT = "system.runtime.heartbeat"

    # Scheduler
    SCHEDULE_TICK = "schedule.tick"
    SCHEDULE_JOB_TRIGGERED = "schedule.job.triggered"

    # User input
    USER_COMMAND_RECEIVED = "user.command.received"
    USER_SESSION_STARTED = "user.session.started"

    # Connector lifecycle
    CONNECTOR_FETCH_REQUESTED = "connector.fetch.requested"
    CONNECTOR_FETCH_STARTED = "connector.fetch.started"
    CONNECTOR_FETCH_COMPLETED = "connector.fetch.completed"
    CONNECTOR_FETCH_FAILED = "connector.fetch.failed"
    SYNC_STARTED = "sync.started"
    SYNC_PROGRESS = "sync.progress"

    # Schedule domain
    SCHEDULE_RAW_DATA_RECEIVED = "schedule.raw_data.received"
    SCHEDULE_PARSED = "schedule.parsed"
    SCHEDULE_UPDATED = "schedule.updated"
    SCHEDULE_REMINDER_DUE = "schedule.reminder.due"

    # Course domain (Reality Topology)
    COURSE_ACTIVATED = "course.activated"
    COURSE_DEACTIVATED = "course.deactivated"
    SEMESTER_UPDATED = "semester.updated"

    # Homework domain
    HOMEWORK_RAW_DATA_RECEIVED = "homework.raw_data.received"
    HOMEWORK_PARSED = "homework.parsed"
    HOMEWORK_NEW = "homework.new"
    HOMEWORK_DEADLINE_APPROACHING = "homework.deadline_approaching"
    HOMEWORK_REMINDER_DUE = "homework.reminder.due"

    # Notification
    NOTIFICATION_SEND = "notification.send"

    # Reminder
    REMINDER_TRIGGERED = "reminder.triggered"
    TELEGRAM_SEND = "telegram.send"
    TELEGRAM_SENT = "telegram.sent"

    # Memory (future)
    MEMORY_ENTRY_CREATED = "memory.entry.created"
    MEMORY_QUERY_REQUESTED = "memory.query.requested"
    # Cognition
    COGNITION_PRESSURE_UPDATED = "cognition.pressure.updated"
    COGNITION_RECOMMENDATION_GENERATED = "cognition.recommendation.generated"

    # Derived state
    DERIVED_STATE_UPDATED = "derived_state.updated"
    DEADLINE_PRESSURE_UPDATED = "deadline_pressure.updated"

    TEMPORAL_BLOCK_ADDED = "temporal.block.added"
    TEMPORAL_BLOCK_UPDATED = "temporal.block.updated"
    TEMPORAL_BLOCK_CANCELLED = "temporal.block.cancelled"
    TEMPORAL_BLOCK_REMOVED = "temporal.block.removed"
    TEMPORAL_PROJECTION_UPDATED = "temporal.projection.updated"
    PLANNING_WINDOW_RECOMMENDED = "planning.window.recommended"
    PLANNING_OVERLOAD_DETECTED = "planning.overload.detected"
    PLANNING_RECOVERY_SUGGESTED = "planning.recovery.suggested"

    # Behavioral feedback
    PLANNING_RECOMMENDATION_ACCEPTED = "planning.recommendation.accepted"
    PLANNING_RECOMMENDATION_SKIPPED = "planning.recommendation.skipped"
    PLANNING_RECOMMENDATION_DELAYED = "planning.recommendation.delayed"
    PLANNING_TASK_COMPLETED = "planning.task.completed"
    PLANNING_TASK_ABANDONED = "planning.task.abandoned"

    # Adaptive planning
    ADAPTIVE_RECOMMENDATION_ADJUSTED = "adaptive.recommendation.adjusted"
    ADAPTIVE_PATTERN_DETECTED = "adaptive.pattern.detected"

    # Intervention
    INTERVENTION_TRIGGERED = "intervention.triggered"
    INTERVENTION_FEEDBACK_RECORDED = "intervention.feedback.recorded"
    INTERVENTION_DELAYED = "intervention.delayed"
    INTERVENTION_SKIPPED = "intervention.skipped"
    HYDRATION_LOGGED = "hydration.logged"

    # Subjective cognition — human-in-the-loop reality correction
    MOOD_RECORDED = "mood.recorded"
    SUBJECTIVE_CONTEXT_ADDED = "subjective.context.added"

    # Execution proposals
    EXECUTION_PROPOSAL_CREATED = "execution.proposal.created"
    EXECUTION_PROPOSAL_ACCEPTED = "execution.proposal.accepted"
    EXECUTION_PROPOSAL_REJECTED = "execution.proposal.rejected"
    EXECUTION_PROPOSAL_EXPIRED = "execution.proposal.expired"
    EXECUTION_COMPLETED = "execution.completed"
    EXECUTION_FAILED = "execution.failed"
    EXECUTION_REQUESTED = "execution.requested"
    CALENDAR_EVENT_CREATED = "calendar.event.created"
    CALENDAR_EVENT_UPDATED = "calendar.event.updated"
    CALENDAR_EVENT_DELETED = "calendar.event.deleted"
    CALENDAR_SCHEDULE_SYNC_REQUESTED = "calendar.schedule_sync.requested"
    CALENDAR_SCHEDULE_SYNC_COMPLETED = "calendar.schedule_sync.completed"
    CALENDAR_SCHEDULE_SYNC_FAILED = "calendar.schedule_sync.failed"
    USER_ACCEPTED_PROPOSAL = "user.accepted.proposal"
    USER_REJECTED_PROPOSAL = "user.rejected.proposal"
    TELEGRAM_PROPOSAL_SENT = "telegram.proposal.sent"

    # Google Calendar auth lifecycle
    GOOGLE_CALENDAR_AUTH_STARTED = "google_calendar.auth.started"
    GOOGLE_CALENDAR_AUTH_COMPLETED = "google_calendar.auth.completed"
    GOOGLE_CALENDAR_AUTH_FAILED = "google_calendar.auth.failed"
    GOOGLE_CALENDAR_TOKEN_REFRESHED = "google_calendar.token.refreshed"
    GOOGLE_CALENDAR_TOKEN_EXPIRED = "google_calendar.token.expired"

    # Vocabulary (Momo)
    VOCAB_SYNC_STARTED = "vocab.sync.started"
    VOCAB_SYNC_COMPLETED = "vocab.sync.completed"
    VOCAB_SYNC_FAILED = "vocab.sync.failed"
    VOCAB_TASK_UPDATED = "vocab.task.updated"
    VOCAB_PROGRESS_UPDATED = "vocab.progress.updated"
    VOCAB_SLACK_DETECTED = "vocab.slack.detected"

    # Art planning
    ART_PLAN_REQUESTED = "art.plan.requested"
    ART_PLAN_CREATED = "art.plan.created"
    ART_PLAN_UPDATED = "art.plan.updated"
    ART_PROGRESS_RECORDED = "art.progress.recorded"
    ART_BLOCK_COMPLETED = "art.block.completed"
    ART_BLOCK_SKIPPED = "art.block.skipped"
    ART_DAILY_REALITY_INSERTED = "art.daily.reality_inserted"
    ART_OBSIDIAN_DAILY_UPDATED = "art.obsidian.daily_updated"
    ART_PLAN_REBALANCED = "art.plan.rebalanced"
    ART_VIBE_CODE_WARNING = "art.vibe_code.warning"

    # Daily review / cognitive audit
    DAILY_REVIEW_REQUESTED = "daily_review.requested"
    DAILY_REVIEW_GENERATED = "daily_review.generated"
    DAILY_REVIEW_SENT = "daily_review.sent"
    COGNITIVE_PROFILE_AUDITED = "cognitive_profile.audited"

    # Finance / Money Reality
    FINANCE_TRANSACTION_RECORDED = "finance.transaction.recorded"
    FINANCE_INCOME_RECORDED = "finance.income.recorded"
    FINANCE_BUDGET_UPDATED = "finance.budget.updated"
    PARENT_FUND_REQUEST_PLANNED = "parent_fund.request.planned"
    PARENT_FUND_REQUEST_RECORDED = "parent_fund.request.recorded"
    PARENT_FUND_RECEIVED = "parent_fund.received"
    PARENT_FUND_ITEM_CONFIGURED = "parent_fund.item.configured"
    PARENT_FUND_RULE_CONFIGURED = "parent_fund.rule.configured"
    PARENT_FUND_REQUEST_PLAN_CANCELLED = "parent_fund.request.plan_cancelled"
    FINANCE_SPENDING_WARNING_TRIGGERED = "finance.spending_warning.triggered"
    FINANCE_BATCH_DRAFTED = "finance.batch.drafted"
    FINANCE_BATCH_ACCEPTED = "finance.batch.accepted"
    FINANCE_BATCH_DISCARDED = "finance.batch.discarded"
    FINANCE_REIMBURSEMENT_RECORDED = "finance.reimbursement.recorded"
    PARTNER_DEBT_CREATED = "partner.debt.created"
    PARTNER_DEBT_REPAID = "partner.debt.repaid"

    # Natural Language Intent Parsing
    NL_INTENT_PARSE_REQUESTED = "nl.intent.parse_requested"
    NL_INTENT_PARSED = "nl.intent.parsed"
    NL_INTENT_PARSE_FAILED = "nl.intent.parse_failed"
    NL_INTENT_EXECUTED = "nl.intent.executed"
    NL_INTENT_LEARNING_SAMPLE_RECORDED = "nl.intent.learning_sample_recorded"
    NL_INTENT_HABIT_SUMMARY_CREATED = "nl.intent.habit_summary_created"

    # Undo / Revoke
    USER_UNDO_REQUESTED = "user.undo.requested"
    USER_ACTION_REVERTED = "user.action.reverted"
    USER_ACTION_REVERT_FAILED = "user.action.revert_failed"

    # Calendar consistency review (post-sync auto-audit)
    CALENDAR_CONSISTENCY_REVIEW_REQUESTED = "calendar.consistency.review_requested"
    CALENDAR_CONSISTENCY_REVIEW_COMPLETED = "calendar.consistency.review_completed"
    CALENDAR_CONSISTENCY_REVIEW_FAILED = "calendar.consistency.review_failed"
    # Calendar consistency repair (review → safe-fix → re-verify)
    CALENDAR_CONSISTENCY_REPAIR_REQUESTED = "calendar.consistency.repair_requested"
    CALENDAR_CONSISTENCY_REPAIR_COMPLETED = "calendar.consistency.repair_completed"
    CALENDAR_CONSISTENCY_REPAIR_FAILED = "calendar.consistency.repair_failed"


class AggregateType(StrEnum):
    """Aggregate namespaces used by state and event storage."""

    SYSTEM = "system"
    USER = "user"
    HOMEWORK = "homework"
    NOTIFICATION = "notification"
    TEMPORAL = "temporal"
    COURSE = "course"
    VOCAB = "vocab"
    ART = "art"
    FINANCE = "finance"
    NL_INTENT = "nl_intent"


@dataclass
class Event:
    """Immutable-ish event envelope used across the runtime."""

    event_type: EventType
    aggregate_id: str
    aggregate_type: AggregateType
    payload: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    event_id: UUID = field(default_factory=uuid4)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    causation_id: UUID | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.event_type, EventType):
            self.event_type = EventType(self.event_type)
        if not isinstance(self.aggregate_type, AggregateType):
            self.aggregate_type = AggregateType(self.aggregate_type)
        self.aggregate_id = str(self.aggregate_id)
        if not isinstance(self.event_id, UUID):
            self.event_id = UUID(str(self.event_id))
        if self.causation_id is not None and not isinstance(self.causation_id, UUID):
            self.causation_id = UUID(str(self.causation_id))
        if isinstance(self.timestamp, str):
            self.timestamp = datetime.fromisoformat(self.timestamp.replace("Z", "+00:00"))
        if self.timestamp.tzinfo is None:
            self.timestamp = self.timestamp.replace(tzinfo=timezone.utc)
        if self.payload is None:
            self.payload = {}
        if self.metadata is None:
            self.metadata = {}

    def to_dict(self) -> dict[str, Any]:
        """Serialize for SQLite event_log writes."""
        return {
            "event_id": str(self.event_id),
            "event_type": self.event_type.value,
            "aggregate_id": self.aggregate_id,
            "aggregate_type": self.aggregate_type.value,
            "timestamp": self.timestamp.isoformat(),
            "causation_id": str(self.causation_id) if self.causation_id else None,
            "payload": json.dumps(self.payload, ensure_ascii=False),
            "metadata": json.dumps(self.metadata, ensure_ascii=False),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Event":
        payload = data.get("payload", {})
        metadata = data.get("metadata", {})
        if isinstance(payload, str):
            payload = json.loads(payload) if payload else {}
        if isinstance(metadata, str):
            metadata = json.loads(metadata) if metadata else {}
        return cls(
            event_id=data.get("event_id", uuid4()),
            event_type=data["event_type"],
            aggregate_id=data["aggregate_id"],
            aggregate_type=data["aggregate_type"],
            timestamp=data.get("timestamp") or datetime.now(timezone.utc),
            causation_id=data.get("causation_id"),
            payload=payload,
            metadata=metadata,
        )

    def with_causation(self, causation_id: UUID | str | None) -> "Event":
        """Return a copy of this event with a causation id."""
        return Event(
            event_type=self.event_type,
            aggregate_id=self.aggregate_id,
            aggregate_type=self.aggregate_type,
            payload=dict(self.payload),
            metadata=dict(self.metadata),
            event_id=self.event_id,
            timestamp=self.timestamp,
            causation_id=causation_id,
        )


@dataclass
class Command:
    """Parsed user command before conversion into USER_COMMAND_RECEIVED."""

    command_type: str
    user_id: str
    params: dict[str, Any] = field(default_factory=dict)
    source: str = "telegram"

    def __post_init__(self) -> None:
        self.user_id = str(self.user_id)
        if self.params is None:
            self.params = {}


@dataclass
class OutputEvent:
    """Simple interface output envelope."""

    user_id: str
    text: str
    payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.user_id = str(self.user_id)
        if self.payload is None:
            self.payload = {}

