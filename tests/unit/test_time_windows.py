"""Tests for time_windows module — busy intervals, overlap detection, free windows."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from src.domain.planning.time_windows import (
    load_busy_intervals,
    detect_overlap,
    compute_free_windows,
    art_exclude_filter,
    LOCAL_TZ,
)

TZ = ZoneInfo("Asia/Singapore")


def _dt(h: int = 0, m: int = 0) -> datetime:
    return datetime(2026, 6, 1, h, m, tzinfo=TZ)


def _dict_block(block_id: str, sh: int, sm: int, eh: int, em: int,
                source: str = "jwxt", block_type: str = "class_lecture",
                managed: bool = False) -> dict:
    """Helper to create a dict-style temporal block."""
    meta = {}
    if managed:
        meta["managed_by"] = "cognitive_os"
        meta["source"] = "daily_art_plan"
    return {
        "block_id": block_id,
        "source": source,
        "block_type": block_type,
        "start": _dt(sh, sm).isoformat(),
        "end": _dt(eh, em).isoformat(),
        "title": "Test Block",
        "metadata": meta,
    }


def _dt_block(block_id: str, sh: int, sm: int, eh: int, em: int,
              source: str = "jwxt", block_type: str = "class_lecture",
              managed: bool = False) -> dict:
    """Helper to create a datetime-style block (like TimeBlock.to_dict())."""
    return _dict_block(block_id, sh, sm, eh, em, source, block_type, managed)


class TestLoadBusyIntervals:
    def test_empty_blocks(self):
        busy = load_busy_intervals([], _dt(6), _dt(23))
        assert busy == []

    def test_single_block(self):
        blocks = [_dt_block("b1", 8, 0, 10, 0)]
        busy = load_busy_intervals(blocks, _dt(6), _dt(23))
        assert len(busy) == 1
        assert busy[0] == (_dt(8, 0), _dt(10, 0))

    def test_multiple_blocks_merged(self):
        blocks = [
            _dt_block("b1", 8, 0, 9, 0),
            _dt_block("b2", 9, 30, 10, 30),  # overlaps b1 if 9:00 < 9:30? No, 9:00-9:30 gap
            _dt_block("b3", 10, 0, 12, 0),    # overlaps b2 at 10:00-10:30
        ]
        busy = load_busy_intervals(blocks, _dt(6), _dt(23))
        assert len(busy) == 2  # b1 separate, b2+b3 merged
        assert busy[1][0] == _dt(9, 30)
        assert busy[1][1] == _dt(12, 0)

    def test_clips_to_day_bounds(self):
        blocks = [_dt_block("b1", 5, 0, 7, 0)]  # starts before day_start
        busy = load_busy_intervals(blocks, _dt(6), _dt(23))
        assert len(busy) == 1
        assert busy[0][0] == _dt(6, 0)  # clipped to day_start
        assert busy[0][1] == _dt(7, 0)

    def test_excludes_managed_art_with_filter(self):
        blocks = [
            _dt_block("art", 10, 0, 12, 0, source="system", block_type="personal_task_block", managed=True),
            _dt_block("class", 14, 0, 16, 0, source="jwxt", block_type="class_lecture"),
        ]
        busy = load_busy_intervals(blocks, _dt(6), _dt(23), exclude_filter=art_exclude_filter)
        assert len(busy) == 1  # only the class
        assert busy[0] == (_dt(14, 0), _dt(16, 0))

    def test_excludes_free_slot_with_filter(self):
        blocks = [
            _dt_block("free", 8, 0, 10, 0, source="system", block_type="free_slot"),
            _dt_block("busy", 14, 0, 15, 0),
        ]
        busy = load_busy_intervals(blocks, _dt(6), _dt(23), exclude_filter=art_exclude_filter)
        assert len(busy) == 1
        assert busy[0] == (_dt(14, 0), _dt(15, 0))


class TestDetectOverlap:
    def test_no_overlap(self):
        busy = [(_dt(8, 0), _dt(10, 0))]
        assert not detect_overlap(_dt(10, 0), _dt(12, 0), busy)

    def test_direct_overlap(self):
        busy = [(_dt(8, 0), _dt(10, 0))]
        assert detect_overlap(_dt(9, 0), _dt(11, 0), busy)

    def test_contained(self):
        busy = [(_dt(8, 0), _dt(12, 0))]
        assert detect_overlap(_dt(9, 0), _dt(10, 0), busy)

    def test_wraps_around(self):
        busy = [(_dt(9, 0), _dt(11, 0))]
        assert detect_overlap(_dt(8, 0), _dt(12, 0), busy)

    def test_edge_touching(self):
        """[start, end) — end touching busy start is NOT overlap."""
        busy = [(_dt(10, 0), _dt(12, 0))]
        assert not detect_overlap(_dt(8, 0), _dt(10, 0), busy)

    def test_empty_busy(self):
        assert not detect_overlap(_dt(8, 0), _dt(10, 0), [])


class TestComputeFreeWindows:
    def test_no_busy_full_day(self):
        free = compute_free_windows([], _dt(6), _dt(23))
        assert len(free) == 1
        assert free[0][0] == _dt(6, 0)
        assert free[0][1] == _dt(23, 0)

    def test_single_busy_block(self):
        blocks = [_dt_block("b1", 10, 0, 12, 0)]
        free = compute_free_windows(blocks, _dt(6), _dt(23))
        assert len(free) == 2
        assert free[0] == (_dt(6, 0), _dt(10, 0))
        assert free[1] == (_dt(12, 0), _dt(23, 0))

    def test_with_buffer(self):
        blocks = [_dt_block("b1", 10, 0, 12, 0)]
        free = compute_free_windows(blocks, _dt(6), _dt(23), buffer_minutes=15)
        assert len(free) == 2
        # Non-edge gap uses buffer
        assert free[0] == (_dt(6, 0), _dt(9, 45))   # first gap: end - buffer = 10:00-0:15 = 9:45
        assert free[1][0] == _dt(12, 15)

    def test_managed_art_excluded_via_filter(self):
        blocks = [
            _dt_block("art", 8, 0, 10, 0, source="system", block_type="personal_task_block", managed=True),
            _dt_block("class", 14, 0, 16, 0),
        ]
        free = compute_free_windows(blocks, _dt(6), _dt(23), exclude_filter=art_exclude_filter)
        assert len(free) == 2
        # Art block should be invisible, so first window is 6:00-14:00
        assert free[0] == (_dt(6, 0), _dt(14, 0))
        assert free[1] == (_dt(16, 0), _dt(23, 0))

    def test_min_window_filter(self):
        blocks = [_dt_block("b1", 8, 0, 8, 25)]  # 25 min busy block
        free = compute_free_windows(blocks, _dt(6), _dt(9), min_window_minutes=30)
        # Gaps: 6:00-8:00 (2h >= 30), 8:25-9:00 (35min >= 30)
        # Both should pass the min_window filter
        assert len(free) == 2

    def test_min_window_skips_small_gaps(self):
        """Gaps shorter than min_window_minutes should be excluded."""
        blocks = [_dt_block("b1", 8, 0, 8, 40)]  # ends 8:40
        free = compute_free_windows(blocks, _dt(6), _dt(9), min_window_minutes=30)
        # Gap after: 8:40-9:00 = 20 min < 30, should be excluded
        assert len(free) == 1  # only 6:00-8:00
        assert free[0] == (_dt(6, 0), _dt(8, 0))

    def test_google_calendar_busy(self):
        blocks = [
            _dt_block("event", 19, 0, 20, 0, source="google_calendar", block_type="calendar_event"),
        ]
        free = compute_free_windows(blocks, _dt(6), _dt(23), exclude_filter=art_exclude_filter)
        assert len(free) == 2
        assert free[0] == (_dt(6, 0), _dt(19, 0))
        assert free[1] == (_dt(20, 0), _dt(23, 0))

    def test_jwxt_and_google_calendar_mixed(self):
        blocks = [
            _dt_block("jwxt", 8, 0, 12, 0, source="jwxt", block_type="class_lecture"),
            _dt_block("gc", 14, 0, 15, 30, source="google_calendar", block_type="calendar_event"),
        ]
        free = compute_free_windows(blocks, _dt(6), _dt(23), exclude_filter=art_exclude_filter)
        assert len(free) == 3
        assert free[0] == (_dt(6, 0), _dt(8, 0))
        assert free[1] == (_dt(12, 0), _dt(14, 0))
        assert free[2] == (_dt(15, 30), _dt(23, 0))
