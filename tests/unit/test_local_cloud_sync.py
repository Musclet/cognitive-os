"""Local domestic-source cloud sync helper tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.local_cloud_sync import normalize_database_url


def test_normalize_database_url_for_neon_asyncpg():
    normalized = normalize_database_url(
        "postgresql://user:pass@example.neon.tech/db"
        "?sslmode=require&channel_binding=require"
    )

    assert normalized.startswith("postgresql+asyncpg://")
    assert "ssl=require" in normalized
    assert "sslmode" not in normalized
    assert "channel_binding" not in normalized
    assert "pass" in normalized


def test_normalize_database_url_rejects_sqlite():
    with pytest.raises(
        ValueError,
        match="local_cloud_database_must_be_postgresql",
    ):
        normalize_database_url("sqlite+aiosqlite:///data/local.db")


def test_windows_task_scripts_use_dpapi_secret_files():
    root = Path(__file__).resolve().parents[2]
    installer = (
        root / "scripts" / "install_local_cloud_sync_task.ps1"
    ).read_text(encoding="utf-8")
    runner = (
        root / "scripts" / "run_local_cloud_sync.ps1"
    ).read_text(encoding="utf-8")

    assert "Export-Clixml" in installer
    assert "New-ScheduledTaskTrigger -Daily -At \"07:00\"" in installer
    assert "StartWhenAvailable" in installer
    assert "Import-Clixml" in runner
    assert "scripts\\local_cloud_sync.py" in runner
