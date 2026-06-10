"""Subjective reality domain handler.

Translates user input (mood/note/context) into structured events.
Pure function: Event -> List[Event]. No I/O. No state.
Human-in-the-loop cognition. Not a chatbot.
"""

from __future__ import annotations

from src.core.events import Event, EventType, AggregateType


async def handle_subjective_command(event: Event) -> list[Event]:
    """Handle subjective commands from USER_COMMAND_RECEIVED.

    Returns MOOD_RECORDED or SUBJECTIVE_CONTEXT_ADDED events.
    Returns [] for non-subjective commands.
    """
    command = event.payload.get("command", "")
    params = event.payload.get("params", {})
    args = params.get("args", "")
    user_id = event.aggregate_id

    if command == "record_mood":
        try:
            score = int(args)
        except (ValueError, TypeError):
            return []
        if score < 1 or score > 10:
            return []
        return [Event(
            event_type=EventType.MOOD_RECORDED,
            aggregate_id=user_id,
            aggregate_type=AggregateType.USER,
            causation_id=event.event_id,
            payload={"score": score},
        )]

    if command == "record_note":
        if not args:
            return []
        return [Event(
            event_type=EventType.SUBJECTIVE_CONTEXT_ADDED,
            aggregate_id=user_id,
            aggregate_type=AggregateType.USER,
            causation_id=event.event_id,
            payload={"kind": "note", "text": args},
        )]

    if command == "record_context":
        if not args:
            return []
        return [Event(
            event_type=EventType.SUBJECTIVE_CONTEXT_ADDED,
            aggregate_id=user_id,
            aggregate_type=AggregateType.USER,
            causation_id=event.event_id,
            payload={"kind": "context", "text": args},
        )]

    if command == "record_school_leave":
        return [Event(
            event_type=EventType.SUBJECTIVE_CONTEXT_ADDED,
            aggregate_id=user_id,
            aggregate_type=AggregateType.USER,
            causation_id=event.event_id,
            payload={"kind": "school_leave", "text": args or "今日请假"},
        )]

    return []
