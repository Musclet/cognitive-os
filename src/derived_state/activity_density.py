"""Activity density derived state — pure function on state dict."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any


def compute_activity_density(
    state: dict[str, Any],
    *,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Compute activity density from notification history.

    Input: state dict from StateEngine
    Output: {events_last_hour, events_last_24h, score}
    """
    notification_state = state.get("notification", {})
    now = as_of or datetime.now(timezone.utc)
    hour_ago = now - timedelta(hours=1)
    day_ago = now - timedelta(hours=24)

    last_hour = 0
    last_24h = 0

    for agg_id, view in notification_state.items():
        history = view.get("history", [])
        for entry in history:
            sent_at_str = entry.get("sent_at", "")
            try:
                sent_at = datetime.fromisoformat(sent_at_str)
                if sent_at.tzinfo is None:
                    sent_at = sent_at.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                continue

            if sent_at >= hour_ago:
                last_hour += 1
            if sent_at >= day_ago:
                last_24h += 1

    # Score: events in 24h / threshold (threshold = 20)
    score = min(last_24h / 20.0, 1.0)

    return {
        "events_last_hour": last_hour,
        "events_last_24h": last_24h,
        "score": round(score, 3),
    }
