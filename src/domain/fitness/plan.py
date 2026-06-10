"""Deterministic 5-day PPL-style workout plan.

Mon = Upper 1 (chest strength)
Tue = Lower 1 (quads)
Wed = Upper 2 (back + shoulder)
Thu = Lower 2 (glutes + hamstrings)
Fri = Upper 3 (chest volume + arms)
Sat / Sun = rest

Each exercise has: name, sets, reps, rir (reps in reserve), notes, superset_with.
Supersets are indicated by sharing the string key in the optional ``superset_with`` field.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Literal

# ── Types ────────────────────────────────────────────────────────────────────

Weekday = Literal[0, 1, 2, 3, 4, 5, 6]  # Mon=0 … Sun=6
DayName = Literal[
    "rest", "Upper 1", "Lower 1", "Upper 2", "Lower 2", "Upper 3"
]

# ── Data model ───────────────────────────────────────────────────────────────


@dataclass
class Exercise:
    name: str
    target_sets: int
    target_reps: str  # e.g. "6-8", "10-12"
    rir: str = "2"  # reps in reserve
    notes: str = ""
    superset_with: str | None = None  # string key shared with paired exercise


@dataclass
class WorkoutDay:
    name: DayName
    focus: str
    exercises: list[Exercise]


# ── Plan definition ──────────────────────────────────────────────────────────

WORKOUT_DAYS: dict[Weekday, DayName] = {
    0: "Upper 1",
    1: "Lower 1",
    2: "Upper 2",
    3: "Lower 2",
    4: "Upper 3",
    5: "rest",
    6: "rest",
}

# 5 training days
UPPER_1 = WorkoutDay(
    name="Upper 1",
    focus="胸力量日",
    exercises=[
        Exercise("史密斯机卧推", 4, "5-8", rir="2"),
        Exercise("上斜器械推胸", 3, "8-10", rir="1-2"),
        Exercise("胸支撑划船（双手+中立握）", 4, "8-10", rir="2"),
        Exercise("高位下拉", 3, "10-12", rir="1"),
        Exercise("器械侧平举", 4, "12-15", rir="1"),
    ],
)

LOWER_1 = WorkoutDay(
    name="Lower 1",
    focus="股四头",
    exercises=[
        Exercise("哈克深蹲（正面+脚位低）", 4, "6-8", rir="2"),
        Exercise("史密斯 RDL", 3, "10-12", rir="1-2"),
        Exercise("腿举（脚中低位）", 3, "10-12"),
        Exercise("腿屈伸（顶峰收缩）", 3, "12-15", notes="最后一组接近力竭"),
        Exercise("坐姿提踵", 4, "10-15"),
    ],
)

UPPER_2 = WorkoutDay(
    name="Upper 2",
    focus="背+肩",
    exercises=[
        Exercise("高位下拉 / 引体（任选）", 4, "8-10"),
        Exercise("坐姿划船（夹背停1秒）", 4, "10-12"),
        Exercise("器械肩推", 3, "8-12"),
        Exercise("面拉", 3, "15-20"),
        Exercise("绳索飞鸟（轻重量激活）", 2, "12-15"),
    ],
)

LOWER_2 = WorkoutDay(
    name="Lower 2",
    focus="臀+腘绳肌",
    exercises=[
        Exercise("臀推", 3, "6-8", rir="1-2"),
        Exercise("史密斯分腿蹲", 3, "10-12 / 每侧"),
        Exercise("坐姿腿弯举（拉伸位）", 4, "10-15"),
        Exercise("俯卧腿弯举（收缩位）", 3, "12-15"),
        Exercise("悬垂举腿", 3, "15-20"),
    ],
)

UPPER_3 = WorkoutDay(
    name="Upper 3",
    focus="胸容量日+手臂",
    exercises=[
        Exercise("上斜史密斯卧推", 3, "10-12", rir="1"),
        Exercise("器械飞鸟", 3, "12-15", notes="慢放；与俯卧撑做超级组", superset_with="chest_volume"),
        Exercise("俯卧撑", 3, "力竭", notes="与器械飞鸟做超级组", superset_with="chest_volume"),
        Exercise("绳索夹胸（低到高）", 3, "12-15", notes="顶峰停1秒"),
        Exercise("牧师凳弯举（器械）", 3, "10-12", notes="与绳索过顶臂屈伸做超级组", superset_with="arms_1"),
        Exercise("绳索过顶臂屈伸", 3, "12-15", notes="与牧师凳弯举做超级组", superset_with="arms_1"),
        Exercise("锤式弯举", 3, "10-12", notes="与单臂下压做超级组", superset_with="arms_2"),
        Exercise("单臂下压", 3, "12-15", notes="与锤式弯举做超级组", superset_with="arms_2"),
    ],
)

WORKOUT_PLAN: dict[DayName, WorkoutDay] = {
    "Upper 1": UPPER_1,
    "Lower 1": LOWER_1,
    "Upper 2": UPPER_2,
    "Lower 2": LOWER_2,
    "Upper 3": UPPER_3,
}

_DAY_NAME_CN: dict[DayName, str] = {
    "Upper 1": "胸力量日",
    "Lower 1": "股四头",
    "Upper 2": "背+肩",
    "Lower 2": "臀+腘绳肌",
    "Upper 3": "胸容量日+手臂",
    "rest": "休息日",
}


# ── Pure helpers ─────────────────────────────────────────────────────────────


def is_rest_day(d: date) -> bool:
    """Return True if *d* is Saturday (5) or Sunday (6)."""
    return d.weekday() >= 5


def get_training_day(d: date) -> DayName:
    """Return the planned DayName for *d*, or 'rest' for weekends."""
    return WORKOUT_DAYS.get(d.weekday(), "rest")


def get_weekday_name(d: date) -> str:
    """Return Chinese weekday name: 星期一 ... 星期日."""
    names = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    return names[d.weekday()]


def day_name_cn(day: DayName) -> str:
    """Return Chinese label for a training day name."""
    return _DAY_NAME_CN.get(day, str(day))


def previous_training_day(d: date) -> date:
    """Return the most recent past date that is a training day (Mon-Fri).

    Walks backward from *d - 1 day* up to 3 days to find the last training day.
    Does NOT return *d* itself even if *d* is a training day.
    """
    for offset in range(1, 4):
        candidate = d - timedelta(days=offset)
        if not is_rest_day(candidate):
            return candidate
    return d - timedelta(days=1)  # fallback
