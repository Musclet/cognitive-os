"""Protected internal endpoint used by the Render daily cron job."""

from __future__ import annotations

import hmac
import json

from fastapi import APIRouter, HTTPException, Request

from src.services.cloud_sync import CloudSyncInProgress, CloudSyncService


router = APIRouter()


def _authorize(request: Request) -> None:
    settings = getattr(request.app.state, "settings", None)
    expected = str(getattr(settings, "cloud_sync_token", "") or "")
    if not expected:
        raise HTTPException(status_code=503, detail="cloud_sync_not_configured")

    provided = str(request.headers.get("x-cloud-sync-token", "") or "")
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="invalid_cloud_sync_token")


@router.post("/api/internal/cloud-sync")
async def cloud_sync(request: Request):
    _authorize(request)
    service: CloudSyncService | None = getattr(
        request.app.state,
        "cloud_sync_service",
        None,
    )
    if service is None:
        raise HTTPException(status_code=503, detail="cloud_sync_service_unavailable")

    raw_body = await request.body()
    try:
        body = json.loads(raw_body) if raw_body else {}
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=422,
            detail="invalid_cloud_sync_sources",
        ) from None
    raw_sources = body.get("sources", ["google_calendar"])
    if not isinstance(raw_sources, list) or not all(
        isinstance(source, str) for source in raw_sources
    ):
        raise HTTPException(status_code=422, detail="invalid_cloud_sync_sources")

    try:
        return await service.run(
            trigger="remote_scheduler",
            sources=tuple(raw_sources),
        )
    except CloudSyncInProgress:
        raise HTTPException(status_code=409, detail="sync_already_running") from None
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="invalid_cloud_sync_sources",
        ) from None


@router.post("/api/internal/cloud-state-refresh")
async def cloud_state_refresh(request: Request):
    _authorize(request)
    service: CloudSyncService | None = getattr(
        request.app.state,
        "cloud_sync_service",
        None,
    )
    event_store = getattr(request.app.state, "event_store", None)
    if service is None or event_store is None:
        raise HTTPException(status_code=503, detail="cloud_sync_service_unavailable")

    try:
        return await service.refresh_from_event_store(event_store)
    except CloudSyncInProgress:
        raise HTTPException(status_code=409, detail="sync_already_running") from None
