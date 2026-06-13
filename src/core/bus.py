"""EventBus — central publish/subscribe channel.

Persists events to EventStore before delivery when one is configured.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from collections.abc import Callable, Coroutine
from typing import Any
from uuid import UUID

from src.core.events import AggregateType, Event, EventType
from src.core.safety import DeadLetterQueue

logger = logging.getLogger(__name__)

Handler = Callable[[Event], Coroutine[Any, Any, list[Event]]]


class EventBus:
    """Pub/sub event bus with optional durable persistence.

    When an EventStore is provided, publish() persists the event
    to the store before delivering to subscribers. If persistence
    fails, the event is not delivered.
    """

    def __init__(self, event_store=None, dead_letter: DeadLetterQueue | None = None) -> None:
        self._subscribers: dict[str, list[Handler]] = defaultdict(list)
        self._published_count: int = 0
        self._event_store = event_store
        self._dead_letter = dead_letter

        # Cascade tracking
        self._cascade_count: int = 0
        self._total_cascaded_events: int = 0
        self._max_fanout: int = 0
        self._depth_breached: int = 0
        self._fanout_samples: list[int] = []

    def subscribe(self, event_type: str, handler: Handler) -> None:
        """Register a handler for a specific event_type."""
        self._subscribers[event_type].append(handler)
        logger.debug("subscribed handler to %s", event_type)

    async def publish(self, event: Event) -> list[Event]:
        """Publish an event. Persists first if store is configured.

        Returns all events produced by handlers (for chaining in pipeline).
        """
        t0 = time.monotonic()
        log = logger.debug if event.event_type.value == "system.runtime.heartbeat" else logger.info
        log("[BUS] publish: type=%s id=%s", event.event_type.value, str(event.event_id)[:8])

        # Persist to durable store first (fail-fast: no delivery on persist failure)
        if self._event_store is not None:
            sequence = await self._event_store.append(event)
            event._sequence = sequence

        handlers = self._subscribers.get(event.event_type, [])
        if not handlers:
            logger.debug("no subscribers for %s", event.event_type)
            return []

        self._published_count += 1
        results = await asyncio.gather(
            *(handler(event) for handler in handlers),
            return_exceptions=True,
        )

        produced: list[Event] = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                handler = handlers[i]
                handler_name = getattr(handler, "__qualname__", getattr(handler, "__name__", repr(handler)))
                logger.error(
                    "handler %s failed for %s: %s",
                    handler_name,
                    event.event_type,
                    result,
                )
                if self._dead_letter is not None:
                    self._dead_letter.add(event, result, handler=handler_name)
                if event.event_type != EventType.SYSTEM_EVENT_FAILED:
                    produced.append(Event(
                        event_type=EventType.SYSTEM_EVENT_FAILED,
                        aggregate_id=event.aggregate_id,
                        aggregate_type=AggregateType.SYSTEM,
                        causation_id=event.event_id,
                        payload={
                            "failed_event_id": str(event.event_id),
                            "failed_event_type": event.event_type.value,
                            "handler": handler_name,
                            "error_type": type(result).__name__,
                            "error": str(result),
                        },
                        metadata={
                            "source": "event_bus",
                            "trace_id": event.metadata.get("trace_id", str(event.event_id)),
                        },
                    ))
                continue
            if result:
                produced.extend(result)

        elapsed = time.monotonic() - t0
        log("[BUS] publish done: type=%s produced=%d duration=%.3fs", event.event_type.value, len(produced), elapsed)
        return produced

    @property
    def subscriber_count(self) -> dict[str, int]:
        """Return a map of event_type -> number of subscribers."""
        return {k: len(v) for k, v in self._subscribers.items()}

    @property
    def published_count(self) -> int:
        return self._published_count

    async def publish_cascade(self, event: Event, max_depth: int = 10) -> list[Event]:
        """Publish an event and cascade all produced events BFS up to max_depth.

        Each cascaded event carries trace metadata:
          trace_id: root event_id (constant across the cascade)
          cascade_depth: hops from root (0 = root)

        Returns all events produced across the cascade (excluding root).
        """
        trace_id = event.event_id
        produced_all: list[Event] = []
        queue: list[tuple[Event, int]] = [(event, 0)]
        visited: set[UUID] = {event.event_id}
        fanout_total = 0

        while queue:
            next_queue: list[tuple[Event, int]] = []
            for evt, depth in queue:
                if depth >= max_depth:
                    self._depth_breached += 1
                    logger.warning(
                        "[CASCADE] max_depth=%d reached at depth=%d event=%s",
                        max_depth, depth, evt.event_type.value,
                    )
                    continue

                produced = await self.publish(evt)
                for p in produced:
                    if p.event_id in visited:
                        continue
                    visited.add(p.event_id)
                    p = p.with_metadata(
                        trace_id=str(trace_id),
                        cascade_depth=depth + 1,
                    )
                    fanout_total += 1
                    next_queue.append((p, depth + 1))
                    produced_all.append(p)

            queue = next_queue

        # Update cascade stats
        self._cascade_count += 1
        self._total_cascaded_events += fanout_total
        if fanout_total > self._max_fanout:
            self._max_fanout = fanout_total
        self._fanout_samples.append(fanout_total)
        # Keep last 100 samples
        if len(self._fanout_samples) > 100:
            self._fanout_samples = self._fanout_samples[-100:]

        logger.info(
            "[CASCADE] root=%s depth=%d fanout=%d total_events=%d",
            str(trace_id)[:8], max(depth for _, depth in [(event, 0)] + queue) if queue else 0,
            fanout_total, len(produced_all),
        )
        return produced_all

    @property
    def cascade_stats(self) -> dict[str, object]:
        """Observability: cascade explosion metrics."""
        samples = self._fanout_samples
        return {
            "total_cascades": self._cascade_count,
            "total_cascaded_events": self._total_cascaded_events,
            "max_fanout": self._max_fanout,
            "avg_fanout": sum(samples) / len(samples) if samples else 0.0,
            "depth_breached": self._depth_breached,
        }
