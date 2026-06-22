"""Tests for Render cloud mode: data-dir rebasing, health, admin import."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from src.infrastructure.config import Settings

SENTINEL = "DO_NOT_LEAK_RENDER_SECRET"


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


# ══════════════════════════════════════════════════════════════════════════════
# Data-dir normalization
# ══════════════════════════════════════════════════════════════════════════════


class TestDataDirNormalization:
    def test_default_data_dir_leaves_paths_unchanged(self):
        s = _settings(data_dir="data")
        s.normalize_data_paths()
        assert s.snapshot_path == "data/state_snapshot.json"
        assert s.chaoxing_state_file == "data/chaoxing_state.json"
        assert s.jwxt_cookies_path == "data/jwxt_cookies.json"
        assert s.google_calendar_credentials_path == "data/google_credentials.json"
        assert s.google_calendar_token_path == "data/google_token.json"

    def test_custom_data_dir_rebases_all_paths(self):
        s = _settings(data_dir="/var/data/cognitive-os")
        s.normalize_data_paths()
        assert s.snapshot_path == "/var/data/cognitive-os/state_snapshot.json"
        assert s.chaoxing_state_file == "/var/data/cognitive-os/chaoxing_state.json"
        assert s.jwxt_cookies_path == "/var/data/cognitive-os/jwxt_cookies.json"
        assert s.google_calendar_credentials_path == (
            "/var/data/cognitive-os/google_credentials.json"
        )
        assert s.google_calendar_token_path == "/var/data/cognitive-os/google_token.json"

    def test_rebase_preserves_explicit_env_override(self):
        """Paths set via env override should NOT be rebased."""
        s = _settings(
            data_dir="/var/data/cognitive-os",
            jwxt_cookies_path="/etc/secrets/jwxt_cookies.json",
        )
        s.normalize_data_paths()
        # Explicit override stays
        assert s.jwxt_cookies_path == "/etc/secrets/jwxt_cookies.json"
        # Default paths still rebase
        assert s.chaoxing_state_file == "/var/data/cognitive-os/chaoxing_state.json"

    def test_database_url_sqlite_rebased(self):
        s = _settings(data_dir="/var/data/cognitive-os")
        s.normalize_data_paths()
        assert s.database_url == (
            "sqlite+aiosqlite:///var/data/cognitive-os/cognitive_os.db"
        )

    def test_database_url_postgres_unchanged(self):
        s = _settings(
            data_dir="/var/data/cognitive-os",
            database_url="postgresql+asyncpg://user:pass@host/db",
        )
        s.normalize_data_paths()
        assert s.database_url == "postgresql+asyncpg://user:pass@host/db"

    def test_ensure_dirs_creates_directory(self, tmp_path):
        dd = tmp_path / "nested" / "data"
        s = _settings(data_dir=str(dd))
        s.ensure_dirs()
        assert dd.is_dir()

    def test_ensure_dirs_does_not_fail_on_existing(self, tmp_path):
        dd = tmp_path / "data"
        dd.mkdir(parents=True)
        s = _settings(data_dir=str(dd))
        s.ensure_dirs()  # no exception
        assert dd.is_dir()


# ══════════════════════════════════════════════════════════════════════════════
# Admin import endpoint
# ══════════════════════════════════════════════════════════════════════════════


class TestAdminImport:
    ADMIN_TOKEN = "test-admin-token-123"

    def _client(self, app_state=None):
        from fastapi.testclient import TestClient
        from src.interface.api.web_routes import router
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)
        if app_state:
            app.state.settings = app_state
        else:
            settings = _settings(
                render_admin_import_enabled=True,
                render_admin_import_token=self.ADMIN_TOKEN,
                data_dir="data",
            )
            settings.normalize_data_paths()
            app.state.settings = settings
        return TestClient(app)

    def test_disabled_by_default(self):
        """When RENDER_ADMIN_IMPORT_ENABLED is false, endpoint returns 403."""
        settings = _settings(
            render_admin_import_enabled=False,
            render_admin_import_token=self.ADMIN_TOKEN,
        )
        client = self._client(settings)
        resp = client.post(
            "/api/web/admin/import",
            json={"kind": "jwxt_cookies", "data": []},
            headers={"X-Admin-Token": self.ADMIN_TOKEN},
        )
        assert resp.status_code == 403

    def test_missing_token_returns_403(self):
        client = self._client()
        resp = client.post(
            "/api/web/admin/import",
            json={"kind": "jwxt_cookies", "data": []},
        )
        assert resp.status_code == 403

    def test_wrong_token_returns_403(self):
        client = self._client()
        resp = client.post(
            "/api/web/admin/import",
            json={"kind": "jwxt_cookies", "data": []},
            headers={"X-Admin-Token": "wrong"},
        )
        assert resp.status_code == 403

    def test_unknown_kind_returns_400(self):
        client = self._client()
        resp = client.post(
            "/api/web/admin/import",
            json={"kind": "env_file", "data": {}},
            headers={"X-Admin-Token": self.ADMIN_TOKEN},
        )
        assert resp.status_code == 400

    def test_jwxt_cookies_import_succeeds(self, tmp_path):
        dd = tmp_path / "render_data"
        dd.mkdir()
        settings = _settings(
            render_admin_import_enabled=True,
            render_admin_import_token=self.ADMIN_TOKEN,
            data_dir=str(dd),
            jwxt_cookies_path=str(dd / "jwxt_cookies.json"),
        )
        client = self._client(settings)
        cookies = [{"name": "session", "value": SENTINEL, "domain": "unn.edu.cn"}]
        resp = client.post(
            "/api/web/admin/import",
            json={"kind": "jwxt_cookies", "data": cookies},
            headers={"X-Admin-Token": self.ADMIN_TOKEN},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["saved"] is True
        assert data["kind"] == "jwxt_cookies"
        assert data["path_basename"] == "jwxt_cookies.json"
        assert data["json_valid"] is True
        assert data["no_secret_printed"] is True
        assert SENTINEL not in json.dumps(data)

        # Verify file written
        assert (dd / "jwxt_cookies.json").is_file()
        written = json.loads((dd / "jwxt_cookies.json").read_text("utf-8"))
        assert written == cookies

    def test_chaoxing_state_import_succeeds(self, tmp_path):
        dd = tmp_path / "render_data"
        dd.mkdir()
        settings = _settings(
            render_admin_import_enabled=True,
            render_admin_import_token=self.ADMIN_TOKEN,
            data_dir=str(dd),
            chaoxing_state_file=str(dd / "chaoxing_state.json"),
        )
        client = self._client(settings)
        state = {"cookies": [{"name": "uid", "value": SENTINEL, "domain": "chaoxing.com"}]}
        resp = client.post(
            "/api/web/admin/import",
            json={"kind": "chaoxing_state", "data": state},
            headers={"X-Admin-Token": self.ADMIN_TOKEN},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["saved"] is True
        assert data["kind"] == "chaoxing_state"
        assert SENTINEL not in json.dumps(data)

        assert (dd / "chaoxing_state.json").is_file()

    def test_google_token_import_succeeds(self, tmp_path):
        dd = tmp_path / "render_data"
        dd.mkdir()
        settings = _settings(
            render_admin_import_enabled=True,
            render_admin_import_token=self.ADMIN_TOKEN,
            data_dir=str(dd),
            google_calendar_token_path=str(dd / "google_token.json"),
        )
        client = self._client(settings)
        token = {"access_token": SENTINEL, "refresh_token": SENTINEL}
        resp = client.post(
            "/api/web/admin/import",
            json={"kind": "google_token", "data": token},
            headers={"X-Admin-Token": self.ADMIN_TOKEN},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["saved"] is True
        assert SENTINEL not in json.dumps(data)

    def test_google_credentials_import_succeeds(self, tmp_path):
        dd = tmp_path / "render_data"
        dd.mkdir()
        settings = _settings(
            render_admin_import_enabled=True,
            render_admin_import_token=self.ADMIN_TOKEN,
            data_dir=str(dd),
            google_calendar_credentials_path=str(dd / "google_credentials.json"),
        )
        client = self._client(settings)
        creds = {"installed": {"client_id": SENTINEL, "client_secret": SENTINEL}}
        resp = client.post(
            "/api/web/admin/import",
            json={"kind": "google_credentials", "data": creds},
            headers={"X-Admin-Token": self.ADMIN_TOKEN},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["saved"] is True
        assert SENTINEL not in json.dumps(data)

    def test_invalid_json_body_returns_400(self):
        client = self._client()
        resp = client.post(
            "/api/web/admin/import",
            content=b"not json",
            headers={
                "Content-Type": "application/json",
                "X-Admin-Token": self.ADMIN_TOKEN,
            },
        )
        assert resp.status_code == 400

    def test_missing_data_field_returns_400(self):
        client = self._client()
        resp = client.post(
            "/api/web/admin/import",
            json={"kind": "jwxt_cookies"},
            headers={"X-Admin-Token": self.ADMIN_TOKEN},
        )
        assert resp.status_code == 400

    def test_atomic_write_creates_backup(self, tmp_path):
        dd = tmp_path / "render_data"
        dd.mkdir()
        dest = dd / "jwxt_cookies.json"
        dest.write_text(json.dumps([{"old": "data"}]), encoding="utf-8")
        settings = _settings(
            render_admin_import_enabled=True,
            render_admin_import_token=self.ADMIN_TOKEN,
            data_dir=str(dd),
            jwxt_cookies_path=str(dest),
        )
        client = self._client(settings)
        resp = client.post(
            "/api/web/admin/import",
            json={"kind": "jwxt_cookies", "data": [{"new": "data"}]},
            headers={"X-Admin-Token": self.ADMIN_TOKEN},
        )
        assert resp.status_code == 200
        # Backup should exist
        backup = dest.with_suffix(".json.bak")
        assert backup.is_file()
        old = json.loads(backup.read_text("utf-8"))
        assert old == [{"old": "data"}]
        new = json.loads(dest.read_text("utf-8"))
        assert new == [{"new": "data"}]

    def test_no_unconfigured_token_blocks_even_with_valid_request(self):
        settings = _settings(
            render_admin_import_enabled=True,
            render_admin_import_token="",
        )
        client = self._client(settings)
        resp = client.post(
            "/api/web/admin/import",
            json={"kind": "jwxt_cookies", "data": []},
            headers={"X-Admin-Token": "anything"},
        )
        assert resp.status_code == 403


# ══════════════════════════════════════════════════════════════════════════════
# Health check — data_dir booleans
# ══════════════════════════════════════════════════════════════════════════════


class TestHealthCheckDataDir:
    def _login(self, client):
        from src.interface.api.web_routes import COOKIE_NAME
        resp = client.post("/api/web/auth/login", json={"pin": "1234"})
        return resp.cookies[COOKIE_NAME]

    def test_status_includes_data_dir_info(self):
        """GET /api/web/status must include a data_dir block with booleans."""
        from fastapi.testclient import TestClient
        from fastapi import FastAPI
        from src.interface.api.web_routes import router

        app = FastAPI()
        app.include_router(router)

        import tempfile
        dd = tempfile.mkdtemp()
        try:
            settings = _settings(
                web_ui_pin="1234",
                web_ui_session_secret="test-secret-for-status",
                web_ui_cookie_secure=False,
                allowed_origins="*",
                data_dir=str(dd),
                jwxt_cookies_path=str(Path(dd) / "jwxt_cookies.json"),
                chaoxing_state_file=str(Path(dd) / "chaoxing_state.json"),
                google_calendar_token_path=str(Path(dd) / "google_token.json"),
            )
            settings.normalize_data_paths()
            settings.ensure_dirs()
            # Create a dummy cookie file
            (Path(dd) / "jwxt_cookies.json").write_text("[]", encoding="utf-8")

            app.state.settings = settings
            client = TestClient(app)
            cookie = self._login(client)

            resp = client.get("/api/web/status", cookies={"cognitive_os_session": cookie})
            assert resp.status_code == 200
            data = resp.json()
            assert "data_dir" in data
            dd_info = data["data_dir"]
            assert dd_info["configured"] is True
            assert dd_info["exists"] is True
            assert dd_info["writable"] is True
            assert dd_info["jwxt_cookie_exists"] is True
            assert dd_info["chaoxing_state_exists"] is False
            assert dd_info["google_token_exists"] is False
            # Must be booleans, not strings or dicts
            for key in ("configured", "exists", "writable", "jwxt_cookie_exists",
                        "chaoxing_state_exists", "google_token_exists"):
                assert isinstance(dd_info[key], bool), f"{key} is not bool"
        finally:
            import shutil
            shutil.rmtree(dd, ignore_errors=True)

    def test_health_data_dir_returns_no_file_content(self):
        """data_dir info must never contain file contents."""
        from fastapi.testclient import TestClient
        from fastapi import FastAPI
        from src.interface.api.web_routes import router

        app = FastAPI()
        app.include_router(router)

        import tempfile
        dd = tempfile.mkdtemp()
        try:
            (Path(dd) / "jwxt_cookies.json").write_text(
                json.dumps([{"name": "token", "value": SENTINEL}]), encoding="utf-8"
            )
            settings = _settings(
                web_ui_pin="1234",
                web_ui_session_secret="test-secret-for-status",
                web_ui_cookie_secure=False,
                allowed_origins="*",
                data_dir=str(dd),
                jwxt_cookies_path=str(Path(dd) / "jwxt_cookies.json"),
                chaoxing_state_file=str(Path(dd) / "chaoxing_state.json"),
                google_calendar_token_path=str(Path(dd) / "google_token.json"),
            )
            app.state.settings = settings
            client = TestClient(app)
            cookie = self._login(client)

            resp = client.get("/api/web/status", cookies={"cognitive_os_session": cookie})
            data = resp.json()
            rendered = json.dumps(data)
            assert SENTINEL not in rendered
        finally:
            import shutil
            shutil.rmtree(dd, ignore_errors=True)


class TestRenderBlueprint:
    def test_blueprint_uses_free_web_and_neon(self):
        blueprint = Path("render.yaml").read_text(encoding="utf-8")

        assert "type: worker" not in blueprint
        assert "\n    disk:" not in blueprint
        assert "type: cron" not in blueprint
        assert "CLOUD_SYNC_TOKEN" in blueprint
        assert "RENDER_ADMIN_IMPORT_ENABLED" in blueprint
        assert 'value: "false"' in blueprint

    def test_github_actions_runs_daily_cloud_sync(self):
        workflow = Path(".github/workflows/cloud-sync.yml").read_text(
            encoding="utf-8",
        )

        assert 'cron: "0 23 * * *"' in workflow
        assert "workflow_dispatch:" in workflow
        assert "python scripts/render_cloud_sync.py" in workflow
        assert "${{ secrets.CLOUD_SYNC_TOKEN }}" in workflow
        assert "CLOUD_SYNC_SOURCES: google_calendar" in workflow

    def test_cloud_sync_settings_default_to_disabled_secret(self):
        settings = _settings()
        assert settings.cloud_sync_token == ""
        assert settings.cloud_sync_source_timeout_seconds == 180
