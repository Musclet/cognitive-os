"""SubjectiveContextRegistry — stores user-submitted subjective reality modifiers.

Following ActiveCourseRegistry pattern:
- Internal item dict with typed dataclass entries
- Event handlers (on_mood_recorded, on_subjective_context_added)
- Query methods for derived state consumption
- to_dict/from_dict for snapshot serialization
- Expiration logic: notes expire at midnight, contexts expire in 24h

Human-in-the-loop cognition: user actively corrects runtime's understanding.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any

from src.core.events import Event


@dataclass
class SubjectiveEntry:
    """A single subjective context entry."""
    entry_id: str
    kind: str  # "note" | "context"
    text: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None


class SubjectiveContextRegistry:
    """Manages active subjective context entries and mood history.

    Pattern: identical to ActiveCourseRegistry in structure.
    Event handlers are wired via event bus in bot.py wire_handlers().
    """

    def __init__(self) -> None:
        self._entries: dict[str, SubjectiveEntry] = {}
        self._mood_history: list[dict] = []
        self._counter: int = 0

    def _next_id(self) -> str:
        self._counter += 1
        return f"subj-{self._counter}"

    # ── Recording methods ──────────────────────────────────────────────

    def record_mood(self, score: int) -> None:
        now = datetime.now(timezone.utc)
        self._mood_history.append({"score": score, "timestamp": now.isoformat()})
        cutoff = now - timedelta(days=30)
        self._mood_history = [
            m for m in self._mood_history
            if datetime.fromisoformat(m["timestamp"]) > cutoff
        ]

    def record_note(self, text: str) -> None:
        now = datetime.now(timezone.utc)
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        eid = self._next_id()
        self._entries[eid] = SubjectiveEntry(
            entry_id=eid, kind="note", text=text,
            created_at=now, expires_at=midnight,
        )

    def record_context(self, text: str) -> None:
        now = datetime.now(timezone.utc)
        eid = self._next_id()
        self._entries[eid] = SubjectiveEntry(
            entry_id=eid, kind="context", text=text,
            created_at=now, expires_at=now + timedelta(hours=24),
        )

    # ── Event handlers (following ActiveCourseRegistry pattern) ────────

    def on_mood_recorded(self, event: Event) -> None:
        score = event.payload.get("score")
        if score is not None:
            self.record_mood(int(score))

    def on_subjective_context_added(self, event: Event) -> None:
        kind = event.payload.get("kind", "")
        text = event.payload.get("text", "")
        if kind == "note":
            self.record_note(text)
        elif kind == "context":
            self.record_context(text)

    # ── Query methods ──────────────────────────────────────────────────

    def get_current_mood(self) -> int | None:
        if not self._mood_history:
            return None
        return self._mood_history[-1]["score"]

    def get_mood_trend(self) -> str:
        """Returns 'improving', 'declining', 'stable', or 'insufficient_data'."""
        if len(self._mood_history) < 3:
            return "insufficient_data"
        recent = self._mood_history[-3:]
        scores = [e["score"] for e in recent]
        if all(scores[i] <= scores[i+1] for i in range(len(scores)-1)):
            return "improving"
        if all(scores[i] >= scores[i+1] for i in range(len(scores)-1)):
            return "declining"
        return "stable"

    def _is_active(self, entry: SubjectiveEntry) -> bool:
        now = datetime.now(timezone.utc)
        return entry.expires_at is None or entry.expires_at > now

    def get_active_notes(self) -> list[SubjectiveEntry]:
        return [e for e in self._entries.values() if e.kind == "note" and self._is_active(e)]

    def get_active_contexts(self) -> list[SubjectiveEntry]:
        return [e for e in self._entries.values() if e.kind == "context" and self._is_active(e)]

    def has_social_plan_today(self) -> bool:
        keywords = [
            "社交", "social", "聚会", "party", "饭局", "约", "饭",
            "见面", "meet", "hangout", "外出", "出门", "聚餐",
        ]
        for note in self.get_active_notes():
            text_lower = note.text.lower()
            if any(kw in text_lower for kw in keywords):
                return True
        return False

    def has_evening_event(self) -> bool:
        keywords = ["晚", "夜", "evening", "night", "今晚", "今晩"]
        for note in self.get_active_notes():
            text_lower = note.text.lower()
            if any(kw in text_lower for kw in keywords):
                return True
        return False

    def to_dict(self) -> dict[str, Any]:
        entries = {}
        for eid, entry in self._entries.items():
            entries[eid] = {
                "entry_id": entry.entry_id,
                "kind": entry.kind,
                "text": entry.text,
                "created_at": entry.created_at.isoformat() if entry.created_at else None,
                "expires_at": entry.expires_at.isoformat() if entry.expires_at else None,
            }
        return {
            "entries": entries,
            "mood_history": self._mood_history,
            "counter": self._counter,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SubjectiveContextRegistry":
        reg = cls()
        reg._counter = data.get("counter", 0)
        reg._mood_history = data.get("mood_history", [])
        for eid, d in data.get("entries", {}).items():
            entry = SubjectiveEntry(
                entry_id=d.get("entry_id", eid),
                kind=d.get("kind", ""),
                text=d.get("text", ""),
                created_at=datetime.fromisoformat(d["created_at"])
                    if d.get("created_at") else datetime.now(timezone.utc),
                expires_at=datetime.fromisoformat(d["expires_at"])
                    if d.get("expires_at") else None,
            )
            reg._entries[eid] = entry
        return reg

    @property
    def entry_count(self) -> int:
        return len(self._entries)

    @property
    def mood_sample_count(self) -> int:
        return len(self._mood_history)
