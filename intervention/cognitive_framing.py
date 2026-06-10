
"""Cognitive framing intervention ? minimal, derived-state-triggered only.

No emotional AI. No chat personality. No continuous output.
Only framing statements tied to specific cognitive states.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from intervention import Intervention


def evaluate_cognitive_framing(
    derived_state: dict[str, Any],
    runtime_state: dict[str, Any],
) -> Intervention | None:
    """Evaluate cognitive framing.

    Criteria:
    - workload_density high + late activity pattern
    - deadline_pressure critical + high active courses

    Returns brief framing statement, not emotional dialogue.
    """
    dp = derived_state.get("deadline_pressure", {})
    wl = derived_state.get("workload_density", {})

    dp_score = dp.get("score", 0.0)
    wl_score = wl.get("score", 0.0)
    capacity_pressure = wl.get("capacity_pressure", 0.0)

    subj = _read_subjective(runtime_state.get("subjective", {}))
    temporal_ctx = runtime_state.get("temporal", {}).get("context", {})

    if temporal_ctx.get("social_block_tonight"):
        return Intervention(
            intervention_type="cognitive_framing",
            message="你今晚已有聚餐安排，建议把任务压到出门前的小块时间。",
            priority=0.62,
            reason="calendar social_block_tonight",
        )

    if temporal_ctx.get("workout_block_later"):
        return Intervention(
            intervention_type="cognitive_framing",
            message="你晚些时候有训练安排，现在补水会降低训练时的疲劳感。",
            priority=0.58,
            reason="calendar workout_block_later",
        )

    if temporal_ctx.get("travel_block_today"):
        return Intervention(
            intervention_type="cognitive_framing",
            message="你今天有出行安排，不适合安排高切换任务，建议只保留一个主任务。",
            priority=0.6,
            reason="calendar travel_block_today",
        )

    # Scenario 0: social plan today + high workload
    if subj.get("social_plan_today") and wl_score > 0.5:
        return Intervention(
            intervention_type="cognitive_framing",
            message=f"你今晚已有社交安排，建议降低额外认知负载。当前负载 {wl_score:.0%}，优先完成核心任务。",
            priority=0.55,
            reason=f"social_plan_today workload={wl_score:.2f}",
        )

    # Scenario 0b: low mood + moderate+ pressure
    mood = subj.get("current_mood")
    if mood is not None and mood <= 3 and dp_score > 0.5:
        return Intervention(
            intervention_type="cognitive_framing",
            message=f"当前压力 {dp_score:.0%}，情绪偏低。建议小步推进，不设过高期待。",
            priority=0.6,
            reason=f"low_mood={mood} pressure={dp_score:.2f}",
        )

    # Scenario 1: high workload, high capacity pressure
    if wl_score > 0.6 and capacity_pressure > 0.7:
        return Intervention(
            intervention_type="cognitive_framing",
            message="Restraint isnt suppression. Its preserving long-term action capacity.",
            priority=0.45,
            reason=f"high_workload={wl_score:.2f} capacity_pressure={capacity_pressure:.2f}",
        )

    # Scenario 2: critical deadline pressure
    if dp_score > 0.85:
        active = dp.get("active_courses", 0)
        return Intervention(
            intervention_type="cognitive_framing",
            message=f"{active} courses active. Sequence matters more than speed. One at a time.",
            priority=0.5,
            reason=f"critical_pressure={dp_score:.2f} courses={active}",
        )

    return None


def _read_subjective(subjective_state: dict[str, dict]) -> dict[str, Any]:
    """Extract subjective context for intervention personalization."""
    modifiers = {"current_mood": None, "social_plan_today": False}
    social_kw = [
        "社交", "social", "聚会", "party", "饭局", "约", "饭",
        "见面", "meet", "hangout", "外出", "出门", "聚餐",
    ]
    now = datetime.now(timezone.utc)
    for user_id, view in subjective_state.items():
        history = view.get("mood_history", [])
        if history:
            modifiers["current_mood"] = history[-1].get("score")
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
                modifiers["social_plan_today"] = True
                break
    return modifiers
