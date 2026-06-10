"""Cognition derived state — Deadline Pressure Engine.

Pure functions over state dict → cognitive metrics.
Integrates temporal projection + homework state.
Deterministic, replay-safe, rule-based only.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any

from src.domain.course_topology import is_excluded_course, normalize_course_name
from src.domain.homework.status import is_open_homework_status
from src.domain.homework.urgency import deadline_urgency_score


def compute_cognition(
    state: dict[str, Any],
    temporal_projection: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute full cognitive state.

    Uses temporal projection (from TimeBlocks) + homework state.

    Returns:
        deadline_pressure, workload_overload, fatigue_risk,
        recovery_window, stress_projection, next_48h_capacity
    """
    now = datetime.now(timezone.utc)

    # Extract inputs
    homework_state = state.get("homework", {})
    notification_state = state.get("notification", {})

    pending = _extract_pending_homework(homework_state)
    busy_density = (temporal_projection or {}).get("busy_density", 0.0)
    weekly_load = (temporal_projection or {}).get("weekly_load", 0.0)
    daily_capacity = (temporal_projection or {}).get("daily_capacity", 17.0)

    # Compute each metric
    deadline_pressure = _deadline_pressure(pending, now)
    workload_overload = _workload_overload(pending, busy_density)
    fatigue_risk = _fatigue_risk(weekly_load, notification_state, now)
    recovery_window = _recovery_window(busy_density, deadline_pressure, daily_capacity)
    stress_projection = _stress_projection(
        deadline_pressure, workload_overload, fatigue_risk
    )
    next_48h_capacity = _next_48h_capacity(pending, now, daily_capacity)

    # ── Subjective reality blending ─────────────────────────────────
    subjective_state = state.get("subjective", {})
    subj = _extract_subjective_modifiers(subjective_state, now)

    mood = subj["current_mood"]
    if mood is not None:
        if mood <= 3:
            fatigue_risk = min(fatigue_risk + 0.15 + (3 - mood) * 0.08, 1.0)
        elif mood <= 5:
            fatigue_risk = min(fatigue_risk + 0.05, 1.0)
        elif mood >= 8:
            fatigue_risk = max(fatigue_risk - 0.08, 0.0)
        if subj["mood_trend"] == "declining":
            fatigue_risk = min(fatigue_risk + 0.05, 1.0)

    if subj["social_plan_today"]:
        next_48h_capacity = min(next_48h_capacity + 0.12, 1.5)
        recovery_window = max(recovery_window * 0.85, 0.05)

    if subj["evening_event"]:
        recovery_window = max(recovery_window * 0.7, 0.05)

    stress_projection = _stress_projection(
        deadline_pressure, workload_overload, fatigue_risk
    )

    # ── Vocab cognitive load (lightweight modifier) ───────────────────
    vocab_state = state.get("vocab", {}).get("momo", {})
    vocab_remaining = (vocab_state.get("today") or {}).get("remaining", 0)
    vocab_stale = vocab_state.get("stale", True)
    vocab_slack = vocab_state.get("slack", False)
    high_pressure = stress_projection > 0.7
    fatigue_high = fatigue_risk > 0.6

    # Vocab cognitive load: remaining > 0 adds small pressure
    if vocab_remaining > 0 and not high_pressure:
        vocab_load = min(vocab_remaining / 50, 0.15)
        workload_overload = min(workload_overload + vocab_load * 0.3, 1.0)
        if high_pressure or fatigue_high:
            pass  # Don't increase load when already high

    # Resolve learning modifiers
    reminder_intensity_boost = 0.0
    if vocab_remaining > 0 and vocab_slack and not high_pressure and not fatigue_high:
        reminder_intensity_boost = 0.15

    return {
        "deadline_pressure": round(deadline_pressure, 3),
        "workload_overload": round(workload_overload, 3),
        "fatigue_risk": round(fatigue_risk, 3),
        "recovery_window": round(recovery_window, 1),
        "stress_projection": round(stress_projection, 3),
        "next_48h_capacity": round(next_48h_capacity, 3),
        "pending_total": len(pending),
        "subjective": {
            "current_mood": subj["current_mood"],
            "mood_trend": subj["mood_trend"],
            "social_plan_today": subj["social_plan_today"],
            "evening_event": subj["evening_event"],
            "active_note_count": subj["active_note_count"],
            "active_context_count": subj["active_context_count"],
        },
        "vocab": {
            "remaining": vocab_remaining,
            "stale": vocab_stale,
            "slack": vocab_slack,
            "reminder_intensity_boost": round(reminder_intensity_boost, 3),
        },
    }


# ── Helpers ───────────────────────────────────────────────────────────

def _extract_subjective_modifiers(
    subjective_state: dict[str, dict],
    now: datetime,
) -> dict[str, Any]:
    """Extract active subjective modifiers from state for cognition blending."""
    modifiers: dict[str, Any] = {
        "current_mood": None,
        "mood_trend": "stable",
        "social_plan_today": False,
        "evening_event": False,
        "active_note_count": 0,
        "active_context_count": 0,
    }
    if not subjective_state:
        return modifiers

    all_moods: list[tuple[datetime, int]] = []
    for user_id, view in subjective_state.items():
        for entry in view.get("mood_history", []):
            try:
                ts = datetime.fromisoformat(entry.get("recorded_at", ""))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                all_moods.append((ts, entry.get("score", 0)))
            except (ValueError, TypeError):
                pass
    all_moods.sort(key=lambda x: x[0])

    if all_moods:
        modifiers["current_mood"] = all_moods[-1][1]
        if len(all_moods) >= 3:
            recent = all_moods[-3:]
            scores = [s for _, s in recent]
            if all(scores[i] <= scores[i+1] for i in range(len(scores)-1)):
                modifiers["mood_trend"] = "improving"
            elif all(scores[i] >= scores[i+1] for i in range(len(scores)-1)):
                modifiers["mood_trend"] = "declining"

    social_keywords = [
        "社交", "social", "聚会", "party", "饭局", "约", "饭",
        "见面", "meet", "hangout", "外出", "出门", "聚餐",
    ]
    evening_keywords = ["晚", "夜", "evening", "night", "今晚", "今晩"]

    for user_id, view in subjective_state.items():
        for note in view.get("notes", []):
            try:
                expires = datetime.fromisoformat(note.get("expires_at", ""))
                if expires.tzinfo is None:
                    expires = expires.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                continue
            if expires <= now:
                continue
            modifiers["active_note_count"] += 1
            text_lower = note.get("text", "").lower()
            if any(kw in text_lower for kw in social_keywords):
                modifiers["social_plan_today"] = True
            if any(kw in text_lower for kw in evening_keywords):
                modifiers["evening_event"] = True

        for ctx in view.get("contexts", []):
            try:
                expires = datetime.fromisoformat(ctx.get("expires_at", ""))
                if expires.tzinfo is None:
                    expires = expires.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                continue
            if expires > now:
                modifiers["active_context_count"] += 1

    return modifiers


def _extract_pending_homework(homework_state: dict) -> list[dict]:
    pending = []
    for agg_id, view in homework_state.items():
        title = view.get("title")
        course = normalize_course_name(view.get("course", ""))
        status = str(view.get("status", "pending") or "").lower()
        raw_status = str(view.get("raw_status", "") or "").lower()
        deadline_str = view.get("deadline")
        if not title or not is_open_homework_status(status, raw_status):
            continue
        if is_excluded_course(course):
            continue
        deadline = None
        if deadline_str:
            try:
                deadline = datetime.fromisoformat(deadline_str)
                if deadline.tzinfo is None:
                    deadline = deadline.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                pass
        pending.append({
            "id": agg_id, "title": title,
            "course": course,
            "deadline": deadline, "deadline_str": deadline_str,
        })
    return pending

def _deadline_pressure(pending: list[dict], now: datetime) -> float:
    """Deadline pressure: how urgent is the closest deadline.

    - Overdue or <= 24h → 1.0
    - <= 72h → elevated
    - > 10 days → no urgency pressure
    - Multiple in 24h window → +0.1 per extra
    """
    if not pending:
        return 0.0

    overdue = 0
    closest_hours: float | None = None
    within_24h = 0

    for hw in pending:
        if hw["deadline"] is None:
            continue
        diff = (hw["deadline"] - now).total_seconds() / 3600
        if diff < 0:
            overdue += 1
        else:
            if closest_hours is None or diff < closest_hours:
                closest_hours = diff
            if diff <= 24:
                within_24h += 1

    base = deadline_urgency_score(closest_hours, overdue)

    clustering_bonus = max(0, within_24h - 1) * 0.1
    return min(base + clustering_bonus, 1.0)


def _workload_overload(pending: list[dict], busy_density: float) -> float:
    """Workload overload: pending count vs available time.

    Combines homework count with schedule density.
    capped at 1.0.
    """
    count_factor = min(len(pending) / 8.0, 1.0)
    # Overload = blend of homework count and schedule density
    return min(count_factor * 0.6 + busy_density * 0.4, 1.0)


def _fatigue_risk(
    weekly_load: float,
    notification_state: dict,
    now: datetime,
) -> float:
    """Fatigue risk: sustained high load + late activity patterns.

    - Weekly load > 0.7 → elevated
    - Late-night activity in last 24h → elevated
    - Multiple consecutive high-load days → compounding
    """
    risk = 0.0

    # Weekly load contribution
    if weekly_load > 0.9:
        risk += 0.5
    elif weekly_load > 0.7:
        risk += 0.3
    elif weekly_load > 0.5:
        risk += 0.15

    # Late activity: check notifications in last 24h during night hours
    day_ago = now - timedelta(hours=24)
    late_count = 0
    total_recent = 0
    for agg_id, view in notification_state.items():
        for entry in view.get("history", []):
            try:
                sent_at = datetime.fromisoformat(entry.get("sent_at", ""))
                if sent_at.tzinfo is None:
                    sent_at = sent_at.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                continue
            if sent_at < day_ago:
                continue
            total_recent += 1
            # Night hours UTC+8: 14:00-22:00 UTC = 22:00-06:00 Beijing
            if sent_at.hour >= 14 or sent_at.hour < 22:
                late_count += 1

    if total_recent > 0 and late_count / total_recent > 0.3:
        risk += 0.2

    return min(risk, 1.0)


def _recovery_window(
    busy_density: float,
    deadline_pressure: float,
    daily_capacity: float,
) -> float:
    """Recovery window: remaining free hours weighted by pressure.

    Low capacity + high pressure → small recovery window.
    High capacity + low pressure → large recovery window.
    Returns estimated free hours available for rest.
    """
    if busy_density > 0.9:
        return max(0.0, daily_capacity * 0.3)
    elif deadline_pressure > 0.7:
        return max(0.0, daily_capacity * 0.5)
    else:
        return daily_capacity * 0.8


def _stress_projection(
    deadline_pressure: float,
    workload_overload: float,
    fatigue_risk: float,
) -> float:
    """Composite stress projection.

    Blend: 45% deadline, 30% overload, 25% fatigue.
    """
    return min(
        deadline_pressure * 0.45 + workload_overload * 0.30 + fatigue_risk * 0.25,
        1.0,
    )


def _next_48h_capacity(
    pending: list[dict],
    now: datetime,
    daily_capacity: float,
) -> float:
    """Project capacity utilization over next 48h.

    Ratio of (deadlines due in 48h + current capacity used) / (capacity * 2 days).
    """
    cutoff = now + timedelta(hours=48)
    due_soon = 0
    for hw in pending:
        if hw["deadline"] and hw["deadline"] <= cutoff:
            due_soon += 1

    # Each deadline ~2h of work
    total_load = due_soon * 2.0
    total_capacity = daily_capacity * 2

    if total_capacity <= 0:
        return 1.0

    return min(total_load / total_capacity, 2.0)
