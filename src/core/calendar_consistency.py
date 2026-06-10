"""Calendar consistency review — post-sync auto-audit.

Runs after sync operations to detect:
- JWXT schedule vs Google Calendar mirror divergence
- Managed art blocks overlapping with busy intervals (class / fixed calendar)
- Stale or empty Google Calendar sync state

All functions are pure-ish: they read from StateEngine (read-only).
No side effects on calendars, no external API calls.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from zoneinfo import ZoneInfo

from src.core.events import Event, EventType, AggregateType
from src.domain.planning.time_windows import load_busy_intervals, detect_overlap, art_exclude_filter

logger = logging.getLogger(__name__)

LOCAL_TZ = ZoneInfo("Asia/Singapore")
ART_MANAGED_SOURCES = {"daily_art_plan", "art_planner"}

# Severity levels
OK = "ok"
WARNING = "warning"
ERROR = "error"

FINDING_SEVERITY_ORDER = {OK: 0, WARNING: 1, ERROR: 2}


def review_sync_status(state_engine) -> list[dict[str, str]]:
    """Check Google Calendar sync recency and event count.

    Returns findings (empty list if everything looks normal).
    """
    findings: list[dict[str, str]] = []
    temporal = state_engine.get_view("temporal", "projection")
    cal_sync = temporal.get("calendar_sync", {}) if temporal else {}

    sync_count = cal_sync.get("count", -1)
    sync_completed_at = cal_sync.get("completed_at", "")

    if sync_count == -1:
        # No sync record at all — this is normal during early startup
        return findings

    if sync_count == 0:
        findings.append({
            "severity": WARNING,
            "message": "Google Calendar 最近同步 0 条，可能未读取到目标日历",
            "detail": f"completed_at={sync_completed_at}",
        })

    # Check if sync is stale (older than 2 hours)
    if sync_completed_at:
        try:
            completed_dt = datetime.fromisoformat(sync_completed_at)
            if completed_dt.tzinfo is None:
                completed_dt = completed_dt.replace(tzinfo=timezone.utc)
            age_minutes = (datetime.now(timezone.utc) - completed_dt).total_seconds() / 60
            if age_minutes > 120:
                findings.append({
                    "severity": WARNING,
                    "message": f"Google Calendar 同步数据已过时（{age_minutes:.0f} 分钟前）",
                    "detail": f"last_completed_at={sync_completed_at}",
                })
        except (ValueError, TypeError):
            pass

    return findings


def _get_block_start_end_local(block: Any) -> tuple[datetime, datetime, str, str, dict]:
    """Normalize any block to local-time start/end + source + block_type + metadata."""
    if isinstance(block, dict):
        raw_start = block.get("start", "")
        raw_end = block.get("end", "")
        b_start = datetime.fromisoformat(raw_start.replace("Z", "+00:00")).astimezone(LOCAL_TZ)
        b_end = datetime.fromisoformat(raw_end.replace("Z", "+00:00")).astimezone(LOCAL_TZ)
        source = str(block.get("source", "") or block.get("external_source", ""))
        block_type = str(block.get("block_type", ""))
        meta = block.get("metadata", {}) or {}
    else:
        b_start = block.start.astimezone(LOCAL_TZ)
        b_end = block.end.astimezone(LOCAL_TZ)
        source = str(getattr(block, "source", ""))
        block_type = str(getattr(block, "block_type", ""))
        meta = block.metadata or {}
    return b_start, b_end, source, block_type, meta


def review_art_block_conflicts(state_engine) -> list[dict[str, str]]:
    """Check managed art blocks for overlap with busy intervals.

    Scans the next 7 days. An art block that overlaps with a JWXT class
    or a Google Calendar busy block is flagged.
    """
    findings: list[dict[str, str]] = []
    blocks = state_engine.get_temporal_blocks()

    now_local = datetime.now(LOCAL_TZ)
    window_end = now_local + timedelta(days=7)

    art_blocks = []
    for b in blocks:
        b_start, _b_end, source, _bt, meta = _get_block_start_end_local(b)
        if b_start < now_local or b_start >= window_end:
            continue
        if source in ART_MANAGED_SOURCES or meta.get("managed_by") == "cognitive_os":
            art_blocks.append(b)

    if not art_blocks:
        return findings

    # Build busy intervals from non-art blocks
    busy = load_busy_intervals(blocks, day_start=now_local, day_end=window_end, exclude_filter=art_exclude_filter)

    for art_b in art_blocks:
        a_start, a_end, _src, _bt, meta = _get_block_start_end_local(art_b)
        if detect_overlap(a_start, a_end, busy):
            title = meta.get("original_title", getattr(art_b, "title", "画画块"))
            findings.append({
                "severity": WARNING,
                "message": f"发现画画块「{title}」与课程/日历冲突",
                "detail": f"{a_start.strftime('%m-%d %H:%M')}-{a_end.strftime('%H:%M')}",
            })

    return findings


def review_schedule_mirror(state_engine, executor=None) -> list[dict[str, str]]:
    """Compare JWXT temporal blocks with expected count.

    If an executor is provided with verify_schedule_mirror, uses it.
    Otherwise does a lightweight local check: counts expected JWXT blocks
    vs what's in temporal state.
    """
    findings: list[dict[str, str]] = []
    blocks = state_engine.get_temporal_blocks()

    now_utc = datetime.now(timezone.utc)
    end_utc = now_utc + timedelta(days=7)

    jwxt_blocks = [
        b for b in blocks
        if str(getattr(b, "source", "")) == "jwxt"
        and str(getattr(b, "block_type", "")) in {"class_lecture", "class_lab"}
        and now_utc <= getattr(b, "start", b.get("start") if isinstance(b, dict) else now_utc).astimezone(timezone.utc) < end_utc
    ]

    if not jwxt_blocks:
        # No JWXT blocks in window — could be school break, not an error
        return findings

    jwxt_count = len(jwxt_blocks)

    # If we have an executor, do the full verification
    if executor is not None:
        try:
            result = executor.verify_schedule_mirror(blocks, days=7)
            if result.get("verified", False):
                findings.append({
                    "severity": OK,
                    "message": "课表镜像一致",
                    "detail": f"JWXT {result.get('jwxt_count', jwxt_count)} 条，日历也 {result.get('calendar_count', '?')} 条",
                })
            else:
                missing = result.get("missing_ids", [])
                extra = result.get("extra_ids", [])
                parts = []
                if missing:
                    parts.append(f"日历缺少 {len(missing)} 条课表块")
                if extra:
                    parts.append(f"日历多出 {len(extra)} 条（可能已取消）")
                findings.append({
                    "severity": WARNING,
                    "message": "；".join(parts) or "课表镜像不一致",
                    "detail": f"jwxt={result.get('jwxt_count')} calendar={result.get('calendar_count')}",
                })
        except Exception as exc:
            findings.append({
                "severity": WARNING,
                "message": f"课表镜像审查失败：{exc}",
                "detail": "",
            })
    else:
        # Lightweight local check — just note the count
        findings.append({
            "severity": OK,
            "message": f"课表镜像：JWXT 未来 7 天 {jwxt_count} 条",
            "detail": "",
        })

    return findings


def run_consistency_review(
    state_engine,
    settings=None,
    gc_executor=None,
) -> dict[str, Any]:
    """Run all consistency checks and return composite result.

    Returns:
        dict with:
          - findings: list of {severity, message, detail}
          - overall_severity: ok / warning / error
          - timestamp: ISO timestamp
    """
    all_findings: list[dict[str, str]] = []

    # 1. Calendar sync status
    all_findings.extend(review_sync_status(state_engine))

    # 2. Art block conflicts
    all_findings.extend(review_art_block_conflicts(state_engine))

    # 3. Schedule mirror (if settings enable it)
    if settings is None or getattr(settings, "google_calendar_schedule_write_enabled", False):
        all_findings.extend(review_schedule_mirror(state_engine, executor=gc_executor))

    # Compute overall severity
    max_severity = OK
    for f in all_findings:
        if FINDING_SEVERITY_ORDER.get(f.get("severity", OK), 0) > FINDING_SEVERITY_ORDER.get(max_severity, 0):
            max_severity = f["severity"]

    return {
        "findings": all_findings,
        "overall_severity": max_severity,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def format_review_summary(review_result: dict[str, Any], compact: bool = True) -> str:
    """Format review findings into a compact human-readable string.

    Args:
        review_result: output of run_consistency_review.
        compact: if True, one line per finding; if False, more verbose.

    Returns:
        Multiline string with emoji-severity prefixes.
    """
    findings = review_result.get("findings", [])
    if not findings:
        return "日历一致性审查：无异常"

    severity = review_result.get("overall_severity", OK)
    sev_icon = {"ok": "✓", "warning": "⚠", "error": "✗"}
    icon = sev_icon.get(severity, "?")

    lines = [f"日程审查 {icon}"]
    for f in findings:
        sev = f.get("severity", OK)
        msg = f.get("message", "")
        icon_s = sev_icon.get(sev, "·")
        if compact:
            lines.append(f"{icon_s} {msg}")
        else:
            detail = f.get("detail", "")
            lines.append(f"{icon_s} {msg}" + (f"（{detail}）" if detail else ""))

    return "\n".join(lines)


# ── Repair functions ─────────────────────────────────────────────────────

REPAIRABLE_SOURCES = {"daily_art_plan", "art_planner"}


def _is_repairable_schedule_issue(findings: list[dict]) -> bool:
    """Check if any finding indicates schedule mirror inconsistency that can be repaired."""
    for f in findings:
        msg = f.get("message", "")
        sev = f.get("severity", OK)
        if sev in (WARNING, ERROR) and ("不一致" in msg or "缺少" in msg or "多出" in msg):
            return True
    return False


def _has_repairable_art_conflict(findings: list[dict]) -> bool:
    """Check if any finding indicates art block conflict ready for repair."""
    for f in findings:
        if "冲突" in f.get("message", ""):
            return True
    return False


async def repair_calendar_consistency(
    state_engine,
    settings,
    review_findings: list[dict] | None = None,
    executor=None,
) -> dict[str, Any]:
    """Attempt to auto-fix calendar consistency issues detected by review.

    Safety constraints:
    - Only touches managed_by=cognitive_os events (JWXT mirror or art planner).
    - Never modifies private calendar events.
    - Respects write gates from settings.
    - Does not create calendar events — only sync/delete existing managed ones.

    Args:
        state_engine: for reading temporal blocks state.
        settings: for write gate config.
        review_findings: findings list from run_consistency_review().
            If None, runs an internal quick review first.
        executor: GoogleCalendarExecutor for write operations.
            If None, no writes are performed (dry-run / report-only).

    Returns:
        dict with per-category repair results:
        {
            "schedule_mirror": {"action": str, "created": int, "updated": int,
                                "deleted": int, "error": str | None},
            "art_conflicts": {"action": str, "deleted": int, "note": str | None},
            "sync_stale": {"action": str, "note": str | None},
            "overall": "ok" | "warning" | "error",
        }
    """
    results: dict[str, Any] = {
        "schedule_mirror": {"action": "none", "created": 0, "updated": 0, "deleted": 0},
        "art_conflicts": {"action": "none", "deleted": 0},
        "sync_stale": {"action": "none", "note": None},
        "overall": OK,
    }

    if review_findings is None:
        review = run_consistency_review(state_engine, settings)
        review_findings = review.get("findings", [])

    # 1. JWXT schedule mirror repair
    if _is_repairable_schedule_issue(review_findings):
        can_write = getattr(settings, "google_calendar_schedule_write_enabled", False)
        if not can_write:
            results["schedule_mirror"] = {
                "action": "skipped",
                "created": 0, "updated": 0, "deleted": 0,
                "skipped_reason": "schedule_calendar_write_disabled",
            }
        elif executor is None:
            results["schedule_mirror"] = {
                "action": "skipped",
                "created": 0, "updated": 0, "deleted": 0,
                "skipped_reason": "no_executor",
            }
        else:
            try:
                sync_result = await executor.sync_schedule_blocks(
                    state_engine.get_temporal_blocks(),
                    days=getattr(settings, "google_calendar_schedule_sync_days", 7),
                )
                if sync_result.get("ok"):
                    results["schedule_mirror"] = {
                        "action": "synced",
                        "created": sync_result.get("created", 0),
                        "updated": sync_result.get("updated", 0),
                        "deleted": sync_result.get("deleted", 0),
                    }
                else:
                    err = sync_result.get("error", "unknown")
                    results["schedule_mirror"] = {
                        "action": "failed",
                        "created": 0, "updated": 0, "deleted": 0,
                        "error": err,
                    }
                    results["overall"] = ERROR
            except Exception as exc:
                logger.exception("schedule mirror repair failed")
                results["schedule_mirror"] = {
                    "action": "failed",
                    "created": 0, "updated": 0, "deleted": 0,
                    "error": str(exc),
                }
                results["overall"] = ERROR

    # 2. Art block conflict repair
    if _has_repairable_art_conflict(review_findings):
        can_write = getattr(settings, "google_calendar_write_enabled", False)
        if not can_write:
            results["art_conflicts"] = {
                "action": "skipped",
                "deleted": 0,
                "skipped_reason": "calendar_write_disabled",
            }
        elif executor is None:
            results["art_conflicts"] = {
                "action": "skipped",
                "deleted": 0,
                "skipped_reason": "no_executor",
            }
        else:
            try:
                deleted_count, note = await _delete_conflicting_art_blocks(
                    state_engine, executor, settings,
                )
                if deleted_count > 0:
                    results["art_conflicts"] = {
                        "action": "deleted",
                        "deleted": deleted_count,
                        "note": note or "冲突画画块已删除，需重新生成计划",
                    }
                else:
                    results["art_conflicts"] = {
                        "action": "none",
                        "deleted": 0,
                        "note": note or "未找到需删除的冲突画画块（可能已自动解决）",
                    }
            except Exception as exc:
                logger.exception("art conflict repair failed")
                results["art_conflicts"] = {
                    "action": "failed",
                    "deleted": 0,
                    "error": str(exc),
                }
                results["overall"] = ERROR

    # 3. Sync stale / zero: can't auto-repair, just report
    for f in review_findings:
        msg = f.get("message", "")
        if "0 条" in msg:
            results["sync_stale"] = {
                "action": "reported",
                "note": "同步结果0条，无法自动修复 — 需检查账号/权限/目标日历",
            }
        elif "过时" in msg:
            results["sync_stale"] = {
                "action": "reported",
                "note": "同步数据过时，无法自动修复 — 需手动触发同步",
            }

    return results


async def _delete_conflicting_art_blocks(
    state_engine,
    executor,
    settings,
) -> tuple[int, str | None]:
    """Find and delete managed art blocks that conflict with busy intervals.

    Only touches managed_by=cognitive_os events with source in REPAIRABLE_SOURCES.
    Never modifies private calendar events.

    Returns:
        (deleted_count, note) tuple.
    """
    from src.domain.planning.time_windows import load_busy_intervals, detect_overlap, art_exclude_filter

    blocks = state_engine.get_temporal_blocks()
    now_local = datetime.now(LOCAL_TZ)
    window_end = now_local + timedelta(days=7)

    # Find art blocks in window
    art_in_window = []
    for b in blocks:
        b_start, b_end, source, _bt, meta = _get_block_start_end_local(b)
        if b_start < now_local or b_start >= window_end:
            continue
        if source in REPAIRABLE_SOURCES or meta.get("managed_by") == "cognitive_os":
            art_in_window.append((b, b_start, b_end, source, meta))

    if not art_in_window:
        return 0, "未来7天无画画块"

    # Build busy intervals once (excludes art blocks)
    busy = load_busy_intervals(
        blocks,
        day_start=now_local,
        day_end=window_end,
        exclude_filter=art_exclude_filter,
    )

    # Find which art blocks conflict
    conflicting = []
    for b, a_start, a_end, source, meta in art_in_window:
        if detect_overlap(a_start, a_end, busy):
            conflicting.append((b, a_start, a_end, source, meta))

    if not conflicting:
        return 0, "画画块与 busy 无冲突"

    # Delete the conflicting managed GC events
    deleted_count = 0
    for b, a_start, a_end, source, meta in conflicting:
        # Get managed art GC events in the conflicting window
        events = await executor.list_managed_art_blocks(a_start, a_end)
        for event in events:
            event_id = event.get("id", "")
            if not event_id:
                continue
            result = await executor.delete_managed_art_block(event_id)
            if result.get("ok"):
                deleted_count += 1

    if deleted_count > 0:
        return deleted_count, f"已删除 {deleted_count} 个冲突画画块，需重新生成计划"
    return 0, "未找到可删除的冲突画画块（GC 中无匹配 managed event）"


def format_repair_summary(repair_result: dict[str, Any]) -> str:
    """Format repair results into a short human-readable string.

    Args:
        repair_result: output of repair_calendar_consistency.

    Returns:
        Single-line summary string, or empty string if nothing was done.
    """
    parts = []

    sm = repair_result.get("schedule_mirror", {})
    sm_action = sm.get("action", "none")
    if sm_action == "synced":
        parts.append(
            f"课表已修正：新增 {sm.get('created', 0)}，"
            f"更新 {sm.get('updated', 0)}，"
            f"删除 {sm.get('deleted', 0)}"
        )
    elif sm_action == "skipped":
        reason = sm.get("skipped_reason", "")
        if "disabled" in reason:
            parts.append("课表修正跳过（写入未开启）")
    elif sm_action == "failed":
        parts.append(f"课表修正失败：{sm.get('error', '未知错误')}")

    ac = repair_result.get("art_conflicts", {})
    ac_action = ac.get("action", "none")
    if ac_action == "deleted":
        parts.append(f"冲突画画块已删除 {ac.get('deleted', 0)} 个")
    elif ac_action == "skipped":
        parts.append("画画冲突修复跳过（写入未开启）")
    elif ac_action == "failed":
        parts.append(f"画画冲突修复失败：{ac.get('error', '未知错误')}")

    ss = repair_result.get("sync_stale", {})
    ss_action = ss.get("action", "none")
    if ss_action == "reported":
        parts.append(f"注意：{ss.get('note', '')}")

    if not parts:
        return ""

    return "\n".join(["修正摘要"] + [f"• {p}" for p in parts])
