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


# ══════════════════════════════════════════════════════════════════════════════
# Login error classification (static / pure-function tests)
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "error_text, expected_code",
    [
        ("用户名或密码错误", "jwxt_invalid_credentials"),
        ("账号或密码错误，请重新输入", "jwxt_invalid_credentials"),
        ("密码或用户名不正确", "jwxt_invalid_credentials"),
        ("密码错误", "jwxt_invalid_password"),
        ("您输入的密码不正确", "jwxt_invalid_password"),
        ("密码有误，请重试", "jwxt_invalid_password"),
        ("用户不存在", "jwxt_invalid_username"),
        ("账号不存在", "jwxt_invalid_username"),
        ("用户名不存在，请检查", "jwxt_invalid_username"),
        ("学号不存在或未注册", "jwxt_invalid_username"),
        ("账号已被锁定", "jwxt_account_locked"),
        ("您的账户已被禁用", "jwxt_account_locked"),
        ("账号已被冻结，请联系管理员", "jwxt_account_locked"),
        ("密码已过期", "jwxt_password_expired"),
        ("密码已失效，请修改", "jwxt_password_expired"),
        ("密码到期请更新", "jwxt_password_expired"),
        ("请输入验证码", "jwxt_auth_requires_user_action"),
        ("验证码错误", "jwxt_auth_requires_user_action"),
        ("系统维护中", ""),
        ("网络异常，请稍后重试", ""),
        ("", ""),
    ],
)
def test_classify_login_error(error_text, expected_code):
    assert JwxtConnector._classify_login_error(error_text) == expected_code


def test_classify_login_error_precedence_password_over_credentials():
    """密码错误 should be classified as jwxt_invalid_password,
    even if it contains 密码 which also appears in 用户名或密码错误."""
    assert JwxtConnector._classify_login_error("密码错误") == "jwxt_invalid_password"


def test_classify_login_error_precedence_username_over_credentials():
    """用户不存在 should be classified as jwxt_invalid_username,
    not jwxt_invalid_credentials."""
    assert JwxtConnector._classify_login_error("用户不存在") == "jwxt_invalid_username"


# ══════════════════════════════════════════════════════════════════════════════
# Full-flow tests — new error codes flow through handle_fetch_request
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_login_failure_with_error_text_classifies_invalid_credentials(tmp_path):
    """When login page shows '用户名或密码错误', the connector reports
    jwxt_invalid_credentials."""
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
        side_effect=JwxtSyncError("jwxt_invalid_credentials")
    )

    events = await connector.handle_fetch_request(_fetch_event())

    failed = next(
        event for event in events
        if event.event_type == EventType.CONNECTOR_FETCH_FAILED
    )
    assert failed.payload["error_code"] == "jwxt_invalid_credentials"


@pytest.mark.asyncio
async def test_login_failure_with_error_text_classifies_invalid_password(tmp_path):
    """When login page shows '密码错误', the connector reports
    jwxt_invalid_password."""
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
        side_effect=JwxtSyncError("jwxt_invalid_password")
    )

    events = await connector.handle_fetch_request(_fetch_event())

    failed = next(
        event for event in events
        if event.event_type == EventType.CONNECTOR_FETCH_FAILED
    )
    assert failed.payload["error_code"] == "jwxt_invalid_password"


@pytest.mark.asyncio
async def test_login_failure_with_error_text_classifies_invalid_username(tmp_path):
    """When login page shows '用户不存在', the connector reports
    jwxt_invalid_username."""
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
        side_effect=JwxtSyncError("jwxt_invalid_username")
    )

    events = await connector.handle_fetch_request(_fetch_event())

    failed = next(
        event for event in events
        if event.event_type == EventType.CONNECTOR_FETCH_FAILED
    )
    assert failed.payload["error_code"] == "jwxt_invalid_username"


@pytest.mark.asyncio
async def test_login_failure_with_error_text_classifies_account_locked(tmp_path):
    """When login page shows account locked text, the connector reports
    jwxt_account_locked."""
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
        side_effect=JwxtSyncError("jwxt_account_locked")
    )

    events = await connector.handle_fetch_request(_fetch_event())

    failed = next(
        event for event in events
        if event.event_type == EventType.CONNECTOR_FETCH_FAILED
    )
    assert failed.payload["error_code"] == "jwxt_account_locked"


@pytest.mark.asyncio
async def test_login_failure_with_error_text_classifies_password_expired(tmp_path):
    """When login page shows password expired text, the connector reports
    jwxt_password_expired."""
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
        side_effect=JwxtSyncError("jwxt_password_expired")
    )

    events = await connector.handle_fetch_request(_fetch_event())

    failed = next(
        event for event in events
        if event.event_type == EventType.CONNECTOR_FETCH_FAILED
    )
    assert failed.payload["error_code"] == "jwxt_password_expired"


@pytest.mark.asyncio
async def test_login_failure_with_error_text_classifies_unknown(tmp_path):
    """When login page has no recognizable error text, the connector reports
    jwxt_login_failed_unknown."""
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
        side_effect=JwxtSyncError("jwxt_login_failed_unknown")
    )

    events = await connector.handle_fetch_request(_fetch_event())

    failed = next(
        event for event in events
        if event.event_type == EventType.CONNECTOR_FETCH_FAILED
    )
    assert failed.payload["error_code"] == "jwxt_login_failed_unknown"


@pytest.mark.asyncio
async def test_new_error_codes_do_not_leak_sentinels(tmp_path, caplog):
    """All new error codes must not leak username/password/cookie sentinel values
    in payloads or logs."""
    caplog.set_level(logging.INFO)
    new_codes = [
        "jwxt_invalid_credentials",
        "jwxt_invalid_username",
        "jwxt_invalid_password",
        "jwxt_account_locked",
        "jwxt_password_expired",
        "jwxt_login_failed_unknown",
    ]
    for code in new_codes:
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
            side_effect=JwxtSyncError(code)
        )

        events = await connector.handle_fetch_request(_fetch_event())
        failed = next(
            event for event in events
            if event.event_type == EventType.CONNECTOR_FETCH_FAILED
        )
        rendered = f"{failed.payload} {caplog.text}"

        assert SENTINEL_USERNAME not in rendered
        assert SENTINEL_PASSWORD not in rendered
        assert SENTINEL_COOKIE not in rendered


# ══════════════════════════════════════════════════════════════════════════════
# Dashboard empty-reason tests — new error codes map correctly
# ══════════════════════════════════════════════════════════════════════════════

from src.domain.dashboard.query import build_dashboard
from src.domain.dashboard.query import _schedule_empty_reason as _reason  # noqa: E402


@pytest.mark.parametrize(
    "error_code, expected_reason",
    [
        ("jwxt_invalid_credentials", "schedule_empty_auth_failed"),
        ("jwxt_invalid_username", "schedule_empty_auth_failed"),
        ("jwxt_invalid_password", "schedule_empty_auth_failed"),
        ("jwxt_account_locked", "schedule_empty_auth_failed"),
        ("jwxt_password_expired", "schedule_empty_auth_failed"),
        ("jwxt_login_failed_unknown", "schedule_empty_auth_failed"),
        ("jwxt_cookie_expired", "schedule_empty_auth_failed"),
        ("jwxt_network_error", "jwxt_network_error"),
        ("jwxt_parser_error", "jwxt_parser_error"),
    ],
)
def test_dashboard_empty_reason_for_jwxt_error_codes(error_code, expected_reason):
    """New JWXT auth error codes should map to schedule_empty_auth_failed.
    Network/parser errors should pass through as-is."""
    schedule: list[dict] = []
    health = {
        "jwxt": {"status": "failed", "error_code": error_code},
    }
    assert _reason(schedule, health) == expected_reason
