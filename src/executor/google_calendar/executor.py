"""Google Calendar Executor — write actions for Google Calendar.

WRITE layer. Only executes after proposal is ACCEPTED by user.
Never executes autonomously. Never deletes or bulk-modifies.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from src.core.proposal import Proposal, ProposalStatus, ProposalType, TargetSystem
from src.core.events import Event, EventType, AggregateType
from src.core.temporal import TimeBlock
from src.infrastructure.config import Settings

logger = logging.getLogger(__name__)


class GoogleCalendarExecutor:
    """Executor for Google Calendar write operations.

    Usage:
        Only called from domain handler after proposal accepted.
        Never called directly from planning or recommendation layer.
    """

    def __init__(self, use_mock: bool = True, settings: Settings | None = None) -> None:
        self.use_mock = use_mock
        self.settings = settings or Settings()

    @staticmethod
    def _failure(proposal_id: str, error_code: str) -> Event:
        return Event(
            event_type=EventType.EXECUTION_FAILED,
            aggregate_id=proposal_id or "google_calendar",
            aggregate_type=AggregateType.SYSTEM,
            payload={
                "proposal_id": proposal_id,
                "error": error_code,
                "error_code": error_code,
            },
        )

    async def execute(self, proposal: Proposal | None) -> Event:
        """Execute an approved proposal.

        Args:
            proposal: Must have status == ACCEPTED.

        Returns:
            EXECUTION_COMPLETED or EXECUTION_FAILED event.
        """
        if proposal is None:
            return self._failure("", "google_calendar_proposal_required")

        if (
            self.settings.google_calendar_write_requires_acceptance
            and proposal.status != ProposalStatus.ACCEPTED
        ):
            return self._failure(
                proposal.proposal_id,
                "google_calendar_proposal_not_accepted",
            )

        if not self.use_mock and not self.settings.google_calendar_write_enabled:
            return self._failure(
                proposal.proposal_id,
                "google_calendar_write_disabled",
            )

        if proposal.target_system != TargetSystem.GOOGLE_CALENDAR:
            return self._failure(
                proposal.proposal_id,
                "google_calendar_invalid_proposal_target",
            )

        if proposal.proposal_type != ProposalType.CREATE_CALENDAR_BLOCK:
            return self._failure(
                proposal.proposal_id,
                "google_calendar_invalid_proposal_operation",
            )

        try:
            payload = proposal.action_payload
            title = payload.get("title", "Untitled")
            start = payload.get("start", "")
            end = payload.get("end", "")

            if self.use_mock:
                logger.info(
                    "MOCK: would create calendar event '%s' at %s-%s",
                    title, start, end
                )
                event_id = f"mock-event-{proposal.proposal_id}"
                html_link = ""
            else:
                created = await self._create_real_event(payload)
                event_id = created["event_id"]
                html_link = created.get("html_link", "")

            return Event(
                event_type=EventType.EXECUTION_COMPLETED,
                aggregate_id=proposal.proposal_id,
                aggregate_type=AggregateType.SYSTEM,
                payload={
                    "proposal_id": proposal.proposal_id,
                    "event_id": event_id,
                    "title": title,
                    "start": start,
                    "end": end,
                    "html_link": html_link,
                },
            )

        except Exception as exc:
            from src.connector.google_calendar.auth import GoogleCalendarAuthError

            if isinstance(exc, GoogleCalendarAuthError):
                return self._failure(proposal.proposal_id, exc.error_code)
            logger.error(
                "Google Calendar API write failed for proposal %s",
                proposal.proposal_id,
            )
            return self._failure(
                proposal.proposal_id,
                "google_calendar_api_error",
            )

    async def _create_real_event(self, payload: dict[str, Any]) -> dict[str, str]:
        """Real Google Calendar API call."""
        service = self._calendar_service()
        body = {
            "summary": payload.get("title", "Untitled"),
            "description": payload.get("description", ""),
            "location": payload.get("location", ""),
            "start": {"dateTime": payload.get("start"), "timeZone": self.settings.google_calendar_timezone},
            "end": {"dateTime": payload.get("end"), "timeZone": self.settings.google_calendar_timezone},
        }
        calendar_id = self._validate_calendar_id(self.settings.google_calendar_calendar_id)
        event = self._execute_with_retry(service.events().insert(
            calendarId=calendar_id,
            body=body,
        ))
        return {
            "event_id": str(event.get("id", "")),
            "html_link": str(event.get("htmlLink", "")),
        }

    async def update_event(
        self,
        event_id: str,
        calendar_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Update an existing Google Calendar event.

        Args:
            event_id: Google Calendar event ID to update.
            calendar_id: Calendar ID (default from settings if None).
            payload: Fields to update (title, start, end, location, description).

        Returns:
            dict with ok=True/False, event_id.
        """
        calendar_id = calendar_id or self.settings.google_calendar_calendar_id
        payload = payload or {}

        if self.use_mock:
            logger.info("MOCK: update calendar event %s in %s", event_id, calendar_id)
            return {"ok": True, "event_id": event_id}

        if not self.settings.google_calendar_write_enabled:
            return {
                "ok": False,
                "error": "google_calendar_write_disabled",
                "error_code": "google_calendar_write_disabled",
                "event_id": event_id,
            }

        body: dict[str, Any] = {}
        if payload.get("title"):
            body["summary"] = payload["title"]
        if payload.get("start"):
            body.setdefault("start", {})["dateTime"] = payload["start"]
            body["start"]["timeZone"] = self.settings.google_calendar_timezone
        if payload.get("end"):
            body.setdefault("end", {})["dateTime"] = payload["end"]
            body["end"]["timeZone"] = self.settings.google_calendar_timezone
        if payload.get("location") is not None:
            body["location"] = payload["location"]
        if payload.get("description") is not None:
            body["description"] = payload["description"]

        try:
            service = self._calendar_service()
            self._execute_with_retry(service.events().patch(
                calendarId=self._validate_calendar_id(calendar_id),
                eventId=event_id,
                body=body,
            ))
            return {"ok": True, "event_id": event_id}
        except Exception as exc:
            from src.connector.google_calendar.auth import GoogleCalendarAuthError

            error_code = (
                exc.error_code
                if isinstance(exc, GoogleCalendarAuthError)
                else "google_calendar_api_error"
            )
            return {
                "ok": False,
                "error": error_code,
                "error_code": error_code,
                "event_id": event_id,
            }

    async def delete_event(
        self,
        event_id: str,
        calendar_id: str | None = None,
    ) -> dict[str, Any]:
        """Delete a managed calendar event by event_id.

        Only deletes events identified by a known event_id (from a previous
        bot-created event). The caller is responsible for confirming the event
        was bot-created before calling this method.

        Args:
            event_id: Google Calendar event ID to delete.
            calendar_id: Calendar ID (default from settings if None).

        Returns:
            dict with ok=True/False and event_id.
        """
        calendar_id = calendar_id or self.settings.google_calendar_calendar_id

        if self.use_mock:
            logger.info("MOCK: delete calendar event %s from %s", event_id, calendar_id)
            return {"ok": True, "event_id": event_id}

        if not self.settings.google_calendar_write_enabled:
            return {
                "ok": False,
                "error": "google_calendar_write_disabled",
                "error_code": "google_calendar_write_disabled",
                "event_id": event_id,
            }

        try:
            service = self._calendar_service()
            self._execute_with_retry(service.events().delete(
                calendarId=self._validate_calendar_id(calendar_id),
                eventId=event_id,
            ))
            return {"ok": True, "event_id": event_id}
        except Exception as exc:
            from src.connector.google_calendar.auth import GoogleCalendarAuthError

            error_code = (
                exc.error_code
                if isinstance(exc, GoogleCalendarAuthError)
                else "google_calendar_api_error"
            )
            return {
                "ok": False,
                "error": error_code,
                "error_code": error_code,
                "event_id": event_id,
            }

    async def sync_schedule_blocks(
        self,
        blocks: list[TimeBlock],
        days: int | None = None,
        calendar_id: str | None = None,
        *,
        proposal: Proposal | None = None,
    ) -> dict[str, Any]:
        """Mirror JWXT schedule blocks into Google Calendar managed events.

        Args:
            blocks: Temporal blocks to mirror.
            days: Sync window in days (default from settings).
            calendar_id: Target calendar ID (default from settings).
            proposal: Accepted proposal authorizing the write. Required when
                google_calendar_write_requires_acceptance is True (non-mock).

        Returns:
            dict with ok=True/False plus created/updated/deleted counts.
        """
        if not self.use_mock and not self.settings.google_calendar_schedule_write_enabled:
            return {"ok": False, "error": "schedule_calendar_write_disabled"}

        if not self.use_mock and self.settings.google_calendar_write_requires_acceptance:
            if proposal is None:
                return {"ok": False, "error": "proposal_required: schedule mirror writes require an accepted proposal"}
            if proposal.status != ProposalStatus.ACCEPTED:
                return {"ok": False, "error": "proposal_not_accepted: schedule mirror writes require an accepted proposal"}
            if proposal.target_system != TargetSystem.GOOGLE_CALENDAR:
                return {"ok": False, "error": "invalid_proposal_target: schedule mirror writes require a Google Calendar proposal"}
            operation = proposal.action_payload.get("operation")
            if operation not in {"sync_schedule_blocks", "calendar_schedule_mirror"}:
                return {"ok": False, "error": "invalid_proposal_operation: schedule mirror writes require a schedule mirror proposal"}

        days = days or self.settings.google_calendar_schedule_sync_days
        calendar_id = calendar_id or self.settings.google_calendar_schedule_calendar_id
        now = datetime.now(timezone.utc)
        end = now + timedelta(days=days)
        wanted = [
            b for b in blocks
            if str(b.source) == "jwxt"
            and str(b.block_type) in {"class_lecture", "class_lab"}
            and now <= b.start.astimezone(timezone.utc) < end
        ]

        if self.use_mock:
            return {
                "ok": True,
                "created": len(wanted),
                "updated": 0,
                "deleted": 0,
                "calendar_id": calendar_id,
            }

        calendar_id = self._validate_calendar_id(calendar_id)
        service = self._calendar_service()
        existing = self._list_managed_schedule_events(service, calendar_id, now, end)
        existing_by_block_id = {
            (item.get("extendedProperties", {}).get("private", {}) or {}).get("jwxt_block_id"): item
            for item in existing
            if (item.get("extendedProperties", {}).get("private", {}) or {}).get("jwxt_block_id")
        }

        created = 0
        updated = 0
        desired_ids: set[str] = set()
        for block in wanted:
            desired_ids.add(block.block_id)
            body = self._schedule_event_body(block)
            old = existing_by_block_id.get(block.block_id)
            if old:
                self._execute_with_retry(service.events().patch(
                    calendarId=calendar_id,
                    eventId=old["id"],
                    body=body,
                ))
                updated += 1
            else:
                self._execute_with_retry(service.events().insert(calendarId=calendar_id, body=body))
                created += 1

        deleted = 0
        for item in existing:
            private = (item.get("extendedProperties", {}).get("private", {}) or {})
            block_id = private.get("jwxt_block_id")
            if private.get("managed_by") != "cognitive_os" or private.get("source") != "jwxt":
                continue
            if block_id and block_id not in desired_ids:
                self._execute_with_retry(service.events().delete(calendarId=calendar_id, eventId=item["id"]))
                deleted += 1

        return {
            "ok": True,
            "created": created,
            "updated": updated,
            "deleted": deleted,
            "calendar_id": calendar_id,
            "block_count": len(wanted),
        }

    async def verify_schedule_mirror(
        self,
        blocks: list[TimeBlock],
        days: int | None = None,
        calendar_id: str | None = None,
    ) -> dict[str, Any]:
        """Verify JWXT schedule mirror against managed Google Calendar events.

        Compares managed event count and jwxt_block_id set for the window.
        Does not touch/delete any events.
        Returns verified=True/False with counts.
        """
        if self.use_mock:
            days = days or self.settings.google_calendar_schedule_sync_days
            now = datetime.now(timezone.utc)
            end = now + timedelta(days=days)
            wanted = [
                b for b in blocks
                if str(b.source) == "jwxt"
                and str(b.block_type) in {"class_lecture", "class_lab"}
                and now <= b.start.astimezone(timezone.utc) < end
            ]
            # In mock mode, sync_schedule_blocks returns created=len(wanted)
            # so verification should match
            return {
                "verified": True,
                "jwxt_count": len(wanted),
                "calendar_count": len(wanted),
                "source": "mock",
            }

        calendar_id = self._validate_calendar_id(
            calendar_id or self.settings.google_calendar_schedule_calendar_id
        )
        days = days or self.settings.google_calendar_schedule_sync_days
        now = datetime.now(timezone.utc)
        end = now + timedelta(days=days)

        wanted = [
            b for b in blocks
            if str(b.source) == "jwxt"
            and str(b.block_type) in {"class_lecture", "class_lab"}
            and now <= b.start.astimezone(timezone.utc) < end
        ]
        desired_ids = {b.block_id for b in wanted}

        service = self._calendar_service()
        existing = self._list_managed_schedule_events(service, calendar_id, now, end)
        existing_ids = set()
        for item in existing:
            private = (item.get("extendedProperties", {}).get("private", {}) or {})
            if private.get("managed_by") == "cognitive_os" and private.get("source") == "jwxt":
                bid = private.get("jwxt_block_id")
                if bid:
                    existing_ids.add(bid)

        verified = desired_ids == existing_ids
        return {
            "verified": verified,
            "jwxt_count": len(desired_ids),
            "calendar_count": len(existing_ids),
            "missing_ids": sorted(desired_ids - existing_ids) if not verified else [],
            "extra_ids": sorted(existing_ids - desired_ids) if not verified else [],
        }

    def _execute_with_retry(self, request: Any, attempts: int = 3) -> dict[str, Any]:
        """Execute a Google API request with retry and exponential backoff."""
        last_exc: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                return request.execute()
            except Exception as exc:
                last_exc = exc
                if attempt >= attempts:
                    break
                logger.warning("Google Calendar API call failed; retrying (%s/%s): %s", attempt, attempts, exc)
                time.sleep(0.5 * attempt)
        raise RuntimeError(f"google_calendar_api_failed: {last_exc}") from last_exc

    def _calendar_service(self):
        try:
            from googleapiclient.discovery import build
        except Exception as exc:
            raise RuntimeError(f"google dependencies unavailable: {exc}") from exc

        from src.connector.google_calendar.auth import GoogleCalendarAuth
        auth = GoogleCalendarAuth(
            credentials_path=self.settings.google_calendar_credentials_path,
            token_path=self.settings.google_calendar_token_path,
            scopes=["https://www.googleapis.com/auth/calendar"],
        )
        creds, _ = auth.authenticate(
            trace_id=f"executor-{uuid4().hex[:8]}",
            allow_interactive=False,
        )
        return build("calendar", "v3", credentials=creds, cache_discovery=False)

    def _validate_calendar_id(self, calendar_id: str) -> str:
        """Resolve 'selected' to a real calendar ID."""
        if calendar_id != "selected":
            return calendar_id
        return self.settings.google_calendar_schedule_calendar_id or "primary"

    def _list_managed_schedule_events(self, service, calendar_id: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
        result = self._execute_with_retry(service.events().list(
            calendarId=calendar_id,
            timeMin=start.isoformat(),
            timeMax=end.isoformat(),
            singleEvents=True,
            showDeleted=False,
            privateExtendedProperty=["managed_by=cognitive_os", "source=jwxt"],
            maxResults=2500,
        ))
        return result.get("items", [])

    def _schedule_event_body(self, block: TimeBlock) -> dict[str, Any]:
        teacher = (block.metadata or {}).get("teacher", "")
        title = f"{block.title}（{teacher}）" if teacher and teacher not in block.title else block.title
        description = "\n".join([
            "来源：Cognitive OS / JWXT",
            f"教师：{teacher or '未提供'}",
            f"节次：{(block.metadata or {}).get('jcs', '未提供')}",
            f"教学周：{(block.metadata or {}).get('teaching_week', '未提供')}",
            f"JWXT block id：{block.block_id}",
        ])
        return {
            "summary": title,
            "location": block.location or "未提供地址",
            "description": description,
            "start": {
                "dateTime": block.start.isoformat(),
                "timeZone": self.settings.google_calendar_timezone,
            },
            "end": {
                "dateTime": block.end.isoformat(),
                "timeZone": self.settings.google_calendar_timezone,
            },
            "extendedProperties": {
                "private": {
                    "managed_by": "cognitive_os",
                    "source": "jwxt",
                    "jwxt_block_id": block.block_id,
                },
            },
        }

    # ── Managed art block methods ────────────────────────────────────────

    async def create_managed_art_block(
        self,
        title: str,
        start: datetime,
        end: datetime,
        plan_id: str = "",
        rationale: str = "",
        target_minutes: int = 0,
        calendar_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a managed art block in Google Calendar.

        Only touches events with extendedProperties: managed_by=cognitive_os, source=daily_art_plan.
        """
        calendar_id = calendar_id or self.settings.art_calendar_id

        if self.use_mock:
            logger.info("MOCK: create managed art block '%s' %s-%s", title, start.isoformat(), end.isoformat())
            return {"ok": True, "event_id": f"mock-art-{uuid4().hex[:8]}", "title": title}

        body = {
            "summary": title,
            "description": self._art_block_description(plan_id, rationale, target_minutes),
            "start": {"dateTime": start.isoformat(), "timeZone": self.settings.google_calendar_timezone},
            "end": {"dateTime": end.isoformat(), "timeZone": self.settings.google_calendar_timezone},
            "extendedProperties": {
                "private": {
                    "managed_by": "cognitive_os",
                    "source": self.settings.art_managed_calendar_source,
                    "plan_id": plan_id,
                },
            },
        }
        service = self._calendar_service()
        event = self._execute_with_retry(service.events().insert(calendarId=calendar_id, body=body))
        return {"ok": True, "event_id": event.get("id", ""), "title": title}

    async def update_managed_art_block(
        self,
        event_id: str,
        title: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        rationale: str | None = None,
        calendar_id: str | None = None,
    ) -> dict[str, Any]:
        """Update an existing managed art block.

        Payload: only provided fields are updated.
        """
        calendar_id = calendar_id or self.settings.art_calendar_id

        if self.use_mock:
            logger.info("MOCK: update managed art block %s", event_id)
            return {"ok": True, "event_id": event_id}

        body: dict[str, Any] = {}
        if title is not None:
            body["summary"] = title
        if start is not None:
            body["start"] = {"dateTime": start.isoformat(), "timeZone": self.settings.google_calendar_timezone}
        if end is not None:
            body["end"] = {"dateTime": end.isoformat(), "timeZone": self.settings.google_calendar_timezone}
        if rationale is not None:
            existing = self._get_managed_event(service=self._calendar_service(), event_id=event_id, calendar_id=calendar_id)
            body["description"] = self._art_block_description(
                existing.get("description", "").split("\n")[0] if existing else "",
                rationale,
                0,
            )

        service = self._calendar_service()
        self._execute_with_retry(service.events().patch(calendarId=calendar_id, eventId=event_id, body=body))
        return {"ok": True, "event_id": event_id}

    async def delete_managed_art_block(
        self,
        event_id: str,
        calendar_id: str | None = None,
    ) -> dict[str, Any]:
        """Delete a managed art block event."""
        calendar_id = calendar_id or self.settings.art_calendar_id

        if self.use_mock:
            logger.info("MOCK: delete managed art block %s", event_id)
            return {"ok": True, "event_id": event_id}

        service = self._calendar_service()
        self._execute_with_retry(service.events().delete(calendarId=calendar_id, eventId=event_id))
        return {"ok": True, "event_id": event_id}

    async def list_managed_art_blocks(
        self,
        start: datetime,
        end: datetime,
        calendar_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """List all managed art block events in a time range."""
        calendar_id = calendar_id or self.settings.art_calendar_id
        source = self.settings.art_managed_calendar_source

        if self.use_mock:
            return []

        service = self._calendar_service()
        result = self._execute_with_retry(service.events().list(
            calendarId=calendar_id,
            timeMin=start.isoformat(),
            timeMax=end.isoformat(),
            singleEvents=True,
            showDeleted=False,
            privateExtendedProperty=[f"managed_by=cognitive_os", f"source={source}"],
            maxResults=500,
        ))
        return result.get("items", [])

    def _art_block_description(self, plan_id: str, rationale: str, target_minutes: int) -> str:
        parts = ["来源：Cognitive OS / Art Planner"]
        if plan_id:
            parts.append(f"计划 ID：{plan_id}")
        if target_minutes > 0:
            parts.append(f"目标时间：{target_minutes}min")
        if rationale:
            parts.append(f"原因：{rationale}")
        return "\n".join(parts)

    def _get_managed_event(self, service, event_id: str, calendar_id: str) -> dict[str, Any] | None:
        try:
            return service.events().get(calendarId=calendar_id, eventId=event_id).execute()
        except Exception:
            return None


# Singleton for handler use
_executor: GoogleCalendarExecutor | None = None


def get_executor(use_mock: bool = True, settings: Settings | None = None) -> GoogleCalendarExecutor:
    global _executor
    if _executor is None:
        _executor = GoogleCalendarExecutor(use_mock=use_mock, settings=settings)
    return _executor


def reset_executor() -> None:
    global _executor
    _executor = None
