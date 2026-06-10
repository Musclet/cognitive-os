#!/usr/bin/env python3
"""CLI: generate today's workout note for manual testing.

Usage:
    python scripts/generate_workout_note.py
    python scripts/generate_workout_note.py --date 2026-06-08
    python scripts/generate_workout_note.py --date 2026-06-08 --dry-run
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

# Ensure repo root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.infrastructure.config import Settings
from src.domain.fitness.generator import (
    generate_workout_choice_notes,
    generate_workout_note,
    workout_daily_link,
    upsert_fitness_section,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a workout note in the Obsidian vault.")
    parser.add_argument("--date", type=str, default=None, help="Date in YYYY-MM-DD format (default: today)")
    parser.add_argument("--dry-run", action="store_true", help="Print to stdout instead of writing")
    args = parser.parse_args()

    d = date.fromisoformat(args.date) if args.date else date.today()
    settings = Settings()

    choice_paths = generate_workout_choice_notes(settings, d)
    path = generate_workout_note(settings, d)
    upsert_fitness_section(settings, d)
    if path is None:
        name_cn = ["一", "二", "三", "四", "五", "六", "日"][d.weekday()]
        print(f"{d.isoformat()} 星期{name_cn} — 休息日，未生成训练笔记")
        print(f"链接提示: {workout_daily_link(settings, d)}")
        print("📋 Daily note updated")
        return

    if args.dry_run:
        print(Path(path).read_text(encoding="utf-8"))
    else:
        print(f"✅ Generated: {path}")

    # Also show the daily link
    print(f"📋 Daily note link: {workout_daily_link(settings, d)}")
    print("📋 Daily note updated")
    print(f"📱 Mobile choices generated: {len(choice_paths)}")


if __name__ == "__main__":
    main()
