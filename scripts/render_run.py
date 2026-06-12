"""Render entry point — web server with embedded worker heartbeat."""
import asyncio, logging, sys, os, time
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
from src.core.safety import DeadLetterQueue
from src.core.events import Event, EventType, AggregateType
from src.interface.api.app import create_app

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
    settings.ensure_dirs()
    settings.apply_env_google_credentials()

    await init_db(settings.database_url)
    event_store = EventStore()
    snapshot_store = SnapshotStore()
    dead_letter = DeadLetterQueue()
    bus = EventBus(event_store=event_store)
    tracer = Tracer()
    state_engine = StateEngine(snapshot_path=settings.snapshot_path, snapshot_store=snapshot_store, snapshot_interval=50)
    pipeline = Pipeline(bus, tracer=tracer)

    # Restore state
    try:
        events = await event_store.replay_all()
        if events:
            await state_engine.rebuild_from_events(events)
            logger.info("state restored: %d events", len(events))
        else:
            state_engine.load_snapshot()
    except Exception:
        logger.exception("state restore failed, trying snapshot")
        state_engine.load_snapshot()

    # Wire state engine to the bus so every published event is applied in real time
    for event_type in EventType:
        bus.subscribe(event_type, state_engine.apply)
    logger.info("state engine subscribed to %d event types", len(EventType))

    # Wire Google Calendar connector for read-only sync
    from src.connector.google_calendar.client import GoogleCalendarConnector
    gcal = GoogleCalendarConnector(settings=settings)
    bus.subscribe(EventType.CONNECTOR_FETCH_REQUESTED, gcal.handle_fetch_request)
    logger.info("google calendar connector subscribed, mock=%s", settings.google_calendar_mock)

    # Build app
    app = create_app(
        event_store=event_store, state_engine=state_engine,
        snapshot_store=snapshot_store, pipeline=pipeline,
        tracer=tracer, dead_letter=dead_letter,
        web_ui_dist_path=str(Path(__file__).parent.parent / "web" / "dist"),
        settings=settings,
    )
    app.state.settings = settings

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

if __name__ == "__main__":
    asyncio.run(main())
