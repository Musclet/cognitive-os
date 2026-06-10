"""Course topology rules.

This keeps external course names aligned with the user's real course map.
"""

from __future__ import annotations


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
