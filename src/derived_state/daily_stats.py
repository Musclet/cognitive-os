"""Lightweight daily profile stats — deterministic, state-derived, replay-safe.

Computes compact stats from existing state/events for nightly review
and writes into ## 系统观察 in Obsidian.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def compute_daily_stats(
    state: dict[str, Any],
    derived: dict[str, Any],
    target_date: datetime | None = None,
) -> dict[str, Any]:
    """Compute lightweight deterministic stats from current state.

    Returns dict with:
      - art_minutes: actual art minutes today (int)
      - deviation_count: count of deviations today (int)
      - deviation_reasons: list of reason strings
      - vocab_finished / vocab_total / vocab_remaining: if available
      - fitness_done: bool, whether fitness was completed today
      - mood_latest / mood_avg: latest and average mood scores if available
      - coding_drift: bool, whether coding/vibecoding was mentioned as drift
    """
    art = state.get("art", {}).get("today", {})
    progress = art.get("progress", {})
    plan = art.get("plan", {})

    # Art minutes
    art_minutes = int(progress.get("completed_minutes", 0) or 0)
    art_target = int(plan.get("target_minutes", 0) or 0)

    # Behavior deviations
    behavior = derived.get("behavior", {})
    feedback_log = behavior.get("feedback_log", [])
    deviation_count = 0
    deviation_reasons = []
    coding_drift = False
    fitness_done = False
    for entry in feedback_log:
        outcome = entry.get("outcome", "")
        ts_str = entry.get("outcome_timestamp", "") or entry.get("timestamp", "")
        if ts_str:
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                if target_date and ts.date() != target_date.date():
                    continue
            except (ValueError, TypeError):
                continue
        if outcome in ("abandoned", "skipped"):
            deviation_count += 1
            reason = entry.get("reason", "") or entry.get("text", "")
            if reason:
                deviation_reasons.append(reason)
            if "coding" in reason.lower() or "vibecoding" in reason.lower() or "代码" in reason or "写代码" in reason:
                coding_drift = True
        if outcome == "completed":
            text = entry.get("reason", "") or entry.get("text", "")
            if "健身" in text or "fitness" in text.lower():
                fitness_done = True

    # Vocab
    vocab = state.get("vocab", {}).get("momo", {})
    vocab_today = vocab.get("today", {}) if isinstance(vocab, dict) else {}
    vocab_finished = int(vocab_today.get("finished", 0) or 0)
    vocab_total = int(vocab_today.get("total", 0) or 0)
    vocab_remaining = int(vocab_today.get("remaining", 0) or 0)

    # Mood
    subjective = state.get("subjective", {})
    mood_latest = None
    mood_avg = None
    mood_scores = []
    for uid, view in subjective.items():
        history = view.get("mood_history", [])
        for h in history:
            ts_str = h.get("recorded_at", "")
            if ts_str:
                try:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    if target_date and ts.date() != target_date.date():
                        continue
                except (ValueError, TypeError):
                    continue
            score = h.get("score")
            if score is not None:
                mood_scores.append(int(score))
    if mood_scores:
        mood_latest = mood_scores[-1]
        mood_avg = round(sum(mood_scores) / len(mood_scores), 1)

    return {
        "art_minutes": art_minutes,
        "art_target": art_target,
        "deviation_count": deviation_count,
        "deviation_reasons": deviation_reasons[:3],
        "vocab_finished": vocab_finished,
        "vocab_total": vocab_total,
        "vocab_remaining": vocab_remaining,
        "fitness_done": fitness_done,
        "mood_latest": mood_latest,
        "mood_avg": mood_avg,
        "coding_drift": coding_drift,
    }


def format_stats_line(stats: dict[str, Any]) -> str:
    """Format daily stats into a concise single line for ## 系统观察."""
    parts = []

    # Art
    art_min = stats.get("art_minutes", 0)
    art_tgt = stats.get("art_target", 0)
    if art_tgt > 0:
        pct = int(art_min / art_tgt * 100)
        parts.append(f"画画 {art_min}/{art_tgt}min ({pct}%)")
    elif art_min > 0:
        parts.append(f"画画 {art_min}min")
    else:
        parts.append("画画 0min")

    # Deviations
    dev_count = stats.get("deviation_count", 0)
    if dev_count > 0:
        dev_reasons = stats.get("deviation_reasons", [])
        reasons_str = f"「{'、'.join(dev_reasons)}」" if dev_reasons else ""
        parts.append(f"偏离 {dev_count} 次{reasons_str}")

    # Vocab
    vf = stats.get("vocab_finished", 0)
    vt = stats.get("vocab_total", 0)
    vr = stats.get("vocab_remaining", 0)
    if vt > 0:
        parts.append(f"背词 {vf}/{vt}（剩 {vr}）")

    # Fitness
    if stats.get("fitness_done"):
        parts.append("健身 ✅")
    else:
        parts.append("健身 ❌")

    # Mood
    ml = stats.get("mood_latest")
    ma = stats.get("mood_avg")
    if ml is not None:
        mood_str = f"心情 {ml}"
        if ma is not None:
            mood_str += f"（均 {ma}）"
        parts.append(mood_str)

    # Coding drift
    if stats.get("coding_drift"):
        parts.append("vibecoding ⚠")

    return " | ".join(parts) if parts else "数据不足"
