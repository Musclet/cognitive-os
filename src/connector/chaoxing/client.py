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
from typing import Any

from src.connector.base import Connector
from src.core.events import Event, EventType, AggregateType
from src.connector.chaoxing.browser import ChaoxingBrowser
from src.connector.chaoxing.course_scraper import fetch_course_list
from src.connector.chaoxing.assignment_scraper import fetch_all_assignments

logger = logging.getLogger(__name__)


class ChaoxingConnector(Connector):
    """Chaoxing (学习通) data connector.

    Fetches homework/course data via Playwright browser automation.
    """

    source_name = "chaoxing"

    def __init__(
        self,
        use_mock: bool = False,
        state_file: str = "data/chaoxing_state.json",
        headless: bool = True,
        event_bus=None,
        course_registry=None,
    ) -> None:
        self._use_mock = use_mock
        self._state_file = state_file
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
            "fetching": self._fetching,
            "authenticated": self._authenticated,
        }

    async def authenticate(self) -> bool:
        """Validate that the state file exists (auth is via stored state)."""
        if self._use_mock:
            self._authenticated = True
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
            return False

        self._authenticated = True
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
        except Exception as e:
            logger.warning("[SESSION] keepalive error: %s", e)
            return False

    async def fetch(self, params: dict[str, Any]) -> dict[str, Any]:
        """Fetch raw data from Chaoxing.

        params:
            query: "homework_list" | "course_list"
        """
        if self._use_mock:
            return self._mock_data(params.get("query", ""))

        if not self._browser:
            return {"source": self.source_name, "error": "browser not initialized"}

        # Ensure browser is started (idempotent) before session check
        await self._browser.start()

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
                    "error": "session expired — re-login required",
                    "session_expired": True,
                }
        else:
            return {
                "source": self.source_name,
                "error": "session expired — re-login required",
                "session_expired": True,
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
            return {"source": self.source_name, "homeworks": [], "courses": []}

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
            "errors": len(errors),
        }

    # -- EventBus handler --

    async def handle_fetch_request(self, event: Event) -> list[Event]:
        """EventBus handler: spawn background fetch, return SYNC_STARTED immediately.

        Background task emits progress + completion via EventBus.
        """
        if event.payload.get("source") != self.source_name:
            return []

        if self._use_mock and self._event_bus is None:
            data = await self.fetch(event.payload)
            return [Event(
                event_type=EventType.CONNECTOR_FETCH_COMPLETED,
                aggregate_id=event.aggregate_id,
                aggregate_type=AggregateType.HOMEWORK,
                causation_id=event.event_id,
                payload=data,
            )]

        if not self._authenticated:
            ok = await self.authenticate()
            if not ok:
                return [Event(
                    event_type=EventType.CONNECTOR_FETCH_FAILED,
                    aggregate_id=event.aggregate_id,
                    aggregate_type=AggregateType.HOMEWORK,
                    causation_id=event.event_id,
                    payload={"source": self.source_name, "error": "auth failed"},
                )]

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

        return [Event(
            event_type=EventType.SYNC_STARTED,
            aggregate_id=event.aggregate_id,
            aggregate_type=AggregateType.HOMEWORK,
            causation_id=event.event_id,
            payload={"source": self.source_name, "course_count": course_count},
        )]

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

                # Check for session expiry in response
                if data.get("session_expired"):
                    self._session_expired = True
                    self._fetch_failure += 1
                    duration_ms = (_time.monotonic() - t_start) * 1000
                    self._last_fetch_duration_ms = duration_ms
                    self._last_error = "session expired"
                    self._fetching = False
                    if self._event_bus:
                        await self._event_bus.publish(Event(
                            event_type=EventType.CONNECTOR_FETCH_FAILED,
                            aggregate_id=event.aggregate_id,
                            aggregate_type=AggregateType.HOMEWORK,
                            causation_id=event.event_id,
                            payload={
                                "source": self.source_name,
                                "error": "session expired — run login_and_save_state()",
                            },
                            metadata={
                                "trace_id": str(event.event_id),
                                "processing_duration_ms": round(duration_ms, 1),
                            },
                        ))
                    return

                # Success
                self._fetch_success += 1
                self._session_expired = False
                duration_ms = (_time.monotonic() - t_start) * 1000
                self._last_fetch_duration_ms = duration_ms
                # Persist refreshed cookies to disk
                if self._browser:
                    try:
                        await self._browser.save_state()
                    except Exception:
                        pass
                if self._event_bus:
                    await self._event_bus.publish_cascade(Event(
                        event_type=EventType.CONNECTOR_FETCH_COMPLETED,
                        aggregate_id=event.aggregate_id,
                        aggregate_type=AggregateType.HOMEWORK,
                        causation_id=event.event_id,
                        payload=data,
                        metadata={
                            "trace_id": str(event.event_id),
                            "processing_duration_ms": round(duration_ms, 1),
                            "connector_stats": self.stats,
                        },
                    ))
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
                self._last_fetch_duration_ms = duration_ms
                self._fetching = False
                if self._event_bus:
                    await self._event_bus.publish(Event(
                        event_type=EventType.CONNECTOR_FETCH_FAILED,
                        aggregate_id=event.aggregate_id,
                        aggregate_type=AggregateType.HOMEWORK,
                        causation_id=event.event_id,
                        payload={"source": self.source_name, "error": f"timeout after {fetch_timeout}s"},
                        metadata={
                            "trace_id": str(event.event_id),
                            "processing_duration_ms": round(duration_ms, 1),
                        },
                    ))
                return

            except Exception as exc:
                logger.exception("Batched fetch failed (attempt %d/%d)", attempt + 1, retries + 1)
                if attempt < retries:
                    logger.info("[CONNECTOR] retrying after %ds backoff...", 5)
                    await asyncio.sleep(5)
                    continue
                self._fetch_failure += 1
                duration_ms = (_time.monotonic() - t_start) * 1000
                self._last_fetch_duration_ms = duration_ms
                self._last_error = str(exc)
                self._fetching = False
                if self._event_bus:
                    await self._event_bus.publish(Event(
                        event_type=EventType.CONNECTOR_FETCH_FAILED,
                        aggregate_id=event.aggregate_id,
                        aggregate_type=AggregateType.HOMEWORK,
                        causation_id=event.event_id,
                        payload={"source": self.source_name, "error": str(exc)},
                        metadata={
                            "trace_id": str(event.event_id),
                            "processing_duration_ms": round(duration_ms, 1),
                        },
                    ))
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
            }
        if query == "course_list":
            return {
                "source": self.source_name,
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
            logger.error("Background task failed: %s", exc, exc_info=exc)
    except (asyncio.CancelledError, asyncio.InvalidStateError):
        pass
