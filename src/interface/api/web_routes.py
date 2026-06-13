"""Web UI routes — PIN auth + dashboard + timeline APIs.

All ``/api/web/*`` endpoints require a valid session cookie.
The old ``/workout`` token auth is untouched.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
from base64 import b64decode, b64encode
from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from src.core.state_engine import StateEngine
from src.core.pipeline import Pipeline
from src.core.events import Command, Event, EventType, AggregateType
from src.domain.course_topology import is_excluded_course, normalize_course_name
from src.domain.dashboard.query import build_dashboard
from src.domain.calendar.conflicts import (
    detect_conflicts as _detect_conflicts,
    parse_datetime as _parse_dt,
)
from src.domain.finance.commands import FinanceCommandError, build_finance_events
from src.domain.homework.status import is_open_homework_status
from src.domain.undo.commands import build_finance_revert_events
from src.interface.api.schemas.calendar import CalendarProposalResponse
from src.interface.api.schemas.dashboard import DashboardResponse
from src.interface.api.schemas.finance import FinanceActionResponse, FinanceRevertResponse

logger = logging.getLogger(__name__)

LOCAL_TZ = timezone(timedelta(hours=8))  # Asia/Singapore
COOKIE_NAME = "web_ui_session"
_MODULE_SESSION_SECRET = ""

router = APIRouter()


# ── Session helpers ──────────────────────────────────────────────────────────


def _session_secret(request: Request) -> str:
    """Return a stable session signing secret for this process."""
    global _MODULE_SESSION_SECRET
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        return "dev-please-set-web-ui-session-secret"
    secret = settings.web_ui_session_secret
    if not secret:
        secret = getattr(request.app.state, "_web_ui_session_secret", "")
        if not secret:
            if not _MODULE_SESSION_SECRET:
                _MODULE_SESSION_SECRET = (
                    "auto-" + hashlib.sha256(secrets.token_bytes(32)).hexdigest()[:32]
                )
            secret = _MODULE_SESSION_SECRET
            request.app.state._web_ui_session_secret = secret
    return secret


def _sign(data: str, secret: str) -> str:
    return hmac.new(secret.encode(), data.encode(), hashlib.sha256).hexdigest()[:16]


def _make_session_cookie(secret: str, days: int = 7) -> str:
    """Create a signed session cookie value: base64(payload).signature"""
    expires = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
    payload = b64encode(json.dumps({"expires": expires}).encode()).decode()
    sig = _sign(payload, secret)
    return f"{payload}.{sig}"


def _validate_session(cookie: str, secret: str) -> bool:
    """Validate a signed session cookie."""
    if "." not in cookie:
        return False
    payload, sig = cookie.rsplit(".", 1)
    expected = _sign(payload, secret)
    if not hmac.compare_digest(sig, expected):
        return False
    try:
        data = json.loads(b64decode(payload).decode())
        exp = datetime.fromisoformat(data["expires"])
        if exp < datetime.now(timezone.utc):
            return False
    except (json.JSONDecodeError, KeyError, ValueError):
        return False
    return True


def _require_session(request: Request) -> None:
    """Raise 401 if session cookie is missing or invalid."""
    secret = _session_secret(request)
    raw = request.cookies.get(COOKIE_NAME, "")
    if not raw or not _validate_session(raw, secret):
        raise HTTPException(status_code=401, detail="session_required")


# ── Settings helper ──────────────────────────────────────────────────────────


def _settings(request: Request):
    s = getattr(request.app.state, "settings", None)
    if s is None:
        raise HTTPException(status_code=503, detail="settings_not_configured")
    return s


# ── Auth endpoints ───────────────────────────────────────────────────────────


@router.post("/api/web/auth/login")
async def web_login(request: Request, body: dict):
    """Validate PIN and set session cookie."""
    settings = _settings(request)
    expected = settings.web_ui_pin
    provided = body.get("pin", "")

    if not expected:
        raise HTTPException(status_code=503, detail="web_ui_pin_not_configured")

    # Use hmac.compare_digest for timing-safe comparison
    if not hmac.compare_digest(str(provided), str(expected)):
        raise HTTPException(status_code=401, detail="invalid_pin")

    secret = _session_secret(request)
    days = int(getattr(settings, "web_ui_session_days", 7))
    cookie_val = _make_session_cookie(secret, days)

    resp = JSONResponse(content={"status": "ok"})
    cookie_secure = bool(getattr(settings, "web_ui_cookie_secure", True))
    resp.set_cookie(
        key=COOKIE_NAME,
        value=cookie_val,
        httponly=True,
        samesite="lax",
        secure=cookie_secure,
        max_age=days * 86400,
        path="/",
    )
    return resp


@router.get("/api/web/auth/check")
async def web_auth_check(request: Request):
    """Check if session is valid. Returns 200 or 401."""
    _require_session(request)
    return {"status": "ok"}


@router.post("/api/web/auth/logout")
async def web_logout(request: Request, response: Response):
    """Clear session cookie."""
    resp = JSONResponse(content={"status": "logged_out"})
    resp.delete_cookie(key=COOKIE_NAME, path="/")
    return resp


# ── Dashboard endpoint ───────────────────────────────────────────────────────


def _safe_get(state: dict[str, Any], key: str, default: Any = None) -> Any:
    """Safely get a key from state dict, returning default if missing."""
    return state.get(key, default) if state else default


def _sync_health_entry(status: str, last_sync: str | None = None, error: str = "", **extra) -> dict[str, Any]:
    entry: dict[str, Any] = {"status": status}
    if last_sync:
        entry["last_sync"] = last_sync
    if error:
        entry["error"] = error
    for key, value in extra.items():
        if value not in (None, "", [], {}):
            entry[key] = value
    return entry


def _latest_task_feedback(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    behavior = _safe_get(state, "behavior", {})
    current = behavior.get("current", {}) if isinstance(behavior, dict) else {}
    log = current.get("feedback_log", []) if isinstance(current, dict) else []
    latest: dict[str, dict[str, Any]] = {}
    if not isinstance(log, list):
        return latest

    for entry in log:
        if not isinstance(entry, dict):
            continue
        task_id = str(entry.get("task_id") or "").strip()
        if not task_id:
            continue
        action = entry.get("action", "")
        outcome = entry.get("outcome", "")
        status = ""
        if outcome == "completed":
            status = "completed"
        elif action in {"skipped", "delayed"}:
            status = action
        if status:
            latest[task_id] = {
                "status": status,
                "action": action,
                "outcome": outcome,
                "timestamp": entry.get("outcome_timestamp") or entry.get("timestamp"),
                "delay_minutes": entry.get("delay_minutes"),
                "delayed_until": entry.get("delayed_until"),
            }
    return latest


def _homework_feedback_for(
    feedback: dict[str, dict[str, Any]],
    agg_id: str,
    hw: dict[str, Any],
) -> dict[str, Any] | None:
    title = str(hw.get("title") or "")
    candidates = [str(agg_id), title]
    for key in candidates:
        if key and key in feedback:
            return feedback[key]
    return None


def _legacy_build_dashboard(state_engine: StateEngine | None, settings: Any) -> dict[str, Any]:
    """Aggregate all available state into a dashboard JSON payload."""
    now = datetime.now(LOCAL_TZ)
    today = now.date()

    derived = state_engine.get_all_derived() if state_engine else {}
    state = state_engine._state if state_engine else {}

    # Deadline pressure
    deadline = derived.get("deadline_pressure", {})
    workload = derived.get("workload_density", {})
    active_ctx = derived.get("active_context", {})

    # Homework
    homework_raw = _safe_get(state, "homework", {})
    task_feedback = _latest_task_feedback(state)
    homework_hidden_count = 0
    homework_list = []
    for agg_id, hw in homework_raw.items():
        if isinstance(hw, dict) and hw.get("title"):
            feedback = _homework_feedback_for(task_feedback, str(agg_id), hw)
            feedback_status = feedback.get("status") if feedback else ""
            if feedback_status in {"completed", "skipped"}:
                homework_hidden_count += 1
                continue
            course = normalize_course_name(hw.get("course", ""), hw.get("teacher"))
            raw_status = str(hw.get("status", "pending") or "pending")
            raw_status_text = str(
                hw.get("raw_status")
                or hw.get("status_text")
                or hw.get("display_status")
                or ""
            )
            if is_excluded_course(course) or (
                feedback_status != "delayed"
                and not is_open_homework_status(raw_status, raw_status_text)
            ):
                homework_hidden_count += 1
                continue
            homework_list.append({
                "id": agg_id,
                "title": hw["title"],
                "course": course,
                "deadline": hw.get("deadline"),
                "status": feedback_status or raw_status,
                "original_status": raw_status,
                "feedback": feedback or None,
            })
    # Sort by deadline (urgent first)
    homework_list.sort(key=lambda x: x.get("deadline") or "9999-12-31")

    # Schedule (JWXT parsed)
    schedule_raw = _safe_get(state, "schedule", {})
    today_schedule = []
    schedule_date_key = today.isoformat()
    day_schedule = schedule_raw.get(schedule_date_key, [])
    if isinstance(day_schedule, list):
        for item in day_schedule:
            if isinstance(item, dict):
                today_schedule.append({
                    "course": item.get("course", item.get("name", "")),
                    "start": item.get("start", item.get("start_time", "")),
                    "end": item.get("end", item.get("end_time", "")),
                    "location": item.get("location", ""),
                    "teacher": item.get("teacher", ""),
                })

    # Temporal / time blocks
    temporal = _safe_get(state, "temporal", {})
    blocks_today = []
    temporal_blocks = temporal.get("blocks", [])
    if isinstance(temporal_blocks, dict):
        blocks_today = temporal_blocks.get(today.isoformat(), [])
    elif isinstance(temporal_blocks, list):
        blocks_today = temporal_blocks

    # Calendar events — merge legacy calendar state + Google Calendar temporal blocks
    calendar_raw = _safe_get(state, "calendar", {})
    calendar_events_today = []
    cal_day = calendar_raw.get(today.isoformat(), [])
    if isinstance(cal_day, list):
        for ev in cal_day:
            if isinstance(ev, dict):
                calendar_events_today.append({
                    "summary": ev.get("summary", ""),
                    "start": ev.get("start", ev.get("start_time", "")),
                    "end": ev.get("end", ev.get("end_time", "")),
                    "source": "legacy",
                })

    # Merge Google Calendar + JWXT temporal blocks for today
    if state_engine is not None:
        try:
            all_blocks = state_engine.get_temporal_blocks()
            for block in all_blocks:
                bd = getattr(block, "to_dict", None)
                if bd is None:
                    continue
                d = bd() if callable(bd) else bd
                source = str(d.get("source", ""))
                if source not in ("google_calendar", "jwxt"):
                    continue
                start_val = d.get("start_time") or d.get("start") or ""
                # Check if block is for today
                if isinstance(start_val, str):
                    if start_val[:10] != today.isoformat():
                        continue
                # Dedup by summary against calendar_events_today
                summary = d.get("summary") or d.get("title") or d.get("label", "") or d.get("description", "") or ""
                if any(e.get("summary") == summary for e in calendar_events_today):
                    continue
                end_val = d.get("end_time") or d.get("end") or ""
                location = d.get("location", "")
                teacher = (d.get("metadata") or {}).get("teacher", d.get("description", ""))
                entry = {
                    "summary": str(summary),
                    "start": str(start_val) if start_val else "",
                    "end": str(end_val) if end_val else "",
                    "source": source,
                }
                calendar_events_today.append(entry)
                # Also populate blocks_today for temporal/weekly view
                blocks_today.append({
                    "course": str(summary),
                    "name": str(summary),
                    "start": str(start_val) if start_val else "",
                    "start_time": str(start_val) if start_val else "",
                    "end": str(end_val) if end_val else "",
                    "end_time": str(end_val) if end_val else "",
                    "location": str(location),
                    "teacher": str(teacher),
                    "source": source,
                })
        except Exception:
            pass  # temporal blocks not initialized yet

    # Vocab
    vocab_raw = _safe_get(state, "vocab", {})
    vocab_progress = {}
    for agg_id, v in vocab_raw.items():
        if isinstance(v, dict):
            vocab_progress[agg_id] = {
                "new_words_today": v.get("new_words_today", v.get("new", 0)),
                "review_words": v.get("review_words", v.get("review", 0)),
                "total_mastered": v.get("total_mastered", v.get("mastered", 0)),
                "streak_days": v.get("streak_days", v.get("streak", 0)),
            }

    # Finance
    finance_monthly = _safe_get(state, "finance", {}).get("monthly", {})
    finance_summary = {}
    if finance_monthly:
        inflow = float(finance_monthly.get("inflow", 0) or 0)
        outflow = float(finance_monthly.get("outflow", 0) or 0)
        outing_spent = float(finance_monthly.get("outing_spent", 0) or 0)
        by_category = finance_monthly.get("by_category", {})
        reverted_action_ids: set[str] = set()
        undo_root = _safe_get(state, "undo", {})
        if isinstance(undo_root, dict):
            for undo_view in undo_root.values():
                if not isinstance(undo_view, dict):
                    continue
                for item in undo_view.get("reverted_actions", []):
                    if isinstance(item, dict) and item.get("action_id"):
                        reverted_action_ids.add(str(item["action_id"]))

        def _finance_log_entry(item: Any, action_type: str) -> dict[str, Any]:
            row = dict(item) if isinstance(item, dict) else {}
            event_id = str(row.get("event_id") or "")
            row["action_id"] = event_id
            row["action_type"] = action_type
            row["reverted"] = bool(event_id and event_id in reverted_action_ids)
            row["can_undo"] = bool(event_id and not row["reverted"])
            return row

        transactions = [
            _finance_log_entry(item, "finance_transaction")
            for item in finance_monthly.get("transactions", [])
        ]
        income_log = [
            _finance_log_entry(item, "finance_income")
            for item in finance_monthly.get("income_log", [])
        ]
        settings_budget = getattr(settings, "finance_monthly_outing_budget", 250)
        settings_savings = getattr(settings, "finance_monthly_savings_target", 500)
        finance_summary = {
            "monthly_budget": int(finance_monthly.get("outing_budget", settings_budget)),
            "monthly_spend": int(outflow),
            "inflow": int(inflow),
            "outflow": int(outflow),
            "outing_spent": int(outing_spent),
            "by_category": {k: int(v) for k, v in by_category.items()},
            "estimated_savings": int(max(0, inflow - outflow)),
            "savings_target": int(finance_monthly.get("savings_target", settings_savings)),
            "savings_progress": int(finance_monthly.get("savings_progress",
                finance_monthly.get("current_savings", 0))),
            "partner_debt": int(finance_monthly.get("partner_debt",
                finance_monthly.get("partner_debt_total", 0))),
            "transactions": transactions[-30:],
            "income_log": income_log[-30:],
            "reverted_action_ids": sorted(reverted_action_ids),
        }

    # Parent funds
    parent_funds_raw = _safe_get(state, "parent_funds", {}).get("current", {})
    parent_funds_summary = {}
    if parent_funds_raw:
        parent_funds_summary = {
            "planned_requests": parent_funds_raw.get("planned_requests", []),
            "request_log": parent_funds_raw.get("request_log", []),
            "received_log": parent_funds_raw.get("received_log", []),
            "recurring_items": parent_funds_raw.get("recurring_items", []),
            "recurring_rules": parent_funds_raw.get("recurring_rules", []),
        }

    # Partner debts
    partner_debts_raw = _safe_get(state, "partner_debts", {}).get("current", {})
    partner_debts_summary = {}
    if partner_debts_raw:
        debts = partner_debts_raw.get("debts", [])
        partner_debts_summary = {
            "total_outstanding": int(partner_debts_raw.get("total_outstanding", 0)),
            "debts": debts[-10:] if debts else [],
        }

    # Art planning
    art_raw = _safe_get(state, "art", {})
    art_summary = {}
    if art_raw:
        today_plan = art_raw.get(today.isoformat(), {})
        if isinstance(today_plan, dict):
            art_summary = {
                "planned_minutes": today_plan.get("planned_minutes", today_plan.get("target", 0)),
                "completed_minutes": today_plan.get("completed_minutes", today_plan.get("completed", 0)),
                "status": today_plan.get("status", "pending"),
            }

    # Fitness summary (today's workout from Obsidian vault)
    fitness_summary = {}
    vault_path = getattr(settings, "obsidian_vault_path", "")
    if vault_path:
        try:
            from src.domain.fitness.ui_service import read_session
            session = read_session(vault_path, today)
            if session:
                pct = round(session["completed_sets"] / max(session["total_sets"], 1) * 100)
                fitness_summary = {
                    "training_day": session.get("training_day", ""),
                    "focus": session.get("focus", ""),
                    "total_sets": session["total_sets"],
                    "completed_sets": session["completed_sets"],
                    "completion_pct": pct,
                    "completed": session.get("completed", False),
                }
        except Exception:
            logger.debug("fitness session read failed (expected if no workout file)")

    # Sync health
    sync_health = {
        "chaoxing": {"status": "unknown"},
        "jwxt": {"status": "unknown"},
        "google_calendar": {"status": "unknown"},
        "momo": {"status": "unknown"},
    }
    # Try to infer from sync events or state
    sync_state = _safe_get(state, "sync", {})
    if sync_state:
        for src in ("chaoxing", "jwxt", "google_calendar", "momo"):
            entry = sync_state.get(src, {})
            if src == "momo" and not entry:
                entry = sync_state.get("momo_vocab", {})
            if entry:
                sync_health[src] = {
                    "status": entry.get("status", "unknown"),
                    "last_sync": entry.get(
                        "last_sync",
                        entry.get("last_sync_at", entry.get("last_sync_completed", entry.get("last_sync_failed"))),
                    ),
                    "error": entry.get("error", ""),
                    "count": entry.get("count", entry.get("block_count", entry.get("total_assignments"))),
                    "duration_ms": entry.get("duration_ms"),
                }

    temporal_projection = _safe_get(state, "temporal", {}).get("projection", {})
    calendar_sync = temporal_projection.get("calendar_sync", {}) if isinstance(temporal_projection, dict) else {}
    if calendar_sync and sync_health["google_calendar"].get("status") == "unknown":
        sync_health["google_calendar"] = _sync_health_entry(
            calendar_sync.get("status", "unknown"),
            calendar_sync.get("completed_at", calendar_sync.get("started_at")),
            calendar_id=calendar_sync.get("calendar_id"),
            calendar_count=calendar_sync.get("calendar_count"),
            count=calendar_sync.get("count"),
        )

    vocab_state = _safe_get(state, "vocab", {}).get("momo", {})
    if vocab_state and sync_health["momo"].get("status") == "unknown":
        sync_health["momo"] = _sync_health_entry(
            vocab_state.get("sync_status", "unknown"),
            vocab_state.get("last_sync_completed", vocab_state.get("last_sync_started", vocab_state.get("last_sync"))),
            vocab_state.get("last_error", ""),
            external_last_sync=vocab_state.get("last_sync"),
            stale=vocab_state.get("stale"),
        )

    calendar_consistency = _safe_get(state, "calendar_consistency", {})

    return {
        "today": today.isoformat(),
        "weekday": _weekday_cn(today),
        "deadline_pressure": deadline,
        "workload_density": workload,
        "active_context": active_ctx,
        "homework": homework_list,
        "homework_count": len(homework_list),
        "homework_hidden_count": homework_hidden_count,
        "today_schedule": today_schedule,
        "calendar_events": calendar_events_today,
        "temporal_blocks": blocks_today,
        "vocab_progress": vocab_progress,
        "fitness": fitness_summary,
        "finance": finance_summary,
        "parent_funds": parent_funds_summary,
        "partner_debts": partner_debts_summary,
        "art": art_summary,
        "sync_health": sync_health,
        "calendar_consistency": {
            "latest": calendar_consistency.get("latest", {}),
            "repair": calendar_consistency.get("repair", {}),
        },
    }


def _settings_from_finance(finance_raw: dict) -> dict:
    """Extract budget/target settings from finance state."""
    if not finance_raw:
        return {}
    return {
        "monthly_budget": finance_raw.get("outing_budget", finance_raw.get("budget", 0)),
        "savings_target": finance_raw.get("savings_target", 0),
    }


def _weekday_cn(d: date) -> str:
    names = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    return names[d.weekday()]


@router.get("/api/web/dashboard", response_model=DashboardResponse)
async def web_dashboard(request: Request):
    """Aggregate dashboard for Web UI."""
    _require_session(request)
    engine: StateEngine | None = getattr(request.app.state, "state_engine", None)
    settings = _settings(request)
    data = build_dashboard(engine, settings)
    return data


# ── Finance action endpoint ────────────────────────────────────────────────────


@router.post("/api/web/finance/action", response_model=FinanceActionResponse)
async def web_finance_action(request: Request, body: dict):
    """Execute a structured finance action and return refreshed dashboard.

    Request:
      { "action": "expense",        "amount": 18, "category":"emotional", "description":"奶茶" }
      { "action": "income",         "amount": 1000, "source":"生活费", "description":"生活费到账" }
      { "action": "parent_received","amount": 150, "person":"爸爸", "description":"买画材", "item_id":"..." }
      { "action": "parent_plan",    "amount": 100, "person":"妈妈", "description":"话费",
                                    "requested_date":"2026-06-10", "item_id":"phone_bill", "category":"..." }
      { "action": "partner_debt",   "amount": 500, "description":"借给对象", "date":"2026-05-03",
                                    "counterparty":"对象" }
    """
    _require_session(request)

    action = str(body.get("action", "")).strip()
    if not action:
        return JSONResponse(status_code=400, content={"ok": False, "message": "action field is required"})

    pipeline: Pipeline | None = getattr(request.app.state, "pipeline", None)
    if pipeline is None:
        return JSONResponse(status_code=503, content={"ok": False, "message": "pipeline not available"})

    user_id = _web_user_id(request)
    trace_id = str(uuid4())
    metadata = _web_event_metadata(user_id, trace_id)
    try:
        root_events = build_finance_events(action, body, user_id, metadata)
        produced: list[Event] = []
        for event in root_events:
            produced.extend(await pipeline.run(event))
    except FinanceCommandError as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content={"ok": False, "message": exc.message},
        )
    except Exception as exc:
        logger.exception("finance action failed: %s", action)
        return {"ok": False, "message": f"操作异常: {str(exc)[:80]}"}

    engine: StateEngine | None = getattr(request.app.state, "state_engine", None)
    settings_obj = _settings(request)
    dashboard = build_dashboard(engine, settings_obj) if engine else None
    tracked = _track_web_actions(root_events + produced, f"finance_{action}", user_id)
    return {
        "ok": True,
        "message": f"已记录{_finance_action_label(action)}",
        "action": action,
        "events": len(root_events) + len(produced),
        "needs_followup": False,
        "dashboard": dashboard,
        "action_id": tracked[0]["action_id"] if tracked else None,
        "can_undo": action in {"expense", "income"},
    }

    produced: list[Event] = []
    root_events: list[Event] = []

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
                payload={"amount": amount, "category": category, "description": description or f"消费{amount}"},
                metadata=metadata,
            )
            root_events.append(event)
            produced = await pipeline.run(event)

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
                payload={"amount": amount, "source": source, "description": description or f"收入{amount}"},
                metadata=metadata,
            )
            root_events.append(event)
            produced = await pipeline.run(event)

        elif action == "parent_received":
            amount = float(body.get("amount", 0))
            person = str(body.get("person", "家庭")).strip() or "家庭"
            description = str(body.get("description", "")).strip()
            item_id = body.get("item_id")
            if amount <= 0:
                return JSONResponse(status_code=400, content={"ok": False, "message": "amount must be > 0"})
            for event_data in [
                (EventType.PARENT_FUND_REQUEST_RECORDED,
                 {"amount": amount, "description": description, "item_id": item_id}),
                (EventType.PARENT_FUND_RECEIVED,
                 {"amount": amount, "description": description, "item_id": item_id, "source": person}),
                (EventType.FINANCE_INCOME_RECORDED,
                 {"amount": amount, "source": person, "description": description}),
            ]:
                etype, epayload = event_data
                ev = Event(
                    event_type=etype,
                    aggregate_id=user_id,
                    aggregate_type=AggregateType.FINANCE,
                    payload=epayload,
                    metadata=metadata,
                )
                root_events.append(ev)
                sub = await pipeline.run(ev)
                produced.extend(sub)

        elif action == "parent_plan":
            amount = float(body.get("amount", 0))
            person = str(body.get("person", "")).strip()
            description = str(body.get("description", "")).strip()
            requested_date = str(body.get("requested_date", "")).strip()
            item_id = body.get("item_id")
            category = str(body.get("category", "other")).strip()
            if amount <= 0:
                return JSONResponse(status_code=400, content={"ok": False, "message": "amount must be > 0"})
            if not requested_date:
                return JSONResponse(status_code=400, content={"ok": False, "message": "requested_date is required"})
            if requested_date:
                try:
                    date.fromisoformat(requested_date[:10])
                except ValueError:
                    return JSONResponse(status_code=400, content={"ok": False, "message": "requested_date is invalid"})
            event = Event(
                event_type=EventType.PARENT_FUND_REQUEST_PLANNED,
                aggregate_id=user_id,
                aggregate_type=AggregateType.FINANCE,
                payload={
                    "amount": amount,
                    "person": person,
                    "description": description,
                    "requested_date": requested_date,
                    "item_id": item_id,
                    "category": category,
                    "action": "advise",
                },
                metadata=metadata,
            )
            root_events.append(event)
            produced = await pipeline.run(event)

        elif action == "partner_debt":
            amount = float(body.get("amount", 0))
            description = str(body.get("description", "")).strip()
            date_str = str(body.get("date", "")).strip()
            counterparty = str(body.get("counterparty", "对象")).strip()
            if amount <= 0:
                return JSONResponse(status_code=400, content={"ok": False, "message": "amount must be > 0"})
            if date_str:
                try:
                    date.fromisoformat(date_str[:10])
                except ValueError:
                    return JSONResponse(status_code=400, content={"ok": False, "message": "date is invalid"})
            event = Event(
                event_type=EventType.PARTNER_DEBT_CREATED,
                aggregate_id=user_id,
                aggregate_type=AggregateType.FINANCE,
                payload={
                    "amount": amount,
                    "description": description,
                    "date": date_str,
                    "counterparty": counterparty,
                },
                metadata=metadata,
            )
            root_events.append(event)
            produced = await pipeline.run(event)

        else:
            return JSONResponse(status_code=400, content={
                "ok": False, "message": f"unknown action: {action}",
            })

    except Exception as exc:
        logger.exception("finance action failed: %s", action)
        return {"ok": False, "message": f"操作异常: {str(exc)[:80]}"}

    # Re-read dashboard
    engine: StateEngine | None = getattr(request.app.state, "state_engine", None)
    settings_obj = _settings(request)
    dashboard = build_dashboard(engine, settings_obj) if engine else None

    # Track undoable actions
    tracked = _track_web_actions(root_events + produced, f"finance_{action}", user_id)
    can_undo = action in {"expense", "income"}
    event_count = len(root_events) + len(produced)

    return {
        "ok": True,
        "message": f"已记录{_finance_action_label(action)}",
        "action": action,
        "events": event_count,
        "needs_followup": False,
        "dashboard": dashboard,
        "action_id": tracked[0]["action_id"] if tracked else None,
        "can_undo": can_undo,
    }


def _finance_action_label(action: str) -> str:
    labels = {
        "expense": "支出",
        "income": "收入",
        "parent_received": "父母资助到账",
        "parent_plan": "要钱计划",
        "partner_debt": "对象欠款",
    }
    return labels.get(action, action)


@router.post("/api/web/finance/revert", response_model=FinanceRevertResponse)
async def web_finance_revert(request: Request, body: dict):
    """Revert a concrete finance ledger row through canonical undo events.

    This is separate from /api/web/actions/undo because ledger rows must remain
    reversible after the in-process recent-action cache is gone.
    """
    _require_session(request)

    action_type = str(body.get("action_type") or "").strip()
    if action_type not in {"finance_transaction", "finance_income"}:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "message": "action_type 必须是 finance_transaction 或 finance_income"},
        )

    action_id = str(body.get("action_id") or "").strip()
    if not action_id:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "message": "旧账目缺少事件 id，不能从明细直接撤回"},
        )

    try:
        amount = float(body.get("amount", 0))
    except (TypeError, ValueError):
        amount = 0
    if amount <= 0:
        return JSONResponse(status_code=400, content={"ok": False, "message": "amount must be > 0"})

    user_id = _web_user_id(request)
    engine: StateEngine | None = getattr(request.app.state, "state_engine", None)
    if engine is not None:
        undo_view = _safe_get(engine._state, "undo", {}).get(user_id, {})
        if isinstance(undo_view, dict):
            for item in undo_view.get("reverted_actions", []):
                if isinstance(item, dict) and item.get("action_id") == action_id:
                    settings_obj = _settings(request)
                    return {
                        "ok": False,
                        "message": "这条账目已经撤回过。",
                        "needs_followup": True,
                        "dashboard": build_dashboard(engine, settings_obj),
                    }

    pipeline: Pipeline | None = getattr(request.app.state, "pipeline", None)
    if pipeline is None:
        return JSONResponse(status_code=503, content={"ok": False, "message": "pipeline not available"})

    trace_id = str(uuid4())
    meta = _web_event_metadata(user_id, trace_id)
    category = str(body.get("category") or "other")

    undo_requested, reverted = build_finance_revert_events(
        user_id=user_id,
        action_id=action_id,
        action_type=action_type,
        amount=amount,
        category=category,
        metadata=meta,
    )

    try:
        await pipeline.run(undo_requested)
        await pipeline.run(reverted)
    except Exception as exc:
        logger.exception("finance ledger revert failed")
        return {"ok": False, "message": f"撤回执行异常: {str(exc)[:80]}", "needs_followup": True}

    settings_obj = _settings(request)
    dashboard = build_dashboard(engine, settings_obj) if engine else None
    return {
        "ok": True,
        "message": "已撤回账目。",
        "needs_followup": False,
        "action_id": action_id,
        "action_type": action_type,
        "events": 2,
        "dashboard": dashboard,
    }


# ── Conflict detection helpers ────────────────────────────────────────────────


def _legacy_parse_dt(s: str, date_ref: date | None = None) -> datetime | None:
    """Parse a datetime string that could be ISO format or HH:MM.

    Returns None if ``s`` is empty or unparseable.
    """
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=LOCAL_TZ)
        return dt
    except (ValueError, TypeError):
        pass
    # HH:MM with date_ref
    if date_ref and ":" in s and len(s) <= 5:
        try:
            return datetime.fromisoformat(f"{date_ref.isoformat()}T{s}:00").replace(tzinfo=LOCAL_TZ)
        except (ValueError, TypeError):
            pass
    return None


def _legacy_detect_conflicts(
    candidate_start: datetime,
    candidate_end: datetime,
    state: dict[str, Any],
    exclude_event_id: str = "",
    temporal_blocks_by_day: dict[str, Any] | None = None,
    temporal_blocks: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Detect overlaps between a candidate time range and existing events.

    Checks JWXT schedule, google_calendar state, temporal Google blocks,
    and system temporal plan blocks. Homework deadlines without start/end
    do not count as time conflicts.

    Returns a list of compact conflict items:
    ``{source, type, title, start, end, location?, event_id?}``
    """
    conflicts: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    d = candidate_start.date()

    def _overlaps(s1: datetime, e1: datetime, s2: datetime, e2: datetime) -> bool:
        return s1 < e2 and s2 < e1

    def _append_conflict(item: dict[str, Any]) -> None:
        key = (
            str(item.get("source", "")),
            str(item.get("event_id", "")),
            str(item.get("title", "")),
            str(item.get("start", "")),
            str(item.get("end", "")),
        )
        if key in seen:
            return
        seen.add(key)
        conflicts.append(item)

    # 1. JWXT Schedule events
    schedule_raw = state.get("schedule", {})
    day_schedule = schedule_raw.get(d.isoformat(), [])
    if isinstance(day_schedule, list):
        for item in day_schedule:
            if not isinstance(item, dict):
                continue
            start_str = item.get("start", item.get("start_time", ""))
            end_str = item.get("end", item.get("end_time", ""))
            s = _parse_dt(start_str, d)
            if s is None:
                continue
            e = _parse_dt(end_str, d) if end_str else s + timedelta(hours=1)
            if _overlaps(candidate_start, candidate_end, s, e):
                _append_conflict({
                    "source": "jwxt",
                    "type": "class",
                    "title": item.get("course", item.get("name", "课程")),
                    "start": s.isoformat(),
                    "end": e.isoformat(),
                    "location": item.get("location", ""),
                })

    # 2. Google Calendar events from state["calendar"]
    calendar_raw = state.get("calendar", {})
    cal_day = calendar_raw.get(d.isoformat(), [])
    if isinstance(cal_day, list):
        for ev in cal_day:
            if not isinstance(ev, dict):
                continue
            ev_id = ev.get("event_id", ev.get("id", ""))
            if exclude_event_id and ev_id == exclude_event_id:
                continue
            start_str = ev.get("start", ev.get("start_time", ""))
            end_str = ev.get("end", ev.get("end_time", ""))
            s = _parse_dt(start_str, d)
            if s is None:
                continue
            e = _parse_dt(end_str, d) if end_str else s + timedelta(hours=1)
            if _overlaps(candidate_start, candidate_end, s, e):
                _append_conflict({
                    "source": "google_calendar",
                    "type": "event",
                    "title": ev.get("summary", "事件"),
                    "start": s.isoformat(),
                    "end": e.isoformat(),
                    "location": ev.get("location", ""),
                    "event_id": ev_id,
                })

    # 3. System temporal plan blocks
    temporal = state.get("temporal", {})
    state_temporal_blocks = temporal.get("blocks", {})
    if isinstance(state_temporal_blocks, dict):
        day_blocks = state_temporal_blocks.get(d.isoformat(), [])
        if isinstance(day_blocks, list):
            for block in day_blocks:
                if not isinstance(block, dict):
                    continue
                start_str = block.get("start", block.get("start_time", ""))
                end_str = block.get("end", block.get("end_time", ""))
                s = _parse_dt(start_str, d)
                if s is None:
                    continue
                e = _parse_dt(end_str, d) if end_str else s + timedelta(hours=1)
                if _overlaps(candidate_start, candidate_end, s, e):
                    _append_conflict({
                        "source": "system",
                        "type": "plan_block",
                        "title": block.get("label", block.get("title", "计划块")),
                        "start": s.isoformat(),
                        "end": e.isoformat(),
                    })

    # 4. Rich temporal blocks held by StateEngine (Google Calendar and system blocks)
    if temporal_blocks_by_day and temporal_blocks:
        day_block_keys = temporal_blocks_by_day.get(d.isoformat(), [])
        if isinstance(day_block_keys, list):
            for block_key in day_block_keys:
                block = temporal_blocks.get(block_key)
                if block is None:
                    continue
                source_obj = getattr(block, "source", "")
                source = getattr(source_obj, "value", str(source_obj))
                if source not in {"google_calendar", "system"}:
                    continue
                meta = getattr(block, "metadata", {}) or {}
                event_id = str(meta.get("external_id", ""))
                if exclude_event_id and event_id == exclude_event_id:
                    continue
                block_start = getattr(block, "start", None)
                block_end = getattr(block, "end", None)
                s = block_start if isinstance(block_start, datetime) else _parse_dt(str(block_start), d)
                if s is None:
                    continue
                e = block_end if isinstance(block_end, datetime) else _parse_dt(str(block_end), d)
                if e is None:
                    e = s + timedelta(hours=1)
                if _overlaps(candidate_start, candidate_end, s, e):
                    block_type_obj = getattr(block, "block_type", "")
                    block_type = getattr(block_type_obj, "value", str(block_type_obj)) or "time_block"
                    _append_conflict({
                        "source": source,
                        "type": block_type,
                        "title": getattr(block, "title", "时间块"),
                        "start": s.isoformat(),
                        "end": e.isoformat(),
                        "location": getattr(block, "location", ""),
                        "event_id": event_id,
                    })

    return conflicts


# ── Timeline endpoint ─────────────────────────────────────────────────────────


@router.get("/api/web/timeline")
async def web_timeline(request: Request, date_str: str | None = None):
    """Return timeline events for a given date (default today)."""
    _require_session(request)
    engine: StateEngine | None = getattr(request.app.state, "state_engine", None)
    if engine is None:
        raise HTTPException(status_code=503, detail="state_engine_not_available")

    d = date.fromisoformat(date_str) if date_str else datetime.now(LOCAL_TZ).date()
    state = engine._state or {}

    events: list[dict[str, Any]] = []

    # 1. JWXT Schedule events
    schedule_raw = _safe_get(state, "schedule", {})
    day_schedule = schedule_raw.get(d.isoformat(), [])
    if isinstance(day_schedule, list):
        for item in day_schedule:
            if isinstance(item, dict):
                events.append({
                    "source": "jwxt",
                    "type": "class",
                    "title": item.get("course", item.get("name", "课程")),
                    "start": item.get("start", item.get("start_time", "")),
                    "end": item.get("end", item.get("end_time", "")),
                    "location": item.get("location", ""),
                    "teacher": item.get("teacher", ""),
                })

    # 2. Google Calendar events (from state["calendar"] and temporal blocks)
    calendar_raw = _safe_get(state, "calendar", {})
    cal_day = calendar_raw.get(d.isoformat(), [])
    if isinstance(cal_day, list):
        for ev in cal_day:
            if isinstance(ev, dict):
                events.append({
                    "source": "google_calendar",
                    "type": "event",
                    "title": ev.get("summary", "事件"),
                    "start": ev.get("start", ev.get("start_time", "")),
                    "end": ev.get("end", ev.get("end_time", "")),
                    "location": ev.get("location", ""),
                    "event_id": ev.get("event_id", ev.get("id", "")),
                    "calendar_id": ev.get("calendar_id", ""),
                })

    # Also read calendar events from temporal blocks (richer metadata with event_id)
    temporal_blocks_by_day = getattr(engine, "_temporal_blocks_by_day", {})
    temporal_blocks = getattr(engine, "_temporal_blocks", {})
    day_block_keys = temporal_blocks_by_day.get(d.isoformat(), [])
    for block_key in day_block_keys:
        block = temporal_blocks.get(block_key)
        if block is None:
            continue
        block_source = str(getattr(block, "source", ""))
        if block_source != "google_calendar":
            continue
        meta = getattr(block, "metadata", {}) or {}
        block_title = getattr(block, "title", "事件")
        block_start = getattr(block, "start", None)
        block_end = getattr(block, "end", None)
        event_id = meta.get("external_id", "")
        calendar_id = meta.get("calendar_id", "")
        events.append({
            "source": "google_calendar",
            "type": "event",
            "title": block_title,
            "start": block_start.isoformat() if hasattr(block_start, "isoformat") else str(block_start),
            "end": block_end.isoformat() if hasattr(block_end, "isoformat") else str(block_end),
            "location": block.location if hasattr(block, "location") else "",
            "event_id": event_id,
            "calendar_id": calendar_id,
        })

    # 3. Temporal blocks (system plan blocks)
    temporal = _safe_get(state, "temporal", {})
    temporal_blocks_dict = temporal.get("blocks", {})
    if isinstance(temporal_blocks_dict, dict):
        day_blocks = temporal_blocks_dict.get(d.isoformat(), [])
        for block in day_blocks:
            if isinstance(block, dict):
                events.append({
                    "source": "system",
                    "type": "plan_block",
                    "title": block.get("label", block.get("title", "计划块")),
                    "start": block.get("start", block.get("start_time", "")),
                    "end": block.get("end", block.get("end_time", "")),
                    "block_type": block.get("type", ""),
                })

    # 4. Homework deadlines due on or before this date
    homework_raw = _safe_get(state, "homework", {})
    for agg_id, hw in homework_raw.items():
        if not isinstance(hw, dict):
            continue
        dl = hw.get("deadline", "")
        if not dl:
            continue
        try:
            dl_date = date.fromisoformat(dl[:10])
        except (ValueError, TypeError):
            continue
        if dl_date != d:
            continue
        events.append({
            "source": "homework",
            "type": "deadline",
            "title": hw.get("title", "作业"),
            "course": hw.get("course", ""),
            "deadline": dl,
            "status": hw.get("status", "pending"),
        })

    # 5. Art plan blocks
    art_raw = _safe_get(state, "art", {})
    day_art = art_raw.get(d.isoformat(), {})
    if isinstance(day_art, dict) and day_art:
        events.append({
            "source": "system",
            "type": "art_plan",
            "title": "艺术创作",
            "planned_minutes": day_art.get("planned_minutes", day_art.get("target", 0)),
            "completed_minutes": day_art.get("completed_minutes", day_art.get("completed", 0)),
        })

    events = _dedupe_timeline_events(events)

    # Sort by start time (events with no time first)
    def _sort_key(e: dict) -> str:
        return e.get("start", e.get("deadline", "")) or ""

    events.sort(key=_sort_key)

    return {"date": d.isoformat(), "count": len(events), "events": events}


def _dedupe_timeline_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove duplicate timeline entries from overlapping calendar sources.

    Google Calendar events may be visible through both legacy ``state["calendar"]``
    and richer temporal blocks. Prefer the entry carrying event_id/calendar_id
    because it unlocks Web edit/delete controls.
    """
    deduped: list[dict[str, Any]] = []
    index_by_key: dict[tuple[Any, ...], int] = {}

    for event in events:
        if event.get("source") != "google_calendar":
            deduped.append(event)
            continue

        key = (
            "google_calendar",
            event.get("calendar_id") or "primary",
            event.get("title") or "",
            event.get("start") or "",
            event.get("end") or "",
            event.get("location") or "",
        )
        old_index = index_by_key.get(key)
        if old_index is None:
            index_by_key[key] = len(deduped)
            deduped.append(event)
            continue

        old_event = deduped[old_index]
        if event.get("event_id") and not old_event.get("event_id"):
            deduped[old_index] = event

    return deduped


# ── Calendar Proposal endpoint ─────────────────────────────────────────


@router.post("/api/web/calendar/proposal", response_model=CalendarProposalResponse)
async def web_calendar_proposal(request: Request, body: dict):
    """Create a proposal for a Google Calendar event (create/update/delete).

    Request:
      create: { action:"create", title, date, start_time, end_time, location, note }
      update: { action:"update", title, date, start_time, end_time, location, note,
                event_id, calendar_id }
      delete: { action:"delete", event_id, calendar_id }

    Returns proposal JSON. The frontend stores it and passes to the existing
    proposal decision endpoint on accept/reject. Calendar writes never happen
    before proposal acceptance.
    """
    _require_session(request)

    action = str(body.get("action", "")).strip().lower()
    if action not in {"create", "update", "delete"}:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "message": "action must be 'create', 'update', or 'delete'"},
        )

    pipeline: Pipeline | None = getattr(request.app.state, "pipeline", None)
    if pipeline is None:
        return JSONResponse(
            status_code=503,
            content={"ok": False, "message": "pipeline not available", "needs_followup": True},
        )

    user_id = _web_user_id(request)
    trace_id = str(uuid4())

    if action == "create":
        title = str(body.get("title", "")).strip()
        date_str = str(body.get("date", "")).strip()
        start_time = str(body.get("start_time", "")).strip()
        end_time = str(body.get("end_time", "")).strip()

        if not title or not date_str or not start_time:
            return JSONResponse(
                status_code=400,
                content={"ok": False, "message": "title, date, and start_time are required"},
            )

        try:
            date.fromisoformat(date_str)
        except ValueError:
            return JSONResponse(
                status_code=400,
                content={"ok": False, "message": f"invalid date: {date_str}"},
            )

        try:
            start_dt = datetime.fromisoformat(f"{date_str}T{start_time}:00").replace(tzinfo=LOCAL_TZ)
            if end_time:
                end_dt = datetime.fromisoformat(f"{date_str}T{end_time}:00").replace(tzinfo=LOCAL_TZ)
            else:
                end_dt = start_dt + timedelta(hours=1)
        except ValueError:
            return JSONResponse(
                status_code=400,
                content={"ok": False, "message": "无效的时间格式，请使用 HH:MM"},
            )
        if end_dt <= start_dt:
            return JSONResponse(
                status_code=400,
                content={"ok": False, "message": "end_time must be after start_time"},
            )

        location = str(body.get("location", "")).strip()
        note = str(body.get("note", "")).strip()

        action_payload = {
            "title": title,
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
            "location": location,
            "description": note or "Web 日历表单创建",
            "calendar_id": "primary",
            "source": "web_ui_calendar_form",
        }

        from src.core.proposal import Proposal, ProposalType, TargetSystem
        proposal = Proposal(
            proposal_type=ProposalType.CREATE_CALENDAR_BLOCK,
            target_system=TargetSystem.GOOGLE_CALENDAR,
            action_payload=action_payload,
            reason=f"Web 日历表单：{title}",
            confidence=0.8,
            user_id=user_id,
        )

    elif action == "update":
        event_id = str(body.get("event_id", "")).strip()
        calendar_id = str(body.get("calendar_id", "")).strip() or "primary"
        if not event_id:
            return JSONResponse(
                status_code=400,
                content={"ok": False, "message": "event_id is required for update"},
            )

        title = str(body.get("title", "")).strip() or "更新事件"
        date_str = str(body.get("date", "")).strip()
        start_time = str(body.get("start_time", "")).strip()
        end_time = str(body.get("end_time", "")).strip()

        action_payload = {
            "event_id": event_id,
            "calendar_id": calendar_id,
            "title": title,
            "location": str(body.get("location", "")).strip(),
            "description": str(body.get("note", "")).strip() or "Web 日历更新",
            "source": "web_ui_calendar_form",
        }
        if date_str and start_time:
            try:
                start_dt = datetime.fromisoformat(f"{date_str}T{start_time}:00").replace(tzinfo=LOCAL_TZ)
                action_payload["start"] = start_dt.isoformat()
                if end_time:
                    end_dt = datetime.fromisoformat(f"{date_str}T{end_time}:00").replace(tzinfo=LOCAL_TZ)
                    action_payload["end"] = end_dt.isoformat()
                else:
                    action_payload["end"] = (start_dt + timedelta(hours=1)).isoformat()
                if datetime.fromisoformat(action_payload["end"]) <= start_dt:
                    return JSONResponse(
                        status_code=400,
                        content={"ok": False, "message": "end_time must be after start_time"},
                    )
            except ValueError:
                return JSONResponse(
                    status_code=400,
                    content={"ok": False, "message": "invalid date/start_time/end_time for update"},
                )

        from src.core.proposal import Proposal, ProposalType, TargetSystem
        proposal = Proposal(
            proposal_type=ProposalType.UPDATE_CALENDAR_EVENT,
            target_system=TargetSystem.GOOGLE_CALENDAR,
            action_payload=action_payload,
            reason=f"Web 更新日历事件：{title}",
            confidence=0.8,
            user_id=user_id,
        )

    else:  # delete
        event_id = str(body.get("event_id", "")).strip()
        calendar_id = str(body.get("calendar_id", "")).strip() or "primary"
        if not event_id:
            return JSONResponse(
                status_code=400,
                content={"ok": False, "message": "event_id is required for delete"},
            )

        title = str(body.get("title", event_id)).strip()
        action_payload = {
            "event_id": event_id,
            "calendar_id": calendar_id,
            "title": title,
            "source": "web_ui_calendar_form",
        }

        from src.core.proposal import Proposal, ProposalType, TargetSystem
        proposal = Proposal(
            proposal_type=ProposalType.DELETE_CALENDAR_EVENT,
            target_system=TargetSystem.GOOGLE_CALENDAR,
            action_payload=action_payload,
            reason=f"Web 删除日历事件：{title}",
            confidence=0.9,
            user_id=user_id,
        )

    proposal_event = Event(
        event_type=EventType.EXECUTION_PROPOSAL_CREATED,
        aggregate_id=proposal.proposal_id,
        aggregate_type=AggregateType.SYSTEM,
        payload=proposal.to_dict(),
        metadata=_web_event_metadata(user_id, trace_id),
    )

    # ── Conflict detection ────────────────────────────────────────────
    conflicts: list[dict[str, Any]] = []
    if action in ("create", "update") and body.get("start_time"):
        engine_for_state: StateEngine | None = getattr(request.app.state, "state_engine", None)
        state = engine_for_state._state if engine_for_state else {}
        try:
            date_str_conflict = str(body.get("date", "")).strip()
            start_time_conflict = str(body.get("start_time", "")).strip()
            end_time_conflict = str(body.get("end_time", "")).strip()
            cs = _parse_dt(f"{date_str_conflict}T{start_time_conflict}:00") or \
                 datetime.fromisoformat(f"{date_str_conflict}T{start_time_conflict}:00").replace(tzinfo=LOCAL_TZ)
            if end_time_conflict:
                ce = _parse_dt(f"{date_str_conflict}T{end_time_conflict}:00") or \
                     datetime.fromisoformat(f"{date_str_conflict}T{end_time_conflict}:00").replace(tzinfo=LOCAL_TZ)
            else:
                ce = cs + timedelta(hours=1)
            exclude = str(body.get("event_id", "")) if action == "update" else ""
            conflicts = _detect_conflicts(
                cs,
                ce,
                state,
                exclude_event_id=exclude,
                temporal_blocks_by_day=getattr(engine_for_state, "_temporal_blocks_by_day", {}) if engine_for_state else {},
                temporal_blocks=getattr(engine_for_state, "_temporal_blocks", {}) if engine_for_state else {},
            )
        except (ValueError, TypeError, Exception):
            logger.debug("conflict detection skipped for calendar proposal", exc_info=True)

    try:
        produced = await pipeline.run(proposal_event)
    except Exception as exc:
        logger.exception("calendar proposal creation failed")
        return {
            "ok": False,
            "needs_followup": True,
            "message": f"日历提案创建异常: {str(exc)[:80]}",
        }

    engine: StateEngine | None = getattr(request.app.state, "state_engine", None)
    settings_obj = _settings(request)
    dashboard = build_dashboard(engine, settings_obj) if engine else None

    resp: dict[str, Any] = {
        "ok": True,
        "message": f"已创建日历提案：{title}",
        "command_type": "calendar_proposal",
        "events": len(produced),
        "needs_followup": False,
        "proposal": proposal.to_dict(),
        "dashboard": dashboard,
    }
    if conflicts:
        resp["conflicts"] = conflicts
    return resp


# ── Action Gateway ─────────────────────────────────────────────────────────


# Quick action ID → deterministic mapping
_QUICK_ACTIONS: dict[str, dict[str, str]] = {
    "sync_refresh": {"command_type": "sync_refresh", "text": "同步刷新数据"},
    "check_homework": {"command_type": "check_homework", "text": "/homework"},
    "show_today": {"command_type": "show_today", "text": "/today"},
    "hydration_250": {"command_type": "quick_hydration", "text": "补水250"},
    "hydration_500": {"command_type": "quick_hydration", "text": "补水500"},
    "bad_state": {"command_type": "record_bad_state", "text": "状态差"},
    "school_leave_today": {"command_type": "record_school_leave", "text": "请假今天"},
    "undo_last": {"command_type": "undo_last_action", "text": "撤回上一条"},
    "complete_art_30": {"command_type": "art_progress", "text": "完成了画画30分钟"},
    "complete_art_60": {"command_type": "art_progress", "text": "完成了画画60分钟"},
    "complete_homework": {"command_type": "generic_completion", "text": "完成了作业"},
    "skip_homework": {"command_type": "task_skip", "text": "跳过作业"},
    "delay_homework_30": {"command_type": "task_delay", "text": "作业稍后30分钟"},
    "log_finance_spend": {"command_type": "finance_transaction", "text": "花了20"},
    "log_finance_income": {"command_type": "finance_transaction", "text": "生活费到账1000"},
}

# Read-only commands — no mutation, return dashboard directly
_READ_ONLY_COMMANDS: frozenset[str] = frozenset({
    "check_homework", "show_today", "show_free_today", "show_week_load",
    "show_state", "show_stress", "show_capacity", "show_menu", "help",
    "ping", "show_behavior", "show_adaptive", "show_patterns",
    "show_reflection", "show_trends", "show_adaptation",
    "check_schedule", "show_registry", "query_schedule_date",
    "cognitive_checkin",
})

# Bot-private commands not yet supported on Web — honest needs_followup
_UNSUPPORTED_WEB_COMMANDS: frozenset[str] = frozenset({
    "cognitive_learning", "undo_last_action",
})

# ── Web action undo cache (in-process, session-only) ──────────────────────

_web_recent_actions: dict[str, dict] = {}
_MAX_WEB_ACTIONS = 50

def _reset_web_action_cache() -> None:
    _web_recent_actions.clear()

def _track_web_actions(
    produced: list[Event],
    command_type: str = "",
    user_id: str = "",
) -> list[dict]:
    """Extract undoable action info from produced events + command_type.

    Tracks finance_transaction/finance_income from produced events.
    Completion records are intentionally not tracked yet because current
    StateEngine can mark a revert but cannot remove the completion/memory rows.
    Returns list of {action_id, action_type} for response.
    """
    tracked: list[dict] = []

    for event in produced:
        if event.event_type == EventType.FINANCE_TRANSACTION_RECORDED:
            action_id = f"web-{uuid4().hex[:12]}"
            _web_recent_actions[action_id] = {
                "action_type": "finance_transaction",
                "params": {
                    "amount": event.payload.get("amount", 0),
                    "category": event.payload.get("category", "other"),
                },
                "user_id": user_id,
                "reverted": False,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            tracked.append({"action_id": action_id, "action_type": "finance_transaction"})

        elif event.event_type == EventType.FINANCE_INCOME_RECORDED:
            action_id = f"web-{uuid4().hex[:12]}"
            _web_recent_actions[action_id] = {
                "action_type": "finance_income",
                "params": {"amount": event.payload.get("amount", 0)},
                "user_id": user_id,
                "reverted": False,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            tracked.append({"action_id": action_id, "action_type": "finance_income"})

    if tracked:
        logger.info("tracked web actions: %s", tracked)

    # Cap cache
    if len(_web_recent_actions) > _MAX_WEB_ACTIONS:
        keys = sorted(
            _web_recent_actions.keys(),
            key=lambda k: _web_recent_actions[k].get("timestamp", ""),
        )
        for k in keys[: len(keys) - _MAX_WEB_ACTIONS]:
            _web_recent_actions.pop(k, None)

    return tracked


def _recent_web_actions_for_user(user_id: str, limit: int = 20) -> list[dict[str, Any]]:
    """Return recent Web actions for the authenticated user.

    This is an in-process action cache, used only to make Web undo discoverable.
    The actual undo still publishes canonical events through the pipeline.
    """
    rows: list[dict[str, Any]] = []
    for action_id, action in _web_recent_actions.items():
        action_user = str(action.get("user_id") or "")
        if action_user and action_user != str(user_id):
            continue

        action_type = str(action.get("action_type") or "operation")
        params = action.get("params", {}) if isinstance(action.get("params"), dict) else {}
        rows.append({
            "action_id": action_id,
            "action_type": action_type,
            "label": _web_action_label(action_type, params),
            "timestamp": action.get("timestamp", ""),
            "reverted": bool(action.get("reverted", False)),
            "can_undo": _web_action_can_undo(action_type, action),
            "params": params,
        })

    rows.sort(key=lambda item: item.get("timestamp", ""), reverse=True)
    return rows[: max(1, min(int(limit or 20), 50))]


def _web_action_can_undo(action_type: str, action: dict[str, Any]) -> bool:
    if action.get("reverted"):
        return False
    if action_type in {"finance_transaction", "finance_income"}:
        return True
    return False


def _web_action_label(action_type: str, params: dict[str, Any]) -> str:
    amount = params.get("amount")
    category = params.get("category")
    if action_type == "finance_transaction":
        suffix = f" · {category}" if category else ""
        return f"消费记录 ¥{amount or 0}{suffix}"
    if action_type == "finance_income":
        return f"收入记录 ¥{amount or 0}"
    if action_type == "completion_record":
        return "完成记录"
    if action_type == "verbal_scheduling":
        return "日历排期"
    return "Web 操作"


def _mock_like(value: Any) -> bool:
    return value.__class__.__module__.startswith("unittest.mock")


def _setting_bool(settings: Any, name: str, default: bool) -> bool:
    value = getattr(settings, name, default)
    if _mock_like(value):
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if value is None:
        return default
    return bool(value)


def _find_execution_result(events: list[Event]) -> Event | None:
    for event in reversed(events):
        if event.event_type in {EventType.EXECUTION_COMPLETED, EventType.EXECUTION_FAILED}:
            return event
    return None


def _execution_message(result: Event, title: str) -> tuple[bool, str]:
    if result.event_type == EventType.EXECUTION_COMPLETED:
        return True, f"日历操作成功：{title}"

    error = str(result.payload.get("error", "calendar_write_failed"))
    if "calendar_write_disabled" in error:
        return False, "日历写入未开启，提案已保留但没有操作 Google Calendar。"
    if "proposal_not_accepted" in error or "not accepted" in error:
        return False, "提案尚未被确认，已拒绝写入日历。"
    if "invalid_proposal_source" in error:
        return False, "提案来源不合法，已拒绝写入日历。"
    return False, f"日历操作失败：{error[:80]}"


def _exec_result_to_event(
    exec_result: dict[str, Any],
    proposal: Any,
    fallback_event_label: str = "CALENDAR_EVENT_CREATED",
) -> Event:
    """Convert a dict result from executor method to an Event."""
    ok = exec_result.get("ok", False)
    event_id = exec_result.get("event_id", "")
    error = exec_result.get("error", "")
    if ok:
        return Event(
            event_type=EventType.EXECUTION_COMPLETED,
            aggregate_id=proposal.proposal_id,
            aggregate_type=AggregateType.SYSTEM,
            payload={
                "proposal_id": proposal.proposal_id,
                "event_id": event_id,
                "title": proposal.action_payload.get("title", ""),
            },
        )
    return Event(
        event_type=EventType.EXECUTION_FAILED,
        aggregate_id=proposal.proposal_id,
        aggregate_type=AggregateType.SYSTEM,
        payload={
            "proposal_id": proposal.proposal_id,
            "error": error or f"calendar_execution_failed_{fallback_event_label}",
        },
    )


# ── Chinese time parsing for verbal scheduling on Web ──────────────────

_CN_DIGIT_MAP: dict[str, int] = {
    "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}


def _parse_cn_int(s: str) -> int | None:
    """Parse Chinese numeral string to int. Accepts '十二', '3', '十', etc."""
    if not s:
        return None
    if s.isdigit():
        return int(s)
    if "十" in s:
        parts = s.split("十")
        tens = _CN_DIGIT_MAP.get(parts[0], 1) if parts[0] else 1
        ones = _CN_DIGIT_MAP.get(parts[1], 0) if len(parts) > 1 and parts[1] else 0
        return tens * 10 + ones
    return _CN_DIGIT_MAP.get(s)


def _extract_time(text: str) -> tuple[int | None, int, str]:
    """Extract (hour, minute, remaining_text) from Chinese time text.

    Handles: '中午十二点', '下午三点半', '晚上7点', '早上八点', '中午' defaults.
    Returns (None, 0, text) when no time can be parsed.
    """
    remaining = text
    period = None
    for kw in ["中午", "下午", "晚上", "上午", "早上", "早晨", "午夜"]:
        if kw in text:
            period = kw
            remaining = remaining.replace(kw, "", 1)
            break

    m = re.search(
        r"(\d+|[\d一二两三四五六七八九十]+)\s*点\s*(\d+|[\d一二两三四五六七八九十]+|半)?\s*(分|分钟)?",
        remaining,
    )
    if m:
        hour = _parse_cn_int(m.group(1))
        minute_str = m.group(2)
        minute = 30 if minute_str == "半" else (_parse_cn_int(minute_str) or 0) if minute_str else 0

        if hour is not None and period in ("下午", "晚上") and hour < 12:
            hour += 12
        elif period in ("早上", "早晨") and hour == 12:
            hour = 0

        remaining = (remaining[: m.start()] + remaining[m.end():]).strip()
        return hour, minute, remaining

    # Period keyword without explicit hour
    if period == "中午":
        return 12, 0, remaining.strip()
    elif period == "午夜":
        return 0, 0, remaining.strip()

    return None, 0, text


def _clean_activity(text: str) -> str:
    """Remove date/time artifacts from activity text."""
    cleaned = text
    for kw in ["今天", "明天", "后天", "昨日", "下周", "下个月"]:
        cleaned = cleaned.replace(kw, "")
    cleaned = re.sub(r"\s+", "", cleaned)
    cleaned = re.sub(r"^(去|到|去办|要)", "", cleaned)
    return cleaned.strip()


def _try_parse_verbal_scheduling(text: str) -> dict | None:
    """Parse free text like '明天中午十二点吃饭' into proposal action_payload.

    Returns {title, start, end} ISO strings or None when unparseable.
    Uses the same _resolve_relative_date from the Telegram router.
    """
    from src.interface.telegram.router import _resolve_relative_date

    date_str = _resolve_relative_date(text)
    if not date_str:
        date_str = datetime.now(LOCAL_TZ).date().isoformat()

    hour, minute, remaining = _extract_time(text)
    if hour is None:
        return None

    title = _clean_activity(remaining) if remaining.strip() else None
    if not title:
        return None

    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=LOCAL_TZ)
    start_dt = dt.replace(hour=hour, minute=minute, second=0)
    end_dt = start_dt + timedelta(hours=1)

    return {
        "title": title,
        "start": start_dt.isoformat(),
        "end": end_dt.isoformat(),
    }


_SYNC_REFRESH_ACTIONS: tuple[str, ...] = (
    "check_homework",
    "schedule_daily_sync",
    "calendar_sync",
    "momo_vocab_sync",
)

_SYSTEM_SYNC_ACTIONS: dict[str, tuple[str, ...]] = {
    "sync_all": _SYNC_REFRESH_ACTIONS,
    "sync_homework": ("check_homework",),
    "sync_schedule": ("schedule_daily_sync",),
    "sync_calendar": ("calendar_sync",),
    "sync_vocab": ("momo_vocab_sync",),
}


def _web_user_id(request: Request) -> str:
    """Return default user id from settings or fallback."""
    settings = _settings(request)
    users = getattr(settings, "telegram_allowed_users", [])
    return str(users[0]) if users else "0"


def _end_of_local_day(now: datetime | None = None) -> datetime:
    current = now or datetime.now(LOCAL_TZ)
    return current.replace(hour=23, minute=59, second=59, microsecond=0)


def _start_of_next_local_day(now: datetime | None = None) -> datetime:
    current = now or datetime.now(LOCAL_TZ)
    tomorrow = current.date() + timedelta(days=1)
    return datetime.combine(tomorrow, datetime.min.time(), tzinfo=LOCAL_TZ)


def _web_event_metadata(user_id: str, trace_id: str) -> dict[str, str]:
    return {"source": "web_ui", "user_id": user_id, "trace_id": trace_id}


def _system_action_label(action: str) -> str:
    labels = {
        "sync_all": "已请求刷新全部数据",
        "sync_homework": "已请求同步作业",
        "sync_schedule": "已请求同步课表",
        "sync_calendar": "已请求同步日历",
        "sync_vocab": "已请求同步背词",
        "calendar_review": "已请求日历一致性审查",
        "calendar_repair": "已请求日历一致性修正",
    }
    return labels.get(action, "已请求系统操作")


def _direct_quick_events(
    action_id: str,
    user_id: str,
    payload: dict[str, Any],
    trace_id: str,
) -> list[Event]:
    """Map Web-only quick actions to real runtime events.

    These actions used to be Telegram handler shortcuts. Web cannot call those
    private branches and pretend success, so publish the canonical event types.
    """
    metadata = _web_event_metadata(user_id, trace_id)

    if action_id in {"hydration_250", "hydration_500"}:
        amount_ml = 250 if action_id == "hydration_250" else 500
        return [Event(
            event_type=EventType.HYDRATION_LOGGED,
            aggregate_id=user_id,
            aggregate_type=AggregateType.USER,
            payload={"amount_ml": amount_ml, "source": "web_ui"},
            metadata=metadata,
        )]

    if action_id == "bad_state":
        return [Event(
            event_type=EventType.SUBJECTIVE_CONTEXT_ADDED,
            aggregate_id=user_id,
            aggregate_type=AggregateType.USER,
            payload={
                "kind": "context",
                "text": payload.get("text") or "今天状态差",
                "expires_at": _end_of_local_day().isoformat(),
                "source": "web_ui",
            },
            metadata=metadata,
        )]

    if action_id == "school_leave_today":
        day = payload.get("date") or datetime.now(LOCAL_TZ).date().isoformat()
        return [Event(
            event_type=EventType.SUBJECTIVE_CONTEXT_ADDED,
            aggregate_id=user_id,
            aggregate_type=AggregateType.USER,
            payload={
                "kind": "school_leave",
                "text": payload.get("text") or "今日请假",
                "date": day,
                "expires_at": _start_of_next_local_day().isoformat(),
                "source": "web_ui",
            },
            metadata=metadata,
        )]

    if action_id in {"complete_art_30", "complete_art_60"}:
        minutes = 30 if action_id == "complete_art_30" else 60
        return [Event(
            event_type=EventType.ART_PROGRESS_RECORDED,
            aggregate_id="art_today",
            aggregate_type=AggregateType.ART,
            payload={
                "completed_minutes": minutes,
                "type": payload.get("type") or "练习",
                "sessions": payload.get("sessions", 1),
                "note": payload.get("note", ""),
                "resistance": False,
                "source": "web_ui",
            },
            metadata=metadata,
        )]

    if action_id == "sync_refresh":
        return [
            Event(
                event_type=EventType.SYSTEM_SCHEDULED_TRIGGER,
                aggregate_id=f"web_{scheduled_action}",
                aggregate_type=AggregateType.SYSTEM,
                payload={"action": scheduled_action},
                metadata=metadata,
            )
            for scheduled_action in _SYNC_REFRESH_ACTIONS
        ]

    if action_id == "complete_homework":
        task_text = payload.get("text") or payload.get("title") or "作业"
        task_id = payload.get("task_id") or payload.get("homework_id") or task_text
        completed = Event(
            event_type=EventType.PLANNING_TASK_COMPLETED,
            aggregate_id=user_id,
            aggregate_type=AggregateType.USER,
            payload={
                "task_id": task_id,
                "task": task_text,
                "text": task_text,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "source": "web_ui",
                "note": payload.get("note", ""),
            },
            metadata=metadata,
        )
        memory = Event(
            event_type=EventType.MEMORY_ENTRY_CREATED,
            aggregate_id=user_id,
            aggregate_type=AggregateType.USER,
            causation_id=completed.event_id,
            payload={
                "content": f"完成：{task_text}",
                "tags": ["completion", "daily_log"],
                "source": "web_ui_completion",
            },
            metadata=metadata,
        )
        return [completed, memory]

    if action_id == "skip_homework":
        task_text = payload.get("text") or payload.get("title") or "作业"
        task_id = payload.get("task_id") or payload.get("homework_id") or task_text
        return [Event(
            event_type=EventType.PLANNING_RECOMMENDATION_SKIPPED,
            aggregate_id=user_id,
            aggregate_type=AggregateType.USER,
            payload={
                "task_id": task_id,
                "task": task_text,
                "text": task_text,
                "skipped_at": datetime.now(timezone.utc).isoformat(),
                "source": "web_ui",
                "note": payload.get("note", ""),
            },
            metadata=metadata,
        )]

    if action_id == "delay_homework_30":
        task_text = payload.get("text") or payload.get("title") or "作业"
        task_id = payload.get("task_id") or payload.get("homework_id") or task_text
        delay_minutes = int(payload.get("delay_minutes") or 30)
        return [Event(
            event_type=EventType.PLANNING_RECOMMENDATION_DELAYED,
            aggregate_id=user_id,
            aggregate_type=AggregateType.USER,
            payload={
                "task_id": task_id,
                "task": task_text,
                "text": task_text,
                "delay_minutes": delay_minutes,
                "delayed_until": (datetime.now(timezone.utc) + timedelta(minutes=delay_minutes)).isoformat(),
                "delayed_at": datetime.now(timezone.utc).isoformat(),
                "source": "web_ui",
                "note": payload.get("note", ""),
            },
            metadata=metadata,
        )]

    return []


def _completion_events(
    user_id: str,
    raw_text: str,
    payload: dict[str, Any],
    trace_id: str,
) -> list[Event]:
    task_text = str(payload.get("text") or payload.get("task") or raw_text).strip()
    if not task_text:
        task_text = "未命名完成事项"
    metadata = _web_event_metadata(user_id, trace_id)
    completed = Event(
        event_type=EventType.PLANNING_TASK_COMPLETED,
        aggregate_id=user_id,
        aggregate_type=AggregateType.USER,
        payload={
            "task_id": task_text,
            "task": task_text,
            "text": task_text,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "source": "web_ui_completion",
            "note": payload.get("note", ""),
        },
        metadata=metadata,
    )
    memory = Event(
        event_type=EventType.MEMORY_ENTRY_CREATED,
        aggregate_id=user_id,
        aggregate_type=AggregateType.USER,
        causation_id=completed.event_id,
        payload={
            "content": f"完成：{task_text}",
            "tags": ["completion", "daily_log"],
            "source": "web_ui_completion",
        },
        metadata=metadata,
    )
    return [completed, memory]


# ── Tasks Action endpoint ──────────────────────────────────────────────────────


def _tasks_action_events(
    action: str,
    items: list[dict[str, Any]],
    user_id: str,
    trace_id: str,
) -> list[Event]:
    """Generate canonical events for tasks actions (complete/skip/delay_30).

    Reuses the same EventType/payload structure as ``_direct_quick_events``
    so downstream handlers process them identically.
    """
    metadata = {"source": "web_ui", "user_id": user_id, "trace_id": trace_id}
    events: list[Event] = []

    for item in items:
        task_id = str(item.get("id") or item.get("title") or "作业")
        title = str(item.get("title") or "作业")

        if action == "complete":
            completed = Event(
                event_type=EventType.PLANNING_TASK_COMPLETED,
                aggregate_id=user_id,
                aggregate_type=AggregateType.USER,
                payload={
                    "task_id": task_id,
                    "task": title,
                    "text": title,
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                    "source": "web_ui",
                    "note": item.get("note", ""),
                },
                metadata=metadata,
            )
            memory = Event(
                event_type=EventType.MEMORY_ENTRY_CREATED,
                aggregate_id=user_id,
                aggregate_type=AggregateType.USER,
                causation_id=completed.event_id,
                payload={
                    "content": f"完成：{title}",
                    "tags": ["completion", "daily_log"],
                    "source": "web_ui_completion",
                },
                metadata=metadata,
            )
            events.extend([completed, memory])

        elif action == "skip":
            events.append(Event(
                event_type=EventType.PLANNING_RECOMMENDATION_SKIPPED,
                aggregate_id=user_id,
                aggregate_type=AggregateType.USER,
                payload={
                    "task_id": task_id,
                    "task": title,
                    "text": title,
                    "skipped_at": datetime.now(timezone.utc).isoformat(),
                    "source": "web_ui",
                    "note": item.get("note", ""),
                },
                metadata=metadata,
            ))

        elif action == "delay_30":
            delay_minutes = int(item.get("delay_minutes") or 30)
            events.append(Event(
                event_type=EventType.PLANNING_RECOMMENDATION_DELAYED,
                aggregate_id=user_id,
                aggregate_type=AggregateType.USER,
                payload={
                    "task_id": task_id,
                    "task": title,
                    "text": title,
                    "delay_minutes": delay_minutes,
                    "delayed_until": (
                        datetime.now(timezone.utc) + timedelta(minutes=delay_minutes)
                    ).isoformat(),
                    "delayed_at": datetime.now(timezone.utc).isoformat(),
                    "source": "web_ui",
                    "note": item.get("note", ""),
                },
                metadata=metadata,
            ))

    return events


@router.post("/api/web/tasks/action")
async def web_tasks_action(request: Request, body: dict):
    """Execute structured tasks actions (complete/skip/delay/calendar_proposal).

    Request:
      batch complete: { "action":"complete",  "items":[{id,title,course},...] }
      batch skip:     { "action":"skip",      "items":[{id,title,course},...] }
      batch delay:    { "action":"delay_30",  "items":[{id,title,course},...] }
      calendar:       { "action":"calendar_proposal", "items":[{id,title,..}],
                        "date":"2026-06-10","start_time":"14:00","end_time":"15:00",
                        "location":"图书馆","note":"..." }

    Response: { ok, message, action, events, item_count?, dashboard, action_id?,
                proposal? }
    """
    _require_session(request)

    action = str(body.get("action", "")).strip()
    items = body.get("items", [])

    valid_actions = {"complete", "skip", "delay_30", "calendar_proposal"}
    if action not in valid_actions:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "message": f"action 必须是 {', '.join(valid_actions)} 之一"},
        )

    user_id = _web_user_id(request)
    trace_id = str(uuid4())

    pipeline: Pipeline | None = getattr(request.app.state, "pipeline", None)
    if pipeline is None:
        return JSONResponse(
            status_code=503,
            content={"ok": False, "message": "pipeline not available"},
        )

    # ── Calendar proposal ────────────────────────────────────────────────
    if action == "calendar_proposal":
        if not isinstance(items, list) or len(items) == 0:
            return JSONResponse(
                status_code=400,
                content={"ok": False, "message": "items 需要包含一个作业项"},
            )
        item = items[0] if isinstance(items[0], dict) else {}
        title = str(item.get("title") or "作业")
        course = str(item.get("course") or "")
        date_str = str(body.get("date", "")).strip()
        start_time = str(body.get("start_time", "")).strip()
        end_time = str(body.get("end_time", "")).strip()
        location = str(body.get("location", "")).strip()
        note = str(body.get("note", "")).strip()

        if not date_str or not start_time:
            return JSONResponse(
                status_code=400,
                content={"ok": False, "message": "date 和 start_time 是必填的"},
            )

        try:
            date.fromisoformat(date_str)
        except ValueError:
            return JSONResponse(
                status_code=400,
                content={"ok": False, "message": f"无效的日期: {date_str}"},
            )

        try:
            start_dt = datetime.fromisoformat(
                f"{date_str}T{start_time}:00"
            ).replace(tzinfo=LOCAL_TZ)
            if end_time:
                end_dt = datetime.fromisoformat(
                    f"{date_str}T{end_time}:00"
                ).replace(tzinfo=LOCAL_TZ)
            else:
                end_dt = start_dt + timedelta(hours=1)
        except ValueError:
            return JSONResponse(
                status_code=400,
                content={"ok": False, "message": "无效的时间格式，请使用 HH:MM"},
            )
        if end_dt <= start_dt:
            return JSONResponse(
                status_code=400,
                content={"ok": False, "message": "end_time 必须晚于 start_time"},
            )

        cal_title = f"作业：{title}"
        if course:
            cal_title = f"{course} - {title}"

        action_payload = {
            "title": cal_title,
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
            "location": location,
            "description": note or f"作业时间块：{title}",
            "calendar_id": "primary",
            "source": "web_ui_tasks_calendar",
        }

        from src.core.proposal import Proposal, ProposalType, TargetSystem
        proposal = Proposal(
            proposal_type=ProposalType.CREATE_CALENDAR_BLOCK,
            target_system=TargetSystem.GOOGLE_CALENDAR,
            action_payload=action_payload,
            reason=f"作业日历安排：{title}",
            confidence=0.8,
            user_id=user_id,
        )

        proposal_event = Event(
            event_type=EventType.EXECUTION_PROPOSAL_CREATED,
            aggregate_id=proposal.proposal_id,
            aggregate_type=AggregateType.SYSTEM,
            payload=proposal.to_dict(),
            metadata=_web_event_metadata(user_id, trace_id),
        )

        try:
            produced = await pipeline.run(proposal_event)
        except Exception as exc:
            logger.exception("tasks calendar proposal failed")
            return {
                "ok": False,
                "message": f"日历提案创建异常: {str(exc)[:80]}",
                "action": action,
                "events": 0,
            }

        engine: StateEngine | None = getattr(request.app.state, "state_engine", None)
        settings_obj = _settings(request)
        dashboard = build_dashboard(engine, settings_obj) if engine else None

        return {
            "ok": True,
            "message": f"已创建日历提案：{cal_title}",
            "action": action,
            "events": len(produced),
            "item_count": 1,
            "proposal": proposal.to_dict(),
            "dashboard": dashboard,
        }

    # ── Batch: complete / skip / delay_30 ─────────────────────────────────
    if not isinstance(items, list) or len(items) == 0:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "message": "items 数组是必填的"},
        )

    events = _tasks_action_events(action, items, user_id, trace_id)
    if not events:
        engine: StateEngine | None = getattr(request.app.state, "state_engine", None)
        settings_obj = _settings(request)
        dashboard = build_dashboard(engine, settings_obj) if engine else None
        return {
            "ok": True,
            "message": "没有需要处理的事项",
            "action": action,
            "events": 0,
            "item_count": 0,
            "dashboard": dashboard,
        }

    produced_total = 0
    try:
        for event in events:
            produced = await pipeline.run(event)
            produced_total += 1 + len(produced)
    except Exception as exc:
        logger.exception("tasks action pipeline failed for %s", action)
        return {
            "ok": False,
            "message": f"操作异常: {str(exc)[:80]}",
            "action": action,
            "events": 0,
            "item_count": len(items),
        }

    tracked_actions = _track_web_actions(events, f"tasks_{action}", user_id)
    engine: StateEngine | None = getattr(request.app.state, "state_engine", None)
    settings_obj = _settings(request)
    dashboard = build_dashboard(engine, settings_obj) if engine else None

    resp: dict[str, Any] = {
        "ok": True,
        "message": f"已处理 {len(items)} 项作业",
        "action": action,
        "events": produced_total,
        "item_count": len(items),
        "dashboard": dashboard,
    }
    if tracked_actions:
        resp["action_id"] = tracked_actions[0]["action_id"]
    return resp


# ── Today Action endpoint ─────────────────────────────────────────────────


def _today_action_label(action: str) -> str:
    labels = {
        "art_progress": "已记录画画进度",
        "hydration": "已记录补水量",
        "completion": "已记录完成事项",
        "context": "已记录今日状态",
        "school_leave_today": "已记录请假",
        "sync_refresh": "同步刷新中",
    }
    return labels.get(action, action)


@router.post("/api/web/today/action")
async def web_today_action(request: Request, body: dict):
    """Execute structured today actions through canonical events.

    Each action publishes the same EventTypes used by the Telegram/bot side
    so downstream handlers (hydration logger, art tracker, memory log, etc.)
    process them identically. No direct StateEngine mutation.

    Request:
      art_progress:       { "action":"art_progress", "minutes":30, "type":"练习", "note":"..." }
      hydration:          { "action":"hydration", "amount_ml":250 }
      completion:         { "action":"completion", "text":"完成了作业", "note":"..." }
      context:            { "action":"context", "text":"今天状态差", "kind":"context" }
      school_leave_today: { "action":"school_leave_today", "text":"请假", "date":"2026-06-05" }
      sync_refresh:       { "action":"sync_refresh" }
    """
    _require_session(request)

    action = str(body.get("action", "")).strip()
    valid_actions = {"art_progress", "hydration", "completion", "context", "school_leave_today", "sync_refresh"}
    if action not in valid_actions:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "message": f"action 必须是 {', '.join(sorted(valid_actions))} 之一"},
        )

    pipeline: Pipeline | None = getattr(request.app.state, "pipeline", None)
    if pipeline is None:
        return JSONResponse(status_code=503, content={"ok": False, "message": "pipeline not available"})

    user_id = _web_user_id(request)
    trace_id = str(uuid4())
    metadata = _web_event_metadata(user_id, trace_id)
    events_to_publish: list[Event] = []

    try:
        if action == "art_progress":
            minutes_raw = body.get("minutes")
            if minutes_raw is None:
                return JSONResponse(status_code=400, content={"ok": False, "message": "minutes is required"})
            minutes = int(minutes_raw)
            if minutes <= 0:
                return JSONResponse(status_code=400, content={"ok": False, "message": "minutes must be > 0"})
            events_to_publish.append(Event(
                event_type=EventType.ART_PROGRESS_RECORDED,
                aggregate_id="art_today",
                aggregate_type=AggregateType.ART,
                payload={
                    "completed_minutes": minutes,
                    "type": str(body.get("type", "练习")) or "练习",
                    "note": str(body.get("note", "")),
                    "sessions": int(body.get("sessions", 1)),
                    "resistance": False,
                    "source": "web_ui_today",
                },
                metadata=metadata,
            ))

        elif action == "hydration":
            amount_raw = body.get("amount_ml")
            if amount_raw is None:
                return JSONResponse(status_code=400, content={"ok": False, "message": "amount_ml is required"})
            amount_ml = int(amount_raw)
            if amount_ml <= 0:
                return JSONResponse(status_code=400, content={"ok": False, "message": "amount_ml must be > 0"})
            events_to_publish.append(Event(
                event_type=EventType.HYDRATION_LOGGED,
                aggregate_id=user_id,
                aggregate_type=AggregateType.USER,
                payload={"amount_ml": amount_ml, "source": "web_ui_today"},
                metadata=metadata,
            ))

        elif action == "completion":
            text = str(body.get("text", "")).strip()
            if not text:
                return JSONResponse(status_code=400, content={"ok": False, "message": "text is required"})
            completed = Event(
                event_type=EventType.PLANNING_TASK_COMPLETED,
                aggregate_id=user_id,
                aggregate_type=AggregateType.USER,
                payload={
                    "task_id": text,
                    "task": text,
                    "text": text,
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                    "source": "web_ui_today",
                    "note": str(body.get("note", "")),
                },
                metadata=metadata,
            )
            memory = Event(
                event_type=EventType.MEMORY_ENTRY_CREATED,
                aggregate_id=user_id,
                aggregate_type=AggregateType.USER,
                causation_id=completed.event_id,
                payload={
                    "content": f"完成：{text}",
                    "tags": ["completion", "daily_log"],
                    "source": "web_ui_today_completion",
                },
                metadata=metadata,
            )
            events_to_publish.extend([completed, memory])

        elif action == "context":
            text = str(body.get("text", "")).strip()
            if not text:
                return JSONResponse(status_code=400, content={"ok": False, "message": "text is required"})
            kind = str(body.get("kind", "context")).strip() or "context"
            events_to_publish.append(Event(
                event_type=EventType.SUBJECTIVE_CONTEXT_ADDED,
                aggregate_id=user_id,
                aggregate_type=AggregateType.USER,
                payload={
                    "kind": kind,
                    "text": text,
                    "expires_at": _end_of_local_day().isoformat(),
                    "source": "web_ui_today",
                },
                metadata=metadata,
            ))

        elif action == "school_leave_today":
            day = str(body.get("date") or datetime.now(LOCAL_TZ).date().isoformat())
            try:
                date.fromisoformat(day)
            except ValueError:
                return JSONResponse(
                    status_code=400,
                    content={"ok": False, "message": f"无效的日期: {day}"},
                )
            events_to_publish.append(Event(
                event_type=EventType.SUBJECTIVE_CONTEXT_ADDED,
                aggregate_id=user_id,
                aggregate_type=AggregateType.USER,
                payload={
                    "kind": "school_leave",
                    "text": str(body.get("text", "今日请假")),
                    "date": day,
                    "expires_at": _start_of_next_local_day().isoformat(),
                    "source": "web_ui_today",
                },
                metadata=metadata,
            ))

        elif action == "sync_refresh":
            for scheduled_action in _SYNC_REFRESH_ACTIONS:
                events_to_publish.append(Event(
                    event_type=EventType.SYSTEM_SCHEDULED_TRIGGER,
                    aggregate_id=f"web_{scheduled_action}",
                    aggregate_type=AggregateType.SYSTEM,
                    payload={"action": scheduled_action},
                    metadata=metadata,
                ))

    except (ValueError, TypeError) as exc:
        return JSONResponse(status_code=400, content={"ok": False, "message": f"参数格式错误: {str(exc)[:80]}"})

    if not events_to_publish:
        return JSONResponse(status_code=400, content={"ok": False, "message": "无需处理的事件"})

    produced_total = 0
    all_produced: list[Event] = []
    try:
        for event in events_to_publish:
            produced = await pipeline.run(event)
            all_produced.extend(produced)
            produced_total += 1 + len(produced)
    except Exception as exc:
        logger.exception("today action pipeline failed: %s", action)
        return {"ok": False, "message": f"操作异常: {str(exc)[:80]}"}

    tracked = _track_web_actions(all_produced + events_to_publish, f"today_{action}", user_id)

    engine: StateEngine | None = getattr(request.app.state, "state_engine", None)
    settings_obj = _settings(request)
    dashboard = build_dashboard(engine, settings_obj) if engine else None

    return {
        "ok": True,
        "message": _today_action_label(action),
        "action": action,
        "events": produced_total,
        "dashboard": dashboard,
        "action_id": tracked[0]["action_id"] if tracked else None,
    }


def _review_int(value: Any, name: str) -> int | None:
    if value in (None, ""):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be integer")
    if parsed < 1 or parsed > 10:
        raise ValueError(f"{name} must be 1..10")
    return parsed


def _review_text(body: dict, key: str) -> str:
    return str(body.get(key, "") or "").strip()


def _review_summary_lines(
    mood_score: int | None,
    energy_score: int | None,
    pressure_score: int | None,
    body_state: str,
    completed: str,
    deviation: str,
    tomorrow: str,
    note: str,
) -> list[str]:
    lines: list[str] = []
    score_parts: list[str] = []
    if mood_score is not None:
        score_parts.append(f"心情 {mood_score}/10")
    if energy_score is not None:
        score_parts.append(f"精力 {energy_score}/10")
    if pressure_score is not None:
        score_parts.append(f"压力 {pressure_score}/10")
    if score_parts:
        lines.append("、".join(score_parts))
    if body_state:
        lines.append(f"身体：{body_state}")
    if completed:
        lines.append(f"完成：{completed}")
    if deviation:
        lines.append(f"偏离：{deviation}")
    if tomorrow:
        lines.append(f"明日：{tomorrow}")
    if note:
        lines.append(f"备注：{note}")
    return lines


@router.post("/api/web/review/action")
async def web_review_action(request: Request, body: dict):
    """Record a structured daily review through canonical events.

    Request fields are optional, but at least one meaningful field is required:
    mood_score, energy_score, pressure_score, body_state, completed,
    deviation, tomorrow, note.
    """
    _require_session(request)

    pipeline: Pipeline | None = getattr(request.app.state, "pipeline", None)
    if pipeline is None:
        return JSONResponse(status_code=503, content={"ok": False, "message": "pipeline not available"})

    try:
        mood_score = _review_int(body.get("mood_score"), "mood_score")
        energy_score = _review_int(body.get("energy_score"), "energy_score")
        pressure_score = _review_int(body.get("pressure_score"), "pressure_score")
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"ok": False, "message": str(exc)})

    body_state = _review_text(body, "body_state")
    completed = _review_text(body, "completed")
    deviation = _review_text(body, "deviation")
    tomorrow = _review_text(body, "tomorrow")
    note = _review_text(body, "note")

    lines = _review_summary_lines(
        mood_score, energy_score, pressure_score,
        body_state, completed, deviation, tomorrow, note,
    )
    if not lines:
        return JSONResponse(status_code=400, content={"ok": False, "message": "至少填写一项复盘内容"})

    user_id = _web_user_id(request)
    trace_id = str(uuid4())
    metadata = _web_event_metadata(user_id, trace_id)
    events_to_publish: list[Event] = []

    if mood_score is not None:
        events_to_publish.append(Event(
            event_type=EventType.MOOD_RECORDED,
            aggregate_id=user_id,
            aggregate_type=AggregateType.USER,
            payload={"score": mood_score, "source": "web_ui_review"},
            metadata=metadata,
        ))

    context_payload: dict[str, Any] = {
        "kind": "daily_review",
        "text": "；".join(lines),
        "fields": {
            "mood_score": mood_score,
            "energy_score": energy_score,
            "pressure_score": pressure_score,
            "body_state": body_state,
            "completed": completed,
            "deviation": deviation,
            "tomorrow": tomorrow,
            "note": note,
        },
        "expires_at": _start_of_next_local_day().isoformat(),
        "source": "web_ui_review",
    }
    events_to_publish.append(Event(
        event_type=EventType.SUBJECTIVE_CONTEXT_ADDED,
        aggregate_id=user_id,
        aggregate_type=AggregateType.USER,
        payload=context_payload,
        metadata=metadata,
    ))

    memory_content = "复盘：" + "；".join(lines)
    events_to_publish.append(Event(
        event_type=EventType.MEMORY_ENTRY_CREATED,
        aggregate_id=user_id,
        aggregate_type=AggregateType.USER,
        payload={
            "content": memory_content,
            "tags": ["daily_log", "daily_review"],
            "source": "web_ui_review",
        },
        metadata=metadata,
    ))

    produced_total = 0
    all_produced: list[Event] = []
    try:
        for event in events_to_publish:
            produced = await pipeline.run(event)
            all_produced.extend(produced)
            produced_total += 1 + len(produced)
    except Exception as exc:
        logger.exception("review action pipeline failed")
        return {"ok": False, "message": f"复盘记录异常: {str(exc)[:80]}"}

    engine: StateEngine | None = getattr(request.app.state, "state_engine", None)
    settings_obj = _settings(request)
    dashboard = build_dashboard(engine, settings_obj) if engine else None

    return {
        "ok": True,
        "message": "已记录今日复盘",
        "events": produced_total,
        "dashboard": dashboard,
    }


@router.post("/api/web/system/action")
async def web_system_action(request: Request, body: dict):
    """Execute system maintenance actions through the runtime event pipeline.

    Supported actions:
      sync_all / sync_homework / sync_schedule / sync_calendar / sync_vocab
      calendar_review / calendar_repair
    """
    _require_session(request)

    action = str(body.get("action", "")).strip()
    valid_actions = set(_SYSTEM_SYNC_ACTIONS) | {"calendar_review", "calendar_repair"}
    if action not in valid_actions:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "message": f"action 必须是 {', '.join(sorted(valid_actions))} 之一"},
        )

    pipeline: Pipeline | None = getattr(request.app.state, "pipeline", None)
    if pipeline is None:
        return JSONResponse(status_code=503, content={"ok": False, "message": "pipeline not available"})

    user_id = _web_user_id(request)
    trace_id = str(uuid4())
    metadata = _web_event_metadata(user_id, trace_id)
    events_to_publish: list[Event] = []

    if action in _SYSTEM_SYNC_ACTIONS:
        for scheduled_action in _SYSTEM_SYNC_ACTIONS[action]:
            events_to_publish.append(Event(
                event_type=EventType.SYSTEM_SCHEDULED_TRIGGER,
                aggregate_id=f"web_{scheduled_action}",
                aggregate_type=AggregateType.SYSTEM,
                payload={
                    "action": scheduled_action,
                    "source": "web_ui_system",
                    "requested_by": user_id,
                },
                metadata=metadata,
            ))
    elif action == "calendar_review":
        events_to_publish.append(Event(
            event_type=EventType.CALENDAR_CONSISTENCY_REVIEW_REQUESTED,
            aggregate_id="system",
            aggregate_type=AggregateType.SYSTEM,
            payload={"source": "web_ui_system", "requested_by": user_id},
            metadata=metadata,
        ))
    elif action == "calendar_repair":
        events_to_publish.append(Event(
            event_type=EventType.CALENDAR_CONSISTENCY_REPAIR_REQUESTED,
            aggregate_id="system",
            aggregate_type=AggregateType.SYSTEM,
            payload={"source": "web_ui_system", "requested_by": user_id},
            metadata=metadata,
        ))

    produced_total = 0
    all_produced: list[Event] = []
    try:
        for event in events_to_publish:
            produced = await pipeline.run(event)
            all_produced.extend(produced)
            produced_total += 1 + len(produced)
    except Exception as exc:
        logger.exception("system action pipeline failed: %s", action)
        return {"ok": False, "message": f"系统操作异常: {str(exc)[:80]}"}

    engine: StateEngine | None = getattr(request.app.state, "state_engine", None)
    settings_obj = _settings(request)
    dashboard = build_dashboard(engine, settings_obj) if engine else None

    return {
        "ok": True,
        "message": _system_action_label(action),
        "action": action,
        "events": produced_total,
        "dashboard": dashboard,
    }


@router.post("/api/web/actions")
async def web_actions(request: Request, body: dict):
    """Execute a web-originated action through the action gateway.

    Request:  { "text": "...", "action": "optional_action_id", "payload": {...} }
    Response: { "ok": bool, "message": str, "command_type": str,
                "events": int, "needs_followup": bool, "dashboard": {...}? }
    """
    _require_session(request)

    text = (body.get("text") or "").strip()
    action_id = body.get("action") or ""
    payload = body.get("payload") or {}

    if not text and not action_id:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "message": "请提供指令文字或快捷动作", "command_type": "", "events": 0, "needs_followup": False},
        )

    # ── Resolve command type and raw_text ────────────────────────────
    command_type: str = ""
    raw_text: str = text

    if action_id and action_id in _QUICK_ACTIONS:
        mapping = _QUICK_ACTIONS[action_id]
        command_type = mapping["command_type"]
        if not raw_text:
            raw_text = mapping["text"]
    elif text:
        # Try parse_message for free-text input
        try:
            from src.interface.telegram.router import parse_message
            cmd = parse_message(text, 0)  # user_id=0, replaced below
            if cmd is not None:
                command_type = cmd.command_type
                raw_text = cmd.params.get("raw_text", text)
        except Exception:
            logger.warning("parse_message failed for text: %s", text)

    if not command_type:
        # Fallback: unrecognized input
        return {
            "ok": False,
            "message": f"无法识别指令：{raw_text[:50]}",
            "command_type": "",
            "events": 0,
            "needs_followup": False,
        }

    user_id = _web_user_id(request)
    trace_id = str(uuid4())

    # ── Verbal scheduling: proposal-first (not direct write) ──────────
    if command_type == "verbal_scheduling":
        parsed = _try_parse_verbal_scheduling(raw_text)
        if parsed is None:
            return {
                "ok": False,
                "needs_followup": True,
                "message": "无法从输入中解析出明确的时间。请用更明确的格式，例如：明天中午十二点吃饭",
                "command_type": command_type,
                "events": 0,
            }

        pl: Pipeline | None = getattr(request.app.state, "pipeline", None)
        if pl is None:
            logger.warning("pipeline not available, cannot create proposal")
            return {
                "ok": False,
                "needs_followup": True,
                "message": "系统管道不可用，请稍后重试。",
                "command_type": command_type,
                "events": 0,
            }

        from src.core.proposal import Proposal, ProposalType, TargetSystem
        proposal = Proposal(
            proposal_type=ProposalType.CREATE_CALENDAR_BLOCK,
            target_system=TargetSystem.GOOGLE_CALENDAR,
            action_payload={
                **parsed,
                "description": "Web 口述排期",
                "calendar_id": "primary",
                "source": "web_ui_verbal_scheduling",
            },
            reason="Web 口述排期",
            confidence=0.7,
            user_id=user_id,
        )

        proposal_event = Event(
            event_type=EventType.EXECUTION_PROPOSAL_CREATED,
            aggregate_id=proposal.proposal_id,
            aggregate_type=AggregateType.SYSTEM,
            payload=proposal.to_dict(),
            metadata=_web_event_metadata(user_id, trace_id),
        )

        try:
            produced = await pl.run(proposal_event)
        except Exception as e:
            logger.exception("verbal scheduling proposal failed")
            return {
                "ok": False,
                "message": f"排期提案创建异常: {str(e)[:80]}",
                "command_type": command_type,
                "events": 0,
                "needs_followup": True,
            }

        engine: StateEngine | None = getattr(request.app.state, "state_engine", None)
        settings_obj = _settings(request)
        dashboard = build_dashboard(engine, settings_obj) if engine else None

        return {
            "ok": True,
            "message": f"已创建排期提案：{parsed['title']}（{parsed['start']}）",
            "command_type": command_type,
            "events": len(produced),
            "needs_followup": False,
            "proposal": proposal.to_dict(),
            "dashboard": dashboard,
        }

    # ── Check unsupported bot-private commands ───────────────────────
    if command_type in _UNSUPPORTED_WEB_COMMANDS:
        msg_by_type = {
            "cognitive_learning": "认知学习需调用 DeepSeek/API，当前仅限 Telegram 私有路径。",
        }
        msg = msg_by_type.get(command_type, "Web 版执行器还没接上，请通过 Telegram 操作。")
        return {
            "ok": False,
            "needs_followup": True,
            "message": msg,
            "command_type": command_type,
            "events": 0,
        }

    # ── Read-only: return dashboard without pipeline mutation ────────
    if command_type in _READ_ONLY_COMMANDS:
        engine: StateEngine | None = getattr(request.app.state, "state_engine", None)
        settings = _settings(request)
        dashboard = build_dashboard(engine, settings)
        return {
            "ok": True,
            "message": f"已获取{raw_text[:30]}数据",
            "command_type": command_type,
            "events": 0,
            "needs_followup": False,
            "dashboard": dashboard,
        }

    # ── Mutating action: publish through pipeline ────────────────────
    pipeline: Pipeline | None = getattr(request.app.state, "pipeline", None)
    if pipeline is None:
        return JSONResponse(
            status_code=503,
            content={
                "ok": False, "message": "动作引擎未就绪（pipeline 不可用）",
                "command_type": command_type, "events": 0, "needs_followup": True,
            },
        )

    direct_events = _direct_quick_events(action_id, user_id, payload, trace_id) if action_id else []
    if direct_events:
        produced_total = 0
        all_produced: list[Event] = []
        try:
            for direct_event in direct_events:
                produced = await pipeline.run(direct_event)
                all_produced.extend(produced)
                produced_total += 1 + len(produced)
        except Exception as e:
            logger.exception("direct action pipeline.run failed for %s", command_type)
            return {
                "ok": False,
                "message": f"动作执行异常: {str(e)[:80]}",
                "command_type": command_type,
                "events": 0,
                "needs_followup": True,
            }

        tracked_actions = _track_web_actions(all_produced, command_type, user_id)
        engine: StateEngine | None = getattr(request.app.state, "state_engine", None)
        settings = _settings(request)
        dashboard = build_dashboard(engine, settings) if engine else None

        resp: dict[str, Any] = {
            "ok": True,
            "message": f"已执行：{raw_text[:40]}",
            "command_type": command_type,
            "events": produced_total,
            "needs_followup": False,
            "dashboard": dashboard,
        }
        if tracked_actions:
            resp["action_id"] = tracked_actions[0]["action_id"]
            resp["action_type"] = tracked_actions[0]["action_type"]
        return resp

    if command_type in {"generic_completion", "completion_record"}:
        completion_events = _completion_events(user_id, raw_text, payload, trace_id)
        produced_total = 0
        try:
            for completion_event in completion_events:
                produced = await pipeline.run(completion_event)
                produced_total += 1 + len(produced)
        except Exception as e:
            logger.exception("completion action pipeline.run failed for %s", command_type)
            return {
                "ok": False,
                "message": f"完成记录异常: {str(e)[:80]}",
                "command_type": command_type,
                "events": 0,
                "needs_followup": True,
            }

        engine: StateEngine | None = getattr(request.app.state, "state_engine", None)
        settings = _settings(request)
        dashboard = build_dashboard(engine, settings) if engine else None

        tracked_actions = _track_web_actions([], command_type, user_id)
        resp: dict[str, Any] = {
            "ok": True,
            "message": f"已记录完成：{raw_text[:40]}",
            "command_type": command_type,
            "events": produced_total,
            "needs_followup": False,
            "dashboard": dashboard,
        }
        if tracked_actions:
            resp["action_id"] = tracked_actions[0]["action_id"]
            resp["action_type"] = tracked_actions[0]["action_type"]
        return resp

    cmd = Command(
        command_type=command_type,
        user_id=user_id,
        params={"raw_text": raw_text, **payload},
        source="web_ui",
    )
    event = Event(
        event_type=EventType.USER_COMMAND_RECEIVED,
        aggregate_id=user_id,
        aggregate_type=AggregateType.USER,
        payload={
            "command": cmd.command_type,
            "params": cmd.params,
        },
        metadata=_web_event_metadata(user_id, trace_id),
    )

    try:
        produced = await pipeline.run(event)
    except Exception as e:
        logger.exception("action pipeline.run failed for %s", command_type)
        return {
            "ok": False,
            "message": f"指令执行异常: {str(e)[:80]}",
            "command_type": command_type,
            "events": 0,
            "needs_followup": True,
        }

    # Refresh dashboard for mutating actions too
    engine: StateEngine | None = getattr(request.app.state, "state_engine", None)
    settings = _settings(request)
    dashboard = build_dashboard(engine, settings) if engine else None

    tracked_actions = _track_web_actions(produced, command_type, user_id)
    resp: dict[str, Any] = {
        "ok": True,
        "message": f"已执行：{raw_text[:40]}",
        "command_type": command_type,
        "events": len(produced),
        "needs_followup": False,
        "dashboard": dashboard,
    }
    if tracked_actions:
        resp["action_id"] = tracked_actions[0]["action_id"]
        resp["action_type"] = tracked_actions[0]["action_type"]
    return resp


@router.post("/api/web/proposals/decision")
async def web_proposal_decision(request: Request, body: dict):
    """Accept or reject a Web-held proposal.

    The Web UI keeps the proposal JSON returned by /api/web/actions. This
    endpoint turns the user's decision into canonical events. Calendar writes
    only happen through GoogleCalendarExecutor after an accepted proposal.
    """
    _require_session(request)

    decision = str(body.get("decision", "")).strip().lower()
    proposal_data = body.get("proposal")
    if decision not in {"accept", "reject"} or not isinstance(proposal_data, dict):
        return {
            "ok": False,
            "needs_followup": True,
            "message": "提案参数不完整，无法处理。",
        }

    from src.core.proposal import Proposal, ProposalStatus, ProposalType

    try:
        proposal = Proposal.from_dict(proposal_data)
    except Exception:
        return {
            "ok": False,
            "needs_followup": True,
            "message": "提案格式无效，无法处理。",
        }

    user_id = _web_user_id(request)
    if proposal.user_id and str(proposal.user_id) != str(user_id):
        return {
            "ok": False,
            "needs_followup": True,
            "message": "这条提案不属于当前用户，不能处理。",
        }

    if proposal.is_expired():
        return {
            "ok": False,
            "needs_followup": True,
            "message": "这条提案已经过期，请重新生成。",
        }

    pipeline: Pipeline | None = getattr(request.app.state, "pipeline", None)
    if pipeline is None:
        return {
            "ok": False,
            "needs_followup": True,
            "message": "动作引擎未就绪（pipeline 不可用）",
        }

    trace_id = str(uuid4())
    title = proposal.action_payload.get("title", "安排")

    if decision == "reject":
        proposal.status = ProposalStatus.REJECTED
        rejected_event = Event(
            event_type=EventType.EXECUTION_PROPOSAL_REJECTED,
            aggregate_id=proposal.proposal_id,
            aggregate_type=AggregateType.SYSTEM,
            payload=proposal.to_dict(),
            metadata=_web_event_metadata(user_id, trace_id),
        )
        await pipeline.run(rejected_event)
        return {
            "ok": True,
            "needs_followup": False,
            "message": f"已拒绝：{title}",
            "proposal_id": proposal.proposal_id,
            "decision": "reject",
        }

    proposal.status = ProposalStatus.ACCEPTED
    accepted_event = Event(
        event_type=EventType.EXECUTION_PROPOSAL_ACCEPTED,
        aggregate_id=proposal.proposal_id,
        aggregate_type=AggregateType.SYSTEM,
        payload=proposal.to_dict(),
        metadata=_web_event_metadata(user_id, trace_id),
    )

    try:
        events = await pipeline.run(accepted_event)
    except Exception as exc:
        logger.exception("proposal accept pipeline failed")
        return {
            "ok": False,
            "needs_followup": True,
            "message": f"提案确认失败：{str(exc)[:80]}",
            "proposal_id": proposal.proposal_id,
            "decision": "accept",
        }

    result = _find_execution_result(events)

    if result is None:
        # Safe bridge for Web/API tests or runtimes where execution handlers
        # are not subscribed. This still emits canonical events and delegates
        # external writes to GoogleCalendarExecutor.
        user_accepted = next(
            (event for event in events if event.event_type == EventType.USER_ACCEPTED_PROPOSAL),
            None,
        )
        if user_accepted is None:
            user_accepted = Event(
                event_type=EventType.USER_ACCEPTED_PROPOSAL,
                aggregate_id=proposal.proposal_id,
                aggregate_type=AggregateType.SYSTEM,
                causation_id=accepted_event.event_id,
                payload=proposal.to_dict(),
                metadata=_web_event_metadata(user_id, trace_id),
            )
            await pipeline.run(user_accepted)

        exec_requested = Event(
            event_type=EventType.EXECUTION_REQUESTED,
            aggregate_id=proposal.proposal_id,
            aggregate_type=AggregateType.SYSTEM,
            causation_id=user_accepted.event_id,
            payload=proposal.to_dict(),
            metadata=_web_event_metadata(user_id, trace_id),
        )
        await pipeline.run(exec_requested)

        from src.executor.google_calendar.executor import GoogleCalendarExecutor

        settings = _settings(request)
        executor = GoogleCalendarExecutor(
            use_mock=_setting_bool(settings, "google_calendar_mock", True),
            settings=settings,
        )

        # Route to correct executor method based on proposal type
        proposal_type = proposal.proposal_type
        action_payload = proposal.action_payload
        cal_event_type = EventType.CALENDAR_EVENT_CREATED

        if proposal_type == ProposalType.UPDATE_CALENDAR_EVENT:
            update_payload = {
                "title": action_payload.get("title", ""),
                "start": action_payload.get("start", ""),
                "end": action_payload.get("end", ""),
                "location": action_payload.get("location", ""),
                "description": action_payload.get("description", ""),
            }
            exec_result_dict = await executor.update_event(
                event_id=action_payload.get("event_id", ""),
                calendar_id=action_payload.get("calendar_id"),
                payload=update_payload,
            )
            result = _exec_result_to_event(
                exec_result_dict, proposal, "CALENDAR_EVENT_UPDATED"
            ).with_causation(exec_requested.event_id)
            event_id = action_payload.get("event_id", "")
            calendar_id = action_payload.get("calendar_id") or "primary"
            cal_event_type = EventType.CALENDAR_EVENT_UPDATED

        elif proposal_type == ProposalType.DELETE_CALENDAR_EVENT:
            exec_result_dict = await executor.delete_event(
                event_id=action_payload.get("event_id", ""),
                calendar_id=action_payload.get("calendar_id"),
            )
            result = _exec_result_to_event(
                exec_result_dict, proposal, "CALENDAR_EVENT_DELETED"
            ).with_causation(exec_requested.event_id)
            event_id = action_payload.get("event_id", "")
            calendar_id = action_payload.get("calendar_id") or "primary"
            cal_event_type = EventType.CALENDAR_EVENT_DELETED

        else:  # CREATE_CALENDAR_BLOCK (original path)
            result = await executor.execute(proposal)
            result = result.with_causation(exec_requested.event_id)
            event_id = result.payload.get("event_id", "")
            calendar_id = action_payload.get("calendar_id") or getattr(
                settings, "google_calendar_calendar_id", "primary"
            )

        if result.event_type == EventType.EXECUTION_COMPLETED:
            if _mock_like(calendar_id):
                calendar_id = "primary"
            calendar_event = Event(
                event_type=cal_event_type,
                aggregate_id=proposal.proposal_id,
                aggregate_type=AggregateType.SYSTEM,
                causation_id=exec_requested.event_id,
                payload={
                    "proposal_id": proposal.proposal_id,
                    "calendar_id": calendar_id,
                    "event_id": event_id,
                    "title": title,
                },
                metadata=_web_event_metadata(user_id, trace_id),
            )
            await pipeline.run(calendar_event)

        await pipeline.run(result)

    if result.event_type == EventType.EXECUTION_COMPLETED and proposal.target_system.value == "google_calendar":
        settings_obj = _settings(request)
        refresh_calendar_id = proposal.action_payload.get("calendar_id") or getattr(
            settings_obj, "google_calendar_calendar_id", "primary"
        )
        if _mock_like(refresh_calendar_id):
            refresh_calendar_id = "primary"
        try:
            await pipeline.run(Event(
                event_type=EventType.CONNECTOR_FETCH_REQUESTED,
                aggregate_id="google_calendar",
                aggregate_type=AggregateType.SYSTEM,
                causation_id=result.event_id,
                payload={
                    "source": "google_calendar",
                    "calendar_id": refresh_calendar_id,
                    "reason": "web_calendar_write_refresh",
                },
                metadata=_web_event_metadata(user_id, trace_id),
            ))
        except Exception:
            logger.exception("calendar refresh after web proposal execution failed")

    ok, message = _execution_message(result, title)
    engine: StateEngine | None = getattr(request.app.state, "state_engine", None)
    settings_obj = _settings(request)
    dashboard = build_dashboard(engine, settings_obj) if engine else None

    return {
        "ok": ok,
        "needs_followup": not ok,
        "message": message,
        "proposal_id": proposal.proposal_id,
        "decision": "accept",
        "event": result.payload,
        "error": result.payload.get("error") if not ok else None,
        "dashboard": dashboard,
    }


@router.get("/api/web/actions/recent")
async def web_actions_recent(request: Request, limit: int = 20):
    """List recent undoable/discoverable Web actions for the current user."""
    _require_session(request)
    user_id = _web_user_id(request)
    return {
        "actions": _recent_web_actions_for_user(user_id, limit),
    }


@router.post("/api/web/actions/undo")
async def web_actions_undo(request: Request, body: dict):
    """Undo a recent web action by action_id.

    Only supports finance_transaction and finance_income for now.
    Calendar proposal-only and completion records are rejected when they cannot
    remove the original side effect safely.

    Request:  { "action_id": "..." }
    Response: { "ok": bool, "message": str, "needs_followup": bool }
    """
    _require_session(request)

    action_id = (body.get("action_id") or "").strip()
    if not action_id or action_id not in _web_recent_actions:
        return {
            "ok": False,
            "needs_followup": True,
            "message": "未找到可撤回的操作，可能已过期。",
        }

    action = _web_recent_actions[action_id]
    user_id = _web_user_id(request)
    if action.get("user_id") and str(action.get("user_id")) != str(user_id):
        return {
            "ok": False,
            "needs_followup": True,
            "message": "这条操作不属于当前用户，不能撤回。",
        }

    if action.get("reverted"):
        return {
            "ok": False,
            "needs_followup": True,
            "message": "该操作已被撤回，不能重复撤回。",
        }

    action_type = action["action_type"]
    params = action.get("params", {})

    # Reject calendar proposal-only items (no event_id, no safe delete path)
    if action_type in ("verbal_scheduling",) and not params.get("event_id"):
        return {
            "ok": False,
            "needs_followup": True,
            "message": "日历排期提案尚未创建实际事件，无法撤回。如需取消请忽略该提案。",
        }

    if action_type == "completion_record":
        return {
            "ok": False,
            "needs_followup": True,
            "message": "完成记录暂不支持 Web 自动撤回，因为当前不能安全删除对应记忆/日志。",
        }

    pipeline: Pipeline | None = getattr(request.app.state, "pipeline", None)
    if pipeline is None:
        return {
            "ok": False,
            "needs_followup": True,
            "message": "撤回引擎未就绪（pipeline 不可用）",
        }

    trace_id = str(uuid4())
    meta = _web_event_metadata(user_id, trace_id)

    # Publish USER_UNDO_REQUESTED first for tracing
    await pipeline.run(Event(
        event_type=EventType.USER_UNDO_REQUESTED,
        aggregate_id=user_id,
        aggregate_type=AggregateType.USER,
        payload={"action_id": action_id, "action_type": action_type, "source": "web_ui_undo"},
        metadata=meta,
    ))

    revert_payload: dict[str, Any] = {
        "action_type": action_type,
        "action_id": action_id,
    }
    if action_type == "finance_transaction":
        revert_payload["amount"] = params.get("amount", 0)
        revert_payload["category"] = params.get("category", "other")
    elif action_type == "finance_income":
        revert_payload["amount"] = params.get("amount", 0)

    try:
        await pipeline.run(Event(
            event_type=EventType.USER_ACTION_REVERTED,
            aggregate_id=user_id,
            aggregate_type=AggregateType.USER,
            payload=revert_payload,
            metadata=meta,
        ))
    except Exception as e:
        logger.exception("undo failed for action %s", action_id)
        return {
            "ok": False,
            "needs_followup": True,
            "message": f"撤回执行异常: {str(e)[:80]}",
        }

    action["reverted"] = True
    action["reverted_at"] = datetime.now(timezone.utc).isoformat()

    return {
        "ok": True,
        "message": f"已撤回操作（{action_type}）",
        "needs_followup": False,
    }


# ── Cognitive action tier recommendation (auth required) ────────────────────────


@router.get("/api/web/cognitive/recommendation")
async def web_cognitive_recommendation(request: Request):
    """Return action-tier recommendations based on current workload/pressure."""
    _require_session(request)
    state_engine: StateEngine | None = getattr(request.app.state, "state_engine", None)
    if not state_engine:
        return JSONResponse(status_code=503, content={"ok": False, "message": "state engine not available"})

    try:
        derived = getattr(state_engine, "_derived", {})
        workload = derived.get("workload_density", {}).get("score", 0) if isinstance(derived, dict) else 0
        pressure = derived.get("deadline_pressure", {}).get("score", 0) if isinstance(derived, dict) else 0
        energy_level = max(0.0, 1.0 - max(workload, pressure))

        if energy_level > 0.7:
            tier = "超额模式"; actions = ["完成全部训练容量", "专注深度工作 90 分钟", "处理本周所有待办"]
        elif energy_level > 0.35:
            tier = "标准模式"; actions = ["完成核心训练", "处理 3 项高优任务", "画画 30 分钟"]
        else:
            tier = "最低启动"; actions = ["只做 1 组轻量训练", f"别想了，现在马上去休息 {15 if energy_level < 0.15 else 10} 分钟", "补水 500ml"]

        return {
            "ok": True,
            "energy_level": round(energy_level, 2),
            "workload_score": round(workload, 2),
            "deadline_pressure": round(pressure, 2),
            "tier": tier,
            "actions": actions,
        }
    except Exception as exc:
        logger.exception("cognitive recommendation failed")
        return JSONResponse(status_code=500, content={"ok": False, "message": f"推荐生成失败: {exc}"})


# ── Desktop bridge stub (auth required) ─────────────────────────────────────────


@router.get("/api/web/desktop/status")
async def web_desktop_status(request: Request):
    """Stub for future local desktop bridge integration."""
    _require_session(request)
    return {
        "ok": True,
        "status": "unavailable",
        "message": "Desktop bridge requires a local Python assistant process (M9)",
    }


# ── JWXT schedule sync (auth required) ──────────────────────────────────────────


@router.post("/api/web/sync/jwxt")
async def web_sync_jwxt(request: Request):
    """Trigger a JWXT schedule sync. Requires valid session."""
    _require_session(request)
    pipeline: Pipeline | None = getattr(request.app.state, "pipeline", None)
    if pipeline is None:
        return JSONResponse(status_code=503, content={"ok": False, "message": "pipeline not available"})

    user_id = _web_user_id(request)
    trace_id = str(uuid4())
    event = Event(
        event_type=EventType.CONNECTOR_FETCH_REQUESTED,
        aggregate_id="jwxt",
        aggregate_type=AggregateType.SYSTEM,
        payload={"source": "jwxt", "query": "weekly_schedule", "intent": "manual_sync"},
        metadata=_web_event_metadata(user_id, trace_id),
    )
    try:
        produced = await pipeline.run(event)
        block_count = sum(1 for e in produced if e.event_type == EventType.TEMPORAL_BLOCK_ADDED)
        failed = any(e.event_type == EventType.CONNECTOR_FETCH_FAILED for e in produced)
        if failed:
            err = ""
            for e in produced:
                if e.event_type == EventType.CONNECTOR_FETCH_FAILED:
                    err = e.payload.get("error", "unknown")
                    break
            return {"ok": False, "message": f"同步失败: {err}", "count": 0, "events": len(produced)}
        return {"ok": True, "message": f"课表同步完成，新增 {block_count} 个课程", "count": block_count, "events": len(produced)}
    except Exception as exc:
        logger.exception("jwxt sync failed")
        return {"ok": False, "message": f"同步异常: {exc}", "count": 0, "events": 0}


@router.post("/api/web/sync/jwxt/raw")
async def web_sync_jwxt_raw(request: Request):
    """Accept raw JWXT API response for manual schedule import.

    Body: {"kbList": [...]}

    Useful when the automated connector cannot log in (CAPTCHA, VPN
    required).  The user copies the kbList array from the browserʼs
    DevTools Network tab and pastes it here.
    """
    _require_session(request)
    pipeline: Pipeline | None = getattr(request.app.state, "pipeline", None)
    if pipeline is None:
        return JSONResponse(status_code=503, content={"ok": False, "message": "pipeline not available"})

    body: dict = {}
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"ok": False, "message": "body must be valid JSON"})

    kb_list = body.get("kbList", body.get("kb_list", body.get("data", [])))
    if not isinstance(kb_list, list) or not kb_list:
        return JSONResponse(status_code=400, content={"ok": False, "message": "kbList field is required (JSON array)"})

    user_id = _web_user_id(request)
    trace_id = str(uuid4())

    try:
        from src.connector.jwxt.client import JwxtConnector
        connector = JwxtConnector(settings=_settings(request))
        blocks = connector.parse_kb_list(kb_list)
        if not blocks:
            return {"ok": False, "message": "未解析到任何课程", "count": 0}

        published = 0
        for block_dict in blocks:
            evt = Event(
                event_type=EventType.TEMPORAL_BLOCK_ADDED,
                aggregate_id=block_dict.get("block_id", str(uuid4())[:8]),
                aggregate_type=AggregateType.TEMPORAL,
                payload=block_dict,
                metadata=_web_event_metadata(user_id, trace_id),
            )
            await pipeline.run(evt)
            published += 1

        # Activate courses from imported blocks
        courses_seen: set[str] = set()
        for block_dict in blocks:
            title = block_dict.get("title", "")
            teacher = (block_dict.get("metadata") or {}).get("teacher", "")
            if title and title not in courses_seen:
                courses_seen.add(title)
                await pipeline.run(Event(
                    event_type=EventType.COURSE_ACTIVATED,
                    aggregate_id=title,
                    aggregate_type=AggregateType.COURSE,
                    payload={"course_name": title, "teacher": teacher, "source": "jwxt", "semester": "current"},
                    metadata=_web_event_metadata(user_id, trace_id),
                ))

        # Update JWXT sync health to completed
        await pipeline.run(Event(
            event_type=EventType.CONNECTOR_FETCH_COMPLETED,
            aggregate_id="jwxt",
            aggregate_type=AggregateType.SYSTEM,
            payload={"source": "jwxt", "course_count": len(courses_seen), "block_count": len(blocks)},
            metadata=_web_event_metadata(user_id, trace_id),
        ))

        # Mirror JWXT schedule to Google Calendar (background, non-blocking)
        async def _mirror_to_gcal():
            try:
                from src.executor.google_calendar.executor import GoogleCalendarExecutor
                from src.core.temporal import TimeBlock
                settings = _settings(request)
                time_blocks = [TimeBlock.from_dict(b) for b in blocks]
                executor = GoogleCalendarExecutor(
                    use_mock=settings.google_calendar_mock,
                    settings=settings,
                )
                result = await executor.sync_schedule_blocks(time_blocks)
                logger.info("jwxt->gcal mirror: %s", result)
            except Exception as exc:
                logger.warning("jwxt->gcal mirror failed (non-fatal): %s", exc)

        asyncio.create_task(_mirror_to_gcal())

        return {"ok": True, "message": f"手动导入 {len(blocks)} 个课程, {len(courses_seen)} 门课 (Google Calendar 后台同步中...)", "count": len(blocks), "events": published}
    except Exception as exc:
        logger.exception("jwxt raw import failed")
        return {"ok": False, "message": f"解析失败: {exc}", "count": 0, "events": 0}


# ── Worker heartbeat status (auth required) ─────────────────────────────────────


@router.get("/api/web/worker/heartbeat_status")
async def web_worker_heartbeat_status(request: Request):
    """Return the latest worker heartbeat from the EventStore."""
    _require_session(request)

    event_store = getattr(request.app.state, "event_store", None)
    if not event_store or not hasattr(event_store, "get_recent"):
        return JSONResponse(status_code=503, content={"ok": False, "message": "event store not available"})

    now_utc = datetime.now(timezone.utc)
    worker_heartbeats: list[dict[str, Any]] = []
    latest_ts: datetime | None = None
    latest_payload: dict[str, Any] = {}

    try:
        recent = await event_store.get_recent(100)
        for evt in recent:
            if evt.event_type == EventType.SYSTEM_RUNTIME_HEARTBEAT and (evt.metadata or {}).get("source") == "worker":
                ts = evt.timestamp
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                worker_heartbeats.append({
                    "timestamp": ts.isoformat(),
                    "emit_count": evt.payload.get("emit_count", 0),
                    "uptime_s": evt.payload.get("uptime_s", 0),
                    "last_sync_status": evt.payload.get("last_sync_status", ""),
                    "last_sync_count": evt.payload.get("last_sync_count", 0),
                })
                if latest_ts is None or ts > latest_ts:
                    latest_ts = ts
                    latest_payload = evt.payload

        if latest_ts is not None:
            age_s = int((now_utc - latest_ts).total_seconds())
            if age_s <= 60:
                status = "alive"
            elif age_s <= 300:
                status = "stale"
            else:
                status = "dead"
            return {
                "ok": True,
                "status": status,
                "last_heartbeat": latest_ts.isoformat(),
                "seconds_since_heartbeat": age_s,
                "emit_count": latest_payload.get("emit_count", 0),
                "uptime_s": latest_payload.get("uptime_s", 0),
                "last_sync_status": latest_payload.get("last_sync_status", ""),
                "last_sync_count": latest_payload.get("last_sync_count", 0),
                "total_heartbeats_in_window": len(worker_heartbeats),
            }
        return {
            "ok": True,
            "status": "missing",
            "last_heartbeat": None,
            "seconds_since_heartbeat": None,
            "emit_count": 0,
            "uptime_s": 0,
            "last_sync_status": "never",
            "last_sync_count": 0,
            "total_heartbeats_in_window": 0,
        }
    except Exception:
        logger.exception("worker heartbeat status query failed")
        return JSONResponse(status_code=500, content={"ok": False, "message": "heartbeat query failed"})


# ── Google Calendar live probe (auth required, no secrets) ─────────────────────


@router.get("/api/web/sync/google-calendar/probe")
async def web_sync_google_calendar_probe(request: Request):
    """Check real Google API connectivity without performing a full sync.

    Requires valid session.  Returns structured probe result — never
    leaks token content or credentials.
    """
    _require_session(request)

    from src.connector.google_calendar.client import GoogleCalendarConnector
    connector = GoogleCalendarConnector(settings=_settings(request))

    try:
        result = await connector.probe_live_connection()
    except Exception as exc:
        result = {"status": "FAIL", "reason": f"ERR_PROBE_EXCEPTION: {exc}"}

    return {"ok": result["status"] == "PASS" or result["status"] == "SKIPPED",
            **result}


# ── Google Calendar real sync (probe-gated, auth required) ──────────────────────


@router.post("/api/web/sync/google-calendar/execute")
async def web_sync_google_calendar_execute(request: Request):
    """Probe-gated real read-only Google Calendar sync.

    The probe MUST pass before real API calls are attempted.  If the
    probe fails the endpoint returns 200 with ``"status": "DEGRADED"``
    so the frontend never sees a crash.
    """
    _require_session(request)

    pipeline: Pipeline | None = getattr(request.app.state, "pipeline", None)
    if pipeline is None:
        return JSONResponse(status_code=503, content={"ok": False, "message": "pipeline not available"})

    from src.connector.google_calendar.client import GoogleCalendarConnector
    connector = GoogleCalendarConnector(settings=_settings(request))

    user_id = _web_user_id(request)
    trace_id = str(uuid4())

    try:
        result, events = await connector.execute_real_readonly_sync(
            causation_id=trace_id, trace_id=trace_id,
        )

        # Publish produced events through the pipeline so StateEngine applies them
        for evt in events:
            await pipeline.run(evt)

        return {
            **result,
            "events_published": len(events),
        }
    except Exception as exc:
        logger.exception("google calendar real sync failed")
        return {
            "ok": False,
            "status": "EXCEPTION",
            "message": f"Real sync crashed: {exc}",
            "count": 0,
            "events_published": 0,
        }


# ── Google Calendar diagnostics (auth required, no secrets) ────────────────────


@router.get("/api/web/diagnostics/google-calendar")
async def web_diagnostics_google_calendar(request: Request):
    """Check whether Google Calendar real-sync prerequisites are met.

    Returns booleans and path-configured flags only. Never returns token
    content, credentials content, or any secret.
    """
    _require_session(request)

    settings = _settings(request)
    creds_path = str(getattr(settings, "google_calendar_credentials_path", ""))
    token_path = str(getattr(settings, "google_calendar_token_path", ""))
    creds_configured = bool(creds_path)
    token_configured = bool(token_path)
    creds_exists = creds_configured and os.path.isfile(creds_path)
    token_exists = token_configured and os.path.isfile(token_path)
    calendar_id = str(getattr(settings, "google_calendar_calendar_id", "primary"))
    mock = bool(getattr(settings, "google_calendar_mock", True))
    write_enabled = bool(getattr(settings, "google_calendar_write_enabled", False))
    creds_env = bool(str(getattr(settings, "google_calendar_credentials_json", "")))
    token_env = bool(str(getattr(settings, "google_calendar_token_json", "")))

    # Credentials available via file OR env var
    has_creds = creds_exists or creds_env
    has_token = token_exists or token_env

    missing: list[str] = []
    if not creds_configured and not creds_env:
        missing.append("credentials_path")
    elif not has_creds:
        missing.append("credentials_file")
    if not token_configured and not token_env:
        missing.append("token_path")
    elif not has_token:
        missing.append("token_file")

    ready = bool(has_creds and has_token and not mock)

    return {
        "ok": True,
        "mock": mock,
        "write_enabled": write_enabled,
        "credentials_path_configured": creds_configured,
        "credentials_file_exists": creds_exists,
        "credentials_env_configured": creds_env,
        "token_path_configured": token_configured,
        "token_file_exists": token_exists,
        "token_env_configured": token_env,
        "calendar_id_configured": bool(calendar_id),
        "timezone": str(getattr(settings, "google_calendar_timezone", "Asia/Singapore")),
        "ready_for_real_sync": ready,
        "missing": missing,
    }


# ── Google Calendar manual sync (auth required) ───────────────────────────────


@router.post("/api/web/sync/google-calendar")
async def web_sync_google_calendar(request: Request):
    """Trigger a read-only Google Calendar sync. Requires valid session."""
    _require_session(request)

    pipeline: Pipeline | None = getattr(request.app.state, "pipeline", None)
    if pipeline is None:
        return JSONResponse(status_code=503, content={"ok": False, "message": "pipeline not available"})

    user_id = _web_user_id(request)
    trace_id = str(uuid4())

    event = Event(
        event_type=EventType.CONNECTOR_FETCH_REQUESTED,
        aggregate_id="google_calendar",
        aggregate_type=AggregateType.SYSTEM,
        payload={"source": "google_calendar", "query": "calendar_events", "intent": "manual_sync"},
        metadata=_web_event_metadata(user_id, trace_id),
    )

    try:
        produced = await pipeline.run(event)
        block_count = sum(1 for e in produced if e.event_type == EventType.TEMPORAL_BLOCK_ADDED)
        cancelled_count = sum(1 for e in produced if e.event_type == EventType.TEMPORAL_BLOCK_CANCELLED)
        failed = any(e.event_type == EventType.CONNECTOR_FETCH_FAILED for e in produced)

        if failed:
            err_detail = ""
            for e in produced:
                if e.event_type == EventType.CONNECTOR_FETCH_FAILED:
                    err_detail = e.payload.get("error", "unknown error")
                    break
            return {
                "ok": False,
                "message": f"同步失败: {err_detail}",
                "count": 0,
                "events": len(produced),
            }

        return {
            "ok": True,
            "message": f"日历同步完成，新增 {block_count} 个事件" +
                       (f"，移除 {cancelled_count} 个" if cancelled_count > 0 else ""),
            "count": block_count,
            "events": len(produced),
        }
    except Exception as exc:
        logger.exception("google calendar manual sync failed")
        return {
            "ok": False,
            "message": f"同步异常: {str(exc)}",
            "count": 0,
            "events": 0,
        }


# ── System status (auth required, no secrets) ──────────────────────────────────


@router.get("/api/web/status")
async def web_status(request: Request):
    """Return system health info. Requires valid session."""
    _require_session(request)

    state_engine: StateEngine | None = getattr(request.app.state, "state_engine", None)
    event_store = getattr(request.app.state, "event_store", None)
    pipeline: Pipeline | None = getattr(request.app.state, "pipeline", None)
    settings = getattr(request.app.state, "settings", None)

    # Event counts
    event_count = await event_store.count() if event_store and hasattr(event_store, "count") else 0
    state_event_count = state_engine.event_count if state_engine else 0
    state_hash = state_engine.state_hash() if state_engine else ""

    # Bus subscribers
    bus = pipeline._bus if pipeline else None
    bus_subscribers = bus.subscriber_count if bus else {}

    # Sanitized settings — no secrets, tokens, PINs, full DATABASE_URL
    settings_info: dict[str, Any] = {}
    if settings:
        db_url = str(getattr(settings, "database_url", ""))
        db_type = "postgresql" if db_url.startswith("postgresql") else "sqlite" if db_url else "unknown"
        settings_info = {
            "chaoxing_mock": bool(getattr(settings, "chaoxing_mock", True)),
            "jwxt_mock": bool(getattr(settings, "jwxt_mock", True)),
            "google_calendar_mock": bool(getattr(settings, "google_calendar_mock", True)),
            "momo_sync_enabled": bool(getattr(settings, "momo_sync_enabled", False)),
            "obsidian_vault_configured": bool(getattr(settings, "obsidian_vault_path", "")),
            "database_url_type": db_type,
        }

    # Worker health — find latest worker heartbeat from recent events
    worker_info: dict[str, Any] = {"status": "missing"}
    if event_store and hasattr(event_store, "get_recent"):
        try:
            recent = await event_store.get_recent(50)
            now_utc = datetime.now(timezone.utc)
            latest: datetime | None = None
            for evt in recent:
                if (evt.event_type == EventType.SYSTEM_RUNTIME_HEARTBEAT
                        and (evt.metadata or {}).get("source") == "worker"):
                    ts = evt.timestamp
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    if latest is None or ts > latest:
                        latest = ts
            if latest is not None:
                age_s = int((now_utc - latest).total_seconds())
                if age_s <= 600:          # ≤ 10 min
                    worker_status = "alive"
                elif age_s <= 1800:       # ≤ 30 min
                    worker_status = "stale"
                else:
                    worker_status = "missing"
                worker_info = {
                    "status": worker_status,
                    "last_heartbeat": latest.isoformat(),
                    "seconds_since_heartbeat": age_s,
                }
        except Exception:
            logger.exception("worker health check failed")

    # Sync health — read from state engine
    sync_health: dict[str, Any] = {
        "google_calendar": {"status": "unknown"},
    }
    if state_engine:
        try:
            sync_state = state_engine._state.get("sync", {})
            gcal_sync = sync_state.get("google_calendar", {})
            if gcal_sync:
                sync_health["google_calendar"] = {
                    "status": gcal_sync.get("status", "unknown"),
                    "last_sync": gcal_sync.get("last_sync_completed") or gcal_sync.get("last_sync"),
                    "count": gcal_sync.get("count") or gcal_sync.get("block_count") or gcal_sync.get("item_count"),
                    "error": gcal_sync.get("error", ""),
                }
        except Exception:
            pass

    return {
        "ok": True,
        "event_count": event_count,
        "state_event_count": state_event_count,
        "state_hash": state_hash,
        "bus_subscribers": bus_subscribers,
        "settings": settings_info,
        "worker": worker_info,
        "sync_health": sync_health,
    }
