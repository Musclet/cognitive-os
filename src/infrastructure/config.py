"""Central configuration — loaded from env vars / .env file."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Telegram ──────────────────────────────────────────────────────
    telegram_bot_token: str = ""
    telegram_allowed_users: list[int] = []  # user_id whitelist

    # ── Storage ───────────────────────────────────────────────────────
    database_url: str = "sqlite+aiosqlite:///data/cognitive_os.db"
    snapshot_path: str = "data/state_snapshot.json"

    # ── Connectors ────────────────────────────────────────────────────
    chaoxing_username: str = ""
    chaoxing_password: str = ""
    chaoxing_mock: bool = True
    chaoxing_state_file: str = "data/chaoxing_state.json"

    jwxt_username: str = ""
    jwxt_password: str = ""
    jwxt_mock: bool = True
    jwxt_login_url: str = "https://jw.unn.edu.cn/xtgl/login_slogin.html"
    jwxt_schedule_year: str = "2025"
    jwxt_schedule_semester: str = "12"
    jwxt_semester_start: str = "2026-03-02"
    jwxt_schedule_window_days: int = 14
    jwxt_cookies_path: str = "data/jwxt_cookies.json"
    jwxt_cookies_json: str = ""
    jwxt_headless: bool = True

    google_calendar_mock: bool = True
    google_calendar_credentials_path: str = "data/google_credentials.json"
    google_calendar_token_path: str = "data/google_token.json"
    google_calendar_credentials_json: str = ""
    google_calendar_token_json: str = ""
    google_calendar_calendar_id: str = "primary"
    google_calendar_timezone: str = "Asia/Singapore"
    google_calendar_sync_window_days: int = 14
    google_calendar_write_enabled: bool = False
    google_calendar_write_requires_acceptance: bool = True
    google_calendar_schedule_write_enabled: bool = False
    google_calendar_schedule_calendar_id: str = "primary"
    google_calendar_schedule_sync_days: int = 7
    google_calendar_poll_interval_minutes: int = 30

    # ── DeepSeek (OpenAI-compatible Chat) ──────────────────────────────
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    deepseek_timeout_seconds: int = 30

    schedule_daily_sync_times: str = "07:30,22:30"
    nightly_review_enabled: bool = True
    nightly_review_time: str = "21:00"
    nightly_review_timezone: str = "Asia/Singapore"
    nightly_review_use_deepseek: bool = False

    # ── Momo / Vocabulary ──────────────────────────────────────────────
    momo_sync_project_path: str = ""
    momo_cache_path: str = ""
    momo_sync_enabled: bool = True
    momo_sync_interval_minutes: int = 30
    momo_stale_after_minutes: int = 90
    momo_sync_timeout_seconds: int = 45
    momo_sync_block_startup: bool = False
    momo_evening_check_time: str = "21:30"

    # ── Scheduler ─────────────────────────────────────────────────────
    schedule_check_interval_minutes: int = 30
    homework_check_interval_minutes: int = 60
    homework_sync_interval_hours: int = 12
    schedule_sync_interval_hours: int = 12
    cognitive_checkin_interval_minutes: int = 240

    # ── Obsidian / Daily notes ────────────────────────────────────────
    obsidian_vault_path: str = ""
    obsidian_daily_folder: str = "daily"
    obsidian_daily_template_path: str = "Templates/每日打卡模板.md"
    obsidian_daily_sink_enabled: bool = True
    workout_ui_base_url: str = ""
    workout_ui_access_token: str = ""

    # ── Art Planning ─────────────────────────────────────────────────
    art_planning_enabled: bool = True
    art_default_target_minutes: int = 360
    art_minimum_keepalive_minutes: int = 25
    art_calendar_id: str = "primary"
    art_managed_calendar_source: str = "daily_art_plan"
    art_vibe_coding_limit_minutes: int = 45

    # ── Finance / Money Reality ──────────────────────────────────────
    finance_monthly_outing_budget: int = 250
    finance_monthly_savings_target: int = 500
    parent_request_safe_interval_days: int = 3
    parent_request_single_risk_threshold: int = 75
    parent_request_weekly_risk_threshold: int = 300

    # ── Web UI ────────────────────────────────────────────────────────
    web_ui_pin: str = ""
    web_ui_session_secret: str = ""
    web_ui_session_days: int = 7
    web_ui_cookie_secure: bool = True

    # ── Inspector API ──────────────────────────────────────────────────
    inspector_admin_token: str = ""
    allowed_origins: str = "http://localhost:5173,http://localhost:8081"

    # ── Mobile API ───────────────────────────────────────────────────
    mobile_api_secret: str = ""
    mobile_token_days: int = 30
    mobile_app_enabled: bool = True

    # ── System ────────────────────────────────────────────────────────
    log_level: str = "INFO"
    data_dir: str = "data"

    def ensure_dirs(self) -> None:
        Path(self.data_dir).mkdir(parents=True, exist_ok=True)

    def apply_env_google_credentials(self) -> None:
        """Write Google Calendar credentials/token from env vars to temp files.

        Render cannot host files; set GOOGLE_CALENDAR_CREDENTIALS_JSON and
        GOOGLE_CALENDAR_TOKEN_JSON as environment variables.  This method
        validates the JSON, writes it to disk, and updates the file-path
        settings so the existing GoogleCalendarConnector works unchanged.

        Never logs the JSON content — only a success/failure line.
        """
        import json as _json
        import logging as _logging
        _log = _logging.getLogger(__name__)

        # ── Credentials ─────────────────────────────────────────────────
        if self.google_calendar_credentials_json:
            try:
                parsed = _json.loads(self.google_calendar_credentials_json)
                if not isinstance(parsed, dict) or "installed" not in parsed and "web" not in parsed:
                    _log.warning("credentials json valid but missing 'installed'/'web' key — skipping")
                else:
                    dest = str(Path(self.data_dir) / "google_credentials_from_env.json")
                    Path(dest).write_text(self.google_calendar_credentials_json, encoding="utf-8")
                    self.google_calendar_credentials_path = dest
                    _log.info("credentials json loaded from env → %s", dest)
            except _json.JSONDecodeError:
                _log.warning("credentials json from env is not valid JSON — skipping")
            except Exception:
                _log.exception("failed to write credentials json from env")

        # ── Token ───────────────────────────────────────────────────────
        if self.google_calendar_token_json:
            try:
                parsed = _json.loads(self.google_calendar_token_json)
                if not isinstance(parsed, dict):
                    _log.warning("token json from env is not a JSON object — skipping")
                else:
                    dest = str(Path(self.data_dir) / "google_token_from_env.json")
                    Path(dest).write_text(self.google_calendar_token_json, encoding="utf-8")
                    self.google_calendar_token_path = dest
                    _log.info("token json loaded from env → %s", dest)
            except _json.JSONDecodeError:
                _log.warning("token json from env is not valid JSON — skipping")
            except Exception:
                _log.exception("failed to write token json from env")

    def apply_env_jwxt_cookies(self) -> None:
        """Write JWXT cookies from env var to a file so JwxtConnector can load them.

        Render cannot host files; set JWXT_COOKIES_JSON as an environment variable.
        This method validates the JSON, writes it to disk, and updates the path
        setting so the JwxtConnector works unchanged.

        Never logs the cookie content.
        """
        import json as _json
        import logging as _logging
        _log = _logging.getLogger(__name__)

        if not self.jwxt_cookies_json:
            return
        try:
            parsed = _json.loads(self.jwxt_cookies_json)
            if not isinstance(parsed, list):
                _log.warning("jwxt cookies json is not a JSON array — skipping")
                return
            dest = str(Path(self.data_dir) / "jwxt_cookies_from_env.json")
            Path(dest).write_text(self.jwxt_cookies_json, encoding="utf-8")
            self.jwxt_cookies_path = dest
            _log.info("jwxt cookies loaded from env → %s (%d cookies)", dest, len(parsed))
        except _json.JSONDecodeError:
            _log.warning("jwxt cookies json from env is not valid JSON — skipping")
        except Exception:
            _log.exception("failed to write jwxt cookies from env")
