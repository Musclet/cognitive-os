"""Render entry point — web server with embedded worker heartbeat."""
import asyncio, logging, sys, os, time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ["HTTP_PROXY"] = ""
os.environ["HTTPS_PROXY"] = ""
os.environ["NO_PROXY"] = "*"

from src.infrastructure.config import Settings
from src.core.pipeline import Pipeline
from src.core.events import Event, EventType, AggregateType
from src.runtime.composition import build_runtime

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("render")

HEARTBEAT_INTERVAL_S = 30


async def _heartbeat_loop(pipeline: Pipeline) -> None:
    """Background task — emit worker heartbeat every 30s so /api/web/status reports worker alive."""
    emit_count = 0
    started_at = time.monotonic()
    logger.info("embedded worker heartbeat started (interval=%ds)", HEARTBEAT_INTERVAL_S)
    while True:
        try:
            await asyncio.sleep(HEARTBEAT_INTERVAL_S)
        except asyncio.CancelledError:
            logger.info("embedded worker heartbeat cancelled")
            break
        emit_count += 1
        uptime_s = time.monotonic() - started_at
        try:
            event = Event(
                event_type=EventType.SYSTEM_RUNTIME_HEARTBEAT,
                aggregate_id="worker",
                aggregate_type=AggregateType.SYSTEM,
                timestamp=datetime.now(timezone.utc),
                payload={"emit_count": emit_count, "uptime_s": round(uptime_s, 1), "source": "worker"},
                metadata={"source": "worker"},
            )
            await pipeline.run(event)
        except Exception:
            logger.exception("heartbeat #%d failed", emit_count)

async def main():
    settings = Settings()
    runtime = await build_runtime(settings, mode="render")
    bus = runtime.bus
    pipeline = runtime.pipeline

    # Restore state
    # Wire Google Calendar connector for read-only sync
    from src.connector.google_calendar.client import GoogleCalendarConnector
    gcal = GoogleCalendarConnector(settings=settings)
    bus.subscribe(EventType.CONNECTOR_FETCH_REQUESTED, gcal.handle_fetch_request)
    logger.info("google calendar connector subscribed, mock=%s", settings.google_calendar_mock)

    # Wire JWXT connector for schedule sync
    from src.connector.jwxt.client import JwxtConnector
    jwxt = JwxtConnector(use_mock=settings.jwxt_mock, settings=settings)
    bus.subscribe(EventType.CONNECTOR_FETCH_REQUESTED, jwxt.handle_fetch_request)
    logger.info("jwxt connector subscribed, mock=%s", settings.jwxt_mock)

    # Build app
    app = runtime.app
    if app is None:
        raise RuntimeError("render runtime did not create the API app")

    # Start embedded worker heartbeat as a background task
    heartbeat_task = asyncio.create_task(_heartbeat_loop(pipeline))

    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
    server = uvicorn.Server(config)
    logger.info("web server starting on port %s (with embedded worker heartbeat)", port)
    try:
        await server.serve()
    finally:
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass
        await runtime.close()

if __name__ == "__main__":
    asyncio.run(main())
