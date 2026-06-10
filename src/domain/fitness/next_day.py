"""Next-workout-day logic.

Deterministic, file-based against Obsidian notes for V1:

- If the previous training day's note is completed → advance to next training day.
- If the previous scheduled workout note exists but is incomplete → suggest same workout (顺延).
- Weekend remains rest unless explicitly chosen later.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from src.domain.fitness.parser import parse_workout_note
from src.domain.fitness.plan import (
    WORKOUT_DAYS,
    DayName,
    get_training_day,
    is_rest_day,
    previous_training_day,
)
from src.infrastructure.config import Settings

LOCAL_TZ = ZoneInfo("Asia/Singapore")

# ── Result types ─────────────────────────────────────────────────────────────


@dataclass
class NextWorkoutDecision:
    """What the system recommends for the next workout session."""

    suggested_date: date
    suggested_day: DayName
    is_carryover: bool  # True if same workout as previous day (顺延)
    previous_date: date | None
    previous_completed: bool
    reason: str


# ── Exposed helper ───────────────────────────────────────────────────────────


def _workout_note_path(settings: Settings, d: date) -> Path:
    from src.domain.fitness.generator import workout_note_path

    return workout_note_path(settings, d)


def next_workday_logic(
    settings: Settings,
    today: date | None = None,
) -> NextWorkoutDecision:
    """Determine the next training day recommendation.

    Steps:
    1. Find the *previous training day* (the most recent Mon-Fri).
    2. If today is a rest day (Sat/Sun), suggest the most recent past training
       day's workout if incomplete, or Monday if it was complete.
    3. If today itself is a training day, check yesterday's note.
    4. If the previous training day note exists but is incomplete → carryover.
    5. If the previous training day note is complete (or missing) → advance
       to the next planned training day.
    """
    if today is None:
        today = datetime.now(LOCAL_TZ).date()

    # ── Find the previous training day ────────────────────────────────────
    prev_day = previous_training_day(today)

    # Get the note for the previous training day
    prev_path = _workout_note_path(settings, prev_day)
    prev_summary = parse_workout_note(prev_path)
    prev_completed = prev_summary.is_session_completed

    # ── Is today a training day? ───────────────────────────────────────────
    today_day_name = get_training_day(today)

    if today_day_name == "rest":
        # Weekend: no training scheduled, but check if prev was incomplete
        if prev_completed or not prev_path.exists():
            # Advance to the upcoming Monday
            days_until_monday = (7 - today.weekday()) % 7
            if days_until_monday == 0:
                days_until_monday = 7  # next Monday, not today
            next_date = today + timedelta(days=days_until_monday)
            next_day_name = get_training_day(next_date)
            return NextWorkoutDecision(
                suggested_date=next_date,
                suggested_day=next_day_name,
                is_carryover=False,
                previous_date=prev_day,
                previous_completed=True,
                reason=f"今天是{['六','日'][today.weekday()-5]}，下一训练日为 {next_date.isoformat()}（{['星期一','星期二','星期三','星期四','星期五','星期六','星期日'][next_date.weekday()]}）",
            )
        else:
            # Carryover: redo the incomplete workout. Since it's weekend,
            # suggest doing the same workout next training day (Monday).
            days_until_monday = (7 - today.weekday()) % 7
            if days_until_monday == 0:
                days_until_monday = 7
            next_date = today + timedelta(days=days_until_monday)
            return NextWorkoutDecision(
                suggested_date=next_date,
                suggested_day=prev_summary.training_day,
                is_carryover=True,
                previous_date=prev_day,
                previous_completed=False,
                reason=f"上次训练（{prev_day.isoformat()} {prev_summary.training_day}）未完成，顺延至 {next_date.isoformat()} 继续",
            )

    # ── Today IS a training day ────────────────────────────────────────────
    if prev_completed or not prev_path.exists():
        # Normal advance: do today's planned workout
        return NextWorkoutDecision(
            suggested_date=today,
            suggested_day=today_day_name,
            is_carryover=False,
            previous_date=prev_day,
            previous_completed=prev_completed,
            reason=f"按计划进行：{today.isoformat()} {today_day_name}",
        )
    else:
        # Previous incomplete: carryover
        return NextWorkoutDecision(
            suggested_date=today,
            suggested_day=prev_summary.training_day,
            is_carryover=True,
            previous_date=prev_day,
            previous_completed=False,
            reason=f"上次训练（{prev_day.isoformat()} {prev_summary.training_day}）未完成，建议今天继续 {prev_summary.training_day}",
        )
