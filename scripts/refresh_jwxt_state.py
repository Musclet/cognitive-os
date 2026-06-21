#!/usr/bin/env python
"""Refresh JWXT cookies through an interactive browser login.

Opens a visible browser so the user can complete the JWXT login manually.
After confirmation, verifies that the browser cookies can access the schedule
API, then saves them to the configured local cookie file.

Usage:
    python scripts/refresh_jwxt_state.py
    python scripts/refresh_jwxt_state.py --cookies-file data/jwxt_cookies.json

Environment:
    JWXT_LOGIN_URL
    JWXT_COOKIES_PATH
    JWXT_SCHEDULE_YEAR
    JWXT_SCHEDULE_SEMESTER
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.connector.jwxt.client import (
    JwxtConnector,
    JwxtSyncError,
    SCHEDULE_API_PATH,
    SCHEDULE_PATH,
    async_playwright,
)
from src.infrastructure.config import Settings

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("refresh_jwxt_state")

EXIT_OK = 0
EXIT_PLAYWRIGHT_MISSING = 1
EXIT_PAGE_LOAD_FAILED = 2
EXIT_LOGIN_NOT_VERIFIED = 3
EXIT_GITIGNORE_FAIL = 4
EXIT_COOKIE_WRITE_ERROR = 5


def _check_gitignore(cookie_path: Path) -> bool:
    """Refuse to write a cookie file that Git does not ignore."""
    try:
        result = subprocess.run(
            ["git", "check-ignore", "-q", str(cookie_path)],
            cwd=Path(__file__).resolve().parent.parent,
            capture_output=True,
        )
    except FileNotFoundError:
        logger.warning("git not found — skipping gitignore check")
        return True
    if result.returncode == 0:
        return True
    logger.error(
        "SAFETY: %s is not git-ignored. Refusing to save JWXT cookies.",
        cookie_path,
    )
    return False


def _safe_url_parts(url: str) -> tuple[str, str]:
    """Return only the URL domain and path, excluding query/fragment data."""
    parsed = urlparse(url)
    return parsed.netloc, parsed.path


def _write_cookies_atomic(cookie_path: Path, cookies: list[dict]) -> None:
    """Atomically replace the local cookie file without logging its content."""
    cookie_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = cookie_path.with_name(f".{cookie_path.name}.tmp")
    try:
        temporary_path.write_text(
            json.dumps(cookies, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary_path, cookie_path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


async def _verify_schedule_access(
    cookies: list[dict],
    settings: Settings,
) -> tuple[bool, str, int]:
    """Verify cookies using the same schedule request contract as the connector."""
    import httpx

    cookie_jar: dict[str, str] = {}
    for item in cookies:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        value = str(item.get("value") or "")
        if name and value:
            cookie_jar[name] = value
    if not cookie_jar:
        return False, "jwxt_cookie_missing", 0

    connector = JwxtConnector(use_mock=False, settings=settings)
    try:
        async with httpx.AsyncClient(
            cookies=cookie_jar,
            timeout=30,
            follow_redirects=False,
        ) as client:
            response = await client.post(
                connector._absolute_url(SCHEDULE_API_PATH),
                data={
                    "xnm": settings.jwxt_schedule_year,
                    "xqm": settings.jwxt_schedule_semester,
                },
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Referer": connector._absolute_url(SCHEDULE_PATH),
                },
            )
        data = connector._decode_schedule_response(response)
    except JwxtSyncError as exc:
        return False, exc.error_code, 0
    except httpx.RequestError:
        return False, "jwxt_network_error", 0
    except Exception:
        return False, "jwxt_parser_error", 0

    kb_list = data.get("kbList", [])
    return True, "", len(kb_list) if isinstance(kb_list, list) else 0


async def _run_interactive(cookie_file: str) -> int:
    if async_playwright is None:
        logger.error("jwxt_playwright_missing: Playwright is not installed.")
        logger.error("Install it with: pip install playwright")
        logger.error("Then run: python -m playwright install chromium")
        return EXIT_PLAYWRIGHT_MISSING

    settings = Settings(jwxt_cookies_path=cookie_file)
    cookie_path = Path(cookie_file)
    cookie_path.parent.mkdir(parents=True, exist_ok=True)
    if not _check_gitignore(cookie_path):
        return EXIT_GITIGNORE_FAIL

    logger.info("=" * 56)
    logger.info("  JWXT Login State Refresh")
    logger.info("=" * 56)
    logger.info("")
    logger.info("A visible browser window will open.")
    logger.info("1. Complete the JWXT login manually in the browser.")
    logger.info("2. Finish any school-required verification shown there.")
    logger.info("3. Return to this terminal and press Enter.")
    logger.info("")

    playwright = await async_playwright().start()
    browser = None
    try:
        browser = await playwright.chromium.launch(headless=False)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            locale="zh-CN",
        )
        page = await context.new_page()
        try:
            await page.goto(
                settings.jwxt_login_url,
                wait_until="domcontentloaded",
                timeout=30_000,
            )
        except Exception:
            logger.error("jwxt_network_error: failed to load the JWXT login page.")
            return EXIT_PAGE_LOAD_FAILED

        domain, path = _safe_url_parts(page.url)
        logger.info("login_page_loaded: True")
        logger.info("login_url_domain: %s", domain)
        logger.info("login_url_path: %s", path)
        logger.info("")
        try:
            await asyncio.to_thread(
                input,
                ">>> Press Enter after the manual login is complete...",
            )
        except (KeyboardInterrupt, EOFError):
            logger.info("Aborted by user.")
            return EXIT_LOGIN_NOT_VERIFIED

        cookies = await context.cookies()
        verified, error_code, schedule_items = await _verify_schedule_access(
            cookies,
            settings,
        )
        logger.info("")
        logger.info("schedule_access_verified: %s", verified)
        logger.info("schedule_items_available: %d", schedule_items)
        if not verified:
            logger.error("error_code: %s", error_code)
            logger.error(
                "JWXT login was not verified. The existing cookie file was not changed."
            )
            return EXIT_LOGIN_NOT_VERIFIED

        try:
            _write_cookies_atomic(cookie_path, cookies)
        except OSError:
            logger.error("jwxt_cookie_write_failed: could not save the cookie file.")
            return EXIT_COOKIE_WRITE_ERROR

        logger.info("cookie_file_saved: True")
        logger.info("cookie_file_path: %s", cookie_path)
        logger.info("")
        logger.info(
            "Refresh complete. The Web system page can now use this cookie "
            "with “同步课表”."
        )
        return EXIT_OK
    finally:
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                pass
        try:
            await playwright.stop()
        except Exception:
            pass


def main() -> int:
    settings = Settings()
    parser = argparse.ArgumentParser(
        description="Refresh JWXT cookies through a visible manual login.",
    )
    parser.add_argument(
        "--cookies-file",
        default=settings.jwxt_cookies_path,
        help=(
            "Cookie output path "
            "(default from JWXT_COOKIES_PATH or data/jwxt_cookies.json)"
        ),
    )
    args = parser.parse_args()
    try:
        return asyncio.run(_run_interactive(args.cookies_file))
    except Exception:
        logger.error("jwxt_refresh_failed: unexpected refresh error.")
        return EXIT_COOKIE_WRITE_ERROR


if __name__ == "__main__":
    sys.exit(main())
