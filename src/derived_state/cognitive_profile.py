"""Cognitive profile audit.

Small deterministic audit over event-derived state. It answers one question:
how much does the system currently know well enough to plan around?
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


def audit_cognitive_profile(
    state: dict[str, Any],
    derived: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.fromtimestamp(0, timezone.utc)
    derived = derived or {}

    memory_entries = []
    for view in state.get("memory", {}).values():
        memory_entries.extend(view.get("entries", []))

    mood_entries = []
    contexts = []
    notes = []
    for view in state.get("subjective", {}).values():
        mood_entries.extend(view.get("mood_history", []))
        contexts.extend(view.get("contexts", []))
        notes.extend(view.get("notes", []))

    feedbacks = state.get("behavior", {}).get("current", {}).get("feedback_log", [])
    art_sessions = state.get("art", {}).get("today", {}).get("progress", {}).get("sessions", [])
    completions = [f for f in feedbacks if f.get("outcome") == "completed"]

    input_count = len(memory_entries) + len(mood_entries) + len(contexts) + len(notes) + len(feedbacks)
    input_score = _score_count(input_count, 30)
    recency_score = _recency_score(memory_entries + mood_entries + contexts + notes + feedbacks, now)
    coverage_score = _coverage_score(memory_entries, mood_entries, contexts, feedbacks, art_sessions)
    consistency_score = _consistency_score(derived.get("behavior", {}), derived.get("reflection", {}))
    planning_score = _planning_score(state, derived, completions)

    maturity = round(
        input_score * 0.25
        + recency_score * 0.20
        + coverage_score * 0.25
        + consistency_score * 0.15
        + planning_score * 0.15
    )

    blind_spots = []
    if len(mood_entries) < 3:
        blind_spots.append("情绪样本不足")
    if len(contexts) + len(notes) < 5:
        blind_spots.append("日常情境样本不足")
    if len(feedbacks) < 5:
        blind_spots.append("完成/跳过/推迟反馈不足")
    if not art_sessions:
        blind_spots.append("画画进入状态的证据不足")
    if not memory_entries:
        blind_spots.append("认知学习记忆为空")

    known = []
    if state.get("homework"):
        known.append("课业压力")
    if state.get("temporal") or derived.get("temporal_projection"):
        known.append("时间占用")
    if mood_entries:
        known.append("近期情绪")
    if feedbacks:
        known.append("执行反馈")
    if art_sessions:
        known.append("画画进度")

    conclusion = _conclusion(maturity, known, blind_spots)
    return {
        "maturity_score": maturity,
        "scores": {
            "input_volume": round(input_score),
            "recency": round(recency_score),
            "coverage": round(coverage_score),
            "consistency": round(consistency_score),
            "planning_utility": round(planning_score),
        },
        "known_areas": known,
        "blind_spots": blind_spots,
        "sample_counts": {
            "memory": len(memory_entries),
            "mood": len(mood_entries),
            "context": len(contexts) + len(notes),
            "feedback": len(feedbacks),
            "art_sessions": len(art_sessions),
        },
        "conclusion": conclusion,
        "audited_at": now.isoformat(),
    }


def _score_count(count: int, target: int) -> float:
    return min(100.0, count / max(target, 1) * 100.0)


def _parse_ts(item: dict[str, Any]) -> datetime | None:
    for key in ("created_at", "recorded_at", "timestamp", "outcome_timestamp"):
        value = item.get(key)
        if not value:
            continue
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
    return None


def _recency_score(items: list[dict[str, Any]], now: datetime) -> float:
    timestamps = [ts for item in items if (ts := _parse_ts(item)) is not None]
    if not timestamps:
        return 0.0
    recent = [ts for ts in timestamps if now - ts <= timedelta(days=3)]
    week = [ts for ts in timestamps if now - ts <= timedelta(days=7)]
    return min(100.0, len(recent) * 18.0 + len(week) * 4.0)


def _coverage_score(memory, moods, contexts, feedbacks, art_sessions) -> float:
    score = 0.0
    if memory:
        score += 20
    if moods:
        score += 20
    if contexts:
        score += 20
    if feedbacks:
        score += 25
    if art_sessions:
        score += 15
    return min(score, 100.0)


def _consistency_score(behavior: dict[str, Any], reflection: dict[str, Any]) -> float:
    if behavior.get("total_recommendations", 0) == 0:
        return 20.0
    reliability = float(behavior.get("planning_reliability", 0.5) or 0.5) * 70
    drift = float(reflection.get("behavior_drift", 0.0) or 0.0)
    return max(0.0, min(100.0, reliability + (1.0 - min(drift, 1.0)) * 30))


def _planning_score(state: dict[str, Any], derived: dict[str, Any], completions: list[dict[str, Any]]) -> float:
    score = 0.0
    if state.get("homework"):
        score += 20
    if derived.get("temporal_projection", {}).get("total_blocks", 0):
        score += 20
    if state.get("vocab", {}).get("momo"):
        score += 15
    if state.get("art", {}).get("today", {}).get("plan"):
        score += 20
    score += min(25, len(completions) * 5)
    return min(score, 100.0)


def _conclusion(score: int, known: list[str], blind_spots: list[str]) -> str:
    known_text = "、".join(known[:4]) if known else "基础输入"
    if score >= 70:
        level = "已经能较稳定地辅助安排"
    elif score >= 40:
        level = "能做低风险安排，但仍需要你持续反馈"
    else:
        level = "还处在证据积累阶段"
    if blind_spots:
        return f"系统目前主要了解你的{known_text}，{level}；最大盲区是{blind_spots[0]}。"
    return f"系统目前主要了解你的{known_text}，{level}。"
