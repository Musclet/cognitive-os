"""Tests: Mobile API auth, dashboard, health."""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, ".")

from src.interface.api.mobile_routes import router as mobile_router


@pytest.fixture
def mobile_app() -> FastAPI:
    """Create a minimal app with mobile router and mock state."""
    app = FastAPI()
    app.include_router(mobile_router)

    settings = MagicMock()
    settings.web_ui_pin = "1234"
    settings.web_ui_session_secret = "test-session-secret"
    settings.mobile_api_secret = ""
    settings.mobile_token_days = 30
    settings.obsidian_vault_path = ""
    settings.telegram_allowed_users = [123]
    settings.finance_monthly_outing_budget = 250
    settings.finance_monthly_savings_target = 500

    state_engine = MagicMock()
    state_engine._state = {}
    state_engine._derived = {}
    state_engine.get_all_derived.return_value = {
        "deadline_pressure": {},
        "workload_density": {},
        "active_context": {},
    }

    event_store = MagicMock()
    event_store.count = AsyncMock(return_value=42)

    pipeline = MagicMock()
    pipeline.run = AsyncMock(return_value=[])

    app.state.settings = settings
    app.state.state_engine = state_engine
    app.state.event_store = event_store
    app.state.pipeline = pipeline

    return app


@pytest.fixture
def mobile_client(mobile_app: FastAPI) -> TestClient:
    return TestClient(mobile_app)


# ══════════════════════════════════════════════════════════════════════════════
# Auth tests
# ══════════════════════════════════════════════════════════════════════════════


class TestMobileAuth:
    def test_login_wrong_pin_returns_401(self, mobile_client: TestClient):
        resp = mobile_client.post("/api/mobile/auth/login", json={"pin": "0000"})
        assert resp.status_code == 401
        assert "invalid_pin" in resp.json().get("detail", "")

    def test_login_correct_pin_returns_token(self, mobile_client: TestClient):
        resp = mobile_client.post("/api/mobile/auth/login", json={"pin": "1234"})
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert "expires_at" in data

    def test_login_empty_pin_returns_401(self, mobile_client: TestClient):
        resp = mobile_client.post("/api/mobile/auth/login", json={"pin": ""})
        assert resp.status_code == 401

    def test_login_missing_body_returns_401(self, mobile_client: TestClient):
        resp = mobile_client.post("/api/mobile/auth/login", json={})
        assert resp.status_code == 401


# ══════════════════════════════════════════════════════════════════════════════
# Dashboard tests
# ══════════════════════════════════════════════════════════════════════════════


class TestMobileDashboard:
    def test_dashboard_no_token_returns_401(self, mobile_client: TestClient):
        resp = mobile_client.get("/api/mobile/dashboard")
        assert resp.status_code == 401

    def test_dashboard_with_valid_token_returns_200(self, mobile_client: TestClient):
        # Login first
        login = mobile_client.post("/api/mobile/auth/login", json={"pin": "1234"})
        token = login.json()["access_token"]

        resp = mobile_client.get(
            "/api/mobile/dashboard",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200

    def test_dashboard_with_wrong_token_returns_401(self, mobile_client: TestClient):
        resp = mobile_client.get(
            "/api/mobile/dashboard",
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert resp.status_code == 401


# ══════════════════════════════════════════════════════════════════════════════
# Health tests
# ══════════════════════════════════════════════════════════════════════════════


class TestMobileHealth:
    def test_health_no_token_returns_401(self, mobile_client: TestClient):
        resp = mobile_client.get("/api/mobile/health")
        assert resp.status_code == 401

    def test_health_with_valid_token_returns_200(self, mobile_client: TestClient):
        login = mobile_client.post("/api/mobile/auth/login", json={"pin": "1234"})
        token = login.json()["access_token"]

        resp = mobile_client.get(
            "/api/mobile/health",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "server_time" in data
        assert "app_version" in data
        assert "database_status" in data
        assert "event_count" in data
