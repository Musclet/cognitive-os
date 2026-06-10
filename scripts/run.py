"""Runtime launcher — starts all services: API + Telegram + Scheduler.

Usage: python scripts/run.py
"""

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


# Force-disable system proxy before telegram imports (httpx reads Windows proxy at import)
import os as _os
_os.environ["HTTP_PROXY"] = ""
_os.environ["HTTPS_PROXY"] = ""
_os.environ["NO_PROXY"] = "*"

from src.infrastructure.config import Settings
from src.storage.db import init_db, close_db
from src.storage.event_store import EventStore
from src.storage.snapshot_store import SnapshotStore
from src.core.bus import EventBus
from src.core.events import AggregateType, Event, EventType
from src.core.pipeline import Pipeline
from src.core.state_engine import StateEngine
from src.core.tracer import Tracer
from src.core.safety import DeadLetterQueue
from src.interface.api.app import create_app
from src.interface.telegram.bot import CognitiveOSBot
from src.infrastructure.scheduler import CognitiveScheduler
from derived_state import DerivedStateEngine
from intervention import InterventionEngine
from derived_state.active_courses import ActiveCourseRegistry
from derived_state.subjective_context import SubjectiveContextRegistry
from src.core.watchdog import RuntimeWatchdog, setup_event_loop_monitoring

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger("runtime")


async def main():
    setup_event_loop_monitoring()

    settings = Settings()
    settings.ensure_dirs()

    # ── Storage ────────────────────────────────────────────────────
    await init_db(settings.database_url)
    event_store = EventStore()
    snapshot_store = SnapshotStore()

    # ── Core ───────────────────────────────────────────────────────
    dead_letter = DeadLetterQueue()
    bus = EventBus(event_store=event_store)
    tracer = Tracer()
    state_engine = StateEngine(
        snapshot_path=settings.snapshot_path,
        snapshot_store=snapshot_store,
        snapshot_interval=50,
    )

    # Active Course Registry
    course_registry = ActiveCourseRegistry()

    # Subjective Context Registry
    subjective_registry = SubjectiveContextRegistry()

    # Derived State Engine
    derived_engine = DerivedStateEngine(event_bus=bus, state_engine=state_engine)

    # Intervention Engine
    intervention_engine = InterventionEngine(event_bus=bus, state_engine=state_engine,
        cooldown_hours=6.0, daily_budget=3)
    pipeline = Pipeline(bus, tracer=tracer)

    # ── Restore state from the event log ────────────────────────────
    try:
        events = await event_store.replay_all()
        if events:
            await state_engine.rebuild_from_events(events)
            logger.info("state restored from event log: %d events", len(events))
        else:
            state_engine.load_snapshot()
            logger.info("event log empty; loaded snapshot if available")
    except Exception:
        logger.exception("state restore from event log failed; falling back to snapshot")
        state_engine.load_snapshot()

    # Watchdog
    watchdog = RuntimeWatchdog(event_bus=bus, interval_seconds=60.0)
    await watchdog.start()

    # ── Scheduler ──────────────────────────────────────────────────
    scheduler = CognitiveScheduler()
    scheduler.set_event_bus(bus)

    # ── Auto-polling: Chaoxing homework (every 12 hours) ──────────────
    scheduler.add_interval_job(
        "auto_sync_homework",
        settings.homework_sync_interval_hours * 60,
        {"action": "check_homework"},
    )

    # ── Auto-polling: JWXT schedule (every 12 hours) ─────────────────
    scheduler.add_interval_job(
        "auto_sync_schedule",
        settings.schedule_sync_interval_hours * 60,
        {"action": "schedule_daily_sync"},
    )

    # ── Auto-polling: Google Calendar (every 30 minutes, configurable) ─
    scheduler.add_interval_job(
        "auto_sync_calendar",
        settings.google_calendar_poll_interval_minutes,
        {"action": "calendar_sync"},
    )

    # ── Auto-polling: Momo vocabulary (lightweight cache/API sync) ──────
    scheduler.add_interval_job(
        "auto_sync_vocab",
        settings.momo_sync_interval_minutes,
        {"action": "momo_vocab_sync"},
    )

    # ── Low-frequency cognitive check-in prompt ───────────────────────
    scheduler.add_interval_job(
        "cognitive_checkin",
        settings.cognitive_checkin_interval_minutes,
        {"action": "cognitive_checkin"},
    )

    # ── NL intent fallback habit summary (every 3 days = 4320 min) ─────
    scheduler.add_interval_job(
        "nl_intent_habit_summary",
        4320,
        {"action": "nl_intent_habit_summary"},
    )

    for sync_time in settings.schedule_daily_sync_times.split(","):
        sync_time = sync_time.strip()
        if not sync_time:
            continue
        hour_text, minute_text = sync_time.split(":", 1)
        scheduler.add_daily_job(
            f"schedule_daily_sync_{hour_text}_{minute_text}",
            int(hour_text),
            int(minute_text),
            {"action": "schedule_daily_sync"},
            timezone_str=settings.google_calendar_timezone,
        )

    if settings.nightly_review_enabled:
        hour_text, minute_text = settings.nightly_review_time.split(":", 1)
        scheduler.add_daily_job(
            f"nightly_review_{hour_text}_{minute_text}",
            int(hour_text),
            int(minute_text),
            {"action": "nightly_review"},
            timezone_str=settings.nightly_review_timezone,
        )
    scheduler.start()

    # ── FastAPI ────────────────────────────────────────────────────
    app = create_app(
        event_store=event_store,
        state_engine=state_engine,
        snapshot_store=snapshot_store,
        pipeline=pipeline,
        tracer=tracer,
        dead_letter=dead_letter,
        scheduler=scheduler,
        web_ui_dist_path="web/dist",
        settings=settings,
    )
    app.state.settings = settings  # expose for workout UI and inspector routes

    import uvicorn
    api_config = uvicorn.Config(app, host="0.0.0.0", port=8081, log_level="info")
    api_server = uvicorn.Server(api_config)

    # ── Telegram ───────────────────────────────────────────────────
    from src.interface.telegram.router import parse_message, command_to_event
    from src.interface.telegram.templates import format_output, format_error, format_help

    bot = CognitiveOSBot(
        settings,
        bus,
        pipeline,
        state_engine,
        course_registry=course_registry,
        derived_engine=derived_engine,
        intervention_engine=intervention_engine,
        subjective_registry=subjective_registry,
        event_store=event_store,
    )
    bot.wire_handlers()

    if settings.momo_sync_enabled and not settings.momo_sync_block_startup:
        async def _startup_momo_sync():
            try:
                await pipeline.run(Event(
                    event_type=EventType.CONNECTOR_FETCH_REQUESTED,
                    aggregate_id="startup_momo_vocab",
                    aggregate_type=AggregateType.VOCAB,
                    payload={"source": "momo_vocab", "query": "vocab_progress", "intent": "startup"},
                ))
            except Exception:
                logger.exception("startup Momo vocabulary sync failed")
        asyncio.create_task(_startup_momo_sync())
        logger.info("[MOMO] startup sync fired in background (non-blocking)")
    elif settings.momo_sync_enabled and settings.momo_sync_block_startup:
        try:
            await pipeline.run(Event(
                event_type=EventType.CONNECTOR_FETCH_REQUESTED,
                aggregate_id="startup_momo_vocab",
                aggregate_type=AggregateType.VOCAB,
                payload={"source": "momo_vocab", "query": "vocab_progress", "intent": "startup"},
            ))
        except Exception:
            logger.exception("startup Momo vocabulary sync failed")

    await derived_engine.derive()

    logger.info("starting services: API :8081, Telegram bot, Scheduler")

    # Run API + Telegram concurrently
    async def run_api():
        await api_server.serve()

    async def run_bot():
        if settings.telegram_bot_token:
            await bot.run_forever()
        else:
            logger.warning("TELEGRAM_BOT_TOKEN not set, bot not started")
            while True:
                await asyncio.sleep(3600)

    try:
        await asyncio.gather(run_api(), run_bot())
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("shutting down...")
    finally:
        scheduler.stop()
        state_engine.save_snapshot()
        await watchdog.stop()
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())
