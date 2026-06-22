"""Cloud sync orchestration and protected endpoint tests."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.events import AggregateType, Event, EventType
from src.core.state_engine import StateEngine
from src.infrastructure.config import Settings
from src.interface.api.cloud_sync_routes import router
from src.services.cloud_sync import CloudSyncInProgress, CloudSyncService


def _completed(source: str, count: int = 1) -> Event:
    payload = {"source": source, "count": count, "pulled_count": count}
    if source == "jwxt":
        payload["temporal_blocks_count"] = count
    if source == "chaoxing":
        payload["homework_count"] = count
    return Event(
        event_type=EventType.CONNECTOR_FETCH_COMPLETED,
        aggregate_id=source,
        aggregate_type=AggregateType.SYSTEM,
        payload=payload,
    )


def _failed(source: str, error_code: str) -> Event:
    return Event(
        event_type=EventType.CONNECTOR_FETCH_FAILED,
        aggregate_id=source,
        aggregate_type=AggregateType.SYSTEM,
        payload={"source": source, "error_code": error_code},
    )


@pytest.mark.asyncio
async def test_cloud_sync_runs_sources_in_fixed_order():
    calls: list[str] = []

    async def run(event: Event):
        source = str(event.payload["source"])
        calls.append(source)
        return [event, _completed(source, len(calls))]

    pipeline = SimpleNamespace(run=run)
    service = CloudSyncService(
        pipeline,
        StateEngine(),
        Settings(_env_file=None, cloud_sync_source_timeout_seconds=1),
    )

    result = await service.run(trigger="test")

    assert calls == ["jwxt", "chaoxing", "google_calendar"]
    assert result["ok"] is True
    assert result["status"] == "completed"
    assert result["sources"]["jwxt"]["temporal_blocks_count"] == 1
    assert result["sources"]["chaoxing"]["homework_count"] == 2
    assert result["sources"]["google_calendar"]["count"] == 3


@pytest.mark.asyncio
async def test_cloud_sync_runs_only_selected_sources_in_fixed_order():
    calls: list[str] = []

    async def run(event: Event):
        source = str(event.payload["source"])
        calls.append(source)
        return [event, _completed(source)]

    service = CloudSyncService(
        SimpleNamespace(run=run),
        StateEngine(),
        Settings(_env_file=None, cloud_sync_source_timeout_seconds=1),
    )

    result = await service.run(
        trigger="local",
        sources=("jwxt", "chaoxing"),
    )

    assert calls == ["jwxt", "chaoxing"]
    assert set(result["sources"]) == {"jwxt", "chaoxing"}


@pytest.mark.asyncio
async def test_cloud_sync_rejects_unknown_source():
    service = CloudSyncService(
        SimpleNamespace(run=AsyncMock()),
        StateEngine(),
        Settings(_env_file=None),
    )

    with pytest.raises(ValueError, match="invalid_cloud_sync_sources"):
        await service.run(trigger="test", sources=("unknown",))


@pytest.mark.asyncio
async def test_cloud_state_refresh_applies_only_new_events():
    state_engine = StateEngine()
    existing = _completed("google_calendar", 1)
    new_event = _completed("jwxt", 2)
    await state_engine.apply(existing)
    store = SimpleNamespace(
        replay_all=AsyncMock(return_value=[existing, new_event]),
    )
    service = CloudSyncService(
        SimpleNamespace(run=AsyncMock()),
        state_engine,
        Settings(_env_file=None),
    )

    result = await service.refresh_from_event_store(store)

    assert result["ok"] is True
    assert result["event_store_count"] == 2
    assert result["new_events"] == 1
    assert state_engine.applied_count == 2


@pytest.mark.asyncio
async def test_cloud_sync_continues_after_one_source_fails():
    calls: list[str] = []

    async def run(event: Event):
        source = str(event.payload["source"])
        calls.append(source)
        terminal = (
            _failed(source, "chaoxing_session_expired")
            if source == "chaoxing"
            else _completed(source)
        )
        return [event, terminal]

    service = CloudSyncService(
        SimpleNamespace(run=run),
        StateEngine(),
        Settings(_env_file=None, cloud_sync_source_timeout_seconds=1),
    )

    result = await service.run(trigger="test")

    assert calls == ["jwxt", "chaoxing", "google_calendar"]
    assert result["ok"] is False
    assert result["status"] == "partial"
    assert (
        result["sources"]["chaoxing"]["error_code"]
        == "chaoxing_session_expired"
    )


@pytest.mark.asyncio
async def test_cloud_sync_continues_after_pipeline_exception():
    calls: list[str] = []

    async def run(event: Event):
        source = str(event.payload["source"])
        calls.append(source)
        if source == "chaoxing":
            raise RuntimeError("sensitive upstream detail")
        return [event, _completed(source)]

    service = CloudSyncService(
        SimpleNamespace(run=run),
        StateEngine(),
        Settings(_env_file=None, cloud_sync_source_timeout_seconds=1),
    )

    result = await service.run(trigger="test")

    assert calls == ["jwxt", "chaoxing", "google_calendar"]
    assert result["status"] == "partial"
    assert result["sources"]["chaoxing"]["error_code"] == "sync_internal_error"
    assert "sensitive upstream detail" not in str(result)


@pytest.mark.asyncio
async def test_cloud_sync_waits_for_background_chaoxing_terminal():
    state_engine = StateEngine()

    async def run(event: Event):
        source = str(event.payload["source"])
        if source != "chaoxing":
            return [event, _completed(source)]

        async def finish():
            await asyncio.sleep(0.01)
            await state_engine.apply(_completed("chaoxing", 4))

        asyncio.create_task(finish())
        return [event, Event(
            event_type=EventType.CONNECTOR_FETCH_STARTED,
            aggregate_id="chaoxing",
            aggregate_type=AggregateType.HOMEWORK,
            payload={"source": "chaoxing"},
        )]

    service = CloudSyncService(
        SimpleNamespace(run=run),
        state_engine,
        Settings(_env_file=None, cloud_sync_source_timeout_seconds=1),
    )

    result = await service.run(trigger="test")

    assert result["ok"] is True
    assert result["sources"]["chaoxing"]["homework_count"] == 4


def _route_client(service, token: str = "cloud-secret") -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.state.settings = SimpleNamespace(cloud_sync_token=token)
    app.state.cloud_sync_service = service
    app.state.event_store = SimpleNamespace()
    return TestClient(app)


def test_cloud_sync_endpoint_requires_configured_token():
    client = _route_client(AsyncMock(), token="")
    response = client.post("/api/internal/cloud-sync")
    assert response.status_code == 503
    assert response.json()["detail"] == "cloud_sync_not_configured"


def test_cloud_sync_endpoint_rejects_missing_and_wrong_token():
    service = SimpleNamespace(run=AsyncMock())
    client = _route_client(service)

    missing = client.post("/api/internal/cloud-sync")
    wrong = client.post(
        "/api/internal/cloud-sync",
        headers={"X-Cloud-Sync-Token": "wrong"},
    )

    assert missing.status_code == 401
    assert wrong.status_code == 401
    service.run.assert_not_awaited()


def test_cloud_sync_endpoint_runs_with_correct_token():
    service = SimpleNamespace(run=AsyncMock(return_value={
        "ok": True,
        "status": "completed",
        "sources": {},
        "events": 3,
    }))
    client = _route_client(service)

    response = client.post(
        "/api/internal/cloud-sync",
        headers={"X-Cloud-Sync-Token": "cloud-secret"},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    service.run.assert_awaited_once_with(
        trigger="remote_scheduler",
        sources=("google_calendar",),
    )
    assert "cloud-secret" not in response.text


def test_cloud_sync_endpoint_accepts_explicit_sources():
    service = SimpleNamespace(run=AsyncMock(return_value={
        "ok": True,
        "status": "completed",
        "sources": {},
        "events": 1,
    }))
    client = _route_client(service)

    response = client.post(
        "/api/internal/cloud-sync",
        headers={"X-Cloud-Sync-Token": "cloud-secret"},
        json={"sources": ["jwxt"]},
    )

    assert response.status_code == 200
    service.run.assert_awaited_once_with(
        trigger="remote_scheduler",
        sources=("jwxt",),
    )


def test_cloud_state_refresh_endpoint_uses_same_authentication():
    service = SimpleNamespace(
        refresh_from_event_store=AsyncMock(return_value={
            "ok": True,
            "status": "completed",
            "new_events": 4,
        }),
    )
    client = _route_client(service)

    response = client.post(
        "/api/internal/cloud-state-refresh",
        headers={"X-Cloud-Sync-Token": "cloud-secret"},
    )

    assert response.status_code == 200
    assert response.json()["new_events"] == 4
    service.refresh_from_event_store.assert_awaited_once()


def test_cloud_sync_endpoint_returns_409_when_busy():
    service = SimpleNamespace(
        run=AsyncMock(side_effect=CloudSyncInProgress("sync_already_running"))
    )
    client = _route_client(service)

    response = client.post(
        "/api/internal/cloud-sync",
        headers={"X-Cloud-Sync-Token": "cloud-secret"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "sync_already_running"
