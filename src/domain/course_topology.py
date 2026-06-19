"""Course topology rules.

This keeps external course names aligned with the user's real course map.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any


COURSE_ALIASES = {
    "Unity应用实训": "虚拟现实技术（张辉）",
    "unity应用实训": "虚拟现实技术（张辉）",
    "UNITY应用实训": "虚拟现实技术（张辉）",
}

EXCLUDED_COURSES = {
    "高等数学",
    "大学英语",
    "线性代数",
}


def infer_teacher(name: str | None, teacher: str | None = None) -> str:
    """Infer teacher from explicit teacher field or course text."""
    explicit = (teacher or "").strip()
    if explicit:
        return explicit
    text = (name or "").strip()
    for candidate in ("张辉", "胡珂"):
        if candidate in text:
            return candidate
    return ""


def normalize_course_name(name: str | None, teacher: str | None = None) -> str:
    """Return the canonical course name for display and state."""
    course = (name or "").strip()
    if not course:
        return ""
    if course in {"虚拟现实技术（张辉）", "虚拟现实技术（胡珂）"}:
        return course
    if course in COURSE_ALIASES:
        return COURSE_ALIASES[course]

    course_teacher = infer_teacher(course, teacher)
    if course in {"虚拟现实技术", "虚拟现实技术实践"}:
        if "张辉" in course_teacher:
            return "虚拟现实技术（张辉）"
        if "胡珂" in course_teacher:
            return "虚拟现实技术（胡珂）"
        return course
    return course


def is_excluded_course(name: str | None) -> bool:
    """Whether a course should be outside the active runtime scope."""
    course = normalize_course_name(name)
    return course in EXCLUDED_COURSES


def chaoxing_scope_names(course_names: list[str]) -> list[str]:
    """Expand canonical course names to the names needed for Chaoxing matching."""
    scope: list[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        if value and value not in seen:
            seen.add(value)
            scope.append(value)

    for name in course_names:
        if is_excluded_course(name):
            continue
        canonical = normalize_course_name(name)
        add(canonical)
        if canonical == "虚拟现实技术（张辉）":
            add("Unity应用实训")
            add("张辉")
        elif canonical == "虚拟现实技术（胡珂）":
            add("胡珂")
        else:
            for alias, target in COURSE_ALIASES.items():
                if target == canonical:
                    add(alias)
    return scope


def course_name_match_keys(
    name: str | None,
    teacher: str | None = None,
) -> set[str]:
    """Return tolerant comparison keys without changing the display name."""
    canonical = normalize_course_name(name, teacher)
    normalized = unicodedata.normalize("NFKC", canonical).casefold().strip()
    normalized = re.sub(r"\s+", " ", normalized)
    if not normalized:
        return set()

    keys = {normalized, normalized.replace(" ", "")}
    without_suffix = re.sub(r"\s*[\(\[【][^\)\]】]+[\)\]】]\s*$", "", normalized)
    without_suffix = without_suffix.strip()
    if without_suffix:
        keys.add(without_suffix)
        keys.add(without_suffix.replace(" ", ""))
    return {key for key in keys if key}


def course_names_match(
    left: str | None,
    right: str | None,
    *,
    left_teacher: str | None = None,
    right_teacher: str | None = None,
) -> bool:
    """Match course names across minor spacing, width and suffix differences."""
    left_keys = course_name_match_keys(left, left_teacher)
    right_keys = course_name_match_keys(right, right_teacher)
    if not left_keys or not right_keys:
        return False
    if left_keys & right_keys:
        return True
    return any(
        min(len(left_key), len(right_key)) >= 4
        and (left_key in right_key or right_key in left_key)
        for left_key in left_keys
        for right_key in right_keys
    )


def current_course_candidates(state_engine: Any) -> tuple[list[str], str]:
    """Read current course names in priority order from durable schedule state."""
    if state_engine is None:
        return [], ""

    temporal_names: list[str] = []
    for block in state_engine.get_temporal_blocks():
        source = str(getattr(block, "source", ""))
        block_type = str(getattr(block, "block_type", ""))
        if source != "jwxt" or block_type not in {
            "class_lecture",
            "class_lab",
            "course",
            "schedule",
            "schedule_item",
        }:
            continue
        temporal_names.append(str(getattr(block, "title", "")))
    names = _dedupe_course_names(temporal_names)
    if names:
        return names, "temporal_blocks"

    schedule_names: list[str] = []
    for view in state_engine.get_all("schedule").values():
        if not isinstance(view, dict):
            continue
        entries = view.get("entries", [])
        if isinstance(entries, list):
            for entry in entries:
                if isinstance(entry, dict):
                    schedule_names.append(
                        str(entry.get("course") or entry.get("title") or entry.get("name") or "")
                    )
    names = _dedupe_course_names(schedule_names)
    if names:
        return names, "state.schedule"

    jwxt_names = [
        str(view.get("course_name", ""))
        for view in state_engine.get_all("course").values()
        if isinstance(view, dict)
        and view.get("active", True)
        and str(view.get("source", "")) == "jwxt"
    ]
    names = _dedupe_course_names(jwxt_names)
    if names:
        return names, "jwxt_course_state"
    return [], ""


def _dedupe_course_names(names: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for name in names:
        canonical = normalize_course_name(name)
        keys = course_name_match_keys(canonical)
        key = min(keys, key=len) if keys else ""
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(canonical)
    return result
