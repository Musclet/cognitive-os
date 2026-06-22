"""Synchronize domestic sources locally, then refresh the live Render state."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from sqlalchemy.engine import make_url

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.connector.chaoxing.client import ChaoxingConnector
from src.connector.jwxt.client import JwxtConnector
from src.core.bus import EventBus
from src.core.events import EventType
from src.core.pipeline import Pipeline
from src.core.safety import DeadLetterQueue
from src.core.state_engine import StateEngine
from src.core.tracer import Tracer
from src.infrastructure.config import Settings
from src.services.cloud_sync import CloudSyncService
from src.storage.db import close_db, init_db
from src.storage.event_store import EventStore


DEFAULT_REFRESH_URL = (
    "https://cognitive-os.onrender.com/api/internal/cloud-state-refresh"
)


def normalize_database_url(raw_url: str) -> str:
    url = make_url(raw_url)
    if not url.drivername.startswith("postgresql"):
        raise ValueError("local_cloud_database_must_be_postgresql")
    query = dict(url.query)
    sslmode = query.pop("sslmode", None)
    query.pop("channel_binding", None)
    if sslmode and "ssl" not in query:
        query["ssl"] = sslmode
    return url.set(
        drivername="postgresql+asyncpg",
        query=query,
    ).render_as_string(hide_password=False)


def _refresh_cloud_state(url: str, token: str, timeout: int) -> dict:
    request = urllib.request.Request(
        url,
        data=b"{}",
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Cloud-Sync-Token": token,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return {
            "ok": False,
            "error_code": f"cloud_state_refresh_http_{exc.code}",
        }
    except (OSError, TimeoutError, json.JSONDecodeError) as exc:
        return {
            "ok": False,
            "error_code": "cloud_state_refresh_failed",
            "error_type": type(exc).__name__,
        }


async def run_local_cloud_sync() -> dict:
    database_url = os.environ.get("NEON_DATABASE_URL", "").strip()
    token = os.environ.get("CLOUD_SYNC_TOKEN", "").strip()
    refresh_url = os.environ.get(
        "CLOUD_STATE_REFRESH_URL",
        DEFAULT_REFRESH_URL,
    ).strip()
    refresh_timeout = max(
        30,
        int(os.environ.get("CLOUD_STATE_REFRESH_TIMEOUT_SECONDS", "180")),
    )
    if not database_url:
        return {"ok": False, "error_code": "neon_database_url_missing"}
    if not token:
        return {"ok": False, "error_code": "cloud_sync_token_missing"}

    settings = Settings(
        database_url=normalize_database_url(database_url),
        jwxt_mock=False,
        chaoxing_mock=False,
        cloud_sync_source_timeout_seconds=max(
            60,
            int(os.environ.get("LOCAL_CLOUD_SYNC_TIMEOUT_SECONDS", "900")),
        ),
    )
    await init_db(settings.database_url)
    event_store = EventStore()
    state_engine = StateEngine()
    dead_letter = DeadLetterQueue()
    bus = EventBus(event_store=event_store, dead_letter=dead_letter)
    pipeline = Pipeline(bus, tracer=Tracer())
    await state_engine.rebuild_from_events(await event_store.replay_all())
    for event_type in EventType:
        bus.subscribe(event_type, state_engine.apply)

    chaoxing = ChaoxingConnector(
        use_mock=False,
        state_file=settings.chaoxing_state_file,
        event_bus=bus,
        settings=settings,
    )
    try:
        jwxt = JwxtConnector(use_mock=False, settings=settings)
        bus.subscribe(
            EventType.CONNECTOR_FETCH_REQUESTED,
            jwxt.handle_fetch_request,
        )
        bus.subscribe(
            EventType.CONNECTOR_FETCH_REQUESTED,
            chaoxing.handle_fetch_request,
        )
        service = CloudSyncService(
            pipeline,
            state_engine,
            settings,
        )
        sync_result = await service.run(
            trigger="local_windows",
            sources=("jwxt", "chaoxing"),
        )
        refresh_result = await asyncio.to_thread(
            _refresh_cloud_state,
            refresh_url,
            token,
            refresh_timeout,
        )
        return {
            "ok": bool(sync_result["ok"] and refresh_result.get("ok")),
            "status": sync_result["status"],
            "sources": sync_result["sources"],
            "cloud_refresh": {
                "ok": bool(refresh_result.get("ok")),
                "new_events": int(refresh_result.get("new_events", 0) or 0),
                "error_code": str(refresh_result.get("error_code", "")),
            },
        }
    finally:
        await chaoxing.close()
        await close_db()


def _print_safe_report(result: dict) -> None:
    print(f"local_cloud_sync_status={result.get('status', 'failed')}")
    for source in ("jwxt", "chaoxing"):
        source_result = result.get("sources", {}).get(source, {})
        print(
            "source=%s status=%s count=%s error_code=%s"
            % (
                source,
                source_result.get("status", "failed"),
                source_result.get("count", 0),
                source_result.get("error_code", ""),
            )
        )
    refresh = result.get("cloud_refresh", {})
    print(
        "cloud_refresh=%s new_events=%s error_code=%s"
        % (
            "completed" if refresh.get("ok") else "failed",
            refresh.get("new_events", 0),
            refresh.get("error_code", result.get("error_code", "")),
        )
    )


def main() -> int:
    try:
        result = asyncio.run(run_local_cloud_sync())
    except ValueError as exc:
        result = {"ok": False, "error_code": str(exc)}
    _print_safe_report(result)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
