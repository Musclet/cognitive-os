"""Wake the Render Web service and request one protected cloud sync."""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request


DEFAULT_URL = "https://cognitive-os.onrender.com/api/internal/cloud-sync"
RETRY_DELAYS = (0, 15, 30)


def _request_sync(url: str, token: str, timeout: int) -> tuple[int, dict]:
    request = urllib.request.Request(
        url,
        data=b"{}",
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Cloud-Sync-Token": token,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            return response.status, json.loads(body.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            body = {"detail": f"http_{exc.code}"}
        return exc.code, body


def _safe_report(payload: dict) -> None:
    print(f"cloud_sync_status={payload.get('status', 'unknown')}")
    sources = payload.get("sources", {})
    if not isinstance(sources, dict):
        return
    for source in ("jwxt", "chaoxing", "google_calendar"):
        result = sources.get(source, {})
        if not isinstance(result, dict):
            continue
        print(
            "source=%s status=%s count=%s error_code=%s"
            % (
                source,
                result.get("status", "unknown"),
                result.get("count", 0),
                result.get("error_code", ""),
            )
        )


def main() -> int:
    url = os.environ.get("CLOUD_SYNC_URL", DEFAULT_URL).strip()
    token = os.environ.get("CLOUD_SYNC_TOKEN", "").strip()
    timeout = max(30, int(os.environ.get("CLOUD_SYNC_HTTP_TIMEOUT_SECONDS", "720")))

    if not token:
        print("cloud_sync_error=missing_cloud_sync_token")
        return 2

    for attempt, delay in enumerate(RETRY_DELAYS, start=1):
        if delay:
            time.sleep(delay)
        try:
            status_code, payload = _request_sync(url, token, timeout)
        except (OSError, TimeoutError, json.JSONDecodeError) as exc:
            print(
                "cloud_sync_attempt=%d result=network_error error_type=%s"
                % (attempt, type(exc).__name__)
            )
            continue

        if status_code == 200:
            _safe_report(payload)
            return 0 if payload.get("ok") is True else 1

        detail = str(payload.get("detail", f"http_{status_code}"))
        print(
            "cloud_sync_attempt=%d result=http_error status=%d detail=%s"
            % (attempt, status_code, detail[:80])
        )
        if status_code in {401, 409}:
            return 1

    print("cloud_sync_error=retry_exhausted")
    return 1


if __name__ == "__main__":
    sys.exit(main())
