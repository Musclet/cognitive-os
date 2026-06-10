
"""Derived State Layer ? deterministic cognition from event stream.

All derived state functions are pure: same input ? same output.
DerivedStateEngine orchestrates: read state ? compute ? emit events.
No side effects. No GPT. No LLM. Replay-safe.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from src.core.events import Event, EventType, AggregateType

logger = logging.getLogger(__name__)


class DerivedStateEngine:
    """Orchestrates derived state computation.

    Reads state from StateEngine (read-only, never mutates).
    Calls pure functions to compute cognition metrics.
    Emits DERIVED_STATE_UPDATED events through EventBus.

    Triggers:
    - SCHEDULE_TICK ? periodic derivation
    - Key domain events ? near-real-time derivation
    """

    def __init__(self, event_bus=None, state_engine=None) -> None:
        self._event_bus = event_bus
        self._state_engine = state_engine
        self._last_derived_hash: str | None = None
        self._derive_count: int = 0

    # ?? EventBus handler ?????????????????????????????????????????????

    async def on_tick(self, event: Event) -> list[Event]:
        """Handle SCHEDULE_TICK: derive and emit if changed."""
        return await self.derive(causation_id=event.event_id)

    async def on_domain_event(self, event: Event) -> list[Event]:
        """Handle domain events that may affect derived state."""
        return await self.derive(causation_id=event.event_id)

    # ?? Core derivation ??????????????????????????????????????????????

    async def derive(self, causation_id=None) -> list[Event]:
        """Compute all derived state from current StateEngine state.

        Returns list with DERIVED_STATE_UPDATED event if state changed.
        """
        if self._state_engine is None:
            return []

        state = self._state_engine._state  # read-only access to raw state

        # Call pure functions
        from derived_state.deadline_pressure import derive_deadline_pressure
        from derived_state.workload_density import derive_workload_density
        from derived_state.active_context import derive_active_context

        deadline = derive_deadline_pressure(state)
        workload = derive_workload_density(state)
        active = derive_active_context(state)

        derived = {
            "deadline_pressure": deadline,
            "workload_density": workload,
            "active_context": active,
        }

        # Hash to avoid redundant emits
        import hashlib, json
        raw = json.dumps(derived, sort_keys=True, ensure_ascii=False, default=str)
        new_hash = hashlib.sha256(raw.encode()).hexdigest()

        if new_hash == self._last_derived_hash:
            return []

        self._last_derived_hash = new_hash
        self._derive_count += 1

        if self._event_bus is None:
            return []

        event = Event(
            event_type=EventType.DERIVED_STATE_UPDATED,
            aggregate_id="derived",
            aggregate_type=AggregateType.SYSTEM,
            causation_id=causation_id,
            payload=derived,
            metadata={
                "derive_count": self._derive_count,
                "hash": new_hash[:16],
            },
        )

        logger.info(
            "[DERIVE] #%d dp=%.2f wl=%.2f ac=%d",
            self._derive_count,
            deadline.get("score", 0),
            workload.get("score", 0),
            active.get("active_course_count", 0),
        )

        # Use publish_cascade so DERIVED_STATE_UPDATED cascades into
        # InterventionEngine → INTERVENTION_TRIGGERED → Telegram send.
        # Return [] because cascade is handled internally — prevents
        # double-processing by parent publish/publish_cascade callers.
        await self._event_bus.publish_cascade(event)
        return []

    # ?? Query ?????????????????????????????????????????????????????????

    def get_last_hash(self) -> str | None:
        return self._last_derived_hash

    @property
    def derive_count(self) -> int:
        return self._derive_count


# Event types that trigger derivation (in addition to SCHEDULE_TICK)
DERIVATION_TRIGGERS = {
    EventType.CONNECTOR_FETCH_COMPLETED,
    EventType.TEMPORAL_BLOCK_UPDATED,
    EventType.TEMPORAL_BLOCK_CANCELLED,
    EventType.HOMEWORK_NEW,
    EventType.HOMEWORK_PARSED,
    EventType.HOMEWORK_DEADLINE_APPROACHING,
    EventType.SCHEDULE_UPDATED,
    EventType.SCHEDULE_PARSED,
    EventType.INTERVENTION_FEEDBACK_RECORDED,
    EventType.INTERVENTION_DELAYED,
    EventType.INTERVENTION_SKIPPED,
    EventType.MOOD_RECORDED,
    EventType.SUBJECTIVE_CONTEXT_ADDED,
}
