"""Scheduler - timer-driven event emitter.

Never calls handlers directly. Only emits events through EventBus.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from zoneinfo import ZoneInfo
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.core.events import Event, EventType, AggregateType

logger = logging.getLogger(__name__)


class CognitiveScheduler:
    """APScheduler wrapper that emits events to EventBus.

    Constraints:
    - Never calls domain handlers directly
    - All scheduling goes through EventBus
    - Jobs only produce system.scheduled_trigger events
    """

    def __init__(self, event_bus=None) -> None:
        self._scheduler = AsyncIOScheduler()
        self._event_bus = event_bus
        self._running = False

    def set_event_bus(self, event_bus) -> None:
        self._event_bus = event_bus

    def add_interval_job(
        self,
        job_id: str,
        interval_minutes: int,
        job_payload: dict | None = None,
    ) -> None:
        async def _emit():
            if self._event_bus is None:
                logger.warning("scheduler has no event bus, skipping tick")
                return
            event = Event(
                event_type=EventType.SYSTEM_SCHEDULED_TRIGGER,
                aggregate_id=job_id,
                aggregate_type=AggregateType.SYSTEM,
                timestamp=datetime.now(timezone.utc),
                payload=job_payload or {},
                metadata={"source": "scheduler", "job_id": job_id, "interval_minutes": interval_minutes},
            )
            tick = Event(
                event_type=EventType.SCHEDULE_TICK,
                aggregate_id=job_id,
                aggregate_type=AggregateType.SYSTEM,
                timestamp=datetime.now(timezone.utc),
                causation_id=event.event_id,
                payload={"job_id": job_id},
                metadata={"source": "scheduler"},
            )
            logger.debug("scheduler tick: %s", job_id)
            if hasattr(self._event_bus, "publish_cascade"):
                await self._event_bus.publish_cascade(event)
            else:
                await self._event_bus.publish(event)
            await self._event_bus.publish(tick)

        self._scheduler.add_job(_emit, "interval", minutes=interval_minutes, id=job_id, replace_existing=True)

    def add_daily_job(
        self,
        job_id: str,
        hour: int,
        minute: int,
        job_payload: dict | None = None,
        timezone_str: str = "Asia/Singapore",
    ) -> None:
        async def _emit():
            if self._event_bus is None:
                logger.warning("scheduler has no event bus, skipping daily tick")
                return
            event = Event(
                event_type=EventType.SYSTEM_SCHEDULED_TRIGGER,
                aggregate_id=job_id,
                aggregate_type=AggregateType.SYSTEM,
                timestamp=datetime.now(timezone.utc),
                payload=job_payload or {},
                metadata={
                    "source": "scheduler",
                    "job_id": job_id,
                    "schedule": f"{hour:02d}:{minute:02d}",
                    "timezone": timezone_str,
                },
            )
            tick = Event(
                event_type=EventType.SCHEDULE_TICK,
                aggregate_id=job_id,
                aggregate_type=AggregateType.SYSTEM,
                timestamp=datetime.now(timezone.utc),
                causation_id=event.event_id,
                payload={"job_id": job_id},
                metadata={"source": "scheduler"},
            )
            if hasattr(self._event_bus, "publish_cascade"):
                await self._event_bus.publish_cascade(event)
            else:
                await self._event_bus.publish(event)
            await self._event_bus.publish(tick)

        self._scheduler.add_job(
            _emit,
            "cron",
            hour=hour,
            minute=minute,
            timezone=ZoneInfo(timezone_str),
            id=job_id,
            replace_existing=True,
        )

    def start(self) -> None:
        if not self._running:
            self._scheduler.start()
            self._running = True
            logger.info("scheduler started")

    def stop(self) -> None:
        if self._running:
            self._scheduler.shutdown(wait=False)
            self._running = False
            logger.info("scheduler stopped")

    @property
    def jobs(self) -> list[dict]:
        result = []
        for job in self._scheduler.get_jobs():
            nrt = getattr(job, 'next_run_time', None)
            result.append({
                "id": job.id,
                "next_run": nrt.isoformat() if nrt else None,
                "trigger": str(job.trigger),
            })
        return result
