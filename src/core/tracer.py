"""Tracer — structured event tracing with trace_id and duration tracking."""

from __future__ import annotations

import time
import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from src.core.events import Event

logger = logging.getLogger(__name__)


@dataclass
class TraceEntry:
    """A single trace entry for an event processing step."""
    trace_id: str
    event_id: str
    event_type: str
    aggregate_id: str
    causation_id: str | None
    start_time: str
    duration_ms: float
    source: str
    depth: int


class Tracer:
    """In-memory tracer that records event processing traces.

    Integrates with Pipeline to inject trace_id and measure duration.
    """

    MAX_ENTRIES = 500

    def __init__(self) -> None:
        self._entries: deque[TraceEntry] = deque(maxlen=self.MAX_ENTRIES)
        self._traces: dict[str, list[TraceEntry]] = {}  # trace_id → entries

    def start_trace(self, event: Event, depth: int = 0) -> tuple[Event, float]:
        """Inject trace_id into event metadata and start timing.

        Returns (event_with_trace, start_time_monotonic).
        """
        # Generate or inherit trace_id
        trace_id = event.metadata.get("trace_id")
        if trace_id is None:
            trace_id = str(uuid4())
            # Create new Event with trace metadata
            new_meta = dict(event.metadata)
            new_meta["trace_id"] = trace_id
            new_meta["source"] = new_meta.get("source", "system")
            event = Event(
                event_type=event.event_type,
                aggregate_id=event.aggregate_id,
                aggregate_type=event.aggregate_type,
                timestamp=event.timestamp,
                event_id=event.event_id,
                causation_id=event.causation_id,
                payload=event.payload,
                metadata=new_meta,
            )

        return event, time.monotonic()

    def end_trace(
        self,
        event: Event,
        start_time: float,
        depth: int = 0,
    ) -> None:
        """Record trace entry with processing duration."""
        duration_ms = (time.monotonic() - start_time) * 1000
        trace_id = event.metadata.get("trace_id", "unknown")

        entry = TraceEntry(
            trace_id=trace_id,
            event_id=str(event.event_id),
            event_type=event.event_type.value,
            aggregate_id=event.aggregate_id,
            causation_id=str(event.causation_id) if event.causation_id else None,
            start_time=datetime.now(timezone.utc).isoformat(),
            duration_ms=round(duration_ms, 3),
            source=event.metadata.get("source", "unknown"),
            depth=depth,
        )

        self._entries.append(entry)
        if trace_id not in self._traces:
            self._traces[trace_id] = []
        self._traces[trace_id].append(entry)

        logger.debug(
            "trace %s: %s (%.2fms)",
            trace_id[:8], event.event_type.value, duration_ms,
        )

    def get_trace(self, trace_id: str) -> list[dict[str, Any]] | None:
        """Get all entries for a trace_id."""
        entries = self._traces.get(trace_id)
        if entries is None:
            return None
        return [
            {
                "event_type": e.event_type,
                "aggregate_id": e.aggregate_id,
                "duration_ms": e.duration_ms,
                "depth": e.depth,
                "causation_id": e.causation_id,
                "start_time": e.start_time,
            }
            for e in sorted(entries, key=lambda x: x.start_time)
        ]

    def get_recent(self, n: int = 50) -> list[dict[str, Any]]:
        """Get recent trace entries."""
        entries = list(self._entries)[-n:]
        return [
            {
                "trace_id": e.trace_id[:8],
                "event_type": e.event_type,
                "aggregate_id": e.aggregate_id,
                "duration_ms": e.duration_ms,
                "depth": e.depth,
                "source": e.source,
                "start_time": e.start_time,
            }
            for e in entries
        ]

    def trace_count(self) -> int:
        return len(self._entries)

    def trace_ids(self) -> list[str]:
        return list(self._traces.keys())[-20:]
