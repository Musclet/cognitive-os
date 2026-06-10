"""Shared pytest fixtures."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest_asyncio

from src.storage.db import close_db, init_db


@pytest_asyncio.fixture(autouse=True)
async def isolated_storage_db(request):
    """Give storage tests a fresh SQLite database per test."""
    if request.node.path.name not in {"test_event_store.py", "test_snapshot_store.py"}:
        yield
        return

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        await init_db(f"sqlite+aiosqlite:///{db_path}")
        try:
            yield
        finally:
            await close_db()
