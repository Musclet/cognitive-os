"""Read and summarise a generated workout note.

Tolerates partially filled notes: missing values, unchecked boxes,
and incomplete metadata.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from src.domain.fitness.plan import WORKOUT_PLAN, DayName, get_training_day, is_rest_day


@dataclass
class SetLog:
    """Data parsed from one set line."""

    set_number: int
    checked: bool
    weight: str  # raw text from the ___ field (or filled value)
    reps: str  # e.g. "8 / 8-10" → "8"
    target_reps: str  # e.g. "8-10"
    rir: str  # raw text
    raw: str  # full line


@dataclass
class ExerciseSummary:
    """Data parsed from one exercise block."""

    name: str
    index: int
    sets: list[SetLog]
    completed_sets: int = 0
    notes: str = ""


@dataclass
class WorkoutSummary:
    """Full parsed summary of a workout note."""

    path: str
    date: date | None = None
    training_day: DayName = "rest"
    focus: str = ""
    total_sets: int = 0
    completed_sets: int = 0
    exercises: list[ExerciseSummary] = field(default_factory=list)
    completed_exercises: int = 0
    total_exercises: int = 0
    is_session_completed: bool = False
    is_rest_day: bool = False

    @property
    def completion_pct(self) -> float:
        return self.completed_sets / self.total_sets if self.total_sets else 0.0

    @property
    def all_exercises_completed(self) -> bool:
        return self.completed_exercises == self.total_exercises if self.total_exercises else False


# ── Parsing ──────────────────────────────────────────────────────────────────


def parse_workout_note(path: str | Path) -> WorkoutSummary:
    """Parse a workout markdown note and return a structured summary.

    Handles:
    - YAML frontmatter (date, training_day, total_sets, completed, completed_sets)
    - Checkbox-based set log (- [x] / - [ ])
    - Weight/reps/RIR fields
    - Partial data
    - Rest-day notes (with ``<!-- workout:rest-day -->`` marker)
    """
    p = Path(path)
    if not p.exists():
        return WorkoutSummary(
            path=str(p),
            is_rest_day=False,
            is_session_completed=False,
        )

    text = p.read_text(encoding="utf-8")

    # ── Frontmatter ──────────────────────────────────────────────────────
    fm = _parse_frontmatter(text)

    # ── Rest day detection ────────────────────────────────────────────────
    if "<!-- workout:rest-day -->" in text or fm.get("training_day") == "rest":
        return WorkoutSummary(
            path=str(p),
            date=_parse_date(fm),
            training_day="rest",
            is_rest_day=True,
            is_session_completed=True,  # rest days are "trivially complete"
        )

    training_day_str = str(fm.get("training_day", "rest"))
    training_day: DayName = training_day_str if training_day_str in WORKOUT_PLAN else "rest"

    # ── Parse exercises and sets ─────────────────────────────────────────
    exercises: list[ExerciseSummary] = []
    # Match ### N. ExerciseName
    ex_pattern = re.compile(r"^### (\d+)\.\s+(.+)$", re.MULTILINE)
    set_pattern = re.compile(
        r"^- \[([ x])\] Set (\d+) \| 重量:\s*(.+?)\s*\| 次数:\s*(.+?)\s*/\s*(.+?)\s*\| RIR:\s*(.+?)$",
        re.MULTILINE,
    )
    note_pattern = re.compile(r"^\s*> (.+)$", re.MULTILINE)

    for ex_match in ex_pattern.finditer(text):
        ex_idx = int(ex_match.group(1))
        ex_name = ex_match.group(2).strip()

        # Find sets between this exercise header and the next one (or EOF)
        ex_start = ex_match.end()
        next_ex = ex_pattern.search(text, ex_start)
        ex_block_end = next_ex.start() if next_ex else len(text)
        ex_block = text[ex_start:ex_block_end]

        sets: list[SetLog] = []
        for s_match in set_pattern.finditer(ex_block):
            checked = s_match.group(1) == "x"
            set_num = int(s_match.group(2))
            weight = s_match.group(3).strip() if not _is_placeholder(s_match.group(3)) else ""
            reps = s_match.group(4).strip() if not _is_placeholder(s_match.group(4)) else ""
            target_reps = s_match.group(5).strip()
            rir = s_match.group(6).strip() if not _is_placeholder(s_match.group(6)) else ""
            sets.append(SetLog(
                set_number=set_num,
                checked=checked,
                weight=weight,
                reps=reps,
                target_reps=target_reps,
                rir=rir,
                raw=s_match.group(0),
            ))

        # Extract notes
        notes_matches = note_pattern.findall(ex_block)
        notes = notes_matches[0] if notes_matches else ""

        completed_sets = sum(1 for s in sets if s.checked)
        exercises.append(ExerciseSummary(
            name=ex_name,
            index=ex_idx,
            sets=sets,
            completed_sets=completed_sets,
            notes=notes,
        ))

    total_sets = sum(len(e.sets) for e in exercises)
    completed_sets = sum(e.completed_sets for e in exercises)
    total_exercises = len(exercises)
    completed_exercises = sum(1 for e in exercises if e.sets and e.completed_sets == len(e.sets))

    # Also try to read from frontmatter if no sets parsed (empty note)
    if total_sets == 0:
        total_sets = int(fm.get("total_sets", 0))
        completed_sets = int(fm.get("completed_sets", 0))

    # Session is completed when ALL sets are checked, OR frontmatter says so
    fm_completed = bool(fm.get("completed", False))
    all_sets_done = completed_sets > 0 and completed_sets >= total_sets

    return WorkoutSummary(
        path=str(p),
        date=_parse_date(fm),
        training_day=training_day,
        focus=str(fm.get("focus", "")),
        total_sets=total_sets,
        completed_sets=completed_sets,
        exercises=exercises,
        completed_exercises=completed_exercises,
        total_exercises=total_exercises,
        is_session_completed=fm_completed or all_sets_done,
    )


def _is_placeholder(value: str) -> bool:
    """Return True if the value is empty or contains only underscores."""
    stripped = value.strip()
    return not stripped or stripped.strip("_") == ""


def _parse_frontmatter(text: str) -> dict[str, Any]:
    """Minimal YAML frontmatter parser (no PyYAML dependency)."""
    fm: dict[str, Any] = {}
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
    if not m:
        return fm
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        # Skip quoted values
        value = value.strip("\"'")
        # Type coercion
        if value.lower() in ("true", "false"):
            fm[key] = value.lower() == "true"
        elif value.isdigit():
            fm[key] = int(value)
        else:
            fm[key] = value
    return fm


def _parse_date(fm: dict[str, Any]) -> date | None:
    raw = fm.get("date", "")
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw))
    except (ValueError, TypeError):
        return None
