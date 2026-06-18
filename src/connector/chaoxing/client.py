"""Chaoxing Connector — event-driven, read-only Reality Adapter.

Replaces old mock. Uses real Playwright browser to fetch
course list and assignment data from 学习通.

Constraints:
- Read-only: only fetches data, never writes state
- Event-driven: subscribes to connector.fetch.requested, emits connector.fetch.completed
- No business logic: no notification, no scheduling, no task classification
- No side effects: no workspace writes, no file downloads (raw data only)
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from src.connector.base import Connector
from src.core.events import Event, EventType, AggregateType
from src.connector.chaoxing.browser import ChaoxingBrowser
from src.connector.chaoxing.course_scraper import fetch_course_list
from src.connector.chaoxing.assignment_scraper import fetch_all_assignments
from src.infrastructure.config import Settings

logger = logging.getLogger(__name__)

CHAOXING_ERROR_MESSAGES = {
    "chaoxing_state_file_missing": "Chaoxing login state is not configured.",
    "chaoxing_session_expired": "Chaoxing login state has expired. Refresh the local state file.",
    "chaoxing_auth_failed": "Chaoxing authentication failed.",
    "chaoxing_playwright_missing": "Playwright is not available for Chaoxing sync.",
    "chaoxing_browser_unavailable": "The browser required for Chaoxing sync is unavailable.",
    "chaoxing_sync_failed": "Chaoxing homework sync failed.",
}


class ChaoxingConnector(Connector):
    """Chaoxing (学习通) data connector.

    Fetches homework/course data via Playwright browser automation.
    """

    source_name = "chaoxing"

    def __init__(
        self,
        use_mock: bool = False,
        state_file: str | None = None,
        headless: bool = True,
        event_bus=None,
        course_registry=None,
        settings: Settings | None = None,
    ) -> None:
        self._use_mock = use_mock
        self._state_file = state_file or (settings or Settings()).chaoxing_state_file
        self._headless = headless
        self._event_bus = event_bus
        self._course_registry = course_registry
        self._browser: ChaoxingBrowser | None = None
        self._authenticated = False
        self._fetching: bool = False
        # Connector health metrics
        self._fetch_count: int = 0
        self._fetch_success: int = 0
        self._fetch_failure: int = 0
        self._session_expired: bool = False
        self._last_fetch_at: float | None = None
        self._last_fetch_duration_ms: float = 0
        self._last_error: str | None = None
        self._last_error_code: str = ""

    @property
    def stats(self) -> dict[str, Any]:
        """Connector health metrics (follows InterventionEngine.stats pattern)."""
        return {
            "fetch_count": self._fetch_count,
            "fetch_success": self._fetch_success,
            "fetch_failure": self._fetch_failure,
            "session_expired": self._session_expired,
            "last_fetch_at": self._last_fetch_at,
            "last_fetch_duration_ms": self._last_fetch_duration_ms,
            "last_error": self._last_error,
            "last_error_code": self._last_error_code,
            "fetching": self._fetching,
            "authenticated": self._authenticated,
            "mock_enabled": self._use_mock,
            "state_file_present": bool(
                not self._use_mock and Path(self._state_file).exists()
            ),
        }

    async def authenticate(self) -> bool:
        """Validate that the state file exists (auth is via stored state)."""
        if self._use_mock:
            self._authenticated = True
            self._last_error_code = ""
            return True

        self._browser = ChaoxingBrowser(
            state_file=self._state_file,
            headless=self._headless,
        )
        if not self._browser.state_path.exists():
            logger.warning(
                "State file not found: %s. Run login_and_save_state() first.",
                self._state_file,
            )
            self._last_error_code = "chaoxing_state_file_missing"
            self._last_error = CHAOXING_ERROR_MESSAGES[self._last_error_code]
            return False

        self._authenticated = True
        self._last_error_code = ""
        self._last_error = None
        return True

    async def keepalive(self) -> bool:
        """Periodic lightweight visit to prevent server-side idle timeout."""
        if self._use_mock:
            return True
        if not self._browser:
            return False
        try:
            await self._browser.start()
            ok = await self._browser.keepalive()
            if ok:
                self._session_expired = False
                await self._browser.save_state()
                logger.debug("[SESSION] keepalive ok, state saved")
            else:
                self._session_expired = True
                logger.warning("[SESSION] keepalive failed — session may be expired")
            return ok
        except Exception as exc:
            logger.warning(
                "[SESSION] keepalive error_type=%s",
                type(exc).__name__,
            )
            return False

    async def fetch(self, params: dict[str, Any]) -> dict[str, Any]:
        """Fetch raw data from Chaoxing.

        params:
            query: "homework_list" | "course_list"
        """
        if self._use_mock:
            return self._mock_data(params.get("query", ""))

        if not self._browser:
            return self._error_result("chaoxing_auth_failed")

        # Ensure browser is started (idempotent) before session check
        try:
            await self._browser.start()
        except Exception as exc:
            error_code = _classify_browser_error(exc)
            logger.warning(
                "Chaoxing browser startup failed error_code=%s error_type=%s",
                error_code,
                type(exc).__name__,
            )
            return self._error_result(error_code)

        # Session expiry check (only in real mode)
        if not self._session_expired:
            session_ok = await self._browser.check_session_valid()
            if not session_ok:
                self._session_expired = True
                logger.error(
                    "[SESSION] Chaoxing session expired. Run login_and_save_state() "
                    "to generate a fresh state file at: %s", self._state_file,
                )
                return {
                    "source": self.source_name,
                    "error_code": "chaoxing_session_expired",
                    "error": CHAOXING_ERROR_MESSAGES["chaoxing_session_expired"],
                    "session_expired": True,
                    "mock_enabled": False,
                }
        else:
            return {
                "source": self.source_name,
                "error_code": "chaoxing_session_expired",
                "error": CHAOXING_ERROR_MESSAGES["chaoxing_session_expired"],
                "session_expired": True,
                "mock_enabled": False,
            }

        query = params.get("query", "")

        if query == "course_list":
            return await self._fetch_courses()

        if query == "homework_list":
            scope = params.get("scope", None)
            on_progress = params.get("on_progress", None)
            return await self._fetch_homework(scope=scope, on_progress=on_progress)

        return {"source": self.source_name, "error": f"unknown query: {query}"}

    async def _fetch_courses(self) -> dict[str, Any]:
        """Fetch course list. Uses persistent browser."""
        assert self._browser is not None
        await self._browser.start()
        courses = await fetch_course_list(self._browser)
        return {
            "source": self.source_name,
            "courses": courses,
            "count": len(courses),
            "mock_enabled": False,
        }

    async def _fetch_homework(self, scope=None, on_progress=None) -> dict[str, Any]:
        """Fetch homework assignments, optionally scoped to specific courses.
        Uses persistent browser: start once, reuse across fetches.
        """
        assert self._browser is not None
        
        # Ensure browser is running (idempotent start)
        await self._browser.start()
        
        courses = await fetch_course_list(self._browser)
        if not courses:
            return {
                "source": self.source_name,
                "homeworks": [],
                "courses": [],
                "total_assignments": 0,
                "pulled_count": 0,
                "homework_count": 0,
                "mock_enabled": False,
            }

        # Scope filtering: match by course_id or name contains keyword
        if scope:
            filtered = []
            skipped = []
            for c in courses:
                cid = c.get("course_id", "")
                name = c.get("name", "")
                if cid in scope or name in scope or any(kw in name for kw in scope):
                    filtered.append(c)
                else:
                    skipped.append(name)
            if skipped:
                logger.info(
                    "[CHAOXING_FILTER] skipped %d non-active courses: %s",
                    len(skipped), ", ".join(skipped[:20]),
                )
            if filtered:
                courses = filtered
                logger.info("[CHAOXING_FILTER] %d/%d courses selected", len(courses), len(courses) + len(skipped))
            else:
                logger.warning("[CHAOXING_FILTER] no courses matched scope, fetching nothing")

        homeworks = await fetch_all_assignments(self._browser, courses, on_progress=on_progress)

        # Filter out errors
        valid = [h for h in homeworks if h.get("title")]
        errors = [h for h in homeworks if not h.get("title")]

        return {
            "source": self.source_name,
            "homeworks": valid,
            "courses": courses,
            "total_courses": len(courses),
            "total_assignments": len(valid),
            "pulled_count": len(valid),
            "homework_count": len(valid),
            "errors": len(errors),
            "mock_enabled": False,
        }

    # -- EventBus handler --

    async def handle_fetch_request(self, event: Event) -> list[Event]:
        """EventBus handler: spawn background fetch, return SYNC_STARTED immediately.

        Background task emits progress + completion via EventBus.
        """
        if event.payload.get("source") != self.source_name:
            return []

        started_event = self._started_event(event)

        if not self._authenticated:
            ok = await self.authenticate()
            if not ok:
                return [
                    started_event,
                    self._failed_event(
                        event,
                        self._last_error_code or "chaoxing_auth_failed",
                    ),
                ]

        if self._use_mock or self._event_bus is None:
            return [started_event, await self._terminal_fetch_event(event, event.payload)]

        if self._fetching:
            logger.warning("Fetch already in progress, ignoring duplicate")
            return []

        scope = event.payload.get("scope", None)
        # Auto-derive scope from registry if not explicitly provided
        if scope is None and self._course_registry is not None:
            self._course_registry.compute_scores()
            scope = self._course_registry.get_active_scope_names()
            if scope:
                logger.info("[SCOPE] derived from registry: %d courses", len(scope))
            else:
                logger.info("[SCOPE] registry empty, doing full fetch")
        course_count = len(scope) if scope else "all"

        task = asyncio.create_task(self._batched_fetch(event, scope))
        task.add_done_callback(_log_bg_task_exception)

        return [started_event, Event(
            event_type=EventType.SYNC_STARTED,
            aggregate_id=event.aggregate_id,
            aggregate_type=AggregateType.HOMEWORK,
            causation_id=event.event_id,
            payload={
                "source": self.source_name,
                "course_count": course_count,
                "mock_enabled": False,
            },
        )]

    def _started_event(self, event: Event) -> Event:
        return Event(
            event_type=EventType.CONNECTOR_FETCH_STARTED,
            aggregate_id=event.aggregate_id,
            aggregate_type=AggregateType.HOMEWORK,
            causation_id=event.event_id,
            payload={
                "source": self.source_name,
                "query": event.payload.get("query", ""),
                "mock_enabled": self._use_mock,
            },
            metadata={"trace_id": str(event.event_id)},
        )

    def _completed_event(
        self,
        event: Event,
        data: dict[str, Any],
        duration_ms: float | None = None,
    ) -> Event:
        payload = dict(data)
        payload.setdefault("source", self.source_name)
        payload.setdefault("mock_enabled", self._use_mock)
        payload.setdefault("pulled_count", len(payload.get("homeworks", [])))
        payload.setdefault("homework_count", len(payload.get("homeworks", [])))
        payload.setdefault("total_assignments", payload["pulled_count"])
        metadata = {"trace_id": str(event.event_id)}
        if duration_ms is not None:
            metadata["duration_ms"] = round(duration_ms, 1)
        return Event(
            event_type=EventType.CONNECTOR_FETCH_COMPLETED,
            aggregate_id=event.aggregate_id,
            aggregate_type=AggregateType.HOMEWORK,
            causation_id=event.event_id,
            payload=payload,
            metadata=metadata,
        )

    def _failed_event(
        self,
        event: Event,
        error_code: str,
        duration_ms: float | None = None,
    ) -> Event:
        metadata = {"trace_id": str(event.event_id)}
        if duration_ms is not None:
            metadata["duration_ms"] = round(duration_ms, 1)
        return Event(
            event_type=EventType.CONNECTOR_FETCH_FAILED,
            aggregate_id=event.aggregate_id,
            aggregate_type=AggregateType.HOMEWORK,
            causation_id=event.event_id,
            payload={
                "source": self.source_name,
                "error_code": error_code,
                "error": CHAOXING_ERROR_MESSAGES.get(
                    error_code,
                    CHAOXING_ERROR_MESSAGES["chaoxing_sync_failed"],
                ),
                "mock_enabled": self._use_mock,
                "pulled_count": 0,
                "homework_count": 0,
            },
            metadata=metadata,
        )

    async def _terminal_fetch_event(
        self,
        event: Event,
        params: dict[str, Any],
    ) -> Event:
        try:
            data = await self.fetch(params)
        except Exception as exc:
            return self._failed_event(event, _classify_browser_error(exc))
        if data.get("error"):
            return self._failed_event(
                event,
                str(data.get("error_code") or "chaoxing_sync_failed"),
            )
        return self._completed_event(event, data)

    def _error_result(self, error_code: str) -> dict[str, Any]:
        self._last_error_code = error_code
        self._last_error = CHAOXING_ERROR_MESSAGES.get(
            error_code,
            CHAOXING_ERROR_MESSAGES["chaoxing_sync_failed"],
        )
        return {
            "source": self.source_name,
            "error_code": error_code,
            "error": self._last_error,
            "mock_enabled": False,
        }

    async def _batched_fetch(self, event: Event, scope):
        """Background task: fetch with progress, emit completion via EventBus.

        Uses publish_cascade so CONNECTOR_FETCH_COMPLETED → HOMEWORK_NEW
        → StateEngine → derived_state chain completes automatically.
        Retries once with 5s backoff on fetch failure.
        """
        import time as _time
        t_start = _time.monotonic()
        self._fetching = True
        self._fetch_count += 1
        self._last_fetch_at = t_start
        fetch_timeout = 300  # seconds (9 courses × ~15s each + buffer)
        retries = 1 if not self._use_mock else 0  # no retry in mock mode

        for attempt in range(retries + 1):
            try:
                async def on_progress(done, total, items):
                    if self._event_bus:
                        await self._event_bus.publish(Event(
                            event_type=EventType.SYNC_PROGRESS,
                            aggregate_id=event.aggregate_id,
                            aggregate_type=AggregateType.HOMEWORK,
                            causation_id=event.event_id,
                            payload={
                                "source": self.source_name,
                                "progress": f"{done}/{total}",
                                "items_so_far": items,
                            },
                        ))

                params = dict(event.payload)
                params["scope"] = scope
                params["on_progress"] = on_progress
                data = await asyncio.wait_for(self.fetch(params), timeout=fetch_timeout)

                if data.get("error"):
                    error_code = str(data.get("error_code") or "chaoxing_sync_failed")
                    self._session_expired = error_code == "chaoxing_session_expired"
                    self._fetch_failure += 1
                    duration_ms = (_time.monotonic() - t_start) * 1000
                    self._last_fetch_duration_ms = duration_ms
                    self._last_error_code = error_code
                    self._last_error = CHAOXING_ERROR_MESSAGES.get(
                        error_code,
                        CHAOXING_ERROR_MESSAGES["chaoxing_sync_failed"],
                    )
                    self._fetching = False
                    if self._event_bus:
                        await self._event_bus.publish(
                            self._failed_event(event, error_code, duration_ms)
                        )
                    return

                # Success
                self._fetch_success += 1
                self._session_expired = False
                self._last_error_code = ""
                self._last_error = None
                duration_ms = (_time.monotonic() - t_start) * 1000
                self._last_fetch_duration_ms = duration_ms
                # Persist refreshed cookies to disk
                if self._browser:
                    try:
                        await self._browser.save_state()
                    except Exception:
                        pass
                if self._event_bus:
                    completed = self._completed_event(event, data, duration_ms)
                    completed = completed.with_metadata(connector_stats=self.stats)
                    await self._event_bus.publish_cascade(completed)
                return

            except asyncio.TimeoutError:
                duration_ms = (_time.monotonic() - t_start) * 1000
                logger.error("Batched fetch timeout after %.1fs (attempt %d/%d)",
                             fetch_timeout, attempt + 1, retries + 1)
                # Save state before retry (preserves session cookies)
                if self._browser:
                    try:
                        await self._browser.save_state()
                    except Exception:
                        pass
                if attempt < retries:
                    logger.info("[CONNECTOR] retrying after %ds backoff...", 5)
                    await asyncio.sleep(5)
                    continue
                self._fetch_failure += 1
                self._last_error = f"timeout after {fetch_timeout}s"
                self._last_error_code = "chaoxing_browser_unavailable"
                self._last_fetch_duration_ms = duration_ms
                self._fetching = False
                if self._event_bus:
                    await self._event_bus.publish(
                        self._failed_event(
                            event,
                            "chaoxing_browser_unavailable",
                            duration_ms,
                        )
                    )
                return

            except Exception as exc:
                error_code = _classify_browser_error(exc)
                logger.error(
                    "Batched fetch failed attempt=%d/%d error_code=%s error_type=%s",
                    attempt + 1,
                    retries + 1,
                    error_code,
                    type(exc).__name__,
                )
                if attempt < retries:
                    logger.info("[CONNECTOR] retrying after %ds backoff...", 5)
                    await asyncio.sleep(5)
                    continue
                self._fetch_failure += 1
                duration_ms = (_time.monotonic() - t_start) * 1000
                self._last_fetch_duration_ms = duration_ms
                self._last_error_code = error_code
                self._last_error = CHAOXING_ERROR_MESSAGES.get(
                    error_code,
                    CHAOXING_ERROR_MESSAGES["chaoxing_sync_failed"],
                )
                self._fetching = False
                if self._event_bus:
                    await self._event_bus.publish(
                        self._failed_event(event, error_code, duration_ms)
                    )
                return

        self._fetching = False

    # ── Mock data (for testing) ───────────────────────────────────────

    def _mock_data(self, query: str) -> dict[str, Any]:
        if query == "homework_list":
            from datetime import datetime, timezone, timedelta
            # Generate urgent deadline: 6 hours from now
            urgent_dl = (datetime.now(timezone.utc) + timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%SZ")
            return {
                "source": self.source_name,
                "mock_enabled": True,
                "homeworks": [
                    {
                        "id": "hw-001",
                        "course": "高等数学",
                        "title": "第三章习题",
                        "deadline": urgent_dl,
                        "status": "pending",
                    },
                    {
                        "id": "hw-002",
                        "course": "大学英语",
                        "title": "Essay: My Campus Life",
                        "deadline": urgent_dl,
                        "status": "pending",
                    },
                ],
                "total_assignments": 2,
                "pulled_count": 2,
                "homework_count": 2,
            }
        if query == "course_list":
            return {
                "source": self.source_name,
                "mock_enabled": True,
                "courses": [
                    {"id": "c-001", "name": "高等数学", "teacher": "张老师"},
                    {"id": "c-002", "name": "大学英语", "teacher": "李老师"},
                ],
            }
        return {"source": self.source_name, "error": f"unknown query: {query}"}


def _log_bg_task_exception(task: asyncio.Task) -> None:
    """Log background task exceptions without crashing the runtime."""
    try:
        exc = task.exception()
        if exc is not None:
            error_code = _classify_browser_error(exc)
            logger.error(
                "Background task failed error_code=%s error_type=%s",
                error_code,
                type(exc).__name__,
            )
    except (asyncio.CancelledError, asyncio.InvalidStateError):
        pass


def _classify_browser_error(exc: Exception) -> str:
    if isinstance(exc, (ImportError, ModuleNotFoundError)):
        return "chaoxing_playwright_missing"
    if isinstance(exc, FileNotFoundError):
        return "chaoxing_state_file_missing"
    message = str(exc).casefold()
    if "playwright" in message and ("not installed" in message or "no module" in message):
        return "chaoxing_playwright_missing"
    if any(marker in message for marker in (
        "executable doesn't exist",
        "browser has been closed",
        "failed to launch",
        "target page, context or browser has been closed",
    )):
        return "chaoxing_browser_unavailable"
    if "storage state" in message or "authentication" in message:
        return "chaoxing_auth_failed"
    return "chaoxing_sync_failed"
