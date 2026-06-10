"""DeepSeek client — OpenAI-compatible chat completions with JSON output.

Thin adapter. Always requests JSON object output.
Never used for autonomous writes. Only user-triggered parsing.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class DeepSeekClient:
    """Thin OpenAI-compatible chat completions client.

    Always requests response_format={"type": "json_object"}.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.deepseek.com",
        model: str = "deepseek-chat",
        timeout_seconds: int = 30,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout_seconds

    async def chat_json(self, system_prompt: str, user_message: str) -> dict[str, Any]:
        """Send a chat completion request and return parsed JSON response."""
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "response_format": {"type": "json_object"},
        }

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                f"{self._base_url}/v1/chat/completions",
                headers=headers,
                json=body,
            )
            response.raise_for_status()
            data = response.json()

        choices = data.get("choices", [])
        if not choices:
            raise RuntimeError(f"DeepSeek returned no choices: {data}")

        content = choices[0].get("message", {}).get("content", "")
        if not content:
            raise RuntimeError("DeepSeek returned empty content")

        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"DeepSeek returned invalid JSON: {content[:200]}") from exc
