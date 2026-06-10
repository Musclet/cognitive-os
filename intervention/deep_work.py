"""Deep work reminder intervention.

Triggers when planning/focus windows suggest deep work capacity
but no deep work has been detected recently.
Uses intervention type cooldown to avoid duplicate spam.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from intervention import Intervention


def evaluate_deep_work(
    derived_state: dict[str, Any],
    runtime_state: dict[str, Any],
) -> Intervention | None:
    """Evaluate whether to trigger a deep work reminder.

    Criteria:
    - planning has recommended windows tagged 'deep_work' or 'focused'
    - cognitive fatigue_risk is low (< 0.6)
    - No deep work tracked recently (behavior feedback_log)
    - Not in a social/travel block window
    """
    planning = derived_state.get("planning", {})
    cog = derived_state.get("cognition", {})
    adaptive = derived_state.get("adaptive_planning", {})

    fatigue = cog.get("fatigue_risk", 0)
    windows = planning.get("focus_windows", [])
    recommended = planning.get("recommended_windows", [])

    # Need focus or recommended deep_work windows
    has_deep_window = any(
        w.get("type") in ("deep_work", "focused") or w.get("quality") == "high"
        for w in windows + recommended
    )
    if not has_deep_window:
        return None

    # Skip if fatigue is too high
    if fatigue >= 0.6:
        return None

    # Check behavior log for recent deep work
    behavior = runtime_state.get("behavior", {})
    feedback_log = behavior.get("feedback_log", [])
    now = datetime.now(timezone.utc)
    recent_deep_work = False
    for entry in reversed(feedback_log[-20:]):
        outcome = entry.get("outcome", "")
        ts_str = entry.get("outcome_timestamp", "") or entry.get("timestamp", "")
        if outcome == "completed" and ts_str:
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                if (now - ts).total_seconds() < 14400:  # 4h
                    recent_deep_work = True
                    break
            except (ValueError, TypeError):
                continue

    if recent_deep_work:
        return None

    # Check social/travel constraints
    temporal_ctx = runtime_state.get("temporal", {}).get("context", {})
    if temporal_ctx.get("social_block_tonight") or temporal_ctx.get("travel_block_today"):
        return None

    intensity = adaptive.get("recommended_intensity", "normal")
    if intensity == "light":
        return None

    return Intervention(
        intervention_type="deep_work_reminder",
        message=(
            "现在有适合深度工作的窗口，建议安排一段连续时间处理核心任务。"
        ),
        priority=0.6,
        reason=(
            f"focus_window_available fatigue={fatigue:.2f} "
            f"intensity={intensity}"
        ),
    )
