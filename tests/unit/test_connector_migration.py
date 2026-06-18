"""Test: Connector Migration — Chaoxing auth, browser, scraper contracts."""

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, ".")

from src.core.events import Event, EventType, AggregateType
from src.connector.chaoxing.client import ChaoxingConnector
from src.connector.chaoxing.browser import ChaoxingBrowser


async def test_connector_mock_mode():
    """Mock mode returns structured data without browser."""
    connector = ChaoxingConnector(use_mock=True)
    ok = await connector.authenticate()
    assert ok

    data = await connector.fetch({"query": "homework_list"})
    assert data["source"] == "chaoxing"
    assert len(data["homeworks"]) == 2
    assert data["homeworks"][0]["course"] == "高等数学"
    print("✓ mock mode returns homework data")

    data2 = await connector.fetch({"query": "course_list"})
    assert len(data2["courses"]) == 2
    print("✓ mock mode returns course data")


async def test_connector_auth_without_state_file():
    """Without state file, auth returns False (not crash)."""
    connector = ChaoxingConnector(
        use_mock=False,
        state_file="/nonexistent/path/state.json",
    )
    ok = await connector.authenticate()
    assert not ok
    print("✓ missing state file: auth returns False")


async def test_connector_fetch_request_handler():
    """handle_fetch_request responds correctly to EventBus events."""
    connector = ChaoxingConnector(use_mock=True)

    # Wrong source → ignored
    event = Event(
        event_type=EventType.CONNECTOR_FETCH_REQUESTED,
        aggregate_id="user-1",
        aggregate_type=AggregateType.HOMEWORK,
        payload={"source": "other_system"},
    )
    result = await connector.handle_fetch_request(event)
    assert result == []
    print("✓ ignores non-chaoxing requests")

    # Correct source → fetch + return completed
    event2 = Event(
        event_type=EventType.CONNECTOR_FETCH_REQUESTED,
        aggregate_id="user-1",
        aggregate_type=AggregateType.HOMEWORK,
        payload={"source": "chaoxing", "query": "homework_list"},
    )
    result2 = await connector.handle_fetch_request(event2)
    assert [event.event_type for event in result2] == [
        EventType.CONNECTOR_FETCH_STARTED,
        EventType.CONNECTOR_FETCH_COMPLETED,
    ]
    assert result2[1].causation_id == event2.event_id
    assert len(result2[1].payload["homeworks"]) == 2
    assert result2[1].payload["mock_enabled"] is True
    print("✓ handle_fetch_request: request → completed with causation")


async def test_connector_fetch_failed_on_error():
    """Unknown query produces fetch_failed event."""
    connector = ChaoxingConnector(use_mock=True)
    event = Event(
        event_type=EventType.CONNECTOR_FETCH_REQUESTED,
        aggregate_id="user-1",
        aggregate_type=AggregateType.HOMEWORK,
        payload={"source": "chaoxing", "query": "invalid_query"},
    )
    result = await connector.handle_fetch_request(event)
    assert [item.event_type for item in result] == [
        EventType.CONNECTOR_FETCH_STARTED,
        EventType.CONNECTOR_FETCH_FAILED,
    ]
    assert result[1].payload["error_code"] == "chaoxing_sync_failed"
    print("✓ unknown query returns a structured failure")


async def test_browser_params_extraction():
    """URL param extraction from old parser code."""
    from src.connector.chaoxing.assignment_scraper import _extract_params_from_url
    url = (
        "https://mooc1.chaoxing.com/visit/stucoursemiddle"
        "?courseid=254560272&clazzid=131115643&cpi=351446317&ismooc2=1&v=2"
    )
    params = _extract_params_from_url(url)
    assert params["courseid"] == "254560272"
    assert params["clazzid"] == "131115643"
    assert params["cpi"] == "351446317"
    print("✓ URL param extraction works")


def test_google_calendar_mapping_preserves_private_markers():
    """Calendar reads must preserve managed markers for repair/planning."""
    from src.connector.google_calendar.client import _map_google_event

    mapped = _map_google_event(
        {
            "id": "evt-1",
            "summary": "🎨 作品推进",
            "description": "Cognitive OS / Art Planner",
            "start": {"dateTime": "2026-06-03T20:15:00+08:00"},
            "end": {"dateTime": "2026-06-03T21:45:00+08:00"},
            "updated": "2026-06-03T11:12:54.219Z",
            "extendedProperties": {
                "private": {
                    "managed_by": "cognitive_os",
                    "source": "daily_art_plan",
                    "plan_id": "plan-1",
                }
            },
        },
        "primary",
        "主日历",
    )

    assert mapped is not None
    meta = mapped["metadata"]
    assert meta["managed_by"] == "cognitive_os"
    assert meta["source"] == "daily_art_plan"
    assert meta["plan_id"] == "plan-1"
    assert meta["extended_private"]["managed_by"] == "cognitive_os"


if __name__ == "__main__":
    asyncio.run(test_connector_mock_mode())
    asyncio.run(test_connector_auth_without_state_file())
    asyncio.run(test_connector_fetch_request_handler())
    asyncio.run(test_connector_fetch_failed_on_error())
    asyncio.run(test_browser_params_extraction())
    print("\nConnector Migration: all checks passed")
