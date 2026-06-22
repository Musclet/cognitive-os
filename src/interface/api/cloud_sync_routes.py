"""Protected internal endpoint used by the Render daily cron job."""

from __future__ import annotations

import hmac

from fastapi import APIRouter, HTTPException, Request

from src.services.cloud_sync import CloudSyncInProgress, CloudSyncService


router = APIRouter()


@router.post("/api/internal/cloud-sync")
async def cloud_sync(request: Request):
    settings = getattr(request.app.state, "settings", None)
    expected = str(getattr(settings, "cloud_sync_token", "") or "")
    if not expected:
        raise HTTPException(status_code=503, detail="cloud_sync_not_configured")

    provided = str(request.headers.get("x-cloud-sync-token", "") or "")
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="invalid_cloud_sync_token")

    service: CloudSyncService | None = getattr(
        request.app.state,
        "cloud_sync_service",
        None,
    )
    if service is None:
        raise HTTPException(status_code=503, detail="cloud_sync_service_unavailable")

    try:
        return await service.run(trigger="render_cron")
    except CloudSyncInProgress:
        raise HTTPException(status_code=409, detail="sync_already_running") from None
