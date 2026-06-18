from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from src.connector.jwxt.client import JwxtConnector, JwxtSyncError
from src.core.events import AggregateType, Event, EventType
from src.core.state_engine import StateEngine
from src.domain.dashboard.query import build_dashboard
from src.infrastructure.config import Settings

SENTINEL_USERNAME = "DO_NOT_LEAK_JWXT_USERNAME"
SENTINEL_PASSWORD = "DO_NOT_LEAK_JWXT_PASSWORD"
SENTINEL_COOKIE = "DO_NOT_LEAK_JWXT_COOKIE"
LOCAL_TZ = timezone(timedelta(hours=8))


def _settings(tmp_path, *, username: str = "", password: str = "") -> Settings:
    return Settings(
        _env_file=None,
        jwxt_mock=False,
        jwxt_username=username,
        jwxt_password=password,
        jwxt_cookies_path=str(tmp_path / "jwxt-cookies.json"),
        jwxt_semester_start=(
            datetime.now(LOCAL_TZ).date()
            - timedelta(days=datetime.now(LOCAL_TZ).date().weekday())
        ).isoformat(),
        jwxt_schedule_window_days=14,
    )


def _fetch_event() -> Event:
    return Event(
        event_type=EventType.CONNECTOR_FETCH_REQUESTED,
        aggregate_id="jwxt-test",
        aggregate_type=AggregateType.SYSTEM,
        payload={
            "source": "jwxt",
            "query": "weekly_schedule",
            "intent": "schedule_daily_sync",
        },
    )


def _write_cookie_file(settings: Settings) -> None:
    cookie_path = settings.jwxt_cookies_path
    with open(cookie_path, "w", encoding="utf-8") as handle:
        json.dump([{"name": "session", "value": SENTINEL_COOKIE}], handle)


class _Response:
    def __init__(
        self,
        status_code: int,
        *,
        location: str = "",
        text: str = "",
        data: object | None = None,
    ) -> None:
        self.status_code = status_code
        self.headers = {"location": location} if location else {}
        self.text = text
        self._data = data

    def json(self):
        if isinstance(self._data, Exception):
            raise self._data
        return self._data


@pytest.mark.asyncio
async def test_missing_cookie_and_credentials_returns_credentials_missing(tmp_path):
    connector = JwxtConnector(use_mock=False, settings=_settings(tmp_path))

    events = await connector.handle_fetch_request(_fetch_event())

    failed = next(
        event for event in events
        if event.event_type == EventType.CONNECTOR_FETCH_FAILED
    )
    assert failed.payload["error_code"] == "jwxt_credentials_missing"
    assert failed.payload["success"] is False


def test_http_302_to_login_is_cookie_expired(tmp_path):
    connector = JwxtConnector(use_mock=False, settings=_settings(tmp_path))

    with pytest.raises(JwxtSyncError) as exc_info:
        connector._decode_schedule_response(
            _Response(
                302,
                location="/xtgl/login_slogin.html",
            )
        )

    assert exc_info.value.error_code == "jwxt_cookie_expired"


@pytest.mark.asyncio
async def test_expired_cookie_without_credentials_returns_credentials_missing(tmp_path):
    settings = _settings(tmp_path)
    _write_cookie_file(settings)
    connector = JwxtConnector(use_mock=False, settings=settings)
    connector._fetch_schedule_api_httpx = AsyncMock(
        side_effect=JwxtSyncError("jwxt_cookie_expired")
    )

    events = await connector.handle_fetch_request(_fetch_event())

    failed = next(
        event for event in events
        if event.event_type == EventType.CONNECTOR_FETCH_FAILED
    )
    assert failed.payload["error_code"] == "jwxt_credentials_missing"


@pytest.mark.asyncio
async def test_expired_cookie_with_credentials_refreshes_and_retries(tmp_path):
    settings = _settings(
        tmp_path,
        username=SENTINEL_USERNAME,
        password=SENTINEL_PASSWORD,
    )
    _write_cookie_file(settings)
    connector = JwxtConnector(use_mock=False, settings=settings)
    connector._fetch_schedule_api_httpx = AsyncMock(side_effect=[
        JwxtSyncError("jwxt_cookie_expired"),
        {"kbList": []},
    ])
    connector._refresh_cookies_with_playwright = AsyncMock(return_value=None)

    result = await connector._fetch_schedule_api()

    assert result == {"kbList": []}
    assert connector._fetch_schedule_api_httpx.await_count == 2
    connector._refresh_cookies_with_playwright.assert_awaited_once()
    assert connector._last_auto_login_attempted is True


@pytest.mark.asyncio
async def test_login_page_requiring_user_action_is_structured(tmp_path):
    settings = _settings(
        tmp_path,
        username=SENTINEL_USERNAME,
        password=SENTINEL_PASSWORD,
    )
    _write_cookie_file(settings)
    connector = JwxtConnector(use_mock=False, settings=settings)
    connector._fetch_schedule_api_httpx = AsyncMock(
        side_effect=JwxtSyncError("jwxt_cookie_expired")
    )
    connector._refresh_cookies_with_playwright = AsyncMock(
        side_effect=JwxtSyncError("jwxt_auth_requires_user_action")
    )

    events = await connector.handle_fetch_request(_fetch_event())

    failed = next(
        event for event in events
        if event.event_type == EventType.CONNECTOR_FETCH_FAILED
    )
    assert failed.payload["error_code"] == "jwxt_auth_requires_user_action"


@pytest.mark.asyncio
async def test_login_failure_does_not_leak_credentials_or_cookie(
    tmp_path,
    caplog,
):
    caplog.set_level(logging.INFO)
    settings = _settings(
        tmp_path,
        username=SENTINEL_USERNAME,
        password=SENTINEL_PASSWORD,
    )
    _write_cookie_file(settings)
    connector = JwxtConnector(use_mock=False, settings=settings)
    connector._fetch_schedule_api_httpx = AsyncMock(
        side_effect=JwxtSyncError("jwxt_cookie_expired")
    )
    connector._refresh_cookies_with_playwright = AsyncMock(
        side_effect=RuntimeError(
            f"{SENTINEL_USERNAME} {SENTINEL_PASSWORD} {SENTINEL_COOKIE}"
        )
    )

    events = await connector.handle_fetch_request(_fetch_event())
    failed = next(
        event for event in events
        if event.event_type == EventType.CONNECTOR_FETCH_FAILED
    )
    rendered = f"{failed.payload} {caplog.text}"

    assert failed.payload["error_code"] == "jwxt_login_failed"
    assert SENTINEL_USERNAME not in rendered
    assert SENTINEL_PASSWORD not in rendered
    assert SENTINEL_COOKIE not in rendered
    assert "cookie_present=True" in caplog.text
    assert "credentials_present=True" in caplog.text
    assert "auto_login_attempted=True" in caplog.text


@pytest.mark.asyncio
async def test_successful_real_sync_creates_temporal_blocks_for_dashboard(tmp_path):
    settings = _settings(
        tmp_path,
        username=SENTINEL_USERNAME,
        password=SENTINEL_PASSWORD,
    )
    connector = JwxtConnector(use_mock=False, settings=settings)
    today = datetime.now(LOCAL_TZ)
    weekday_name = [
        "星期一",
        "星期二",
        "星期三",
        "星期四",
        "星期五",
        "星期六",
        "星期日",
    ][today.weekday()]
    connector._fetch_schedule_api = AsyncMock(return_value={
        "kbList": [{
            "kcmc": "自动同步课程",
            "xm": "测试教师",
            "zcd": "1周",
            "xqjmc": weekday_name,
            "jcs": "1-2",
            "cdmc": "A101",
        }]
    })

    events = await connector.handle_fetch_request(_fetch_event())
    engine = StateEngine()
    for event in events:
        await engine.apply(event)

    temporal_blocks = engine.get_temporal_blocks()
    dashboard = build_dashboard(engine, settings, as_of=today)
    completed = next(
        event for event in events
        if event.event_type == EventType.CONNECTOR_FETCH_COMPLETED
    )

    assert len(temporal_blocks) == 1
    assert completed.payload["temporal_blocks_count"] == 1
    assert completed.payload["pulled_count"] == 1
    assert dashboard["schedule_count"] == 1
    assert dashboard["today_schedule"][0]["course"] == "自动同步课程"
