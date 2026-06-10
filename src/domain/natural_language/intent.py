"""Intent schema, validation, and Command mapping for AI-based NL fallback.

Flow:
  1. Deterministic parse_message() returns None
  2. DeepSeek parses raw text into structured JSON
  3. validate_and_map() checks schema + allowlist → returns Command or None
  4. Command goes through existing handle_message dispatch

No chatbot persona. No GPT conversation. Pure intent classification.
"""

from __future__ import annotations

import logging
from typing import Any

from src.core.events import Command

logger = logging.getLogger(__name__)

# ── Allowed intent → Command mapping ─────────────────────────────────────────
# Each entry defines the target command_type and which params are required
# from the AI JSON output. The AI must produce exactly these param names.
#
# Only intent types listed here may be executed. Everything else is rejected.

AllowedIntentSpec = dict[str, Any]

ALLOWED_INTENTS: dict[str, AllowedIntentSpec | None] = {
    # Deterministic-show commands (no extra params needed)
    "show_today": {"command_type": "show_today", "required_params": []},
    "check_homework": {"command_type": "check_homework", "required_params": []},
    "sync_refresh": {"command_type": "sync_refresh", "required_params": []},
    # Schedule queries
    "query_schedule_date": {
        "command_type": "query_schedule_date",
        "required_params": ["date"],
    },
    # School leave
    "record_school_leave": {
        "command_type": "record_school_leave",
        "required_params": ["date"],
    },
    # Verbal scheduling → enters pending mode in bot
    "verbal_scheduling": {"command_type": "verbal_scheduling", "required_params": []},
    # Finance transaction
    "finance_transaction": {"command_type": "finance_transaction", "required_params": []},
    # Completion record → enters pending mode in bot
    "completion_record": {"command_type": "completion_record", "required_params": []},
    # Hydration
    "hydration_record": {"command_type": "drink", "required_params": []},
    # Subjective context
    "subjective_context": {
        "command_type": "record_context",
        "required_params": ["text"],
    },
    # Explicit unknown — no execution, only recording
    "unknown": None,
}

# All intent name strings (for fast lookup)
ALLOWED_INTENT_NAMES: set[str] = set(ALLOWED_INTENTS.keys())

# Intents that are recorded but never produce a Command
NON_EXECUTABLE_INTENTS: set[str] = {"unknown"}


def validate_ai_output(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Validate and sanitize AI JSON output against the allowed intent schema.

    Expected AI output format:
      {
        "intent": "show_today",
        "params": {},
        "confidence": 0.95,
        "raw_phrase": "今天有什么安排",
        "reasoning": "user is asking about today's plan"
      }

    Returns a sanitized dict with keys: intent, params, confidence, raw_phrase
    or None if validation fails.
    """
    intent = raw.get("intent", "")
    if not isinstance(intent, str) or intent not in ALLOWED_INTENT_NAMES:
        logger.warning("NL intent: unknown or missing intent=%r", intent)
        return None

    spec = ALLOWED_INTENTS[intent]
    if spec is None and intent == "unknown":
        # unknown is always valid (record-only)
        return {
            "intent": "unknown",
            "params": {},
            "confidence": raw.get("confidence", 0.0),
            "raw_phrase": raw.get("raw_phrase", ""),
        }

    if spec is None:
        return None

    params = raw.get("params", {})
    if not isinstance(params, dict):
        logger.warning("NL intent: params is not a dict")
        return None

    # Check required params exist and are non-empty
    for req_key in spec["required_params"]:
        val = params.get(req_key)
        if not val or not isinstance(val, str):
            logger.warning(
                "NL intent: missing required param %r for intent=%s",
                req_key,
                intent,
            )
            return None

    # Sanitize: remove keys not in required_params to prevent injection
    safe_params = {k: params[k] for k in spec["required_params"] if k in params}

    # Always include raw_text for traceability
    safe_params["raw_text"] = raw.get("raw_phrase", "")
    safe_params["nl_fallback"] = True

    return {
        "intent": intent,
        "params": safe_params,
        "confidence": raw.get("confidence", 0.0),
        "raw_phrase": raw.get("raw_phrase", ""),
    }


def map_to_command(
    validated: dict[str, Any], user_id: str
) -> Command | None:
    """Map a validated AI intent to a Command, or None for non-executable intents.

    Args:
        validated: output from validate_ai_output()
        user_id: string user id

    Returns:
        Command ready for dispatch, or None for non-executable intents.
    """
    intent = validated["intent"]
    if intent in NON_EXECUTABLE_INTENTS:
        return None

    spec = ALLOWED_INTENTS.get(intent)
    if spec is None:
        return None

    return Command(
        command_type=spec["command_type"],
        user_id=user_id,
        params=validated["params"],
        source="nl_fallback",
    )
