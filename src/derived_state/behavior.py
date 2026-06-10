"""Behavioral derived state — feedback loop metrics.

Pure function over state dictionary → behavior metrics.
Deterministic, replay-safe, rule-based only.
"""

from __future__ import annotations

from typing import Any


def compute_behavior(state: dict[str, dict[str, dict[str, Any]]]) -> dict[str, Any]:
    """Compute behavioral metrics from feedback history.

    Reads behavior aggregate from state.

    Returns:
        execution_consistency, delay_tendency, fatigue_compliance_drop,
        deep_work_success_rate, planning_reliability.
    """
    behavior_data = state.get("behavior", {})
    current = behavior_data.get("current", {})
    feedbacks = current.get("feedback_log", [])

    if not feedbacks:
        return {
            "execution_consistency": 1.0,
            "delay_tendency": 0.0,
            "fatigue_compliance_drop": 0.0,
            "deep_work_success_rate": 1.0,
            "planning_reliability": 1.0,
            "total_recommendations": 0,
            "accepted_count": 0,
            "skipped_count": 0,
            "delayed_count": 0,
            "completed_count": 0,
            "abandoned_count": 0,
        }

    total = len(feedbacks)
    accepted = sum(1 for f in feedbacks if f.get("action") == "accepted")
    skipped = sum(1 for f in feedbacks if f.get("action") == "skipped")
    delayed = sum(1 for f in feedbacks if f.get("action") == "delayed")
    completed = sum(1 for f in feedbacks if f.get("outcome") == "completed")
    abandoned = sum(1 for f in feedbacks if f.get("outcome") == "abandoned")

    # execution_consistency: ratio of engaged (accepted+delayed) / on_time (accepted)
    if total > 0:
        execution_consistency = (accepted + delayed * 0.3) / total
        execution_consistency = min(1.0, execution_consistency)
    else:
        execution_consistency = 1.0

    # delay_tendency: ratio of delayed among engaged
    engaged = accepted + delayed
    if engaged > 0:
        delay_tendency = delayed / engaged
    else:
        delay_tendency = 0.0

    # fatigue_compliance_drop: compliance when fatigue was high vs low
    high_fatigue_feedbacks = [
        f for f in feedbacks
        if f.get("cognition_at_time", {}).get("fatigue_risk", 0) > 0.7
    ]
    low_fatigue_feedbacks = [
        f for f in feedbacks
        if f.get("cognition_at_time", {}).get("fatigue_risk", 0) <= 0.4
    ]

    def _compliance_rate(fb_list):
        if not fb_list:
            return 1.0
        a = sum(1 for f in fb_list if f.get("action") == "accepted")
        return a / len(fb_list)

    high_compliance = _compliance_rate(high_fatigue_feedbacks)
    low_compliance = _compliance_rate(low_fatigue_feedbacks)
    fatigue_compliance_drop = max(0.0, low_compliance - high_compliance)

    # deep_work_success_rate: completed tasks that were deep_work
    deep_work_feedbacks = [
        f for f in feedbacks
        if f.get("window_type") == "deep_work"
    ]
    if deep_work_feedbacks:
        dw_completed = sum(1 for f in deep_work_feedbacks if f.get("outcome") == "completed")
        deep_work_success_rate = dw_completed / len(deep_work_feedbacks)
    else:
        deep_work_success_rate = 1.0

    # planning_reliability: overall execution_consistency weighted by recency
    if total >= 3:
        recent_5 = feedbacks[-5:]
        recent_engaged = sum(1 for f in recent_5 if f.get("action") in ("accepted", "delayed"))
        recent_completed = sum(1 for f in recent_5 if f.get("outcome") == "completed")
        planning_reliability = (recent_engaged / len(recent_5) + recent_completed / max(1, recent_engaged)) / 2
        planning_reliability = min(1.0, planning_reliability)
    else:
        planning_reliability = execution_consistency

    return {
        "execution_consistency": round(execution_consistency, 3),
        "delay_tendency": round(delay_tendency, 3),
        "fatigue_compliance_drop": round(fatigue_compliance_drop, 3),
        "deep_work_success_rate": round(deep_work_success_rate, 3),
        "planning_reliability": round(planning_reliability, 3),
        "total_recommendations": total,
        "accepted_count": accepted,
        "skipped_count": skipped,
        "delayed_count": delayed,
        "completed_count": completed,
        "abandoned_count": abandoned,
    }
