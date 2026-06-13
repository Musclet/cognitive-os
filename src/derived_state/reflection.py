"""Reflection Engine — long-term trend analysis from event-derived behavior.

Pure function over state dictionary → reflection metrics.
Deterministic, replay-safe, event-derived only.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any


def compute_reflection(
    state: dict,
    *,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Analyze long-term behavioral trends from feedback log.

    Reads behavior/current/feedback_log from state.

    Returns:
        weekly_consistency_trend, deep_work_trend, fatigue_trend,
        planning_effectiveness, recommendation_acceptance_rate, behavior_drift.
    """
    behavior_data = state.get("behavior", {})
    current = behavior_data.get("current", {})
    feedbacks = current.get("feedback_log", [])

    if len(feedbacks) < 5:
        return {
            "weekly_consistency_trend": "insufficient_data",
            "deep_work_trend": "insufficient_data",
            "fatigue_trend": "insufficient_data",
            "planning_effectiveness": 0.5,
            "recommendation_acceptance_rate": 0.0,
            "behavior_drift": 0.0,
            "sample_count": len(feedbacks),
            "analysis_period_days": 0,
        }

    now = as_of or datetime.fromtimestamp(0, timezone.utc)

    # Parse feedbacks with timestamps
    parsed = []
    for fb in feedbacks:
        ts_str = fb.get("timestamp", "")
        try:
            ts = datetime.fromisoformat(ts_str)
        except (ValueError, TypeError):
            continue
        parsed.append({
            "ts": ts,
            "action": fb.get("action", ""),
            "outcome": fb.get("outcome", ""),
            "window_type": fb.get("window_type", "standard"),
            "cog": fb.get("cognition_at_time", {}),
        })

    if len(parsed) < 5:
        return {
            "weekly_consistency_trend": "insufficient_data",
            "deep_work_trend": "insufficient_data",
            "fatigue_trend": "insufficient_data",
            "planning_effectiveness": 0.5,
            "recommendation_acceptance_rate": 0.0,
            "behavior_drift": 0.0,
            "sample_count": len(parsed),
            "analysis_period_days": 0,
        }

    parsed.sort(key=lambda x: x["ts"])
    first_ts = parsed[0]["ts"]
    last_ts = parsed[-1]["ts"]
    period_days = max((last_ts - first_ts).total_seconds() / 86400, 1)

    # ── Split into halves for trend detection ──────────────────────
    midpoint = first_ts + (last_ts - first_ts) / 2
    first_half = [p for p in parsed if p["ts"] <= midpoint]
    second_half = [p for p in parsed if p["ts"] > midpoint]

    def _consistency_score(entries):
        if not entries:
            return 0.5
        a = sum(1 for e in entries if e["action"] == "accepted")
        d = sum(1 for e in entries if e["action"] == "delayed")
        return (a + d * 0.3) / len(entries)

    def _deep_work_rate(entries):
        dw = [e for e in entries if e.get("window_type") == "deep_work"]
        if not dw:
            return 0.5
        return sum(1 for e in dw if e.get("outcome") == "completed") / len(dw)

    def _avg_fatigue_skip(entries):
        high_fatigue = [e for e in entries if e.get("cog", {}).get("fatigue_risk", 0) > 0.7]
        if not high_fatigue:
            return 0.0
        return sum(1 for e in high_fatigue if e.get("action") == "skipped") / len(high_fatigue)

    # Compute trends
    cons_1 = _consistency_score(first_half) if first_half else 0.5
    cons_2 = _consistency_score(second_half) if second_half else 0.5
    cons_delta = cons_2 - cons_1

    dw_1 = _deep_work_rate(first_half) if first_half else 0.5
    dw_2 = _deep_work_rate(second_half) if second_half else 0.5
    dw_delta = dw_2 - dw_1

    fat_1 = _avg_fatigue_skip(first_half) if first_half else 0.0
    fat_2 = _avg_fatigue_skip(second_half) if second_half else 0.0
    fat_delta = fat_2 - fat_1

    # ── Trend labels ───────────────────────────────────────────────
    def _trend_label(delta, threshold=0.1):
        if delta > threshold:
            return "improving"
        elif delta < -threshold:
            return "declining"
        return "stable"

    # ── Planning effectiveness ─────────────────────────────────────
    engaged = [e for e in parsed if e["action"] in ("accepted", "delayed")]
    completed = [e for e in parsed if e.get("outcome") == "completed"]
    effectiveness = len(completed) / max(len(parsed), 1)

    # ── Acceptance rate ────────────────────────────────────────────
    accepted = [e for e in parsed if e["action"] == "accepted"]
    acceptance_rate = len(accepted) / max(len(parsed), 1)

    # ── Behavior drift (sum of absolute deltas) ────────────────────
    drift = (abs(cons_delta) + abs(dw_delta) + abs(fat_delta)) / 3

    # ── Weekly trend: use last 7 days vs prior ─────────────────────
    week_ago = now - timedelta(days=7)
    recent_week = [p for p in parsed if p["ts"] > week_ago]
    prior = [p for p in parsed if p["ts"] <= week_ago]

    if recent_week and prior:
        r_cons = _consistency_score(recent_week)
        p_cons = _consistency_score(prior)
        weekly_trend = _trend_label(r_cons - p_cons, 0.08)
    else:
        # If all samples are inside or outside the wall-clock 7-day window,
        # fall back to the deterministic in-sample trend. This keeps replay and
        # tests stable when fixed historical fixtures age past the current week.
        weekly_trend = _trend_label(cons_delta, 0.08)

    return {
        "weekly_consistency_trend": weekly_trend,
        "deep_work_trend": _trend_label(dw_delta, 0.12),
        "fatigue_trend": "improving" if fat_delta < -0.05 else ("declining" if fat_delta > 0.10 else "stable"),
        "planning_effectiveness": round(effectiveness, 3),
        "recommendation_acceptance_rate": round(acceptance_rate, 3),
        "behavior_drift": round(drift, 3),
        "sample_count": len(parsed),
        "analysis_period_days": round(period_days, 1),
        "first_half_metrics": {
            "consistency": round(cons_1, 3),
            "deep_work_rate": round(dw_1, 3),
            "fatigue_skip_rate": round(fat_1, 3),
        },
        "second_half_metrics": {
            "consistency": round(cons_2, 3),
            "deep_work_rate": round(dw_2, 3),
            "fatigue_skip_rate": round(fat_2, 3),
        },
    }
