"""Test: DeepSeek client and parsing behavior — all mocked, no live API."""

from __future__ import annotations

import json
import sys
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, ".")

import pytest


def _mock_httpx_client(response_data: dict, raise_error: Exception | None = None):
    """Create a mock for httpx.AsyncClient that returns the given response."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = response_data
    if raise_error:
        mock_response.raise_for_status.side_effect = raise_error
    else:
        mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.post = AsyncMock(return_value=mock_response)
    return mock_client


@pytest.mark.asyncio
async def test_deepseek_client_success():
    """Client returns parsed JSON on valid response."""
    from src.infrastructure.deepseek import DeepSeekClient

    mock_client = _mock_httpx_client({
        "choices": [
            {
                "message": {
                    "content": json.dumps({"events": [{"event_type": "MEMORY_ENTRY_CREATED", "content": "test"}]})
                }
            }
        ]
    })

    with patch("httpx.AsyncClient", return_value=mock_client):
        client = DeepSeekClient(api_key="test-key")
        result = await client.chat_json("system prompt", "user message")

    assert result == {"events": [{"event_type": "MEMORY_ENTRY_CREATED", "content": "test"}]}


@pytest.mark.asyncio
async def test_deepseek_client_empty_choices():
    """Raises RuntimeError when choices is empty."""
    from src.infrastructure.deepseek import DeepSeekClient

    mock_client = _mock_httpx_client({"choices": []})

    with patch("httpx.AsyncClient", return_value=mock_client):
        client = DeepSeekClient(api_key="test-key")
        with pytest.raises(RuntimeError, match="no choices"):
            await client.chat_json("system", "user")


@pytest.mark.asyncio
async def test_deepseek_client_empty_content():
    """Raises RuntimeError when content is empty."""
    from src.infrastructure.deepseek import DeepSeekClient

    mock_client = _mock_httpx_client({
        "choices": [{"message": {"content": ""}}]
    })

    with patch("httpx.AsyncClient", return_value=mock_client):
        client = DeepSeekClient(api_key="test-key")
        with pytest.raises(RuntimeError, match="empty content"):
            await client.chat_json("system", "user")


@pytest.mark.asyncio
async def test_deepseek_client_invalid_json():
    """Raises RuntimeError when response is not valid JSON."""
    from src.infrastructure.deepseek import DeepSeekClient

    mock_client = _mock_httpx_client({
        "choices": [{"message": {"content": "not json"}}]
    })

    with patch("httpx.AsyncClient", return_value=mock_client):
        client = DeepSeekClient(api_key="test-key")
        with pytest.raises(RuntimeError, match="invalid JSON"):
            await client.chat_json("system", "user")


@pytest.mark.asyncio
async def test_deepseek_client_http_error():
    """Raises on HTTP error."""
    from src.infrastructure.deepseek import DeepSeekClient

    mock_client = _mock_httpx_client({}, raise_error=Exception("HTTP 401"))

    with patch("httpx.AsyncClient", return_value=mock_client):
        client = DeepSeekClient(api_key="test-key")
        with pytest.raises(Exception, match="HTTP 401"):
            await client.chat_json("system", "user")


@pytest.mark.asyncio
async def test_deepseek_cognitive_learning_parsing():
    """Cognitive learning: DeepSeek parses natural language into events."""
    from src.infrastructure.deepseek import DeepSeekClient

    mock_client = _mock_httpx_client({
        "choices": [
            {
                "message": {
                    "content": json.dumps({
                        "events": [
                            {
                                "event_type": "MEMORY_ENTRY_CREATED",
                                "content": "今天学习了Python异步编程",
                                "tags": ["python", "async"],
                            },
                            {
                                "event_type": "SUBJECTIVE_CONTEXT_ADDED",
                                "kind": "context",
                                "content": "感觉状态不错",
                            },
                        ]
                    })
                }
            }
        ]
    })

    with patch("httpx.AsyncClient", return_value=mock_client):
        client = DeepSeekClient(api_key="test-key")
        result = await client.chat_json("system prompt", "今天学了Python异步编程")
        events = result.get("events", [])
        assert len(events) == 2
        assert events[0]["event_type"] == "MEMORY_ENTRY_CREATED"
        assert events[1]["event_type"] == "SUBJECTIVE_CONTEXT_ADDED"


@pytest.mark.asyncio
async def test_deepseek_verbal_scheduling_parsing():
    """Verbal scheduling: DeepSeek parses natural language into calendar payload."""
    from src.infrastructure.deepseek import DeepSeekClient

    mock_client = _mock_httpx_client({
        "choices": [
            {
                "message": {
                    "content": json.dumps({
                        "title": "团队周会",
                        "start": "2026-06-01T14:00:00+08:00",
                        "end": "2026-06-01T15:00:00+08:00",
                        "description": "每周一团队同步",
                        "location": "会议室A",
                    })
                }
            }
        ]
    })

    with patch("httpx.AsyncClient", return_value=mock_client):
        client = DeepSeekClient(api_key="test-key")
        result = await client.chat_json("system prompt", "周一下午2点到3点在会议室A开团队周会")
        assert result["title"] == "团队周会"
        assert result["start"] == "2026-06-01T14:00:00+08:00"
        assert result["end"] == "2026-06-01T15:00:00+08:00"
