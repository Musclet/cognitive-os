"""Pipeline - event processing executor with tracing and dedup."""

from __future__ import annotations

import logging
import time
from collections import deque

from src.core.events import Event
from src.core.bus import EventBus
from src.core.tracer import Tracer

logger = logging.getLogger(__name__)


class Pipeline:
    def __init__(self, bus: EventBus, tracer: Tracer | None = None, max_depth: int = 20) -> None:
        self._bus = bus
        self._tracer = tracer or Tracer()
        self._max_depth = max_depth

    async def run(self, initial_event: Event) -> list[Event]:
        t0 = time.monotonic()
        all_events: list[Event] = [initial_event]
        seen_ids = {initial_event.event_id}
        queue: deque[tuple[Event, int]] = deque([(initial_event, 0)])
        depth_reached = 0

        while queue:
            event, depth = queue.popleft()
            if depth >= self._max_depth:
                logger.warning("[PIPE] max depth reached at %d", depth)
                break
            depth_reached = max(depth_reached, depth)

            logger.info("[PIPE] processing: type=%s depth=%d", event.event_type.value, depth)
            traced_event, start_time = self._tracer.start_trace(event, depth)
            produced = await self._bus.publish(traced_event)
            self._tracer.end_trace(traced_event, start_time, depth)
            logger.info("[PIPE] completed: type=%s depth=%d produced=%d", event.event_type.value, depth, len(produced))

            for p in produced:
                if p.event_id not in seen_ids:
                    seen_ids.add(p.event_id)
                    all_events.append(p)
                    queue.append((p, depth + 1))

        elapsed = time.monotonic() - t0
        logger.info("[PIPE] run done: total=%d depth=%d duration=%.3fs", len(all_events), depth_reached, elapsed)
        return all_events

    @property
    def tracer(self) -> Tracer:
        return self._tracer
