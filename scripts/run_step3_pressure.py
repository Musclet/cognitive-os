
import asyncio, logging, os, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from src.core.bus import EventBus
from src.core.pipeline import Pipeline
from src.core.state_engine import StateEngine
from src.core.events import Event, EventType, AggregateType
from src.storage.db import init_db, close_db
from src.storage.event_store import EventStore
from src.infrastructure.scheduler import CognitiveScheduler
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
    "pydantic_settings",
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


# ── FakeConnector: simulates slow/timeout/error/cancel ──────────────────

class FakeConnector:
    """Simulates connector failure modes without real I/O.
    
    Commands:
      /normal  → fast success (10ms)
      /slow    → delayed success (5s)
      /timeout → hangs forever (45s, exceeds SafeHandler timeout)
      /error   → raises exception
      /crash   → raises SystemExit-like error
      /malform → returns malformed payload
      /chain   → returns events that trigger further processing
    """

    async def handle(self, event):
        cmd = event.payload.get("command", "")
        uid = event.aggregate_id
        
        if cmd == "normal":
            await asyncio.sleep(0.01)
            return [Event(event_type=EventType.NOTIFICATION_SEND, aggregate_id=uid,
                aggregate_type=AggregateType.USER, causation_id=event.event_id,
                payload={"message": "normal: OK (10ms)", "details": []})]
        
        if cmd == "slow":
            await asyncio.sleep(5.0)
            return [Event(event_type=EventType.NOTIFICATION_SEND, aggregate_id=uid,
                aggregate_type=AggregateType.USER, causation_id=event.event_id,
                payload={"message": "slow: OK (5s delay)", "details": []})]
        
        if cmd == "timeout":
            await asyncio.sleep(45.0)  # exceeds SafeHandler 30s timeout
            return [Event(event_type=EventType.NOTIFICATION_SEND, aggregate_id=uid,
                aggregate_type=AggregateType.USER, causation_id=event.event_id,
                payload={"message": "timeout: should never arrive", "details": []})]
        
        if cmd == "error":
            raise RuntimeError("FakeConnector simulated error")
        
        if cmd == "crash":
            raise SystemExit("FakeConnector simulated crash")
        
        if cmd == "malform":
            # Return something that is NOT a list — should be caught
            return {"bad": "payload"}
        
        if cmd == "chain":
            # Emit a connector.fetch.requested to trigger pipeline chaining
            return [Event(event_type=EventType.CONNECTOR_FETCH_REQUESTED,
                aggregate_id=uid, aggregate_type=AggregateType.HOMEWORK,
                causation_id=event.event_id,
                payload={"source": "fake", "query": "pressure_test"})]
        
        return []

_fake = FakeConnector()


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
            "reflection","trends","adaptation","propose","normal","slow","timeout","error","crash","malform","chain",
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
    db_dir = Path("data")
    db_dir.mkdir(exist_ok=True)
    await init_db("sqlite+aiosqlite:///" + str(db_dir / "step2.db"))
    event_store = EventStore()
    bus = EventBus(event_store=event_store)
    state_engine = StateEngine()
    pipeline = Pipeline(bus)
    bus.subscribe(EventType.USER_COMMAND_RECEIVED, echo_handler)
    bus.subscribe(EventType.USER_COMMAND_RECEIVED, _fake.handle)  # pressure test: 2nd subscriber
    bus.subscribe(EventType.CONNECTOR_FETCH_REQUESTED, _fake.handle)  # chain handler
    bus.subscribe(EventType.NOTIFICATION_SEND, state_engine.apply)
    watchdog = RuntimeWatchdog(event_bus=bus, interval_seconds=5.0)
    await watchdog.start()

    # Scheduler: emit-only, no domain handlers attached
    scheduler = CognitiveScheduler()
    scheduler.set_event_bus(bus)
    scheduler.add_interval_job("step2_tick", 1, {"action": "step2_ping"})
    scheduler.start()
    logger.info("SCHEDULER: started, ticking every 30s")
    bot = EchoBot(token, bus, pipeline, state_engine)
    logger.info("=" * 50)
    logger.info("STEP 3: Echo + SQLite + Scheduler + FakeConnector Pressure")
    logger.info("=" * 50)
    await bot.start()
    logger.info("Entering keep-alive loop...")
    try:
        while True:
            await asyncio.sleep(1)
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("shutting down...")
    finally:
        scheduler.stop()
        await watchdog.stop()
        await bot.stop()
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())
