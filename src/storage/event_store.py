"""EventStore — append-only event log (SQLite/PostgreSQL via SQLAlchemy).

All events are persisted before delivery. Immutable after write.
"""

from __future__ import annotations

import logging

from sqlalchemy import text

from src.core.events import Event
from src.storage.db import get_session

logger = logging.getLogger(__name__)

SCHEMA_DDL_SQLITE = [
    """CREATE TABLE IF NOT EXISTS event_log (
    sequence       INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id       TEXT NOT NULL UNIQUE,
    event_type     TEXT NOT NULL,
    aggregate_id   TEXT NOT NULL,
    aggregate_type TEXT NOT NULL,
    timestamp      TEXT NOT NULL,
    causation_id   TEXT,
    payload        TEXT NOT NULL,
    metadata       TEXT NOT NULL
)""",
    "CREATE INDEX IF NOT EXISTS idx_event_log_type ON event_log(event_type)",
    "CREATE INDEX IF NOT EXISTS idx_event_log_aggregate ON event_log(aggregate_id)",
    "CREATE INDEX IF NOT EXISTS idx_event_log_causation ON event_log(causation_id)",
]

SCHEMA_DDL_POSTGRES = [
    """CREATE TABLE IF NOT EXISTS event_log (
    sequence       SERIAL PRIMARY KEY,
    event_id       TEXT NOT NULL UNIQUE,
    event_type     TEXT NOT NULL,
    aggregate_id   TEXT NOT NULL,
    aggregate_type TEXT NOT NULL,
    timestamp      TEXT NOT NULL,
    causation_id   TEXT,
    payload        TEXT NOT NULL,
    metadata       TEXT NOT NULL
)""",
    "CREATE INDEX IF NOT EXISTS idx_event_log_type ON event_log(event_type)",
    "CREATE INDEX IF NOT EXISTS idx_event_log_aggregate ON event_log(aggregate_id)",
    "CREATE INDEX IF NOT EXISTS idx_event_log_causation ON event_log(causation_id)",
]


class EventStore:
    """Append-only event log backed by the configured database (SQLite or PostgreSQL)."""

    def __init__(self) -> None:
        self._initialized = False

    async def _ensure_schema(self) -> None:
        if self._initialized:
            return
        session = await get_session()
        try:
            # Detect database type from engine URL
            from sqlalchemy.ext.asyncio import AsyncSession
            engine_url = str(session.get_bind().url)
            ddl = SCHEMA_DDL_POSTGRES if "postgresql" in engine_url else SCHEMA_DDL_SQLITE
            for stmt in ddl:
                await session.execute(text(stmt))
            await session.commit()
            self._initialized = True
        finally:
            await session.close()

    async def append(self, event: Event) -> int:
        """Append an event to the log. Returns the sequence number.

        Raises if event_id already exists (UNIQUE constraint).
        """
        await self._ensure_schema()
        data = event.to_dict()
        session = await get_session()
        try:
            await session.execute(
                text(
                    "INSERT INTO event_log (event_id, event_type, aggregate_id, "
                    "aggregate_type, timestamp, causation_id, payload, metadata) "
                    "VALUES (:event_id, :event_type, :aggregate_id, :aggregate_type, "
                    ":timestamp, :causation_id, :payload, :metadata)"
                ),
                data,
            )
            await session.commit()
            # Get the assigned sequence
            row = await session.execute(
                text("SELECT sequence FROM event_log WHERE event_id = :eid"),
                {"eid": data["event_id"]},
            )
            seq = row.scalar()
            logger.debug("appended event %s at sequence %d", data["event_id"], seq)
            return seq
        finally:
            await session.close()

    async def replay_all(self) -> list[Event]:
        """Return all events ordered by sequence."""
        return await self._query(
            "SELECT * FROM event_log ORDER BY sequence ASC"
        )

    async def replay_from(self, sequence: int) -> list[Event]:
        """Return events after the given sequence, ordered."""
        return await self._query(
            "SELECT * FROM event_log WHERE sequence > :seq ORDER BY sequence ASC",
            {"seq": sequence},
        )

    async def get_recent(self, n: int = 20) -> list[Event]:
        """Return the most recent N events."""
        events = await self._query(
            "SELECT * FROM event_log ORDER BY sequence DESC LIMIT :n",
            {"n": n},
        )
        return list(reversed(events))

    async def get_by_aggregate(self, aggregate_id: str) -> list[Event]:
        """Return all events for a given aggregate."""
        return await self._query(
            "SELECT * FROM event_log WHERE aggregate_id = :aid ORDER BY sequence ASC",
            {"aid": aggregate_id},
        )

    async def get_by_type(self, event_type: str) -> list[Event]:
        """Return all events of a given type."""
        return await self._query(
            "SELECT * FROM event_log WHERE event_type = :etype ORDER BY sequence ASC",
            {"etype": event_type},
        )

    async def get_by_causation(self, causation_id: str) -> list[Event]:
        """Return events caused by the given event."""
        return await self._query(
            "SELECT * FROM event_log WHERE causation_id = :cid ORDER BY sequence ASC",
            {"cid": causation_id},
        )

    async def get_by_event_id(self, event_id: str) -> Event | None:
        """Return a single event by its event_id."""
        events = await self._query(
            "SELECT * FROM event_log WHERE event_id = :eid",
            {"eid": event_id},
        )
        return events[0] if events else None

    async def count(self) -> int:
        """Return total number of events."""
        await self._ensure_schema()
        session = await get_session()
        try:
            result = await session.execute(text("SELECT COUNT(*) FROM event_log"))
            return result.scalar() or 0
        finally:
            await session.close()

    async def last_sequence(self) -> int:
        """Return the highest sequence number, or 0 if empty."""
        await self._ensure_schema()
        session = await get_session()
        try:
            result = await session.execute(text("SELECT MAX(sequence) FROM event_log"))
            val = result.scalar()
            return val if val is not None else 0
        finally:
            await session.close()

    async def _query(self, sql: str, params: dict | None = None) -> list[Event]:
        """Execute a query and deserialize rows into Events."""
        await self._ensure_schema()
        session = await get_session()
        try:
            result = await session.execute(text(sql), params or {})
            rows = result.mappings().all()
            events = []
            for row in rows:
                data = dict(row)
                event = Event.from_dict(data)
                if data.get("sequence") is not None:
                    event._sequence = int(data["sequence"])
                events.append(event)
            return events
        finally:
            await session.close()
