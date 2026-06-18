"""Read-only dashboard projection over a StateEngine snapshot."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from src.core.state_engine import StateEngine
from src.domain.course_topology import is_excluded_course, normalize_course_name
from src.domain.homework.status import is_open_homework_status

logger = logging.getLogger(__name__)
LOCAL_TZ = timezone(timedelta(hours=8))
COURSE_BLOCK_TYPES = {"class_lecture", "class_lab", "course", "schedule", "schedule_item"}


def build_dashboard(
    state_engine: StateEngine | None,
    settings: Any,
    *,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Build the Web dashboard without mutating runtime state."""
    now = (as_of or datetime.now(LOCAL_TZ)).astimezone(LOCAL_TZ)
    today = now.date()
    snapshot = state_engine.snapshot() if state_engine else {}
    if not isinstance(snapshot, dict):
        snapshot = {}
    state = snapshot.get("state")
    derived = snapshot.get("derived")
    if not isinstance(state, dict):
        state = getattr(state_engine, "_state", {}) if state_engine else {}
    if not isinstance(derived, dict):
        derived = state_engine.get_all_derived() if state_engine else {}
    if not isinstance(derived, dict):
        derived = {}

    deadline = derived.get("deadline_pressure", {})
    workload = derived.get("workload_density", {})
    active_context = derived.get("active_context", {})

    feedback = _latest_task_feedback(state)
    homework: list[dict[str, Any]] = []
    hidden_count = 0
    for aggregate_id, item in _mapping(state, "homework").items():
        if not isinstance(item, dict) or not item.get("title"):
            continue
        task_feedback = _homework_feedback(feedback, str(aggregate_id), item)
        feedback_status = task_feedback.get("status") if task_feedback else ""
        if feedback_status in {"completed", "skipped"}:
            hidden_count += 1
            continue
        course = normalize_course_name(item.get("course", ""), item.get("teacher"))
        status = str(item.get("status", "pending") or "pending")
        raw_status = str(
            item.get("raw_status")
            or item.get("status_text")
            or item.get("display_status")
            or ""
        )
        if is_excluded_course(course) or (
            feedback_status != "delayed"
            and not is_open_homework_status(status, raw_status)
        ):
            hidden_count += 1
            continue
        homework.append({
            "id": aggregate_id,
            "title": item["title"],
            "course": course,
            "deadline": item.get("deadline"),
            "status": feedback_status or status,
            "original_status": status,
            "feedback": task_feedback or None,
        })
    homework.sort(key=lambda item: item.get("deadline") or "9999-12-31")

    today_schedule: list[dict[str, Any]] = []
    schedule_keys: set[tuple[str, str, str]] = set()
    schedule = _mapping(state, "schedule").get(today.isoformat(), [])
    if isinstance(schedule, list):
        for item in schedule:
            if isinstance(item, dict):
                schedule_item = {
                    "course": item.get("course", item.get("name", "")),
                    "start": item.get("start", item.get("start_time", "")),
                    "end": item.get("end", item.get("end_time", "")),
                    "location": item.get("location", ""),
                    "teacher": item.get("teacher", ""),
                }
                _append_schedule_item(today_schedule, schedule_keys, schedule_item)

    temporal = _mapping(state, "temporal")
    temporal_blocks = temporal.get("blocks", [])
    blocks_today = (
        list(temporal_blocks.get(today.isoformat(), []))
        if isinstance(temporal_blocks, dict)
        else list(temporal_blocks)
        if isinstance(temporal_blocks, list)
        else []
    )

    calendar_events: list[dict[str, Any]] = []
    calendar_day = _mapping(state, "calendar").get(today.isoformat(), [])
    if isinstance(calendar_day, list):
        for item in calendar_day:
            if isinstance(item, dict):
                calendar_events.append({
                    "summary": item.get("summary", ""),
                    "start": item.get("start", item.get("start_time", "")),
                    "end": item.get("end", item.get("end_time", "")),
                    "source": "legacy",
                })

    if state_engine is not None:
        for block in state_engine.get_temporal_blocks():
            data = block.to_dict()
            source = str(data.get("source", ""))
            schedule_item = _temporal_schedule_item(data, today)
            if schedule_item:
                _append_schedule_item(today_schedule, schedule_keys, schedule_item)
            if source not in {"google_calendar", "jwxt"}:
                continue
            start = str(data.get("start_time") or data.get("start") or "")
            if start[:10] != today.isoformat():
                continue
            title = str(
                data.get("summary")
                or data.get("title")
                or data.get("label")
                or data.get("description")
                or ""
            )
            if not any(item.get("summary") == title for item in calendar_events):
                calendar_events.append({
                    "summary": title,
                    "start": start,
                    "end": str(data.get("end_time") or data.get("end") or ""),
                    "source": source,
                })
            metadata = data.get("metadata") or {}
            blocks_today.append({
                "course": title,
                "name": title,
                "start": start,
                "start_time": start,
                "end": str(data.get("end_time") or data.get("end") or ""),
                "end_time": str(data.get("end_time") or data.get("end") or ""),
                "location": str(data.get("location", "")),
                "teacher": str(metadata.get("teacher", data.get("description", ""))),
                "source": source,
            })

    today_schedule.sort(key=lambda item: (str(item.get("start", "")), str(item.get("course", ""))))

    vocab_progress: dict[str, dict[str, Any]] = {}
    for aggregate_id, item in _mapping(state, "vocab").items():
        if isinstance(item, dict):
            vocab_progress[aggregate_id] = {
                "new_words_today": item.get("new_words_today", item.get("new", 0)),
                "review_words": item.get("review_words", item.get("review", 0)),
                "total_mastered": item.get("total_mastered", item.get("mastered", 0)),
                "streak_days": item.get("streak_days", item.get("streak", 0)),
            }

    finance = _finance_summary(state, settings)
    parent_funds = _mapping(state, "parent_funds").get("current", {})
    partner_debts = _mapping(state, "partner_debts").get("current", {})
    art = _art_summary(state, today)
    fitness = _fitness_summary(settings, today)
    sync_health = _sync_health(state)
    chaoxing_health = sync_health["chaoxing"]
    configured_mock = bool(getattr(settings, "chaoxing_mock", True))
    chaoxing_health.setdefault("mock_enabled", configured_mock)
    if configured_mock and chaoxing_health.get("status") == "unknown":
        chaoxing_health["status"] = "mock"
    homework_empty_reason = _homework_empty_reason(
        homework,
        hidden_count,
        chaoxing_health,
    )
    schedule_empty_reason = _schedule_empty_reason(today_schedule, sync_health)
    consistency = _mapping(state, "calendar_consistency")

    return {
        "today": today.isoformat(),
        "weekday": _weekday_cn(today),
        "deadline_pressure": deadline,
        "workload_density": workload,
        "active_context": active_context,
        "homework": homework,
        "homework_count": len(homework),
        "homework_hidden_count": hidden_count,
        "homework_empty_reason": homework_empty_reason,
        "today_schedule": today_schedule,
        "schedule_count": len(today_schedule),
        "schedule_empty_reason": schedule_empty_reason,
        "calendar_events": calendar_events,
        "temporal_blocks": blocks_today,
        "vocab_progress": vocab_progress,
        "fitness": fitness,
        "finance": finance,
        "parent_funds": {
            "planned_requests": parent_funds.get("planned_requests", []),
            "request_log": parent_funds.get("request_log", []),
            "received_log": parent_funds.get("received_log", []),
            "recurring_items": parent_funds.get("recurring_items", []),
            "recurring_rules": parent_funds.get("recurring_rules", []),
        } if parent_funds else {},
        "partner_debts": {
            "total_outstanding": int(partner_debts.get("total_outstanding", 0)),
            "debts": list(partner_debts.get("debts", []))[-10:],
        } if partner_debts else {},
        "art": art,
        "sync_health": sync_health,
        "calendar_consistency": {
            "latest": consistency.get("latest", {}),
            "repair": consistency.get("repair", {}),
        },
    }


def _mapping(state: dict[str, Any], key: str) -> dict[str, Any]:
    value = state.get(key, {}) if state else {}
    return value if isinstance(value, dict) else {}


def _append_schedule_item(
    schedule: list[dict[str, Any]],
    seen: set[tuple[str, str, str]],
    item: dict[str, Any],
) -> None:
    key = (
        str(item.get("course", "")).strip().casefold(),
        _schedule_key_time(item.get("start")),
        _schedule_key_time(item.get("end")),
    )
    if not key[0] or key in seen:
        return
    seen.add(key)
    schedule.append(item)


def _schedule_key_time(value: Any) -> str:
    text = str(value or "").strip()
    parsed = _local_datetime(text)
    return parsed.strftime("%H:%M") if parsed else text


def _temporal_schedule_item(data: dict[str, Any], today: date) -> dict[str, Any] | None:
    source = str(data.get("source", "")).strip()
    block_type = str(data.get("block_type", data.get("type", ""))).strip()
    if block_type and block_type not in COURSE_BLOCK_TYPES:
        return None
    if not block_type and source != "jwxt":
        return None

    start = _local_datetime(data.get("start_time") or data.get("start"))
    end = _local_datetime(data.get("end_time") or data.get("end"))
    if start is None or start.date() != today:
        return None

    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    course = str(
        data.get("course")
        or data.get("title")
        or data.get("name")
        or data.get("summary")
        or metadata.get("course")
        or ""
    ).strip()
    if not course:
        return None

    return {
        "course": course,
        "start": start.strftime("%H:%M"),
        "end": end.strftime("%H:%M") if end else "",
        "location": str(data.get("location") or metadata.get("location") or ""),
        "teacher": str(metadata.get("teacher") or data.get("teacher") or data.get("description") or ""),
        "source": source,
    }


def _local_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=LOCAL_TZ)
    return parsed.astimezone(LOCAL_TZ)


def _schedule_empty_reason(
    schedule: list[dict[str, Any]],
    sync_health: dict[str, dict[str, Any]],
) -> str:
    if schedule:
        return ""
    jwxt = sync_health.get("jwxt", {})
    if jwxt.get("status") != "failed":
        return "schedule_empty_no_blocks"
    error_code = str(jwxt.get("error_code", "")).strip()
    if error_code.startswith("jwxt_") and error_code not in {
        "jwxt_network_error",
        "jwxt_parser_error",
    }:
        return "schedule_empty_auth_failed"
    if error_code:
        return error_code
    error = str(jwxt.get("error", "")).strip()
    lowered = error.casefold()
    auth_markers = ("auth", "cookie", "credential", "login", "认证", "登录", "凭据")
    if any(marker in lowered for marker in auth_markers):
        return "schedule_empty_auth_failed"
    return error or "schedule_empty_no_blocks"


def _homework_empty_reason(
    homework: list[dict[str, Any]],
    hidden_count: int,
    chaoxing_health: dict[str, Any],
) -> str:
    if homework:
        return ""
    if chaoxing_health.get("mock_enabled"):
        return (
            "homework_empty_mock_filtered"
            if hidden_count > 0
            else "homework_empty_mock_enabled"
        )
    if chaoxing_health.get("status") == "failed":
        return str(
            chaoxing_health.get("error_code")
            or chaoxing_health.get("error")
            or "chaoxing_auth_failed"
        )
    return "homework_empty_no_items"


def _latest_task_feedback(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    current = _mapping(state, "behavior").get("current", {})
    log = current.get("feedback_log", []) if isinstance(current, dict) else []
    latest: dict[str, dict[str, Any]] = {}
    if not isinstance(log, list):
        return latest
    for item in log:
        if not isinstance(item, dict):
            continue
        task_id = str(item.get("task_id") or "").strip()
        action = item.get("action", "")
        outcome = item.get("outcome", "")
        status = "completed" if outcome == "completed" else action if action in {"skipped", "delayed"} else ""
        if task_id and status:
            latest[task_id] = {
                "status": status,
                "action": action,
                "outcome": outcome,
                "timestamp": item.get("outcome_timestamp") or item.get("timestamp"),
                "delay_minutes": item.get("delay_minutes"),
                "delayed_until": item.get("delayed_until"),
            }
    return latest


def _homework_feedback(feedback, aggregate_id, homework):
    for key in (aggregate_id, str(homework.get("title") or "")):
        if key and key in feedback:
            return feedback[key]
    return None


def _finance_summary(state: dict[str, Any], settings: Any) -> dict[str, Any]:
    monthly = _mapping(state, "finance").get("monthly", {})
    if not monthly:
        return {}
    reverted: set[str] = set()
    for view in _mapping(state, "undo").values():
        if isinstance(view, dict):
            for item in view.get("reverted_actions", []):
                if isinstance(item, dict) and item.get("action_id"):
                    reverted.add(str(item["action_id"]))

    def ledger(items, action_type):
        rows = []
        for item in items:
            row = dict(item) if isinstance(item, dict) else {}
            event_id = str(row.get("event_id") or "")
            row.update({
                "action_id": event_id,
                "action_type": action_type,
                "reverted": bool(event_id and event_id in reverted),
                "can_undo": bool(event_id and event_id not in reverted),
            })
            rows.append(row)
        return rows

    inflow = float(monthly.get("inflow", 0) or 0)
    outflow = float(monthly.get("outflow", 0) or 0)
    return {
        "monthly_budget": int(monthly.get(
            "outing_budget",
            getattr(settings, "finance_monthly_outing_budget", 250),
        )),
        "monthly_spend": int(outflow),
        "inflow": int(inflow),
        "outflow": int(outflow),
        "outing_spent": int(float(monthly.get("outing_spent", 0) or 0)),
        "by_category": {
            key: int(value)
            for key, value in monthly.get("by_category", {}).items()
        },
        "estimated_savings": int(max(0, inflow - outflow)),
        "savings_target": int(monthly.get(
            "savings_target",
            getattr(settings, "finance_monthly_savings_target", 500),
        )),
        "savings_progress": int(monthly.get(
            "savings_progress",
            monthly.get("current_savings", 0),
        )),
        "partner_debt": int(monthly.get(
            "partner_debt",
            monthly.get("partner_debt_total", 0),
        )),
        "transactions": ledger(
            monthly.get("transactions", []),
            "finance_transaction",
        )[-30:],
        "income_log": ledger(
            monthly.get("income_log", []),
            "finance_income",
        )[-30:],
        "reverted_action_ids": sorted(reverted),
    }


def _art_summary(state: dict[str, Any], today: date) -> dict[str, Any]:
    item = _mapping(state, "art").get(today.isoformat(), {})
    if not isinstance(item, dict) or not item:
        return {}
    return {
        "planned_minutes": item.get("planned_minutes", item.get("target", 0)),
        "completed_minutes": item.get("completed_minutes", item.get("completed", 0)),
        "status": item.get("status", "pending"),
    }


def _fitness_summary(settings: Any, today: date) -> dict[str, Any]:
    vault_path = getattr(settings, "obsidian_vault_path", "")
    if not vault_path:
        return {}
    try:
        from src.domain.fitness.ui_service import read_session

        session = read_session(vault_path, today)
        if not session:
            return {}
        return {
            "training_day": session.get("training_day", ""),
            "focus": session.get("focus", ""),
            "total_sets": session["total_sets"],
            "completed_sets": session["completed_sets"],
            "completion_pct": round(
                session["completed_sets"] / max(session["total_sets"], 1) * 100
            ),
            "completed": session.get("completed", False),
        }
    except Exception:
        logger.debug("fitness session read failed", exc_info=True)
        return {}


def _sync_health(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result = {
        source: {"status": "unknown"}
        for source in ("chaoxing", "jwxt", "google_calendar", "momo")
    }
    sync = _mapping(state, "sync")
    for source in result:
        item = sync.get(source, {})
        if source == "momo" and not item:
            item = sync.get("momo_vocab", {})
        if item:
            result[source] = {
                "status": item.get("status", "unknown"),
                "last_sync": item.get(
                    "last_sync",
                    item.get(
                        "last_sync_at",
                        item.get("last_sync_completed", item.get("last_sync_failed")),
                    ),
                ),
                "error": item.get("error", ""),
                "error_code": item.get("error_code", ""),
                "count": item.get(
                    "count",
                    item.get("block_count", item.get("total_assignments")),
                ),
                "pulled_count": item.get(
                    "pulled_count",
                    item.get("total_assignments", item.get("count")),
                ),
                "temporal_blocks_count": item.get(
                    "temporal_blocks_count",
                    item.get("block_count"),
                ),
                "homework_count": item.get("homework_count"),
                "mock_enabled": item.get("mock_enabled", False),
                "auto_login_attempted": item.get("auto_login_attempted", False),
                "success": item.get("success"),
                "duration_ms": item.get("duration_ms"),
            }

    projection = _mapping(state, "temporal").get("projection", {})
    calendar = projection.get("calendar_sync", {}) if isinstance(projection, dict) else {}
    if calendar and result["google_calendar"]["status"] == "unknown":
        result["google_calendar"] = _health_entry(
            calendar.get("status", "unknown"),
            calendar.get("completed_at", calendar.get("started_at")),
            calendar_id=calendar.get("calendar_id"),
            calendar_count=calendar.get("calendar_count"),
            count=calendar.get("count"),
        )

    vocab = _mapping(state, "vocab").get("momo", {})
    if vocab and result["momo"]["status"] == "unknown":
        result["momo"] = _health_entry(
            vocab.get("sync_status", "unknown"),
            vocab.get(
                "last_sync_completed",
                vocab.get("last_sync_started", vocab.get("last_sync")),
            ),
            vocab.get("last_error", ""),
            external_last_sync=vocab.get("last_sync"),
            stale=vocab.get("stale"),
        )
    return result


def _health_entry(status, last_sync=None, error="", **extra):
    item = {"status": status}
    if last_sync:
        item["last_sync"] = last_sync
    if error:
        item["error"] = error
    item.update({
        key: value
        for key, value in extra.items()
        if value not in (None, "", [], {})
    })
    return item


def _weekday_cn(value: date) -> str:
    return ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"][
        value.weekday()
    ]
