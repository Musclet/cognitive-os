"""Planning derived state — task scheduling recommendations.

Pure function over temporal blocks + cognition → planning suggestions.
Deterministic, replay-safe, rule-based only.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any

from src.core.temporal import TimeBlock


def compute_planning(
    temporal_blocks: list[TimeBlock],
    cognition: dict[str, Any],
    adaptive: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Generate planning suggestions from current state.

    Args:
        temporal_blocks: All current TimeBlocks (schedule, calendar, DDLs).
        cognition: Current cognitive state from compute_cognition().

    Returns:
        recommended_windows, overloaded_days, focus_windows, recovery_slots.
    """
    now = datetime.now(timezone.utc)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow = today + timedelta(days=1)

    stress = cognition.get("stress_projection", 0)
    fatigue = cognition.get("fatigue_risk", 0)
    capacity_48h = cognition.get("next_48h_capacity", 0)
    pending = cognition.get("pending_total", 0)

    # Adaptive overrides
    adaptive_intensity = adaptive.get("recommended_intensity", "normal") if adaptive else "normal"
    adaptive_window = adaptive.get("preferred_window_type", "standard") if adaptive else "standard"
    adaptive_confidence = adaptive.get("adaptation_confidence", 0) if adaptive else 0

    # Parse blocks into today and tomorrow
    today_blocks = [b for b in temporal_blocks if _in_day(b, today)]
    tomorrow_blocks = [b for b in temporal_blocks if _in_day(b, tomorrow)]

    # Compute free slots for today
    today_free = _find_free_slots(today, today_blocks)

    # Generate windows
    recommended_windows = _recommend_windows(today_free, stress, fatigue, pending, adaptive_intensity, adaptive_window, adaptive_confidence)
    overloaded_days = _detect_overloaded_days(temporal_blocks, today)
    focus_windows = _find_focus_windows(today_free, stress, fatigue, adaptive_intensity, adaptive_confidence)
    recovery_slots = _find_recovery_slots(today_free, fatigue)

    return {
        "recommended_windows": recommended_windows,
        "overloaded_days": overloaded_days,
        "focus_windows": focus_windows,
        "recovery_slots": recovery_slots,
        "pending_tasks": pending,
        "planning_advice": _generate_planning_advice(
            recommended_windows, overloaded_days, cognition
        ),
    }


# ── Helpers ───────────────────────────────────────────────────────────

WAKE_HOUR = 6
SLEEP_HOUR = 23


def _in_day(block: TimeBlock, day: datetime) -> bool:
    day_end = day + timedelta(days=1)
    return block.start < day_end and block.end > day


def _find_free_slots(day: datetime, busy: list[TimeBlock]) -> list[dict]:
    """Find free time slots for a given day."""
    wake = day.replace(hour=WAKE_HOUR)
    sleep = day.replace(hour=SLEEP_HOUR)

    sorted_busy = sorted(
        [b for b in busy if b.start < sleep and b.end > wake],
        key=lambda b: b.start,
    )

    slots = []
    cursor = wake

    for block in sorted_busy:
        b_start = max(block.start, wake)
        b_end = min(block.end, sleep)
        if b_start > cursor:
            duration = (b_start - cursor).total_seconds() / 60
            slots.append({
                "start": cursor.isoformat(),
                "end": b_start.isoformat(),
                "duration_minutes": round(duration),
            })
        cursor = max(cursor, b_end)

    if cursor < sleep:
        duration = (sleep - cursor).total_seconds() / 60
        slots.append({
            "start": cursor.isoformat(),
            "end": sleep.isoformat(),
            "duration_minutes": round(duration),
        })

    return slots


def _recommend_windows(
    free_slots: list[dict],
    stress: float,
    fatigue: float,
    pending: int,
    adaptive_intensity: str = "normal",
    adaptive_window: str = "standard",
    adaptive_confidence: float = 0,
) -> list[dict]:
    """Recommend execution windows for pending tasks.

    Rules:
    - Slots >= 90min with low stress → "deep work" window
    - Slots >= 45min → "standard task" window
    - Slots < 30min → "quick task" window
    - High stress/fatigue → downgrade window type
    """
    if pending == 0:
        return []

    windows = []
    for slot in free_slots:
        dur = slot["duration_minutes"]
        if dur < 15:
            continue

        # Classify window type (base classification)
        if dur >= 90 and stress < 0.5 and fatigue < 0.4:
            wtype = "deep_work"
            label = "Deep work window"
        elif dur >= 45 and stress < 0.7:
            wtype = "standard"
            label = "Task window"
        elif dur >= 20:
            wtype = "quick"
            label = "Quick task"
        else:
            continue

        # Downgrade if fatigue is high (before adaptive adjustment)
        if fatigue > 0.6 and wtype == "deep_work":
            wtype = "standard"
            label = "Task window (fatigue-limited)"
        elif fatigue > 0.8 and wtype == "standard":
            wtype = "quick"
            label = "Quick task only (high fatigue)"

        # Adaptive adjustment: adjust based on learned behavior patterns
        if adaptive_confidence > 0.3:
            # Cap window type based on preferred window
            window_rank = {"deep_work": 3, "standard": 2, "quick": 1}
            max_rank = window_rank.get(adaptive_window, 2)
            current_rank = window_rank.get(wtype, 2)
            if current_rank > max_rank:
                # Downgrade to preferred window type
                wtype = adaptive_window
                label = f"{adaptive_window.replace('_', ' ').title()} window (adaptive)"

            # Adjust intensity: reduce slot duration expectations
            if adaptive_intensity == "reduced":
                if wtype == "deep_work":
                    wtype = "standard"
                    label = "Task window (intensity reduced)"
                elif wtype == "standard" and dur < 60:
                    wtype = "quick"
                    label = "Quick task (intensity reduced)"
            elif adaptive_intensity == "light":
                wtype = "quick"
                label = "Light task (intensity minimized)"
                if dur < 15:
                    continue  # Skip too-short slots in light mode

            if adaptive_intensity == "focused" and wtype == "standard":
                # Promote to deep_work if conditions allow
                if dur >= 90 and stress < 0.4 and fatigue < 0.3:
                    wtype = "deep_work"
                    label = "Deep work window (focused mode)"

        # Format time
        start_ts = slot["start"][11:16]
        end_ts = slot["end"][11:16]

        windows.append({
            "time": f"{start_ts}-{end_ts}",
            "duration_minutes": dur,
            "type": wtype,
            "label": label,
            "reason": _window_reason(wtype, stress, fatigue, dur),
        })

    return windows


def _window_reason(wtype: str, stress: float, fatigue: float, dur: int) -> str:
    if wtype == "deep_work":
        return f"{dur}min free + low stress ({stress*100:.0f}%) — ideal for focused work"
    if wtype == "standard":
        return f"{dur}min available — suitable for regular tasks"
    return f"Brief {dur}min window — quick tasks only"


def _detect_overloaded_days(
    blocks: list[TimeBlock],
    today: datetime,
) -> list[dict]:
    """Detect days with high occupancy."""
    overloaded = []
    for offset in range(7):
        day = today + timedelta(days=offset)
        wake = day.replace(hour=WAKE_HOUR)
        sleep = day.replace(hour=SLEEP_HOUR)

        occupied = 0.0
        for b in blocks:
            if not _in_day(b, day):
                continue
            b_start = max(b.start, wake)
            b_end = min(b.end, sleep)
            if b_end > b_start:
                occupied += (b_end - b_start).total_seconds() / 3600

        waking_hours = SLEEP_HOUR - WAKE_HOUR
        density = occupied / waking_hours

        if density > 0.8:
            overloaded.append({
                "date": day.strftime("%Y-%m-%d"),
                "density": round(density, 2),
                "free_hours": round(waking_hours - occupied, 1),
                "level": "critical" if density > 0.9 else "high",
            })
        elif density > 0.65:
            overloaded.append({
                "date": day.strftime("%Y-%m-%d"),
                "density": round(density, 2),
                "free_hours": round(waking_hours - occupied, 1),
                "level": "moderate",
            })

    return overloaded[:3]  # top 3


def _find_focus_windows(
    free_slots: list[dict],
    stress: float,
    fatigue: float,
    adaptive_intensity: str = "normal",
    adaptive_confidence: float = 0,
) -> list[dict]:
    """Find slots suitable for focused deep work."""
    if stress > 0.6 or fatigue > 0.5:
        return []

    # Adaptive: if intensity is light/reduced, don't force focus
    if adaptive_confidence > 0.3 and adaptive_intensity in ("light", "reduced"):
        return []  # User doesn't respond well to focus windows

    focus = []
    for slot in free_slots:
        if slot["duration_minutes"] >= 90:
            start_ts = slot["start"][11:16]
            end_ts = slot["end"][11:16]
            focus.append({
                "time": f"{start_ts}-{end_ts}",
                "duration_minutes": slot["duration_minutes"],
                "quality": "excellent" if slot["duration_minutes"] >= 150 else "good",
            })

    return focus[:2]  # top 2


def _find_recovery_slots(
    free_slots: list[dict],
    fatigue: float,
) -> list[dict]:
    """Suggest recovery slots when fatigue is elevated."""
    if fatigue < 0.3:
        return []

    recovery = []
    for slot in free_slots:
        if slot["duration_minutes"] >= 30:
            start_ts = slot["start"][11:16]
            end_ts = slot["end"][11:16]
            recovery.append({
                "time": f"{start_ts}-{end_ts}",
                "duration_minutes": slot["duration_minutes"],
                "suggestion": "Take a break" if fatigue > 0.6 else "Light activity",
            })

    return recovery[:2]


def _generate_planning_advice(
    windows: list[dict],
    overloaded: list[dict],
    cognition: dict[str, Any],
) -> list[str]:
    """Generate human-readable planning advice."""
    advice = []

    stress = cognition.get("stress_projection", 0)
    pending = cognition.get("pending_total", 0)
    capacity = cognition.get("next_48h_capacity", 0)

    # Overload warning
    if overloaded:
        worst = overloaded[0]
        if worst["level"] == "critical":
            advice.append(
                f"{worst['date']} is critically overloaded "
                f"({worst['density']*100:.0f}%). Shift tasks to earlier days."
            )

    # Capacity advice
    if capacity > 1.0 and pending > 0:
        advice.append(
            f"48h capacity exceeded. {pending} tasks pending. "
            f"Complete highest-priority task first."
        )

    # Window recommendation
    deep_work = [w for w in windows if w["type"] == "deep_work"]
    if deep_work and stress < 0.4:
        advice.append(
            f"Good conditions for deep work at {deep_work[0]['time']}. "
            f"Use this for your hardest task."
        )

    # Recovery suggestion
    if stress > 0.6:
        advice.append("Consider a recovery break before starting new tasks.")

    if not advice:
        advice.append("Schedule looks manageable. Good time for review or study.")

    return advice
