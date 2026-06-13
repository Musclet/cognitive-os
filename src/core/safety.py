"""Runtime Safety — dead-letter, timeout, retry.

Ensures connector failures never block the event loop.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from src.core.events import Event, EventType, AggregateType

logger = logging.getLogger(__name__)


@dataclass
class DeadLetterEntry:
    """A failed event stored for inspection."""
    entry_id: str
    event_id: str
    event_type: str
    error: str
    error_type: str
    handler: str
    timestamp: str
    payload: dict[str, Any] = field(default_factory=dict)
    retry_count: int = 0


class DeadLetterQueue:
    """In-memory dead-letter queue for failed events."""

    MAX_ENTRIES = 200

    def __init__(self) -> None:
        self._entries: list[DeadLetterEntry] = []

    def add(
        self,
        event: Event,
        error: Exception,
        retry_count: int = 0,
        handler: str = "",
    ) -> DeadLetterEntry:
        entry = DeadLetterEntry(
            entry_id=str(uuid4()),
            event_id=str(event.event_id),
            event_type=event.event_type.value,
            error=str(error),
            error_type=type(error).__name__,
            handler=handler,
            timestamp=datetime.now(timezone.utc).isoformat(),
            payload=event.payload,
            retry_count=retry_count,
        )
        self._entries.append(entry)
        if len(self._entries) > self.MAX_ENTRIES:
            self._entries = self._entries[-self.MAX_ENTRIES:]
        logger.warning("dead-letter: %s failed: %s", event.event_type, error)
        return entry

    def get_all(self) -> list[dict[str, Any]]:
        return [
            {
                "entry_id": e.entry_id,
                "event_id": e.event_id,
                "event_type": e.event_type,
                "error": e.error,
                "error_type": e.error_type,
                "handler": e.handler,
                "timestamp": e.timestamp,
                "retry_count": e.retry_count,
            }
            for e in self._entries
        ]

    def count(self) -> int:
        return len(self._entries)

    def clear(self) -> None:
        self._entries.clear()


class SafeHandler:
    """Wraps an async handler with timeout and dead-letter capture.

    Usage:
        safe = SafeHandler(dlq, timeout_seconds=30, max_retries=3)
        bus.subscribe(event_type, safe.wrap(original_handler))
    """

    def __init__(
        self,
        dead_letter: DeadLetterQueue,
        timeout_seconds: float = 30.0,
        max_retries: int = 3,
    ) -> None:
        self._dlq = dead_letter
        self._timeout = timeout_seconds
        self._max_retries = max_retries

    def wrap(self, handler):
        """Return a wrapped handler with timeout + dead-letter."""
        async def wrapped(event: Event) -> list[Event]:
            retries = event.metadata.get("retry_count", 0)

            for attempt in range(self._max_retries + 1):
                try:
                    result = await asyncio.wait_for(
                        handler(event),
                        timeout=self._timeout,
                    )
                    return result

                except asyncio.TimeoutError:
                    logger.error("handler timeout for %s (attempt %d)", event.event_type, attempt + 1)
                    if attempt == self._max_retries:
                        self._dlq.add(event, TimeoutError(f"timeout after {self._timeout}s"), retries + attempt)
                        return [Event(
                            event_type=EventType.SYSTEM_CONNECTOR_TIMEOUT,
                            aggregate_id=event.aggregate_id,
                            aggregate_type=AggregateType.SYSTEM,
                            causation_id=event.event_id,
                            payload={"original_event_type": event.event_type.value, "timeout_s": self._timeout},
                        )]

                except Exception as exc:
                    logger.error("handler error for %s (attempt %d): %s", event.event_type, attempt + 1, exc)
                    if attempt == self._max_retries:
                        self._dlq.add(event, exc, retries + attempt)
                        return [Event(
                            event_type=EventType.SYSTEM_EVENT_FAILED,
                            aggregate_id=event.aggregate_id,
                            aggregate_type=AggregateType.SYSTEM,
                            causation_id=event.event_id,
                            payload={"original_event_type": event.event_type.value, "error": str(exc)},
                        )]

            return []

        return wrapped
