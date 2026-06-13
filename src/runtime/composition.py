"""Shared composition root for local, Render Web, and worker processes."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from fastapi import FastAPI

from src.core.bus import EventBus
from src.core.events import EventType
from src.core.pipeline import Pipeline
from src.core.safety import DeadLetterQueue
from src.core.state_engine import StateEngine
from src.core.tracer import Tracer
from src.infrastructure.config import Settings
from src.infrastructure.scheduler import CognitiveScheduler
from src.interface.api.app import create_app
from src.storage.db import close_db, init_db
from src.storage.event_store import EventStore
from src.storage.snapshot_store import SnapshotStore

logger = logging.getLogger(__name__)

RuntimeMode = Literal["local", "render", "worker"]

MODE_CAPABILITIES: dict[RuntimeMode, frozenset[str]] = {
    "local": frozenset({"core", "api", "telegram", "scheduler", "watchdog"}),
    "render": frozenset({"core", "api", "google_calendar_read", "jwxt_read", "heartbeat"}),
    "worker": frozenset({"core", "heartbeat"}),
}


@dataclass
class Runtime:
    """Core objects shared by every production entry point."""

    mode: RuntimeMode
    settings: Settings
    event_store: EventStore
    snapshot_store: SnapshotStore
    bus: EventBus
    tracer: Tracer
    state_engine: StateEngine
    pipeline: Pipeline
    dead_letter: DeadLetterQueue
    scheduler: CognitiveScheduler | None = None
    app: FastAPI | None = None
    background_tasks: list[asyncio.Task] = field(default_factory=list)
    capabilities: frozenset[str] = field(default_factory=frozenset)

    async def close(self) -> None:
        """Cancel owned tasks, save state, and close shared storage."""
        for task in self.background_tasks:
            task.cancel()
        if self.background_tasks:
            await asyncio.gather(*self.background_tasks, return_exceptions=True)
        self.state_engine.save_snapshot()
        await close_db()


async def build_runtime(
    settings: Settings,
    mode: RuntimeMode,
    *,
    web_ui_dist_path: str | None = None,
) -> Runtime:
    """Build the shared core and restore StateEngine from durable storage."""
    if mode not in MODE_CAPABILITIES:
        raise ValueError(f"unsupported runtime mode: {mode}")

    settings.ensure_dirs()
    if mode in {"render", "worker"}:
        settings.apply_env_google_credentials()
    if mode == "render":
        settings.apply_env_jwxt_cookies()

    await init_db(settings.database_url)
    event_store = EventStore()
    snapshot_store = SnapshotStore()
    dead_letter = DeadLetterQueue()
    bus = EventBus(event_store=event_store, dead_letter=dead_letter)
    tracer = Tracer()
    state_engine = StateEngine(
        snapshot_path=settings.snapshot_path,
        snapshot_store=snapshot_store,
        snapshot_interval=100 if mode == "worker" else 50,
    )
    pipeline = Pipeline(bus, tracer=tracer)

    await _restore_state(state_engine, event_store)
    for event_type in EventType:
        bus.subscribe(event_type, state_engine.apply)

    app = None
    if mode != "worker":
        dist_path = web_ui_dist_path
        if dist_path is None:
            dist_path = str(Path(__file__).parents[2] / "web" / "dist")
        app = create_app(
            event_store=event_store,
            state_engine=state_engine,
            snapshot_store=snapshot_store,
            pipeline=pipeline,
            tracer=tracer,
            dead_letter=dead_letter,
            web_ui_dist_path=dist_path,
            settings=settings,
        )

    return Runtime(
        mode=mode,
        settings=settings,
        event_store=event_store,
        snapshot_store=snapshot_store,
        bus=bus,
        tracer=tracer,
        state_engine=state_engine,
        pipeline=pipeline,
        dead_letter=dead_letter,
        app=app,
        capabilities=MODE_CAPABILITIES[mode],
    )


async def _restore_state(state_engine: StateEngine, event_store: EventStore) -> None:
    try:
        events = await event_store.replay_all()
        if events:
            await state_engine.rebuild_from_events(events)
            logger.info("state restored from event log: %d events", len(events))
        else:
            state_engine.load_snapshot()
            logger.info("event log empty; loaded snapshot if available")
    except Exception:
        logger.exception("state restore failed; falling back to snapshot")
        state_engine.load_snapshot()
