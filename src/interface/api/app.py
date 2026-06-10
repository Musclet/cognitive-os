"""FastAPI Inspector — read-only event, state, trace, safety, and scheduler inspection API."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import logging

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from src.storage.event_store import EventStore
from src.storage.snapshot_store import SnapshotStore
from src.core.state_engine import StateEngine
from src.core.pipeline import Pipeline
from src.core.tracer import Tracer
from src.core.safety import DeadLetterQueue
from src.interface.api.workout_routes import router as workout_router
from src.interface.api.web_routes import router as web_ui_router
from src.interface.api.mobile_routes import router as mobile_router


logger = logging.getLogger(__name__)

# ── Inspector API admin auth ────────────────────────────────────────────────


def _require_inspector_admin(request: Request) -> None:
    """Require admin token for Inspector API access.

    Checks Authorization: Bearer <token> or X-Admin-Token header.
    If INSPECTOR_ADMIN_TOKEN is empty (not configured), return 403.
    """
    settings = getattr(request.app.state, "settings", None)
    token = getattr(settings, "inspector_admin_token", "") if settings else ""
    if not token:
        raise HTTPException(status_code=403, detail="inspector_api_disabled")

    # Try Authorization: Bearer <token>
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        provided = auth_header[7:]
        if provided == token:
            return

    # Try X-Admin-Token header
    x_admin = request.headers.get("X-Admin-Token", "")
    if x_admin == token:
        return

    raise HTTPException(status_code=403, detail="invalid_admin_token")


def create_app(
    event_store: EventStore,
    state_engine: StateEngine | None = None,
    snapshot_store: SnapshotStore | None = None,
    pipeline: Pipeline | None = None,
    tracer: Tracer | None = None,
    dead_letter: DeadLetterQueue | None = None,
    scheduler=None,
    web_ui_dist_path: str | None = None,
    settings=None,
) -> FastAPI:
    app = FastAPI(title="Cognitive OS Inspector", version="0.1.0")

    # CORS: read allowed origins from settings, default to local dev
    allowed_origins = ["http://localhost:5173", "http://localhost:8081"]
    if settings is not None:
        origins_str = getattr(settings, "allowed_origins", "")
        if origins_str:
            allowed_origins = [o.strip() for o in origins_str.split(",") if o.strip()]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["*"],
    )

    # ── Root redirect to Web UI ────────────────────────────────────────
    @app.get("/", include_in_schema=False)
    async def root_redirect():
        return RedirectResponse("/app")

    # ── Inspector API admin auth middleware ────────────────────────────
    @app.middleware("http")
    async def _inspector_auth_middleware(request: Request, call_next):
        """Guard Inspector API routes with admin token.

        Protects: /events, /state, /snapshots, /dead-letter, /traces,
        /scheduler, /stats, /aggregates, /dashboard (the legacy one).
        Does NOT protect: /api/web/auth/*, /api/web/*, /app/*, /api/workout/*.
        OPTIONS preflight requests are passed through for CORS.
        """
        # Allow CORS preflight through without auth
        if request.method == "OPTIONS":
            return await call_next(request)

        _INSPECTOR_PREFIXES = (
            "/events",
            "/state",
            "/snapshots",
            "/dead-letter",
            "/traces",
            "/scheduler/jobs",
            "/stats",
            "/aggregates",
            "/dashboard",
        )
        path = request.url.path
        if not any(path.startswith(p) for p in _INSPECTOR_PREFIXES):
            return await call_next(request)

        try:
            _require_inspector_admin(request)
        except HTTPException as exc:
            return JSONResponse(
                status_code=exc.status_code,
                content={"detail": exc.detail},
            )
        return await call_next(request)

    app.state.event_store = event_store
    app.state.state_engine = state_engine
    app.state.pipeline = pipeline
    app.state.snapshot_store = snapshot_store
    app.state.tracer = tracer
    app.state.dead_letter = dead_letter
    app.state.scheduler = scheduler

    # ── Workout UI routes ────────────────────────────────────────────
    app.include_router(workout_router)

    # ── Web UI routes ────────────────────────────────────────────────
    app.include_router(web_ui_router)

    # ── Mobile API routes ─────────────────────────────────────────────
    app.include_router(mobile_router)

    # ── Event endpoints ──────────────────────────────────────────────

    @app.get("/events/recent")
    async def get_recent_events(n: int = Query(default=20, le=200)):
        store: EventStore = app.state.event_store
        events = await store.get_recent(n)
        return {"count": len(events), "events": [_event_to_dict(e) for e in events]}

    @app.get("/events/{event_id}")
    async def get_event(event_id: str):
        store: EventStore = app.state.event_store
        event = await store.get_by_event_id(event_id)
        if event is None:
            raise HTTPException(status_code=404, detail="Event not found")
        return _event_to_dict(event)

    @app.get("/events")
    async def get_events_by_type(
        type: str = Query(default=None),
        aggregate_id: str = Query(default=None),
        limit: int = Query(default=50, le=500),
    ):
        store: EventStore = app.state.event_store
        if aggregate_id:
            events = await store.get_by_aggregate(aggregate_id)
        elif type:
            events = await store.get_by_type(type)
        else:
            events = await store.get_recent(limit)
        return {"count": len(events), "events": [_event_to_dict(e) for e in events[:limit]]}

    @app.get("/aggregates/{aggregate_id}/events")
    async def get_aggregate_events(aggregate_id: str):
        store: EventStore = app.state.event_store
        events = await store.get_by_aggregate(aggregate_id)
        return {"aggregate_id": aggregate_id, "count": len(events), "events": [_event_to_dict(e) for e in events]}

    @app.get("/trace/{causation_id}")
    async def get_causation_chain(causation_id: str):
        store: EventStore = app.state.event_store
        chain = []
        current_id = causation_id
        seen = set()
        while current_id and current_id not in seen:
            seen.add(current_id)
            event = await store.get_by_event_id(current_id)
            if event is None:
                break
            chain.append(_event_to_dict(event))
            current_id = str(event.causation_id) if event.causation_id else None
        return {"root_event_id": causation_id, "depth": len(chain), "chain": chain}

    # ── Trace endpoints ──────────────────────────────────────────────

    @app.get("/traces/recent")
    async def get_recent_traces(n: int = Query(default=50, le=500)):
        t: Tracer | None = app.state.tracer
        if t is None:
            raise HTTPException(status_code=503, detail="Tracer not available")
        return {"count": t.trace_count(), "traces": t.get_recent(n)}

    @app.get("/traces/{trace_id}")
    async def get_trace(trace_id: str):
        t: Tracer | None = app.state.tracer
        if t is None:
            raise HTTPException(status_code=503, detail="Tracer not available")
        trace = t.get_trace(trace_id)
        if trace is None:
            raise HTTPException(status_code=404, detail="Trace not found")
        return {"trace_id": trace_id, "steps": len(trace), "entries": trace}

    @app.get("/traces")
    async def list_trace_ids():
        t: Tracer | None = app.state.tracer
        if t is None:
            raise HTTPException(status_code=503, detail="Tracer not available")
        return {"trace_ids": t.trace_ids()}

    # ── State endpoints ──────────────────────────────────────────────

    @app.get("/state")
    async def get_state():
        engine: StateEngine | None = app.state.state_engine
        if engine is None:
            raise HTTPException(status_code=503, detail="State engine not available")
        return {
            "event_count": engine.event_count,
            "state_hash": engine.state_hash(),
            "derived": engine.get_all_derived(),
        }

    @app.get("/state/derived")
    async def get_derived_state():
        engine: StateEngine | None = app.state.state_engine
        if engine is None:
            raise HTTPException(status_code=503, detail="State engine not available")
        return engine.get_all_derived()

    @app.get("/state/aggregate/{aggregate_type}/{aggregate_id}")
    async def get_aggregate_state(aggregate_type: str, aggregate_id: str):
        engine: StateEngine | None = app.state.state_engine
        if engine is None:
            raise HTTPException(status_code=503, detail="State engine not available")
        view = engine.get_view(aggregate_type, aggregate_id)
        if not view:
            raise HTTPException(status_code=404, detail="Aggregate not found")
        return {"aggregate_type": aggregate_type, "aggregate_id": aggregate_id, "state": view}

    # ── Snapshot endpoints ───────────────────────────────────────────

    @app.get("/snapshots")
    async def get_snapshots():
        snap_store: SnapshotStore | None = app.state.snapshot_store
        if snap_store is None:
            raise HTTPException(status_code=503, detail="Snapshot store not available")
        snaps = await snap_store.get_all()
        latest = await snap_store.get_latest()
        return {"snapshots": snaps, "latest_sequence": latest[1] if latest else None}

    # ── Dead-letter ──────────────────────────────────────────────────

    @app.get("/dead-letter")
    async def get_dead_letter():
        dlq: DeadLetterQueue | None = app.state.dead_letter
        if dlq is None:
            raise HTTPException(status_code=503, detail="Dead-letter queue not available")
        return {"count": dlq.count(), "entries": dlq.get_all()}

    @app.delete("/dead-letter")
    async def clear_dead_letter():
        dlq: DeadLetterQueue | None = app.state.dead_letter
        if dlq is None:
            raise HTTPException(status_code=503, detail="Dead-letter queue not available")
        dlq.clear()
        return {"status": "cleared"}

    # ── Scheduler ────────────────────────────────────────────────────

    @app.get("/scheduler/jobs")
    async def get_scheduler_jobs():
        sched = app.state.scheduler
        if sched is None:
            raise HTTPException(status_code=503, detail="Scheduler not available")
        return {"jobs": sched.jobs if hasattr(sched, 'jobs') else []}

    # ── Stats ────────────────────────────────────────────────────────

    @app.get("/stats")
    async def get_stats():
        store: EventStore = app.state.event_store
        engine: StateEngine | None = app.state.state_engine
        t: Tracer | None = app.state.tracer
        dlq: DeadLetterQueue | None = app.state.dead_letter
        total = await store.count()
        last_seq = await store.last_sequence()
        return {
            "total_events": total,
            "last_sequence": last_seq,
            "applied_events": engine.event_count if engine else 0,
            "traces_recorded": t.trace_count() if t else 0,
            "dead_letter_count": dlq.count() if dlq else 0,
            "server_time_utc": datetime.now(timezone.utc).isoformat(),
        }

    # ── Dashboard ────────────────────────────────────────────────────

    @app.get("/dashboard")
    async def dashboard():
        from src.interface.dashboard.template import DASHBOARD_HTML
        from fastapi.responses import HTMLResponse
        return HTMLResponse(content=DASHBOARD_HTML)

    # ── Web UI SPA serving ───────────────────────────────────────────
    if web_ui_dist_path:
        mount_web_ui(app, web_ui_dist_path)

    return app


# ── SPA static file serving for Web UI ─────────────────────────────────────


def mount_web_ui(app: FastAPI, dist_path: str = "web/dist") -> None:
    """Mount the React/Vite SPA at /app with fallback routing.

    Serves PWA manifest, service worker, icons, and static assets from the
    Vite build output.  SPA routes that don't match a physical file fall
    back to index.html for client-side routing.
    """
    import os
    from fastapi.responses import FileResponse

    if not os.path.isdir(dist_path):
        logger.warning("Web UI dist not found at %s; /app routes will 404", dist_path)
        return

    # Serve static assets (JS, CSS, images) from the Vite output
    assets_dir = os.path.join(dist_path, "assets")
    if os.path.isdir(assets_dir):
        app.mount("/app/assets", StaticFiles(directory=assets_dir), name="web_ui_assets")

    index_path = os.path.join(dist_path, "index.html")
    if not os.path.exists(index_path):
        logger.warning("Web UI index.html not found at %s", index_path)
        return

    # ── PWA / root-level static files ──────────────────────────────────
    # These must be served with correct Content-Type before the SPA
    # catch-all, otherwise the browser can't parse manifest.json or
    # register the service worker.

    PWA_FILES: list[tuple[str, str]] = [
        ("manifest.json", "application/manifest+json"),
        ("sw.js", "application/javascript; charset=utf-8"),
        ("icon-192.svg", "image/svg+xml"),
        ("icon-512.svg", "image/svg+xml"),
    ]

    for _filename, _media_type in PWA_FILES:
        _file_path = os.path.join(dist_path, _filename)
        if os.path.exists(_file_path):

            def _make_pwa_route(file_path: str, media_type: str, route_name: str):
                @app.get(f"/app/{route_name}", include_in_schema=False)
                async def _serve():
                    return FileResponse(
                        file_path,
                        media_type=media_type,
                        headers={"Cache-Control": "public, max-age=86400"},
                    )
                return _serve

            _make_pwa_route(_file_path, _media_type, _filename)

    # ── SPA fallback ───────────────────────────────────────────────────
    index_html = open(index_path, encoding="utf-8").read()

    @app.get("/app/{full_path:path}", response_class=HTMLResponse, include_in_schema=False)
    async def serve_web_ui(full_path: str):
        return HTMLResponse(content=index_html)

    @app.get("/app", response_class=HTMLResponse, include_in_schema=False)
    async def serve_web_ui_root():
        return HTMLResponse(content=index_html)

    logger.info("Web UI SPA + PWA mounted at /app from %s", dist_path)


def _event_to_dict(event) -> dict[str, Any]:
    return {
        "event_id": str(event.event_id),
        "event_type": event.event_type.value,
        "aggregate_id": event.aggregate_id,
        "aggregate_type": event.aggregate_type.value,
        "timestamp": event.timestamp.isoformat(),
        "causation_id": str(event.causation_id) if event.causation_id else None,
        "payload": event.payload,
        "metadata": event.metadata,
    }
