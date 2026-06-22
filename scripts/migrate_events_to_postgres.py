"""Idempotently migrate the append-only event log to a cloud database.

Default behavior is dry-run. Pass ``--apply`` to write the target database.
The script never prints event payloads, metadata, or database URLs.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.storage.event_store import SCHEMA_DDL_POSTGRES, SCHEMA_DDL_SQLITE


EVENT_COLUMNS = (
    "sequence",
    "event_id",
    "event_type",
    "aggregate_id",
    "aggregate_type",
    "timestamp",
    "causation_id",
    "payload",
    "metadata",
)


def sqlite_url(path: str) -> str:
    resolved = Path(path).expanduser().resolve()
    return f"sqlite+aiosqlite:///{resolved.as_posix()}"


def normalize_target_url(raw_url: str) -> str:
    url = make_url(raw_url)
    if not url.drivername.startswith("postgresql"):
        return raw_url
    query = dict(url.query)
    sslmode = query.pop("sslmode", None)
    query.pop("channel_binding", None)
    if sslmode and "ssl" not in query:
        query["ssl"] = sslmode
    return url.set(
        drivername="postgresql+asyncpg",
        query=query,
    ).render_as_string(hide_password=False)


async def _ensure_schema(engine: AsyncEngine) -> None:
    ddl = (
        SCHEMA_DDL_POSTGRES
        if engine.dialect.name == "postgresql"
        else SCHEMA_DDL_SQLITE
    )
    async with engine.begin() as connection:
        for statement in ddl:
            await connection.execute(text(statement))


async def _load_rows(
    engine: AsyncEngine,
    *,
    missing_table_is_empty: bool = False,
) -> list[dict[str, Any]]:
    try:
        async with engine.connect() as connection:
            result = await connection.execute(
                text("SELECT * FROM event_log ORDER BY sequence ASC")
            )
            return [dict(row) for row in result.mappings().all()]
    except (OperationalError, ProgrammingError):
        if missing_table_is_empty:
            return []
        raise


def _migration_plan(
    source_rows: list[dict[str, Any]],
    target_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int, list[int]]:
    target_ids = {str(row["event_id"]) for row in target_rows}
    target_sequences = {
        int(row["sequence"]): str(row["event_id"])
        for row in target_rows
    }
    inserts: list[dict[str, Any]] = []
    skipped = 0
    conflicts: list[int] = []

    for row in source_rows:
        event_id = str(row["event_id"])
        sequence = int(row["sequence"])
        if event_id in target_ids:
            skipped += 1
            continue
        occupied_by = target_sequences.get(sequence)
        if occupied_by and occupied_by != event_id:
            conflicts.append(sequence)
            continue
        inserts.append(row)

    return inserts, skipped, conflicts


async def migrate_event_log(
    source_url: str,
    target_url: str,
    *,
    apply: bool,
) -> dict[str, Any]:
    source_engine = create_async_engine(source_url, echo=False)
    target_engine = create_async_engine(normalize_target_url(target_url), echo=False)
    try:
        source_rows = await _load_rows(source_engine)
        if apply:
            await _ensure_schema(target_engine)
        target_rows = await _load_rows(
            target_engine,
            missing_table_is_empty=not apply,
        )
        inserts, skipped, conflicts = _migration_plan(source_rows, target_rows)
        if conflicts:
            return {
                "ok": False,
                "mode": "apply" if apply else "dry_run",
                "source_count": len(source_rows),
                "target_count_before": len(target_rows),
                "would_insert": len(inserts),
                "skipped_existing": skipped,
                "sequence_conflict_count": len(conflicts),
                "error_code": "target_sequence_conflict",
            }

        inserted = 0
        if apply and inserts:
            columns = ", ".join(EVENT_COLUMNS)
            values = ", ".join(f":{column}" for column in EVENT_COLUMNS)
            statement = text(
                f"INSERT INTO event_log ({columns}) VALUES ({values}) "
                "ON CONFLICT (event_id) DO NOTHING"
            )
            async with target_engine.begin() as connection:
                for row in inserts:
                    result = await connection.execute(statement, row)
                    inserted += max(int(result.rowcount or 0), 0)
                if target_engine.dialect.name == "postgresql":
                    await connection.execute(text(
                        "SELECT setval("
                        "pg_get_serial_sequence('event_log', 'sequence'), "
                        "COALESCE((SELECT MAX(sequence) FROM event_log), 1), "
                        "(SELECT COUNT(*) > 0 FROM event_log)"
                        ")"
                    ))

        return {
            "ok": True,
            "mode": "apply" if apply else "dry_run",
            "source_count": len(source_rows),
            "target_count_before": len(target_rows),
            "would_insert": len(inserts),
            "inserted": inserted,
            "skipped_existing": skipped,
            "sequence_conflict_count": 0,
        }
    finally:
        await source_engine.dispose()
        await target_engine.dispose()


def _backup_source(source_path: str, backup_dir: str) -> str:
    source = Path(source_path).expanduser().resolve()
    destination_dir = Path(backup_dir).expanduser()
    destination_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = destination_dir / f"{source.stem}-{stamp}{source.suffix}.bak"
    with sqlite3.connect(source) as source_db, sqlite3.connect(destination) as backup_db:
        source_db.backup(backup_db)
    return destination.name


def _default_backup_dir() -> str:
    preferred = Path(r"D:\CognitiveOSRuntime\New project 8\migration-backups")
    if os.name == "nt":
        return str(preferred)
    return "migration-backups"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="data/cognitive_os.db")
    parser.add_argument("--target-env", default="NEON_DATABASE_URL")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--backup-dir",
        default=os.environ.get("COGNITIVE_OS_BACKUP_DIR", _default_backup_dir()),
    )
    args = parser.parse_args()

    source_path = Path(args.source).expanduser()
    if not source_path.is_file():
        print(json.dumps({
            "ok": False,
            "error_code": "source_database_missing",
        }))
        return 2

    target_url = os.environ.get(args.target_env, "").strip()
    if not target_url:
        print(json.dumps({
            "ok": False,
            "error_code": "target_database_url_missing",
            "target_env": args.target_env,
        }))
        return 2

    backup_name = ""
    if args.apply:
        backup_name = _backup_source(str(source_path), args.backup_dir)

    result = asyncio.run(migrate_event_log(
        sqlite_url(str(source_path)),
        target_url,
        apply=args.apply,
    ))
    if backup_name:
        result["backup_created"] = True
        result["backup_name"] = backup_name
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
