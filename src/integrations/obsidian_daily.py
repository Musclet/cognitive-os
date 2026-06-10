"""Obsidian daily note writer — deterministic, idempotent section upsert.

Locates daily note path from config, creates from template if missing,
and upserts stable sections without rewriting the whole file.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Any

from src.infrastructure.config import Settings

logger = logging.getLogger(__name__)
LOCAL_TZ = ZoneInfo("Asia/Singapore")

# ── Audit tracking ────────────────────────────────────────────────────────

_AUDIT_LOG: dict[str, Any] = {
    "last_write_path": "",
    "last_section": "",
    "skipped_duplicate_count": 0,
    "last_error": "",
    "write_count": 0,
}


def get_audit() -> dict[str, Any]:
    """Return current audit metadata."""
    return dict(_AUDIT_LOG)


def reset_audit() -> None:
    """Reset audit log."""
    _AUDIT_LOG.clear()
    _AUDIT_LOG.update({
        "last_write_path": "",
        "last_section": "",
        "skipped_duplicate_count": 0,
        "last_error": "",
        "write_count": 0,
    })

SECTION_HEADERS = {
    "art_plan": "## 🎨 绘画训练",
    "event_flow": "## 🧾 今日事件流",
    "finance": "## 资金流",
    "parent_funds": "## 家庭资金请求",
    # Stable fixed-structure sections (A.2)
    "plan": "## 今日计划",
    "actual": "## 实际完成",
    "deviation": "## 偏离原因",
    "art_training": "## 画画训练",
    "language": "## 英语/日语",
    "fitness": "## 健身",
    "system_obs": "## 系统观察",
}

# Idempotent event marker prefix — prevents duplicate lines on replay/retry.
_EVENT_MARKER_PREFIX = "<!-- obsidian-sink:"


def _daily_note_path(settings: Settings, date: datetime | None = None) -> Path:
    """Resolve daily note path: vault / daily_folder / M.D.md

    Example: .../桐一日/daily/5.31.md
    """
    if date is None:
        date = datetime.now(LOCAL_TZ)
    vault = Path(settings.obsidian_vault_path)
    folder = settings.obsidian_daily_folder
    # Non-ISO format: month.day (no leading zeros on month)
    filename = f"{date.month}.{date.day}.md"
    return vault / folder / filename


def _read_or_create_note(path: Path, settings: Settings, date: datetime | None = None) -> str:
    """Read existing daily note or create from template.

    Returns full file content as string.
    """
    if path.exists():
        return path.read_text(encoding="utf-8")

    # Try to create from template
    template_path = Path(settings.obsidian_vault_path) / settings.obsidian_daily_template_path
    if template_path.exists():
        template = template_path.read_text(encoding="utf-8")
        if date is None:
            date = datetime.now(LOCAL_TZ)
        weekday_names = ["一", "二", "三", "四", "五", "六", "日"]
        content = (
            template
            .replace("{{date}}", date.strftime("%Y-%m-%d"))
            .replace("{{weekday}}", weekday_names[date.weekday()])
            .replace("{{星期#}}", f"星期{weekday_names[date.weekday()]}")
            .replace("{{month}}", str(date.month))
            .replace("{{day}}", str(date.day))
        )
    else:
        content = f"# {date.strftime('%Y-%m-%d')}\n\n"
        content += "## 今日计划\n\n\n"
        content += "## 实际完成\n\n\n"
        content += "## 偏离原因\n\n\n"
        content += "## 🎨 绘画训练\n\n\n"
        content += "## 画画训练\n\n\n"
        content += "## 英语/日语\n\n\n"
        content += "## 健身\n\n\n"
        content += "## 系统观察\n\n\n"
        content += "## 🧾 今日事件流\n\n"

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    logger.info("created daily note from template: %s", path)
    return content


def _upsert_section(content: str, header: str, new_body: str) -> str:
    """Idempotently replace content under a section header.

    Preserves everything before the header and after the next section or EOF.
    Creates the header + body at end if header not found.
    """
    header_pattern = re.compile(re.escape(header) + r"\s*\n")
    match = header_pattern.search(content)
    if not match:
        # Append at end
        content = content.rstrip() + "\n\n" + header + "\n" + new_body.rstrip() + "\n"
        return content

    # Find next section (## ...) after this header
    after_header = match.end()
    rest = content[after_header:]
    next_section = re.search(r"\n## ", rest)
    if next_section:
        end = next_section.start() + 1  # keep the newline before next section
    else:
        end = len(rest)

    new_part = new_body.rstrip() + "\n"
    return content[:after_header] + new_part + content[after_header + end:]


class ObsidianDailyWriter:
    """Service for writing structured data to the current Obsidian daily note.

    Thread-safe for sequential calls. Idempotent — calling twice with same
    data produces the same file content.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or Settings()

    # ── Public API ────────────────────────────────────────────────────────

    def write_art_plan(
        self,
        target_minutes: int,
        blocks: list[dict[str, Any]],
        date: datetime | None = None,
    ) -> str:
        """Write or update the ## 🎨 绘画训练 section with today's art plan.

        Returns the section body that was written.
        """
        lines = [f"目标：{target_minutes} 分钟"]
        for i, b in enumerate(blocks, 1):
            start_s = b.get("start", "")[11:16] if b.get("start") else "???"
            end_s = b.get("end", "")[11:16] if b.get("end") else "???"
            title = b.get("title", "练习")
            lines.append(f"- [ ] {i}. {start_s}-{end_s} {title}（{b.get('duration_min', 0)}min）")
        body = "\n".join(lines)

        path = _daily_note_path(self._settings, date)
        content = _read_or_create_note(path, self._settings, date)
        content = _upsert_section(content, SECTION_HEADERS["art_plan"], body)
        path.write_text(content, encoding="utf-8")
        # Update audit
        _AUDIT_LOG["last_write_path"] = str(path)
        _AUDIT_LOG["last_section"] = SECTION_HEADERS["art_plan"]
        _AUDIT_LOG["write_count"] = _AUDIT_LOG.get("write_count", 0) + 1
        return body

    def write_event_line(self, line: str, date: datetime | None = None) -> None:
        """Append a single timestamped event line to ## 🧾 今日事件流."""
        path = _daily_note_path(self._settings, date)
        content = _read_or_create_note(path, self._settings, date)

        header = SECTION_HEADERS["event_flow"]
        header_pattern = re.compile(re.escape(header) + r"\s*\n")
        match = header_pattern.search(content)
        if not match:
            content = content.rstrip() + "\n\n" + header + "\n"
            path.write_text(content, encoding="utf-8")
            content = _read_or_create_note(path, self._settings, date)

        # Re-read after potential write
        if not match:
            header_pattern = re.compile(re.escape(header) + r"\s*\n")
            match = header_pattern.search(content)

        after_header = match.end()
        rest = content[after_header:]
        next_section = re.search(r"\n## ", rest)
        if next_section:
            insert_pos = after_header + next_section.start()
        else:
            insert_pos = len(content.rstrip()) + 1

        timestamp = datetime.now(LOCAL_TZ).strftime("%H:%M")
        new_line = f"- {timestamp} {line}\n"
        new_content = content[:insert_pos] + new_line + content[insert_pos:]
        path.write_text(new_content, encoding="utf-8")

    def write_progress(
        self,
        completed_minutes: int,
        target_minutes: int,
        sessions: list[dict[str, Any]],
        date: datetime | None = None,
    ) -> str:
        """Update the art plan section with progress info.

        Appends a progress summary line to the existing art plan section.
        Returns the appended body.
        """
        remaining = max(0, target_minutes - completed_minutes)
        lines = [f"已完成：{completed_minutes}min / {target_minutes}min（剩余 {remaining}min）"]
        for s in sessions:
            what = s.get("type", "练习")
            dur = s.get("duration_minutes", 0)
            note = s.get("note", "")
            note_str = f" — {note}" if note else ""
            lines.append(f"  - {what} {dur}min{note_str}")
        body = "\n".join(lines)

        path = _daily_note_path(self._settings, date)
        content = _read_or_create_note(path, self._settings, date)

        # We add a "进度" sub-section under art_plan
        header = SECTION_HEADERS["art_plan"]
        progress_marker = "### 进度"
        new_section = f"\n{progress_marker}\n{body}\n"

        # Check if progress marker already exists
        if progress_marker in content:
            content = _upsert_section(content, progress_marker, body)
        else:
            # Append progress after the checklist section (before next ## or EOF)
            header_pattern = re.compile(re.escape(header) + r"\s*\n")
            match = header_pattern.search(content)
            if match:
                after_header = match.end()
                rest = content[after_header:]
                next_section = re.search(r"\n## ", rest)
                if next_section:
                    insert_pos = after_header + next_section.start()
                else:
                    insert_pos = len(content.rstrip()) + 1
                content = content[:insert_pos] + new_section + content[insert_pos:]

        path.write_text(content, encoding="utf-8")
        return body

    def write_section(
        self,
        header: str,
        body: str,
        date: datetime | None = None,
    ) -> str:
        """Upsert any section header with the given body.

        Returns the body that was written.
        Primarily for the fixed-structure sections added in A.2.
        """
        path = _daily_note_path(self._settings, date)
        content = _read_or_create_note(path, self._settings, date)
        content = _upsert_section(content, header, body)
        path.write_text(content, encoding="utf-8")
        # Update audit
        _AUDIT_LOG["last_write_path"] = str(path)
        _AUDIT_LOG["last_section"] = header
        _AUDIT_LOG["write_count"] = _AUDIT_LOG.get("write_count", 0) + 1
        return body

    def write_morning_entry(
        self,
        mood_score: int | None = None,
        arrangements: list[str] | None = None,
        art_target_minutes: int | None = None,
        triggered_refresh: list[str] | None = None,
        date: datetime | None = None,
    ) -> dict[str, str]:
        """Write morning greeting data into the fixed-structure sections.

        Populates ## 今日计划 and ## 系统观察 sections.
        Returns dict of {section_key: body_written}.
        """
        written: dict[str, str] = {}

        # ── 今日计划 ──────────────────────────────────────────────────────
        plan_lines = []
        if arrangements:
            for a in arrangements:
                plan_lines.append(f"- [ ] {a}")
        else:
            plan_lines.append("- [ ] 待补充")
        if art_target_minutes:
            plan_lines.append(f"- [ ] 画画 {art_target_minutes}min")
        plan_body = "\n".join(plan_lines)
        written["plan"] = self.write_section(SECTION_HEADERS["plan"], plan_body, date)

        # ── 系统观察 ──────────────────────────────────────────────────────
        obs_lines = []
        if mood_score is not None:
            labels = {1: "极低", 2: "很低", 3: "偏低", 4: "略低", 5: "中性",
                      6: "略高", 7: "偏高", 8: "很高", 9: "极高", 10: "巅峰"}
            label = labels.get(mood_score, "")
            obs_lines.append(f"- 心情：{mood_score}/10（{label}）")
        if art_target_minutes:
            obs_lines.append(f"- 绘画目标：{art_target_minutes}min")
        if triggered_refresh:
            obs_lines.append(f"- 已请求数据刷新：{'、'.join(triggered_refresh)}")
        if obs_lines:
            body = "\n".join(obs_lines)
            try:
                written["system_obs"] = self.write_section(SECTION_HEADERS["system_obs"], body, date)
            except Exception as exc:
                _AUDIT_LOG["last_error"] = str(exc)
                logger.warning("Obsidian system_obs write failed: %s", exc)

        return written

    # ── Finance / Money Reality ──────────────────────────────────────────

    def write_finance_line_idempotent(
        self,
        line: str,
        event_id: str,
        date: datetime | None = None,
    ) -> bool:
        """Append a finance transaction line to ## 资金流 with idempotency.

        Lines look like: - 奶茶 18元 | 情绪消费
        Returns True when written, False when skipped (duplicate).
        """
        path = _daily_note_path(self._settings, date)
        if not path.parent.exists():
            path.parent.mkdir(parents=True, exist_ok=True)

        marker = f"{_EVENT_MARKER_PREFIX}{event_id} -->"
        if path.exists():
            existing = path.read_text(encoding="utf-8")
            if marker in existing:
                _AUDIT_LOG["skipped_duplicate_count"] = _AUDIT_LOG.get("skipped_duplicate_count", 0) + 1
                return False

        # Get or create the ## 资金流 section, append line
        header = SECTION_HEADERS["finance"]
        content = _read_or_create_note(path, self._settings, date)
        content = _upsert_section(content, header, line)
        content += f"{marker}\n"
        path.write_text(content, encoding="utf-8")
        _AUDIT_LOG["last_write_path"] = str(path)
        _AUDIT_LOG["last_section"] = header
        _AUDIT_LOG["write_count"] = _AUDIT_LOG.get("write_count", 0) + 1
        return True

    def write_parent_fund_line_idempotent(
        self,
        line: str,
        event_id: str,
        date: datetime | None = None,
    ) -> bool:
        """Append a parent fund line to ## 家庭资金请求 with idempotency.

        Lines look like: - 要了150元买画材
        Returns True when written, False when skipped (duplicate).
        """
        path = _daily_note_path(self._settings, date)
        if not path.parent.exists():
            path.parent.mkdir(parents=True, exist_ok=True)

        marker = f"{_EVENT_MARKER_PREFIX}{event_id} -->"
        if path.exists():
            existing = path.read_text(encoding="utf-8")
            if marker in existing:
                _AUDIT_LOG["skipped_duplicate_count"] = _AUDIT_LOG.get("skipped_duplicate_count", 0) + 1
                return False

        header = SECTION_HEADERS["parent_funds"]
        content = _read_or_create_note(path, self._settings, date)
        content = _upsert_section(content, header, line)
        content += f"{marker}\n"
        path.write_text(content, encoding="utf-8")
        _AUDIT_LOG["last_write_path"] = str(path)
        _AUDIT_LOG["last_section"] = header
        _AUDIT_LOG["write_count"] = _AUDIT_LOG.get("write_count", 0) + 1
        return True

    def write_event_line_idempotent(
        self,
        line: str,
        event_id: str,
        date: datetime | None = None,
    ) -> bool:
        """Append an event line to ## 🧾 今日事件流 with an idempotency marker.

        If the marker for *event_id* already exists in the file, the line
        is skipped (replay safety). Returns True when written, False when
        skipped.
        """
        path = _daily_note_path(self._settings, date)
        if not path.parent.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            existing = path.read_text(encoding="utf-8")
            marker = f"{_EVENT_MARKER_PREFIX}{event_id} -->"
            if marker in existing:
                logger.debug("event %s already in daily note, skipping", event_id)
                _AUDIT_LOG["skipped_duplicate_count"] = _AUDIT_LOG.get("skipped_duplicate_count", 0) + 1
                return False

        # Write via existing method (which does the upsert)
        self.write_event_line(line, date)

        # Append the marker after the event content
        if path.exists():
            content = path.read_text(encoding="utf-8")
            marker = f"{_EVENT_MARKER_PREFIX}{event_id} -->"
            if marker not in content:
                content += f"{marker}\n"
                path.write_text(content, encoding="utf-8")
        return True

    def update_checkbox(self, block_index: int, checked: bool = True, date: datetime | None = None) -> None:
        """Check or uncheck a specific art block checkbox in the daily note."""
        path = _daily_note_path(self._settings, date)
        if not path.exists():
            return
        content = path.read_text(encoding="utf-8")
        marker = "- [ ]" if not checked else "- [x]"
        pattern = re.compile(rf"^- \[.\] \d+\. ", re.MULTILINE)
        matches = list(pattern.finditer(content))
        if block_index < len(matches):
            m = matches[block_index]
            content = content[:m.start()] + marker + content[m.start() + 5:]
            path.write_text(content, encoding="utf-8")

    @property
    def daily_note_path(self) -> str:
        return str(_daily_note_path(self._settings))
