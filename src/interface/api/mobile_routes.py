"""Mobile API routes — token-based auth for native iOS app.

All /api/mobile/* endpoints require Authorization: Bearer <token>.
Token is HMAC-signed, issued by /api/mobile/auth/login after PIN verification.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
from base64 import b64decode, b64encode
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from src.core.state_engine import StateEngine
from src.domain.dashboard.query import build_dashboard
from src.interface.api.schemas.dashboard import DashboardResponse
from src.core.pipeline import Pipeline

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/mobile")

LOCAL_TZ = timezone(timedelta(hours=8))  # Asia/Singapore

# ── Token helpers ────────────────────────────────────────────────────────────


def _mobile_secret(request: Request) -> str:
    """Return the mobile API signing secret from settings."""
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        raise HTTPException(status_code=503, detail="settings_not_configured")

    # Prefer mobile_api_secret, fallback to web_ui_session_secret
    secret = getattr(settings, "mobile_api_secret", "") or ""
    if not secret:
        secret = getattr(settings, "web_ui_session_secret", "") or ""
    if not secret:
        # Generate a process-local fallback (same as web session secret pattern)
        if not hasattr(request.app.state, "_mobile_fallback_secret"):
            request.app.state._mobile_fallback_secret = (
                "mobile-auto-" + hashlib.sha256(secrets.token_bytes(32)).hexdigest()[:32]
            )
        secret = request.app.state._mobile_fallback_secret
    return secret


def _make_mobile_token(secret: str, days: int = 30) -> str:
    """Create a signed mobile access token: base64(payload).signature"""
    expires = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
    payload = b64encode(json.dumps({"expires": expires, "type": "mobile"}).encode()).decode()
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()[:16]
    return f"{payload}.{sig}"


def _validate_mobile_token(token: str, secret: str) -> bool:
    """Validate a signed mobile access token."""
    if "." not in token:
        return False
    payload, sig = token.rsplit(".", 1)
    expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()[:16]
    if not hmac.compare_digest(sig, expected):
        return False
    try:
        data = json.loads(b64decode(payload).decode())
        if data.get("type") != "mobile":
            return False
        exp = datetime.fromisoformat(data["expires"])
        if exp < datetime.now(timezone.utc):
            return False
    except (json.JSONDecodeError, KeyError, ValueError):
        return False
    return True


def _require_mobile_auth(request: Request) -> None:
    """Raise 401 if Authorization: Bearer <token> is missing or invalid."""
    secret = _mobile_secret(request)
    auth = request.headers.get("Authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="mobile_token_required")
    token = auth[7:].strip()
    if not token or not _validate_mobile_token(token, secret):
        raise HTTPException(status_code=401, detail="invalid_mobile_token")


def _settings(request: Request):
    s = getattr(request.app.state, "settings", None)
    if s is None:
        raise HTTPException(status_code=503, detail="settings_not_configured")
    return s


# ── Auth endpoint ────────────────────────────────────────────────────────────


@router.post("/auth/login")
async def mobile_login(request: Request, body: dict):
    """Validate PIN and return a mobile access token.

    Request: { "pin": "string" }
    Response: { "access_token": "...", "expires_at": "...", "token_type": "bearer" }
    """
    settings = _settings(request)
    expected = str(getattr(settings, "web_ui_pin", ""))
    provided = str(body.get("pin", ""))

    if not expected:
        raise HTTPException(status_code=503, detail="web_ui_pin_not_configured")

    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="invalid_pin")

    secret = _mobile_secret(request)
    days = int(getattr(settings, "mobile_token_days", 30))
    token = _make_mobile_token(secret, days)
    expires_at = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()

    return {
        "access_token": token,
        "expires_at": expires_at,
        "token_type": "bearer",
    }


# ── Dashboard ────────────────────────────────────────────────────────────────


@router.get("/dashboard", response_model=DashboardResponse)
async def mobile_dashboard(request: Request):
    """Return the aggregated dashboard JSON (same as web UI)."""
    _require_mobile_auth(request)

    engine: StateEngine | None = getattr(request.app.state, "state_engine", None)
    settings = _settings(request)

    return build_dashboard(engine, settings)


# ── Workout ──────────────────────────────────────────────────────────────────


@router.get("/workout/session")
async def mobile_workout_session(request: Request, date: str | None = None):
    """Return today's workout session data."""
    _require_mobile_auth(request)
    settings = _settings(request)
    vault = getattr(settings, "obsidian_vault_path", "")
    if not vault:
        raise HTTPException(status_code=503, detail="obsidian_vault_not_configured")

    from zoneinfo import ZoneInfo
    from src.domain.fitness.ui_service import read_session
    d = datetime.now(ZoneInfo("Asia/Singapore")).date()
    if date:
        from datetime import date as dt_date
        d = dt_date.fromisoformat(date[:10])

    session = read_session(vault, d)
    if not session:
        return {"date": d.isoformat(), "session": None}

    return {"date": d.isoformat(), "session": session}


@router.post("/workout/session/select")
async def mobile_workout_select(request: Request, body: dict):
    """Select or create a workout session for a given day."""
    _require_mobile_auth(request)
    settings = _settings(request)
    vault = getattr(settings, "obsidian_vault_path", "")
    if not vault:
        raise HTTPException(status_code=503, detail="obsidian_vault_not_configured")

    date_str = str(body.get("date", ""))
    day_name = str(body.get("day_name", ""))
    force = bool(body.get("force", False))

    if not date_str or not day_name:
        return JSONResponse(status_code=400, content={"ok": False, "message": "date and day_name required"})

    from datetime import date as dt_date
    from src.domain.fitness.ui_service import select_or_create_session
    d = dt_date.fromisoformat(date_str[:10])
    session = select_or_create_session(vault, d, day_name, force=force)
    return {"date": d.isoformat(), "session": session}


@router.post("/workout/set/update")
async def mobile_workout_set_update(request: Request, body: dict):
    """Update a workout set: checked, weight, reps, rir."""
    _require_mobile_auth(request)
    settings = _settings(request)
    vault = getattr(settings, "obsidian_vault_path", "")
    if not vault:
        raise HTTPException(status_code=503, detail="obsidian_vault_not_configured")

    date_str = str(body.get("date", ""))
    exercise_index = int(body.get("exercise_index", 0))
    set_number = int(body.get("set_number", 0))

    if not date_str:
        return JSONResponse(status_code=400, content={"ok": False, "message": "date required"})

    from datetime import date as dt_date
    from src.domain.fitness.ui_service import update_set as svc_update_set
    d = dt_date.fromisoformat(date_str[:10])

    # Collect fields to update
    for field in ("checked", "weight", "reps", "rir"):
        val = body.get(field)
        if val is not None:
            svc_update_set(vault, d, exercise_index, set_number, field, val)

    from src.domain.fitness.ui_service import read_session
    session = read_session(vault, d)
    return {"date": d.isoformat(), "session": session}


# ── Finance ──────────────────────────────────────────────────────────────────


@router.post("/finance/action")
async def mobile_finance_action(request: Request, body: dict):
    """Execute a finance action (expense/income) through the pipeline.

    Request:
      { "action": "expense", "amount": 18, "category": "food", "description": "lunch" }
      { "action": "income",  "amount": 1000, "source": "allowance", "description": "monthly" }
    """
    _require_mobile_auth(request)

    action = str(body.get("action", "")).strip()
    if action not in {"expense", "income"}:
        return JSONResponse(status_code=400, content={"ok": False, "message": "action must be expense or income"})

    pipeline: Pipeline | None = getattr(request.app.state, "pipeline", None)
    if pipeline is None:
        return JSONResponse(status_code=503, content={"ok": False, "message": "pipeline not available"})

    from src.core.events import Event, EventType, AggregateType
    from src.interface.api.web_routes import _build_dashboard

    user_id = "0"
    users = getattr(_settings(request), "telegram_allowed_users", [])
    if users:
        user_id = str(users[0])

    trace_id = str(uuid4())
    metadata = {"source": "mobile_ios", "user_id": user_id, "trace_id": trace_id}

    try:
        if action == "expense":
            amount = float(body.get("amount", 0))
            category = str(body.get("category", "other")).strip() or "other"
            description = str(body.get("description", "")).strip()
            if amount <= 0:
                return JSONResponse(status_code=400, content={"ok": False, "message": "amount must be > 0"})
            event = Event(
                event_type=EventType.FINANCE_TRANSACTION_RECORDED,
                aggregate_id=user_id,
                aggregate_type=AggregateType.FINANCE,
                payload={"amount": amount, "category": category, "description": description or f"spend {amount}", "user_id": user_id},
                metadata=metadata,
            )
            await pipeline.run(event)

        elif action == "income":
            amount = float(body.get("amount", 0))
            source = str(body.get("source", "other")).strip() or "other"
            description = str(body.get("description", "")).strip()
            if amount <= 0:
                return JSONResponse(status_code=400, content={"ok": False, "message": "amount must be > 0"})
            event = Event(
                event_type=EventType.FINANCE_INCOME_RECORDED,
                aggregate_id=user_id,
                aggregate_type=AggregateType.FINANCE,
                payload={"amount": amount, "source": source, "description": description or f"income {amount}", "user_id": user_id},
                metadata=metadata,
            )
            await pipeline.run(event)

    except Exception as exc:
        logger.exception("mobile finance action failed")
        return {"ok": False, "message": f"error: {str(exc)[:80]}"}

    engine: StateEngine | None = getattr(request.app.state, "state_engine", None)
    dashboard = _build_dashboard(engine, _settings(request)) if engine else None
    return {"ok": True, "message": f"Recorded {action}", "dashboard": dashboard}


# ── Health check ─────────────────────────────────────────────────────────────


@router.get("/health")
async def mobile_health(request: Request):
    """Return server health information."""
    _require_mobile_auth(request)

    engine: StateEngine | None = getattr(request.app.state, "state_engine", None)
    event_store = getattr(request.app.state, "event_store", None)

    from datetime import datetime as dt
    import sys

    db_status = "unknown"
    event_count = 0
    if event_store:
        try:
            event_count = await event_store.count()
            db_status = "ok"
        except Exception:
            db_status = "error"

    return {
        "server_time": dt.now(timezone.utc).isoformat(),
        "app_version": "0.2.0",
        "database_status": db_status,
        "event_count": event_count,
        "python": sys.version.split()[0],
    }
