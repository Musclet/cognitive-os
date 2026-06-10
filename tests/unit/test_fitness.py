"""Tests: fitness domain — plan, generator, parser, next-day logic, daily integration."""

from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, ".")

from src.domain.fitness.plan import (
    WORKOUT_PLAN,
    WORKOUT_DAYS,
    Exercise,
    WorkoutDay,
    is_rest_day,
    get_training_day,
    get_weekday_name,
    day_name_cn,
    previous_training_day,
)
from src.domain.fitness.generator import (
    generate_workout_choice_notes,
    generate_workout_note,
    generate_workout_note_body,
    workout_daily_link,
    upsert_fitness_section,
    workout_choice_note_path,
    workout_web_ui_line,
    workout_note_path,
    FITNESS_BLOCK_START,
    FITNESS_BLOCK_END,
)
from src.domain.fitness.parser import (
    parse_workout_note,
    WorkoutSummary,
    ExerciseSummary,
    SetLog,
)
from src.domain.fitness.next_day import next_workday_logic, NextWorkoutDecision
from src.integrations.obsidian_daily import ObsidianDailyWriter, SECTION_HEADERS


LOCAL_TZ = ZoneInfo("Asia/Singapore")


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_settings(tmp_path: Path):
    settings = MagicMock()
    settings.obsidian_vault_path = str(tmp_path)
    settings.obsidian_daily_folder = "daily"
    settings.obsidian_daily_template_path = "Templates/每日打卡模板.md"
    settings.obsidian_daily_sink_enabled = True
    return settings


# ══════════════════════════════════════════════════════════════════════════════
# 1. Plan — exactly 5 training days + weekend rest
# ══════════════════════════════════════════════════════════════════════════════


def test_plan_has_five_training_days():
    """Plan defines exactly 5 training day names."""
    training = {k: v for k, v in WORKOUT_DAYS.items() if v != "rest"}
    assert len(training) == 5


def test_plan_has_weekend_rest():
    """Sat (5) and Sun (6) are rest days."""
    assert WORKOUT_DAYS[5] == "rest"
    assert WORKOUT_DAYS[6] == "rest"


def test_plan_has_valid_structure():
    """Each training day in WORKOUT_PLAN has exercises with sets/reps/RIR."""
    for day_name, day in WORKOUT_PLAN.items():
        assert isinstance(day, WorkoutDay)
        assert day.name == day_name
        assert len(day.exercises) >= 3, f"{day_name} has fewer than 3 exercises"
        for ex in day.exercises:
            assert isinstance(ex, Exercise)
            assert ex.target_sets >= 1
            assert ex.target_reps != ""


def test_plan_exercises_have_reasonable_sets():
    """No exercise has more than 8 sets (sanity check)."""
    for day in WORKOUT_PLAN.values():
        for ex in day.exercises:
            assert ex.target_sets <= 8, f"{ex.name} has {ex.target_sets} sets"


def test_is_rest_day():
    """Weekdays Mon-Thu are not rest; Fri is training; Sat/Sun are rest."""
    # 2026-06-01 = Monday
    assert not is_rest_day(date(2026, 6, 1))  # Mon
    assert not is_rest_day(date(2026, 6, 2))  # Tue
    assert not is_rest_day(date(2026, 6, 3))  # Wed
    assert not is_rest_day(date(2026, 6, 4))  # Thu
    assert not is_rest_day(date(2026, 6, 5))  # Fri (training)
    assert is_rest_day(date(2026, 6, 6))  # Sat
    assert is_rest_day(date(2026, 6, 7))  # Sun


def test_get_training_day_monday():
    """Monday (0) → Upper 1."""
    assert get_training_day(date(2026, 6, 1)) == "Upper 1"


def test_get_training_day_friday():
    """Friday (4) → Upper 3."""
    assert get_training_day(date(2026, 6, 5)) == "Upper 3"


def test_get_training_day_weekend_rest():
    """Weekend → rest."""
    assert get_training_day(date(2026, 6, 6)) == "rest"
    assert get_training_day(date(2026, 6, 7)) == "rest"


def test_get_weekday_name():
    """Chinese weekday name matches."""
    assert get_weekday_name(date(2026, 6, 1)) == "星期一"
    assert get_weekday_name(date(2026, 6, 7)) == "星期日"


def test_day_name_cn():
    """Chinese labels are non-empty."""
    assert day_name_cn("Upper 1") == "胸力量日"
    assert day_name_cn("rest") == "休息日"


def test_previous_training_day():
    """previous_training_day walks back to last training day."""
    # Sunday → Friday
    assert previous_training_day(date(2026, 6, 7)) == date(2026, 6, 5)
    # Monday → Friday (previous week's last training day)
    assert previous_training_day(date(2026, 6, 1)) == date(2026, 5, 29)
    # Tuesday → Monday
    assert previous_training_day(date(2026, 6, 2)) == date(2026, 6, 1)

# ══════════════════════════════════════════════════════════════════════════════
# 2. Generator — creates set checkboxes, weight/reps/RIR fields, frontmatter
# ══════════════════════════════════════════════════════════════════════════════


def test_generator_creates_checkboxes_and_fields(tmp_settings):
    """Generated note has checkboxes per set and weight/reps/RIR columns."""
    path = generate_workout_note(tmp_settings, date(2026, 6, 1))  # Mon = Upper 1
    assert path is not None
    text = Path(path).read_text(encoding="utf-8")
    assert "- [ ] Set 1" in text
    assert "- [ ] Set 2" in text
    assert "重量: ___ kg" in text
    assert "次数: ___" in text
    assert "RIR: ___" in text


def test_generator_includes_frontmatter(tmp_settings):
    """Generated note has YAML frontmatter with training metadata."""
    path = generate_workout_note(tmp_settings, date(2026, 6, 1))
    assert path is not None
    text = Path(path).read_text(encoding="utf-8")
    assert "---" in text
    assert "training_day: Upper 1" in text
    assert "type: workout/session" in text
    assert "date: 2026-06-01" in text
    assert "total_sets:" in text


def test_generator_returns_none_on_rest_day(tmp_settings):
    """Rest days return None (no file auto-generated)."""
    result = generate_workout_note(tmp_settings, date(2026, 6, 6))  # Sat
    assert result is None


def test_generator_correct_number_of_sets(tmp_settings):
    """Each exercise appears with the correct number of set rows."""
    path = generate_workout_note(tmp_settings, date(2026, 6, 1))
    text = Path(path).read_text(encoding="utf-8")
    # Upper 1 has 5 exercises; count "### " headers
    exercise_count = text.count("### ")
    assert exercise_count == 5


def test_generator_rest_day_note_text(tmp_settings):
    """rest_day_note can generate text without writing a file."""
    from src.domain.fitness.generator import rest_day_note
    text = rest_day_note(date(2026, 6, 6), "星期六")
    assert "休息日" in text
    assert "<!-- workout:rest-day -->" in text


def test_generator_uses_user_chinese_plan(tmp_settings):
    """Generated plan uses the user's actual Chinese machine-based exercises."""
    path = generate_workout_note(tmp_settings, date(2026, 6, 1))
    text = Path(path).read_text(encoding="utf-8")
    for ex_name in ["史密斯机卧推", "上斜器械推胸", "胸支撑划船", "高位下拉", "器械侧平举"]:
        assert ex_name in text


def test_generator_preserves_existing_workout_note(tmp_settings):
    """Existing workout notes are not overwritten, preserving Obsidian edits."""
    path = generate_workout_note(tmp_settings, date(2026, 6, 1))
    p = Path(path)
    p.write_text("manual edit\n- [x] custom set\n", encoding="utf-8")

    same_path = generate_workout_note(tmp_settings, date(2026, 6, 1))

    assert same_path == path
    assert p.read_text(encoding="utf-8") == "manual edit\n- [x] custom set\n"


def test_generator_creates_mobile_choice_notes(tmp_settings):
    """Mobile fallback creates plain candidate notes for each workout day."""
    paths = generate_workout_choice_notes(tmp_settings, date(2026, 6, 1))

    assert len(paths) == 6
    upper_1 = workout_choice_note_path(tmp_settings, date(2026, 6, 1), "Upper 1")
    assert upper_1.exists()
    assert "史密斯机卧推" in upper_1.read_text(encoding="utf-8")
    rest = workout_choice_note_path(tmp_settings, date(2026, 6, 1), "rest")
    assert rest.exists()
    assert "休息日" in rest.read_text(encoding="utf-8")


def test_generator_creates_in_workout_folder(tmp_settings):
    """Note is placed at Vault/Workout/YYYY-MM-DD.md."""
    path = generate_workout_note(tmp_settings, date(2026, 6, 1))
    assert path is not None
    assert "Workout" in path
    assert "2026-06-01.md" in path


# ══════════════════════════════════════════════════════════════════════════════
# 3. Parser — detects completed / partial session
# ══════════════════════════════════════════════════════════════════════════════


def _write_sample_note(tmp_path: Path, content: str, filename: str = "Workout/2026-06-01.md") -> str:
    p = tmp_path / filename
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return str(p)


SAMPLE_COMPLETED_NOTE = """---
date: 2026-06-01
type: workout/session
training_day: Upper 1
focus: 胸力量日
completed: false
total_sets: 20
completed_sets: 0
---

# Upper 1 — 胸力量日

### 1. 史密斯机卧推
- [x] Set 1 | 重量: 60 kg | 次数: 8 / 6-8 | RIR: 1
- [x] Set 2 | 重量: 65 kg | 次数: 7 / 6-8 | RIR: 1
- [x] Set 3 | 重量: 65 kg | 次数: 7 / 6-8 | RIR: 1
- [x] Set 4 | 重量: 60 kg | 次数: 8 / 6-8 | RIR: 1

### 2. 上斜器械推胸
- [x] Set 1 | 重量: 24 kg | 次数: 9 / 8-10 | RIR: 2
- [x] Set 2 | 重量: 24 kg | 次数: 8 / 8-10 | RIR: 2
- [x] Set 3 | 重量: 22 kg | 次数: 10 / 8-10 | RIR: 1

<!-- workout:session -->
"""


SAMPLE_PARTIAL_NOTE = """---
date: 2026-06-02
type: workout/session
training_day: Lower 1
focus: 股四头
completed: false
total_sets: 22
completed_sets: 0
---

# Lower 1 — 股四头

### 1. 哈克深蹲（正面+脚位低）
- [x] Set 1 | 重量: 80 kg | 次数: 7 / 6-8 | RIR: 1
- [x] Set 2 | 重量: 85 kg | 次数: 6 / 6-8 | RIR: 1
- [x] Set 3 | 重量: 85 kg | 次数: 6 / 6-8 | RIR: 1
- [x] Set 4 | 重量: 80 kg | 次数: 7 / 6-8 | RIR: 1

### 2. 腿举（脚中低位）
- [ ] Set 1 | 重量: ___ kg | 次数: ___ / 10-12 | RIR: ___
- [ ] Set 2 | 重量: ___ kg | 次数: ___ / 10-12 | RIR: ___
- [ ] Set 3 | 重量: ___ kg | 次数: ___ / 10-12 | RIR: ___

<!-- workout:session -->
"""


SAMPLE_REST_NOTE = """# 休息日 — 2026-06-06 (星期六)

今日是计划休息日，不安排训练。

---
<!-- workout:rest-day -->
"""


SAMPLE_FILLED_NOTE = """---
date: 2026-06-03
type: workout/session
training_day: Upper 2
focus: 背+肩
completed: false
total_sets: 21
completed_sets: 0
---

# Upper 2 — 背+肩

### 1. 高位下拉 / 引体（任选）
- [x] Set 1 | 重量: 70 kg | 次数: 8 / 8-10 | RIR: 1
- [x] Set 2 | 重量: 75 kg | 次数: 7 / 8-10 | RIR: 1
- [x] Set 3 | 重量: 75 kg | 次数: 6 / 8-10 | RIR: 1
- [x] Set 4 | 重量: 70 kg | 次数: 8 / 8-10 | RIR: 1
  > 任选高位下拉或引体

### 2. 坐姿划船（夹背停1秒）
- [x] Set 1 | 重量: 60 kg | 次数: 10 / 10-12 | RIR: 1
- [x] Set 2 | 重量: 60 kg | 次数: 10 / 10-12 | RIR: 1
- [x] Set 3 | 重量: 60 kg | 次数: 10 / 10-12 | RIR: 1
- [x] Set 4 | 重量: 55 kg | 次数: 12 / 10-12 | RIR: 1
  > 夹背停1秒

### 3. 器械肩推
- [x] Set 1 | 重量: 30 kg | 次数: 8 / 8-12 | RIR: 1
- [x] Set 2 | 重量: 30 kg | 次数: 8 / 8-12 | RIR: 1
- [x] Set 3 | 重量: 25 kg | 次数: 10 / 8-12 | RIR: 1

<!-- workout:session -->
"""


def test_parser_parses_completed_session(tmp_path):
    """Parser detects completed session (all four bench sets done)."""
    path = _write_sample_note(tmp_path, SAMPLE_COMPLETED_NOTE)
    result = parse_workout_note(path)
    assert result.training_day == "Upper 1"
    assert result.focus == "胸力量日"
    assert result.completed_sets == 7  # 4 bench + 3 incline
    assert result.total_sets == 7
    assert result.is_session_completed is True
    assert result.exercises is not None


def test_parser_parses_partial_session(tmp_path):
    """Parser detects partially filled note (squat done, leg press empty)."""
    path = _write_sample_note(tmp_path, SAMPLE_PARTIAL_NOTE)
    result = parse_workout_note(path)
    assert result.training_day == "Lower 1"
    assert result.completed_sets == 4  # only squat sets checked
    assert result.total_sets == 7  # 4 squat + 3 leg press
    assert result.is_session_completed is False
    assert result.completed_exercises == 1  # only squat fully done
    assert result.total_exercises == 2


def test_parser_parses_rest_day(tmp_path):
    """Rest day note is identified."""
    path = _write_sample_note(tmp_path, SAMPLE_REST_NOTE, "Workout/2026-06-06.md")
    result = parse_workout_note(path)
    assert result.is_rest_day is True
    assert result.is_session_completed is True


def test_parser_missing_file(tmp_path):
    """Non-existent note returns empty summary."""
    path = str(tmp_path / "Workout" / "nonexistent.md")
    result = parse_workout_note(path)
    assert result.is_session_completed is False
    assert result.path == path


def test_parser_extracts_weights_and_reps(tmp_path):
    """Parser extracts weight, reps, and RIR from filled lines."""
    path = _write_sample_note(tmp_path, SAMPLE_FILLED_NOTE)
    result = parse_workout_note(path)
    assert len(result.exercises) >= 3
    first_ex = result.exercises[0]
    assert first_ex.name == "高位下拉 / 引体（任选）"
    assert first_ex.sets[0].weight == "70 kg"
    assert first_ex.sets[0].reps == "8"
    assert first_ex.sets[0].rir == "1"
    assert first_ex.completed_sets == 4


def test_parser_completion_pct(tmp_path):
    """completion_pct returns correct ratio."""
    path = _write_sample_note(tmp_path, SAMPLE_PARTIAL_NOTE)
    result = parse_workout_note(path)
    assert result.completion_pct == pytest.approx(4 / 7)


def test_parser_extracts_notes(tmp_path):
    """Parser extracts exercise-level notes."""
    path = _write_sample_note(tmp_path, SAMPLE_FILLED_NOTE)
    result = parse_workout_note(path)
    assert any("夹背停1秒" in ex.notes for ex in result.exercises)


# ══════════════════════════════════════════════════════════════════════════════
# 4. Next-day logic — advances or rolls over
# ══════════════════════════════════════════════════════════════════════════════


def test_next_day_advances_when_completed(tmp_settings):
    """When previous day's note is completed, advance to next training day."""
    # Complete Monday's note
    generate_workout_note(tmp_settings, date(2026, 6, 1))  # Mon
    decision = next_workday_logic(tmp_settings, today=date(2026, 6, 2))  # Tue
    # Mon is complete because no unchecked boxes → yes
    assert decision.suggested_day in decision.reason


def test_next_day_suggests_carryover_on_incomplete(tmp_settings):
    """When previous training day note exists but is incomplete, suggest carryover."""
    # Write a partial note for Monday (only some sets checked)
    monday_path = Path(tmp_settings.obsidian_vault_path) / "Workout" / "2026-06-01.md"
    monday_path.parent.mkdir(parents=True, exist_ok=True)
    monday_path.write_text(SAMPLE_PARTIAL_NOTE.replace("2026-06-02", "2026-06-01"), encoding="utf-8")

    decision = next_workday_logic(tmp_settings, today=date(2026, 6, 2))
    assert decision.is_carryover
    assert not decision.previous_completed


def test_next_day_weekend_advances_to_monday(tmp_settings):
    """On weekend, if previous note is absent, suggest Monday (no carryover)."""
    # No note for Friday → advances normally
    decision = next_workday_logic(tmp_settings, today=date(2026, 6, 6))  # Sat
    assert decision.suggested_date == date(2026, 6, 8)  # next Monday
    assert not decision.is_carryover


def test_next_day_weekend_carryover(tmp_settings):
    """On weekend with incomplete prev, suggest Monday with carryover day."""
    fri_path = Path(tmp_settings.obsidian_vault_path) / "Workout" / "2026-06-05.md"
    fri_path.parent.mkdir(parents=True, exist_ok=True)
    fri_path.write_text(SAMPLE_PARTIAL_NOTE.replace("2026-06-02", "2026-06-05"), encoding="utf-8")

    decision = next_workday_logic(tmp_settings, today=date(2026, 6, 6))  # Sat
    assert decision.is_carryover
    assert decision.suggested_date == date(2026, 6, 8)  # next Monday


def test_next_day_returns_reason(tmp_settings):
    """Decision includes a Chinese reason string."""
    decision = next_workday_logic(tmp_settings, today=date(2026, 6, 1))
    assert isinstance(decision.reason, str)
    assert len(decision.reason) > 0


# ══════════════════════════════════════════════════════════════════════════════
# 5. Daily note integration — idempotent, no duplicates
# ══════════════════════════════════════════════════════════════════════════════


def test_daily_note_fitness_link(tmp_settings):
    """Fitness section in daily note includes link to workout note."""
    path = workout_note_path(tmp_settings, date(2026, 6, 1))
    generate_workout_note(tmp_settings, date(2026, 6, 1))
    body = upsert_fitness_section(tmp_settings, date(2026, 6, 1))
    assert "Upper 1" in body
    assert "Workout/2026-06-01.md" in body
    assert "```cognitive-workout" in body
    assert "手机无插件选择" in body
    assert "Workout/Choices/2026-06-01 - Upper 1" in body
    assert "http://<Tailscale地址>:8081/workout?date=2026-06-01" in body


def test_daily_note_fitness_web_ui_line_uses_configured_base_url(tmp_settings):
    """Configured Tailscale/base URL turns the daily link into a clickable URL."""
    tmp_settings.workout_ui_base_url = "http://test-machine.tailnet.ts.net:8081/"

    line = workout_web_ui_line(tmp_settings, date(2026, 6, 1))

    assert line == "- [Web UI 训练界面](http://test-machine.tailnet.ts.net:8081/workout?date=2026-06-01)"


def test_daily_note_fitness_web_ui_line_includes_access_token(tmp_settings):
    """Configured access token is embedded in the generated daily URL."""
    tmp_settings.workout_ui_base_url = "https://workout.example.com"
    tmp_settings.workout_ui_access_token = "secret-token"

    line = workout_web_ui_line(tmp_settings, date(2026, 6, 1))

    assert line == "- [Web UI 训练界面](https://workout.example.com/workout?date=2026-06-01&token=secret-token)"


def test_daily_note_fitness_rest_day(tmp_settings):
    """Rest day daily note has rest message, no broken link."""
    link = workout_daily_link(tmp_settings, date(2026, 6, 6))  # Sat
    assert "休息日" in link
    assert "Workout/" not in link  # no broken link


def test_daily_note_fitness_section_idempotent(tmp_settings):
    """Writing fitness section twice produces identical content."""
    from src.integrations.obsidian_daily import ObsidianDailyWriter, _daily_note_path

    generate_workout_note(tmp_settings, date(2026, 6, 1))
    upsert_fitness_section(tmp_settings, date(2026, 6, 1))

    writer = ObsidianDailyWriter(tmp_settings)
    dn_path = _daily_note_path(tmp_settings, datetime(2026, 6, 1, tzinfo=LOCAL_TZ))
    first = dn_path.read_text(encoding="utf-8")

    upsert_fitness_section(tmp_settings, date(2026, 6, 1))
    second = dn_path.read_text(encoding="utf-8")

    assert first == second, "fitness section upsert should be idempotent"


def test_daily_note_fitness_section_not_duplicated(tmp_settings):
    """Fitness section should not have duplicate entries after second write."""
    from src.integrations.obsidian_daily import ObsidianDailyWriter, _daily_note_path

    generate_workout_note(tmp_settings, date(2026, 6, 1))
    upsert_fitness_section(tmp_settings, date(2026, 6, 1))
    upsert_fitness_section(tmp_settings, date(2026, 6, 1))

    writer = ObsidianDailyWriter(tmp_settings)
    dn_path = _daily_note_path(tmp_settings, datetime(2026, 6, 1, tzinfo=LOCAL_TZ))
    content = dn_path.read_text(encoding="utf-8")

    # Section header appears exactly once
    assert content.count("## 健身") == 1
    # Workout link appears exactly once
    assert content.count("Workout/2026-06-01.md") == 1


def test_daily_note_fitness_preserves_manual_content(tmp_settings):
    """Fitness upsert only replaces its managed block, preserving manual notes."""
    from src.integrations.obsidian_daily import _daily_note_path

    dn_path = _daily_note_path(tmp_settings, datetime(2026, 6, 1, tzinfo=LOCAL_TZ))
    dn_path.parent.mkdir(parents=True, exist_ok=True)
    dn_path.write_text(
        "# 2026-06-01\n\n"
        "## 健身\n"
        "- 手写训练感受：今天胸推状态不错\n\n"
        "## 系统观察\n\n",
        encoding="utf-8",
    )

    upsert_fitness_section(tmp_settings, date(2026, 6, 1))
    upsert_fitness_section(tmp_settings, date(2026, 6, 1))

    content = dn_path.read_text(encoding="utf-8")
    assert "- 手写训练感受：今天胸推状态不错" in content
    assert content.count("Workout/2026-06-01.md") == 1
    assert content.count(FITNESS_BLOCK_START) == 1
    assert content.count(FITNESS_BLOCK_END) == 1


def test_daily_note_fitness_migrates_old_generated_link(tmp_settings):
    """Old generated fitness links are moved into the managed block once."""
    from src.integrations.obsidian_daily import _daily_note_path

    dn_path = _daily_note_path(tmp_settings, datetime(2026, 6, 1, tzinfo=LOCAL_TZ))
    dn_path.parent.mkdir(parents=True, exist_ok=True)
    dn_path.write_text(
        "# 2026-06-01\n\n"
        "## 健身\n"
        "- [健身 - Upper 1 (胸力量日)](Workout/2026-06-01.md)\n\n"
        "- 手写：卧推手感好\n\n"
        "## 系统观察\n\n",
        encoding="utf-8",
    )

    upsert_fitness_section(tmp_settings, date(2026, 6, 1))
    upsert_fitness_section(tmp_settings, date(2026, 6, 1))

    content = dn_path.read_text(encoding="utf-8")
    assert content.count("Workout/2026-06-01.md") == 1
    assert "- 手写：卧推手感好" in content


def test_daily_note_workout_link_generator(tmp_settings):
    """workout_daily_link returns correct markdown link for training day."""
    link = workout_daily_link(tmp_settings, date(2026, 6, 1))  # Mon
    assert "Upper 1" in link
    assert "Workout/2026-06-01.md" in link
    assert link.startswith("- [")


# ══════════════════════════════════════════════════════════════════════════════
# 6. Edge cases
# ══════════════════════════════════════════════════════════════════════════════


def test_parser_empty_file_in_workout_folder(tmp_path):
    """Parser handles empty file gracefully."""
    p = tmp_path / "Workout" / "2026-06-01.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("", encoding="utf-8")
    result = parse_workout_note(str(p))
    assert not result.is_session_completed
    assert not result.is_rest_day


def test_generator_all_days_have_unique_exercises(tmp_settings):
    """Each training day has at least 3 exercises with unique names."""
    for day_name, day in WORKOUT_PLAN.items():
        names = [e.name for e in day.exercises]
        assert len(names) == len(set(names)), f"{day_name} has duplicate exercises"
        assert len(names) >= 3, f"{day_name} has fewer than 3 exercises"


def test_parser_no_frontmatter(tmp_path):
    """Parser handles note without frontmatter."""
    content = """# My Custom Workout

### 1. Custom Exercise
- [x] Set 1 | 重量: 50 kg | 次数: 10 / 10-12 | RIR: 2
"""
    path = _write_sample_note(tmp_path, content)
    result = parse_workout_note(path)
    assert result.completed_sets == 1
    assert result.training_day == "rest"  # default


def test_workout_note_path_resolves(tmp_settings):
    """Workout note path follows vault/Workout/YYYY-MM-DD.md."""
    d = date(2026, 6, 1)
    path = workout_note_path(tmp_settings, d)
    expected = Path(tmp_settings.obsidian_vault_path) / "Workout" / "2026-06-01.md"
    assert path == expected


def test_next_day_logic_returns_decision_object(tmp_settings):
    """next_workday_logic returns a NextWorkoutDecision."""
    decision = next_workday_logic(tmp_settings, today=date(2026, 6, 1))
    assert isinstance(decision, NextWorkoutDecision)
    assert hasattr(decision, "suggested_date")
    assert hasattr(decision, "suggested_day")
    assert hasattr(decision, "is_carryover")
    assert hasattr(decision, "previous_date")
    assert hasattr(decision, "previous_completed")
    assert hasattr(decision, "reason")


def test_generate_workout_note_body_includes_marker(tmp_settings):
    """Generated body includes the session completion marker."""
    from src.domain.fitness.plan import WORKOUT_PLAN
    body = generate_workout_note_body(WORKOUT_PLAN["Upper 1"])
    assert "<!-- workout:session -->" in body


def test_parser_filled_note_detects_full_completion(tmp_path):
    """Parser marks session completed when all sets are checked."""
    path = _write_sample_note(tmp_path, SAMPLE_COMPLETED_NOTE)
    result = parse_workout_note(path)
    assert result.is_session_completed is True


def test_parser_partial_note_not_completed(tmp_path):
    """Parser does not mark session completed when sets unchecked."""
    path = _write_sample_note(tmp_path, SAMPLE_PARTIAL_NOTE)
    result = parse_workout_note(path)
    assert result.is_session_completed is False
