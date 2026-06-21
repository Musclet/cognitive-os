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
from src.core.events import AggregateType, Event, EventType
from src.interface.telegram.bot import CognitiveOSBot
from src.infrastructure.scheduler import CognitiveScheduler
from src.runtime.composition import build_runtime
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

    # ── Local dev defaults for Web UI login ─────────────────────────────
    # Only applied when WEB_UI_PIN is not already configured via env or .env.
    # render_run.py is unaffected — this code only runs in scripts/run.py.
    _pin = (settings.web_ui_pin or "").strip()
    if not _pin:
        settings.web_ui_pin = "123456"
        logger.info("Local Web UI dev PIN: 123456")
    _secret = (settings.web_ui_session_secret or "").strip()
    if not _secret:
        settings.web_ui_session_secret = "local-dev-session-secret"
    if "WEB_UI_COOKIE_SECURE" not in _os.environ:
        settings.web_ui_cookie_secure = False

    runtime = await build_runtime(settings, mode="local", web_ui_dist_path="web/dist")

    # ── Storage ────────────────────────────────────────────────────
    event_store = runtime.event_store

    # ── Core ───────────────────────────────────────────────────────
    bus = runtime.bus
    state_engine = runtime.state_engine

    # Active Course Registry
    course_registry = ActiveCourseRegistry()

    # Subjective Context Registry
    subjective_registry = SubjectiveContextRegistry()

    # Derived State Engine
    derived_engine = DerivedStateEngine(event_bus=bus, state_engine=state_engine)

    # Intervention Engine
    intervention_engine = InterventionEngine(event_bus=bus, state_engine=state_engine,
        cooldown_hours=6.0, daily_budget=3)
    pipeline = runtime.pipeline

    # ── Restore state from the event log ────────────────────────────
    # Watchdog
    watchdog = RuntimeWatchdog(event_bus=bus, interval_seconds=60.0)
    await watchdog.start()

    # ── Scheduler ──────────────────────────────────────────────────
    scheduler = CognitiveScheduler()
    scheduler.set_event_bus(bus)
    runtime.scheduler = scheduler
    if runtime.app is not None:
        runtime.app.state.scheduler = scheduler

    # ── Auto-polling: Chaoxing homework (every 12 hours) ──────────────
    scheduler.add_interval_job(
        "auto_sync_homework",
        settings.homework_sync_interval_hours * 60,
        {"action": "check_homework"},
    )

    # ── Optional extra JWXT polling. Daily sync times are registered below. ───────
    if settings.schedule_sync_interval_hours > 0:
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
    app = runtime.app
    if app is None:
        raise RuntimeError("local runtime did not create the API app")

    import uvicorn
    api_config = uvicorn.Config(app, host="0.0.0.0", port=8081, log_level="info")
    api_server = uvicorn.Server(api_config)

    # ── Telegram ───────────────────────────────────────────────────
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
        await watchdog.stop()
        await runtime.close()


if __name__ == "__main__":
    asyncio.run(main())
