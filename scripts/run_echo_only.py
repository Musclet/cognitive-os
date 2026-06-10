
import asyncio, logging, os, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from src.core.bus import EventBus
from src.core.pipeline import Pipeline
from src.core.state_engine import StateEngine
from src.core.events import Event, EventType, AggregateType
from src.core.watchdog import RuntimeWatchdog, setup_event_loop_monitoring
from src.interface.telegram.router import parse_message, command_to_event
from src.interface.telegram.templates import format_help

# Force-disable system proxy BEFORE telegram imports (httpx reads at import time)
import os as _os
_os.environ["HTTP_PROXY"] = ""
_os.environ["HTTPS_PROXY"] = ""
_os.environ["NO_PROXY"] = "*"

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logging.getLogger("telegram").setLevel(logging.DEBUG)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("echo_only")

_FORBIDDEN = [
    "src.infrastructure", "pydantic_settings",
    "src.storage", "aiosqlite", "sqlalchemy",
    "playwright", "src.connector",
    "src.domain", "src.derived_state",
    "uvicorn", "fastapi", "src.interface.api",
]

def _verify_isolation():
    violations = [m for m in _FORBIDDEN if m in sys.modules]
    if violations:
        logger.error("ISOLATION VIOLATED: %s", violations)
        sys.exit(1)
    logger.info("ISOLATION VERIFIED")


async def echo_handler(event: Event) -> list[Event]:
    cmd = event.payload.get("command", "?")
    uid = event.aggregate_id
    if cmd == "ping":
        return [Event(event_type=EventType.NOTIFICATION_SEND, aggregate_id=uid, aggregate_type=AggregateType.USER, causation_id=event.event_id, payload={"message": "pong", "details": []})]
    if cmd == "help":
        return [Event(event_type=EventType.NOTIFICATION_SEND, aggregate_id=uid, aggregate_type=AggregateType.USER, causation_id=event.event_id, payload={"message": format_help(), "details": []})]
    return [Event(event_type=EventType.NOTIFICATION_SEND, aggregate_id=uid, aggregate_type=AggregateType.USER, causation_id=event.event_id, payload={"message": f"echo: {cmd}", "details": ["isolation"]})]

class EchoBot:
    def __init__(self, token, bus, pipeline, state_engine):
        self._token = token
        self._bus = bus
        self._pipeline = pipeline
        self._state_engine = state_engine
        self._app = None

    async def start(self):
        logger.info("BOT TOKEN LOADED: %s...", self._token[:12])
        self._app = Application.builder().token(self._token).build()
        logger.info("App built, calling initialize...")
        await self._app.initialize()
        me = await self._app.bot.get_me()
        logger.info("BOT USERNAME: @%s id=%s", me.username, me.id)
        self._app.add_handler(CommandHandler([
            "ping","help","start","state","today","homework",
            "schedule","free_today","week_load","stress","capacity",
            "plan_today","plan_tomorrow","focus_window",
            "done","skip","delay","behavior","adaptive","patterns",
            "reflection","trends","adaptation","propose",
        ], self._handle))
        self._app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle))
        await self._app.start()
        await self._app.updater.start_polling()
        logger.info("POLLING STARTED")

    async def stop(self):
        if self._app:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
            logger.info("bot stopped")

    async def _handle(self, update, context):
        logger.info("[TG-IN] update_id=%s", update.update_id)
        if not update.message or not update.message.text:
            return
        user = update.effective_user
        if not user:
            return
        text = update.message.text.strip()
        logger.info("[TG-IN] text=%r user=%s chat=%s", text[:60], user.id, update.message.chat_id)
        cmd = parse_message(text, user.id)
        if cmd is None:
            await update.message.reply_text(format_help())
            return
        event = command_to_event(cmd)
        try:
            all_events = await self._pipeline.run(event)
            notifs = [e for e in all_events if e.event_type == EventType.NOTIFICATION_SEND]
            if notifs:
                for n in notifs:
                    msg = n.payload.get("message", "")
                    if msg:
                        logger.info("[TG-OUT] reply len=%d", len(msg))
                        await update.message.reply_text(msg)
            else:
                await update.message.reply_text("no output")
        except Exception as exc:
            logger.exception("[TG-IN] error")
            await update.message.reply_text(f"error: {exc}")



async def main():
    setup_event_loop_monitoring()
    # Force-disable system proxy for Telegram API (httpx reads Windows proxy settings)
    os.environ["HTTP_PROXY"] = ""
    os.environ["HTTPS_PROXY"] = ""
    os.environ["NO_PROXY"] = "*"
    logger.info("Proxy disabled for Telegram API")
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN not set")
        sys.exit(1)
    _verify_isolation()
    bus = EventBus(event_store=None)
    state_engine = StateEngine()
    pipeline = Pipeline(bus)
    bus.subscribe(EventType.USER_COMMAND_RECEIVED, echo_handler)
    bus.subscribe(EventType.NOTIFICATION_SEND, state_engine.apply)
    watchdog = RuntimeWatchdog(event_bus=bus, interval_seconds=5.0)
    await watchdog.start()
    bot = EchoBot(token, bus, pipeline, state_engine)
    logger.info("=" * 50)
    logger.info("ECHO-ONLY ISOLATION MODE")
    logger.info("=" * 50)
    await bot.start()
    logger.info("Entering keep-alive loop...")
    try:
        while True:
            await asyncio.sleep(1)
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("shutting down...")
    finally:
        await watchdog.stop()
        await bot.stop()


if __name__ == "__main__":
    asyncio.run(main())
