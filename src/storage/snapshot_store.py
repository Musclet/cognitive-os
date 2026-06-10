"""SnapshotStore — durable snapshot persistence (SQLite/PostgreSQL via SQLAlchemy)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import text

from src.storage.db import get_session

logger = logging.getLogger(__name__)

SCHEMA_DDL = [
    """CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_id   TEXT PRIMARY KEY,
    state         TEXT NOT NULL,
    last_sequence INTEGER NOT NULL,
    created_at    TEXT NOT NULL
)""",
]


class SnapshotStore:
    def __init__(self) -> None:
        self._initialized = False

    async def _ensure_schema(self) -> None:
        if self._initialized:
            return
        session = await get_session()
        try:
            for ddl in SCHEMA_DDL:
                await session.execute(text(ddl))
            await session.commit()
            self._initialized = True
        finally:
            await session.close()

    async def save(self, state: dict[str, Any], last_sequence: int) -> str:
        await self._ensure_schema()
        snapshot_id = str(uuid4())
        session = await get_session()
        try:
            state_json = json.dumps(state, ensure_ascii=False, default=str)
            created_at = datetime.now(timezone.utc).isoformat()
            await session.execute(
                text(
                    "INSERT INTO snapshots (snapshot_id, state, last_sequence, created_at) "
                    "VALUES (:sid, :state, :seq, :created)"
                ),
                {"sid": snapshot_id, "state": state_json, "seq": last_sequence, "created": created_at},
            )
            await session.commit()
            logger.debug("snapshot %s saved at sequence %d", snapshot_id, last_sequence)
            return snapshot_id
        finally:
            await session.close()

    async def get_latest(self) -> tuple[dict[str, Any], int] | None:
        await self._ensure_schema()
        session = await get_session()
        try:
            result = await session.execute(
                text(
                    "SELECT state, last_sequence FROM snapshots "
                    "ORDER BY last_sequence DESC LIMIT 1"
                )
            )
            row = result.mappings().first()
            if row is None:
                return None
            state = json.loads(row["state"])
            return state, row["last_sequence"]
        except (json.JSONDecodeError, KeyError) as exc:
            logger.warning("snapshot corrupt: %s", exc)
            return None
        finally:
            await session.close()

    async def get_all(self) -> list[dict[str, Any]]:
        await self._ensure_schema()
        session = await get_session()
        try:
            result = await session.execute(
                text("SELECT snapshot_id, last_sequence, created_at FROM snapshots ORDER BY last_sequence DESC")
            )
            return [dict(row) for row in result.mappings().all()]
        finally:
            await session.close()

    async def delete_older_than(self, sequence: int) -> int:
        await self._ensure_schema()
        session = await get_session()
        try:
            result = await session.execute(
                text("DELETE FROM snapshots WHERE last_sequence < :seq"),
                {"seq": sequence},
            )
            await session.commit()
            return result.rowcount or 0
        finally:
            await session.close()
