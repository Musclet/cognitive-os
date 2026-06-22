"""SQLite-to-cloud event migration tests using temporary databases."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from scripts.migrate_events_to_postgres import (
    _ensure_schema,
    migrate_event_log,
    normalize_target_url,
    sqlite_url,
)


async def _seed(url: str) -> None:
    engine = create_async_engine(url)
    try:
        await _ensure_schema(engine)
        async with engine.begin() as connection:
            for sequence in (1, 2):
                await connection.execute(text(
                    "INSERT INTO event_log "
                    "(sequence, event_id, event_type, aggregate_id, "
                    "aggregate_type, timestamp, causation_id, payload, metadata) "
                    "VALUES (:sequence, :event_id, :event_type, :aggregate_id, "
                    ":aggregate_type, :timestamp, NULL, :payload, :metadata)"
                ), {
                    "sequence": sequence,
                    "event_id": f"event-{sequence}",
                    "event_type": "system.started",
                    "aggregate_id": "system",
                    "aggregate_type": "system",
                    "timestamp": f"2026-06-22T00:00:0{sequence}+00:00",
                    "payload": "{}",
                    "metadata": "{}",
                })
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_migration_dry_run_apply_and_rerun_are_idempotent(tmp_path: Path):
    source = sqlite_url(str(tmp_path / "source.db"))
    target = sqlite_url(str(tmp_path / "target.db"))
    await _seed(source)
    target_engine = create_async_engine(target)
    await _ensure_schema(target_engine)
    await target_engine.dispose()

    dry_run = await migrate_event_log(source, target, apply=False)
    first_apply = await migrate_event_log(source, target, apply=True)
    second_apply = await migrate_event_log(source, target, apply=True)

    assert dry_run["would_insert"] == 2
    assert dry_run["inserted"] == 0
    assert first_apply["inserted"] == 2
    assert second_apply["inserted"] == 0
    assert second_apply["skipped_existing"] == 2

    engine = create_async_engine(target)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(text(
                "SELECT sequence, event_id FROM event_log ORDER BY sequence"
            ))
            assert result.all() == [(1, "event-1"), (2, "event-2")]
    finally:
        await engine.dispose()


def test_normalize_neon_url_uses_asyncpg_and_safe_ssl_parameter():
    normalized = normalize_target_url(
        "postgresql://user:pass@example.neon.tech/db"
        "?sslmode=require&channel_binding=require"
    )
    assert normalized.startswith("postgresql+asyncpg://")
    assert "user:pass@" in normalized
    assert "ssl=require" in normalized
    assert "sslmode" not in normalized
    assert "channel_binding" not in normalized
