"""Momo (Vocabulary) Connector — reads Momo cache, emits vocab events.

Read-only. Never prints bearer tokens or full cache.
On npm/API failure, falls back to reading cache and marks stale.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from src.core.events import Event, EventType, AggregateType

logger = logging.getLogger(__name__)

FORGET_RESPONSES = {"FORGET", "VAGUE"}
STICKING_TAG = "STICKING"


async def fetch_momo_vocab(
    settings: Any,
    event_id: Any = None,
) -> list[Event]:
    """Fetch vocab data from Momo sync project.

    If npm run sync succeeds, reads fresh cache.
    If npm unavailable or cache exists, falls back to existing cache with stale flag.
    Never prints bearer token or full cache content.
    """
    produced: list[Event] = []
    project_path = Path(settings.momo_sync_project_path)
    cache_path = Path(settings.momo_cache_path)
    enabled = bool(getattr(settings, "momo_sync_enabled", True))
    stale_minutes = int(getattr(settings, "momo_stale_after_minutes", 90))
    causation = event_id

    if not enabled:
        logger.info("[MOMO] sync disabled, skipping")
        return []

    cache_before = None
    if cache_path.exists():
        try:
            cache_before = json.loads(cache_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("[MOMO] failed to read existing cache: %s", exc)

    # Emit sync started
    produced.append(Event(
        event_type=EventType.VOCAB_SYNC_STARTED,
        aggregate_id="momo",
        aggregate_type=AggregateType.VOCAB,
        timestamp=datetime.now(timezone.utc),
        causation_id=causation,
        payload={"source": "momo_vocab"},
    ))

    npm_ok = False
    npm_timeout = int(getattr(settings, "momo_sync_timeout_seconds", 8))
    if project_path.exists() and (project_path / "package.json").exists():
        try:
            npm_bin = shutil.which("npm.cmd") or shutil.which("npm") or "npm"
            proc = await asyncio.create_subprocess_exec(
                npm_bin, "run", "sync",
                cwd=str(project_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=max(npm_timeout, 1))
            out_tail = stdout.decode("utf-8", errors="ignore")[-500:]
            err_tail = stderr.decode("utf-8", errors="ignore")[-500:]
            if proc.returncode == 0:
                npm_ok = True
                logger.info("[MOMO] npm sync completed successfully")
            else:
                logger.warning(
                    "[MOMO] npm sync exited with code %s stdout_tail=%r stderr_tail=%r",
                    proc.returncode,
                    out_tail,
                    err_tail,
                )
        except asyncio.TimeoutError:
            logger.warning("[MOMO] npm sync timed out after %ss", npm_timeout)
            try:
                proc.kill()
            except Exception:
                pass
        except (FileNotFoundError, OSError, TypeError) as exc:
            logger.warning("[MOMO] npm sync failed: %s", exc)

    # Read cache (post-sync or fallback)
    cache = None
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("[MOMO] failed to parse cache: %s", exc)

    if cache is None and cache_before is not None:
        cache = cache_before
        logger.info("[MOMO] falling back to pre-sync cache")

    if cache is None:
        produced.append(Event(
            event_type=EventType.VOCAB_SYNC_FAILED,
            aggregate_id="momo",
            aggregate_type=AggregateType.VOCAB,
            causation_id=causation,
            payload={"error": "No cache data available. Is the Momo sync project configured?", "source": "momo_vocab"},
        ))
        return produced

    # Compute staleness
    last_sync_str = cache.get("last_sync", "")
    stale = True
    if last_sync_str:
        try:
            last_sync_dt = datetime.fromisoformat(last_sync_str.replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - last_sync_dt).total_seconds() / 60
            stale = age > stale_minutes
        except (ValueError, TypeError):
            pass

    # Extract progress
    progress = cache.get("progress", {})
    finished = int(progress.get("finished", 0) or 0)
    total = int(progress.get("total", 0) or 0)
    study_time = int(progress.get("study_time", 0) or 0)

    remaining = max(0, total - finished) if total > 0 else 0

    # Today items
    today_items = cache.get("today_items", []) or []
    today_finished = sum(1 for item in today_items if item.get("is_finished"))
    today_total = len(today_items)
    today_remaining = today_total - today_finished
    today_new = sum(1 for item in today_items if item.get("is_new"))
    today_new_remaining = sum(1 for item in today_items if item.get("is_new") and not item.get("is_finished"))
    today_review = today_total - today_new
    today_review_remaining = today_remaining - today_new_remaining

    # Forgetting / sticking
    records = cache.get("study_records", []) or []
    forgetting_count = sum(
        1 for r in records
        if r.get("last_response") in FORGET_RESPONSES
    )
    sticking_count = sum(
        1 for r in records
        if STICKING_TAG in (r.get("tags") or [])
    )

    # Slack detection
    slack = False
    if stale:
        slack = True
    if today_total > 0 and today_finished == 0:
        now_utc = datetime.now(timezone.utc)
        local_hour = now_utc.hour + 8  # approximate Asia/Singapore
        if local_hour >= 20 or local_hour <= 6:
            slack = True

    payload = {
        "source": "momo_vocab",
        "npm_sync_ok": npm_ok,
        "last_sync": last_sync_str,
        "stale": stale,
        "progress": {"finished": finished, "total": total, "study_time": study_time, "remaining": remaining},
        "today": {
            "total": today_total,
            "finished": today_finished,
            "remaining": today_remaining,
            "new_remaining": today_new_remaining,
            "review_remaining": today_review_remaining,
        },
        "forgetting_count": forgetting_count,
        "sticking_count": sticking_count,
        "slack": slack,
    }

    if npm_ok:
        produced.append(Event(
            event_type=EventType.VOCAB_SYNC_COMPLETED,
            aggregate_id="momo",
            aggregate_type=AggregateType.VOCAB,
            causation_id=causation,
            payload=payload,
        ))
    else:
        produced.append(Event(
            event_type=EventType.VOCAB_SYNC_FAILED,
            aggregate_id="momo",
            aggregate_type=AggregateType.VOCAB,
            causation_id=causation,
            payload={**payload, "error": "npm sync failed, using cached data (may be stale)"},
        ))

    # Emit progress update
    produced.append(Event(
        event_type=EventType.VOCAB_PROGRESS_UPDATED,
        aggregate_id="momo",
        aggregate_type=AggregateType.VOCAB,
        causation_id=causation,
        payload=payload,
    ))

    if slack:
        produced.append(Event(
            event_type=EventType.VOCAB_SLACK_DETECTED,
            aggregate_id="momo",
            aggregate_type=AggregateType.VOCAB,
            causation_id=causation,
            payload=payload,
        ))

    return produced
