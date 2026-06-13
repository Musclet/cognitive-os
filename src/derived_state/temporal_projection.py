"""Temporal Projection Engine — derived state from TimeBlocks.

Pure functions: list[TimeBlock] → TemporalProjection.
Deterministic, replay-safe, no external dependencies.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Any

from src.core.temporal import TimeBlock, TemporalProjection, TemporalSource, TimeBlockType

# Configurable constants
WAKE_HOUR = 6     # Earliest waking hour
SLEEP_HOUR = 23   # Latest active hour
DAILY_WAKING_HOURS = SLEEP_HOUR - WAKE_HOUR  # 17h
WEEKLY_CAPACITY = DAILY_WAKING_HOURS * 7  # ~119h baseline


def compute_projection(
    blocks: list[TimeBlock],
    *,
    as_of: datetime | None = None,
) -> TemporalProjection:
    """Compute a TemporalProjection from a set of TimeBlocks.

    Args:
        blocks: All current TimeBlocks from all sources.

    Returns:
        TemporalProjection with free_slots, density, switching, capacity, load.
    """
    now = as_of or datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)
    week_start = today_start - timedelta(days=now.weekday())
    week_end = week_start + timedelta(days=7)

    # Filter blocks within the current week
    week_blocks = [b for b in blocks if b.start < week_end and b.end > week_start]

    # Compute free slots for today
    today_busy = sorted(
        [b for b in week_blocks if b.start < today_end and b.end > today_start],
        key=lambda b: b.start,
    )
    free_slots = _compute_free_slots(today_start, today_end, today_busy)

    # Busy density: fraction of waking hours occupied today
    busy_density = _compute_busy_density(today_start, today_end, today_busy)

    # Context switching: count distinct block types / sources
    context_switching_score = _compute_context_switching(today_busy)

    # Daily capacity: remaining free hours today
    today_occupied = sum(
        min((b.end - b.start).total_seconds() / 3600, 4)  # cap per block at 4h
        for b in today_busy
    )
    daily_capacity = max(0, DAILY_WAKING_HOURS - today_occupied)

    # Weekly load: total occupied hours this week / baseline
    weekly_occupied = 0.0
    for b in week_blocks:
        if b.start >= week_start and b.end <= week_end:
            weekly_occupied += min(
                (b.end - b.start).total_seconds() / 3600, 4
            )
    weekly_load = weekly_occupied / WEEKLY_CAPACITY

    # Source breakdown
    source_breakdown: dict[str, int] = defaultdict(int)
    for b in week_blocks:
        source_breakdown[b.source.value] += 1

    return TemporalProjection(
        free_slots=free_slots,
        busy_density=min(busy_density, 1.0),
        context_switching_score=min(context_switching_score, 1.0),
        daily_capacity=round(daily_capacity, 1),
        weekly_load=round(min(weekly_load, 2.0), 3),
        total_blocks=len(week_blocks),
        source_breakdown=dict(source_breakdown),
    )


def _compute_free_slots(
    day_start: datetime,
    day_end: datetime,
    busy_blocks: list[TimeBlock],
) -> list[TimeBlock]:
    """Find gaps between busy blocks during waking hours."""
    wake_start = day_start.replace(hour=WAKE_HOUR)
    sleep_end = day_start.replace(hour=SLEEP_HOUR)

    free_slots = []
    cursor = wake_start

    for block in busy_blocks:
        b_start = max(block.start, wake_start)
        b_end = min(block.end, sleep_end)

        if b_start > cursor:
            free_slots.append(TimeBlock(
                block_id=f"free_{cursor.isoformat()}",
                source=TemporalSource.SYSTEM,
                block_type=TimeBlockType.FREE_SLOT,
                start=cursor,
                end=min(b_start, sleep_end),
                title="Free",
            ))
        cursor = max(cursor, b_end)

    # Final gap until sleep
    if cursor < sleep_end:
        free_slots.append(TimeBlock(
            block_id=f"free_{cursor.isoformat()}",
            source=TemporalSource.SYSTEM,
            block_type=TimeBlockType.FREE_SLOT,
            start=cursor,
            end=sleep_end,
            title="Free",
        ))

    return free_slots


def _compute_busy_density(
    day_start: datetime,
    day_end: datetime,
    busy_blocks: list[TimeBlock],
) -> float:
    """Fraction of waking hours occupied."""
    wake_start = day_start.replace(hour=WAKE_HOUR)
    sleep_end = day_start.replace(hour=SLEEP_HOUR)

    occupied_minutes = 0.0
    for block in busy_blocks:
        b_start = max(block.start, wake_start)
        b_end = min(block.end, sleep_end)
        if b_end > b_start:
            occupied_minutes += (b_end - b_start).total_seconds() / 60

    waking_minutes = DAILY_WAKING_HOURS * 60
    return occupied_minutes / waking_minutes


def _compute_context_switching(busy_blocks: list[TimeBlock]) -> float:
    """Score based on how many distinct source/type transitions occur.

    More different activities → higher switching cost.
    """
    if len(busy_blocks) < 2:
        return 0.0

    transitions = 0
    for i in range(1, len(busy_blocks)):
        prev = busy_blocks[i - 1]
        curr = busy_blocks[i]
        if prev.source != curr.source or prev.block_type != curr.block_type:
            transitions += 1

    # Normalize: 10+ transitions → 1.0
    return min(transitions / 10.0, 1.0)
