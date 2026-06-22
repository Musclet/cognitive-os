"""Sequential cloud sync orchestration through the existing event pipeline."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from src.core.events import AggregateType, Event, EventType
from src.core.pipeline import Pipeline
from src.core.state_engine import StateEngine
from src.infrastructure.config import Settings

logger = logging.getLogger(__name__)


class CloudSyncInProgress(RuntimeError):
    """Raised when a second cloud sync starts while one is still running."""


class CloudSyncService:
    """Run JWXT, Chaoxing, and Google Calendar reads in a fixed order."""

    _SOURCES: tuple[tuple[str, str, AggregateType], ...] = (
        ("jwxt", "weekly_schedule", AggregateType.SYSTEM),
        ("chaoxing", "homework_list", AggregateType.HOMEWORK),
        ("google_calendar", "upcoming", AggregateType.SYSTEM),
    )

    def __init__(
        self,
        pipeline: Pipeline,
        state_engine: StateEngine,
        settings: Settings,
    ) -> None:
        self._pipeline = pipeline
        self._state_engine = state_engine
        self._settings = settings
        self._lock = asyncio.Lock()

    async def run(self, *, trigger: str) -> dict[str, Any]:
        if self._lock.locked():
            raise CloudSyncInProgress("sync_already_running")

        async with self._lock:
            started_at = datetime.now(timezone.utc)
            source_results: dict[str, dict[str, Any]] = {}
            total_events = 0

            for source, query, aggregate_type in self._SOURCES:
                try:
                    result, event_count = await self._sync_source(
                        source=source,
                        query=query,
                        aggregate_type=aggregate_type,
                        trigger=trigger,
                    )
                except Exception as exc:
                    logger.error(
                        "cloud sync source failed source=%s error_type=%s",
                        source,
                        type(exc).__name__,
                    )
                    result = {
                        "status": "failed",
                        "error_code": "sync_internal_error",
                        "count": 0,
                    }
                    event_count = 0
                source_results[source] = result
                total_events += event_count

            completed = sum(
                1 for result in source_results.values()
                if result["status"] == "completed"
            )
            if completed == len(source_results):
                status = "completed"
            elif completed:
                status = "partial"
            else:
                status = "failed"

            return {
                "ok": status == "completed",
                "status": status,
                "trigger": trigger,
                "started_at": started_at.isoformat(),
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "sources": source_results,
                "events": total_events,
            }

    async def _sync_source(
        self,
        *,
        source: str,
        query: str,
        aggregate_type: AggregateType,
        trigger: str,
    ) -> tuple[dict[str, Any], int]:
        previous_terminal = self._terminal_marker(source)
        request_event = Event(
            event_type=EventType.CONNECTOR_FETCH_REQUESTED,
            aggregate_id=f"cloud_sync_{source}",
            aggregate_type=aggregate_type,
            payload={
                "source": source,
                "query": query,
                "intent": "cloud_sync",
                "trigger": trigger,
            },
            metadata={"source": "cloud_sync", "trigger": trigger},
        )

        events = await self._pipeline.run(request_event)
        terminal = self._terminal_event(events, source)
        if terminal is not None:
            return self._event_summary(terminal), len(events)

        timeout = max(
            1,
            int(self._settings.cloud_sync_source_timeout_seconds),
        )
        summary = await self._wait_for_terminal(
            source,
            previous_terminal,
            timeout=timeout,
        )
        return summary, len(events)

    async def _wait_for_terminal(
        self,
        source: str,
        previous_terminal: str,
        *,
        timeout: int,
    ) -> dict[str, Any]:
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            view = self._state_engine.get_view("sync", source)
            status = str(view.get("status", ""))
            terminal_marker = self._terminal_marker(source)
            if (
                status in {"completed", "failed"}
                and terminal_marker
                and terminal_marker != previous_terminal
            ):
                return self._state_summary(source, view)
            await asyncio.sleep(0.25)

        return {
            "status": "failed",
            "error_code": "sync_timeout",
            "count": 0,
        }

    def _terminal_marker(self, source: str) -> str:
        view = self._state_engine.get_view("sync", source)
        return max(
            str(view.get("last_sync_completed") or ""),
            str(view.get("last_sync_failed") or ""),
        )

    @staticmethod
    def _terminal_event(events: list[Event], source: str) -> Event | None:
        for event in reversed(events):
            if (
                event.event_type in {
                    EventType.CONNECTOR_FETCH_COMPLETED,
                    EventType.CONNECTOR_FETCH_FAILED,
                }
                and event.payload.get("source") == source
            ):
                return event
        return None

    @classmethod
    def _event_summary(cls, event: Event) -> dict[str, Any]:
        status = (
            "completed"
            if event.event_type == EventType.CONNECTOR_FETCH_COMPLETED
            else "failed"
        )
        result = cls._safe_summary(status, event.payload, event.metadata)
        if not result["last_sync_at"]:
            result["last_sync_at"] = event.timestamp.isoformat()
        return result

    @classmethod
    def _state_summary(cls, source: str, view: dict[str, Any]) -> dict[str, Any]:
        status = str(view.get("status", "failed"))
        payload = {**view, "source": source}
        return cls._safe_summary(status, payload, {})

    @staticmethod
    def _safe_summary(
        status: str,
        payload: dict[str, Any],
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        count = (
            payload.get("temporal_blocks_count")
            if payload.get("source") == "jwxt"
            else payload.get("homework_count")
            if payload.get("source") == "chaoxing"
            else payload.get("count")
        )
        if count is None:
            count = (
                payload.get("block_count")
                or payload.get("pulled_count")
                or metadata.get("item_count")
                or 0
            )
        return {
            "status": status,
            "error_code": (
                payload.get("error_code")
                or metadata.get("error_code")
                or ("" if status == "completed" else "sync_failed")
            ),
            "count": int(count or 0),
            "pulled_count": int(payload.get("pulled_count", count or 0) or 0),
            "temporal_blocks_count": int(
                payload.get("temporal_blocks_count", 0) or 0
            ),
            "homework_count": int(payload.get("homework_count", 0) or 0),
            "last_sync_at": str(
                payload.get("last_sync_at")
                or payload.get("last_sync")
                or payload.get("last_sync_completed")
                or payload.get("last_sync_failed")
                or ""
            ),
        }
