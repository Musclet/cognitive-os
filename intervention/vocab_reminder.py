"""Vocabulary fragment reminder intervention.

Triggers only when:
- vocab remaining > 0
- there is an upcoming/current free slot 8-20 min or evening fallback
- no recent same-type reminder due to cooldown/budget
- pressure not too high for strong reminder

Messages are short and ambient. No calendar writes.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any

from intervention import Intervention

FRAGMENT_MIN = 8
FRAGMENT_MAX = 20
EVENING_HOUR = 21  # 9 PM local as evening fallback


def evaluate_vocab_reminder(
    derived_state: dict[str, Any],
    runtime_state: dict[str, Any],
) -> Intervention | None:
    """Evaluate vocabulary fragment reminder.

    Args:
        derived_state: From DERIVED_STATE_UPDATED event payload.
        runtime_state: Current runtime views (hydration, behavior, temporal).

    Returns:
        Intervention if conditions met, None otherwise.
    """
    cognition = derived_state.get("cognition", {})
    planning = derived_state.get("planning", {})
    vocab = cognition.get("vocab", {})
    remaining = vocab.get("remaining", 0)

    if remaining <= 0:
        return None

    pressure = cognition.get("stress_projection", 0)
    fatigue = cognition.get("fatigue_risk", 0)

    # Don't trigger when pressure is too high for strong reminder
    if pressure > 0.8:
        return None

    # Find suitable free slots from planning
    windows = planning.get("recommended_windows", [])
    temporal_ctx = runtime_state.get("temporal", {}).get("context", {})
    if isinstance(temporal_ctx, dict):
        social_tonight = temporal_ctx.get("social_block_tonight", False)
    else:
        social_tonight = False

    # Check for a suitable free slot 8-20 min
    fragment_slot = None
    for w in windows:
        dur = w.get("duration_minutes", 0)
        if FRAGMENT_MIN <= dur <= FRAGMENT_MAX:
            fragment_slot = w
            break

    # Evening fallback: if no suitable slot but it's late evening
    now_local = datetime.now(timezone.utc) + timedelta(hours=8)
    is_evening = now_local.hour >= EVENING_HOUR

    # Build message
    if fragment_slot and not social_tonight:
        boost = vocab.get("reminder_intensity_boost", 0)
        if boost > 0.1:
            return Intervention(
                intervention_type="vocab_reminder",
                message=(
                    f"现在有 {fragment_slot['duration_minutes']} 分钟碎片空档，"
                    f"适合清一轮复习词（剩 {remaining} 个）。"
                ),
                priority=0.45,
                reason=f"free_slot={fragment_slot['duration_minutes']}min remaining={remaining} slack_boost",
            )
        if fatigue > 0.6:
            return Intervention(
                intervention_type="vocab_reminder",
                message=(
                    f"有 {fragment_slot['duration_minutes']} 分钟空档。"
                    f"疲劳较高，只过一遍易忘词即可（剩 {remaining} 个）。"
                ),
                priority=0.35,
                reason=f"free_slot={fragment_slot['duration_minutes']}min remaining={remaining} high_fatigue",
            )
        return Intervention(
            intervention_type="vocab_reminder",
            message=(
                f"{fragment_slot['duration_minutes']} 分钟碎片时间，"
                f"可以刷 {remaining} 个待复习词。"
            ),
            priority=0.35,
            reason=f"free_slot={fragment_slot['duration_minutes']}min remaining={remaining}",
        )

    # Evening fallback
    if is_evening and remaining > 0 and not social_tonight:
        stale = vocab.get("stale", False)
        slack = vocab.get("slack", False)
        if slack and pressure < 0.6:
            return Intervention(
                intervention_type="vocab_reminder",
                message=(
                    f"背词连续下滑，今天先保底 10 分钟，不追量。"
                    f"（剩 {remaining} 个待复习）"
                ),
                priority=0.4,
                reason=f"evening_fallback remaining={remaining} slack",
            )
        if stale:
            return Intervention(
                intervention_type="vocab_reminder",
                message=(
                    f"背词数据已过时，明天同步后看看进度。"
                    f"今晚还有空就过一遍已学词。"
                ),
                priority=0.25,
                reason=f"evening_fallback stale_cache remaining={remaining}",
            )

    return None
