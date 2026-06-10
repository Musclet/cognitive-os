"""Obsidian workout note generator and daily-note fitness section writer.

Produces structured markdown at ``Workout/YYYY-MM-DD.md`` inside the vault,
and can upsert a ``## 健身`` section in the daily note with a link + summary.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from src.domain.fitness.plan import (
    WORKOUT_PLAN,
    DayName,
    Exercise,
    WorkoutDay,
    get_training_day,
    is_rest_day,
    get_weekday_name,
    day_name_cn,
)
from src.infrastructure.config import Settings
from src.integrations.obsidian_daily import (
    _daily_note_path,
    _read_or_create_note,
    SECTION_HEADERS,
)

logger = logging.getLogger(__name__)
LOCAL_TZ = ZoneInfo("Asia/Singapore")
FITNESS_BLOCK_START = "<!-- cognitive-os:fitness:start -->"
FITNESS_BLOCK_END = "<!-- cognitive-os:fitness:end -->"

# ── Path resolution ──────────────────────────────────────────────────────────


def workout_note_path(
    settings: Settings, d: date | None = None, vault_path: str | None = None
) -> Path:
    """Resolve ``Vault/Workout/YYYY-MM-DD.md``.

    Args:
        settings: App settings (used for obsidian_vault_path unless *vault_path* given).
        d: Target date (default today local).
        vault_path: Override vault path (for testing).
    """
    if d is None:
        d = datetime.now(LOCAL_TZ).date()
    vault = Path(vault_path or settings.obsidian_vault_path)
    return vault / "Workout" / f"{d.isoformat()}.md"


def workout_choice_note_path(
    settings: Settings,
    d: date,
    day_name: DayName,
    vault_path: str | None = None,
) -> Path:
    """Resolve a mobile-friendly candidate note path for a chosen workout."""
    vault = Path(vault_path or settings.obsidian_vault_path)
    safe_day = str(day_name).replace("/", "-")
    return vault / "Workout" / "Choices" / f"{d.isoformat()} - {safe_day}.md"


# ── Rest-day note ────────────────────────────────────────────────────────────


def rest_day_note(d: date, weekday_name: str) -> str:
    """Generate a brief rest-day markdown snippet."""
    return (
        f"# 休息日 — {d.isoformat()} ({weekday_name})\n\n"
        f"今日是计划休息日，不安排训练。\n\n"
        f"---\n<!-- workout:rest-day -->\n"
    )


# ── Workout note body ────────────────────────────────────────────────────────


def _exercise_to_md(idx: int, ex: Exercise) -> str:
    """Format one exercise as a markdown block.

    Returns something like::

        ### 1. Barbell Bench Press
        - [ ] Set 1 | 重量: ___ kg | 次数: ___ / 6-8 | RIR: ___
        - [ ] Set 2 | 重量: ___ kg | 次数: ___ / 6-8 | RIR: ___
        - [ ] Set 3 | 重量: ___ kg | 次数: ___ / 6-8 | RIR: ___
        - [ ] Set 4 | 重量: ___ kg | 次数: ___ / 6-8 | RIR: ___
          Notes: Heavy compound — log weight

    """
    lines = [f"### {idx}. {ex.name}"]
    for s in range(1, ex.target_sets + 1):
        lines.append(f"- [ ] Set {s} | 重量: ___ kg | 次数: ___ / {ex.target_reps} | RIR: ___")
    if ex.notes:
        lines.append(f"  > {ex.notes}")
    lines.append("")  # trailing blank line
    return "\n".join(lines)


def _superset_note(exercises: list[Exercise]) -> str:
    """Append a superset hint if any exercises are paired."""
    paired = {e.superset_with for e in exercises if e.superset_with}
    if not paired:
        return ""
    parts: list[str] = []
    for key in sorted(paired):
        group = [e.name for e in exercises if e.superset_with == key]
        if len(group) >= 2:
            parts.append(f"  - Superset: {' 与 '.join(group)}")
    if not parts:
        return ""
    return "**超级组 / Supersets:**\n" + "\n".join(parts) + "\n\n"


def generate_workout_note_body(day: WorkoutDay) -> str:
    """Return the full markdown body (without YAML frontmatter) for a training day."""
    lines: list[str] = []

    # Header
    lines.append(f"# {day.name} — {day.focus}")
    lines.append("")
    lines.append(
        "> 编辑说明：在 Obsidian 中勾选完成组数、填写重量/次数/RIR。"
    )
    lines.append("> 可拖动行调整顺序、替换动作、或添加自定义组。")
    lines.append("")

    # Superset overview
    ss = _superset_note(day.exercises)
    if ss:
        lines.append(ss)
        lines.append("")

    # Exercises
    for i, ex in enumerate(day.exercises, 1):
        lines.append(_exercise_to_md(i, ex))

    # Completion marker
    lines.append("")
    lines.append("<!-- workout:session -->")
    lines.append("")

    return "\n".join(lines)


# ── Note generation (with frontmatter) ───────────────────────────────────────


def _frontmatter(day: WorkoutDay | None, d: date) -> str:
    """YAML frontmatter for workout notes."""
    if day is None:
        return (
            "---\n"
            f"date: {d.isoformat()}\n"
            "type: workout/rest\n"
            f"training_day: rest\n"
            "completed: false\n"
            "---\n\n"
        )
    return (
        "---\n"
        f"date: {d.isoformat()}\n"
        "type: workout/session\n"
        f"training_day: {day.name}\n"
        f"focus: {day.focus}\n"
        "completed: false\n"
        f"total_sets: {sum(e.target_sets for e in day.exercises)}\n"
        "completed_sets: 0\n"
        "---\n\n"
    )


def generate_workout_note(
    settings: Settings,
    d: date | None = None,
    overwrite: bool = False,
) -> str | None:
    """Generate or update a workout note in the Obsidian vault for *d*.

    Returns the path string of the written file, or ``None`` for rest days
    (no file written — the vault already has no note on rest days by design,
    but a weekend check is exposed via :func:`rest_day_note` for callers that
    want to write one).

    Existing notes are preserved by default because the user edits weight,
    reps, RIR, checkboxes, and exercise order directly in Obsidian.
    Pass ``overwrite=True`` only for explicit regeneration.
    """
    if d is None:
        d = datetime.now(LOCAL_TZ).date()

    day_name = get_training_day(d)
    path = workout_note_path(settings, d)

    # On rest days we do NOT auto-generate a file (the user can create one).
    if day_name == "rest":
        return None

    day = WORKOUT_PLAN[day_name]
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists() and not overwrite:
        logger.info("workout note already exists; preserving user edits: %s", path)
        return str(path)

    body = _frontmatter(day, d) + generate_workout_note_body(day)
    path.write_text(body, encoding="utf-8")
    logger.info("wrote workout note: %s", path)
    return str(path)


def generate_workout_choice_notes(
    settings: Settings,
    d: date | None = None,
    overwrite: bool = False,
) -> list[str]:
    """Pre-generate mobile fallback candidate notes for every training day.

    These are plain markdown files linked from the daily note, so they work on
    mobile even when custom Obsidian plugins are not synced or enabled.
    Existing candidate notes are preserved by default.
    """
    if d is None:
        d = datetime.now(LOCAL_TZ).date()

    written: list[str] = []
    for day_name, day in WORKOUT_PLAN.items():
        path = workout_choice_note_path(settings, d, day_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and not overwrite:
            written.append(str(path))
            continue
        body = _frontmatter(day, d) + generate_workout_note_body(day)
        path.write_text(body, encoding="utf-8")
        written.append(str(path))

    rest_path = workout_choice_note_path(settings, d, "rest")
    if overwrite or not rest_path.exists():
        rest_path.parent.mkdir(parents=True, exist_ok=True)
        rest_path.write_text(_frontmatter(None, d) + rest_day_note(d, get_weekday_name(d)), encoding="utf-8")
    written.append(str(rest_path))
    return written


# ── Daily-note integration ───────────────────────────────────────────────────


def workout_daily_link(
    settings: Settings,
    d: date | None = None,
    day_name: DayName | None = None,
) -> str:
    """Return a markdown link line pointing to today's workout note.

    Example: ``- [健身 - Upper 1](Workout/2026-06-01.md)``
    """
    if d is None:
        d = datetime.now(LOCAL_TZ).date()
    day_name = day_name or get_training_day(d)
    if day_name == "rest":
        weekday = get_weekday_name(d)
        return f"- {weekday}：休息日（无训练）"
    return f"- [健身 - {day_name} ({day_name_cn(day_name)})](Workout/{d.isoformat()}.md)"


def workout_mobile_choice_links(d: date) -> list[str]:
    """Return plain wiki links for mobile fallback workout selection."""
    lines = [
        "### 手机无插件选择",
        "> 如果手机端没有按钮，直接点下面任意一个训练笔记开始记录。",
    ]
    for day_name in WORKOUT_PLAN:
        label = f"{day_name}｜{day_name_cn(day_name)}"
        lines.append(f"- [[Workout/Choices/{d.isoformat()} - {day_name}|{label}]]")
    lines.append(f"- [[Workout/Choices/{d.isoformat()} - rest|今天休息]]")
    return lines


def workout_web_ui_line(settings: Settings, d: date) -> str:
    """Return the daily-note line pointing to the workout web UI."""
    raw_token = getattr(settings, "workout_ui_access_token", "")
    token = raw_token.strip() if isinstance(raw_token, str) else ""
    token_query = f"&token={token}" if token else ""
    path = f"/workout?date={d.isoformat()}{token_query}"
    raw_base_url = getattr(settings, "workout_ui_base_url", "")
    base_url = raw_base_url.strip().rstrip("/") if isinstance(raw_base_url, str) else ""
    if base_url:
        return f"- [Web UI 训练界面]({base_url}{path})"
    return f"- Web UI 训练界面：`http://<Tailscale地址>:8081{path}`"


def _drop_stale_generated_fitness_lines(text: str, current_link: str) -> str:
    """Remove old system-generated fitness link lines, preserving manual notes."""
    cleaned: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == current_link.strip():
            continue
        if stripped.startswith("- [健身 - ") and "](Workout/" in stripped:
            continue
        if stripped.startswith("- 星期") and "休息日（无训练）" in stripped:
            continue
        cleaned.append(line)
    return "\n".join(cleaned).strip()


def upsert_fitness_section(
    settings: Settings,
    d: date | None = None,
    day_name: DayName | None = None,
    extra_lines: list[str] | None = None,
) -> str:
    """Upsert the managed fitness block in the daily note for date *d*.

    Adds a link to the workout note (if not rest day) plus any *extra_lines*.
    Uses an idempotent marker so only the managed block is replaced. Manual
    notes written under ``## 健身`` are preserved.

    Returns the managed body without marker comments.
    """
    if d is None:
        d = datetime.now(LOCAL_TZ).date()

    current_link = workout_daily_link(settings, d, day_name)
    web_link = workout_web_ui_line(settings, d)
    lines: list[str] = [
        "```cognitive-workout",
        f"date: {d.isoformat()}",
        "```",
        "> 没看到上面的选择按钮：切到阅读模式；或在命令面板搜索“选择今日训练”；手机端需启用 Cognitive OS Workout 插件。",
        current_link,
        web_link,
        "",
        *workout_mobile_choice_links(d),
    ]
    if extra_lines:
        lines.extend(extra_lines)
    body = "\n".join(lines) + "\n"

    note_datetime = datetime.combine(d, datetime.min.time(), tzinfo=LOCAL_TZ)
    path = _daily_note_path(settings, note_datetime)
    content = _read_or_create_note(path, settings, note_datetime)
    header = SECTION_HEADERS["fitness"]
    managed_block = f"{FITNESS_BLOCK_START}\n{body.rstrip()}\n{FITNESS_BLOCK_END}\n"

    header_index = content.find(header)
    if header_index == -1:
        content = content.rstrip() + f"\n\n{header}\n{managed_block}"
        path.write_text(content, encoding="utf-8")
        return body

    section_start = content.find("\n", header_index)
    if section_start == -1:
        section_start = len(content)
        before = content + "\n"
    else:
        section_start += 1
        before = content[:section_start]

    next_section_index = content.find("\n## ", section_start)
    if next_section_index == -1:
        section_body = content[section_start:]
        after = ""
    else:
        section_body = content[section_start:next_section_index]
        after = content[next_section_index:]

    start_index = section_body.find(FITNESS_BLOCK_START)
    end_index = section_body.find(FITNESS_BLOCK_END)
    if start_index != -1 and end_index != -1 and end_index > start_index:
        end_index += len(FITNESS_BLOCK_END)
        before_marker = _drop_stale_generated_fitness_lines(
            section_body[:start_index],
            current_link,
        )
        after_marker = _drop_stale_generated_fitness_lines(
            section_body[end_index:],
            current_link,
        )
        new_section_body = (
            before_marker
            + ("\n\n" if before_marker else "")
            + managed_block.rstrip()
            + ("\n\n" + after_marker if after_marker else "")
        ).rstrip() + "\n"
    else:
        preserved = _drop_stale_generated_fitness_lines(section_body, current_link)
        if preserved:
            new_section_body = managed_block + "\n" + preserved + "\n"
        else:
            new_section_body = managed_block

    path.write_text(before + new_section_body + after, encoding="utf-8")
    return body
