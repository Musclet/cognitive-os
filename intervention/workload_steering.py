
"""Workload steering intervention.

Triggers when deadline pressure is high and rising.
Goal: modulate task ingestion, not emotional comfort.
Subjective-aware: contextualizes steering when user has social plans.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from intervention import Intervention


def _check_social_plan(subjective_state: dict[str, dict]) -> bool:
    """Check if any active note mentions social plans."""
    social_kw = [
        "社交", "social", "聚会", "party", "饭局", "约", "饭",
        "见面", "meet", "hangout", "外出", "出门", "聚餐",
    ]
    now = datetime.now(timezone.utc)
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
            if any(kw in note.get("text", "").lower() for kw in social_kw):
                return True
    return False


def evaluate_workload_steering(
    derived_state: dict[str, Any],
    runtime_state: dict[str, Any],
) -> Intervention | None:
    """Evaluate whether to trigger workload steering.

    Criteria:
    - deadline_pressure.score > 0.7
    - trend is "critical" or "rising"
    - If social plan detected, contextualize message
    """
    dp = derived_state.get("deadline_pressure", {})
    score = dp.get("score", 0.0)
    trend = dp.get("trend", "stable")

    if score < 0.7:
        return None

    if trend not in ("critical", "rising"):
        return None

    active = dp.get("active_courses", 0)
    overdue = dp.get("overdue_count", 0)
    social_plan = _check_social_plan(runtime_state.get("subjective", {}))
    temporal_ctx = runtime_state.get("temporal", {}).get("context", {})
    travel_today = temporal_ctx.get("travel_block_today", False)

    # Social plan + critical: contextualized steering
    if social_plan and trend == "critical":
        return Intervention(
            intervention_type="workload_steering",
            message=(
                f"截止压力严重（{score:.0%}），但你今晚有社交安排。"
                f"优先处理最紧急任务，其余推迟。{active} 门课程中 {overdue} 条超期。"
            ),
            priority=0.75,
            reason=f"social_plan + critical_pressure={score:.2f}",
        )

    if travel_today and score > 0.6:
        return Intervention(
            intervention_type="workload_steering",
            message=f"你今天有出行安排，不适合高切换任务。当前压力 {score:.0%}，建议只保留一个主任务。",
            priority=0.65,
            reason=f"travel_today pressure={score:.2f}",
        )

    if trend == "critical":
        message = (
            f"Deadline pressure critical ({score:.0%}). "
            f"{active} courses active"
            + (f", {overdue} overdue." if overdue else ".")
            + " Dont start new tasks tonight."
        )
    else:
        message = (
            f"Workload rising ({score:.0%}). "
            f"{active} courses with pending work."
            + " Hold new tasks, focus on existing."
        )

    return Intervention(
        intervention_type="workload_steering",
        message=message,
        priority=0.7 if trend == "critical" else 0.5,
        reason=f"deadline_pressure={score:.2f} trend={trend} active={active} overdue={overdue}",
    )
