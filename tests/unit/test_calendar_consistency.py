"""Tests for calendar consistency review — post-sync auto-audit.

Covers:
- review_sync_status: stale/empty sync detection
- review_art_block_conflicts: art block vs busy overlap
- review_schedule_mirror: JWXT vs GC mirror verification
- run_consistency_review: composite result with correct severity
- format_review_summary: compact output formatting
- Event flow: CALENDAR_CONSISTENCY_REVIEW_REQUESTED → COMPLETED/FAILED
- StateEngine handlers for review events
- Dedup: no duplicate reviews in cooldown window
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from src.core.events import Event, EventType, AggregateType
from src.core.state_engine import StateEngine
from src.core.calendar_consistency import (
    review_sync_status,
    review_art_block_conflicts,
    review_schedule_mirror,
    run_consistency_review,
    format_review_summary,
    OK,
    WARNING,
    ERROR,
)
from src.infrastructure.config import Settings


# ── Helpers ────────────────────────────────────────────────────────────

def _make_block(
    source: str = "jwxt",
    block_type: str = "class_lecture",
    start_offset_hours: float = 1.0,
    duration_hours: float = 1.0,
    title: str = "Test",
    managed: bool = False,
) -> dict:
    """Create a dict-style temporal block relative to now."""
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Asia/Singapore")
    now = datetime.now(tz)
    start = now + timedelta(hours=start_offset_hours)
    end = start + timedelta(hours=duration_hours)
    meta = {}
    if managed:
        meta["managed_by"] = "cognitive_os"
        meta["source"] = "daily_art_plan"
    return {
        "block_id": f"{source}-{uuid4().hex[:8]}",
        "source": source,
        "block_type": block_type,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "title": title,
        "metadata": meta,
    }


def _make_timeblock_obj(source, block_type, start_hour_offset, end_hour_offset, title="Test", managed=False):
    """Create a SimpleNamespace TimeBlock-like object for state_engine temporal blocks.

    Times are relative to now + offset hours, so blocks are always in the future.
    """
    from zoneinfo import ZoneInfo
    from types import SimpleNamespace
    tz = ZoneInfo("Asia/Singapore")
    now = datetime.now(tz)
    base = now + timedelta(hours=1)  # start 1 hour from now
    meta = {}
    if managed:
        meta["managed_by"] = "cognitive_os"
        meta["source"] = "daily_art_plan"
    return SimpleNamespace(
        source=source,
        block_type=block_type,
        start=base + timedelta(hours=start_hour_offset),
        end=base + timedelta(hours=end_hour_offset),
        title=title,
        block_id=f"{source}-{uuid4().hex[:8]}",
        location="",
        metadata=meta,
    )


def _make_state_engine_with_blocks(blocks: list | None = None,
                                   sync_count: int = 5,
                                   sync_completed_at: str | None = None) -> StateEngine:
    """Build a StateEngine seeded with temporal blocks and sync state."""
    se = StateEngine()
    if blocks:
        for b in blocks:
            # Directly inject into _temporal_blocks to bypass TemporalSource validation
            if hasattr(b, 'block_id'):
                src = str(b.source)
                title = str(b.title)
                start_iso = b.start.isoformat()
                end_iso = b.end.isoformat()
            else:
                src = str(b.get('source', 'unknown'))
                title = str(b.get('title', ''))
                start_iso = b.get('start', '')
                if hasattr(start_iso, 'isoformat'):
                    start_iso = start_iso.isoformat()
                end_iso = b.get('end', '')
                if hasattr(end_iso, 'isoformat'):
                    end_iso = end_iso.isoformat()
            key = f"{src}|{title}|{start_iso}|{end_iso}"
            se._temporal_blocks[key] = b
        se._refresh_temporal_views()
        se._derived_dirty = True
    # Seed calendar sync state
    temporal = se._ensure_aggregate("temporal", "projection")
    temporal["calendar_sync"] = {
        "status": "completed",
        "count": sync_count,
        "completed_at": sync_completed_at or datetime.now(timezone.utc).isoformat(),
    }
    if sync_count == 0:
        temporal["calendar_sync"]["count"] = 0
    return se


# ── Tests for review_sync_status ───────────────────────────────────────

class TestReviewSyncStatus:
    def test_ok_when_recent_sync(self):
        """Sync with events in the last hour → ok (empty findings)."""
        se = _make_state_engine_with_blocks(sync_count=15)
        findings = review_sync_status(se)
        assert len(findings) == 0

    def test_warning_when_zero_sync_count(self):
        """Sync completed but 0 events → warning."""
        se = _make_state_engine_with_blocks(sync_count=0)
        findings = review_sync_status(se)
        assert len(findings) == 1
        assert findings[0]["severity"] == WARNING
        assert "0 条" in findings[0]["message"]

    def test_warning_when_stale_sync(self):
        """Sync older than 2 hours → warning."""
        old_time = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        se = _make_state_engine_with_blocks(sync_count=5, sync_completed_at=old_time)
        findings = review_sync_status(se)
        assert len(findings) == 1
        assert findings[0]["severity"] == WARNING
        assert "过时" in findings[0]["message"]

    def test_no_findings_when_no_sync_record(self):
        """No sync record at all (count=-1) → empty findings."""
        se = StateEngine()
        findings = review_sync_status(se)
        assert len(findings) == 0


# ── Tests for review_art_block_conflicts ──────────────────────────────

class TestReviewArtBlockConflicts:
    def test_no_conflicts_when_no_art_blocks(self):
        """No managed art blocks → empty findings."""
        blocks = [
            _make_timeblock_obj("jwxt", "class_lecture", 8, 10),
            _make_timeblock_obj("google_calendar", "calendar_event", 14, 15),
        ]
        se = _make_state_engine_with_blocks(blocks)
        findings = review_art_block_conflicts(se)
        assert len(findings) == 0

    def test_conflict_art_block_overlaps_jwxt_class(self):
        """Managed art block overlapping a JWXT class → warning."""
        blocks = [
            _make_timeblock_obj("jwxt", "class_lecture", 8, 10, title="数学"),
            _make_timeblock_obj("daily_art_plan", "art_block", 9, 11, title="画画练习", managed=True),
        ]
        se = _make_state_engine_with_blocks(blocks)
        findings = review_art_block_conflicts(se)
        assert len(findings) == 1
        assert findings[0]["severity"] == WARNING
        assert "冲突" in findings[0]["message"]

    def test_no_conflict_art_block_outside_busy(self):
        """Managed art block in free time → no conflict."""
        blocks = [
            _make_timeblock_obj("jwxt", "class_lecture", 8, 10, title="数学"),
            _make_timeblock_obj("daily_art_plan", "art_block", 10, 12, title="画画练习", managed=True),
        ]
        se = _make_state_engine_with_blocks(blocks)
        findings = review_art_block_conflicts(se)
        assert len(findings) == 0

    def test_free_slot_excluded_from_busy(self):
        """Free slot blocks don't create artificial conflicts."""
        blocks = [
            _make_timeblock_obj("art_planner", "free_slot", 9, 11, managed=True),
            _make_timeblock_obj("daily_art_plan", "art_block", 9, 11, title="画画", managed=True),
        ]
        se = _make_state_engine_with_blocks(blocks)
        findings = review_art_block_conflicts(se)
        assert len(findings) == 0


# ── Tests for review_schedule_mirror ─────────────────────────────────

class TestReviewScheduleMirror:
    def test_ok_when_verified(self):
        """verify_schedule_mirror returns verified=True → ok severity."""
        blocks = [
            _make_timeblock_obj("jwxt", "class_lecture", 8, 10),
        ]
        se = _make_state_engine_with_blocks(blocks)
        mock_executor = MagicMock()
        mock_executor.verify_schedule_mirror.return_value = {
            "verified": True,
            "jwxt_count": 2,
            "calendar_count": 2,
        }
        findings = review_schedule_mirror(se, executor=mock_executor)
        assert len(findings) == 1
        assert findings[0]["severity"] == OK
        assert "一致" in findings[0]["message"]

    def test_warning_when_not_verified(self):
        """verify_schedule_mirror returns verified=False → warning."""
        blocks = [
            _make_timeblock_obj("jwxt", "class_lecture", 8, 10),
        ]
        se = _make_state_engine_with_blocks(blocks)
        mock_executor = MagicMock()
        mock_executor.verify_schedule_mirror.return_value = {
            "verified": False,
            "jwxt_count": 3,
            "calendar_count": 1,
            "missing_ids": ["id1", "id2"],
            "extra_ids": [],
        }
        findings = review_schedule_mirror(se, executor=mock_executor)
        assert len(findings) == 1
        assert findings[0]["severity"] == WARNING

    def test_warning_on_executor_exception(self):
        """Executor raises exception → warning severity, doesn't crash."""
        blocks = [
            _make_timeblock_obj("jwxt", "class_lecture", 8, 10),
        ]
        se = _make_state_engine_with_blocks(blocks)
        mock_executor = MagicMock()
        mock_executor.verify_schedule_mirror.side_effect = RuntimeError("API error")
        findings = review_schedule_mirror(se, executor=mock_executor)
        assert len(findings) == 1
        assert findings[0]["severity"] == WARNING


# ── Tests for run_consistency_review (composite) ──────────────────────

class TestRunConsistencyReview:
    def test_ok_path(self):
        """All checks pass → overall_severity=ok."""
        blocks = [
            _make_timeblock_obj("jwxt", "class_lecture", 8, 10),
        ]
        se = _make_state_engine_with_blocks(blocks, sync_count=10)
        result = run_consistency_review(se, settings=None, gc_executor=None)
        assert result["overall_severity"] == OK

    def test_warning_on_conflict(self):
        """Art block conflict → overall_severity=warning."""
        blocks = [
            _make_timeblock_obj("jwxt", "class_lecture", 8, 10, title="数学"),
            _make_timeblock_obj("daily_art_plan", "art_block", 9, 11, title="画画", managed=True),
        ]
        se = _make_state_engine_with_blocks(blocks, sync_count=10)
        result = run_consistency_review(se, settings=None)
        assert result["overall_severity"] == WARNING

    def test_findings_included_in_result(self):
        """Result dict includes findings list and timestamp."""
        se = _make_state_engine_with_blocks(sync_count=0)
        result = run_consistency_review(se)
        assert "findings" in result
        assert "overall_severity" in result
        assert "timestamp" in result
        assert isinstance(result["findings"], list)


# ── Tests for format_review_summary ──────────────────────────────────

class TestFormatReviewSummary:
    def test_no_findings(self):
        """Empty findings → '无异常'."""
        text = format_review_summary({"findings": [], "overall_severity": OK})
        assert "无异常" in text

    def test_compact_format(self):
        """Compact format: severity icon + message per line."""
        result = {
            "findings": [
                {"severity": OK, "message": "课表镜像一致", "detail": "jwxt=2 calendar=2"},
                {"severity": WARNING, "message": "发现冲突", "detail": ""},
            ],
            "overall_severity": WARNING,
        }
        text = format_review_summary(result, compact=True)
        assert "课表镜像一致" in text
        assert "发现冲突" in text

    def test_non_compact_includes_detail(self):
        """Non-compact format includes detail in parentheses."""
        result = {
            "findings": [
                {"severity": OK, "message": "课表镜像一致", "detail": "jwxt=2 calendar=2"},
            ],
            "overall_severity": OK,
        }
        text = format_review_summary(result, compact=False)
        assert "jwxt=2" in text


# ── Tests for StateEngine handlers ───────────────────────────────────

class TestStateEngineHandlers:
    def test_review_requested_stored(self):
        """CALENDAR_CONSISTENCY_REVIEW_REQUESTED stores request info."""
        se = StateEngine()
        se._on_calendar_consistency_review_requested(Event(
            event_type=EventType.CALENDAR_CONSISTENCY_REVIEW_REQUESTED,
            aggregate_id="system",
            aggregate_type=AggregateType.SYSTEM,
            payload={"source": "test"},
            metadata={"trace_id": "abc123"},
        ))
        view = se.get_view("calendar_consistency", "latest")
        assert view["request_source"] == "test"
        assert view["request_trace"] == "abc123"

    def test_review_completed_stored(self):
        """CALENDAR_CONSISTENCY_REVIEW_COMPLETED stores findings."""
        se = StateEngine()
        se._on_calendar_consistency_review_completed(Event(
            event_type=EventType.CALENDAR_CONSISTENCY_REVIEW_COMPLETED,
            aggregate_id="system",
            aggregate_type=AggregateType.SYSTEM,
            payload={
                "findings": [{"severity": OK, "message": "ok"}],
                "overall_severity": OK,
            },
        ))
        view = se.get_view("calendar_consistency", "latest")
        assert view["overall_severity"] == OK
        assert len(view["findings"]) == 1
        assert view["review_count"] == 1

    def test_review_failed_stored(self):
        """CALENDAR_CONSISTENCY_REVIEW_FAILED stores error."""
        se = StateEngine()
        se._on_calendar_consistency_review_failed(Event(
            event_type=EventType.CALENDAR_CONSISTENCY_REVIEW_FAILED,
            aggregate_id="system",
            aggregate_type=AggregateType.SYSTEM,
            payload={"error": "connection refused"},
        ))
        view = se.get_view("calendar_consistency", "latest")
        assert view["last_error"] == "connection refused"

    def test_review_completed_sets_derived_dirty(self):
        """REVIEW_COMPLETED marks derived dirty for dashboard refresh."""
        se = StateEngine()
        assert not se._derived_dirty
        se._on_calendar_consistency_review_completed(Event(
            event_type=EventType.CALENDAR_CONSISTENCY_REVIEW_COMPLETED,
            aggregate_id="system",
            aggregate_type=AggregateType.SYSTEM,
            payload={"findings": [], "overall_severity": OK},
        ))
        assert se._derived_dirty

    def test_multiple_reviews_accumulates_history(self):
        """Multiple reviews keep last 20 entries in history."""
        se = StateEngine()
        for i in range(25):
            se._on_calendar_consistency_review_completed(Event(
                event_type=EventType.CALENDAR_CONSISTENCY_REVIEW_COMPLETED,
                aggregate_id="system",
                aggregate_type=AggregateType.SYSTEM,
                payload={"findings": [], "overall_severity": OK},
            ))
        view = se.get_view("calendar_consistency", "latest")
        assert len(view["history"]) == 20  # capped at 20
        assert view["review_count"] == 25


# ── Tests for dedup scenario (not via bus, but via review function) ──

class TestDedup:
    def test_same_state_does_not_change_review_result(self):
        """Calling review twice with same state produces identical result."""
        blocks = [
            _make_timeblock_obj("jwxt", "class_lecture", 8, 10),
        ]
        se = _make_state_engine_with_blocks(blocks, sync_count=5)
        r1 = run_consistency_review(se)
        r2 = run_consistency_review(se)
        assert r1["overall_severity"] == r2["overall_severity"]
        assert len(r1["findings"]) == len(r2["findings"])


# ── Tests for event flow (pipeline/bus behaviour) ────────────────────

class TestEventFlow:
    @pytest.mark.asyncio
    async def test_review_request_to_completed_via_apply(self):
        """Simulate pipeline: apply REVIEW_REQUESTED then REVIEW_COMPLETED on state_engine."""
        se = StateEngine()
        # Apply review requested
        await se.apply(Event(
            event_type=EventType.CALENDAR_CONSISTENCY_REVIEW_REQUESTED,
            aggregate_id="system",
            aggregate_type=AggregateType.SYSTEM,
            payload={"source": "auto_test"},
        ))
        v1 = se.get_view("calendar_consistency", "latest")
        assert v1.get("last_requested_at")
        assert v1.get("request_source") == "auto_test"

        # Apply review completed
        await se.apply(Event(
            event_type=EventType.CALENDAR_CONSISTENCY_REVIEW_COMPLETED,
            aggregate_id="system",
            aggregate_type=AggregateType.SYSTEM,
            payload={"findings": [], "overall_severity": OK},
        ))
        v2 = se.get_view("calendar_consistency", "latest")
        assert v2["overall_severity"] == OK

    @pytest.mark.asyncio
    async def test_review_failure_does_not_crash(self):
        """Simulate a handler that catches exceptions and emits FAILED."""
        se = StateEngine()
        try:
            await se.apply(Event(
                event_type=EventType.CALENDAR_CONSISTENCY_REVIEW_FAILED,
                aggregate_id="system",
                aggregate_type=AggregateType.SYSTEM,
                payload={"error": "test error"},
            ))
        except Exception:
            pytest.fail("review failed event should not crash state_engine")
        v = se.get_view("calendar_consistency", "latest")
        assert v.get("last_error") == "test error"


# ── Tests for review with zero sync (edge case) ──────────────────────

class TestEdgeCases:
    def test_zero_calendar_sync_with_blocks(self):
        """Calendar sync count=0 but blocks exist in state → warning."""
        blocks = [
            _make_timeblock_obj("jwxt", "class_lecture", 8, 10),
        ]
        se = _make_state_engine_with_blocks(blocks, sync_count=0)
        findings = review_sync_status(se)
        assert any("0 条" in f["message"] for f in findings)

    def test_empty_state_no_crash(self):
        """Empty state engine → review runs without crashing."""
        se = StateEngine()
        result = run_consistency_review(se)
        assert result["overall_severity"] in (OK, WARNING, ERROR)
        assert isinstance(result["findings"], list)
