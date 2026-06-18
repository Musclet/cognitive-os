from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import pytest

from src.connector.chaoxing import browser as chaoxing_browser_module
from src.connector.chaoxing.client import ChaoxingConnector
from src.core.events import AggregateType, Event, EventType
from src.core.state_engine import StateEngine
from src.domain.homework.handlers import handle_fetch_completed

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
