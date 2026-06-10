"""Adaptive Planning — behavior-aware recommendation tuning.

Pure function over behavior + cognition → adaptation parameters.
Deterministic, replay-safe, rule-based only.
"""

from __future__ import annotations

from typing import Any


def compute_adaptive_planning(
    behavior: dict[str, Any],
    cognition: dict[str, Any],
    adapted_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute adaptation parameters from observed behavior.

    Args:
        behavior: Output from compute_behavior().
        cognition: Output from compute_cognition().

    Returns:
        recommended_intensity, preferred_window_type, adaptation_confidence,
        compliance_risk, patterns_detected, adjustment_reasons.
    """
    ec = behavior.get("execution_consistency", 0)
    dt = behavior.get("delay_tendency", 0)
    dw = behavior.get("deep_work_success_rate", 0)
    fcd = behavior.get("fatigue_compliance_drop", 0)
    pr = behavior.get("planning_reliability", 0)
    total = behavior.get("total_recommendations", 0)

    sp = cognition.get("stress_projection", 0)
    fr = cognition.get("fatigue_risk", 0)

    # Adapted parameter thresholds (with safe defaults)
    params = adapted_params.get("params", {}) if adapted_params else {}
    dw_threshold = params.get("deep_work_threshold", 0.5)
    fatigue_weight = params.get("fatigue_penalty", 1.0)

    patterns = []
    reasons = []

    # ── Defaults ──────────────────────────────────────────────────
    intensity = "normal"
    preferred_window = "standard"
    confidence = 0.5
    compliance_risk = 0.3

    # ── Rule 1: deep_work_success_rate low → downgrade ────────────
    if total >= 3 and dw < dw_threshold:
        patterns.append("deep_work_resistant")
        reasons.append(f"Deep work completion rate is low ({dw*100:.0f}%). Preferring shorter windows.")
        preferred_window = "standard"
        intensity = "reduced"
        confidence = min(confidence + 0.15, 1.0)

    # ── Rule 2: delay_tendency high → shorter windows ─────────────
    if total >= 3 and dt > 0.4:
        patterns.append("chronic_delayer")
        reasons.append(f"High delay tendency ({dt*100:.0f}%). Recommending quick, immediate tasks.")
        preferred_window = "quick"
        intensity = "light"
        compliance_risk = min(compliance_risk + 0.25, 1.0)
        confidence = min(confidence + 0.15, 1.0)

    # ── Rule 3: fatigue compliance drop high → protect recovery ───
    if total >= 3 and fcd > 0.3:
        patterns.append("fatigue_sensitive")
        reasons.append(f"Compliance drops {fcd*100:.0f}% under fatigue. Adding recovery protection.")
        if intensity == "normal":
            intensity = "reduced"
        compliance_risk = min(compliance_risk + 0.2, 1.0)
        confidence = min(confidence + 0.1, 1.0)

    # ── Rule 4: high consistency + low delay → deep work ready ────
    if total >= 3 and ec > 0.7 and dt < 0.2 and dw > max(0.6, dw_threshold + 0.1):
        patterns.append("deep_work_ready")
        reasons.append(f"High consistency ({ec*100:.0f}%) with good deep work rate. Promoting focus windows.")
        preferred_window = "deep_work"
        intensity = "focused"
        compliance_risk = max(compliance_risk - 0.15, 0.0)
        confidence = min(confidence + 0.2, 1.0)

    # ── Rule 5: planning_reliability low → gentle planning ────────
    if total >= 5 and pr < 0.4:
        if "chronic_delayer" not in patterns:
            patterns.append("unreliable_planner")
        reasons.append(f"Planning reliability is low ({pr*100:.0f}%). Reducing recommendation aggressiveness.")
        if intensity in ("normal", "focused"):
            intensity = "reduced"
        compliance_risk = min(compliance_risk + 0.2, 1.0)
        confidence = min(confidence + 0.15, 1.0)

    # ── Rule 6: too few data points → low confidence ──────────────
    if total < 3:
        confidence = max(confidence - 0.3, 0.1)
        reasons.append(f"Only {total} feedback samples. Low confidence in adaptation.")

    # ── Rule 7: oscillation guard ──────────────────────────────────
    # If there's a mix of deep_work_ready and deep_work_resistant, prefer standard
    if "deep_work_ready" in patterns and "deep_work_resistant" in patterns:
        preferred_window = "standard"
        intensity = "normal"
        reasons.append("Mixed deep work signals detected. Defaulting to standard windows.")
        confidence = max(confidence - 0.1, 0.1)

    return {
        "recommended_intensity": intensity,
        "preferred_window_type": preferred_window,
        "adaptation_confidence": round(confidence, 3),
        "compliance_risk": round(compliance_risk, 3),
        "patterns_detected": patterns,
        "adjustment_reasons": reasons,
    }
