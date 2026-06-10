
"""Minimal Intervention Layer ? deterministic behavioral steering.

All interventions are derived from cognitive state, never random.
Cooldown + budget enforced. Replay-safe.
No GPT. No emotional AI. No chat personality.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any

from src.core.events import Event, EventType, AggregateType

logger = logging.getLogger(__name__)


@dataclass
class Intervention:
    """A behavioral steering suggestion."""
    intervention_type: str
    message: str
    priority: float  # 0.0-1.0, higher = more important
    reason: str      # why triggered, for observability


# Category grouping for cooldown enforcement
_INTERVENTION_CATEGORIES: dict[str, str] = {
    "hydration": "hydration",
    "workout_hydration": "hydration",
    "deep_work_reminder": "deep_work",
    "workload_steering": "workload",
    "cognitive_framing": "cognitive_framing",
    "vocab_reminder": "vocab",
}

# Category cooldown in hours (default 3h)
_CATEGORY_COOLDOWN_HOURS: dict[str, float] = {
    "hydration": 3.0,
    "deep_work": 4.0,
    "workload": 3.0,
    "cognitive_framing": 3.0,
    "vocab": 3.0,
}

# Important keywords that bypass cooldown
_IMPORTANT_REASON_KEYWORDS = [
    "free_window", "homework.*24h", "severe.*drift",
    "nightly_review", "critical_pressure", "deadline.*24",
]


def _is_important_reminder(reason: str) -> bool:
    """Check if a reminder reason indicates importance (bypasses cooldown)."""
    import re
    return any(re.search(kw, reason, re.IGNORECASE) for kw in _IMPORTANT_REASON_KEYWORDS)


class InterventionEngine:
    """Evaluates derived state and emits INTERVENTION_TRIGGERED events.

    Enforces:
    - Per-type cooldown (default 6h)
    - Daily budget (default 3 interventions/day)
    - Priority ordering

    Subscribes to DERIVED_STATE_UPDATED.
    """

    def __init__(
        self,
        event_bus=None,
        state_engine=None,
        cooldown_hours: float = 6.0,
        daily_budget: int = 3,
    ) -> None:
        self._event_bus = event_bus
        self._state_engine = state_engine
        self._cooldown_hours = cooldown_hours
        self._daily_budget = daily_budget

        # Tracking
        self._last_triggered: dict[str, datetime] = {}  # type -> last ts
        self._last_category_triggered: dict[str, datetime] = {}  # category -> last ts
        self._daily_count: int = 0
        self._day_reset: datetime = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        self._suppressed_count: int = 0
        self._triggered_count: int = 0

    # -- EventBus handler -----------------------------------------------

    async def on_derived_state(self, event: Event) -> list[Event]:
        """Evaluate all interventions on DERIVED_STATE_UPDATED."""
        derived = event.payload
        runtime_state = self._get_runtime_state()

        interventions: list[Intervention] = []

        # Import evaluators locally (they live in sibling modules)
        from intervention.workload_steering import evaluate_workload_steering
        from intervention.hydration import evaluate_hydration
        from intervention.cognitive_framing import evaluate_cognitive_framing

        from intervention.deep_work import evaluate_deep_work

        from intervention.vocab_reminder import evaluate_vocab_reminder

        for evaluator in (evaluate_workload_steering, evaluate_hydration, evaluate_cognitive_framing, evaluate_deep_work, evaluate_vocab_reminder):
            result = evaluator(derived, runtime_state)
            if result is not None:
                interventions.append(result)

        if not interventions:
            return []

        # Filter by cooldown + budget, sort by priority
        approved = []
        for inv in sorted(interventions, key=lambda i: -i.priority):
            if self._can_trigger(inv):
                self._record_trigger(inv)
                approved.append(inv)
            else:
                self._suppressed_count += 1
                logger.debug(
                    "[INTERVENTION] suppressed %s (cooldown/budget) priority=%.2f",
                    inv.intervention_type, inv.priority,
                )

        if not approved:
            return []

        if self._event_bus is None:
            return []

        produced = []
        for inv in approved:
            e = Event(
                event_type=EventType.INTERVENTION_TRIGGERED,
                aggregate_id="user-1",
                aggregate_type=AggregateType.USER,
                causation_id=event.event_id,
                payload={
                    "intervention_type": inv.intervention_type,
                    "message": inv.message,
                    "priority": inv.priority,
                    "reason": inv.reason,
                },
            )
            produced.append(e)
            self._triggered_count += 1
            logger.info(
                "[INTERVENTION] triggered %s priority=%.2f: %s",
                inv.intervention_type, inv.priority, inv.reason,
            )

        return produced

    # -- Cooldown + Budget ----------------------------------------------

    def _can_trigger(self, inv: Intervention) -> bool:
        """Check cooldown and budget. Uses category-level cooldown."""
        now = datetime.now(timezone.utc)

        # Reason requirement: suppress if no reason
        if not inv.reason or not inv.reason.strip():
            logger.debug("[INTERVENTION] suppressed %s: no reason provided", inv.intervention_type)
            return False

        # Reset daily counter
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if today > self._day_reset:
            self._daily_count = 0
            self._day_reset = today

        # Daily budget
        if self._daily_count >= self._daily_budget:
            return False

        # Category-based cooldown
        category = _INTERVENTION_CATEGORIES.get(inv.intervention_type, inv.intervention_type)
        cooldown = _CATEGORY_COOLDOWN_HOURS.get(category, self._cooldown_hours)

        # Important reminders bypass cooldown
        if not _is_important_reminder(inv.reason):
            last_cat = self._last_category_triggered.get(category)
            if last_cat is not None:
                elapsed = (now - last_cat).total_seconds() / 3600
                if elapsed < cooldown:
                    return False

            # Also check per-type cooldown (legacy)
            last = self._last_triggered.get(inv.intervention_type)
            if last is not None:
                elapsed = (now - last).total_seconds() / 3600
                if elapsed < cooldown:
                    return False

        return True

    def _record_trigger(self, inv: Intervention) -> None:
        now = datetime.now(timezone.utc)
        self._last_triggered[inv.intervention_type] = now
        category = _INTERVENTION_CATEGORIES.get(inv.intervention_type, inv.intervention_type)
        self._last_category_triggered[category] = now
        self._daily_count += 1

    def _get_runtime_state(self) -> dict[str, Any]:
        if self._state_engine is None:
            return {}
        return {
            "hydration": self._state_engine.get_view("hydration", "current"),
            "behavior": self._state_engine.get_view("behavior", "current"),
            "subjective": self._state_engine.get_all("subjective"),
            "temporal": self._state_engine.get_view("temporal", "projection"),
        }

    # -- Observability --------------------------------------------------

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "triggered_count": self._triggered_count,
            "suppressed_count": self._suppressed_count,
            "daily_count": self._daily_count,
            "daily_budget": self._daily_budget,
            "cooldown_hours": self._cooldown_hours,
            "last_triggered": {k: v.isoformat() for k, v in self._last_triggered.items()},
        }
