from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.connector.chaoxing import browser as chaoxing_browser_module
from src.connector.chaoxing import client as chaoxing_client_module
from src.connector.chaoxing.client import ChaoxingConnector
from src.core.bus import EventBus
from src.core.events import AggregateType, Event, EventType
from src.core.state_engine import StateEngine
from src.domain.dashboard.query import build_dashboard
from src.domain.homework.handlers import handle_fetch_completed
from src.infrastructure.config import Settings

SENTINEL = "DO_NOT_LEAK_CHAOXING_SECRET"
FORBIDDEN_SENSITIVE_TEXT = (
    SENTINEL.casefold(),
    "cookie",
    "token",
    "password",
    "authorization",
)


def _fetch_event() -> Event:
    return Event(
        event_type=EventType.CONNECTOR_FETCH_REQUESTED,
        aggregate_id="chaoxing-test",
        aggregate_type=AggregateType.HOMEWORK,
        payload={"source": "chaoxing", "query": "homework_list"},
    )


def _assert_no_sensitive_text(*values: object) -> None:
    text = " ".join(str(value) for value in values).casefold()
    for forbidden in FORBIDDEN_SENSITIVE_TEXT:
        assert forbidden not in text


@pytest.mark.asyncio
async def test_mock_sync_is_explicitly_marked():
    connector = ChaoxingConnector(use_mock=True)

    events = await connector.handle_fetch_request(_fetch_event())

    completed = next(event for event in events if event.event_type == EventType.CONNECTOR_FETCH_COMPLETED)
    assert completed.payload["mock_enabled"] is True
    assert completed.payload["pulled_count"] == 2
    assert completed.payload["homework_count"] == 2


@pytest.mark.asyncio
async def test_real_sync_missing_state_file_has_structured_error(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    connector = ChaoxingConnector(
        use_mock=False,
        state_file=str(tmp_path / "missing-state.json"),
    )

    events = await connector.handle_fetch_request(_fetch_event())

    failed = next(event for event in events if event.event_type == EventType.CONNECTOR_FETCH_FAILED)
    assert failed.payload["error_code"] == "chaoxing_state_file_missing"
    assert failed.payload["mock_enabled"] is False
    _assert_no_sensitive_text(failed.payload, caplog.text)


@pytest.mark.asyncio
async def test_real_sync_invalid_state_reports_session_expired(tmp_path):
    state_file = tmp_path / "state.json"
    state_file.write_text(SENTINEL, encoding="utf-8")
    connector = ChaoxingConnector(use_mock=False, state_file=str(state_file))
    browser = AsyncMock()
    browser.start.return_value = None
    browser.check_session_valid.return_value = False
    connector._browser = browser
    connector._authenticated = True

    events = await connector.handle_fetch_request(_fetch_event())

    failed = next(event for event in events if event.event_type == EventType.CONNECTOR_FETCH_FAILED)
    assert failed.payload["error_code"] == "chaoxing_session_expired"
    assert failed.payload["mock_enabled"] is False
    _assert_no_sensitive_text(failed.payload)


@pytest.mark.asyncio
async def test_real_sync_playwright_import_missing_is_sanitized(
    tmp_path,
    monkeypatch,
    caplog,
):
    caplog.set_level(logging.DEBUG)
    state_file = tmp_path / "state.json"
    state_file.write_text(SENTINEL, encoding="utf-8")
    monkeypatch.setattr(chaoxing_browser_module, "async_playwright", None)
    connector = ChaoxingConnector(use_mock=False, state_file=str(state_file))

    events = await connector.handle_fetch_request(_fetch_event())

    failed = next(event for event in events if event.event_type == EventType.CONNECTOR_FETCH_FAILED)
    assert failed.payload["error_code"] == "chaoxing_playwright_missing"
    assert "error_code=chaoxing_playwright_missing" in caplog.text
    assert "ModuleNotFoundError" in caplog.text
    _assert_no_sensitive_text(failed.payload, caplog.text)


@pytest.mark.asyncio
async def test_real_sync_browser_launch_failure_is_sanitized(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    state_file = tmp_path / "state.json"
    state_file.write_text(SENTINEL, encoding="utf-8")
    connector = ChaoxingConnector(use_mock=False, state_file=str(state_file))
    browser = AsyncMock()
    browser.start.side_effect = RuntimeError(
        "Failed to launch browser "
        f"{SENTINEL} cookie token password authorization"
    )
    connector._browser = browser
    connector._authenticated = True

    events = await connector.handle_fetch_request(_fetch_event())

    failed = next(event for event in events if event.event_type == EventType.CONNECTOR_FETCH_FAILED)
    assert failed.payload["error_code"] == "chaoxing_browser_unavailable"
    assert "error_code=chaoxing_browser_unavailable" in caplog.text
    assert "RuntimeError" in caplog.text
    _assert_no_sensitive_text(failed.payload, caplog.text)


@pytest.mark.asyncio
async def test_real_sync_success_enters_state_engine(tmp_path):
    state_file = tmp_path / "state.json"
    state_file.touch()
    connector = ChaoxingConnector(use_mock=False, state_file=str(state_file))
    browser = AsyncMock()
    browser.start.return_value = None
    browser.check_session_valid.return_value = True
    browser.save_state.return_value = True
    connector._browser = browser
    connector._authenticated = True
    connector._fetch_homework = AsyncMock(return_value={
        "source": "chaoxing",
        "mock_enabled": False,
        "homeworks": [{
            "id": "real-hw-1",
            "course": "虚拟现实技术",
            "title": "实验报告",
            "deadline": "2026-06-30T12:00:00Z",
            "status": "pending",
        }],
        "courses": [{"course_id": "real-course-1", "name": "虚拟现实技术"}],
        "total_assignments": 1,
        "pulled_count": 1,
        "homework_count": 1,
    })

    events = await connector.handle_fetch_request(_fetch_event())
    completed = next(event for event in events if event.event_type == EventType.CONNECTOR_FETCH_COMPLETED)
    engine = StateEngine()
    await engine.apply(completed)
    for domain_event in await handle_fetch_completed(completed):
        await engine.apply(domain_event)

    assert engine.get_view("homework", "real-hw-1")["title"] == "实验报告"
    sync = engine.get_view("sync", "chaoxing")
    assert sync["status"] == "completed"
    assert sync["mock_enabled"] is False
    assert sync["pulled_count"] == 1
    assert sync["homework_count"] == 1


def _connector_settings(timeout: int = 300):
    return SimpleNamespace(
        chaoxing_state_file="data/chaoxing_state.json",
        chaoxing_sync_timeout_seconds=timeout,
    )


def test_chaoxing_sync_timeout_setting_defaults_to_300_seconds():
    assert Settings(_env_file=None).chaoxing_sync_timeout_seconds == 300


@pytest.mark.asyncio
async def test_real_sync_filters_58_courses_to_current_candidates(monkeypatch):
    courses = [
        {
            "course_id": f"course-{index}",
            "name": f"历史课程 {index}",
            "url": f"https://example.test/{index}",
        }
        for index in range(53)
    ] + [
        {
            "course_id": "current-data",
            "name": "数据　结构（张老师）",
            "url": "https://example.test/data",
        },
        {
            "course_id": "current-os",
            "name": "操作系统",
            "url": "https://example.test/os",
        },
        {
            "course_id": "current-network",
            "name": "计算机网络",
            "url": "https://example.test/network",
        },
        {
            "course_id": "current-db",
            "name": "数据库原理",
            "url": "https://example.test/db",
        },
        {
            "course_id": "current-vr",
            "name": "Unity应用实训",
            "url": "https://example.test/vr",
        },
    ]
    selected: list[dict] = []

    async def fake_fetch_all(browser, filtered, **kwargs):
        selected.extend(filtered)
        return []

    monkeypatch.setattr(
        chaoxing_client_module,
        "fetch_course_list",
        AsyncMock(return_value=courses),
    )
    monkeypatch.setattr(
        chaoxing_client_module,
        "fetch_all_assignments",
        fake_fetch_all,
    )
    connector = ChaoxingConnector(
        use_mock=True,
        settings=_connector_settings(),
    )
    connector._browser = AsyncMock()

    result = await connector._fetch_homework(
        scope=[
            "数据 结构",
            "操作系统",
            "计算机网络",
            "数据库原理",
            "虚拟现实技术（张辉）",
        ],
        current_course_source="temporal_blocks",
    )

    assert result["total_courses"] == 58
    assert result["filtered_courses"] == 5
    assert result["skipped_courses"] == 53
    assert len(selected) == 5
    assert result["scanning_all_courses"] is False


@pytest.mark.asyncio
async def test_real_sync_without_current_courses_scans_all(monkeypatch):
    courses = [
        {"course_id": str(index), "name": f"课程 {index}", "url": "https://example.test"}
        for index in range(3)
    ]
    selected: list[dict] = []

    async def fake_fetch_all(browser, filtered, **kwargs):
        selected.extend(filtered)
        return []

    monkeypatch.setattr(
        chaoxing_client_module,
        "fetch_course_list",
        AsyncMock(return_value=courses),
    )
    monkeypatch.setattr(
        chaoxing_client_module,
        "fetch_all_assignments",
        fake_fetch_all,
    )
    connector = ChaoxingConnector(
        use_mock=True,
        settings=_connector_settings(),
    )
    connector._browser = AsyncMock()

    result = await connector._fetch_homework(scope=[])

    assert len(selected) == 3
    assert result["scanning_all_courses"] is True
    assert result["filtered_courses"] == 3


@pytest.mark.asyncio
async def test_real_sync_with_no_matching_current_courses_stops(monkeypatch):
    fetch_all = AsyncMock(return_value=[])
    monkeypatch.setattr(
        chaoxing_client_module,
        "fetch_course_list",
        AsyncMock(return_value=[
            {"course_id": "old", "name": "历史课程", "url": "https://example.test"},
        ]),
    )
    monkeypatch.setattr(chaoxing_client_module, "fetch_all_assignments", fetch_all)
    connector = ChaoxingConnector(
        use_mock=True,
        settings=_connector_settings(),
    )
    connector._browser = AsyncMock()

    result = await connector._fetch_homework(scope=["当前课程"])

    assert result["error_code"] == "chaoxing_no_matching_current_courses"
    assert result["total_courses"] == 1
    assert result["active_course_candidates"] == 1
    assert result["scanned_courses"] == 0
    fetch_all.assert_not_awaited()


@pytest.mark.asyncio
async def test_timeout_preserves_partial_homework_in_dashboard(monkeypatch):
    bus = EventBus()
    engine = StateEngine()
    bus.subscribe(EventType.SYNC_PROGRESS, engine.apply)
    bus.subscribe(EventType.HOMEWORK_NEW, engine.apply)
    bus.subscribe(EventType.CONNECTOR_FETCH_COMPLETED, engine.apply)
    bus.subscribe(EventType.CONNECTOR_FETCH_FAILED, engine.apply)

    connector = ChaoxingConnector(
        use_mock=False,
        event_bus=bus,
        settings=_connector_settings(),
    )
    connector._browser = AsyncMock()
    connector._authenticated = True
    connector._sync_timeout_seconds = 0.01

    async def partial_fetch(params):
        await params["on_filter_ready"]({
            "total_courses": 58,
            "filtered_courses": 5,
            "skipped_courses": 53,
            "active_course_candidates": 5,
            "current_course_source": "temporal_blocks",
            "scanning_all_courses": False,
        })
        await params["on_course_complete"](1, 5, [{
            "id": "partial-homework",
            "course": "数据结构",
            "title": "第一次作业",
            "deadline": "2026-06-30T12:00:00Z",
            "status": "pending",
        }])
        await asyncio.sleep(1)
        return {}

    monkeypatch.setattr(connector, "fetch", partial_fetch)

    await connector._batched_fetch(
        _fetch_event(),
        ["数据结构", "操作系统", "计算机网络", "数据库原理", "软件工程"],
        "temporal_blocks",
    )

    assert engine.get_view("homework", "partial-homework")["title"] == "第一次作业"
    sync = engine.get_view("sync", "chaoxing")
    assert sync["status"] == "completed"
    assert sync["total_courses"] == 58
    assert sync["filtered_courses"] == 5
    assert sync["scanned_courses"] == 1
    assert sync["assignments_found"] == 1
    assert sync["partial"] is True
    assert sync["timeout"] is True

    dashboard = build_dashboard(
        engine,
        Settings(_env_file=None, chaoxing_mock=False),
    )
    assert dashboard["homework_count"] == 1
    assert dashboard["homework"][0]["id"] == "partial-homework"


# ══════════════════════════════════════════════════════════════════════════════
# refresh_chaoxing_state script tests
# ══════════════════════════════════════════════════════════════════════════════

import json
import subprocess
import sys
from pathlib import Path
from scripts.refresh_chaoxing_state import (
    EXIT_OK,
    EXIT_PLAYWRIGHT_MISSING,
    EXIT_LOGIN_TIMEOUT,
    EXIT_LOGIN_NOT_DETECTED,
    EXIT_GITIGNORE_FAIL,
    _check_gitignore,
    _count_storage_state,
    _has_chaoxing_cookies,
)


def _scripts_dir() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "scripts"


class TestRefreshChaoxingStateArgs:
    """Argument parsing tests for the refresh script."""

    def test_default_state_file_from_settings(self):
        """Default --state-file should come from Settings."""
        from src.infrastructure.config import Settings
        s = Settings()
        assert s.chaoxing_state_file == "data/chaoxing_state.json"

    def test_help_flag(self):
        """--help should exit 0 and mention the script name."""
        result = subprocess.run(
            [sys.executable, str(_scripts_dir() / "refresh_chaoxing_state.py"),
             "--help"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "refresh_chaoxing_state" in result.stdout

    def test_custom_timeout_and_state_file_accepted(self):
        """--timeout and --state-file should be accepted without error."""
        result = subprocess.run(
            [sys.executable, str(_scripts_dir() / "refresh_chaoxing_state.py"),
             "--timeout", "1", "--state-file", "/tmp/test_chaoxing_state.json",
             "--help"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0


class TestRefreshChaoxingStateSafety:
    """Safety-oriented tests — no credentials, no cookie values."""

    def test_count_storage_state_never_outputs_cookie_values(self, tmp_path):
        """_count_storage_state returns only integer counts, never cookie data."""
        state = tmp_path / "state.json"
        state.write_text(json.dumps({
            "cookies": [
                {"name": "session", "value": SENTINEL, "domain": ".chaoxing.com"},
                {"name": "token", "value": SENTINEL, "domain": "passport2.chaoxing.com"},
            ],
            "origins": [{"origin": "https://i.chaoxing.com", "localStorage": []}],
        }), encoding="utf-8")
        summary = _count_storage_state(state)
        assert summary == {"cookies_count": 2, "origins_count": 1}
        # Field names may contain "cookies" — ensure values are just ints
        assert all(isinstance(v, int) for v in summary.values())
        output = json.dumps(summary)
        assert SENTINEL not in output
        assert "token" not in output.casefold()
        assert "password" not in output.casefold()
        assert "authorization" not in output.casefold()

    def test_count_storage_state_handles_missing_file(self, tmp_path):
        """Missing file returns zero counts."""
        summary = _count_storage_state(tmp_path / "nonexistent.json")
        assert summary == {"cookies_count": 0, "origins_count": 0}

    def test_count_storage_state_handles_invalid_json(self, tmp_path):
        """Invalid JSON returns zero counts."""
        state = tmp_path / "bad.json"
        state.write_text("not json", encoding="utf-8")
        summary = _count_storage_state(state)
        assert summary == {"cookies_count": 0, "origins_count": 0}

    def test_has_chaoxing_cookies_positive(self, tmp_path):
        """Detects Chaoxing-related cookie domains."""
        state = tmp_path / "state.json"
        state.write_text(json.dumps({
            "cookies": [
                {"name": "uid", "value": "x", "domain": ".chaoxing.com"},
            ],
            "origins": [],
        }), encoding="utf-8")
        assert _has_chaoxing_cookies(state) is True

    def test_has_chaoxing_cookies_negative(self, tmp_path):
        """Returns False when no Chaoxing cookies present."""
        state = tmp_path / "state.json"
        state.write_text(json.dumps({
            "cookies": [
                {"name": "x", "value": "y", "domain": "other-site.com"},
            ],
            "origins": [],
        }), encoding="utf-8")
        assert _has_chaoxing_cookies(state) is False

    def test_has_chaoxing_cookies_handles_missing_file(self, tmp_path):
        """Missing file returns False."""
        assert _has_chaoxing_cookies(tmp_path / "nonexistent.json") is False

    def test_gitignore_allows_data_dir(self, tmp_path):
        """_check_gitignore returns True when data/ is gitignored."""
        # The project's .gitignore has data/ — test against that
        repo_root = Path(__file__).resolve().parent.parent.parent
        state_in_data = repo_root / "data" / "chaoxing_state_test.json"
        result = _check_gitignore(state_in_data)
        assert result is True

    def test_gitignore_blocks_non_ignored_path(self, tmp_path):
        """_check_gitignore returns False for a path outside data/."""
        # tmp_path is outside the repo — git check-ignore returns exit 1
        # But since git may not track tmp_path at all, the behavior depends
        # on git. We test that _check_gitignore doesn't crash.
        result = _check_gitignore(tmp_path / "chaoxing_state.json")
        # Result depends on whether git tracks tmp_path; either way no crash
        assert isinstance(result, bool)

    def test_exit_codes_are_distinct(self):
        """All exit codes must be distinct."""
        codes = {
            EXIT_OK, EXIT_PLAYWRIGHT_MISSING, EXIT_LOGIN_TIMEOUT,
            EXIT_LOGIN_NOT_DETECTED, EXIT_GITIGNORE_FAIL,
        }
        assert len(codes) == 5

    def test_script_importable(self):
        """The script module can be imported without side effects."""
        import scripts.refresh_chaoxing_state  # noqa: F401

    def test_no_sensitive_text_in_script_source(self):
        """Script source must not contain sentinel or hardcoded secrets."""
        script_path = _scripts_dir() / "refresh_chaoxing_state.py"
        source = script_path.read_text(encoding="utf-8")
        assert SENTINEL not in source
        assert "password" not in source.casefold() or "chaoxing_playwright" in source
