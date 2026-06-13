"""Runtime composition contract tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.core.events import EventType
from src.infrastructure.config import Settings
from src.runtime.composition import MODE_CAPABILITIES, build_runtime


def _settings(tmp_path: Path, name: str) -> Settings:
    return Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / f'{name}.db'}",
        snapshot_path=str(tmp_path / f"{name}-snapshot.json"),
        web_ui_pin="1234",
        web_ui_session_secret="composition-test",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["local", "render", "worker"])
async def test_modes_share_core_composition(tmp_path: Path, mode: str):
    runtime = await build_runtime(_settings(tmp_path, mode), mode=mode)
    try:
        assert runtime.mode == mode
        assert runtime.event_store is not None
        assert runtime.snapshot_store is not None
        assert runtime.bus is not None
        assert runtime.tracer is not None
        assert runtime.state_engine is not None
        assert runtime.pipeline is not None
        assert runtime.dead_letter is not None
        assert runtime.capabilities == MODE_CAPABILITIES[mode]
        assert (runtime.app is not None) is (mode != "worker")

        subscribers = runtime.bus.subscriber_count
        assert all(subscribers.get(event_type, 0) >= 1 for event_type in EventType)
    finally:
        await runtime.close()


def test_mode_capability_differences_are_explicit():
    assert "telegram" in MODE_CAPABILITIES["local"]
    assert "scheduler" in MODE_CAPABILITIES["local"]
    assert "google_calendar_read" in MODE_CAPABILITIES["render"]
    assert "jwxt_read" in MODE_CAPABILITIES["render"]
    assert MODE_CAPABILITIES["worker"] == frozenset({"core", "heartbeat"})
