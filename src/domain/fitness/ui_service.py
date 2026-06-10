"""Structured workout note read/write service for the mobile-friendly Workout UI.

Provides roundtrip-safe operations on generated workout markdown notes,
preserving manual edits outside the structured set-line format.

Every function reads the file, performs a targeted modification, writes back,
and returns the parsed session state.  Only the modified set lines and
frontmatter counters are ever touched.
"""

from __future__ import annotations

import re
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any, Callable

from src.domain.fitness.plan import WORKOUT_PLAN, get_weekday_name
from src.domain.fitness.generator import _frontmatter, generate_workout_note_body, rest_day_note
from src.domain.fitness.parser import _is_placeholder, _parse_frontmatter

# ── Regex ──────────────────────────────────────────────────────────────────────

# Captures the full set line into logical groups for clean replacement.
SET_LINE_RE = re.compile(
    r"^(- \[([ x])\] Set (\d+) \| 重量:\s*)(.*?)(\s*kg \| 次数:\s*)(.*?)(\s*/\s*)(.*?)(\s*\| RIR:\s*)(.*?)\s*$",
    re.MULTILINE,
)

EX_HEADER_RE = re.compile(r"^### (\d+)\.\s+(.+)$", re.MULTILINE)

# ── Parse helpers ──────────────────────────────────────────────────────────────


def parse_set_line(line: str) -> dict[str, Any] | None:
    """Parse a single set line into a dict, or None if it doesn't match."""
    m = SET_LINE_RE.match(line)
    if not m:
        return None
    w = m.group(4).strip()
    r = m.group(6).strip()
    ri = m.group(10).strip()
    return {
        "set_number": int(m.group(3)),
        "checked": m.group(2) == "x",
        "weight": "" if _is_placeholder(w) else w,
        "reps": "" if _is_placeholder(r) else r,
        "target_reps": m.group(8).strip(),
        "rir": "" if _is_placeholder(ri) else ri,
    }


def format_set_line(s: dict[str, Any]) -> str:
    """Render one set dict back to a markdown set line."""
    box = "x" if s["checked"] else " "
    w = s["weight"] or "___"
    r = s["reps"] or "___"
    ri = s["rir"] or "___"
    return f"- [{box}] Set {s['set_number']} | 重量: {w} kg | 次数: {r} / {s['target_reps']} | RIR: {ri}"


# ── Block-level helpers ────────────────────────────────────────────────────────


def _find_exercise_blocks(text: str) -> list[tuple[int, int, str, int]]:
    """Return (start, end, name, index) for each exercise header block."""
    blocks: list[tuple[int, int, str, int]] = []
    for m in EX_HEADER_RE.finditer(text):
        idx = int(m.group(1))
        name = m.group(2).strip()
        start = m.start()
        nxt = EX_HEADER_RE.search(text, m.end())
        end = nxt.start() if nxt else len(text)
        blocks.append((start, end, name, idx))
    return blocks


def _set_line_count(block_text: str) -> int:
    """Count set lines in an exercise block."""
    return len(SET_LINE_RE.findall(block_text))


def _add_blank_set(block_text: str) -> tuple[str, int]:
    """Append a blank set line; return (new_text, new_set_num)."""
    lines = list(SET_LINE_RE.finditer(block_text))
    if not lines:
        return block_text, 1
    last = lines[-1]
    num = int(last.group(3)) + 1
    target = last.group(8).strip()
    new = f"- [ ] Set {num} | 重量: ___ kg | 次数: ___ / {target} | RIR: ___"
    # Insert preserving trailing whitespace
    pos = last.end()
    rest = block_text[pos:]
    nl = "\n" if rest.startswith("\n") else ""
    if nl:
        rest = rest[1:]
    return block_text[:pos] + nl + new + ("\n" if rest else "") + rest, num


def _duplicate_last_set(block_text: str) -> tuple[str, int]:
    """Duplicate the last set line with incremented number."""
    lines = list(SET_LINE_RE.finditer(block_text))
    if not lines:
        return _add_blank_set(block_text)
    last = lines[-1]
    num = int(last.group(3)) + 1
    new = (
        f"- [{last.group(2)}] Set {num}"
        f" | 重量: {last.group(4).strip()} kg"
        f" | 次数: {last.group(6).strip()} / {last.group(8).strip()}"
        f" | RIR: {last.group(10).strip()}"
    )
    pos = last.end()
    rest = block_text[pos:]
    nl = "\n" if rest.startswith("\n") else ""
    if nl:
        rest = rest[1:]
    return block_text[:pos] + nl + new + ("\n" if rest else "") + rest, num


def _delete_set_from_block(block_text: str, set_num: int) -> str:
    """Remove one set line and collapse double blank lines."""
    removed = SET_LINE_RE.sub(
        lambda m: "" if int(m.group(3)) == set_num else m.group(0),
        block_text,
    )
    # Collapse 3+ consecutive newlines into 2 (keep paragraph separators)
    removed = re.sub(r"\n{3,}", "\n\n", removed)
    return removed.strip()


# ── Frontmatter helpers ────────────────────────────────────────────────────────


def _renumber_headers(text: str) -> str:
    """Renumber ### N. headers to sequential 1..N preserving exercise names."""
    n = 0
    def _repl(m):
        nonlocal n
        n += 1
        return f"### {n}. {m.group(2)}"
    return EX_HEADER_RE.sub(_repl, text)


def _count_sets(text: str) -> tuple[int, int]:
    """Return (total_sets, completed_sets) across the whole note."""
    total = 0
    done = 0
    for m in SET_LINE_RE.finditer(text):
        total += 1
        if m.group(2) == "x":
            done += 1
    return total, done


def _update_fm(text: str, key: str, value: Any) -> str:
    """Update a single YAML frontmatter field in-place."""
    pat = re.compile(rf"^{key}:\s*.*$", re.MULTILINE)
    return pat.sub(f"{key}: {value}", text) if pat.search(text) else text


def _refresh_frontmatter(text: str) -> str:
    """Recalculate total_sets, completed_sets, completed in frontmatter."""
    total, done = _count_sets(text)
    text = _update_fm(text, "total_sets", total)
    text = _update_fm(text, "completed_sets", done)
    text = _update_fm(text, "completed", "true" if total > 0 and done >= total else "false")
    return text


def _has_logged_progress(text: str) -> bool:
    """Return True if the note contains checked sets or filled values."""
    for m in SET_LINE_RE.finditer(text):
        if m.group(2) == "x":
            return True
        if not _is_placeholder(m.group(4)):
            return True
        if not _is_placeholder(m.group(6)):
            return True
        if not _is_placeholder(m.group(10)):
            return True
    return False


def _render_session_body(d: date, day_name: str) -> str:
    """Render a workout/rest note body for *day_name*."""
    day = WORKOUT_PLAN.get(day_name)
    if day is None:
        return _frontmatter(None, d) + rest_day_note(d, get_weekday_name(d))
    return _frontmatter(day, d) + generate_workout_note_body(day)


# ── Exercise block modifier ────────────────────────────────────────────────────


def _modify_block(text: str, exercise_index: int, modifier: Callable[[str], tuple[str, int]]) -> str:
    """Find exercise *exercise_index*, apply *modifier*, splice result in."""
    for start, end, name, idx in _find_exercise_blocks(text):
        if idx == exercise_index:
            new_block, _ = modifier(text[start:end])
            # Ensure the block ends with a newline so the splice with text[end:]
            # does not merge the block's last line with the next header.
            if not new_block.endswith("\n"):
                new_block += "\n"
            return text[:start] + new_block + text[end:]
    raise ValueError(f"Exercise #{exercise_index} not found")


# ── Session parsing ────────────────────────────────────────────────────────────


def _parse_session(text: str, d: date) -> dict[str, Any]:
    """Parse full workout markdown into a JSON-compatible session dict."""
    fm = _parse_frontmatter(text)
    exercises: list[dict[str, Any]] = []
    for start, end, name, idx in _find_exercise_blocks(text):
        block = text[start:end]
        sets = [s for line in block.split("\n") if (s := parse_set_line(line))]
        note_m = re.search(r"^\s*>\s*(.+)$", block, re.MULTILINE)
        exercises.append({
            "name": name,
            "index": idx,
            "sets": sets,
            "notes": note_m.group(1).strip() if note_m else "",
            "total_sets": len(sets),
        })
    total, done = _count_sets(text)
    return {
        "date": d.isoformat(),
        "training_day": str(fm.get("training_day", "rest")),
        "focus": str(fm.get("focus", "")),
        "exercises": exercises,
        "completed": total > 0 and done >= total,
        "total_sets": total,
        "completed_sets": done,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════════════════════


def session_path(vault_path: str, d: date) -> Path:
    """Resolve the markdown file path for a given date."""
    return Path(vault_path) / "Workout" / f"{d.isoformat()}.md"


def read_session(vault_path: str, d: date) -> dict[str, Any] | None:
    """Read a workout session.  Returns None if the file doesn't exist."""
    p = session_path(vault_path, d)
    if not p.exists():
        return None
    return _parse_session(p.read_text(encoding="utf-8"), d)


def select_or_create_session(
    vault_path: str,
    d: date,
    day_name: str,
    force: bool = False,
) -> dict[str, Any]:
    """Create or safely switch a workout note for *d*.

    If a different session already exists and has logged progress, switching is
    blocked unless ``force`` is true. This prevents silent loss of workout data.
    """
    p = session_path(vault_path, d)
    p.parent.mkdir(parents=True, exist_ok=True)

    if p.exists():
        text = p.read_text(encoding="utf-8")
        current = _parse_frontmatter(text).get("training_day", "rest")
        if current == day_name:
            return _parse_session(text, d)
        if not force and _has_logged_progress(text):
            raise ValueError("session_has_progress")
        p.write_text(_render_session_body(d, day_name), encoding="utf-8")
        return _parse_session(p.read_text(encoding="utf-8"), d)

    p.write_text(_render_session_body(d, day_name), encoding="utf-8")
    return _parse_session(p.read_text(encoding="utf-8"), d)


def update_set(
    vault_path: str,
    d: date,
    exercise_index: int,
    set_number: int,
    checked: bool | None = None,
    weight: str | None = None,
    reps: str | None = None,
    rir: str | None = None,
) -> dict[str, Any]:
    """Update one set's fields.  ``None`` fields keep their current value."""
    p = session_path(vault_path, d)
    text = p.read_text(encoding="utf-8")

    def updater(block: str) -> tuple[str, int]:
        def _repl(m):
            if int(m.group(3)) != set_number:
                return m.group(0)
            c = checked if checked is not None else (m.group(2) == "x")
            w = (weight if weight is not None else m.group(4).strip()) or "___"
            r = (reps if reps is not None else m.group(6).strip()) or "___"
            t = m.group(8).strip()
            ri = (rir if rir is not None else m.group(10).strip()) or "___"
            return f"- [{'x' if c else ' '}] Set {set_number} | 重量: {w} kg | 次数: {r} / {t} | RIR: {ri}"
        return SET_LINE_RE.sub(_repl, block), 0

    text = _modify_block(text, exercise_index, updater)
    text = _refresh_frontmatter(text)
    p.write_text(text, encoding="utf-8")
    return _parse_session(text, d)


def add_set(vault_path: str, d: date, exercise_index: int) -> dict[str, Any]:
    """Add a blank set to the end of an exercise."""
    p = session_path(vault_path, d)
    text = p.read_text(encoding="utf-8")
    text = _modify_block(text, exercise_index, _add_blank_set)
    text = _refresh_frontmatter(text)
    p.write_text(text, encoding="utf-8")
    return _parse_session(text, d)


def duplicate_set(vault_path: str, d: date, exercise_index: int) -> dict[str, Any]:
    """Duplicate the last set of an exercise."""
    p = session_path(vault_path, d)
    text = p.read_text(encoding="utf-8")
    text = _modify_block(text, exercise_index, _duplicate_last_set)
    text = _refresh_frontmatter(text)
    p.write_text(text, encoding="utf-8")
    return _parse_session(text, d)


def delete_set(vault_path: str, d: date, exercise_index: int, set_number: int) -> dict[str, Any]:
    """Delete one set from an exercise."""
    p = session_path(vault_path, d)
    text = p.read_text(encoding="utf-8")

    def deleter(block: str) -> tuple[str, int]:
        return _delete_set_from_block(block, set_number), 0

    text = _modify_block(text, exercise_index, deleter)
    text = _refresh_frontmatter(text)
    p.write_text(text, encoding="utf-8")
    return _parse_session(text, d)


def move_exercise(
    vault_path: str, d: date, exercise_index: int, direction: str,
) -> dict[str, Any]:
    """Move an exercise block up or down, then renumber headers to 1..N.

    When the exercise is already at the boundary the session is returned
    unmodified (no error).
    """
    if direction not in ("up", "down"):
        raise ValueError(f"direction must be 'up' or 'down', got {direction!r}")

    p = session_path(vault_path, d)
    text = p.read_text(encoding="utf-8")

    blocks = _find_exercise_blocks(text)
    target_pos = None
    for i, (_s, _e, _n, idx) in enumerate(blocks):
        if idx == exercise_index:
            target_pos = i
            break
    if target_pos is None:
        raise ValueError(f"Exercise #{exercise_index} not found")

    if (direction == "up" and target_pos == 0) or (direction == "down" and target_pos == len(blocks) - 1):
        return _parse_session(text, d)

    swap_pos = target_pos - 1 if direction == "up" else target_pos + 1
    p1, p2 = min(target_pos, swap_pos), max(target_pos, swap_pos)
    s1, e1 = blocks[p1][0], blocks[p1][1]
    s2, e2 = blocks[p2][0], blocks[p2][1]

    block1 = text[s1:e1]
    block2 = text[s2:e2]
    between = text[e1:s2]

    new_text = text[:s1] + block2 + between + block1 + text[e2:]
    new_text = _renumber_headers(new_text)
    new_text = _refresh_frontmatter(new_text)
    p.write_text(new_text, encoding="utf-8")
    return _parse_session(new_text, d)


def _update_block_name_notes(block: str, name: str | None, notes: str | None) -> str:
    """Update exercise header name and/or notes ``> ...`` line in a block."""
    if name is not None:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("exercise_name_required")
        block = EX_HEADER_RE.sub(lambda m: f"### {m.group(1)}. {clean_name}", block, count=1)
    if notes is not None:
        note_re = re.compile(r"^\s*>.+$", re.MULTILINE)
        if notes.strip():
            new_note_line = f"  > {notes.strip()}"
            if note_re.search(block):
                block = note_re.sub(new_note_line, block)
            else:
                set_matches = list(SET_LINE_RE.finditer(block))
                if set_matches:
                    last = set_matches[-1]
                    block = block[:last.end()] + "\n" + new_note_line + block[last.end():]
                else:
                    block = block.rstrip() + "\n" + new_note_line + "\n"
        else:
            block = note_re.sub("", block)
    return block


def update_exercise(
    vault_path: str, d: date,
    exercise_index: int,
    name: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Update exercise header name and/or notes line."""
    p = session_path(vault_path, d)
    text = p.read_text(encoding="utf-8")

    def modifier(block: str) -> tuple[str, int]:
        return _update_block_name_notes(block, name, notes), 0

    text = _modify_block(text, exercise_index, modifier)
    p.write_text(text, encoding="utf-8")
    return _parse_session(text, d)


def add_exercise(
    vault_path: str, d: date,
    name: str,
    target_reps: str = "8-12",
    notes: str = "",
    sets_count: int = 3,
) -> dict[str, Any]:
    """Append a custom exercise with *sets_count* blank sets at the end."""
    clean_name = name.strip()
    if not clean_name:
        raise ValueError("exercise_name_required")
    if sets_count < 1 or sets_count > 20:
        raise ValueError("sets_count_must_be_1_to_20")
    clean_target_reps = target_reps.strip() or "8-12"

    p = session_path(vault_path, d)
    text = p.read_text(encoding="utf-8")

    lines = [f"### 99. {clean_name}"]
    for s in range(1, sets_count + 1):
        lines.append(f"- [ ] Set {s} | 重量: ___ kg | 次数: ___ / {clean_target_reps} | RIR: ___")
    if notes.strip():
        lines.append(f"  > {notes.strip()}")
    lines.append("")
    new_block = "\n".join(lines)

    marker = "<!-- workout:session -->"
    marker_pos = text.find(marker)
    if marker_pos >= 0:
        text = text[:marker_pos] + new_block + "\n\n" + text[marker_pos:]
    else:
        text = text.rstrip() + "\n\n" + new_block + "\n"

    text = _renumber_headers(text)
    text = _refresh_frontmatter(text)
    p.write_text(text, encoding="utf-8")
    return _parse_session(text, d)


def delete_exercise(vault_path: str, d: date, exercise_index: int) -> dict[str, Any]:
    """Remove an exercise block and renumber remaining headers.

    Only the exercise header, its set lines, and its notes line are removed;
    any trailing content (markers, manual notes) between this exercise and the
    next header is preserved.
    """
    p = session_path(vault_path, d)
    text = p.read_text(encoding="utf-8")

    blocks = _find_exercise_blocks(text)
    target_block = None
    for start, end, _name, idx in blocks:
        if idx == exercise_index:
            target_block = (start, end)
            break
    if target_block is None:
        raise ValueError(f"Exercise #{exercise_index} not found")

    start, end = target_block

    # Only remove up to the last set/notes line, preserving trailing content
    # (e.g. <!-- workout:session -->, manual notes) that sits between this
    # exercise and the next header.
    remove_end = start
    # Header line
    nl = text.find("\n", start)
    if nl >= 0 and nl < end:
        remove_end = nl + 1
    # Set lines
    for m in SET_LINE_RE.finditer(text, remove_end, end):
        remove_end = m.end()
    # Notes line after the last set
    note_re = re.compile(r"^\s*>\s*(.*)$", re.MULTILINE)
    for m in note_re.finditer(text, remove_end, end):
        remove_end = max(remove_end, m.end())

    text = text[:start] + text[remove_end:]
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = _renumber_headers(text)
    text = _refresh_frontmatter(text)
    p.write_text(text, encoding="utf-8")
    return _parse_session(text, d)
