from datetime import datetime, timedelta, timezone

from src.core.state_engine import StateEngine
from src.core.temporal import TemporalSource, TimeBlock, TimeBlockType
from src.domain.course_topology import (
    chaoxing_scope_names,
    course_names_match,
    current_course_candidates,
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


def test_course_names_match_width_spacing_and_bracket_suffix():
    assert course_names_match("数据　结构（张老师）", "数据 结构")
    assert course_names_match("Unity应用实训", "虚拟现实技术（张辉）")
    assert not course_names_match("大学物理", "计算机网络")


def test_current_courses_prefer_temporal_blocks():
    engine = StateEngine()
    now = datetime.now(timezone.utc)
    engine._temporal_blocks["jwxt-1"] = TimeBlock(
        "jwxt-1",
        TemporalSource.JWXT,
        TimeBlockType.CLASS_LECTURE,
        now,
        now + timedelta(hours=1),
        "数据结构",
    )
    engine._state["schedule"] = {
        "today": {"entries": [{"course": "不应优先"}]},
    }

    names, source = current_course_candidates(engine)

    assert names == ["数据结构"]
    assert source == "temporal_blocks"


def test_current_courses_fall_back_to_schedule_then_jwxt_state():
    engine = StateEngine()
    engine._state["schedule"] = {
        "today": {"entries": [{"name": "操作系统"}]},
    }

    names, source = current_course_candidates(engine)

    assert names == ["操作系统"]
    assert source == "state.schedule"

    engine._state["schedule"] = {}
    engine._state["course"] = {
        "计算机网络": {
            "course_name": "计算机网络",
            "source": "jwxt",
            "active": True,
        }
    }
    names, source = current_course_candidates(engine)
    assert names == ["计算机网络"]
    assert source == "jwxt_course_state"
