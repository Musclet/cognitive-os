from src.domain.course_topology import (
    chaoxing_scope_names,
    is_excluded_course,
    normalize_course_name,
)


def test_course_aliases_canonicalize_unity():
    assert normalize_course_name("Unity应用实训") == "虚拟现实技术（张辉）"
    assert normalize_course_name("虚拟现实技术实践", "张辉") == "虚拟现实技术（张辉）"
    assert normalize_course_name("虚拟现实技术实践", "胡珂") == "虚拟现实技术（胡珂）"
    assert normalize_course_name("虚拟现实技术") == "虚拟现实技术"


def test_excluded_courses():
    assert is_excluded_course("高等数学")
    assert is_excluded_course("大学英语")
    assert is_excluded_course("线性代数")
    assert not is_excluded_course("虚拟现实技术（张辉）")


def test_chaoxing_scope_expands_aliases_and_drops_noise():
    scope = chaoxing_scope_names(["虚拟现实技术实践", "大学英语"])
    assert "虚拟现实技术实践" in scope
    assert "大学英语" not in scope


def test_chaoxing_scope_expands_teacher_specific_vr():
    scope = chaoxing_scope_names(["虚拟现实技术（张辉）", "虚拟现实技术（胡珂）"])
    assert "Unity应用实训" in scope
    assert "张辉" in scope
    assert "胡珂" in scope
