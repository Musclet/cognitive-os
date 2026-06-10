"""Parent fund scheduler — deterministic rules for requesting money from parents.

Key rules:
- Safe interval between ordinary requests: ~3 days.
- Single amount above 75 RMB is risky/refused.
- Weekly total above 300 RMB is dangerous.
- Art supplies/books/courses = legitimate learning investment (ok to request).
- Fixed recurring items have due dates; they remain in queue even after ad hoc.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

LOCAL_TZ = timezone.utc  # caller provides local-aware datetimes

# ── Default fixed recurring items ────────────────────────────────────────────

DEFAULT_FIXED_ITEMS: list[dict[str, Any]] = [
    {"item_id": "phone_bill", "label": "话费", "amount": 100, "interval_days": 30, "start_offset_days": 1},
    {"item_id": "internet", "label": "网费", "amount": 60, "interval_days": 30, "start_offset_days": 1},
    {"item_id": "metro", "label": "地铁", "amount": 50, "interval_days": 30, "start_offset_days": 1},
    {"item_id": "water", "label": "水费", "amount": 100, "interval_days": 30, "start_offset_days": 1},
    {"item_id": "paper", "label": "纸", "amount": 35, "interval_days": 30, "start_offset_days": 1},
    {"item_id": "haircut", "label": "剪头发", "amount": 35, "interval_days": 15, "start_offset_days": 1},
    {"item_id": "acne_meds", "label": "祛痘药", "amount": 60, "interval_days": 60, "start_offset_days": 1},
    {"item_id": "toiletries", "label": "洗发水沐浴露", "amount": 60, "interval_days": 90, "start_offset_days": 1},
    {"item_id": "book", "label": "书", "amount": 0, "interval_days": 45, "start_offset_days": 1},
]


def compute_next_eligible_date(
    last_request_date: datetime | None,
    safe_interval_days: int = 3,
    now: datetime | None = None,
) -> datetime:
    """Compute the earliest safe date for the next parent fund request.

    If there is no prior request, returns now (immediately eligible).
    """
    if now is None:
        now = datetime.now(LOCAL_TZ)
    if last_request_date is None:
        return now
    candidate = last_request_date + timedelta(days=safe_interval_days)
    return max(candidate, now)


def compute_weekly_total(
    request_log: list[dict[str, Any]],
    now: datetime | None = None,
) -> float:
    """Sum all parent-fund requests in the current 7-day rolling window."""
    if now is None:
        now = datetime.now(LOCAL_TZ)
    week_ago = now - timedelta(days=7)
    total = 0.0
    for entry in request_log:
        dt_str = entry.get("timestamp", "")
        if not dt_str:
            continue
        try:
            dt = datetime.fromisoformat(dt_str)
        except (ValueError, TypeError):
            continue
        if dt >= week_ago:
            total += float(entry.get("amount", 0))
    return total


def compute_due_items(
    fixed_items: list[dict[str, Any]],
    request_log: list[dict[str, Any]],
    received_log: list[dict[str, Any]],
    last_request_date: datetime | None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Determine which fixed recurring items are due.

    An item is due if the last request/received for that item_id
    was more than interval_days ago, or if it has never been requested.
    For book, due status requires a configured amount > 0.
    """
    if now is None:
        now = datetime.now(LOCAL_TZ)

    # Build lookup: item_id -> latest timestamp from request or received
    last_per_item: dict[str, datetime] = {}
    for entry in request_log:
        iid = entry.get("item_id", "")
        ts = entry.get("timestamp", "")
        if iid and ts:
            try:
                dt = datetime.fromisoformat(ts)
            except (ValueError, TypeError):
                continue
            if iid not in last_per_item or dt > last_per_item[iid]:
                last_per_item[iid] = dt
    for entry in received_log:
        iid = entry.get("item_id", "")
        ts = entry.get("timestamp", "")
        if iid and ts:
            try:
                dt = datetime.fromisoformat(ts)
            except (ValueError, TypeError):
                continue
            if iid not in last_per_item or dt > last_per_item[iid]:
                last_per_item[iid] = dt

    due: list[dict[str, Any]] = []
    for order, item in enumerate(fixed_items):
        # Book with amount 0 = not configured yet → skip
        if item["item_id"] == "book" and item.get("amount", 0) <= 0:
            continue
        last_dt = last_per_item.get(item["item_id"])
        interval = timedelta(days=item["interval_days"])
        if last_dt is None:
            # Never requested → due now
            start = item.get("start_offset_days", 0)
            due_date = now + timedelta(days=start)
            due.append({**item, "due_date": due_date.isoformat()})
        elif now - last_dt >= interval:
            due.append({**item, "due_date": now.isoformat()})

    return due


def compute_30_day_request_schedule(
    fixed_items: list[dict[str, Any]],
    request_log: list[dict[str, Any]],
    received_log: list[dict[str, Any]],
    planned_requests: list[dict[str, Any]] | None = None,
    safe_interval_days: int = 3,
    now: datetime | None = None,
    horizon_days: int = 30,
) -> list[dict[str, Any]]:
    """Schedule fixed parent-fund requests in the next horizon.

    Fixed needs are not dropped after ad hoc requests. They are placed no
    earlier than their due date and no earlier than the next safe request date,
    then spaced by safe_interval_days.
    """
    if now is None:
        now = datetime.now(LOCAL_TZ)
    horizon_end = now + timedelta(days=horizon_days)

    last_per_item: dict[str, datetime] = {}
    for entry in [*request_log, *received_log]:
        iid = entry.get("item_id", "")
        ts = entry.get("timestamp", "")
        if not iid or not ts:
            continue
        try:
            dt = datetime.fromisoformat(ts)
        except (ValueError, TypeError):
            continue
        if iid not in last_per_item or dt > last_per_item[iid]:
            last_per_item[iid] = dt

    upcoming: list[dict[str, Any]] = []
    for order, item in enumerate(fixed_items):
        if item.get("item_id") == "book" and item.get("amount", 0) <= 0:
            continue

        last_dt = last_per_item.get(item.get("item_id", ""))
        if last_dt is None:
            due_date = now + timedelta(days=int(item.get("start_offset_days", 0)))
        else:
            due_date = last_dt + timedelta(days=int(item.get("interval_days", 30)))

        if due_date < now:
            due_date = now

        if due_date <= horizon_end:
            upcoming.append({**item, "due_date": due_date, "_order": order})

    upcoming.sort(key=lambda x: (x["due_date"], x.get("_order", 0)))

    reservations: list[dict[str, Any]] = []
    for order, entry in enumerate(planned_requests or []):
        requested = entry.get("requested_date") or entry.get("request_date")
        if not requested:
            continue
        try:
            requested_dt = datetime.fromisoformat(str(requested))
        except (ValueError, TypeError):
            continue
        if requested_dt < now:
            requested_dt = now
        if requested_dt <= horizon_end:
            label = entry.get("description") or entry.get("label") or "预定要钱"
            reservations.append({
                "item_id": entry.get("item_id"),
                "label": label,
                "amount": float(entry.get("amount", 0)),
                "due_date": requested_dt,
                "_order": order,
                "planned": True,
            })

    reservations.sort(key=lambda x: (x["due_date"], x.get("_order", 0)))

    cursor = compute_next_safe_date(request_log, safe_interval_days, now)
    scheduled: list[dict[str, Any]] = []
    reserve_idx = 0
    interval = timedelta(days=safe_interval_days)

    def append_reservation(reservation: dict[str, Any], cursor_dt: datetime) -> datetime:
        request_date = max(reservation["due_date"], cursor_dt)
        scheduled.append({
            **reservation,
            "due_date": reservation["due_date"].isoformat(),
            "request_date": request_date.isoformat(),
            "pushed": request_date.date() > reservation["due_date"].date(),
        })
        return request_date + interval

    for item in upcoming:
        target_date = max(item["due_date"], cursor)
        while reserve_idx < len(reservations) and reservations[reserve_idx]["due_date"] < target_date:
            cursor = append_reservation(reservations[reserve_idx], cursor)
            reserve_idx += 1
            target_date = max(item["due_date"], cursor)

        request_date = max(item["due_date"], cursor)
        while (
            reserve_idx < len(reservations)
            and request_date <= reservations[reserve_idx]["due_date"] < request_date + interval
        ):
            cursor = append_reservation(reservations[reserve_idx], cursor)
            reserve_idx += 1
            request_date = max(item["due_date"], cursor)

        scheduled.append({
            **item,
            "due_date": item["due_date"].isoformat(),
            "request_date": request_date.isoformat(),
            "pushed": request_date.date() > item["due_date"].date(),
        })
        cursor = request_date + interval

    while reserve_idx < len(reservations):
        cursor = append_reservation(reservations[reserve_idx], cursor)
        reserve_idx += 1

    scheduled.sort(key=lambda x: (x["request_date"], 0 if x.get("planned") else 1))

    return scheduled


def schedule_request_advice(
    amount: float,
    description: str,
    category: str,
    fixed_items: list[dict[str, Any]],
    request_log: list[dict[str, Any]],
    received_log: list[dict[str, Any]],
    last_request_date: datetime | None,
    safe_interval_days: int = 3,
    single_risk_threshold: float = 75,
    weekly_risk_threshold: float = 300,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Generate advice about a planned parent fund request.

    Returns dict with:
      - safe: bool — whether requesting now is recommended
      - reason: str — explanation
      - recommended_date: str (ISO) — when to ask
      - split_suggestion: list[dict] | None — if amount > threshold, suggested chunks
      - warnings: list[str]
      - due_items: list[dict] — fixed items currently due
    """
    if now is None:
        now = datetime.now(LOCAL_TZ)

    warnings: list[str] = []
    split_suggestion: list[dict[str, Any]] | None = None

    # 1. Check if this is a fixed due item
    due_items = compute_due_items(fixed_items, request_log, received_log, last_request_date, now)

    # If it matches a fixed due item, treat as legitimate
    is_fixed_due = any(
        item["label"] in description or item["item_id"] in description
        for item in due_items
    )

    # 2. Check safe interval
    eligible_date = compute_next_eligible_date(last_request_date, safe_interval_days, now)
    now_eligible = eligible_date <= now

    # 3. Check weekly total
    weekly_total = compute_weekly_total(request_log, now)
    if weekly_total + amount > weekly_risk_threshold and not is_fixed_due:
        warnings.append(f"本周已请求 {weekly_total:.0f} 元，加上这笔 {amount:.0f} 元将超过 {weekly_risk_threshold:.0f} 元的周上限，建议延后。")

    # 4. Check single amount threshold
    if amount > single_risk_threshold and not is_fixed_due:
        # Suggest split
        full_chunks = int(amount // single_risk_threshold)
        remainder = amount % single_risk_threshold
        chunks: list[dict[str, Any]] = []
        chunk_date = eligible_date
        for i in range(full_chunks):
            chunks.append({
                "amount": single_risk_threshold,
                "date": chunk_date.isoformat(),
                "label": f"第{i+1}笔（共{full_chunks + (1 if remainder > 0 else 0)}笔）",
            })
            chunk_date += timedelta(days=safe_interval_days)
        if remainder > 0:
            chunks.append({
                "amount": remainder,
                "date": chunk_date.isoformat(),
                "label": f"第{full_chunks + 1}笔（共{full_chunks + 1}笔）",
            })
        split_suggestion = chunks
        warnings.append(f"单笔 {amount:.0f} 元超过 {single_risk_threshold:.0f} 元的建议上限，可能被拒绝。建议分 {len(chunks)} 笔要。")

    # 5. Build result
    if is_fixed_due:
        safe = True
        reason = f"这是固定支出（{description}），可以按时请求。"
    elif not now_eligible:
        safe = False
        reason = f"距上次请求不足 {safe_interval_days} 天，建议等到 {eligible_date.strftime('%m月%d日')} 再要。"
    elif warnings:
        safe = False
        reason = "当前请求有风险。" if not split_suggestion else f"当前请求有风险，建议分笔要。"
    else:
        safe = True
        reason = "可以请求。"

    return {
        "safe": safe,
        "reason": reason,
        "recommended_date": eligible_date.isoformat(),
        "split_suggestion": split_suggestion,
        "warnings": warnings,
        "due_items": due_items,
    }


def apply_request_record(
    request_log: list[dict[str, Any]],
    amount: float,
    description: str,
    item_id: str | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Append a new request record and return updated log.

    Each entry: {amount, description, timestamp, item_id (optional)}.
    """
    if now is None:
        now = datetime.now(LOCAL_TZ)
    entry: dict[str, Any] = {
        "amount": amount,
        "description": description,
        "timestamp": now.isoformat(),
    }
    if item_id:
        entry["item_id"] = item_id
    updated = list(request_log)
    updated.append(entry)
    return updated


def compute_next_safe_date(
    request_log: list[dict[str, Any]],
    safe_interval_days: int = 3,
    now: datetime | None = None,
) -> datetime:
    """Compute next safe date based on the most recent actual request."""
    if now is None:
        now = datetime.now(LOCAL_TZ)
    last_date = None
    for entry in reversed(request_log):
        ts = entry.get("timestamp", "")
        if ts:
            try:
                last_date = datetime.fromisoformat(ts)
                break
            except (ValueError, TypeError):
                continue
    return compute_next_eligible_date(last_date, safe_interval_days, now)
