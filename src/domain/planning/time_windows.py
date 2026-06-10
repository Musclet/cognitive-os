"""Reusable time window / overlap detection utilities.

Provides:
- load_busy_intervals: extract busy intervals from temporal blocks
- detect_overlap: check if a time range overlaps with busy intervals
- compute_free_windows: compute free windows from temporal blocks
- art_exclude_filter: default filter for art planning context

All times normalized to Asia/Singapore.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Callable
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

LOCAL_TZ = ZoneInfo("Asia/Singapore")

ART_SOURCES = {"daily_art_plan", "art_planner"}

FREE_SLOT_TYPE = "free_slot"

# Busy-source allowlist for art planning context:
# (source -> set of block_types that should count as busy)
ART_PLANNING_BUSY_SOURCES: dict[str, set[str]] = {
    "jwxt": {"class_lecture", "class_lab", "exam"},
    "google_calendar": {
        "calendar_event", "meeting", "meeting_block",
        "social", "social_block", "workout", "workout_block",
        "travel", "travel_block", "personal_task", "personal_task_block",
        "busy", "busy_block", "exam",
    },
}

ALL_BUSY_TYPES: set[str] = {
    "class_lecture", "class_lab", "exam",
    "calendar_event", "meeting", "meeting_block",
    "social", "social_block", "workout", "workout_block",
    "travel", "travel_block", "personal_task", "personal_task_block",
    "busy", "busy_block", "reminder",
}


def _get_block_start_end(block: Any) -> tuple[datetime, datetime, dict, str]:
    """Normalize a block (dict or TimeBlock) to (start, end, metadata, block_type_str)."""
    if isinstance(block, dict):
        raw_start = block.get("start", "")
        raw_end = block.get("end", "")
        b_start = datetime.fromisoformat(raw_start.replace("Z", "+00:00")).astimezone(LOCAL_TZ)
        b_end = datetime.fromisoformat(raw_end.replace("Z", "+00:00")).astimezone(LOCAL_TZ)
        meta = block.get("metadata", {}) or {}
        block_type = str(block.get("block_type", ""))
    else:
        b_start = block.start.astimezone(LOCAL_TZ)
        b_end = block.end.astimezone(LOCAL_TZ)
        meta = block.metadata or {}
        block_type = str(block.block_type)
    return b_start, b_end, meta, block_type


def _get_block_source(block: Any) -> str:
    if isinstance(block, dict):
        return str(block.get("source", "") or block.get("external_source", ""))
    return str(getattr(block, "source", ""))


def art_exclude_filter(block: Any) -> bool:
    """Return True if the block should be excluded from busy computation.

    For art planning: managed art blocks and free slots are excluded.
    """
    _, _, meta, block_type = _get_block_start_end(block)
    if block_type == FREE_SLOT_TYPE:
        return True
    if meta.get("managed_by") == "cognitive_os" and meta.get("source") in ART_SOURCES:
        return True
    return False


def load_busy_intervals(
    blocks: list[Any],
    day_start: datetime,
    day_end: datetime,
    exclude_filter: Callable[[Any], bool] | None = None,
) -> list[tuple[datetime, datetime]]:
    """Extract and merge busy intervals from temporal blocks.

    Args:
        blocks: list of TimeBlock objects or dicts.
        day_start: start of window (timezone-aware, Asia/Singapore).
        day_end: end of window (timezone-aware, Asia/Singapore).
        exclude_filter: callable(block) -> True to exclude from busy.

    Returns:
        Sorted, merged list of (start, end) busy intervals.
    """
    busy: list[tuple[datetime, datetime]] = []
    for b in blocks:
        b_start, b_end, _meta, _block_type = _get_block_start_end(b)
        source = _get_block_source(b)

        if b_end <= day_start or b_start >= day_end:
            continue

        b_start = max(b_start, day_start)
        b_end = min(b_end, day_end)

        if exclude_filter and exclude_filter(b):
            continue

        # Only actual time-occupying blocks should reduce planning capacity.
        # Deadlines/reminders are temporal signals, not busy intervals.
        if source in ART_PLANNING_BUSY_SOURCES:
            allowed_types = ART_PLANNING_BUSY_SOURCES[source]
            if _block_type not in allowed_types:
                continue
        elif _block_type not in ALL_BUSY_TYPES:
            continue

        busy.append((b_start, b_end))

    # Merge overlapping intervals
    busy.sort()
    merged: list[tuple[datetime, datetime]] = []
    for start, end in busy:
        if merged and start < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    return merged


def detect_overlap(
    start: datetime,
    end: datetime,
    busy_intervals: list[tuple[datetime, datetime]],
) -> bool:
    """Check whether [start, end) overlaps with any busy interval."""
    for b_start, b_end in busy_intervals:
        if start < b_end and b_start < end:
            return True
    return False


def compute_free_windows(
    blocks: list[Any],
    day_start: datetime,
    day_end: datetime,
    exclude_filter: Callable[[Any], bool] | None = None,
    min_window_minutes: int = 30,
    buffer_minutes: int = 0,
) -> list[tuple[datetime, datetime]]:
    """Compute free time windows within day bounds.

    Args:
        blocks: list of temporal blocks (dict or TimeBlock).
        day_start: window start (timezone-aware).
        day_end: window end (timezone-aware).
        exclude_filter: optional filter to exclude blocks from busy.
        min_window_minutes: minimum free-window duration (default 30).
        buffer_minutes: buffer subtracted from each edge adjacent to busy.

    Returns:
        Sorted list of (start, end) free windows.
    """
    busy = load_busy_intervals(blocks, day_start, day_end, exclude_filter)
    return _free_from_busy(busy, day_start, day_end, min_window_minutes, buffer_minutes)


def _free_from_busy(
    busy: list[tuple[datetime, datetime]],
    day_start: datetime,
    day_end: datetime,
    min_window_minutes: int = 30,
    buffer_minutes: int = 0,
) -> list[tuple[datetime, datetime]]:
    """Compute free windows from already-merged busy intervals."""
    buf = timedelta(minutes=buffer_minutes)
    free: list[tuple[datetime, datetime]] = []
    cursor = day_start

    for b_start, b_end in busy:
        # Gap: cursor -> b_start
        gap_start = cursor
        gap_end = b_start
        eff_start = gap_start + buf if gap_start != day_start else gap_start
        eff_end = gap_end - buf if gap_end != day_end else gap_end
        if eff_end > eff_start and (eff_end - eff_start).total_seconds() / 60 >= min_window_minutes:
            free.append((eff_start, eff_end))
        cursor = max(cursor, b_end)

    # Gap after last busy
    if cursor < day_end:
        gap_start = cursor
        eff_start = gap_start + buf if gap_start != day_start else gap_start
        eff_end = day_end
        if eff_end > eff_start and (eff_end - eff_start).total_seconds() / 60 >= min_window_minutes:
            free.append((eff_start, eff_end))

    return free
