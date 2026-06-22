"""Render entry point — Web/API server for the cloud deployment."""
import asyncio, logging, sys, os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ["HTTP_PROXY"] = ""
os.environ["HTTPS_PROXY"] = ""
os.environ["NO_PROXY"] = "*"

from src.infrastructure.config import Settings
from src.core.events import Event, EventType, AggregateType
from src.runtime.composition import build_runtime

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("render")

async def main():
    settings = Settings()
    settings.normalize_data_paths()
    settings.ensure_dirs()
    logger.info("data dir: %s (exists=%s)", settings.data_dir, Path(settings.data_dir).is_dir())
    runtime = await build_runtime(settings, mode="render")
    bus = runtime.bus

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

    # Wire Chaoxing connector for homework sync
    from src.connector.chaoxing.client import ChaoxingConnector
    chaoxing = ChaoxingConnector(
        use_mock=settings.chaoxing_mock,
        state_file=settings.chaoxing_state_file,
        event_bus=bus,
        settings=settings,
    )
    bus.subscribe(EventType.CONNECTOR_FETCH_REQUESTED, chaoxing.handle_fetch_request)
    logger.info("chaoxing connector subscribed, mock=%s", settings.chaoxing_mock)

    # Wire scheduled-trigger → connector bridge (normally done by Telegram bot)
    async def _on_scheduled(event: Event) -> list[Event]:
        if event.event_type != EventType.SYSTEM_SCHEDULED_TRIGGER:
            return []
        action = str(event.payload.get("action", ""))
        if action == "check_homework":
            return [Event(
                event_type=EventType.CONNECTOR_FETCH_REQUESTED,
                aggregate_id=event.aggregate_id,
                aggregate_type=AggregateType.HOMEWORK,
                causation_id=event.event_id,
                payload={"source": "chaoxing", "query": "homework_list"},
            )]
        if action == "schedule_daily_sync":
            return [Event(
                event_type=EventType.CONNECTOR_FETCH_REQUESTED,
                aggregate_id=event.aggregate_id,
                aggregate_type=AggregateType.SYSTEM,
                causation_id=event.event_id,
                payload={"source": "jwxt", "query": "weekly_schedule", "intent": "schedule_daily_sync"},
            )]
        if action == "calendar_sync":
            return [Event(
                event_type=EventType.CONNECTOR_FETCH_REQUESTED,
                aggregate_id=event.aggregate_id,
                aggregate_type=AggregateType.SYSTEM,
                causation_id=event.event_id,
                payload={"source": "google_calendar", "query": "upcoming"},
            )]
        return []
    bus.subscribe(EventType.SYSTEM_SCHEDULED_TRIGGER, _on_scheduled)
    logger.info("scheduled-trigger bridge subscribed")

    # Build app
    app = runtime.app
    if app is None:
        raise RuntimeError("render runtime did not create the API app")

    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
    server = uvicorn.Server(config)
    logger.info("web server starting on port %s", port)
    try:
        await server.serve()
    finally:
        await runtime.close()

if __name__ == "__main__":
    asyncio.run(main())
