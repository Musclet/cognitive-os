"""Cognitive OS Worker — background heartbeat emitter.

Runs alongside the web service, sharing DATABASE_URL / EventStore.
Keeps the event log warm and proves the worker is alive via periodic
``system.runtime.heartbeat`` events.

Does NOT start: uvicorn, Telegram, scheduler, connectors (chaoxing /
jwxt / google_calendar / momo), or the web UI build.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ["HTTP_PROXY"] = ""
os.environ["HTTPS_PROXY"] = ""
os.environ["NO_PROXY"] = "*"

from src.infrastructure.config import Settings
from src.storage.db import init_db, close_db
from src.storage.event_store import EventStore
from src.storage.snapshot_store import SnapshotStore
from src.core.bus import EventBus
from src.core.pipeline import Pipeline
from src.core.state_engine import StateEngine
from src.core.tracer import Tracer
from src.core.events import Event, EventType, AggregateType

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger("worker")

HEARTBEAT_INTERVAL_S = 300  # 5 minutes


async def main() -> None:
    settings = Settings()
    settings.ensure_dirs()
    settings.apply_env_google_credentials()

    logger.info("worker starting — db=%s snapshot=%s",
                "postgres" if settings.database_url.startswith("postgresql") else "sqlite",
                settings.snapshot_path)

    # ── Storage ────────────────────────────────────────────────────────
    await init_db(settings.database_url)
    event_store = EventStore()
    snapshot_store = SnapshotStore()

    # ── Core ───────────────────────────────────────────────────────────
    bus = EventBus(event_store=event_store)
    tracer = Tracer()
    state_engine = StateEngine(
        snapshot_path=settings.snapshot_path,
        snapshot_store=snapshot_store,
        snapshot_interval=100,
    )
    pipeline = Pipeline(bus, tracer=tracer)

    # ── Replay — rebuild state from the shared event log ───────────────
    try:
        events = await event_store.replay_all()
        if events:
            await state_engine.rebuild_from_events(events)
            logger.info("state rebuilt from %d events", len(events))
        else:
            state_engine.load_snapshot()
            logger.info("event log empty; snapshot loaded if available")
    except Exception:
        logger.exception("state rebuild failed; falling back to snapshot")
        state_engine.load_snapshot()

    # ── Wire state engine to bus (real-time apply) ─────────────────────
    for event_type in EventType:
        bus.subscribe(event_type, state_engine.apply)
    logger.info("subscribed state engine to %d event types", len(EventType))

    # ── Heartbeat loop ─────────────────────────────────────────────────
    emit_count = 0
    started_at = time.monotonic()

    logger.info("worker alive — heartbeat every %ds", HEARTBEAT_INTERVAL_S)

    while True:
        try:
            await asyncio.sleep(HEARTBEAT_INTERVAL_S)
        except asyncio.CancelledError:
            logger.info("worker cancelled; shutting down")
            break

        emit_count += 1
        uptime_s = time.monotonic() - started_at

        try:
            event = Event(
                event_type=EventType.SYSTEM_RUNTIME_HEARTBEAT,
                aggregate_id="worker",
                aggregate_type=AggregateType.SYSTEM,
                timestamp=datetime.now(timezone.utc),
                payload={
                    "emit_count": emit_count,
                    "uptime_s": round(uptime_s, 1),
                    "source": "worker",
                },
                metadata={"source": "worker"},
            )
            produced = await pipeline.run(event)
            logger.info(
                "heartbeat #%d uptime=%.0fs state_events=%d cascade=%d",
                emit_count, uptime_s, state_engine.event_count, len(produced),
            )
        except Exception:
            logger.exception("heartbeat #%d failed — will retry in %ds",
                             emit_count, HEARTBEAT_INTERVAL_S)

    # ── Cleanup ────────────────────────────────────────────────────────
    state_engine.save_snapshot()
    await close_db()
    logger.info("worker shut down cleanly")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("worker stopped by signal")
