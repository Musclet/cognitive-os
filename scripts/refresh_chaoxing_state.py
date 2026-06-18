#!/usr/bin/env python
"""Refresh the Chaoxing login state file interactively.

Opens a visible browser window so you can log into Chaoxing / 超星学习通
manually. Once logged in, press Enter to save the browser state to a local
file so the automated homework sync can use it.

Usage:
    python scripts/refresh_chaoxing_state.py
    python scripts/refresh_chaoxing_state.py --timeout 180
    python scripts/refresh_chaoxing_state.py --state-file data/my_state.json

Environment (optional, overrides defaults):
    CHAOXING_STATE_FILE = path to state file (default: data/chaoxing_state.json)
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.infrastructure.config import Settings

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
)
logger = logging.getLogger("refresh_chaoxing_state")

EXIT_OK = 0
EXIT_PLAYWRIGHT_MISSING = 1
EXIT_LOGIN_TIMEOUT = 2
EXIT_LOGIN_NOT_DETECTED = 3
EXIT_GITIGNORE_FAIL = 4
EXIT_STATE_WRITE_ERROR = 5


def _check_gitignore(state_path: Path) -> bool:
    """Ensure the state file directory is git-ignored before writing."""
    import subprocess

    try:
        result = subprocess.run(
            ["git", "check-ignore", "-q", str(state_path)],
            cwd=Path(__file__).resolve().parent.parent,
            capture_output=True,
        )
        if result.returncode != 0:
            logger.error(
                "SAFETY: %s is NOT git-ignored. Refusing to write state file. "
                "Add it to .gitignore first.",
                state_path,
            )
            return False
        return True
    except FileNotFoundError:
        # git not available — warn but don't block
        logger.warning("git not found — skipping gitignore check")
        return True


def _count_storage_state(state_path: Path) -> dict[str, int]:
    """Return safe summary counts from a Playwright storage-state file."""
    import json

    try:
        data = json.loads(state_path.read_text("utf-8"))
    except Exception:
        return {"cookies_count": 0, "origins_count": 0}

    cookies = data.get("cookies", [])
    origins = data.get("origins", [])
    return {
        "cookies_count": len(cookies) if isinstance(cookies, list) else 0,
        "origins_count": len(origins) if isinstance(origins, list) else 0,
    }


def _has_chaoxing_cookies(state_path: Path) -> bool:
    """Check if storage state contains any Chaoxing-related cookie domains."""
    import json

    try:
        data = json.loads(state_path.read_text("utf-8"))
    except Exception:
        return False

    cookies = data.get("cookies", [])
    if not isinstance(cookies, list):
        return False

    chaoxing_domains = (
        "chaoxing", "passport2", "i.chaoxing", "mooc1",
        "edu.cn", "chaoxing.com",
    )
    for cookie in cookies:
        domain = str(cookie.get("domain", "") or "").casefold()
        if any(d in domain for d in chaoxing_domains):
            return True
    return False


async def _run_interactive(state_file: str, timeout_seconds: int) -> int:
    """Open browser, wait for manual login, save state."""
    from src.connector.chaoxing.browser import (
        async_playwright,
        CHAOXING_URL,
        LOGIN_SUCCESS_SELECTORS,
    )

    if async_playwright is None:
        logger.error("chaoxing_playwright_missing: Playwright is not installed.")
        logger.error("Install it with: pip install playwright")
        logger.error("Then: python -m playwright install chromium")
        return EXIT_PLAYWRIGHT_MISSING

    state_path = Path(state_file)

    # Check gitignore
    state_path.parent.mkdir(parents=True, exist_ok=True)
    if not _check_gitignore(state_path):
        return EXIT_GITIGNORE_FAIL

    import asyncio

    logger.info("=" * 56)
    logger.info("  Chaoxing Login State Refresh")
    logger.info("=" * 56)
    logger.info("")
    logger.info("A browser window will open shortly.")
    logger.info("1. Log in to Chaoxing / 超星学习通 in the browser.")
    logger.info("2. After login, the browser shows your course page.")
    logger.info("3. Come back to THIS terminal and press Enter.")
    logger.info("")

    pw = await async_playwright().start()
    browser = None
    try:
        browser = await pw.chromium.launch(headless=False)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 720},
            locale="zh-CN",
        )
        page = await context.new_page()

        try:
            await page.goto(
                CHAOXING_URL, wait_until="domcontentloaded", timeout=30_000
            )
        except Exception:
            logger.error("Failed to load Chaoxing page. Check network connection.")
            return EXIT_LOGIN_NOT_DETECTED

        logger.info("Browser opened at: %s", CHAOXING_URL)
        logger.info("Waiting for login... (timeout: %ds)", timeout_seconds)
        logger.info("")

        # Poll for auto-detection while also watching for Enter key
        deadline = asyncio.get_event_loop().time() + timeout_seconds
        success = False

        while asyncio.get_event_loop().time() < deadline:
            # Check for selectors indicating logged-in state
            for selector in LOGIN_SUCCESS_SELECTORS:
                try:
                    if await page.locator(selector).count() > 0:
                        logger.info("Login detected via selector: %s", selector)
                        await page.wait_for_timeout(2000)
                        success = True
                        break
                except Exception:
                    pass

            if success:
                break

            # Check URL-based detection
            try:
                url = page.url
                if "i.chaoxing.com" in url and "passport" not in url:
                    logger.info("Login detected via URL: %s", url)
                    await page.wait_for_timeout(2000)
                    success = True
                    break
            except Exception:
                pass

            await asyncio.sleep(2)

        if not success:
            logger.info("")
            logger.info(
                "Auto-detection did not confirm login within %ds.", timeout_seconds
            )
            logger.info(
                "If you ARE logged in, the browser state will still be saved."
            )
            logger.info(
                "If you are NOT logged in, press Ctrl+C to abort, "
                "or Enter to save current state anyway."
            )

        # Always wait for user Enter confirmation before saving
        logger.info("")
        try:
            input(">>> Press Enter to save the login state...")
        except (KeyboardInterrupt, EOFError):
            logger.info("Aborted by user.")
            return EXIT_LOGIN_NOT_DETECTED

        # Save state
        try:
            await context.storage_state(path=str(state_path))
        except OSError as exc:
            logger.error("Failed to write state file: %s", exc)
            return EXIT_STATE_WRITE_ERROR

        summary = _count_storage_state(state_path)
        has_chaoxing = _has_chaoxing_cookies(state_path)

        logger.info("")
        logger.info("=" * 56)
        logger.info("state file saved: True")
        logger.info("path: %s", state_path)
        logger.info("cookies_count: %d", summary["cookies_count"])
        logger.info("origins_count: %d", summary["origins_count"])
        logger.info("has_chaoxing_cookies: %s", has_chaoxing)
        logger.info("=" * 56)

        if not has_chaoxing:
            logger.warning(
                "No Chaoxing-related cookies detected. "
                "The login may not have completed successfully."
            )
            return EXIT_LOGIN_NOT_DETECTED

        return EXIT_OK

    finally:
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                pass
        try:
            await pw.stop()
        except Exception:
            pass


def main() -> int:
    settings = Settings()

    parser = argparse.ArgumentParser(
        description="Refresh the Chaoxing login state file interactively.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Seconds to wait for auto-detection of login (default: 300)",
    )
    parser.add_argument(
        "--state-file",
        default=settings.chaoxing_state_file,
        help="Path to the state file (default from CHAOXING_STATE_FILE env or data/chaoxing_state.json)",
    )
    args = parser.parse_args()

    try:
        import asyncio

        return asyncio.run(_run_interactive(args.state_file, args.timeout))
    except ImportError as exc:
        if "playwright" in str(exc).casefold():
            logger.error("chaoxing_playwright_missing: %s", exc)
            return EXIT_PLAYWRIGHT_MISSING
        raise
    except Exception as exc:
        logger.error("Unexpected error: %s", exc)
        return EXIT_STATE_WRITE_ERROR


if __name__ == "__main__":
    sys.exit(main())
