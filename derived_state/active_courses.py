from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any
from urllib.parse import urlparse, parse_qs
from src.core.events import Event, EventType
from src.domain.course_topology import chaoxing_scope_names, is_excluded_course, normalize_course_name


def extract_course_id(url: str) -> str:
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    cid = qs.get("courseid", [""])[0]
    clid = qs.get("clazzid", [""])[0]
    return f"{cid}_{clid}" if cid and clid else url

@dataclass
class ActiveCourse:
    course_id: str
    course_name: str
    last_schedule_hit: datetime | None = None
    last_homework_seen: datetime | None = None
    last_synced: datetime | None = None
    last_interaction: datetime | None = None
    deadline_density: float = 0.0
    attention_score: float = 0.0
    pending_deadlines: int = 0
    pending_deadline_hours: float | None = None
    recent_activity: bool = False
    active: bool = True


class ActiveCourseRegistry:
    def __init__(self) -> None:
        self._courses: dict[str, ActiveCourse] = {}

    def register(self, course_id: str, course_name: str) -> ActiveCourse:
        course_name = normalize_course_name(course_name)
        if course_id not in self._courses:
            self._courses[course_id] = ActiveCourse(course_id=course_id, course_name=course_name)
        elif course_name:
            self._courses[course_id].course_name = course_name
        return self._courses[course_id]

    def record_schedule_hit(self, course_id: str, course_name: str = "") -> None:
        c = self.register(course_id, course_name)
        c.last_schedule_hit = datetime.now(timezone.utc)

    def record_homework_seen(self, course_id: str, course_name: str = "", deadline_hours: float | None = None) -> None:
        c = self.register(course_id, course_name)
        c.last_homework_seen = datetime.now(timezone.utc)
        if deadline_hours is not None:
            if c.pending_deadline_hours is None or deadline_hours < c.pending_deadline_hours:
                c.pending_deadline_hours = deadline_hours
            c.pending_deadlines += 1

    def record_sync(self, course_id: str) -> None:
        if course_id in self._courses:
            self._courses[course_id].last_synced = datetime.now(timezone.utc)

    def record_interaction(self, course_id: str, course_name: str = "") -> None:
        c = self.register(course_id, course_name)
        c.last_interaction = datetime.now(timezone.utc)

    # ── Event handlers ──────────────────────────────────────────────

    def on_course_activated(self, event: Event) -> None:
        """COURSE_ACTIVATED handler: register course from JWXT."""
        course_name = normalize_course_name(event.payload.get("course_name", event.aggregate_id))
        course_id = event.aggregate_id
        if is_excluded_course(course_name):
            if course_id in self._courses:
                self._courses[course_id].active = False
            return
        self.register(course_id, course_name)
        self._courses[course_id].last_schedule_hit = datetime.now(timezone.utc)

    def on_course_deactivated(self, event: Event) -> None:
        """COURSE_DEACTIVATED handler: mark course inactive."""
        course_id = event.aggregate_id
        if course_id in self._courses:
            self._courses[course_id].active = False

    # ── Query methods ───────────────────────────────────────────────

    def get_active_scope_names(self) -> list[str]:
        """Return course names of all active courses for Chaoxing scope filtering."""
        names = [c.course_name for c in self._courses.values() if c.active and c.course_name]
        return chaoxing_scope_names(names)

    def compute_scores(self, schedule_course_ids: set[str] | None = None, deadline_window_hours: int = 72) -> None:
        now = datetime.now(timezone.utc)
        schedule_ids = schedule_course_ids or set()
        for c in self._courses.values():
            if not c.active:
                c.attention_score = 0.0
                continue
            score = 0.0
            if c.course_id in schedule_ids or (c.last_schedule_hit and (now - c.last_schedule_hit) < timedelta(hours=24)):
                score += 0.40
            if c.pending_deadline_hours is not None and c.pending_deadline_hours <= deadline_window_hours:
                proximity = 1.0 - (c.pending_deadline_hours / deadline_window_hours)
                score += 0.35 * max(0, proximity)
            elif c.pending_deadlines > 0:
                score += 0.10
            recent = timedelta(hours=48)
            if (c.last_synced and (now - c.last_synced) < recent) or (c.last_interaction and (now - c.last_interaction) < recent):
                score += 0.15
            if c.last_interaction and (now - c.last_interaction) < timedelta(hours=24):
                score += 0.10
            c.attention_score = min(round(score, 3), 1.0)
            c.recent_activity = bool(c.last_synced and (now - c.last_synced) < timedelta(days=7))
        max_pending = max((c.pending_deadlines for c in self._courses.values() if c.active), default=1)
        for c in self._courses.values():
            c.deadline_density = round(c.pending_deadlines / max(max_pending, 1), 2)

    def derive_scope(self, threshold: float = 0.25, max_courses: int = 10) -> list[str]:
        scored = [(cid, c.attention_score) for cid, c in self._courses.items() if c.active and c.attention_score >= threshold]
        scored.sort(key=lambda x: -x[1])
        return [cid for cid, _ in scored[:max_courses]]

    def get(self, course_id: str) -> ActiveCourse | None:
        return self._courses.get(course_id)

    def get_all(self) -> dict[str, ActiveCourse]:
        return dict(self._courses)

    @property
    def course_count(self) -> int:
        return len([c for c in self._courses.values() if c.active])

    def to_dict(self) -> dict[str, Any]:
        return {cid: {"course_id": c.course_id, "course_name": c.course_name,
            "last_schedule_hit": c.last_schedule_hit.isoformat() if c.last_schedule_hit else None,
            "last_homework_seen": c.last_homework_seen.isoformat() if c.last_homework_seen else None,
            "last_synced": c.last_synced.isoformat() if c.last_synced else None,
            "last_interaction": c.last_interaction.isoformat() if c.last_interaction else None,
            "deadline_density": c.deadline_density, "attention_score": c.attention_score,
            "pending_deadlines": c.pending_deadlines, "pending_deadline_hours": c.pending_deadline_hours,
            "recent_activity": c.recent_activity, "active": c.active} for cid, c in self._courses.items()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ActiveCourseRegistry":
        reg = cls()
        for cid, d in data.items():
            c = ActiveCourse(course_id=d.get("course_id", cid), course_name=d.get("course_name", ""),
                deadline_density=d.get("deadline_density", 0.0), attention_score=d.get("attention_score", 0.0),
                pending_deadlines=d.get("pending_deadlines", 0), pending_deadline_hours=d.get("pending_deadline_hours"),
                recent_activity=d.get("recent_activity", False), active=d.get("active", True))
            for tf in ("last_schedule_hit", "last_homework_seen", "last_synced", "last_interaction"):
                v = d.get(tf)
                if v:
                    try:
                        setattr(c, tf, datetime.fromisoformat(v))
                    except (ValueError, TypeError):
                        pass
            reg._courses[cid] = c
        return reg


# Legacy compatibility functions
def compute_active_courses(events: list[Event]) -> dict[str, dict[str, Any]]:
    now = datetime.now(timezone.utc)
    courses: dict[str, dict] = {}
    for e in events:
        if e.event_type == EventType.HOMEWORK_NEW:
            cn = e.payload.get("course", "")
            cid = e.payload.get("course_id", cn)
            ds = e.payload.get("deadline", "")
            if not cid:
                continue
            if cid not in courses:
                courses[cid] = {"course_id": cid, "display_name": cn, "total_homework": 0, "pending_homework": 0,
                    "closest_deadline": None, "last_seen": None, "events": []}
            c = courses[cid]
            c["total_homework"] += 1
            c["display_name"] = cn
            c["last_seen"] = e.timestamp.isoformat()
            c["events"].append(e.timestamp)
            if ds:
                try:
                    dl = datetime.fromisoformat(ds.replace("Z", "+00:00"))
                    if dl > now:
                        c["pending_homework"] += 1
                        if c["closest_deadline"] is None or dl < datetime.fromisoformat(c["closest_deadline"].replace("Z", "+00:00")):
                            c["closest_deadline"] = dl.isoformat()
                except (ValueError, TypeError):
                    pass
    for cid, c in courses.items():
        total = max(c["total_homework"], 1)
        c["deadline_density"] = round(c["pending_homework"] / total, 2)
        rc = now - timedelta(days=7)
        rc_count = sum(1 for ts in c["events"] if ts > rc)
        c["recent_activity"] = round(min(rc_count / max(total, 1), 1.0), 2)
        c["active_score"] = round(c["deadline_density"] * 0.7 + c["recent_activity"] * 0.3, 2)
        del c["events"]
    return courses


def get_active_scope(courses: dict[str, dict], threshold: float = 0.3, max_courses: int = 10) -> list[str]:
    scored = [(cid, c["active_score"]) for cid, c in courses.items() if c["active_score"] >= threshold]
    scored.sort(key=lambda x: -x[1])
    return [cid for cid, _ in scored[:max_courses]]
