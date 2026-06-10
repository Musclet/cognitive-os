"""Temporal Core — unified time models.

All time-related data across the system normalizes to TimeBlock.
Sources: jwxt, google_calendar, homework deadlines, manual entries, etc.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4


# ── Temporal Source ────────────────────────────────────────────────────

class TemporalSource(StrEnum):
    """All recognized sources of temporal data."""
    JWXT = "jwxt"                    # 教务系统课表
    GOOGLE_CALENDAR = "google_calendar"
    CHAOXING_HOMEWORK = "chaoxing"    # 学习通作业 DDL
    MANUAL = "manual"                 # 用户手动添加
    SYSTEM = "system"                 # 系统生成的提醒/block
    UNKNOWN = "unknown"


# ── TimeBlock Type ─────────────────────────────────────────────────────

class TimeBlockType(StrEnum):
    CLASS_LECTURE = "class_lecture"
    CLASS_LAB = "class_lab"
    EXAM = "exam"
    HOMEWORK_DEADLINE = "homework_deadline"
    CALENDAR_EVENT = "calendar_event"
    BUSY_BLOCK = "busy_block"
    SOCIAL_BLOCK = "social_block"
    WORKOUT_BLOCK = "workout_block"
    TRAVEL_BLOCK = "travel_block"
    MEETING_BLOCK = "meeting_block"
    RECOVERY_BLOCK = "recovery_block"
    PERSONAL_TASK_BLOCK = "personal_task_block"
    REMINDER = "reminder"
    FREE_SLOT = "free_slot"
    CUSTOM = "custom"


# ── TimeBlock ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TimeBlock:
    """Universal temporal unit.

    Every time-related piece of data becomes a TimeBlock.
    All connectors normalize to this schema.
    """

    block_id: str
    source: TemporalSource
    block_type: TimeBlockType
    start: datetime
    end: datetime
    title: str = ""
    location: str = ""
    description: str = ""
    all_day: bool = False
    recurrence: str | None = None          # RFC 5545 RRULE string or None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_minutes(self) -> float:
        return (self.end - self.start).total_seconds() / 60.0

    def overlaps(self, other: TimeBlock) -> bool:
        return self.start < other.end and other.start < self.end

    def contains(self, dt: datetime) -> bool:
        return self.start <= dt < self.end

    def to_dict(self) -> dict[str, Any]:
        return {
            "block_id": self.block_id,
            "source": self.source.value,
            "block_type": self.block_type.value,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "title": self.title,
            "location": self.location,
            "description": self.description,
            "all_day": self.all_day,
            "recurrence": self.recurrence,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TimeBlock:
        return cls(
            block_id=data["block_id"],
            source=TemporalSource(data["source"]),
            block_type=TimeBlockType(data["block_type"]),
            start=datetime.fromisoformat(data["start"]),
            end=datetime.fromisoformat(data["end"]),
            title=data.get("title", ""),
            location=data.get("location", ""),
            description=data.get("description", ""),
            all_day=data.get("all_day", False),
            recurrence=data.get("recurrence"),
            metadata=data.get("metadata", {}),
        )

    @classmethod
    def make_deadline(
        cls,
        title: str,
        deadline: datetime,
        course: str = "",
        source: TemporalSource = TemporalSource.CHAOXING_HOMEWORK,
    ) -> TimeBlock:
        """Create a deadline TimeBlock (duration = 1h placeholder)."""
        return cls(
            block_id=str(uuid4()),
            source=source,
            block_type=TimeBlockType.HOMEWORK_DEADLINE,
            start=deadline,
            end=deadline,  # instant deadline, not a duration block
            title=title,
            metadata={"course": course},
        )


# ── TemporalProjection ─────────────────────────────────────────────────

@dataclass
class TemporalProjection:
    """Computed projection from all TimeBlocks.

    Derived state — purely computed from current TimeBlock set.
    Deterministic, replay-safe.
    """

    free_slots: list[TimeBlock]
    busy_density: float                  # 0-1, fraction of waking hours occupied
    context_switching_score: float       # 0-1, how many distinct context switches
    daily_capacity: float                # remaining free hours today
    weekly_load: float                   # total occupied hours this week / baseline
    total_blocks: int
    source_breakdown: dict[str, int]     # source → count

    def to_dict(self) -> dict[str, Any]:
        return {
            "free_slots": [b.to_dict() for b in self.free_slots],
            "busy_density": round(self.busy_density, 3),
            "context_switching_score": round(self.context_switching_score, 3),
            "daily_capacity": round(self.daily_capacity, 1),
            "weekly_load": round(self.weekly_load, 3),
            "total_blocks": self.total_blocks,
            "source_breakdown": self.source_breakdown,
        }


# ── Temporal Event Types ───────────────────────────────────────────────

# New event types for the unified temporal layer
TEMPORAL_BLOCK_ADDED = "temporal.block.added"
TEMPORAL_BLOCK_REMOVED = "temporal.block.removed"
TEMPORAL_PROJECTION_UPDATED = "temporal.projection.updated"
