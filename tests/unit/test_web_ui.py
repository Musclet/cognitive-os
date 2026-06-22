"""Tests: Web UI auth, dashboard, timeline, SPA serving."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, ".")

from src.core.events import AggregateType, Event, EventType
from src.interface.api.web_routes import router as web_router, COOKIE_NAME, _make_session_cookie, _validate_session


# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture
def app() -> FastAPI:
    """Create a minimal FastAPI app with the web router and mock state."""
    app = FastAPI()
    app.include_router(web_router)

    # Mock settings
    settings = MagicMock()
    settings.web_ui_pin = "1234"
    settings.web_ui_session_secret = "test-secret-key-for-hmac"
    settings.web_ui_session_days = 7
    settings.obsidian_vault_path = ""
    settings.telegram_allowed_users = [123]
    app.state.settings = settings

    # Mock state engine
    state_engine = MagicMock()
    state_engine._state = {}
    state_engine.get_all_derived.return_value = {
        "deadline_pressure": {"score": 0.0, "trend": "stable", "active_courses": 0, "overdue_count": 0, "closest_hours": None},
        "workload_density": {"score": 0.0, "total_pending": 0, "by_course": {}, "capacity_pressure": 0},
        "active_context": {"active_course_count": 0, "active_courses": [], "most_urgent": None},
    }
    app.state.state_engine = state_engine

    # Mock pipeline
    pipeline = MagicMock()
    pipeline.run = AsyncMock(return_value=[])
    app.state.pipeline = pipeline

    return app


class AsyncMock(MagicMock):
    async def __call__(self, *args, **kwargs):
        return super(AsyncMock, self).__call__(*args, **kwargs)


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


# ══════════════════════════════════════════════════════════════════════════════
# Auth tests
# ══════════════════════════════════════════════════════════════════════════════


class TestAuth:
    def test_login_success(self, client: TestClient):
        resp = client.post("/api/web/auth/login", json={"pin": "1234"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        # Cookie should be set
        assert COOKIE_NAME in resp.cookies

    def test_login_wrong_pin(self, client: TestClient):
        resp = client.post("/api/web/auth/login", json={"pin": "0000"})
        assert resp.status_code == 401
        data = resp.json()
        assert "invalid_pin" in data.get("detail", "")

    def test_login_empty_pin(self, client: TestClient):
        resp = client.post("/api/web/auth/login", json={"pin": ""})
        assert resp.status_code == 401

    def test_login_missing_body(self, client: TestClient):
        resp = client.post("/api/web/auth/login", json={})
        assert resp.status_code == 401

    def test_check_authenticated(self, client: TestClient):
        # Login first
        login_resp = client.post("/api/web/auth/login", json={"pin": "1234"})
        cookie = login_resp.cookies[COOKIE_NAME]

        # Check with cookie
        resp = client.get("/api/web/auth/check", cookies={COOKIE_NAME: cookie})
        assert resp.status_code == 200

    def test_empty_session_secret_is_stable_within_process(self, app: FastAPI):
        app.state.settings.web_ui_session_secret = ""
        client = TestClient(app)

        login_resp = client.post("/api/web/auth/login", json={"pin": "1234"})
        assert login_resp.status_code == 200
        cookie = login_resp.cookies[COOKIE_NAME]

        resp = client.get("/api/web/auth/check", cookies={COOKIE_NAME: cookie})
        assert resp.status_code == 200

    def test_check_unauthenticated(self, client: TestClient):
        resp = client.get("/api/web/auth/check")
        assert resp.status_code == 401

    def test_check_expired_cookie(self, client: TestClient):
        # Create an expired cookie
        secret = "test-secret-key-for-hmac"
        cookie = _make_session_cookie(secret, days=-1)  # negative = expired yesterday
        resp = client.get("/api/web/auth/check", cookies={COOKIE_NAME: cookie})
        assert resp.status_code == 401

    def test_check_invalid_cookie(self, client: TestClient):
        resp = client.get("/api/web/auth/check", cookies={COOKIE_NAME: "invalid.cookie.value"})
        assert resp.status_code == 401

    def test_logout(self, client: TestClient):
        resp = client.post("/api/web/auth/logout")
        assert resp.status_code == 200
        # Cookie should be deleted
        set_cookie = resp.headers.get("set-cookie", "")
        assert COOKIE_NAME in set_cookie

    def test_login_timing_safe(self):
        """Verify hmac.compare_digest is used for PIN comparison."""
        secret = "test-secret"
        cookie = _make_session_cookie(secret, days=7)
        assert _validate_session(cookie, secret) is True
        assert _validate_session(cookie, "wrong-secret") is False
        assert _validate_session("bad.data", secret) is False

    def test_dashboard_requires_auth(self, client: TestClient):
        resp = client.get("/api/web/dashboard")
        assert resp.status_code == 401

    def test_timeline_requires_auth(self, client: TestClient):
        resp = client.get("/api/web/timeline")
        assert resp.status_code == 401


# ══════════════════════════════════════════════════════════════════════════════
# Dashboard tests
# ══════════════════════════════════════════════════════════════════════════════


class TestDashboard:
    def _login(self, client: TestClient) -> str:
        resp = client.post("/api/web/auth/login", json={"pin": "1234"})
        return resp.cookies[COOKIE_NAME]

    def test_dashboard_structure(self, client: TestClient):
        cookie = self._login(client)
        resp = client.get("/api/web/dashboard", cookies={COOKIE_NAME: cookie})
        assert resp.status_code == 200
        data = resp.json()

        # Check all expected keys exist
        assert "today" in data
        assert "weekday" in data
        assert "deadline_pressure" in data
        assert "workload_density" in data
        assert "active_context" in data
        assert "homework" in data
        assert "homework_count" in data
        assert "homework_empty_reason" in data
        assert "today_schedule" in data
        assert "calendar_events" in data
        assert "temporal_blocks" in data
        assert "vocab_progress" in data
        assert "fitness" in data
        assert "finance" in data
        assert "art" in data
        assert "sync_health" in data

    def test_dashboard_sync_health_uses_sync_projection(self, client: TestClient, app: FastAPI):
        app.state.state_engine._state = {
            "sync": {
                "jwxt": {"status": "completed", "last_sync": "2026-06-05T03:00:00+00:00", "block_count": 8},
                "chaoxing": {"status": "failed", "last_sync_failed": "2026-06-05T03:01:00+00:00", "error": "auth failed"},
            }
        }

        cookie = self._login(client)
        resp = client.get("/api/web/dashboard", cookies={COOKIE_NAME: cookie})
        assert resp.status_code == 200
        sync = resp.json()["sync_health"]
        assert sync["jwxt"]["status"] == "completed"
        assert sync["jwxt"]["last_sync"] == "2026-06-05T03:00:00+00:00"
        assert sync["jwxt"]["count"] == 8
        assert sync["chaoxing"]["status"] == "failed"
        assert sync["chaoxing"]["error"] == "auth failed"

    def test_dashboard_mock_filtered_homework_has_explicit_empty_reason(
        self,
        client: TestClient,
        app: FastAPI,
    ):
        app.state.state_engine._state = {
            "homework": {
                "mock-1": {
                    "title": "第三章习题",
                    "course": "高等数学",
                    "status": "pending",
                }
            },
            "sync": {
                "chaoxing": {
                    "status": "completed",
                    "mock_enabled": True,
                    "pulled_count": 1,
                    "homework_count": 1,
                }
            },
        }

        cookie = self._login(client)
        resp = client.get("/api/web/dashboard", cookies={COOKIE_NAME: cookie})

        assert resp.status_code == 200
        data = resp.json()
        assert data["homework"] == []
        assert data["homework_hidden_count"] == 1
        assert data["homework_empty_reason"] == "homework_empty_mock_filtered"
        assert data["sync_health"]["chaoxing"]["mock_enabled"] is True

    def test_dashboard_real_chaoxing_homework_is_visible(self, client: TestClient, app: FastAPI):
        app.state.state_engine._state = {
            "homework": {
                "real-1": {
                    "title": "实验报告",
                    "course": "虚拟现实技术",
                    "status": "pending",
                }
            },
            "sync": {
                "chaoxing": {
                    "status": "completed",
                    "mock_enabled": False,
                    "pulled_count": 1,
                    "homework_count": 1,
                }
            },
        }

        cookie = self._login(client)
        resp = client.get("/api/web/dashboard", cookies={COOKIE_NAME: cookie})

        assert resp.status_code == 200
        data = resp.json()
        assert data["homework_count"] == 1
        assert data["homework"][0]["title"] == "实验报告"
        assert data["homework_empty_reason"] == ""

    def test_dashboard_sync_health_falls_back_to_calendar_and_vocab_state(self, client: TestClient, app: FastAPI):
        app.state.state_engine._state = {
            "temporal": {
                "projection": {
                    "calendar_sync": {
                        "status": "completed",
                        "completed_at": "2026-06-05T03:02:00+00:00",
                        "calendar_id": "primary",
                        "calendar_count": 2,
                        "count": 12,
                    }
                }
            },
            "vocab": {
                "momo": {
                    "sync_status": "failed",
                    "last_sync_completed": "2026-06-05T03:03:00+00:00",
                    "last_error": "npm sync failed",
                    "last_sync": "2026-06-05T00:00:00Z",
                    "stale": True,
                }
            },
        }

        cookie = self._login(client)
        resp = client.get("/api/web/dashboard", cookies={COOKIE_NAME: cookie})
        assert resp.status_code == 200
        sync = resp.json()["sync_health"]
        assert sync["google_calendar"]["status"] == "completed"
        assert sync["google_calendar"]["last_sync"] == "2026-06-05T03:02:00+00:00"
        assert sync["google_calendar"]["calendar_count"] == 2
        assert sync["google_calendar"]["count"] == 12
        assert sync["momo"]["status"] == "failed"
        assert sync["momo"]["error"] == "npm sync failed"
        assert sync["momo"]["external_last_sync"] == "2026-06-05T00:00:00Z"

    def test_dashboard_with_homework_state(self, client: TestClient, app: FastAPI):
        # Add homework state
        app.state.state_engine._state = {
            "homework": {
                "hw1": {
                    "title": "Test HW",
                    "course": "数学",
                    "deadline": "2026-06-10T23:59:00",
                    "status": "pending",
                }
            }
        }
        cookie = self._login(client)
        resp = client.get("/api/web/dashboard", cookies={COOKIE_NAME: cookie})
        assert resp.status_code == 200
        data = resp.json()
        assert data["homework_count"] == 1
        assert data["homework"][0]["title"] == "Test HW"

    def test_dashboard_applies_homework_feedback_overlay(self, client: TestClient, app: FastAPI):
        """Completed/skipped homework is hidden; delayed homework stays marked delayed."""
        app.state.state_engine._state = {
            "homework": {
                "hw_done": {"title": "Done HW", "course": "数学", "deadline": "2026-06-10T23:59:00", "status": "pending"},
                "hw_skip": {"title": "Skip HW", "course": "英语", "deadline": "2026-06-11T23:59:00", "status": "pending"},
                "hw_delay": {"title": "Delay HW", "course": "物理", "deadline": "2026-06-12T23:59:00", "status": "pending"},
                "hw_open": {"title": "Open HW", "course": "化学", "deadline": "2026-06-13T23:59:00", "status": "pending"},
            },
            "behavior": {
                "current": {
                    "feedback_log": [
                        {"task_id": "hw_done", "action": "unknown", "outcome": "completed", "outcome_timestamp": "2026-06-05T00:00:00+00:00"},
                        {"task_id": "hw_skip", "action": "skipped", "timestamp": "2026-06-05T00:01:00+00:00"},
                        {
                            "task_id": "hw_delay",
                            "action": "delayed",
                            "timestamp": "2026-06-05T00:02:00+00:00",
                            "delay_minutes": 30,
                            "delayed_until": "2026-06-05T00:32:00+00:00",
                        },
                    ]
                }
            },
        }
        cookie = self._login(client)
        resp = client.get("/api/web/dashboard", cookies={COOKIE_NAME: cookie})
        assert resp.status_code == 200
        data = resp.json()
        assert data["homework_count"] == 2
        assert data["homework_hidden_count"] == 2
        titles = {hw["title"] for hw in data["homework"]}
        assert titles == {"Delay HW", "Open HW"}
        delayed = next(hw for hw in data["homework"] if hw["id"] == "hw_delay")
        assert delayed["status"] == "delayed"
        assert delayed["feedback"]["delay_minutes"] == 30

    def test_dashboard_filters_excluded_and_closed_homework(self, client: TestClient, app: FastAPI):
        """Web dashboard must show only active-course unfinished homework."""
        app.state.state_engine._state = {
            "homework": {
                "hw_math": {"title": "第三章习题", "course": "高等数学", "deadline": "2026-06-10T23:59:00", "status": "pending"},
                "hw_english": {"title": "Essay", "course": "大学英语", "deadline": "2026-06-11T23:59:00", "status": "pending"},
                "hw_linear": {"title": "第四章作业", "course": "线性代数", "deadline": "2026-06-12T23:59:00", "status": "pending"},
                "hw_review": {"title": "已提交作业", "course": "虚拟现实技术", "deadline": "2026-06-13T23:59:00", "status": "待批阅"},
                "hw_done": {"title": "完成作业", "course": "虚拟现实技术", "deadline": "2026-06-14T23:59:00", "status": "submitted"},
                "hw_open": {"title": "实验报告", "course": "虚拟现实技术", "teacher": "张辉", "deadline": "2026-06-15T23:59:00", "status": "未提交"},
            },
        }
        cookie = self._login(client)
        resp = client.get("/api/web/dashboard", cookies={COOKIE_NAME: cookie})
        assert resp.status_code == 200
        data = resp.json()
        assert data["homework_count"] == 1
        assert data["homework_hidden_count"] == 5
        assert data["homework"][0]["title"] == "实验报告"
        assert data["homework"][0]["course"] == "虚拟现实技术（张辉）"

    def test_dashboard_with_finance_state(self, client: TestClient, app: FastAPI):
        app.state.state_engine._state = {
            "finance": {
                "monthly": {
                    "outflow": 120,
                    "inflow": 1000,
                    "by_category": {"necessary": 80, "outing": 40},
                    "outing_spent": 40,
                }
            }
        }
        cookie = self._login(client)
        resp = client.get("/api/web/dashboard", cookies={COOKIE_NAME: cookie})
        assert resp.status_code == 200
        data = resp.json()
        assert data["finance"]["monthly_spend"] == 120

    def test_dashboard_with_schedule(self, client: TestClient, app: FastAPI):
        import datetime
        today = datetime.date.today().isoformat()
        app.state.state_engine._state = {
            "schedule": {
                today: [
                    {"course": "数学", "start": "08:00", "end": "09:30", "location": "A101"}
                ]
            }
        }
        cookie = self._login(client)
        resp = client.get("/api/web/dashboard", cookies={COOKIE_NAME: cookie})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["today_schedule"]) == 1
        assert data["today_schedule"][0]["course"] == "数学"
        assert data["schedule_count"] == 1
        assert data["schedule_empty_reason"] == ""

    def test_dashboard_projects_today_jwxt_temporal_block(self, client: TestClient, app: FastAPI):
        from datetime import datetime, timedelta, timezone
        from src.core.temporal import TemporalSource, TimeBlock, TimeBlockType

        local_tz = timezone(timedelta(hours=8))
        start = datetime.now(local_tz).replace(hour=10, minute=10, second=0, microsecond=0)
        block = TimeBlock(
            block_id="jwxt-today",
            source=TemporalSource.JWXT,
            block_type=TimeBlockType.CLASS_LECTURE,
            start=start,
            end=start + timedelta(minutes=90),
            title="计算机图形学",
            location="实验楼 C-101",
            description="张老师",
            metadata={"teacher": "张老师"},
        )
        app.state.state_engine.get_temporal_blocks.return_value = [block]

        cookie = self._login(client)
        resp = client.get("/api/web/dashboard", cookies={COOKIE_NAME: cookie})

        assert resp.status_code == 200
        data = resp.json()
        assert data["schedule_count"] == 1
        assert data["schedule_empty_reason"] == ""
        assert data["today_schedule"] == [{
            "course": "计算机图形学",
            "start": "10:10",
            "end": "11:40",
            "location": "实验楼 C-101",
            "teacher": "张老师",
            "source": "jwxt",
        }]

    def test_dashboard_ignores_non_today_and_non_course_temporal_blocks(
        self,
        client: TestClient,
        app: FastAPI,
    ):
        from datetime import datetime, timedelta, timezone
        from src.core.temporal import TemporalSource, TimeBlock, TimeBlockType

        local_tz = timezone(timedelta(hours=8))
        today = datetime.now(local_tz).replace(hour=8, minute=20, second=0, microsecond=0)
        app.state.state_engine.get_temporal_blocks.return_value = [
            TimeBlock(
                block_id="jwxt-tomorrow",
                source=TemporalSource.JWXT,
                block_type=TimeBlockType.CLASS_LAB,
                start=today + timedelta(days=1),
                end=today + timedelta(days=1, minutes=90),
                title="明日实验",
            ),
            TimeBlock(
                block_id="workout-today",
                source=TemporalSource.SYSTEM,
                block_type=TimeBlockType.WORKOUT_BLOCK,
                start=today,
                end=today + timedelta(hours=1),
                title="力量训练",
            ),
            TimeBlock(
                block_id="jwxt-reminder",
                source=TemporalSource.JWXT,
                block_type=TimeBlockType.REMINDER,
                start=today,
                end=today + timedelta(minutes=10),
                title="教务提醒",
            ),
        ]

        cookie = self._login(client)
        resp = client.get("/api/web/dashboard", cookies={COOKIE_NAME: cookie})

        assert resp.status_code == 200
        data = resp.json()
        assert data["today_schedule"] == []
        assert data["schedule_count"] == 0
        assert data["schedule_empty_reason"] == "schedule_empty_no_blocks"

    def test_dashboard_deduplicates_schedule_and_temporal_block(self, client: TestClient, app: FastAPI):
        from datetime import datetime, timedelta, timezone
        from src.core.temporal import TemporalSource, TimeBlock, TimeBlockType

        local_tz = timezone(timedelta(hours=8))
        start = datetime.now(local_tz).replace(hour=8, minute=20, second=0, microsecond=0)
        app.state.state_engine._state = {
            "schedule": {
                start.date().isoformat(): [{
                    "course": "影视特效技术",
                    "start": start.isoformat(),
                    "end": (start + timedelta(minutes=90)).isoformat(),
                    "location": "教学楼 A-301",
                }]
            }
        }
        app.state.state_engine.get_temporal_blocks.return_value = [
            TimeBlock(
                block_id="jwxt-duplicate",
                source=TemporalSource.JWXT,
                block_type=TimeBlockType.CLASS_LECTURE,
                start=start,
                end=start + timedelta(minutes=90),
                title="影视特效技术",
                location="教学楼 A-301",
            )
        ]

        cookie = self._login(client)
        resp = client.get("/api/web/dashboard", cookies={COOKIE_NAME: cookie})

        assert resp.status_code == 200
        data = resp.json()
        assert data["schedule_count"] == 1
        assert len(data["today_schedule"]) == 1

    def test_dashboard_schedule_empty_reason_uses_jwxt_auth_failure(
        self,
        client: TestClient,
        app: FastAPI,
    ):
        app.state.state_engine._state = {
            "sync": {
                "jwxt": {
                    "status": "failed",
                    "error_code": "jwxt_cookie_expired",
                    "error": "教务登录失败：cookie 失效",
                }
            }
        }

        cookie = self._login(client)
        resp = client.get("/api/web/dashboard", cookies={COOKIE_NAME: cookie})

        assert resp.status_code == 200
        data = resp.json()
        assert data["schedule_count"] == 0
        assert data["schedule_empty_reason"] == "schedule_empty_auth_failed"
        assert data["sync_health"]["jwxt"]["error_code"] == "jwxt_cookie_expired"


# ══════════════════════════════════════════════════════════════════════════════
# Timeline tests
# ══════════════════════════════════════════════════════════════════════════════


class TestTimeline:
    def _login(self, client: TestClient) -> str:
        resp = client.post("/api/web/auth/login", json={"pin": "1234"})
        return resp.cookies[COOKIE_NAME]

    def test_timeline_empty(self, client: TestClient):
        cookie = self._login(client)
        resp = client.get("/api/web/timeline", cookies={COOKIE_NAME: cookie})
        assert resp.status_code == 200
        data = resp.json()
        assert "events" in data
        assert data["count"] == 0

    def test_timeline_with_schedule(self, client: TestClient, app: FastAPI):
        app.state.state_engine._state = {
            "schedule": {
                "2026-06-10": [
                    {"course": "物理", "start": "10:00", "end": "11:30"}
                ]
            }
        }
        cookie = self._login(client)
        resp = client.get("/api/web/timeline?date_str=2026-06-10", cookies={COOKIE_NAME: cookie})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["events"]) == 1
        assert data["events"][0]["source"] == "jwxt"

    def test_timeline_reads_latest_jwxt_temporal_blocks(self, client: TestClient, app: FastAPI):
        """A successful JWXT connector sync appears in Time without legacy schedule state."""
        from datetime import datetime, timedelta, timezone
        from src.core.temporal import TemporalSource, TimeBlock, TimeBlockType

        local_tz = timezone(timedelta(hours=8))
        start = datetime(2026, 6, 22, 10, 10, tzinfo=local_tz)
        block = TimeBlock(
            block_id="jwxt-latest-class",
            source=TemporalSource.JWXT,
            block_type=TimeBlockType.CLASS_LECTURE,
            start=start,
            end=start + timedelta(hours=1, minutes=40),
            title="毕业设计",
            location="敷文园D102",
            metadata={"teacher": "指导老师"},
        )
        block_key = "|".join([
            str(block.source),
            block.title,
            block.start.isoformat(),
            block.end.isoformat(),
        ])
        engine = app.state.state_engine
        engine._state = {}
        engine._temporal_blocks_by_day = {"2026-06-22": [block_key]}
        engine._temporal_blocks = {block_key: block}

        cookie = self._login(client)
        resp = client.get(
            "/api/web/timeline?date_str=2026-06-22",
            cookies={COOKIE_NAME: cookie},
        )

        assert resp.status_code == 200
        events = resp.json()["events"]
        assert len(events) == 1
        assert events[0]["source"] == "jwxt"
        assert events[0]["title"] == "毕业设计"
        assert events[0]["start"] == "2026-06-22T10:10:00+08:00"
        assert events[0]["teacher"] == "指导老师"

    def test_timeline_dedupes_legacy_and_temporal_jwxt(self, client: TestClient, app: FastAPI):
        """The same JWXT class is not shown twice when both state views exist."""
        from datetime import datetime, timedelta, timezone
        from src.core.temporal import TemporalSource, TimeBlock, TimeBlockType

        local_tz = timezone(timedelta(hours=8))
        start = datetime(2026, 6, 22, 10, 10, tzinfo=local_tz)
        block = TimeBlock(
            block_id="jwxt-dedup-class",
            source=TemporalSource.JWXT,
            block_type=TimeBlockType.CLASS_LECTURE,
            start=start,
            end=start + timedelta(hours=1, minutes=40),
            title="毕业设计",
            location="敷文园D102",
            metadata={"teacher": "指导老师"},
        )
        block_key = "|".join([
            str(block.source),
            block.title,
            block.start.isoformat(),
            block.end.isoformat(),
        ])
        engine = app.state.state_engine
        engine._state = {
            "schedule": {
                "2026-06-22": [{
                    "course": "毕业设计",
                    "start": "10:10",
                    "end": "11:50",
                    "location": "敷文园D102",
                }],
            },
        }
        engine._temporal_blocks_by_day = {"2026-06-22": [block_key]}
        engine._temporal_blocks = {block_key: block}

        cookie = self._login(client)
        resp = client.get(
            "/api/web/timeline?date_str=2026-06-22",
            cookies={COOKIE_NAME: cookie},
        )

        assert resp.status_code == 200
        classes = [
            event
            for event in resp.json()["events"]
            if event["source"] == "jwxt" and event["title"] == "毕业设计"
        ]
        assert len(classes) == 1
        assert classes[0]["teacher"] == "指导老师"

    def test_timeline_with_homework_deadline(self, client: TestClient, app: FastAPI):
        app.state.state_engine._state = {
            "homework": {
                "hw1": {
                    "title": "Due HW",
                    "course": "英语",
                    "deadline": "2026-06-15T23:59:00",
                    "status": "pending",
                }
            }
        }
        cookie = self._login(client)
        resp = client.get("/api/web/timeline?date_str=2026-06-15", cookies={COOKIE_NAME: cookie})
        assert resp.status_code == 200
        data = resp.json()
        homework_events = [e for e in data["events"] if e["source"] == "homework"]
        assert len(homework_events) == 1


# ══════════════════════════════════════════════════════════════════════════════
# Session helpers unit tests
# ══════════════════════════════════════════════════════════════════════════════


class TestSessionHelpers:
    def test_make_and_validate_session(self):
        secret = "my-secret"
        cookie = _make_session_cookie(secret, days=7)
        assert _validate_session(cookie, secret) is True

    def test_validate_wrong_secret(self):
        cookie = _make_session_cookie("secret1", days=7)
        assert _validate_session(cookie, "secret2") is False

    def test_validate_tampered_payload(self):
        secret = "secret"
        cookie = _make_session_cookie(secret, days=7)
        parts = cookie.split(".")
        # Tamper with payload
        tampered = "AAAA" + "." + parts[1]
        assert _validate_session(tampered, secret) is False

    def test_validate_malformed(self):
        assert _validate_session("no-dot", "secret") is False
        assert _validate_session("", "secret") is False
        assert _validate_session("a.b.c", "secret") is False


# ══════════════════════════════════════════════════════════════════════════════
# Action Gateway tests
# ══════════════════════════════════════════════════════════════════════════════


class TestActionGateway:
    def _login(self, client: TestClient) -> str:
        resp = client.post("/api/web/auth/login", json={"pin": "1234"})
        return resp.cookies[COOKIE_NAME]

    def test_unauthenticated_returns_401(self, client: TestClient):
        """No session cookie → 401."""
        resp = client.post("/api/web/actions", json={"text": "同步刷新数据"})
        assert resp.status_code == 401

    def test_sync_refresh_goes_through_pipeline(self, client: TestClient, app: FastAPI):
        """sync_refresh publishes scheduled trigger events through pipeline.run."""
        cookie = self._login(client)
        resp = client.post("/api/web/actions", json={"action": "sync_refresh"}, cookies={COOKIE_NAME: cookie})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["command_type"] == "sync_refresh"
        assert app.state.pipeline.run.call_count == 4
        events = [call.args[0] for call in app.state.pipeline.run.call_args_list]
        assert {event.payload["action"] for event in events} == {
            "check_homework",
            "schedule_daily_sync",
            "calendar_sync",
            "momo_vocab_sync",
        }
        assert all(event.event_type == EventType.SYSTEM_SCHEDULED_TRIGGER for event in events)

    def test_hydration_action_publishes_canonical_event(self, client: TestClient, app: FastAPI):
        """hydration_250 publishes HYDRATION_LOGGED, not a Telegram-only command."""
        cookie = self._login(client)
        resp = client.post("/api/web/actions", json={"action": "hydration_250"}, cookies={COOKIE_NAME: cookie})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["command_type"] == "quick_hydration"
        app.state.pipeline.run.assert_called_once()
        event = app.state.pipeline.run.call_args.args[0]
        assert event.event_type == EventType.HYDRATION_LOGGED
        assert event.payload["amount_ml"] == 250

    def test_bad_state_publishes_subjective_context(self, client: TestClient, app: FastAPI):
        cookie = self._login(client)
        resp = client.post("/api/web/actions", json={"action": "bad_state"}, cookies={COOKIE_NAME: cookie})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        event = app.state.pipeline.run.call_args.args[0]
        assert event.event_type == EventType.SUBJECTIVE_CONTEXT_ADDED
        assert event.payload["kind"] == "context"
        assert "状态差" in event.payload["text"]
        assert event.payload["expires_at"]

    def test_art_progress_publishes_canonical_event(self, client: TestClient, app: FastAPI):
        cookie = self._login(client)
        resp = client.post("/api/web/actions", json={"action": "complete_art_30"}, cookies={COOKIE_NAME: cookie})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        event = app.state.pipeline.run.call_args.args[0]
        assert event.event_type == EventType.ART_PROGRESS_RECORDED
        assert event.payload["completed_minutes"] == 30

    def test_undo_last_returns_needs_followup(self, client: TestClient, app: FastAPI):
        """undo_last needs a real action id and revert handler; do not fake success."""
        app.state.pipeline.run.reset_mock()
        cookie = self._login(client)
        resp = client.post("/api/web/actions", json={"action": "undo_last"}, cookies={COOKIE_NAME: cookie})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert data["command_type"] == "undo_last_action"
        assert data["needs_followup"] is True
        app.state.pipeline.run.assert_not_called()

    def test_read_only_show_today_returns_dashboard(self, client: TestClient, app: FastAPI):
        """show_today returns ok with dashboard, no pipeline mutation."""
        # Reset pipeline mock call count
        app.state.pipeline.run.reset_mock()
        cookie = self._login(client)
        resp = client.post("/api/web/actions", json={"action": "show_today"}, cookies={COOKIE_NAME: cookie})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["command_type"] == "show_today"
        assert data["needs_followup"] is False
        # Read-only commands return dashboard data
        assert "dashboard" in data
        # Pipeline should NOT have been called for read-only commands
        app.state.pipeline.run.assert_not_called()

    def test_read_only_check_homework_returns_dashboard(self, client: TestClient, app: FastAPI):
        """check_homework returns ok with dashboard, no pipeline."""
        app.state.pipeline.run.reset_mock()
        cookie = self._login(client)
        resp = client.post("/api/web/actions", json={"action": "check_homework"}, cookies={COOKIE_NAME: cookie})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["command_type"] == "check_homework"
        assert "dashboard" in data
        app.state.pipeline.run.assert_not_called()

    def test_finance_transaction_goes_through_pipeline(self, client: TestClient, app: FastAPI):
        """finance_transaction text no longer blocked; goes through pipeline as USER_COMMAND_RECEIVED."""
        app.state.pipeline.run.reset_mock()
        cookie = self._login(client)
        resp = client.post("/api/web/actions", json={"text": "花了50"}, cookies={COOKIE_NAME: cookie})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["command_type"] == "finance_transaction"
        assert data["needs_followup"] is False
        app.state.pipeline.run.assert_called_once()
        event = app.state.pipeline.run.call_args.args[0]
        assert event.event_type == EventType.USER_COMMAND_RECEIVED
        assert event.payload["command"] == "finance_transaction"

    def test_finance_income_goes_through_pipeline(self, client: TestClient, app: FastAPI):
        """Income text (生活费到账) goes through pipeline as finance_transaction, not blocked."""
        app.state.pipeline.run.reset_mock()
        cookie = self._login(client)
        resp = client.post("/api/web/actions", json={"text": "生活费到账1000"}, cookies={COOKIE_NAME: cookie})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["command_type"] == "finance_transaction"
        assert data["needs_followup"] is False
        app.state.pipeline.run.assert_called_once()
        event = app.state.pipeline.run.call_args.args[0]
        assert event.event_type == EventType.USER_COMMAND_RECEIVED
        # The finance domain handler would further classify income vs expense downstream
        assert event.payload["command"] == "finance_transaction"

    def test_complete_homework_publishes_planning_task_completed(self, client: TestClient, app: FastAPI):
        """complete_homework publishes completion + memory events via pipeline."""
        app.state.pipeline.run.reset_mock()
        cookie = self._login(client)
        resp = client.post("/api/web/actions", json={"action": "complete_homework"}, cookies={COOKIE_NAME: cookie})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["command_type"] == "generic_completion"
        assert app.state.pipeline.run.call_count == 2
        events = [call.args[0] for call in app.state.pipeline.run.call_args_list]
        assert events[0].event_type == EventType.PLANNING_TASK_COMPLETED
        assert events[0].payload["task_id"] == "作业"
        assert events[1].event_type == EventType.MEMORY_ENTRY_CREATED

    def test_complete_homework_uses_specific_homework_payload(self, client: TestClient, app: FastAPI):
        """Task card completion carries the concrete homework id/title."""
        app.state.pipeline.run.reset_mock()
        cookie = self._login(client)
        resp = client.post(
            "/api/web/actions",
            json={
                "action": "complete_homework",
                "payload": {"task_id": "hw-123", "title": "数据结构作业", "course": "数据结构"},
            },
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        events = [call.args[0] for call in app.state.pipeline.run.call_args_list]
        assert events[0].event_type == EventType.PLANNING_TASK_COMPLETED
        assert events[0].payload["task_id"] == "hw-123"
        assert events[0].payload["task"] == "数据结构作业"
        assert events[1].payload["content"] == "完成：数据结构作业"

    def test_skip_homework_publishes_skipped_event(self, client: TestClient, app: FastAPI):
        """Task card skip publishes PLANNING_RECOMMENDATION_SKIPPED."""
        app.state.pipeline.run.reset_mock()
        cookie = self._login(client)
        resp = client.post(
            "/api/web/actions",
            json={"action": "skip_homework", "payload": {"task_id": "hw-456", "title": "英语作业"}},
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["command_type"] == "task_skip"
        app.state.pipeline.run.assert_called_once()
        event = app.state.pipeline.run.call_args.args[0]
        assert event.event_type == EventType.PLANNING_RECOMMENDATION_SKIPPED
        assert event.payload["task_id"] == "hw-456"
        assert event.payload["task"] == "英语作业"

    def test_delay_homework_publishes_delayed_event(self, client: TestClient, app: FastAPI):
        """Task card delay publishes PLANNING_RECOMMENDATION_DELAYED with delay minutes."""
        app.state.pipeline.run.reset_mock()
        cookie = self._login(client)
        resp = client.post(
            "/api/web/actions",
            json={
                "action": "delay_homework_30",
                "payload": {"task_id": "hw-789", "title": "数学作业", "delay_minutes": 30},
            },
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["command_type"] == "task_delay"
        app.state.pipeline.run.assert_called_once()
        event = app.state.pipeline.run.call_args.args[0]
        assert event.event_type == EventType.PLANNING_RECOMMENDATION_DELAYED
        assert event.payload["task_id"] == "hw-789"
        assert event.payload["delay_minutes"] == 30
        assert event.payload["delayed_until"]

    def test_generic_completion_records_real_completion_events(self, client: TestClient, app: FastAPI):
        """Free-text completion records real completion + memory events."""
        app.state.pipeline.run.reset_mock()
        cookie = self._login(client)
        resp = client.post("/api/web/actions", json={"text": "完成了作业"}, cookies={COOKIE_NAME: cookie})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["command_type"] == "generic_completion"
        assert data["needs_followup"] is False
        assert app.state.pipeline.run.call_count == 2
        events = [call.args[0] for call in app.state.pipeline.run.call_args_list]
        assert events[0].event_type == EventType.PLANNING_TASK_COMPLETED
        assert events[0].payload["task_id"] == "完成了作业"
        assert events[1].event_type == EventType.MEMORY_ENTRY_CREATED
        assert events[1].causation_id == events[0].event_id

    def test_unsupported_cognitive_learning_returns_needs_followup(self, client: TestClient):
        """cognitive_learning still blocked — DeepSeek path is Telegram-private only."""
        cookie = self._login(client)
        resp = client.post("/api/web/actions", json={"text": "认知学习"}, cookies={COOKIE_NAME: cookie})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert data["needs_followup"] is True
        assert data["command_type"] == "cognitive_learning"

    def test_verbal_scheduling_creates_proposal(self, client: TestClient, app: FastAPI):
        """verbal_scheduling with parseable text creates EXECUTION_PROPOSAL_CREATED."""
        from src.core.events import EventType
        app.state.pipeline.run.reset_mock()
        cookie = self._login(client)
        # "明天中午十二点吃饭" triggers verbal_scheduling in parse_message
        resp = client.post("/api/web/actions", json={"text": "明天中午十二点吃饭"}, cookies={COOKIE_NAME: cookie})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["needs_followup"] is False
        assert data["command_type"] == "verbal_scheduling"
        assert "proposal" in data
        assert data["proposal"]["proposal_type"] == "create_calendar_block"
        assert data["proposal"]["target_system"] == "google_calendar"
        assert data["proposal"]["action_payload"]["title"] == "吃饭"
        assert "start" in data["proposal"]["action_payload"]
        assert "end" in data["proposal"]["action_payload"]
        # Pipeline was called with EXECUTION_PROPOSAL_CREATED
        app.state.pipeline.run.assert_called_once()
        event = app.state.pipeline.run.call_args.args[0]
        assert event.event_type == EventType.EXECUTION_PROPOSAL_CREATED

    def test_verbal_scheduling_unparseable_returns_needs_followup(self, client: TestClient, app: FastAPI):
        """verbal_scheduling with no time indicator returns needs_followup."""
        app.state.pipeline.run.reset_mock()
        cookie = self._login(client)
        # "后天吃饭" has no time indicator → unparseable
        resp = client.post("/api/web/actions", json={"text": "后天吃饭"}, cookies={COOKIE_NAME: cookie})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert data["needs_followup"] is True
        assert data["command_type"] == "verbal_scheduling"
        app.state.pipeline.run.assert_not_called()

    def test_proposal_reject_publishes_rejected_event(self, client: TestClient, app: FastAPI):
        """Rejecting a Web proposal publishes EXECUTION_PROPOSAL_REJECTED."""
        from src.core.proposal import Proposal, ProposalType, TargetSystem
        from src.core.events import EventType

        proposal = Proposal(
            proposal_type=ProposalType.CREATE_CALENDAR_BLOCK,
            target_system=TargetSystem.GOOGLE_CALENDAR,
            action_payload={"title": "吃饭", "start": "2026-06-06T12:00:00+08:00", "end": "2026-06-06T13:00:00+08:00"},
            user_id="123",
        )

        app.state.pipeline.run.reset_mock()
        app.state.pipeline.run.return_value = []
        cookie = self._login(client)
        resp = client.post(
            "/api/web/proposals/decision",
            json={"decision": "reject", "proposal": proposal.to_dict()},
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["decision"] == "reject"
        app.state.pipeline.run.assert_called_once()
        event = app.state.pipeline.run.call_args.args[0]
        assert event.event_type == EventType.EXECUTION_PROPOSAL_REJECTED
        assert event.payload["status"] == "rejected"

    def test_proposal_accept_write_disabled_returns_failure(self, client: TestClient, app: FastAPI):
        """Accepting with real writes disabled returns a failure, not fake success."""
        from src.core.proposal import Proposal, ProposalType, TargetSystem
        from src.core.events import EventType

        app.state.settings.google_calendar_mock = False
        app.state.settings.google_calendar_write_enabled = False
        app.state.settings.google_calendar_write_requires_acceptance = True
        proposal = Proposal(
            proposal_type=ProposalType.CREATE_CALENDAR_BLOCK,
            target_system=TargetSystem.GOOGLE_CALENDAR,
            action_payload={"title": "吃饭", "start": "2026-06-06T12:00:00+08:00", "end": "2026-06-06T13:00:00+08:00"},
            user_id="123",
        )

        app.state.pipeline.run.reset_mock()
        app.state.pipeline.run.return_value = []
        cookie = self._login(client)
        resp = client.post(
            "/api/web/proposals/decision",
            json={"decision": "accept", "proposal": proposal.to_dict()},
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert data["needs_followup"] is True
        assert "calendar_write_disabled" in data["error"]
        assert "未开启" in data["message"]
        events = [call.args[0] for call in app.state.pipeline.run.call_args_list]
        assert EventType.EXECUTION_PROPOSAL_ACCEPTED in [e.event_type for e in events]
        assert EventType.EXECUTION_REQUESTED in [e.event_type for e in events]
        assert EventType.EXECUTION_FAILED in [e.event_type for e in events]
        assert EventType.CALENDAR_EVENT_CREATED not in [e.event_type for e in events]

    def test_proposal_accept_mock_reports_not_persisted(self, client: TestClient, app: FastAPI):
        """Mock calendar mode must not claim a persisted calendar event."""
        from src.core.proposal import Proposal, ProposalType, TargetSystem
        from src.core.events import EventType

        app.state.settings.google_calendar_mock = True
        app.state.settings.google_calendar_write_enabled = True
        app.state.settings.google_calendar_write_requires_acceptance = True
        proposal = Proposal(
            proposal_type=ProposalType.CREATE_CALENDAR_BLOCK,
            target_system=TargetSystem.GOOGLE_CALENDAR,
            action_payload={
                "title": "吃饭",
                "start": "2026-06-06T12:00:00+08:00",
                "end": "2026-06-06T13:00:00+08:00",
                "calendar_id": "primary",
            },
            user_id="123",
        )

        app.state.pipeline.run.reset_mock()
        app.state.pipeline.run.return_value = []
        cookie = self._login(client)
        resp = client.post(
            "/api/web/proposals/decision",
            json={"decision": "accept", "proposal": proposal.to_dict()},
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert data["needs_followup"] is True
        assert data["error_code"] == "calendar_mock_enabled"
        assert data["event"]["error"] == "calendar_mock_enabled"
        app.state.pipeline.run.assert_not_called()

    def test_finance_transaction_includes_action_id(self, client: TestClient, app: FastAPI):
        """Finance transaction response includes action_id when events produced."""
        from src.core.events import Event, EventType, AggregateType
        # Make pipeline return FINANCE_TRANSACTION_RECORDED
        app.state.pipeline.run.reset_mock()
        app.state.pipeline.run.return_value = [
            Event(
                event_type=EventType.FINANCE_TRANSACTION_RECORDED,
                aggregate_id="123",
                aggregate_type=AggregateType.FINANCE,
                payload={"amount": 50, "category": "food", "description": "花了50"},
            )
        ]
        cookie = self._login(client)
        resp = client.post("/api/web/actions", json={"text": "花了50"}, cookies={COOKIE_NAME: cookie})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["action_id"] is not None
        assert data["action_type"] == "finance_transaction"

    def test_finance_income_includes_action_id(self, client: TestClient, app: FastAPI):
        """Finance income response includes action_id when events produced."""
        from src.core.events import Event, EventType, AggregateType
        app.state.pipeline.run.reset_mock()
        app.state.pipeline.run.return_value = [
            Event(
                event_type=EventType.FINANCE_INCOME_RECORDED,
                aggregate_id="123",
                aggregate_type=AggregateType.FINANCE,
                payload={"amount": 1000, "source": "生活费", "description": "生活费到账1000"},
            )
        ]
        cookie = self._login(client)
        resp = client.post("/api/web/actions", json={"text": "生活费到账1000"}, cookies={COOKIE_NAME: cookie})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["action_id"] is not None
        assert data["action_type"] == "finance_income"

    def test_undo_valid_action(self, client: TestClient, app: FastAPI):
        """Undo endpoint publishes USER_ACTION_REVERTED for tracked action."""
        from src.core.events import Event, EventType, AggregateType
        from src.interface.api.web_routes import _web_recent_actions, _reset_web_action_cache
        _reset_web_action_cache()

        # Pre-populate a tracked action
        test_action_id = "web-test-undo-001"
        _web_recent_actions[test_action_id] = {
            "action_type": "finance_transaction",
            "params": {"amount": 50, "category": "food"},
            "user_id": "123",
            "reverted": False,
            "timestamp": "2026-06-05T12:00:00+00:00",
        }

        app.state.pipeline.run.reset_mock()
        app.state.pipeline.run.return_value = []
        cookie = self._login(client)
        resp = client.post("/api/web/actions/undo", json={"action_id": test_action_id}, cookies={COOKIE_NAME: cookie})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["needs_followup"] is False

        # Should have called USER_UNDO_REQUESTED + USER_ACTION_REVERTED
        assert app.state.pipeline.run.call_count == 2
        calls = [call.args[0] for call in app.state.pipeline.run.call_args_list]
        assert calls[0].event_type == EventType.USER_UNDO_REQUESTED
        assert calls[1].event_type == EventType.USER_ACTION_REVERTED
        assert calls[1].payload["action_type"] == "finance_transaction"
        assert calls[1].payload["amount"] == 50

    def test_recent_actions_lists_current_user_actions(self, client: TestClient):
        """Recent actions endpoint is scoped to the current Web user."""
        from src.interface.api.web_routes import _web_recent_actions, _reset_web_action_cache
        _reset_web_action_cache()

        _web_recent_actions["web-old"] = {
            "action_type": "finance_income",
            "params": {"amount": 100},
            "user_id": "123",
            "reverted": False,
            "timestamp": "2026-06-05T10:00:00+00:00",
        }
        _web_recent_actions["web-new"] = {
            "action_type": "finance_transaction",
            "params": {"amount": 20, "category": "food"},
            "user_id": "123",
            "reverted": False,
            "timestamp": "2026-06-05T12:00:00+00:00",
        }
        _web_recent_actions["web-other-user"] = {
            "action_type": "finance_transaction",
            "params": {"amount": 999, "category": "other"},
            "user_id": "999",
            "reverted": False,
            "timestamp": "2026-06-05T13:00:00+00:00",
        }

        cookie = self._login(client)
        resp = client.get("/api/web/actions/recent", cookies={COOKIE_NAME: cookie})
        assert resp.status_code == 200
        actions = resp.json()["actions"]

        assert [item["action_id"] for item in actions] == ["web-new", "web-old"]
        assert actions[0]["label"] == "消费记录 ¥20 · food"
        assert actions[0]["can_undo"] is True
        assert actions[1]["label"] == "收入记录 ¥100"

    def test_recent_actions_marks_reverted_after_undo(self, client: TestClient, app: FastAPI):
        """Undo state is visible from the recent actions list."""
        from src.interface.api.web_routes import _web_recent_actions, _reset_web_action_cache
        _reset_web_action_cache()

        test_action_id = "web-test-recent-reverted"
        _web_recent_actions[test_action_id] = {
            "action_type": "finance_transaction",
            "params": {"amount": 35, "category": "daily"},
            "user_id": "123",
            "reverted": False,
            "timestamp": "2026-06-05T12:00:00+00:00",
        }

        app.state.pipeline.run.reset_mock()
        app.state.pipeline.run.return_value = []
        cookie = self._login(client)
        undo_resp = client.post("/api/web/actions/undo", json={"action_id": test_action_id}, cookies={COOKIE_NAME: cookie})
        assert undo_resp.status_code == 200
        assert undo_resp.json()["ok"] is True

        resp = client.get("/api/web/actions/recent", cookies={COOKIE_NAME: cookie})
        assert resp.status_code == 200
        action = resp.json()["actions"][0]
        assert action["action_id"] == test_action_id
        assert action["reverted"] is True
        assert action["can_undo"] is False

    def test_recent_actions_requires_auth(self, client: TestClient):
        """Recent actions endpoint requires a valid Web session."""
        resp = client.get("/api/web/actions/recent")
        assert resp.status_code == 401

    def test_undo_completion_record_rejected(self, client: TestClient, app: FastAPI):
        """Completion undo is rejected until it can remove memory/log side effects."""
        from src.interface.api.web_routes import _web_recent_actions, _reset_web_action_cache
        _reset_web_action_cache()

        test_action_id = "web-test-undo-cr-001"
        _web_recent_actions[test_action_id] = {
            "action_type": "completion_record",
            "params": {},
            "user_id": "123",
            "reverted": False,
            "timestamp": "2026-06-05T12:00:00+00:00",
        }

        app.state.pipeline.run.reset_mock()
        app.state.pipeline.run.return_value = []
        cookie = self._login(client)
        resp = client.post("/api/web/actions/undo", json={"action_id": test_action_id}, cookies={COOKIE_NAME: cookie})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert data["needs_followup"] is True
        app.state.pipeline.run.assert_not_called()

    def test_undo_unsafe_proposal_rejected(self, client: TestClient, app: FastAPI):
        """Undo for calendar proposal without event_id is rejected."""
        from src.interface.api.web_routes import _web_recent_actions, _reset_web_action_cache
        _reset_web_action_cache()

        test_action_id = "web-test-undo-prop-001"
        _web_recent_actions[test_action_id] = {
            "action_type": "verbal_scheduling",
            "params": {},  # no event_id
            "user_id": "123",
            "reverted": False,
            "timestamp": "2026-06-05T12:00:00+00:00",
        }

        app.state.pipeline.run.reset_mock()
        cookie = self._login(client)
        resp = client.post("/api/web/actions/undo", json={"action_id": test_action_id}, cookies={COOKIE_NAME: cookie})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert data["needs_followup"] is True
        # Pipeline should NOT have been called
        app.state.pipeline.run.assert_not_called()

    def test_undo_already_reverted(self, client: TestClient, app: FastAPI):
        """Already reverted action cannot be undone again."""
        from src.interface.api.web_routes import _web_recent_actions, _reset_web_action_cache
        _reset_web_action_cache()

        test_action_id = "web-test-undo-repeat"
        _web_recent_actions[test_action_id] = {
            "action_type": "finance_transaction",
            "params": {"amount": 20, "category": "food"},
            "user_id": "123",
            "reverted": True,
            "timestamp": "2026-06-05T12:00:00+00:00",
        }

        app.state.pipeline.run.reset_mock()
        cookie = self._login(client)
        resp = client.post("/api/web/actions/undo", json={"action_id": test_action_id}, cookies={COOKIE_NAME: cookie})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert data["needs_followup"] is True
        app.state.pipeline.run.assert_not_called()

    def test_undo_not_found(self, client: TestClient, app: FastAPI):
        """Unknown action_id returns not found."""
        app.state.pipeline.run.reset_mock()
        cookie = self._login(client)
        resp = client.post("/api/web/actions/undo", json={"action_id": "nonexistent"}, cookies={COOKIE_NAME: cookie})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert data["needs_followup"] is True
        app.state.pipeline.run.assert_not_called()

    def test_undo_requires_auth(self, client: TestClient):
        """Undo endpoint requires session."""
        resp = client.post("/api/web/actions/undo", json={"action_id": "test"})
        assert resp.status_code == 401

    def test_empty_body_returns_400(self, client: TestClient):
        cookie = self._login(client)
        resp = client.post("/api/web/actions", json={}, cookies={COOKIE_NAME: cookie})
        assert resp.status_code == 400
        data = resp.json()
        assert data["ok"] is False

    def test_missing_pipeline_returns_503_for_mutating(self, client: TestClient, app: FastAPI):
        """When pipeline is None, mutating action returns 503."""
        app.state.pipeline = None
        cookie = self._login(client)
        resp = client.post("/api/web/actions", json={"action": "sync_refresh"}, cookies={COOKIE_NAME: cookie})
        assert resp.status_code == 503
        data = resp.json()
        assert data["ok"] is False
        assert data["needs_followup"] is True


# ══════════════════════════════════════════════════════════════════════════════
# Calendar proposal tests
# ══════════════════════════════════════════════════════════════════════════════


class TestCalendarProposal:
    def _login(self, client: TestClient) -> str:
        resp = client.post("/api/web/auth/login", json={"pin": "1234"})
        return resp.cookies[COOKIE_NAME]

    def test_requires_auth(self, client: TestClient):
        """POST /api/web/calendar/proposal requires session."""
        resp = client.post("/api/web/calendar/proposal", json={"action": "create"})
        assert resp.status_code == 401

    def test_create_returns_proposal_no_direct_write(self, client: TestClient, app: FastAPI):
        """Create proposal returns proposal JSON and does NOT write directly."""
        app.state.pipeline.run.reset_mock()
        app.state.pipeline.run.return_value = []
        cookie = self._login(client)
        resp = client.post(
            "/api/web/calendar/proposal",
            json={"action": "create", "title": "测试事件", "date": "2026-06-10", "start_time": "10:00"},
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "proposal" in data
        assert data["proposal"]["proposal_type"] == "create_calendar_block"
        assert data["proposal"]["target_system"] == "google_calendar"
        assert data["proposal"]["action_payload"]["title"] == "测试事件"
        # Pipeline should have been called once with EXECUTION_PROPOSAL_CREATED
        app.state.pipeline.run.assert_called_once()
        event = app.state.pipeline.run.call_args.args[0]
        assert event.event_type == EventType.EXECUTION_PROPOSAL_CREATED

    def test_create_missing_required_fields(self, client: TestClient):
        """Create without title/date/start_time returns 400."""
        cookie = self._login(client)
        resp = client.post(
            "/api/web/calendar/proposal",
            json={"action": "create", "title": "test"},
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 400

    def test_create_rejects_end_before_start(self, client: TestClient):
        """Create rejects invalid time ranges before proposal creation."""
        cookie = self._login(client)
        resp = client.post(
            "/api/web/calendar/proposal",
            json={
                "action": "create",
                "title": "bad range",
                "date": "2026-06-10",
                "start_time": "12:00",
                "end_time": "11:00",
            },
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 400
        assert "end_time" in resp.json()["message"]

    def test_invalid_action(self, client: TestClient):
        """Invalid action string returns 400."""
        cookie = self._login(client)
        resp = client.post(
            "/api/web/calendar/proposal",
            json={"action": "invalid_action"},
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 400

    def test_update_returns_proposal(self, client: TestClient, app: FastAPI):
        """Update proposal returns UPDATE_CALENDAR_EVENT proposal."""
        app.state.pipeline.run.reset_mock()
        app.state.pipeline.run.return_value = []
        cookie = self._login(client)
        resp = client.post(
            "/api/web/calendar/proposal",
            json={
                "action": "update",
                "title": "更新事件",
                "event_id": "mock-event-123",
                "calendar_id": "primary",
                "date": "2026-06-10",
                "start_time": "11:00",
            },
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["proposal"]["proposal_type"] == "update_calendar_event"
        assert data["proposal"]["action_payload"]["event_id"] == "mock-event-123"

    def test_update_rejects_invalid_time(self, client: TestClient):
        """Update rejects invalid date/time instead of silently dropping timing fields."""
        cookie = self._login(client)
        resp = client.post(
            "/api/web/calendar/proposal",
            json={
                "action": "update",
                "title": "更新事件",
                "event_id": "mock-event-123",
                "date": "bad-date",
                "start_time": "11:00",
            },
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 400

    def test_update_rejects_end_before_start(self, client: TestClient):
        """Update rejects invalid time ranges before proposal creation."""
        cookie = self._login(client)
        resp = client.post(
            "/api/web/calendar/proposal",
            json={
                "action": "update",
                "title": "更新事件",
                "event_id": "mock-event-123",
                "date": "2026-06-10",
                "start_time": "12:00",
                "end_time": "11:00",
            },
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 400

    def test_update_missing_event_id(self, client: TestClient):
        """Update without event_id returns 400."""
        cookie = self._login(client)
        resp = client.post(
            "/api/web/calendar/proposal",
            json={"action": "update", "title": "test"},
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 400

    def test_delete_returns_proposal(self, client: TestClient, app: FastAPI):
        """Delete proposal returns DELETE_CALENDAR_EVENT proposal."""
        app.state.pipeline.run.reset_mock()
        app.state.pipeline.run.return_value = []
        cookie = self._login(client)
        resp = client.post(
            "/api/web/calendar/proposal",
            json={"action": "delete", "event_id": "mock-event-456", "calendar_id": "primary"},
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["proposal"]["proposal_type"] == "delete_calendar_event"
        assert data["proposal"]["action_payload"]["event_id"] == "mock-event-456"

    def test_delete_missing_event_id(self, client: TestClient):
        """Delete without event_id returns 400."""
        cookie = self._login(client)
        resp = client.post(
            "/api/web/calendar/proposal",
            json={"action": "delete"},
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 400

    def test_timeline_includes_event_id_from_temporal_blocks(self, client: TestClient, app: FastAPI):
        """Timeline endpoint includes event_id/calendar_id from temporal block metadata."""
        from datetime import datetime, timezone, timedelta
        from src.core.temporal import TemporalSource, TimeBlock, TimeBlockType

        now = datetime.now(timezone.utc)
        block = TimeBlock(
            block_id="test-block-1",
            source=TemporalSource.GOOGLE_CALENDAR,
            block_type=TimeBlockType.CALENDAR_EVENT,
            start=now,
            end=now + timedelta(hours=1),
            title="Test Calendar Event",
            location="Test Location",
            metadata={
                "external_source": "google_calendar",
                "external_id": "gcal-event-001",
                "calendar_id": "primary",
            },
        )
        day_key = now.date().isoformat()
        block_key = "|".join(["google_calendar", block.title, block.start.isoformat(), block.end.isoformat()])

        engine = app.state.state_engine
        engine._temporal_blocks_by_day = {day_key: [block_key]}
        engine._temporal_blocks = {block_key: block}

        cookie = self._login(client)
        resp = client.get(f"/api/web/timeline?date_str={day_key}", cookies={COOKIE_NAME: cookie})
        assert resp.status_code == 200
        data = resp.json()
        cal_events = [e for e in data["events"] if e["source"] == "google_calendar"]
        assert len(cal_events) >= 1
        found = any(e.get("event_id") == "gcal-event-001" and e.get("calendar_id") == "primary" for e in cal_events)
        assert found, f"Expected event_id=gcal-event-001 in google_calendar events: {cal_events}"

    def test_timeline_returns_google_event_in_local_timezone(self, client: TestClient, app: FastAPI):
        """Timeline returns Google event timestamps in the app's UTC+8 timezone."""
        from datetime import datetime, timezone, timedelta
        from src.core.temporal import TemporalSource, TimeBlock, TimeBlockType

        start = datetime(2026, 6, 22, 6, 30, tzinfo=timezone.utc)
        block = TimeBlock(
            block_id="local-time-block",
            source=TemporalSource.GOOGLE_CALENDAR,
            block_type=TimeBlockType.MEETING_BLOCK,
            start=start,
            end=start + timedelta(hours=1, minutes=30),
            title="毕设会议",
            metadata={
                "external_source": "google_calendar",
                "external_id": "gcal-local-time-001",
                "calendar_id": "primary",
            },
        )
        day_key = "2026-06-22"
        block_key = "|".join(["google_calendar", block.title, block.start.isoformat(), block.end.isoformat()])

        engine = app.state.state_engine
        engine._temporal_blocks_by_day = {day_key: [block_key]}
        engine._temporal_blocks = {block_key: block}

        cookie = self._login(client)
        resp = client.get(f"/api/web/timeline?date_str={day_key}", cookies={COOKIE_NAME: cookie})

        assert resp.status_code == 200
        event = next(
            item
            for item in resp.json()["events"]
            if item.get("event_id") == "gcal-local-time-001"
        )
        assert event["start"] == "2026-06-22T14:30:00+08:00"
        assert event["end"] == "2026-06-22T16:00:00+08:00"

    def test_create_returns_conflicts_for_overlapping_jwxt(self, client: TestClient, app: FastAPI):
        """Create proposal returns conflicts when overlapping a JWXT class."""
        app.state.pipeline.run.reset_mock()
        app.state.pipeline.run.return_value = []
        app.state.state_engine._state = {
            "schedule": {
                "2026-06-10": [
                    {"course": "数学", "start": "10:00", "end": "11:30", "location": "A101"}
                ]
            }
        }
        cookie = self._login(client)
        resp = client.post(
            "/api/web/calendar/proposal",
            json={
                "action": "create", "title": "重叠事件",
                "date": "2026-06-10", "start_time": "10:30", "end_time": "11:00",
            },
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "conflicts" in data
        assert len(data["conflicts"]) == 1
        assert data["conflicts"][0]["source"] == "jwxt"
        assert data["conflicts"][0]["title"] == "数学"

    def test_create_no_conflict_when_no_overlap(self, client: TestClient, app: FastAPI):
        """Create proposal has empty conflicts when no overlap exists."""
        app.state.pipeline.run.reset_mock()
        app.state.pipeline.run.return_value = []
        app.state.state_engine._state = {
            "schedule": {
                "2026-06-10": [
                    {"course": "数学", "start": "10:00", "end": "11:30"}
                ]
            }
        }
        cookie = self._login(client)
        resp = client.post(
            "/api/web/calendar/proposal",
            json={
                "action": "create", "title": "无冲突",
                "date": "2026-06-10", "start_time": "14:00", "end_time": "15:00",
            },
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        conflicts = data.get("conflicts", [])
        assert len(conflicts) == 0

    def test_update_excludes_own_event_id_from_conflict(self, client: TestClient, app: FastAPI):
        """Update proposal excludes the same event_id from conflict detection."""
        app.state.pipeline.run.reset_mock()
        app.state.pipeline.run.return_value = []
        app.state.state_engine._state = {
            "calendar": {
                "2026-06-10": [
                    {
                        "summary": "自己",
                        "start": "2026-06-10T10:00:00+08:00",
                        "end": "2026-06-10T11:30:00+08:00",
                        "event_id": "my-event-001",
                    }
                ]
            }
        }
        cookie = self._login(client)
        # Update the same event to slightly different time — should not conflict with itself
        resp = client.post(
            "/api/web/calendar/proposal",
            json={
                "action": "update", "title": "自己更新",
                "event_id": "my-event-001",
                "date": "2026-06-10", "start_time": "10:00", "end_time": "11:30",
            },
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        conflicts = data.get("conflicts", [])
        assert len(conflicts) == 0, f"Expected no conflicts when updating own event, got {conflicts}"

    def test_create_invalid_start_time_returns_400(self, client: TestClient):
        """Create with invalid start_time returns 400, not 500."""
        cookie = self._login(client)
        resp = client.post(
            "/api/web/calendar/proposal",
            json={
                "action": "create", "title": "bad time",
                "date": "2026-06-10", "start_time": "25:00",
            },
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 400
        data = resp.json()
        assert data["ok"] is False

    def test_create_non_numeric_start_time_returns_400(self, client: TestClient):
        """Create with non-numeric start_time returns 400, not 500."""
        cookie = self._login(client)
        resp = client.post(
            "/api/web/calendar/proposal",
            json={
                "action": "create", "title": "bad time",
                "date": "2026-06-10", "start_time": "abc",
            },
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 400
        data = resp.json()
        assert data["ok"] is False

    def test_delete_has_no_conflicts(self, client: TestClient, app: FastAPI):
        """Delete proposal response does not contain conflicts."""
        app.state.pipeline.run.reset_mock()
        app.state.pipeline.run.return_value = []
        cookie = self._login(client)
        resp = client.post(
            "/api/web/calendar/proposal",
            json={"action": "delete", "event_id": "mock-event-789"},
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data.get("conflicts") is None or data["conflicts"] == []

    def test_create_conflicts_with_calendar_state(self, client: TestClient, app: FastAPI):
        """Create proposal returns conflicts with google_calendar events."""
        app.state.pipeline.run.reset_mock()
        app.state.pipeline.run.return_value = []
        app.state.state_engine._state = {
            "calendar": {
                "2026-06-10": [
                    {
                        "summary": "会议",
                        "start": "2026-06-10T14:00:00+08:00",
                        "end": "2026-06-10T15:00:00+08:00",
                        "event_id": "gcal-001",
                    }
                ]
            }
        }
        cookie = self._login(client)
        resp = client.post(
            "/api/web/calendar/proposal",
            json={
                "action": "create", "title": "重叠",
                "date": "2026-06-10", "start_time": "14:30", "end_time": "15:30",
            },
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "conflicts" in data
        gcal_conflicts = [c for c in data["conflicts"] if c["source"] == "google_calendar"]
        assert len(gcal_conflicts) >= 1
        assert "会议" in gcal_conflicts[0]["title"]

    def test_create_conflicts_with_temporal_google_block(self, client: TestClient, app: FastAPI):
        """Create proposal detects richer Google Calendar temporal blocks."""
        from datetime import datetime, timezone, timedelta
        from src.core.temporal import TemporalSource, TimeBlock, TimeBlockType

        app.state.pipeline.run.reset_mock()
        app.state.pipeline.run.return_value = []

        start = datetime(2026, 6, 10, 14, 0, tzinfo=timezone(timedelta(hours=8)))
        block = TimeBlock(
            block_id="temporal-gcal-conflict",
            source=TemporalSource.GOOGLE_CALENDAR,
            block_type=TimeBlockType.CALENDAR_EVENT,
            start=start,
            end=start + timedelta(hours=1),
            title="Temporal Google 会议",
            location="Room T",
            metadata={
                "external_source": "google_calendar",
                "external_id": "temporal-event-001",
                "calendar_id": "primary",
            },
        )
        block_key = "|".join(["google_calendar", block.title, block.start.isoformat(), block.end.isoformat()])
        engine = app.state.state_engine
        engine._state = {}
        engine._temporal_blocks_by_day = {"2026-06-10": [block_key]}
        engine._temporal_blocks = {block_key: block}

        cookie = self._login(client)
        resp = client.post(
            "/api/web/calendar/proposal",
            json={
                "action": "create",
                "title": "重叠 temporal",
                "date": "2026-06-10",
                "start_time": "14:30",
                "end_time": "15:00",
            },
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 200
        conflicts = resp.json().get("conflicts", [])
        assert any(
            c["source"] == "google_calendar"
            and c.get("event_id") == "temporal-event-001"
            and c["title"] == "Temporal Google 会议"
            for c in conflicts
        )

    def test_timeline_dedupes_calendar_state_and_temporal_block(self, client: TestClient, app: FastAPI):
        """Timeline prefers the temporal copy with event_id when duplicate calendar data exists."""
        from datetime import datetime, timezone, timedelta
        from src.core.temporal import TemporalSource, TimeBlock, TimeBlockType

        now = datetime.now(timezone.utc).replace(microsecond=0)
        day_key = now.date().isoformat()
        title = "Duplicated Calendar Event"
        start = now.isoformat()
        end = (now + timedelta(hours=1)).isoformat()

        engine = app.state.state_engine
        engine._state = {
            "calendar": {
                day_key: [{
                    "summary": title,
                    "start": start,
                    "end": end,
                    "location": "Room A",
                }]
            }
        }

        block = TimeBlock(
            block_id="test-block-dup",
            source=TemporalSource.GOOGLE_CALENDAR,
            block_type=TimeBlockType.CALENDAR_EVENT,
            start=now,
            end=now + timedelta(hours=1),
            title=title,
            location="Room A",
            metadata={
                "external_source": "google_calendar",
                "external_id": "gcal-event-dup",
                "calendar_id": "primary",
            },
        )
        block_key = "|".join(["google_calendar", block.title, block.start.isoformat(), block.end.isoformat()])
        engine._temporal_blocks_by_day = {day_key: [block_key]}
        engine._temporal_blocks = {block_key: block}

        cookie = self._login(client)
        resp = client.get(f"/api/web/timeline?date_str={day_key}", cookies={COOKIE_NAME: cookie})
        assert resp.status_code == 200
        cal_events = [e for e in resp.json()["events"] if e["source"] == "google_calendar"]

        assert len([e for e in cal_events if e["title"] == title]) == 1
        assert cal_events[0]["event_id"] == "gcal-event-dup"


# ══════════════════════════════════════════════════════════════════════════════
# Calendar proposal accept/execution tests
# ══════════════════════════════════════════════════════════════════════════════


class TestCalendarProposalExecution:
    """Tests for proposal accept with create/update/delete execution."""

    def _login(self, client: TestClient) -> str:
        resp = client.post("/api/web/auth/login", json={"pin": "1234"})
        return resp.cookies[COOKIE_NAME]

    def _setup_mock_executor_settings(self, app: FastAPI):
        app.state.settings.google_calendar_mock = True
        app.state.settings.google_calendar_write_enabled = True
        app.state.settings.google_calendar_write_requires_acceptance = True

    def test_accept_update_proposal_calls_executor_and_emits_updated(self, client: TestClient, app: FastAPI):
        """Accepting update proposal calls executor and emits CALENDAR_EVENT_UPDATED."""
        from src.core.proposal import Proposal, ProposalType, TargetSystem

        self._setup_mock_executor_settings(app)

        proposal = Proposal(
            proposal_type=ProposalType.UPDATE_CALENDAR_EVENT,
            target_system=TargetSystem.GOOGLE_CALENDAR,
            action_payload={
                "title": "更新测试",
                "event_id": "mock-event-upd-001",
                "calendar_id": "primary",
                "start": "2026-06-10T11:00:00+08:00",
                "end": "2026-06-10T12:00:00+08:00",
                "location": "教室",
                "description": "测试更新",
            },
            user_id="123",
        )

        app.state.pipeline.run.reset_mock()
        app.state.pipeline.run.return_value = []
        cookie = self._login(client)
        resp = client.post(
            "/api/web/proposals/decision",
            json={"decision": "accept", "proposal": proposal.to_dict()},
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["needs_followup"] is False

        events = [call.args[0] for call in app.state.pipeline.run.call_args_list]
        event_types = [e.event_type for e in events]
        assert EventType.EXECUTION_PROPOSAL_ACCEPTED in event_types
        assert EventType.CALENDAR_EVENT_UPDATED in event_types, (
            f"CALENDAR_EVENT_UPDATED not found in {event_types}"
        )
        assert EventType.EXECUTION_COMPLETED in event_types
        assert EventType.CONNECTOR_FETCH_REQUESTED in event_types
        for ev in events:
            if ev.event_type == EventType.CALENDAR_EVENT_UPDATED:
                assert ev.payload["event_id"] == "mock-event-upd-001"
                assert ev.payload["calendar_id"] == "primary"
            if ev.event_type == EventType.CONNECTOR_FETCH_REQUESTED:
                assert ev.payload["source"] == "google_calendar"
                assert ev.payload["reason"] == "web_calendar_write_refresh"

    def test_accept_delete_proposal_calls_executor_and_emits_deleted(self, client: TestClient, app: FastAPI):
        """Accepting delete proposal calls executor and emits CALENDAR_EVENT_DELETED."""
        from src.core.proposal import Proposal, ProposalType, TargetSystem

        self._setup_mock_executor_settings(app)

        proposal = Proposal(
            proposal_type=ProposalType.DELETE_CALENDAR_EVENT,
            target_system=TargetSystem.GOOGLE_CALENDAR,
            action_payload={
                "title": "删除测试",
                "event_id": "mock-event-del-001",
                "calendar_id": "primary",
            },
            user_id="123",
        )

        app.state.pipeline.run.reset_mock()
        app.state.pipeline.run.return_value = []
        cookie = self._login(client)
        resp = client.post(
            "/api/web/proposals/decision",
            json={"decision": "accept", "proposal": proposal.to_dict()},
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["needs_followup"] is False

        events = [call.args[0] for call in app.state.pipeline.run.call_args_list]
        event_types = [e.event_type for e in events]
        assert EventType.EXECUTION_PROPOSAL_ACCEPTED in event_types
        assert EventType.CALENDAR_EVENT_DELETED in event_types, (
            f"CALENDAR_EVENT_DELETED not found in {event_types}"
        )
        assert EventType.EXECUTION_COMPLETED in event_types
        assert EventType.CONNECTOR_FETCH_REQUESTED in event_types
        for ev in events:
            if ev.event_type == EventType.CALENDAR_EVENT_DELETED:
                assert ev.payload["event_id"] == "mock-event-del-001"

    def test_accept_update_write_disabled_returns_failure(self, client: TestClient, app: FastAPI):
        """Update with real writes disabled returns failure."""
        from src.core.proposal import Proposal, ProposalType, TargetSystem

        app.state.settings.google_calendar_mock = False
        app.state.settings.google_calendar_write_enabled = False
        app.state.settings.google_calendar_write_requires_acceptance = True

        proposal = Proposal(
            proposal_type=ProposalType.UPDATE_CALENDAR_EVENT,
            target_system=TargetSystem.GOOGLE_CALENDAR,
            action_payload={
                "title": "更新测试",
                "event_id": "mock-event-upd-002",
                "calendar_id": "primary",
            },
            user_id="123",
        )

        app.state.pipeline.run.reset_mock()
        app.state.pipeline.run.return_value = []
        cookie = self._login(client)
        resp = client.post(
            "/api/web/proposals/decision",
            json={"decision": "accept", "proposal": proposal.to_dict()},
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert data["needs_followup"] is True
        assert "calendar_write_disabled" in data.get("error", "")

        events = [call.args[0] for call in app.state.pipeline.run.call_args_list]
        event_types = [e.event_type for e in events]
        assert EventType.EXECUTION_PROPOSAL_ACCEPTED in event_types
        assert EventType.EXECUTION_FAILED in event_types
        assert EventType.CALENDAR_EVENT_UPDATED not in event_types

    def test_accept_delete_write_disabled_returns_failure(self, client: TestClient, app: FastAPI):
        """Delete with real writes disabled returns failure."""
        from src.core.proposal import Proposal, ProposalType, TargetSystem

        app.state.settings.google_calendar_mock = False
        app.state.settings.google_calendar_write_enabled = False
        app.state.settings.google_calendar_write_requires_acceptance = True

        proposal = Proposal(
            proposal_type=ProposalType.DELETE_CALENDAR_EVENT,
            target_system=TargetSystem.GOOGLE_CALENDAR,
            action_payload={
                "title": "删除测试",
                "event_id": "mock-event-del-002",
                "calendar_id": "primary",
            },
            user_id="123",
        )

        app.state.pipeline.run.reset_mock()
        app.state.pipeline.run.return_value = []
        cookie = self._login(client)
        resp = client.post(
            "/api/web/proposals/decision",
            json={"decision": "accept", "proposal": proposal.to_dict()},
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert data["needs_followup"] is True
        assert "calendar_write_disabled" in data.get("error", "")

        events = [call.args[0] for call in app.state.pipeline.run.call_args_list]
        event_types = [e.event_type for e in events]
        assert EventType.CALENDAR_EVENT_DELETED not in event_types


# ══════════════════════════════════════════════════════════════════════════════
# Finance action endpoint tests
# ══════════════════════════════════════════════════════════════════════════════


class TestFinanceAction:
    def _login(self, client: TestClient) -> str:
        resp = client.post("/api/web/auth/login", json={"pin": "1234"})
        return resp.cookies[COOKIE_NAME]

    def test_requires_auth(self, client: TestClient):
        resp = client.post("/api/web/finance/action", json={"action": "expense"})
        assert resp.status_code == 401

    def test_expense_publishes_transaction_recorded(self, client: TestClient, app: FastAPI):
        from src.core.events import Event, EventType, AggregateType
        app.state.pipeline.run.reset_mock()
        app.state.pipeline.run.return_value = [
            Event(EventType.FINANCE_TRANSACTION_RECORDED, "123", AggregateType.FINANCE, payload={"amount": 18, "category": "emotional"}),
        ]
        cookie = self._login(client)
        resp = client.post("/api/web/finance/action", json={
            "action": "expense", "amount": 18, "category": "emotional", "description": "奶茶",
        }, cookies={COOKIE_NAME: cookie})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["action"] == "expense"
        app.state.pipeline.run.assert_called_once()
        event = app.state.pipeline.run.call_args.args[0]
        assert event.event_type == EventType.FINANCE_TRANSACTION_RECORDED
        assert event.payload["amount"] == 18

    def test_expense_returns_dashboard(self, client: TestClient, app: FastAPI):
        from src.core.events import Event, EventType, AggregateType
        app.state.pipeline.run.reset_mock()
        app.state.pipeline.run.return_value = [
            Event(EventType.FINANCE_TRANSACTION_RECORDED, "123", AggregateType.FINANCE, payload={"amount": 50, "category": "food"}),
        ]
        cookie = self._login(client)
        resp = client.post("/api/web/finance/action", json={
            "action": "expense", "amount": 50,
        }, cookies={COOKIE_NAME: cookie})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "dashboard" in data

    def test_income_publishes_income_recorded(self, client: TestClient, app: FastAPI):
        from src.core.events import Event, EventType, AggregateType
        app.state.pipeline.run.reset_mock()
        app.state.pipeline.run.return_value = [
            Event(EventType.FINANCE_INCOME_RECORDED, "123", AggregateType.FINANCE, payload={"amount": 1000, "source": "生活费"}),
        ]
        cookie = self._login(client)
        resp = client.post("/api/web/finance/action", json={
            "action": "income", "amount": 1000, "source": "生活费", "description": "生活费到账",
        }, cookies={COOKIE_NAME: cookie})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["action"] == "income"
        app.state.pipeline.run.assert_called_once()
        event = app.state.pipeline.run.call_args.args[0]
        assert event.event_type == EventType.FINANCE_INCOME_RECORDED

    def test_income_returns_action_id(self, client: TestClient, app: FastAPI):
        from src.core.events import Event, EventType, AggregateType
        app.state.pipeline.run.reset_mock()
        app.state.pipeline.run.return_value = [
            Event(EventType.FINANCE_INCOME_RECORDED, "123", AggregateType.FINANCE, payload={"amount": 500, "source": "兼职"}),
        ]
        cookie = self._login(client)
        resp = client.post("/api/web/finance/action", json={
            "action": "income", "amount": 500,
        }, cookies={COOKIE_NAME: cookie})
        assert resp.status_code == 200
        data = resp.json()
        assert data["action_id"] is not None

    def test_expense_tracks_action_id_when_pipeline_returns_no_children(self, client: TestClient, app: FastAPI):
        """Structured finance root events are undoable even when pipeline returns no child events."""
        app.state.pipeline.run.reset_mock()
        app.state.pipeline.run.return_value = []
        cookie = self._login(client)
        resp = client.post("/api/web/finance/action", json={
            "action": "expense", "amount": 20, "category": "necessary", "description": "午饭",
        }, cookies={COOKIE_NAME: cookie})
        assert resp.status_code == 200
        data = resp.json()
        assert data["can_undo"] is True
        assert data["action_id"] is not None
        assert data["events"] == 1

    def test_parent_received_publishes_three_events(self, client: TestClient, app: FastAPI):
        from src.core.events import Event, EventType, AggregateType
        app.state.pipeline.run.reset_mock()
        app.state.pipeline.run.return_value = []
        cookie = self._login(client)
        resp = client.post("/api/web/finance/action", json={
            "action": "parent_received", "amount": 150, "person": "爸爸", "description": "买画材",
        }, cookies={COOKIE_NAME: cookie})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        # Each event run produces its own pipeline.run call
        calls = [call.args[0] for call in app.state.pipeline.run.call_args_list]
        event_types = [e.event_type for e in calls]
        assert EventType.PARENT_FUND_REQUEST_RECORDED in event_types
        assert EventType.PARENT_FUND_RECEIVED in event_types
        assert EventType.FINANCE_INCOME_RECORDED in event_types

    def test_parent_plan_publishes_planned(self, client: TestClient, app: FastAPI):
        from src.core.events import Event, EventType, AggregateType
        app.state.pipeline.run.reset_mock()
        app.state.pipeline.run.return_value = [
            Event(EventType.PARENT_FUND_REQUEST_PLANNED, "123", AggregateType.FINANCE,
                  payload={"amount": 100, "description": "话费", "requested_date": "2026-06-10"}),
        ]
        cookie = self._login(client)
        resp = client.post("/api/web/finance/action", json={
            "action": "parent_plan", "amount": 100, "person": "妈妈", "description": "话费",
            "requested_date": "2026-06-10",
        }, cookies={COOKIE_NAME: cookie})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["action"] == "parent_plan"
        app.state.pipeline.run.assert_called_once()
        event = app.state.pipeline.run.call_args.args[0]
        assert event.event_type == EventType.PARENT_FUND_REQUEST_PLANNED
        assert event.payload["requested_date"] == "2026-06-10"

    def test_partner_debt_publishes_debt_created(self, client: TestClient, app: FastAPI):
        from src.core.events import Event, EventType, AggregateType
        app.state.pipeline.run.reset_mock()
        app.state.pipeline.run.return_value = [
            Event(EventType.PARTNER_DEBT_CREATED, "123", AggregateType.FINANCE,
                  payload={"amount": 500, "counterparty": "对象", "date": "2026-05-03"}),
        ]
        cookie = self._login(client)
        resp = client.post("/api/web/finance/action", json={
            "action": "partner_debt", "amount": 500, "counterparty": "对象",
            "description": "借给对象", "date": "2026-05-03",
        }, cookies={COOKIE_NAME: cookie})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        app.state.pipeline.run.assert_called_once()
        event = app.state.pipeline.run.call_args.args[0]
        assert event.event_type == EventType.PARTNER_DEBT_CREATED
        assert event.payload["counterparty"] == "对象"
        assert event.payload["amount"] == 500

    def test_invalid_amount_returns_400(self, client: TestClient):
        cookie = self._login(client)
        resp = client.post("/api/web/finance/action", json={
            "action": "expense", "amount": 0,
        }, cookies={COOKIE_NAME: cookie})
        assert resp.status_code == 400

    def test_unknown_action_returns_400(self, client: TestClient):
        cookie = self._login(client)
        resp = client.post("/api/web/finance/action", json={
            "action": "invalid_action",
        }, cookies={COOKIE_NAME: cookie})
        assert resp.status_code == 400

    def test_parent_plan_invalid_date_returns_400(self, client: TestClient):
        cookie = self._login(client)
        resp = client.post("/api/web/finance/action", json={
            "action": "parent_plan",
            "amount": 100,
            "description": "话费",
            "requested_date": "bad-date",
        }, cookies={COOKIE_NAME: cookie})
        assert resp.status_code == 400

    def test_parent_plan_missing_date_returns_400(self, client: TestClient):
        cookie = self._login(client)
        resp = client.post("/api/web/finance/action", json={
            "action": "parent_plan",
            "amount": 100,
            "description": "话费",
        }, cookies={COOKIE_NAME: cookie})
        assert resp.status_code == 400

    def test_partner_debt_invalid_date_returns_400(self, client: TestClient):
        cookie = self._login(client)
        resp = client.post("/api/web/finance/action", json={
            "action": "partner_debt",
            "amount": 100,
            "description": "借款",
            "date": "bad-date",
        }, cookies={COOKIE_NAME: cookie})
        assert resp.status_code == 400

    def test_dashboard_includes_parent_funds(self, client: TestClient, app: FastAPI):
        """Dashboard includes parent_funds when state has them."""
        app.state.state_engine._state = {
            "parent_funds": {
                "current": {
                    "planned_requests": [{"amount": 100, "description": "话费", "timestamp": "2026-06-01T00:00:00+00:00"}],
                    "request_log": [{"amount": 50, "description": "买零食", "timestamp": "2026-06-02T00:00:00+00:00"}],
                    "received_log": [{"amount": 1000, "description": "生活费", "timestamp": "2026-06-01T00:00:00+00:00"}],
                }
            },
            "partner_debts": {
                "current": {
                    "debts": [{"amount": 200, "counterparty": "对象", "description": "借给对象", "timestamp": "2026-06-01T00:00:00+00:00"}],
                    "total_outstanding": 200,
                }
            },
        }
        cookie = self._login(client)
        resp = client.get("/api/web/dashboard", cookies={COOKIE_NAME: cookie})
        assert resp.status_code == 200
        data = resp.json()
        assert "parent_funds" in data
        assert len(data["parent_funds"]["planned_requests"]) == 1
        assert len(data["parent_funds"]["request_log"]) == 1
        assert len(data["parent_funds"]["received_log"]) == 1
        assert "partner_debts" in data
        assert data["partner_debts"]["total_outstanding"] == 200
        assert len(data["partner_debts"]["debts"]) == 1

    def test_dashboard_includes_finance_ledger_rows_with_undo_flags(self, client: TestClient, app: FastAPI):
        """Finance dashboard exposes concrete ledger rows for Web editing."""
        app.state.state_engine._state = {
            "finance": {
                "monthly": {
                    "month": "2026-06",
                    "outflow": 70,
                    "inflow": 100,
                    "outing_spent": 20,
                    "by_category": {"outing": 20, "necessary": 50},
                    "transactions": [
                        {"event_id": "tx-1", "amount": 20, "category": "outing", "description": "电影", "timestamp": "2026-06-01T12:00:00+00:00"},
                        {"event_id": "tx-2", "amount": 50, "category": "necessary", "description": "午饭", "timestamp": "2026-06-02T12:00:00+00:00"},
                    ],
                    "income_log": [
                        {"event_id": "inc-1", "amount": 100, "source": "生活费", "description": "生活费到账", "timestamp": "2026-06-01T08:00:00+00:00"},
                    ],
                }
            },
            "undo": {
                "123": {
                    "reverted_actions": [
                        {"action_id": "tx-1", "action_type": "finance_transaction", "reverted_at": "2026-06-03T00:00:00+00:00"}
                    ]
                }
            },
        }
        cookie = self._login(client)
        resp = client.get("/api/web/dashboard", cookies={COOKIE_NAME: cookie})
        assert resp.status_code == 200
        finance = resp.json()["finance"]
        tx1 = next(t for t in finance["transactions"] if t["action_id"] == "tx-1")
        tx2 = next(t for t in finance["transactions"] if t["action_id"] == "tx-2")
        inc = finance["income_log"][0]
        assert tx1["reverted"] is True
        assert tx1["can_undo"] is False
        assert tx2["can_undo"] is True
        assert inc["can_undo"] is True

    def test_finance_revert_endpoint_publishes_canonical_undo_events(self, client: TestClient, app: FastAPI):
        from src.core.events import EventType

        app.state.pipeline.run.reset_mock()
        app.state.pipeline.run.return_value = []
        app.state.state_engine._state = {
            "finance": {"monthly": {"month": "2026-06", "outflow": 20, "transactions": []}},
            "undo": {"123": {"reverted_actions": []}},
        }
        cookie = self._login(client)
        resp = client.post("/api/web/finance/revert", json={
            "action_type": "finance_transaction",
            "action_id": "tx-1",
            "amount": 20,
            "category": "outing",
        }, cookies={COOKIE_NAME: cookie})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        calls = [call.args[0] for call in app.state.pipeline.run.call_args_list]
        assert [event.event_type for event in calls] == [
            EventType.USER_UNDO_REQUESTED,
            EventType.USER_ACTION_REVERTED,
        ]
        assert calls[1].payload["action_id"] == "tx-1"
        assert calls[1].payload["action_type"] == "finance_transaction"
        assert calls[1].payload["amount"] == 20
        assert calls[1].payload["category"] == "outing"

    def test_finance_revert_endpoint_rejects_already_reverted_row(self, client: TestClient, app: FastAPI):
        app.state.pipeline.run.reset_mock()
        app.state.state_engine._state = {
            "undo": {
                "123": {
                    "reverted_actions": [
                        {"action_id": "tx-1", "action_type": "finance_transaction", "reverted_at": "2026-06-03T00:00:00+00:00"}
                    ]
                }
            }
        }
        cookie = self._login(client)
        resp = client.post("/api/web/finance/revert", json={
            "action_type": "finance_transaction",
            "action_id": "tx-1",
            "amount": 20,
            "category": "outing",
        }, cookies={COOKIE_NAME: cookie})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        app.state.pipeline.run.assert_not_called()

    def test_expense_can_undo_true(self, client: TestClient, app: FastAPI):
        from src.core.events import Event, EventType, AggregateType
        app.state.pipeline.run.reset_mock()
        app.state.pipeline.run.return_value = [
            Event(EventType.FINANCE_TRANSACTION_RECORDED, "123", AggregateType.FINANCE, payload={"amount": 50, "category": "food"}),
        ]
        cookie = self._login(client)
        resp = client.post("/api/web/finance/action", json={
            "action": "expense", "amount": 50,
        }, cookies={COOKIE_NAME: cookie})
        assert resp.status_code == 200
        assert resp.json()["can_undo"] is True

    def test_parent_received_can_undo_false(self, client: TestClient, app: FastAPI):
        app.state.pipeline.run.reset_mock()
        app.state.pipeline.run.return_value = []
        cookie = self._login(client)
        resp = client.post("/api/web/finance/action", json={
            "action": "parent_received", "amount": 150, "person": "爸爸",
        }, cookies={COOKIE_NAME: cookie})
        assert resp.status_code == 200
        assert resp.json()["can_undo"] is False


# ══════════════════════════════════════════════════════════════════════════════
# Tasks action endpoint tests
# ══════════════════════════════════════════════════════════════════════════════


class TestTasksAction:
    """Tests for POST /api/web/tasks/action."""

    def _login(self, client: TestClient) -> str:
        resp = client.post("/api/web/auth/login", json={"pin": "1234"})
        return resp.cookies[COOKIE_NAME]

    def test_requires_auth(self, client: TestClient):
        """No session cookie → 401."""
        resp = client.post("/api/web/tasks/action", json={"action": "complete", "items": []})
        assert resp.status_code == 401

    def test_batch_complete_publishes_events_for_each_item(self, client: TestClient, app: FastAPI):
        """Batch complete publishes PLANNING_TASK_COMPLETED + MEMORY_ENTRY_CREATED per item."""
        app.state.pipeline.run.reset_mock()
        app.state.pipeline.run.return_value = []
        cookie = self._login(client)
        items = [
            {"id": "hw-1", "title": "数学作业", "course": "数学"},
            {"id": "hw-2", "title": "英语作业", "course": "英语"},
        ]
        resp = client.post(
            "/api/web/tasks/action",
            json={"action": "complete", "items": items},
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["action"] == "complete"
        assert data["item_count"] == 2
        # 2 items × 2 events each = 4 pipeline calls
        assert app.state.pipeline.run.call_count == 4
        events = [call.args[0] for call in app.state.pipeline.run.call_args_list]
        assert events[0].event_type == EventType.PLANNING_TASK_COMPLETED
        assert events[0].payload["task_id"] == "hw-1"
        assert events[1].event_type == EventType.MEMORY_ENTRY_CREATED
        assert events[1].payload["content"] == "完成：数学作业"
        assert events[2].event_type == EventType.PLANNING_TASK_COMPLETED
        assert events[2].payload["task_id"] == "hw-2"
        assert events[3].event_type == EventType.MEMORY_ENTRY_CREATED
        assert events[3].payload["content"] == "完成：英语作业"

    def test_batch_skip_publishes_skipped_for_each_item(self, client: TestClient, app: FastAPI):
        """Batch skip publishes PLANNING_RECOMMENDATION_SKIPPED per item."""
        app.state.pipeline.run.reset_mock()
        app.state.pipeline.run.return_value = []
        cookie = self._login(client)
        items = [
            {"id": "hw-a", "title": "物理作业"},
            {"id": "hw-b", "title": "化学作业"},
        ]
        resp = client.post(
            "/api/web/tasks/action",
            json={"action": "skip", "items": items},
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["item_count"] == 2
        # 2 items × 1 event each = 2 pipeline calls
        assert app.state.pipeline.run.call_count == 2
        events = [call.args[0] for call in app.state.pipeline.run.call_args_list]
        assert events[0].event_type == EventType.PLANNING_RECOMMENDATION_SKIPPED
        assert events[0].payload["task_id"] == "hw-a"
        assert events[1].event_type == EventType.PLANNING_RECOMMENDATION_SKIPPED
        assert events[1].payload["task_id"] == "hw-b"

    def test_batch_delay_publishes_delayed_with_30_minutes(self, client: TestClient, app: FastAPI):
        """Batch delay publishes PLANNING_RECOMMENDATION_DELAYED with delay_minutes=30 per item."""
        app.state.pipeline.run.reset_mock()
        app.state.pipeline.run.return_value = []
        cookie = self._login(client)
        items = [
            {"id": "hw-x", "title": "数据结构作业"},
            {"id": "hw-y", "title": "算法作业"},
        ]
        resp = client.post(
            "/api/web/tasks/action",
            json={"action": "delay_30", "items": items},
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["item_count"] == 2
        assert app.state.pipeline.run.call_count == 2
        events = [call.args[0] for call in app.state.pipeline.run.call_args_list]
        for event in events:
            assert event.event_type == EventType.PLANNING_RECOMMENDATION_DELAYED
            assert event.payload["delay_minutes"] == 30
            assert event.payload["delayed_until"]
        assert events[0].payload["task_id"] == "hw-x"
        assert events[1].payload["task_id"] == "hw-y"

    def test_calendar_proposal_returns_proposal_no_direct_write(self, client: TestClient, app: FastAPI):
        """calendar_proposal returns proposal JSON and does NOT execute calendar write."""
        app.state.pipeline.run.reset_mock()
        app.state.pipeline.run.return_value = []
        cookie = self._login(client)
        items = [{"id": "hw-cal", "title": "高数作业", "course": "高等数学"}]
        resp = client.post(
            "/api/web/tasks/action",
            json={
                "action": "calendar_proposal",
                "items": items,
                "date": "2026-06-15",
                "start_time": "14:00",
                "end_time": "15:30",
                "location": "图书馆",
                "note": "复习积分部分",
            },
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["action"] == "calendar_proposal"
        assert data["item_count"] == 1
        assert "proposal" in data
        assert data["proposal"]["proposal_type"] == "create_calendar_block"
        assert data["proposal"]["target_system"] == "google_calendar"
        # Title should include course prefix
        payload = data["proposal"]["action_payload"]
        assert "高等数学" in payload.get("title", "")
        assert payload.get("location") == "图书馆"
        # Pipeline called once with EXECUTION_PROPOSAL_CREATED
        app.state.pipeline.run.assert_called_once()
        event = app.state.pipeline.run.call_args.args[0]
        assert event.event_type == EventType.EXECUTION_PROPOSAL_CREATED

    def test_calendar_proposal_invalid_date_returns_400(self, client: TestClient):
        """Invalid date returns 400 JSON with friendly message."""
        cookie = self._login(client)
        items = [{"id": "hw-cal", "title": "高数作业"}]
        resp = client.post(
            "/api/web/tasks/action",
            json={
                "action": "calendar_proposal",
                "items": items,
                "date": "not-a-date",
                "start_time": "14:00",
            },
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 400
        data = resp.json()
        assert data["ok"] is False
        assert "无效的日期" in data["message"]

    def test_calendar_proposal_missing_date_returns_400(self, client: TestClient):
        """Missing date returns 400."""
        cookie = self._login(client)
        items = [{"id": "hw-cal", "title": "高数作业"}]
        resp = client.post(
            "/api/web/tasks/action",
            json={
                "action": "calendar_proposal",
                "items": items,
                "start_time": "14:00",
            },
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 400
        data = resp.json()
        assert data["ok"] is False

    def test_calendar_proposal_end_before_start_returns_400(self, client: TestClient):
        """end_time before start_time returns 400."""
        cookie = self._login(client)
        items = [{"id": "hw-cal", "title": "高数作业"}]
        resp = client.post(
            "/api/web/tasks/action",
            json={
                "action": "calendar_proposal",
                "items": items,
                "date": "2026-06-15",
                "start_time": "15:00",
                "end_time": "14:00",
            },
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 400
        data = resp.json()
        assert data["ok"] is False
        assert "end_time" in data["message"]

    def test_calendar_proposal_missing_start_time_returns_400(self, client: TestClient):
        """Missing start_time returns 400."""
        cookie = self._login(client)
        items = [{"id": "hw-cal", "title": "高数作业"}]
        resp = client.post(
            "/api/web/tasks/action",
            json={
                "action": "calendar_proposal",
                "items": items,
                "date": "2026-06-15",
            },
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 400
        data = resp.json()
        assert data["ok"] is False

    def test_calendar_proposal_invalid_time_returns_400(self, client: TestClient):
        """Invalid HH:MM time returns 400 instead of a server error."""
        cookie = self._login(client)
        items = [{"id": "hw-cal", "title": "高数作业"}]
        resp = client.post(
            "/api/web/tasks/action",
            json={
                "action": "calendar_proposal",
                "items": items,
                "date": "2026-06-15",
                "start_time": "14:60",
            },
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 400
        data = resp.json()
        assert data["ok"] is False
        assert "时间格式" in data["message"]

    def test_invalid_action_returns_400(self, client: TestClient):
        """Invalid action string returns 400."""
        cookie = self._login(client)
        resp = client.post(
            "/api/web/tasks/action",
            json={"action": "invalid_action", "items": []},
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 400

    def test_missing_items_returns_400(self, client: TestClient):
        """Empty or missing items returns 400 for batch actions."""
        cookie = self._login(client)
        resp = client.post(
            "/api/web/tasks/action",
            json={"action": "complete"},
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 400

    def test_batch_returns_dashboard(self, client: TestClient, app: FastAPI):
        """Batch actions include dashboard in response."""
        app.state.pipeline.run.reset_mock()
        app.state.pipeline.run.return_value = []
        cookie = self._login(client)
        items = [{"id": "hw-dash", "title": "测试作业"}]
        resp = client.post(
            "/api/web/tasks/action",
            json={"action": "complete", "items": items},
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "dashboard" in data

    def test_calendar_proposal_returns_dashboard(self, client: TestClient, app: FastAPI):
        """calendar_proposal includes dashboard in response."""
        app.state.pipeline.run.reset_mock()
        app.state.pipeline.run.return_value = []
        cookie = self._login(client)
        items = [{"id": "hw-cal", "title": "测试安排"}]
        resp = client.post(
            "/api/web/tasks/action",
            json={
                "action": "calendar_proposal",
                "items": items,
                "date": "2026-06-20",
                "start_time": "09:00",
            },
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "dashboard" in data

    def test_single_item_complete_works(self, client: TestClient, app: FastAPI):
        """Single item complete works identically to batch."""
        app.state.pipeline.run.reset_mock()
        app.state.pipeline.run.return_value = []
        cookie = self._login(client)
        items = [{"id": "hw-single", "title": "单一作业"}]
        resp = client.post(
            "/api/web/tasks/action",
            json={"action": "complete", "items": items},
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["item_count"] == 1
        # 1 item × 2 events = 2 pipeline calls
        assert app.state.pipeline.run.call_count == 2
        events = [call.args[0] for call in app.state.pipeline.run.call_args_list]
        assert events[0].event_type == EventType.PLANNING_TASK_COMPLETED
        assert events[0].payload["task_id"] == "hw-single"
        assert events[1].event_type == EventType.MEMORY_ENTRY_CREATED

    def test_missing_pipeline_returns_503(self, client: TestClient, app: FastAPI):
        """When pipeline is None, returns 503."""
        app.state.pipeline = None
        cookie = self._login(client)
        resp = client.post(
            "/api/web/tasks/action",
            json={"action": "complete", "items": [{"id": "hw", "title": "作业"}]},
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 503


# ══════════════════════════════════════════════════════════════════════════════
# Today Action endpoint tests
# ══════════════════════════════════════════════════════════════════════════════


class TestTodayAction:
    """Tests for POST /api/web/today/action."""

    def _login(self, client: TestClient) -> str:
        resp = client.post("/api/web/auth/login", json={"pin": "1234"})
        return resp.cookies[COOKIE_NAME]

    def test_requires_auth(self, client: TestClient):
        """No session cookie → 401."""
        resp = client.post("/api/web/today/action", json={"action": "hydration", "amount_ml": 250})
        assert resp.status_code == 401

    def test_invalid_action_returns_400(self, client: TestClient):
        """Unknown action returns 400."""
        cookie = self._login(client)
        resp = client.post("/api/web/today/action", json={"action": "invalid"}, cookies={COOKIE_NAME: cookie})
        assert resp.status_code == 400
        data = resp.json()
        assert data["ok"] is False

    def test_art_progress_publishes_art_progress_recorded(self, client: TestClient, app: FastAPI):
        """art_progress publishes ART_PROGRESS_RECORDED."""
        app.state.pipeline.run.reset_mock()
        app.state.pipeline.run.return_value = []
        cookie = self._login(client)
        resp = client.post(
            "/api/web/today/action",
            json={"action": "art_progress", "minutes": 45, "type": "创作", "note": "画了幅水彩"},
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["action"] == "art_progress"
        app.state.pipeline.run.assert_called_once()
        event = app.state.pipeline.run.call_args.args[0]
        assert event.event_type == EventType.ART_PROGRESS_RECORDED
        assert event.payload["completed_minutes"] == 45
        assert event.payload["type"] == "创作"
        assert event.payload["note"] == "画了幅水彩"

    def test_art_progress_missing_minutes_returns_400(self, client: TestClient):
        """art_progress without minutes returns 400."""
        cookie = self._login(client)
        resp = client.post("/api/web/today/action", json={"action": "art_progress"}, cookies={COOKIE_NAME: cookie})
        assert resp.status_code == 400

    def test_art_progress_zero_minutes_returns_400(self, client: TestClient):
        """art_progress with minutes=0 returns 400."""
        cookie = self._login(client)
        resp = client.post(
            "/api/web/today/action",
            json={"action": "art_progress", "minutes": 0},
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 400

    def test_hydration_publishes_hydration_logged(self, client: TestClient, app: FastAPI):
        """hydration publishes HYDRATION_LOGGED with amount_ml."""
        app.state.pipeline.run.reset_mock()
        app.state.pipeline.run.return_value = []
        cookie = self._login(client)
        resp = client.post(
            "/api/web/today/action",
            json={"action": "hydration", "amount_ml": 350},
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["action"] == "hydration"
        app.state.pipeline.run.assert_called_once()
        event = app.state.pipeline.run.call_args.args[0]
        assert event.event_type == EventType.HYDRATION_LOGGED
        assert event.payload["amount_ml"] == 350

    def test_hydration_missing_amount_returns_400(self, client: TestClient):
        """hydration without amount_ml returns 400."""
        cookie = self._login(client)
        resp = client.post(
            "/api/web/today/action",
            json={"action": "hydration"},
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 400

    def test_hydration_zero_amount_returns_400(self, client: TestClient):
        """hydration with amount_ml=0 returns 400."""
        cookie = self._login(client)
        resp = client.post(
            "/api/web/today/action",
            json={"action": "hydration", "amount_ml": 0},
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 400

    def test_completion_publishes_planning_task_completed_and_memory(self, client: TestClient, app: FastAPI):
        """completion publishes PLANNING_TASK_COMPLETED + MEMORY_ENTRY_CREATED."""
        app.state.pipeline.run.reset_mock()
        app.state.pipeline.run.return_value = []
        cookie = self._login(client)
        resp = client.post(
            "/api/web/today/action",
            json={"action": "completion", "text": "完成了数学作业", "note": "花了2小时"},
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["action"] == "completion"
        assert app.state.pipeline.run.call_count == 2
        events = [call.args[0] for call in app.state.pipeline.run.call_args_list]
        assert events[0].event_type == EventType.PLANNING_TASK_COMPLETED
        assert events[0].payload["task_id"] == "完成了数学作业"
        assert events[0].payload["note"] == "花了2小时"
        assert events[1].event_type == EventType.MEMORY_ENTRY_CREATED

    def test_completion_missing_text_returns_400(self, client: TestClient):
        """completion without text returns 400."""
        cookie = self._login(client)
        resp = client.post(
            "/api/web/today/action",
            json={"action": "completion"},
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 400

    def test_context_publishes_subjective_context_added(self, client: TestClient, app: FastAPI):
        """context publishes SUBJECTIVE_CONTEXT_ADDED with expires_at."""
        app.state.pipeline.run.reset_mock()
        app.state.pipeline.run.return_value = []
        cookie = self._login(client)
        resp = client.post(
            "/api/web/today/action",
            json={"action": "context", "text": "今天状态好", "kind": "context"},
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        app.state.pipeline.run.assert_called_once()
        event = app.state.pipeline.run.call_args.args[0]
        assert event.event_type == EventType.SUBJECTIVE_CONTEXT_ADDED
        assert event.payload["kind"] == "context"
        assert event.payload["text"] == "今天状态好"
        assert event.payload["expires_at"] is not None

    def test_context_defaults_kind_to_context(self, client: TestClient, app: FastAPI):
        """context without kind defaults to 'context'."""
        app.state.pipeline.run.reset_mock()
        app.state.pipeline.run.return_value = []
        cookie = self._login(client)
        resp = client.post(
            "/api/web/today/action",
            json={"action": "context", "text": "有点累"},
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        event = app.state.pipeline.run.call_args.args[0]
        assert event.event_type == EventType.SUBJECTIVE_CONTEXT_ADDED
        assert event.payload["kind"] == "context"

    def test_context_missing_text_returns_400(self, client: TestClient):
        """context without text returns 400."""
        cookie = self._login(client)
        resp = client.post(
            "/api/web/today/action",
            json={"action": "context"},
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 400

    def test_school_leave_today_publishes_subjective_context(self, client: TestClient, app: FastAPI):
        """school_leave_today publishes SUBJECTIVE_CONTEXT_ADDED with kind school_leave."""
        app.state.pipeline.run.reset_mock()
        app.state.pipeline.run.return_value = []
        cookie = self._login(client)
        resp = client.post(
            "/api/web/today/action",
            json={"action": "school_leave_today", "text": "请假一天", "date": "2026-06-05"},
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["action"] == "school_leave_today"
        app.state.pipeline.run.assert_called_once()
        event = app.state.pipeline.run.call_args.args[0]
        assert event.event_type == EventType.SUBJECTIVE_CONTEXT_ADDED
        assert event.payload["kind"] == "school_leave"
        assert event.payload["date"] == "2026-06-05"
        assert event.payload["expires_at"] is not None

    def test_school_leave_today_defaults_date(self, client: TestClient, app: FastAPI):
        """school_leave_today without date uses today."""
        app.state.pipeline.run.reset_mock()
        app.state.pipeline.run.return_value = []
        cookie = self._login(client)
        resp = client.post(
            "/api/web/today/action",
            json={"action": "school_leave_today"},
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        event = app.state.pipeline.run.call_args.args[0]
        assert event.event_type == EventType.SUBJECTIVE_CONTEXT_ADDED
        assert event.payload["kind"] == "school_leave"

    def test_school_leave_today_invalid_date_returns_400(self, client: TestClient):
        """school_leave_today rejects invalid dates before publishing."""
        cookie = self._login(client)
        resp = client.post(
            "/api/web/today/action",
            json={"action": "school_leave_today", "date": "bad-date"},
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 400
        data = resp.json()
        assert data["ok"] is False
        assert "无效的日期" in data["message"]

    def test_sync_refresh_publishes_system_scheduled_trigger(self, client: TestClient, app: FastAPI):
        """sync_refresh publishes scheduled trigger events (same as existing sync_refresh)."""
        app.state.pipeline.run.reset_mock()
        app.state.pipeline.run.return_value = []
        cookie = self._login(client)
        resp = client.post(
            "/api/web/today/action",
            json={"action": "sync_refresh"},
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["action"] == "sync_refresh"
        assert app.state.pipeline.run.call_count == 4
        events = [call.args[0] for call in app.state.pipeline.run.call_args_list]
        assert all(event.event_type == EventType.SYSTEM_SCHEDULED_TRIGGER for event in events)
        assert {event.payload["action"] for event in events} == {
            "check_homework", "schedule_daily_sync", "calendar_sync", "momo_vocab_sync",
        }


class TestReviewAction:
    def _login(self, client: TestClient) -> str:
        resp = client.post("/api/web/auth/login", json={"pin": "1234"})
        return resp.cookies[COOKIE_NAME]

    def test_review_requires_auth(self, client: TestClient):
        resp = client.post("/api/web/review/action", json={"mood_score": 6})
        assert resp.status_code == 401

    def test_review_requires_at_least_one_field(self, client: TestClient):
        cookie = self._login(client)
        resp = client.post(
            "/api/web/review/action",
            json={},
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 400
        assert "至少填写" in resp.json()["message"]

    def test_review_rejects_invalid_score(self, client: TestClient):
        cookie = self._login(client)
        resp = client.post(
            "/api/web/review/action",
            json={"mood_score": 11},
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 400
        assert "mood_score" in resp.json()["message"]

    def test_review_publishes_mood_context_and_memory(self, client: TestClient, app: FastAPI):
        app.state.pipeline.run.reset_mock()
        app.state.pipeline.run.return_value = []
        cookie = self._login(client)

        resp = client.post(
            "/api/web/review/action",
            json={
                "mood_score": 6,
                "energy_score": 4,
                "pressure_score": 7,
                "body_state": "困",
                "completed": "画画 60min",
                "deviation": "临时安排挤占时间",
                "tomorrow": "先画画",
                "note": "晚上效率低",
            },
            cookies={COOKIE_NAME: cookie},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["message"] == "已记录今日复盘"
        assert app.state.pipeline.run.call_count == 3
        events = [call.args[0] for call in app.state.pipeline.run.call_args_list]
        assert [event.event_type for event in events] == [
            EventType.MOOD_RECORDED,
            EventType.SUBJECTIVE_CONTEXT_ADDED,
            EventType.MEMORY_ENTRY_CREATED,
        ]
        assert events[0].payload["score"] == 6
        assert events[1].payload["kind"] == "daily_review"
        assert events[1].payload["fields"]["pressure_score"] == 7
        assert "临时安排挤占时间" in events[1].payload["text"]
        assert "daily_review" in events[2].payload["tags"]
        assert "daily_log" in events[2].payload["tags"]
        assert "completion" not in events[2].payload["tags"]
        assert "画画 60min" in events[2].payload["content"]

    def test_review_without_mood_publishes_context_and_memory(self, client: TestClient, app: FastAPI):
        app.state.pipeline.run.reset_mock()
        app.state.pipeline.run.return_value = []
        cookie = self._login(client)

        resp = client.post(
            "/api/web/review/action",
            json={"completed": "整理了任务", "deviation": "没有画画"},
            cookies={COOKIE_NAME: cookie},
        )

        assert resp.status_code == 200
        events = [call.args[0] for call in app.state.pipeline.run.call_args_list]
        assert [event.event_type for event in events] == [
            EventType.SUBJECTIVE_CONTEXT_ADDED,
            EventType.MEMORY_ENTRY_CREATED,
        ]


class TestSystemAction:
    def _login(self, client: TestClient) -> str:
        resp = client.post("/api/web/auth/login", json={"pin": "1234"})
        return resp.cookies[COOKIE_NAME]

    def test_system_action_requires_auth(self, client: TestClient):
        resp = client.post("/api/web/system/action", json={"action": "sync_all"})
        assert resp.status_code == 401

    def test_sync_schedule_publishes_scheduled_trigger(self, client: TestClient, app: FastAPI):
        app.state.pipeline.run.reset_mock()
        app.state.pipeline.run.return_value = []
        cookie = self._login(client)

        resp = client.post(
            "/api/web/system/action",
            json={"action": "sync_schedule"},
            cookies={COOKIE_NAME: cookie},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["action"] == "sync_schedule"
        assert app.state.pipeline.run.call_count == 1
        event = app.state.pipeline.run.call_args.args[0]
        assert event.event_type == EventType.SYSTEM_SCHEDULED_TRIGGER
        assert event.payload["action"] == "schedule_daily_sync"
        assert event.payload["source"] == "web_ui_system"

    def test_sync_schedule_returns_structured_auth_failure(self, client: TestClient, app: FastAPI):
        failed = Event(
            event_type=EventType.CONNECTOR_FETCH_FAILED,
            aggregate_id="web_schedule_daily_sync",
            aggregate_type=AggregateType.SYSTEM,
            payload={
                "source": "jwxt",
                "success": False,
                "error_code": "jwxt_auth_requires_user_action",
                "error": "JWXT login requires user action.",
                "pulled_count": 0,
                "temporal_blocks_count": 0,
                "last_sync_at": "2026-06-18T12:00:00+00:00",
            },
        )
        app.state.pipeline.run = AsyncMock(return_value=[failed])
        cookie = self._login(client)

        resp = client.post(
            "/api/web/system/action",
            json={"action": "sync_schedule"},
            cookies={COOKIE_NAME: cookie},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert data["sync_status"]["success"] is False
        assert data["sync_status"]["error_code"] == "jwxt_auth_requires_user_action"
        assert data["sync_status"]["pulled_count"] == 0
        assert data["sync_status"]["temporal_blocks_count"] == 0

    def test_sync_schedule_returns_counts_on_success(self, client: TestClient, app: FastAPI):
        completed = Event(
            event_type=EventType.CONNECTOR_FETCH_COMPLETED,
            aggregate_id="web_schedule_daily_sync",
            aggregate_type=AggregateType.SYSTEM,
            payload={
                "source": "jwxt",
                "success": True,
                "pulled_count": 4,
                "block_count": 3,
                "temporal_blocks_count": 3,
                "last_sync_at": "2026-06-18T12:00:00+00:00",
            },
        )
        app.state.pipeline.run = AsyncMock(return_value=[completed])
        cookie = self._login(client)

        resp = client.post(
            "/api/web/system/action",
            json={"action": "sync_schedule"},
            cookies={COOKIE_NAME: cookie},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["sync_status"]["success"] is True
        assert data["sync_status"]["pulled_count"] == 4
        assert data["sync_status"]["temporal_blocks_count"] == 3
        assert data["sync_status"]["last_sync_at"] == "2026-06-18T12:00:00+00:00"

    def test_direct_jwxt_sync_api_returns_structured_failure(self, client: TestClient, app: FastAPI):
        failed = Event(
            event_type=EventType.CONNECTOR_FETCH_FAILED,
            aggregate_id="jwxt",
            aggregate_type=AggregateType.SYSTEM,
            payload={
                "source": "jwxt",
                "success": False,
                "error_code": "jwxt_credentials_missing",
                "message": "JWXT username or password is not configured.",
                "pulled_count": 0,
                "temporal_blocks_count": 0,
                "last_sync_at": "2026-06-18T12:00:00+00:00",
            },
        )
        app.state.pipeline.run = AsyncMock(return_value=[failed])
        cookie = self._login(client)

        resp = client.post(
            "/api/web/sync/jwxt",
            cookies={COOKIE_NAME: cookie},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert data["success"] is False
        assert data["error_code"] == "jwxt_credentials_missing"
        assert data["pulled_count"] == 0
        assert data["temporal_blocks_count"] == 0

    def test_sync_homework_returns_structured_failure(self, client: TestClient, app: FastAPI):
        failed = Event(
            event_type=EventType.CONNECTOR_FETCH_FAILED,
            aggregate_id="web_check_homework",
            aggregate_type=AggregateType.HOMEWORK,
            payload={
                "source": "chaoxing",
                "error_code": "chaoxing_state_file_missing",
                "error": "Chaoxing login state is not configured.",
                "mock_enabled": False,
                "pulled_count": 0,
                "homework_count": 0,
            },
        )
        app.state.pipeline.run = AsyncMock(return_value=[failed])
        cookie = self._login(client)

        resp = client.post(
            "/api/web/system/action",
            json={"action": "sync_homework"},
            cookies={COOKIE_NAME: cookie},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert data["sync_status"]["status"] == "failed"
        assert data["sync_status"]["error_code"] == "chaoxing_state_file_missing"
        assert data["sync_status"]["pulled_count"] == 0
        assert data["sync_status"]["homework_count"] == 0

    def test_sync_homework_returns_filter_and_partial_status(
        self,
        client: TestClient,
        app: FastAPI,
    ):
        completed = Event(
            event_type=EventType.CONNECTOR_FETCH_COMPLETED,
            aggregate_id="web_check_homework",
            aggregate_type=AggregateType.HOMEWORK,
            payload={
                "source": "chaoxing",
                "mock_enabled": False,
                "pulled_count": 12,
                "homework_count": 12,
                "total_courses": 58,
                "filtered_courses": 5,
                "skipped_courses": 53,
                "scanned_courses": 3,
                "assignments_found": 12,
                "active_course_candidates": 5,
                "current_course_source": "temporal_blocks",
                "scanning_all_courses": False,
                "partial": True,
                "timeout": True,
            },
        )
        app.state.pipeline.run = AsyncMock(return_value=[completed])
        cookie = self._login(client)

        resp = client.post(
            "/api/web/system/action",
            json={"action": "sync_homework"},
            cookies={COOKIE_NAME: cookie},
        )

        assert resp.status_code == 200
        status = resp.json()["sync_status"]
        assert status["total_courses"] == 58
        assert status["filtered_courses"] == 5
        assert status["skipped_courses"] == 53
        assert status["scanned_courses"] == 3
        assert status["assignments_found"] == 12
        assert status["active_course_candidates"] == 5
        assert status["current_course_source"] == "temporal_blocks"
        assert status["scanning_all_courses"] is False
        assert status["partial"] is True
        assert status["timeout"] is True

    def test_sync_all_uses_cloud_sync_service(self, client: TestClient, app: FastAPI):
        app.state.pipeline.run.reset_mock()
        calls: list[str] = []

        async def run(*, trigger: str):
            calls.append(trigger)
            return {
                "ok": True,
                "status": "completed",
                "sources": {
                    "jwxt": {"status": "completed", "count": 3},
                    "chaoxing": {"status": "completed", "count": 4},
                    "google_calendar": {"status": "completed", "count": 5},
                },
                "events": 12,
            }

        app.state.cloud_sync_service = SimpleNamespace(run=run)
        cookie = self._login(client)

        resp = client.post(
            "/api/web/system/action",
            json={"action": "sync_all"},
            cookies={COOKIE_NAME: cookie},
        )

        assert resp.status_code == 200
        assert resp.json()["sync_status"]["status"] == "completed"
        assert calls == ["web_ui"]
        app.state.pipeline.run.assert_not_called()

    def test_calendar_review_publishes_review_requested(self, client: TestClient, app: FastAPI):
        app.state.pipeline.run.reset_mock()
        app.state.pipeline.run.return_value = []
        cookie = self._login(client)

        resp = client.post(
            "/api/web/system/action",
            json={"action": "calendar_review"},
            cookies={COOKIE_NAME: cookie},
        )

        assert resp.status_code == 200
        event = app.state.pipeline.run.call_args.args[0]
        assert event.event_type == EventType.CALENDAR_CONSISTENCY_REVIEW_REQUESTED
        assert event.payload["source"] == "web_ui_system"

    def test_calendar_repair_publishes_repair_requested(self, client: TestClient, app: FastAPI):
        app.state.pipeline.run.reset_mock()
        app.state.pipeline.run.return_value = []
        cookie = self._login(client)

        resp = client.post(
            "/api/web/system/action",
            json={"action": "calendar_repair"},
            cookies={COOKIE_NAME: cookie},
        )

        assert resp.status_code == 200
        event = app.state.pipeline.run.call_args.args[0]
        assert event.event_type == EventType.CALENDAR_CONSISTENCY_REPAIR_REQUESTED
        assert event.payload["source"] == "web_ui_system"

    def test_system_action_invalid_returns_400(self, client: TestClient):
        cookie = self._login(client)
        resp = client.post(
            "/api/web/system/action",
            json={"action": "restart_everything"},
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 400


    def test_returns_dashboard(self, client: TestClient, app: FastAPI):
        """Today actions return refreshed dashboard."""
        app.state.pipeline.run.reset_mock()
        app.state.pipeline.run.return_value = []
        cookie = self._login(client)
        resp = client.post(
            "/api/web/today/action",
            json={"action": "hydration", "amount_ml": 250},
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "dashboard" in data

    def test_missing_pipeline_returns_503(self, client: TestClient, app: FastAPI):
        """When pipeline is None, returns 503."""
        app.state.pipeline = None
        cookie = self._login(client)
        resp = client.post(
            "/api/web/today/action",
            json={"action": "hydration", "amount_ml": 250},
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 503


# ══════════════════════════════════════════════════════════════════════════════
# Local dev defaults — WEB_UI_PIN=123456
# ══════════════════════════════════════════════════════════════════════════════


class TestLocalDevDefaults:
    """Login with local dev defaults (PIN=123456, cookie_secure=False)."""

    @pytest.fixture
    def dev_app(self) -> FastAPI:
        from src.infrastructure.config import Settings
        app = FastAPI()
        app.include_router(web_router)
        settings = Settings()
        settings.web_ui_pin = "123456"
        settings.web_ui_session_secret = "local-dev-session-secret"
        settings.web_ui_session_days = 7
        settings.web_ui_cookie_secure = False
        settings.obsidian_vault_path = ""
        settings.telegram_allowed_users = [123]
        app.state.settings = settings
        se = MagicMock()
        se._state = {}
        se.get_all_derived.return_value = {
            "deadline_pressure": {"score": 0.0}, "workload_density": {"score": 0.0},
            "active_context": {"active_course_count": 0},
        }
        app.state.state_engine = se
        pipeline = MagicMock()
        pipeline.run = AsyncMock(return_value=[])
        app.state.pipeline = pipeline
        return app

    @pytest.fixture
    def dev_client(self, dev_app: FastAPI) -> TestClient:
        return TestClient(dev_app)

    def test_login_dev_pin_success(self, dev_client: TestClient):
        resp = dev_client.post("/api/web/auth/login", json={"pin": "123456"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        assert COOKIE_NAME in resp.cookies

    def test_login_wrong_pin_401(self, dev_client: TestClient):
        resp = dev_client.post("/api/web/auth/login", json={"pin": "0000"})
        assert resp.status_code == 401
        assert "invalid_pin" in resp.json().get("detail", "")

    def test_cookie_set_with_secure_false(self, dev_client: TestClient):
        resp = dev_client.post("/api/web/auth/login", json={"pin": "123456"})
        assert resp.status_code == 200
        assert COOKIE_NAME in resp.cookies

    def test_auth_check_after_dev_login(self, dev_client: TestClient):
        r = dev_client.post("/api/web/auth/login", json={"pin": "123456"})
        cookie = r.cookies[COOKIE_NAME]
        resp = dev_client.get("/api/web/auth/check", cookies={COOKIE_NAME: cookie})
        assert resp.status_code == 200


class TestSettingsNoDefaultPin:
    """Settings class itself must not ship a production PIN."""

    def test_default_pin_empty(self):
        from src.infrastructure.config import Settings
        s = Settings(_env_file="")
        assert not s.web_ui_pin

    def test_default_cookie_secure_true(self):
        from src.infrastructure.config import Settings
        s = Settings(_env_file="")
        assert s.web_ui_cookie_secure is True

    def test_no_pin_configured_returns_503(self):
        from src.infrastructure.config import Settings
        app = FastAPI()
        app.include_router(web_router)
        settings = Settings(_env_file="")
        settings.web_ui_pin = ""
        settings.web_ui_session_secret = "t"
        app.state.settings = settings
        se = MagicMock()
        se._state = {}
        app.state.state_engine = se
        pipeline = MagicMock()
        pipeline.run = AsyncMock(return_value=[])
        app.state.pipeline = pipeline
        client = TestClient(app)
        resp = client.post("/api/web/auth/login", json={"pin": "123456"})
        assert resp.status_code == 503
        assert "web_ui_pin_not_configured" in resp.json().get("detail", "")


# ══════════════════════════════════════════════════════════════════════════════
# Proposal decision from StateEngine — proposal_id-only fallback
# ══════════════════════════════════════════════════════════════════════════════


class TestProposalDecisionFromStateEngine:
    """Tests: /api/web/proposals/decision with proposal_id-only via StateEngine."""

    def _login(self, client: TestClient) -> str:
        resp = client.post("/api/web/auth/login", json={"pin": "1234"})
        return resp.cookies[COOKIE_NAME]

    @pytest.fixture
    def se_app(self) -> FastAPI:
        """App fixture with a real StateEngine seeded with a pending proposal."""
        from src.core.state_engine import StateEngine
        from src.core.events import Event, EventType, AggregateType
        from src.core.proposal import Proposal, ProposalType, TargetSystem

        app = FastAPI()
        app.include_router(web_router)

        # Settings
        settings = MagicMock()
        settings.web_ui_pin = "1234"
        settings.web_ui_session_secret = "test-secret-key-for-hmac"
        settings.web_ui_session_days = 7
        settings.obsidian_vault_path = ""
        settings.telegram_allowed_users = [123]
        settings.google_calendar_mock = False
        settings.google_calendar_write_enabled = True
        settings.google_calendar_write_requires_acceptance = True
        app.state.settings = settings

        # Real StateEngine with a pending proposal
        engine = StateEngine()
        proposal = Proposal(
            proposal_type=ProposalType.CREATE_CALENDAR_BLOCK,
            target_system=TargetSystem.GOOGLE_CALENDAR,
            action_payload={"title": "Test", "start": "2026-06-06T12:00:00+08:00", "end": "2026-06-06T13:00:00+08:00"},
            user_id="123",
        )
        import asyncio
        asyncio.run(engine.apply(Event(
            event_type=EventType.EXECUTION_PROPOSAL_CREATED,
            aggregate_id=proposal.proposal_id,
            aggregate_type=AggregateType.SYSTEM,
            payload=proposal.to_dict(),
        )))
        app.state.state_engine = engine
        app.state._test_proposal_id = proposal.proposal_id
        app.state._test_proposal = proposal

        # Mock pipeline
        pipeline = MagicMock()
        pipeline.run = AsyncMock(return_value=[
            Event(
                event_type=EventType.EXECUTION_COMPLETED,
                aggregate_id=proposal.proposal_id,
                aggregate_type=AggregateType.SYSTEM,
                payload={
                    "proposal_id": proposal.proposal_id,
                    "event_id": "test-event",
                    "title": "Test",
                    "start": "2026-06-06T12:00:00+08:00",
                    "end": "2026-06-06T13:00:00+08:00",
                },
            )
        ])
        app.state.pipeline = pipeline

        return app

    @pytest.fixture
    def se_app_empty(self) -> FastAPI:
        """App fixture with a real empty StateEngine (no proposals)."""
        from src.core.state_engine import StateEngine
        app = FastAPI()
        app.include_router(web_router)
        settings = MagicMock()
        settings.web_ui_pin = "1234"
        settings.web_ui_session_secret = "test-secret-key"
        settings.web_ui_session_days = 7
        settings.obsidian_vault_path = ""
        settings.telegram_allowed_users = [123]
        app.state.settings = settings
        app.state.state_engine = StateEngine()
        pipeline = MagicMock()
        pipeline.run = AsyncMock(return_value=[])
        app.state.pipeline = pipeline
        return app

    # ── Test 1: proposal_id-only + StateEngine has proposal → reject works ──

    def test_proposal_id_only_reject(self, se_app: FastAPI):
        """Reject with only proposal_id succeeds when StateEngine has the proposal."""
        client = TestClient(se_app)
        cookie = self._login(client)
        resp = client.post(
            "/api/web/proposals/decision",
            json={"decision": "reject", "proposal_id": se_app.state._test_proposal_id},
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["decision"] == "reject"
        se_app.state.pipeline.run.assert_called_once()

    # ── Test 2: proposal_id-only + StateEngine has proposal → accept works ──

    def test_proposal_id_only_accept(self, se_app: FastAPI):
        """Accept with only proposal_id succeeds when StateEngine has the proposal."""
        client = TestClient(se_app)
        cookie = self._login(client)
        resp = client.post(
            "/api/web/proposals/decision",
            json={"decision": "accept", "proposal_id": se_app.state._test_proposal_id},
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["decision"] == "accept"

    # ── Test 3: proposal_id-only but StateEngine cannot find it ────────────

    def test_proposal_id_not_found(self, se_app_empty: FastAPI):
        """proposal_id that StateEngine cannot find returns a clear error."""
        client = TestClient(se_app_empty)
        cookie = self._login(client)
        resp = client.post(
            "/api/web/proposals/decision",
            json={"decision": "accept", "proposal_id": "nonexistent"},
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert "未找到" in data.get("message", "")
        assert "proposal_id" in data

    # ── Test 4: Full proposal dict still works (backward compat) ──────────

    def test_full_proposal_backwards_compat(self, se_app: FastAPI):
        """Sending the full proposal dict still works (old path)."""
        from src.core.proposal import Proposal, ProposalType, TargetSystem
        proposal = Proposal(
            proposal_type=ProposalType.CREATE_CALENDAR_BLOCK,
            target_system=TargetSystem.GOOGLE_CALENDAR,
            action_payload={"title": "吃饭", "start": "2026-06-06T12:00:00+08:00", "end": "2026-06-06T13:00:00+08:00"},
            user_id="123",
        )
        se_app.state.pipeline.run.reset_mock()
        se_app.state.pipeline.run.return_value = []
        client = TestClient(se_app)
        cookie = self._login(client)
        resp = client.post(
            "/api/web/proposals/decision",
            json={"decision": "reject", "proposal": proposal.to_dict()},
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["decision"] == "reject"
        se_app.state.pipeline.run.assert_called_once()

    # ── Test 5: Replay recovery — no process cache, only StateEngine ──────

    def test_after_replay_recover_accept(self, se_app: FastAPI):
        """After replay (no process cache), proposal_id-only accept works."""
        # The se_app fixture already has a proposal in StateEngine.
        # Simulate "process restart" by getting the stored proposal_id
        # and proving the endpoint works without any in-memory proposal list.
        client = TestClient(se_app)
        cookie = self._login(client)
        resp = client.post(
            "/api/web/proposals/decision",
            json={"decision": "accept", "proposal_id": se_app.state._test_proposal_id},
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        # After accept, the pipeline was called
        se_app.state.pipeline.run.assert_called()

    # ── Test 6: no decision → error ──────────────────────────────────────

    def test_decision_missing(self, se_app: FastAPI):
        """Missing decision returns error."""
        client = TestClient(se_app)
        cookie = self._login(client)
        resp = client.post(
            "/api/web/proposals/decision",
            json={"proposal_id": se_app.state._test_proposal_id},
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False

    # ── Test 7: no proposal and no proposal_id → error ───────────────────

    def test_no_proposal_no_id(self, se_app: FastAPI):
        """Neither proposal nor proposal_id → error."""
        client = TestClient(se_app)
        cookie = self._login(client)
        resp = client.post(
            "/api/web/proposals/decision",
            json={"decision": "accept"},
            cookies={COOKIE_NAME: cookie},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert "proposal" in data.get("message", "") or "proposal_id" in data.get("message", "")
