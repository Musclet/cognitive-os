"""Cognitive OS Worker — background heartbeat + sync guardian.

Runs alongside the web service, sharing DATABASE_URL / EventStore.

- Emits a ``system.runtime.heartbeat`` every 30 seconds.
- Every 5 minutes, runs a probe-gated Google Calendar real sync
  (via ``GoogleCalendarConnector.execute_real_readonly_sync``).
- All failures are isolated — a sync failure never kills the worker.

Does NOT start: uvicorn, Telegram, scheduler, or the web UI build.
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
from src.core.events import Event, EventType, AggregateType
from src.runtime.composition import build_runtime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger("worker")

HEARTBEAT_INTERVAL_S = 30  # heartbeat every 30s


async def main() -> None:
    settings = Settings()
    runtime = await build_runtime(settings, mode="worker")

    logger.info("worker starting — db=%s snapshot=%s",
                "postgres" if settings.database_url.startswith("postgresql") else "sqlite",
                settings.snapshot_path)

    # ── Storage ────────────────────────────────────────────────────────
    pipeline = runtime.pipeline

    # ── Core ───────────────────────────────────────────────────────────

    # ── Replay — rebuild state from the shared event log ───────────────
    # ── Wire state engine to bus (real-time apply) ─────────────────────
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

        # ── Heartbeat ──────────────────────────────────────────────────
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
            await pipeline.run(event)
            logger.debug("heartbeat #%d uptime=%.0fs", emit_count, uptime_s)
        except Exception:
            logger.exception("heartbeat #%d failed", emit_count)

    # ── Cleanup ────────────────────────────────────────────────────────
    await runtime.close()
    logger.info("worker shut down cleanly")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("worker stopped by signal")
