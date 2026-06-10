"""Shared async database connection (supports SQLite and PostgreSQL via SQLAlchemy)."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

_engine = None
_session_factory = None


async def init_db(database_url: str) -> None:
    """Initialize the async engine and session factory."""
    global _engine, _session_factory

    # Ensure data directory exists for SQLite (PostgreSQL connects via URL only)
    if database_url.startswith("sqlite"):
        import re
        match = re.search(r"sqlite\+aiosqlite:///(.+)", database_url)
        if match:
            db_path = Path(match.group(1))
            db_path.parent.mkdir(parents=True, exist_ok=True)

    _engine = create_async_engine(database_url, echo=False)
    _session_factory = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)


async def get_session() -> AsyncSession:
    """Get a new async session."""
    if _session_factory is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _session_factory()


async def close_db() -> None:
    """Close the database engine."""
    global _engine, _session_factory
    if _engine:
        await _engine.dispose()
        _engine = None
    _session_factory = None
