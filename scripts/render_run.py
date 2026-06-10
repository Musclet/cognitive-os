"""Render entry point — web server only, no Telegram bot."""
import asyncio, logging, sys, os
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
from src.core.events import Event, EventType
from src.interface.api.app import create_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("render")

async def main():
    settings = Settings()
    settings.ensure_dirs()

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

    # Build app
    app = create_app(
        event_store=event_store, state_engine=state_engine,
        snapshot_store=snapshot_store, pipeline=pipeline,
        tracer=tracer, dead_letter=dead_letter,
        web_ui_dist_path=str(Path(__file__).parent.parent / "web" / "dist"),
        settings=settings,
    )
    app.state.settings = settings

    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
    server = uvicorn.Server(config)
    logger.info("Starting web server on port %s", port)
    await server.serve()

if __name__ == "__main__":
    asyncio.run(main())
