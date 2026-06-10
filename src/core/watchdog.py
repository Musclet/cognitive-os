"""Runtime Watchdog — heartbeat emitter + async health monitor.

Emits system.runtime.heartbeat every N seconds through EventBus.
Tracks async task health and event loop liveness.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from src.core.events import Event, EventType, AggregateType

logger = logging.getLogger(__name__)


class RuntimeWatchdog:
    """Emits heartbeat events and monitors event loop health.

    If heartbeats stop appearing in the event log, the event loop
    is stalled (even if the process appears alive).
    """

    def __init__(self, event_bus=None, interval_seconds: float = 5.0) -> None:
        self._bus = event_bus
        self._interval = interval_seconds
        self._task: asyncio.Task | None = None
        self._last_emit: float = 0.0
        self._emit_count: int = 0
        self._running: bool = False

    def set_event_bus(self, bus) -> None:
        self._bus = bus

    @property
    def last_emit_age(self) -> float:
        """Seconds since last heartbeat was emitted."""
        if self._last_emit == 0:
            return -1.0
        return time.monotonic() - self._last_emit

    @property
    def emit_count(self) -> int:
        return self._emit_count

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        """Begin emitting heartbeats. Non-blocking."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._heartbeat_loop())
        self._task.add_done_callback(_log_task_exception)
        logger.info("[WATCHDOG] started, interval=%ss", self._interval)

    async def stop(self) -> None:
        """Stop heartbeats."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("[WATCHDOG] stopped")

    async def _heartbeat_loop(self) -> None:
        """Loop that emits heartbeat events every interval."""
        while self._running:
            try:
                await asyncio.sleep(self._interval)
                if not self._running:
                    break
                if self._bus:
                    event = Event(
                        event_type=EventType.SYSTEM_RUNTIME_HEARTBEAT,
                        aggregate_id="watchdog",
                        aggregate_type=AggregateType.SYSTEM,
                        timestamp=datetime.now(timezone.utc),
                        payload={
                            "emit_count": self._emit_count + 1,
                            "uptime_s": time.monotonic(),
                        },
                        metadata={"source": "watchdog"},
                    )
                    await self._bus.publish(event)
                self._emit_count += 1
                self._last_emit = time.monotonic()
                logger.debug("[HEARTBEAT] runtime alive, count=%d", self._emit_count)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("[WATCHDOG] heartbeat publish failed")


def _log_task_exception(task: asyncio.Task) -> None:
    """Callback to log any unhandled exception in a background task."""
    try:
        exc = task.exception()
        if exc:
            logger.error("[TASK_DEATH] background task failed: %s", exc, exc_info=exc)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error("[TASK_DEATH] task exception callback error: %s", e)


def setup_event_loop_monitoring() -> None:
    """Configure asyncio event loop with debug mode and exception handler.

    Call once at startup, before any async work.
    """
    loop = asyncio.get_event_loop()

    # Enable debug mode for slow callback detection
    loop.set_debug(True)

    def _exception_handler(loop, context):
        """Custom exception handler that logs all async exceptions."""
        msg = context.get("message", "no message")
        exception = context.get("exception")
        future = context.get("future")
        task_info = ""

        if future:
            task_info = f" future={future}"
        elif context.get("task"):
            task_info = f" task={context['task']}"

        if exception:
            logger.error(
                "[EVENT_LOOP_EXCEPTION] %s%s: %s",
                msg, task_info, exception,
                exc_info=exception,
            )
        else:
            logger.error("[EVENT_LOOP_EXCEPTION] %s%s", msg, task_info)

    loop.set_exception_handler(_exception_handler)
    logger.info("[EVENT_LOOP] debug mode enabled, exception handler set")
