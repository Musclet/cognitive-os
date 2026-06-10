"""Art domain handlers — art planning, progress tracking, calendar management.

Subscribes to art.* events and produces planner events.
Deterministic v1 planner with clean interfaces.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from src.core.events import Event, EventType, AggregateType
from src.core.temporal import TimeBlock, TemporalSource, TimeBlockType
from src.domain.planning.time_windows import (
    compute_free_windows,
    detect_overlap,
    load_busy_intervals,
    art_exclude_filter,
    LOCAL_TZ as TW_LOCAL_TZ,
)
from src.infrastructure.config import Settings

logger = logging.getLogger(__name__)
LOCAL_TZ = ZoneInfo("Asia/Singapore")

# ── Day type classification ───────────────────────────────────────────────

DAY_TYPE_IDEAL = "ideal"
DAY_TYPE_NORMAL = "normal"
DAY_TYPE_HIGH_PRESSURE = "high_pressure"
DAY_TYPE_RECOVERY = "recovery"

DAY_TARGETS: dict[str, int] = {
    DAY_TYPE_IDEAL: 360,        # 6h+
    DAY_TYPE_NORMAL: 210,       # 3-4.5h (default 3.5h)
    DAY_TYPE_HIGH_PRESSURE: 120, # 1.5-2.5h (default 2h)
    DAY_TYPE_RECOVERY: 40,      # 25-60min keep-alive
}

ART_BLOCK_TEMPLATES = [
    {"title": "\U0001f3a8 人体结构训练", "default_min": 90},
    {"title": "\U0001f3a8 作品推进", "default_min": 90},
    {"title": "\U0001f3a8 临摹拆解", "default_min": 60},
]

ART_SOURCES = {"daily_art_plan", "art_planner"}


def classify_day_type(
    deadline_pressure: float = 0.0,
    daily_capacity_hours: float = 12.0,
    fatigue_risk: float = 0.0,
    mood_score: int | None = None,
) -> str:
    """Classify today's day type based on available capacity and pressure.

    Returns one of: ideal, normal, high_pressure, recovery
    """
    if fatigue_risk > 0.7 or (mood_score is not None and mood_score <= 3):
        return DAY_TYPE_RECOVERY
    if deadline_pressure > 0.7 or daily_capacity_hours < 4:
        return DAY_TYPE_HIGH_PRESSURE
    if deadline_pressure > 0.4 or daily_capacity_hours < 7:
        return DAY_TYPE_NORMAL
    return DAY_TYPE_IDEAL


def compute_target_minutes(
    day_type: str,
    settings: Settings | None = None,
) -> int:
    """Get target art minutes for the given day type."""
    s = settings or Settings()
    base = DAY_TARGETS.get(day_type, s.art_default_target_minutes)
    # Honor absolute bounds
    return max(s.art_minimum_keepalive_minutes, min(base, s.art_default_target_minutes))


# ── Free slot computation ─────────────────────────────────────────────────

def _compute_free_slots(
    blocks: list[TimeBlock],
    day_start: datetime,
    day_end: datetime,
    buffer_minutes: int = 5,
) -> list[tuple[datetime, datetime]]:
    """Compute free time slots within day bounds after removing busy blocks.

    Delegates to the shared time_windows module. Managed art blocks and
    free_slot blocks are excluded from busy automatically.
    Skips free windows shorter than 30 minutes.
    Applies a small buffer so art blocks don't abut busy blocks.
    """
    return compute_free_windows(
        blocks,
        day_start,
        day_end,
        exclude_filter=art_exclude_filter,
        min_window_minutes=30,
        buffer_minutes=buffer_minutes,
    )


# ── Planner ────────────────────────────────────────────────────────────────

async def handle_art_plan_requested(event: Event) -> list[Event]:
    """Handle ART_PLAN_REQUESTED — generate art plan and emit ART_PLAN_CREATED.

    Event payload can include:
        blocks: list[TimeBlock] — current temporal blocks
        state: dict — state engine state snapshot
        date_override: str — ISO date for testing
    """
    settings = Settings()
    now = datetime.now(LOCAL_TZ)
    date_str = event.payload.get("date_override", now.strftime("%Y-%m-%d"))

    # Determine day target
    day_type = event.payload.get("day_type", "")
    target_minutes = event.payload.get("target_minutes", 0)
    if not target_minutes:
        if not day_type:
            # Infer from context
            state = event.payload.get("state", {})
            derived = state.get("derived", state) if isinstance(state, dict) else {}
            cognition = derived.get("cognition", {})
            dp = cognition.get("deadline_pressure", 0.0)
            fr = cognition.get("fatigue_risk", 0.0)
            proj = derived.get("temporal_projection", {})
            dc = proj.get("daily_capacity", 12.0)
            day_type = classify_day_type(deadline_pressure=dp, daily_capacity_hours=dc, fatigue_risk=fr)
        target_minutes = compute_target_minutes(day_type, settings)

    # Parse day bounds
    today = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=LOCAL_TZ)
    day_start = today.replace(hour=0, minute=0, second=0)
    day_end = today.replace(hour=23, minute=59, second=59)

    # Compute available free slots (after current time if today)
    raw_blocks = event.payload.get("blocks", [])
    time_blocks = []
    for b in raw_blocks:
        if isinstance(b, dict):
            try:
                time_blocks.append(TimeBlock.from_dict(b))
            except Exception:
                pass
        elif isinstance(b, TimeBlock):
            time_blocks.append(b)

    now_local = datetime.now(LOCAL_TZ)
    effective_start = day_start
    if date_str == now.strftime("%Y-%m-%d"):
        effective_start = max(day_start, now_local)

    free_slots = _compute_free_slots(time_blocks, effective_start, day_end)

    # Distribute target minutes across free slots as art blocks
    remaining = target_minutes
    art_blocks: list[dict[str, Any]] = []
    block_templates = list(ART_BLOCK_TEMPLATES)
    template_idx = 0

    for slot_start, slot_end in free_slots:
        if remaining <= 0:
            break
        slot_duration = int((slot_end - slot_start).total_seconds() / 60)
        if slot_duration < 30:
            continue

        # Pick block size: prefer 60-120 min, cap by remaining
        preferred = min(90, slot_duration, remaining)
        duration = min(preferred, slot_duration)

        template = block_templates[template_idx % len(block_templates)]
        template_idx += 1

        block_start = slot_start
        block_end = block_start + timedelta(minutes=duration)

        art_blocks.append({
            "title": template["title"],
            "start": block_start.isoformat(),
            "end": block_end.isoformat(),
            "duration_min": duration,
            "template": template["title"],
        })

        remaining -= duration

    # Track unscheduled capacity so upstream can warn the user
    unscheduled_minutes = max(0, remaining)

    return [Event(
        event_type=EventType.ART_PLAN_CREATED,
        aggregate_id="art_today",
        aggregate_type=AggregateType.ART,
        payload={
            "date": date_str,
            "day_type": day_type,
            "target_minutes": target_minutes,
            "total_planned_minutes": target_minutes - remaining,
            "unscheduled_minutes": unscheduled_minutes,
            "blocks": art_blocks,
            "free_slots": [(s.isoformat(), e.isoformat()) for s, e in free_slots],
        },
    )]


def _compute_free_slots_from_state(blocks: list, now_local: datetime, day_end: datetime) -> list[tuple[datetime, datetime]]:
    """Compute free slots, excluding managed art blocks."""
    time_blocks = []
    for b in blocks:
        if isinstance(b, dict):
            try:
                time_blocks.append(TimeBlock.from_dict(b))
            except Exception:
                pass
        elif isinstance(b, TimeBlock):
            time_blocks.append(b)
    return _compute_free_slots(time_blocks, now_local, day_end)


async def handle_art_progress_recorded(event: Event) -> list[Event]:
    """Handle ART_PROGRESS_RECORDED — update plan and emit ART_PLAN_UPDATED if rebalance needed.

    Payload:
        completed_minutes: int
        type: str — e.g. "人体速写", "作品推进"
        sessions: int — number of sessions
        note: str — optional user note
        resistance: bool — whether user reported resistance
    """
    # Store progress; check if remaining blocks should be adjusted
    completed = event.payload.get("completed_minutes", 0)
    session_type = event.payload.get("type", "")
    sessions = event.payload.get("sessions", 1)
    note = event.payload.get("note", "")
    resistance = event.payload.get("resistance", False)

    events: list[Event] = []

    # If resistance reported and significant time remains, emit rebalance
    if resistance:
        events.append(Event(
            event_type=EventType.ART_PLAN_REBALANCED,
            aggregate_id="art_today",
            aggregate_type=AggregateType.ART,
            payload={
                "reason": "resistance",
                "completed_minutes": completed,
                "note": note,
                "session_type": session_type,
            },
        ))

    return events


async def handle_art_reality_inserted(event: Event) -> list[Event]:
    """Handle ART_DAILY_REALITY_INSERTED — insert a calendar reality block and trigger rebalance.

    Payload:
        title: str
        start: str (ISO)
        end: str (ISO)
        description: str — optional
        source: str — defaults to "manual"
    """
    settings = Settings()
    title = event.payload.get("title", "临时事项")
    start_str = event.payload.get("start")
    end_str = event.payload.get("end")
    if not start_str or not end_str:
        logger.warning("reality_inserted missing start/end")
        return []

    try:
        start_dt = datetime.fromisoformat(start_str)
        end_dt = datetime.fromisoformat(end_str)
    except (ValueError, TypeError):
        logger.warning("reality_inserted invalid datetime format")
        return []

    # Emit a temporal block for the inserted event
    block = TimeBlock(
        block_id=str(uuid4()),
        source=TemporalSource.MANUAL,
        block_type=TimeBlockType.PERSONAL_TASK_BLOCK,
        start=start_dt,
        end=end_dt,
        title=title,
        description=event.payload.get("description", ""),
    )

    # Emit rebalance event for remaining art blocks
    now = datetime.now(LOCAL_TZ)
    events: list[Event] = [
        Event(
            event_type=EventType.TEMPORAL_BLOCK_ADDED,
            aggregate_id=block.block_id,
            aggregate_type=AggregateType.TEMPORAL,
            payload=block.to_dict(),
        ),
        Event(
            event_type=EventType.ART_PLAN_REBALANCED,
            aggregate_id="art_today",
            aggregate_type=AggregateType.ART,
            payload={
                "reason": "reality_inserted",
                "inserted_title": title,
                "inserted_start": start_str,
                "inserted_end": end_str,
            },
        ),
    ]

    return events


async def handle_art_vibe_code_warning(event: Event) -> list[Event]:
    """Handle ART_VIBE_CODE_WARNING — check if art starter block is done."""
    return []


# ── Natural language parsing (heuristic + DeepSeek) ────────────────────────

_PROGRESS_PATTERNS: list[tuple[re.Pattern[str], dict[str, Any]]] = [
    (re.compile(r"完成\s*(画画|绘画|画|art)", re.IGNORECASE), {"domain": "art"}),
    (re.compile(r"(画画|绘画).*?(\d+)\s*小时", re.IGNORECASE), {"domain": "art"}),
    (re.compile(r"(画画|绘画).*?(\d+)\s*分钟", re.IGNORECASE), {"domain": "art"}),
    (re.compile(r"画画.*?(\d+)%", re.IGNORECASE), {"domain": "art", "is_percent": True}),
    (re.compile(r"(画不动|画累了|不想画|跳过\s*(画画|绘画))", re.IGNORECASE), {"domain": "art", "resistance": True}),
]

_INSERTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(下午|上午|早上|晚上|中午).*?(去|到|去办|吃饭|出门|要)", re.IGNORECASE),
    re.compile(r"(临时|突然|临时要).*?(\d+)\s*(小时|分钟)", re.IGNORECASE),
    re.compile(r"(\d+)[:：点].*?(到|至)[\s]*(\d+)[:：点]", re.IGNORECASE),
]

_GOOD_MORNING_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^早安\s*$"),
    re.compile(r"^早\s*[~～]?$"),
    re.compile(r"^早上好\s*$"),
]


def is_good_morning(text: str) -> bool:
    """Check if text is a "早安" / "早上好" message."""
    return any(p.match(text.strip()) for p in _GOOD_MORNING_PATTERNS)


def is_progress_message(text: str) -> str | None:
    """Check if text is an art progress message.

    Returns None if not a progress message, or the matched domain.
    """
    text_stripped = text.strip()
    for pattern, info in _PROGRESS_PATTERNS:
        if pattern.search(text_stripped):
            return info.get("domain")
    return None


def is_insertion_message(text: str) -> bool:
    """Check if text is a temporal insertion message."""
    return any(p.search(text.strip()) for p in _INSERTION_PATTERNS)


# ── Progress parsing ──────────────────────────────────────────────────────

def parse_art_progress(text: str) -> dict[str, Any] | None:
    """Parse art progress from natural language text.

    Returns dict with parsed fields or None if not parseable.
    """
    text_stripped = text.strip()

    # Pattern: 完成 画画 2小时 人体速写 12张
    m = re.search(r"完成(?:了)?\s*(画画|绘画|art).*?(\d+(?:\.\d+)?)\s*小时", text_stripped, re.IGNORECASE)
    if m:
        hours = float(m.group(2))
        # Extract sub-type
        sub_type = "练习"
        # After the duration, before 张/页
        after_duration = text_stripped[m.end():].strip()
        # Try to extract "XX张" or "XX页"
        count_m = re.search(r"(\d+)\s*(张|页)", after_duration)
        count = int(count_m.group(1)) if count_m else 0
        # Get type description before count
        if count_m:
            type_text = after_duration[:count_m.start()].strip()
            if type_text:
                sub_type = type_text
        return {
            "completed_minutes": int(hours * 60),
            "type": sub_type,
            "sessions": 1,
            "count": count,
            "resistance": False,
        }

    # Pattern: 画画完成40%
    m = re.search(r"(?:画画|绘画).*?(\d+)\s*%", text_stripped)
    if m:
        pct = int(m.group(1))
        # Infer minutes from percentage (estimated)
        return {
            "completed_minutes": None,
            "percent": pct,
            "type": "练习",
            "sessions": 1,
            "resistance": False,
        }

    # Pattern: 画不动 / 跳过 画画
    m = re.search(r"(画不动|画累了|不想画|跳过.*?(?:画画|绘画|art))", text_stripped, re.IGNORECASE)
    if m:
        return {
            "completed_minutes": 0,
            "type": "练习",
            "sessions": 0,
            "resistance": True,
            "note": m.group(1),
        }

    return None
