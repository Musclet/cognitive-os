"""Google Calendar connector — temporal read integration."""

from __future__ import annotations

import hashlib
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from src.connector.base import Connector
from src.connector.google_calendar.auth import GoogleCalendarAuth
from src.core.events import AggregateType, Event, EventType
from src.core.temporal import TemporalSource, TimeBlock, TimeBlockType
from src.infrastructure.config import Settings

logger = logging.getLogger(__name__)


class GoogleCalendarConnector(Connector):
    source_name = "google_calendar"

    def __init__(self, use_mock: bool | None = None, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()
        if use_mock is not None:
            self.settings.google_calendar_mock = bool(use_mock)
        self._authenticated = False
        self._creds = None

    async def authenticate(self) -> bool:
        if self.settings.google_calendar_mock:
            self._authenticated = True
            return True
        if self._authenticated:
            return True
        auth = GoogleCalendarAuth(
            credentials_path=self.settings.google_calendar_credentials_path,
            token_path=self.settings.google_calendar_token_path,
            scopes=["https://www.googleapis.com/auth/calendar"],
        )
        self._creds, _ = auth.authenticate(trace_id=f"auth-{uuid4().hex[:8]}")
        self._authenticated = True
        return True

    async def fetch(self, params: dict[str, Any]) -> dict[str, Any]:
        data, _ = await self.fetch_with_auth_events(params)
        return data

    async def fetch_with_auth_events(self, params: dict[str, Any]) -> tuple[dict[str, Any], list[Event]]:
        trace_id = str(params.get("trace_id", f"calendar-{uuid4().hex[:8]}"))
        auth_events: list[Event] = []
        if self.settings.google_calendar_mock:
            return self._mock_events(), auth_events

        auth = GoogleCalendarAuth(
            credentials_path=self.settings.google_calendar_credentials_path,
            token_path=self.settings.google_calendar_token_path,
            scopes=["https://www.googleapis.com/auth/calendar"],
        )
        self._creds, auth_events = auth.authenticate(trace_id=trace_id)
        self._authenticated = True

        try:
            from googleapiclient.discovery import build
        except Exception as exc:
            raise RuntimeError(f"google api client unavailable: {exc}") from exc

        service = build("calendar", "v3", credentials=self._creds, cache_discovery=False)
        now = datetime.now(timezone.utc)
        window_days = int(params.get("sync_window_days", self.settings.google_calendar_sync_window_days))
        time_min = now.isoformat()
        time_max = (now + timedelta(days=window_days)).isoformat()
        calendar_id = str(params.get("calendar_id", self.settings.google_calendar_calendar_id))
        calendars = _resolve_calendars(service, calendar_id)
        blocks: list[dict[str, Any]] = []
        raw_count = 0
        for calendar in calendars:
            result = _execute_google_request(service.events().list(
                calendarId=calendar["id"],
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                orderBy="startTime",
                showDeleted=True,
            ))
            items = result.get("items", [])
            raw_count += len(items)
            for item in items:
                mapped = _map_google_event(item, calendar["id"], calendar.get("summary", ""))
                if mapped:
                    blocks.append(mapped)

        return {
            "source": self.source_name,
            "calendar_id": calendar_id,
            "calendar_count": len(calendars),
            "calendars": calendars,
            "blocks": blocks,
            "count": len(blocks),
            "raw_count": raw_count,
            "dedup_count": len({b["metadata"]["dedup_key"] for b in blocks if b.get("metadata", {}).get("dedup_key")}),
        }, auth_events

    def _mock_events(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        blocks = [
            TimeBlock(
                block_id=str(uuid4()),
                source=TemporalSource.GOOGLE_CALENDAR,
                block_type=TimeBlockType.SOCIAL_BLOCK,
                start=now + timedelta(hours=3),
                end=now + timedelta(hours=5),
                title="聚餐",
                metadata={"external_source": "google_calendar", "calendar_id": "primary"},
            ).to_dict(),
            TimeBlock(
                block_id=str(uuid4()),
                source=TemporalSource.GOOGLE_CALENDAR,
                block_type=TimeBlockType.WORKOUT_BLOCK,
                start=now + timedelta(hours=7),
                end=now + timedelta(hours=8),
                title="Gym",
                metadata={"external_source": "google_calendar", "calendar_id": "primary"},
            ).to_dict(),
            TimeBlock(
                block_id=str(uuid4()),
                source=TemporalSource.GOOGLE_CALENDAR,
                block_type=TimeBlockType.MEETING_BLOCK,
                start=now + timedelta(hours=10),
                end=now + timedelta(hours=11),
                title="Project meeting",
                metadata={"external_source": "google_calendar", "calendar_id": "primary"},
            ).to_dict(),
        ]
        return {
            "source": self.source_name,
            "calendar_id": "primary",
            "calendar_count": 1,
            "calendars": [{"id": "primary", "summary": "primary"}],
            "blocks": blocks,
            "count": len(blocks),
            "raw_count": len(blocks),
            "dedup_count": len(blocks),
        }

    async def handle_fetch_request(self, event: Event) -> list[Event]:
        if event.payload.get("source") != self.source_name:
            return []

        started = time.monotonic()
        trace_id = str(event.event_id)
        calendar_id = str(event.payload.get("calendar_id", self.settings.google_calendar_calendar_id))
        out: list[Event] = [
            Event(
                event_type=EventType.CONNECTOR_FETCH_STARTED,
                aggregate_id=event.aggregate_id,
                aggregate_type=AggregateType.SYSTEM,
                causation_id=event.event_id,
                payload={"source": self.source_name, "calendar_id": calendar_id},
                metadata={"trace_id": trace_id, "source": self.source_name, "calendar_id": calendar_id},
            )
        ]

        try:
            data, auth_events = await self.fetch_with_auth_events({**event.payload, "trace_id": trace_id})
            out.extend(auth_events)

            for block in data.get("blocks", []):
                status = block.get("metadata", {}).get("status", "")
                event_type = EventType.TEMPORAL_BLOCK_ADDED
                if status == "cancelled":
                    event_type = EventType.TEMPORAL_BLOCK_CANCELLED
                elif block.get("metadata", {}).get("updated_existing"):
                    event_type = EventType.TEMPORAL_BLOCK_UPDATED

                out.append(Event(
                    event_type=event_type,
                    aggregate_id=block["block_id"],
                    aggregate_type=AggregateType.TEMPORAL,
                    causation_id=event.event_id,
                    payload=block,
                    metadata={
                        "trace_id": trace_id,
                        "source": self.source_name,
                        "calendar_id": data.get("calendar_id", calendar_id),
                        "dedup_key": block.get("metadata", {}).get("dedup_key", ""),
                    },
                ))

            out.append(Event(
                event_type=EventType.CONNECTOR_FETCH_COMPLETED,
                aggregate_id=event.aggregate_id,
                aggregate_type=AggregateType.SYSTEM,
                causation_id=event.event_id,
                payload=data,
                metadata={
                    "trace_id": trace_id,
                    "source": self.source_name,
                    "calendar_id": data.get("calendar_id", calendar_id),
                    "calendar_count": data.get("calendar_count", 1),
                    "calendars": data.get("calendars", []),
                    "duration_ms": round((time.monotonic() - started) * 1000, 1),
                    "item_count": data.get("count", 0),
                    "dedup_count": data.get("dedup_count", 0),
                    "error_code": "",
                },
            ))
            return out
        except Exception as exc:
            out.append(Event(
                event_type=EventType.CONNECTOR_FETCH_FAILED,
                aggregate_id=event.aggregate_id,
                aggregate_type=AggregateType.SYSTEM,
                causation_id=event.event_id,
                payload={"source": self.source_name, "error": str(exc), "calendar_id": calendar_id},
                metadata={
                    "trace_id": trace_id,
                    "source": self.source_name,
                    "calendar_id": calendar_id,
                    "duration_ms": round((time.monotonic() - started) * 1000, 1),
                    "item_count": 0,
                    "dedup_count": 0,
                    "error_code": "fetch_failed",
                },
            ))
            return out

    async def probe_live_connection(self) -> dict[str, Any]:
        """Lightweight connectivity check against the real Google API.

        Never throws — every error path returns a structured dict.
        Uses a 3-second timeout so a slow network cannot block the caller.
        """
        if self.settings.google_calendar_mock:
            return {"status": "SKIPPED", "reason": "MOCK_ENABLED"}

        import os as _os
        creds_env = bool(str(getattr(self.settings, "google_calendar_credentials_json", "")))
        token_env = bool(str(getattr(self.settings, "google_calendar_token_json", "")))
        creds_file = _os.path.isfile(str(self.settings.google_calendar_credentials_path))
        token_file = _os.path.isfile(str(self.settings.google_calendar_token_path))
        if not (creds_env or creds_file) and not (token_env or token_file):
            return {"status": "FAIL", "reason": "ERR_NO_CREDENTIALS"}

        try:
            ok = await self.authenticate()
            if not ok or not self._creds:
                return {"status": "FAIL", "reason": "ERR_AUTH_FAILED"}
        except Exception as exc:
            return {"status": "FAIL", "reason": f"ERR_AUTH_EXCEPTION: {exc}"}

        try:
            from googleapiclient.discovery import build as _build
            import asyncio as _asyncio
            service = _build("calendar", "v3", credentials=self._creds, cache_discovery=False)
            result = await _asyncio.wait_for(
                _asyncio.to_thread(
                    lambda: service.calendarList().list(maxResults=1, fields="items(id,summary)").execute(),
                ),
                timeout=3.0,
            )
            items = result.get("items", [])
            cal_id = items[0].get("id", "?") if items else "none"
            return {"status": "PASS", "calendar_count": len(items), "sample_calendar_id": cal_id}
        except _asyncio.TimeoutError:
            return {"status": "FAIL", "reason": "ERR_TIMEOUT_3S"}
        except Exception as exc:
            msg = str(exc)
            if "invalid_grant" in msg.lower():
                return {"status": "FAIL", "reason": "ERR_INVALID_GRANT"}
            if "insufficient" in msg.lower() or "permission" in msg.lower():
                return {"status": "FAIL", "reason": "ERR_INSUFFICIENT_PERMISSIONS"}
            if "not found" in msg.lower():
                return {"status": "FAIL", "reason": "ERR_CALENDAR_NOT_FOUND"}
            return {"status": "FAIL", "reason": f"ERR_API: {msg[:120]}"}

    async def execute_real_readonly_sync(self, causation_id: str = "", trace_id: str = "") -> tuple[dict[str, Any], list[Event]]:
        """Run a probe-gated real read-only sync against Google Calendar.

        Returns (result_dict, produced_events).  If the probe fails the
        result dict carries ``"status": "DEGRADED"`` so callers can
        return 200 with a clear message instead of crashing.
        """
        probe = await self.probe_live_connection()
        if probe["status"] != "PASS":
            return {
                "ok": True,
                "status": "DEGRADED",
                "message": f"Real sync blocked — probe {probe['status']}: {probe.get('reason','')}",
                "count": 0,
                "probe": probe,
            }, []

        # Probe passed — run the normal fetch pipeline
        params: dict[str, Any] = {
            "source": self.source_name,
            "query": "calendar_events",
            "intent": "real_readonly_sync",
            "calendar_id": self.settings.google_calendar_calendar_id,
            "sync_window_days": self.settings.google_calendar_sync_window_days,
        }
        data, auth_events = await self.fetch_with_auth_events(params)

        produced: list[Event] = list(auth_events)

        started = time.monotonic()
        for block in data.get("blocks", []):
            status = block.get("metadata", {}).get("status", "")
            event_type = EventType.TEMPORAL_BLOCK_ADDED
            if status == "cancelled":
                event_type = EventType.TEMPORAL_BLOCK_CANCELLED
            elif block.get("metadata", {}).get("updated_existing"):
                event_type = EventType.TEMPORAL_BLOCK_UPDATED
            produced.append(Event(
                event_type=event_type,
                aggregate_id=block["block_id"],
                aggregate_type=AggregateType.TEMPORAL,
                causation_id=causation_id,
                payload=block,
                metadata={
                    "trace_id": trace_id,
                    "source": self.source_name,
                    "calendar_id": data.get("calendar_id", self.settings.google_calendar_calendar_id),
                    "dedup_key": block.get("metadata", {}).get("dedup_key", ""),
                },
            ))

        produced.append(Event(
            event_type=EventType.CONNECTOR_FETCH_COMPLETED,
            aggregate_id=self.source_name,
            aggregate_type=AggregateType.SYSTEM,
            causation_id=causation_id,
            payload=data,
            metadata={
                "trace_id": trace_id,
                "source": self.source_name,
                "duration_ms": round((time.monotonic() - started) * 1000, 1),
                "item_count": data.get("count", 0),
            },
        ))

        return {
            "ok": True,
            "status": "SUCCESS",
            "message": f"Real sync completed — {data.get('count', 0)} events",
            "count": data.get("count", 0),
            "probe": probe,
        }, produced


def _keyword_kind(title: str, description: str) -> TimeBlockType:
    hay = f"{title} {description}".lower()
    if any(k in hay for k in ("聚餐", "dinner", "meal")):
        return TimeBlockType.SOCIAL_BLOCK
    if any(k in hay for k in ("gym", "健身", "workout")):
        return TimeBlockType.WORKOUT_BLOCK
    if any(k in hay for k in ("train", "airport", "travel", "出行")):
        return TimeBlockType.TRAVEL_BLOCK
    if any(k in hay for k in ("meeting", "call", "面试")):
        return TimeBlockType.MEETING_BLOCK
    if any(k in hay for k in ("休息", "recovery", "sleep")):
        return TimeBlockType.RECOVERY_BLOCK
    return TimeBlockType.CALENDAR_EVENT


def _resolve_calendars(service: Any, calendar_id: str) -> list[dict[str, str]]:
    if calendar_id != "selected":
        return [{"id": calendar_id, "summary": calendar_id}]
    result = _execute_google_request(service.calendarList().list(maxResults=100))
    calendars = []
    for item in result.get("items", []):
        if item.get("selected") or item.get("primary"):
            calendars.append({
                "id": item.get("id", ""),
                "summary": item.get("summary", item.get("id", "")),
            })
    if not calendars:
        calendars.append({"id": "primary", "summary": "primary"})
    return calendars


def _execute_google_request(request: Any, attempts: int = 3) -> dict[str, Any]:
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return request.execute()
        except Exception as exc:
            last_exc = exc
            if attempt >= attempts:
                break
            logger.warning("Google Calendar request failed; retrying (%s/%s): %s", attempt, attempts, exc)
            time.sleep(0.5 * attempt)
    raise RuntimeError(f"google_calendar_network_failed: {last_exc}") from last_exc


def _map_google_event(item: dict[str, Any], calendar_id: str, calendar_summary: str = "") -> dict[str, Any] | None:
    event_id = item.get("id")
    if not event_id:
        return None
    start_raw = item.get("start", {}).get("dateTime") or item.get("start", {}).get("date")
    end_raw = item.get("end", {}).get("dateTime") or item.get("end", {}).get("date")
    if not start_raw or not end_raw:
        return None
    start_dt = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
    end_dt = datetime.fromisoformat(end_raw.replace("Z", "+00:00"))
    updated = item.get("updated", "")
    title = item.get("summary", "(No title)")
    description = item.get("description", "")
    location = item.get("location", "")
    status = item.get("status", "confirmed")
    transparency = item.get("transparency", "opaque")
    extended = item.get("extendedProperties", {}) or {}
    private_props = extended.get("private", {}) or {}
    shared_props = extended.get("shared", {}) or {}
    kind = _keyword_kind(title, description)
    dedup_raw = f"google_calendar|{calendar_id}|{event_id}|{updated}|{start_dt.isoformat()}|{end_dt.isoformat()}"
    dedup_key = hashlib.sha256(dedup_raw.encode()).hexdigest()[:24]
    block_id = hashlib.sha256(f"gcal|{event_id}|{start_dt.isoformat()}|{calendar_id}".encode()).hexdigest()[:24]
    metadata = {
        "external_source": "google_calendar",
        "external_id": event_id,
        "calendar_id": calendar_id,
        "calendar_summary": calendar_summary,
        "updated": updated,
        "transparency": transparency,
        "status": status,
        "dedup_key": dedup_key,
        "extended_private": private_props,
        "extended_shared": shared_props,
    }
    for key in ("managed_by", "source", "plan_id", "jwxt_block_id"):
        if key in private_props:
            metadata[key] = private_props[key]

    return TimeBlock(
        block_id=block_id,
        source=TemporalSource.GOOGLE_CALENDAR,
        block_type=kind,
        start=start_dt,
        end=end_dt,
        title=title,
        location=location,
        description=description,
        metadata=metadata,
    ).to_dict()
