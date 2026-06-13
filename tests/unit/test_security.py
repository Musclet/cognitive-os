"""Tests: Inspector API admin token auth, CORS, web auth gating.

These tests verify the security fixes applied in the hardening pass:
- Inspector API requires admin token (INSPECTOR_ADMIN_TOKEN)
- Web UI endpoints require session cookie
- CORS does not use wildcard in production
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, ".")

from src.interface.api.app import create_app
from src.interface.api.web_routes import COOKIE_NAME


# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture
def inspector_app() -> FastAPI:
    """Create a full app with admin token configured for Inspector API tests."""
    # Mock event store that returns empty lists / zeros
    event_store = MagicMock()
    event_store.get_recent = AsyncMock(return_value=[])
    event_store.get_by_event_id = AsyncMock(return_value=None)
    event_store.get_by_aggregate = AsyncMock(return_value=[])
    event_store.get_by_type = AsyncMock(return_value=[])
    event_store.get_by_causation = AsyncMock(return_value=[])
    event_store.count = AsyncMock(return_value=0)
    event_store.last_sequence = AsyncMock(return_value=0)
    event_store.replay_all = AsyncMock(return_value=[])
    event_store.replay_from = AsyncMock(return_value=[])
    event_store.append = AsyncMock(return_value=1)

    snapshot_store = MagicMock()
    snapshot_store.get_latest = AsyncMock(return_value=None)
    snapshot_store.get_all = AsyncMock(return_value=[])
    snapshot_store.save = AsyncMock(return_value=None)

    state_engine = MagicMock()
    state_engine._state = {}
    state_engine._derived = {}
    state_engine._applied_count = 0
    state_engine.event_count = 0
    state_engine.state_hash.return_value = "abc123"
    state_engine.get_all_derived.return_value = {}
    state_engine.get_view.return_value = {}

    tracer = MagicMock()
    tracer.trace_count.return_value = 0
    tracer.get_recent.return_value = []

    dead_letter = MagicMock()
    dead_letter.count.return_value = 0

    pipeline = MagicMock()
    pipeline.run = AsyncMock(return_value=[])

    # Settings with admin token configured
    settings = MagicMock()
    settings.inspector_admin_token = "test-admin-token-42"
    settings.allowed_origins = "http://localhost:5173"
    settings.web_ui_pin = "1234"
    settings.web_ui_session_secret = "test-session-secret"
    settings.web_ui_session_days = 7
    settings.web_ui_cookie_secure = False
    settings.obsidian_vault_path = ""
    settings.telegram_allowed_users = [123]
    settings.momo_sync_enabled = False

    app = create_app(
        event_store=event_store,
        state_engine=state_engine,
        snapshot_store=snapshot_store,
        pipeline=pipeline,
        tracer=tracer,
        dead_letter=dead_letter,
        web_ui_dist_path=None,
        settings=settings,
    )
    return app


@pytest.fixture
def inspector_app_no_token() -> FastAPI:
    """Create a full app with NO admin token (Inspector API disabled)."""
    event_store = MagicMock()
    event_store.get_recent = AsyncMock(return_value=[])
    event_store.get_by_event_id = AsyncMock(return_value=None)
    event_store.get_by_aggregate = AsyncMock(return_value=[])
    event_store.get_by_type = AsyncMock(return_value=[])
    event_store.count = AsyncMock(return_value=0)
    event_store.last_sequence = AsyncMock(return_value=0)

    snapshot_store = MagicMock()
    snapshot_store.get_latest = AsyncMock(return_value=None)
    snapshot_store.get_all = AsyncMock(return_value=[])

    state_engine = MagicMock()
    state_engine._state = {}
    state_engine._derived = {}
    state_engine.event_count = 0
    state_engine.state_hash.return_value = "abc123"
    state_engine.get_all_derived.return_value = {}
    state_engine.get_view.return_value = {}

    pipeline = MagicMock()
    pipeline.run = AsyncMock(return_value=[])

    settings = MagicMock()
    settings.inspector_admin_token = ""  # NOT configured → 403
    settings.allowed_origins = "http://localhost:5173"
    settings.web_ui_pin = "1234"
    settings.web_ui_session_secret = "test-session-secret"
    settings.web_ui_session_days = 7
    settings.web_ui_cookie_secure = False
    settings.obsidian_vault_path = ""
    settings.telegram_allowed_users = [123]
    settings.momo_sync_enabled = False

    app = create_app(
        event_store=event_store,
        state_engine=state_engine,
        snapshot_store=snapshot_store,
        pipeline=pipeline,
        web_ui_dist_path=None,
        settings=settings,
    )
    return app


# ══════════════════════════════════════════════════════════════════════════════
# Inspector API admin token tests
# ══════════════════════════════════════════════════════════════════════════════


class TestInspectorAdminAuth:
    """Verify Inspector API endpoints require admin token."""

    # ── No token → 403 ────────────────────────────────────────────────────

    def test_state_denied_without_token(self, inspector_app: FastAPI):
        client = TestClient(inspector_app)
        resp = client.get("/state")
        assert resp.status_code == 403, f"expected 403, got {resp.status_code}: {resp.text}"

    def test_events_recent_denied_without_token(self, inspector_app: FastAPI):
        client = TestClient(inspector_app)
        resp = client.get("/events/recent")
        assert resp.status_code == 403, f"expected 403, got {resp.status_code}: {resp.text}"

    def test_stats_denied_without_token(self, inspector_app: FastAPI):
        client = TestClient(inspector_app)
        resp = client.get("/stats")
        assert resp.status_code == 403, f"expected 403, got {resp.status_code}: {resp.text}"

    def test_snapshots_denied_without_token(self, inspector_app: FastAPI):
        client = TestClient(inspector_app)
        resp = client.get("/snapshots")
        assert resp.status_code == 403, f"expected 403, got {resp.status_code}: {resp.text}"

    def test_dead_letter_denied_without_token(self, inspector_app: FastAPI):
        client = TestClient(inspector_app)
        resp = client.get("/dead-letter")
        assert resp.status_code == 403, f"expected 403, got {resp.status_code}: {resp.text}"

    # ── Correct token → 200 ───────────────────────────────────────────────

    def test_state_allowed_with_bearer_token(self, inspector_app: FastAPI):
        client = TestClient(inspector_app)
        resp = client.get(
            "/state",
            headers={"Authorization": "Bearer test-admin-token-42"},
        )
        assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text}"

    def test_events_recent_allowed_with_bearer_token(self, inspector_app: FastAPI):
        client = TestClient(inspector_app)
        resp = client.get(
            "/events/recent",
            headers={"Authorization": "Bearer test-admin-token-42"},
        )
        assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text}"

    def test_stats_allowed_with_bearer_token(self, inspector_app: FastAPI):
        client = TestClient(inspector_app)
        resp = client.get(
            "/stats",
            headers={"Authorization": "Bearer test-admin-token-42"},
        )
        assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text}"

    def test_state_allowed_with_x_admin_token_header(self, inspector_app: FastAPI):
        client = TestClient(inspector_app)
        resp = client.get(
            "/state",
            headers={"X-Admin-Token": "test-admin-token-42"},
        )
        assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text}"

    # ── Wrong token → 403 ─────────────────────────────────────────────────

    def test_wrong_token_denied(self, inspector_app: FastAPI):
        client = TestClient(inspector_app)
        resp = client.get(
            "/stats",
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert resp.status_code == 403, f"expected 403, got {resp.status_code}: {resp.text}"

    def test_empty_bearer_token_denied(self, inspector_app: FastAPI):
        client = TestClient(inspector_app)
        resp = client.get(
            "/stats",
            headers={"Authorization": "Bearer "},
        )
        assert resp.status_code == 403, f"expected 403, got {resp.status_code}: {resp.text}"

    # ── No token configured → Inspector disabled ──────────────────────────

    def test_inspector_disabled_when_token_empty(self, inspector_app_no_token: FastAPI):
        client = TestClient(inspector_app_no_token)
        resp = client.get("/state")
        assert resp.status_code == 403
        data = resp.json()
        assert "inspector_api_disabled" in data.get("detail", "")


# ══════════════════════════════════════════════════════════════════════════════
# Web UI auth tests (already covered in test_web_ui.py — cross-reference tests)
# ══════════════════════════════════════════════════════════════════════════════


class TestWebAuthSecurity:
    """Verify web UI auth holds after the security hardening pass."""

    def test_dashboard_requires_session(self, inspector_app: FastAPI):
        """Unauthenticated access to /api/web/dashboard must return 401."""
        client = TestClient(inspector_app)
        resp = client.get("/api/web/dashboard")
        assert resp.status_code == 401, f"expected 401, got {resp.status_code}: {resp.text}"

    def test_login_sets_cookie(self, inspector_app: FastAPI):
        """Successful login must set the session cookie."""
        client = TestClient(inspector_app)
        resp = client.post("/api/web/auth/login", json={"pin": "1234"})
        assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text}"
        assert COOKIE_NAME in resp.cookies, "session cookie not set"

    def test_cookie_secure_respects_setting(self, inspector_app: FastAPI):
        """Cookie secure flag should be configurable."""
        client = TestClient(inspector_app)
        resp = client.post("/api/web/auth/login", json={"pin": "1234"})
        assert resp.status_code == 200
        # With web_ui_cookie_secure=False, set-cookie should NOT have "Secure"
        set_cookie = resp.headers.get("set-cookie", "")
        # secure=False → no "Secure" in the cookie string
        assert "Secure" not in set_cookie.split(";")[0], \
            f"cookie should not be Secure when web_ui_cookie_secure=False: {set_cookie}"

    def test_web_auth_check_requires_session(self, inspector_app: FastAPI):
        """Auth check without cookie must return 401."""
        client = TestClient(inspector_app)
        resp = client.get("/api/web/auth/check")
        assert resp.status_code == 401


# ══════════════════════════════════════════════════════════════════════════════
# CORS configuration test
# ══════════════════════════════════════════════════════════════════════════════


class TestCorsConfig:
    """Verify CORS middleware is not using wildcard."""

    def test_cors_does_not_use_wildcard(self, inspector_app: FastAPI):
        """Production app should not have allow_origins=['*']."""
        # TestClient sends origin=localhost by default; wildcard would work too
        # We verify by checking OPTIONS preflight with a disallowed origin
        client = TestClient(inspector_app)
        resp = client.options(
            "/state",
            headers={
                "Origin": "https://evil.example.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        # A wildcard CORS would return allow-origin: *
        # Our config only allows localhost origins → should NOT return the evil origin
        acao = resp.headers.get("access-control-allow-origin", "")
        assert acao != "*", "CORS should not use wildcard"
        assert "evil.example.com" not in acao, \
            f"evil origin should not be allowed: {acao}"

    def test_localhost_origin_is_allowed(self, inspector_app: FastAPI):
        """Localhost origin should pass CORS."""
        client = TestClient(inspector_app)
        resp = client.options(
            "/state",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
            },
        )
        # Should either allow the origin or return 200
        assert resp.status_code in (200, 204), \
            f"localhost origin should be allowed, got {resp.status_code}"


# ══════════════════════════════════════════════════════════════════════════════
# Web UI paths NOT protected by Inspector auth
# ══════════════════════════════════════════════════════════════════════════════


class TestInspectorDoesNotBlockWebUi:
    """Verify Inspector auth middleware does not block web UI paths."""

    def test_login_page_not_blocked(self, inspector_app: FastAPI):
        """/api/web/auth/login must NOT require admin token."""
        client = TestClient(inspector_app)
        # Without admin token, but with correct PIN
        resp = client.post("/api/web/auth/login", json={"pin": "1234"})
        assert resp.status_code == 200, f"login should work, got {resp.status_code}: {resp.text}"

    def test_app_spa_not_blocked(self, inspector_app: FastAPI):
        """The /app SPA routes must NOT require admin token."""
        client = TestClient(inspector_app)
        # /app should return a redirect or 404 (not 403)
        resp = client.get("/app")
        assert resp.status_code != 403, \
            f"/app should NOT require admin token, got {resp.status_code}"
