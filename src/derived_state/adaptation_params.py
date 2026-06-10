"""Adaptation Parameters — bounded numeric tuning from behavior + reflection.

Pure function. Returns parameter adjustments within safe ranges.
No self-modifying code. No rule changes. Just numbers.

Deterministic, replay-safe.
"""

from __future__ import annotations

from typing import Any


# ── Parameter bounds (hard safety limits) ──────────────────────────────

_PARAM_BOUNDS = {
    "deep_work_threshold":        (0.3, 0.8),   # Min success rate for deep_work promotion
    "overload_sensitivity":       (0.5, 1.5),   # Multiplier for overload detection
    "recovery_weight":            (0.3, 1.7),   # Weight of recovery suggestions
    "preferred_window_duration":  (20, 150),    # Target window in minutes
    "fatigue_penalty":            (0.5, 1.5),   # How much fatigue downgrades windows
}

# Default values
_DEFAULTS = {
    "deep_work_threshold": 0.5,
    "overload_sensitivity": 1.0,
    "recovery_weight": 1.0,
    "preferred_window_duration": 90,
    "fatigue_penalty": 1.0,
}

# ── Adaptation step sizes (how much to adjust per reflection cycle) ────
_STEP = {
    "deep_work_threshold": 0.05,
    "overload_sensitivity": 0.05,
    "recovery_weight": 0.05,
    "preferred_window_duration": 10,
    "fatigue_penalty": 0.05,
}

# Anti-oscillation: minimum samples before adaptation
_MIN_SAMPLES = 7


def compute_adapted_params(
    behavior: dict[str, Any],
    reflection: dict[str, Any],
) -> dict[str, Any]:
    """Compute adapted parameters from behavior and reflection.

    Args:
        behavior: From compute_behavior().
        reflection: From compute_reflection().
        previous_params: Previous adapted params for hysteresis (optional).

    Returns:
        Dict of param_name → value, plus metadata.
    """
    params = dict(_DEFAULTS)
    changes = []
    reasons = []

    total = behavior.get("total_recommendations", 0)
    if total < _MIN_SAMPLES:
        return {
            "params": params,
            "changes": [],
            "reasons": [f"Need {_MIN_SAMPLES} samples, have {total}. Using defaults."],
            "stable_since_samples": total,
        }

    dw_trend = reflection.get("deep_work_trend", "stable")
    dw_rate = behavior.get("deep_work_success_rate", 0.5)
    fcd = behavior.get("fatigue_compliance_drop", 0)
    dt = behavior.get("delay_tendency", 0)
    ec = behavior.get("execution_consistency", 0)
    drift = reflection.get("behavior_drift", 0)

    # ── Rule 1: Deep work threshold ────────────────────────────────
    # If DW success is declining → raise threshold (require more proof before promoting DW)
    # If DW success is improving and high → lower threshold
    if dw_trend == "declining" and dw_rate < 0.5:
        new_val = _clamp(
            params["deep_work_threshold"] + _STEP["deep_work_threshold"],
            "deep_work_threshold"
        )
        if new_val != params["deep_work_threshold"]:
            changes.append(f"deep_work_threshold: {params['deep_work_threshold']:.2f} → {new_val:.2f}")
            reasons.append(f"Deep work success declining ({dw_rate*100:.0f}%). Raising threshold.")
            params["deep_work_threshold"] = new_val
    elif dw_trend == "improving" and dw_rate > 0.6:
        new_val = _clamp(
            params["deep_work_threshold"] - _STEP["deep_work_threshold"],
            "deep_work_threshold"
        )
        if new_val != params["deep_work_threshold"]:
            changes.append(f"deep_work_threshold: {params['deep_work_threshold']:.2f} → {new_val:.2f}")
            reasons.append(f"Deep work success improving ({dw_rate*100:.0f}%). Lowering threshold.")
            params["deep_work_threshold"] = new_val

    # ── Rule 2: Fatigue penalty ────────────────────────────────────
    # If fatigue compliance drop is high → increase penalty (more aggressively downgrade)
    if fcd > 0.5:
        new_val = _clamp(
            params["fatigue_penalty"] + _STEP["fatigue_penalty"],
            "fatigue_penalty"
        )
        if new_val != params["fatigue_penalty"]:
            changes.append(f"fatigue_penalty: {params['fatigue_penalty']:.2f} → {new_val:.2f}")
            reasons.append(f"Fatigue compliance drop is {fcd*100:.0f}%. Increasing fatigue penalty.")
            params["fatigue_penalty"] = new_val
    elif fcd < 0.15:
        new_val = _clamp(
            params["fatigue_penalty"] - _STEP["fatigue_penalty"],
            "fatigue_penalty"
        )
        if new_val != params["fatigue_penalty"]:
            changes.append(f"fatigue_penalty: {params['fatigue_penalty']:.2f} → {new_val:.2f}")
            reasons.append("Fatigue compliance normalized. Reducing penalty.")
            params["fatigue_penalty"] = new_val

    # ── Rule 3: Recovery weight ────────────────────────────────────
    if reflection.get("fatigue_trend") == "declining":
        new_val = _clamp(
            params["recovery_weight"] + _STEP["recovery_weight"],
            "recovery_weight"
        )
        if new_val != params["recovery_weight"]:
            changes.append(f"recovery_weight: {params['recovery_weight']:.2f} → {new_val:.2f}")
            reasons.append("Fatigue trend declining. Increasing recovery weight.")
            params["recovery_weight"] = new_val
    elif reflection.get("fatigue_trend") == "improving":
        new_val = _clamp(
            params["recovery_weight"] - _STEP["recovery_weight"],
            "recovery_weight"
        )
        if new_val != params["recovery_weight"]:
            changes.append(f"recovery_weight: {params['recovery_weight']:.2f} → {new_val:.2f}")
            reasons.append("Fatigue trend improving. Reducing recovery weight.")
            params["recovery_weight"] = new_val

    # ── Rule 4: Window duration ────────────────────────────────────
    if dt > 0.4:
        # High delay → shorter windows
        new_val = _clamp(
            params["preferred_window_duration"] - _STEP["preferred_window_duration"],
            "preferred_window_duration"
        )
        if new_val != params["preferred_window_duration"]:
            changes.append(f"preferred_window_duration: {params['preferred_window_duration']} → {new_val}min")
            reasons.append(f"Delay tendency {dt*100:.0f}%. Shortening target windows.")
            params["preferred_window_duration"] = new_val
    elif dt < 0.15 and ec > 0.7:
        # Low delay + high consistency → longer windows
        new_val = _clamp(
            params["preferred_window_duration"] + _STEP["preferred_window_duration"],
            "preferred_window_duration"
        )
        if new_val != params["preferred_window_duration"]:
            changes.append(f"preferred_window_duration: {params['preferred_window_duration']} → {new_val}min")
            reasons.append(f"High consistency ({ec*100:.0f}%). Extending target windows.")
            params["preferred_window_duration"] = new_val

    # ── Rule 5: Overload sensitivity ───────────────────────────────
    # If behavior is drifting → be more sensitive to overload
    if drift > 0.2:
        new_val = _clamp(
            params["overload_sensitivity"] + _STEP["overload_sensitivity"],
            "overload_sensitivity"
        )
        if new_val != params["overload_sensitivity"]:
            changes.append(f"overload_sensitivity: {params['overload_sensitivity']:.2f} → {new_val:.2f}")
            reasons.append(f"Behavior drift detected ({drift:.2f}). Increasing overload sensitivity.")
            params["overload_sensitivity"] = new_val
    elif drift < 0.05:
        new_val = _clamp(
            params["overload_sensitivity"] - _STEP["overload_sensitivity"],
            "overload_sensitivity"
        )
        if new_val != params["overload_sensitivity"]:
            changes.append(f"overload_sensitivity: {params['overload_sensitivity']:.2f} → {new_val:.2f}")
            reasons.append("Behavior stable. Reducing overload sensitivity.")
            params["overload_sensitivity"] = new_val

    # ── Hysteresis: if no changes, note stability ──────────────────
    if not changes:
        reasons.append("All parameters stable. No adaptation needed.")

    return {
        "params": {k: round(v, 3) for k, v in params.items()},
        "changes": changes,
        "reasons": reasons,
        "stable_since_samples": total,
    }


def _clamp(value: float, param_name: str) -> float:
    """Clamp parameter value to safe bounds."""
    lo, hi = _PARAM_BOUNDS[param_name]
    return max(lo, min(hi, value))


def get_default_params() -> dict[str, float]:
    """Return default adaptation parameters."""
    return dict(_DEFAULTS)


def get_param_bounds() -> dict[str, tuple[float, float]]:
    """Return parameter bounds for inspection."""
    return dict(_PARAM_BOUNDS)
