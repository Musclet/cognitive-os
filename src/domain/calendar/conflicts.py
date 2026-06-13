"""Deterministic calendar conflict detection."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

LOCAL_TZ = timezone(timedelta(hours=8))


def parse_datetime(value: str, date_ref: date | None = None) -> datetime | None:
    """Parse an ISO datetime or an HH:MM value anchored to date_ref."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=LOCAL_TZ)
    except (ValueError, TypeError):
        pass
    if date_ref and ":" in value and len(value) <= 5:
        try:
            return datetime.fromisoformat(
                f"{date_ref.isoformat()}T{value}:00"
            ).replace(tzinfo=LOCAL_TZ)
        except (ValueError, TypeError):
            pass
    return None


def detect_conflicts(
    candidate_start: datetime,
    candidate_end: datetime,
    state: dict[str, Any],
    exclude_event_id: str = "",
    temporal_blocks_by_day: dict[str, Any] | None = None,
    temporal_blocks: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return existing schedule, calendar, and temporal blocks that overlap."""
    conflicts: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    day = candidate_start.date()

    def append(item: dict[str, Any]) -> None:
        key = (
            str(item.get("source", "")),
            str(item.get("event_id", "")),
            str(item.get("title", "")),
            str(item.get("start", "")),
            str(item.get("end", "")),
        )
        if key not in seen:
            seen.add(key)
            conflicts.append(item)

    def overlaps(start: datetime, end: datetime) -> bool:
        return candidate_start < end and start < candidate_end

    schedule = state.get("schedule", {}).get(day.isoformat(), [])
    if isinstance(schedule, list):
        for item in schedule:
            if not isinstance(item, dict):
                continue
            start = parse_datetime(item.get("start", item.get("start_time", "")), day)
            if start is None:
                continue
            end = parse_datetime(item.get("end", item.get("end_time", "")), day)
            end = end or start + timedelta(hours=1)
            if overlaps(start, end):
                append({
                    "source": "jwxt",
                    "type": "class",
                    "title": item.get("course", item.get("name", "课程")),
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "location": item.get("location", ""),
                })

    calendar = state.get("calendar", {}).get(day.isoformat(), [])
    if isinstance(calendar, list):
        for item in calendar:
            if not isinstance(item, dict):
                continue
            event_id = item.get("event_id", item.get("id", ""))
            if exclude_event_id and event_id == exclude_event_id:
                continue
            start = parse_datetime(item.get("start", item.get("start_time", "")), day)
            if start is None:
                continue
            end = parse_datetime(item.get("end", item.get("end_time", "")), day)
            end = end or start + timedelta(hours=1)
            if overlaps(start, end):
                append({
                    "source": "google_calendar",
                    "type": "event",
                    "title": item.get("summary", "事件"),
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "location": item.get("location", ""),
                    "event_id": event_id,
                })

    state_blocks = state.get("temporal", {}).get("blocks", {})
    if isinstance(state_blocks, dict):
        day_blocks = state_blocks.get(day.isoformat(), [])
        if isinstance(day_blocks, list):
            for item in day_blocks:
                if not isinstance(item, dict):
                    continue
                start = parse_datetime(item.get("start", item.get("start_time", "")), day)
                if start is None:
                    continue
                end = parse_datetime(item.get("end", item.get("end_time", "")), day)
                end = end or start + timedelta(hours=1)
                if overlaps(start, end):
                    append({
                        "source": "system",
                        "type": "plan_block",
                        "title": item.get("label", item.get("title", "计划块")),
                        "start": start.isoformat(),
                        "end": end.isoformat(),
                    })

    if temporal_blocks_by_day and temporal_blocks:
        keys = temporal_blocks_by_day.get(day.isoformat(), [])
        if isinstance(keys, list):
            for key in keys:
                block = temporal_blocks.get(key)
                if block is None:
                    continue
                source_obj = getattr(block, "source", "")
                source = getattr(source_obj, "value", str(source_obj))
                if source not in {"google_calendar", "system"}:
                    continue
                metadata = getattr(block, "metadata", {}) or {}
                event_id = str(metadata.get("external_id", ""))
                if exclude_event_id and event_id == exclude_event_id:
                    continue
                start_raw = getattr(block, "start", None)
                end_raw = getattr(block, "end", None)
                start = (
                    start_raw
                    if isinstance(start_raw, datetime)
                    else parse_datetime(str(start_raw), day)
                )
                if start is None:
                    continue
                end = (
                    end_raw
                    if isinstance(end_raw, datetime)
                    else parse_datetime(str(end_raw), day)
                )
                end = end or start + timedelta(hours=1)
                if overlaps(start, end):
                    block_type_obj = getattr(block, "block_type", "")
                    block_type = getattr(
                        block_type_obj,
                        "value",
                        str(block_type_obj),
                    ) or "time_block"
                    append({
                        "source": source,
                        "type": block_type,
                        "title": getattr(block, "title", "时间块"),
                        "start": start.isoformat(),
                        "end": end.isoformat(),
                        "location": getattr(block, "location", ""),
                        "event_id": event_id,
                    })

    return conflicts
