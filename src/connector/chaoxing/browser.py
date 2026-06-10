"""Browser context management for Chaoxing connector.

Extracted from old project: app/chaoxing/login.py + crawler.py
Refactored: async, no file system side effects, returns structured data only.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright, Browser, BrowserContext, Page

logger = logging.getLogger(__name__)

CHAOXING_URL = "https://i.chaoxing.com/"
LOGIN_SUCCESS_SELECTORS = [
    ".head-img", ".avatar", 'a[title="退出"]', ".logout",
    ".mycourse", ".course-list", ".personal-avatar",
]


class ChaoxingBrowser:
    """Manages Playwright browser lifecycle for Chaoxing.

    Only responsibility: create browser + context + page.
    Does NOT parse, does NOT write state, does NOT call external services.
    """

    def __init__(self, state_file: str = "data/chaoxing_state.json", headless: bool = True) -> None:
        self._state_path = Path(state_file)
        self._headless = headless
        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None

    @property
    def state_path(self) -> Path:
        return self._state_path

    async def start(self) -> BrowserContext:
        """Launch browser and create context with stored auth state.
        Idempotent: skips if already running.
        """
        if self._browser and self._context:
            logger.info("[BROWSER] already running, skipping start")
            return self._context

        if not self._state_path.exists():
            raise FileNotFoundError(
                f"State file not found: {self._state_path}\n"
                "Run login_and_save_state() first."
            )

        logger.info("[BROWSER] starting Playwright...")
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=self._headless)
        self._context = await self._browser.new_context(
            storage_state=str(self._state_path),
            viewport={"width": 1280, "height": 720},
            locale="zh-CN",
        )
        logger.info("[BROWSER] started")
        return self._context

    async def stop(self) -> None:
        """Clean shutdown. Safe to call multiple times."""
        logger.info("[BROWSER] stopping...")
        try:
            if self._browser:
                await self._browser.close()
                logger.info("[BROWSER] closed")
        except Exception as e:
            logger.warning("[BROWSER] close error: %s", e)
        try:
            if self._playwright:
                await self._playwright.stop()
                logger.info("[BROWSER] playwright stopped")
        except Exception as e:
            logger.warning("[BROWSER] playwright stop error: %s", e)
        self._context = None
        self._browser = None
        self._playwright = None

    async def save_state(self) -> bool:
        """Persist current browser cookies/localStorage to state file."""
        if not self._context:
            return False
        try:
            await self._context.storage_state(path=str(self._state_path))
            logger.debug("[BROWSER] state saved to %s", self._state_path)
            return True
        except Exception as e:
            logger.warning("[BROWSER] save state failed: %s", e)
            return False

    async def keepalive(self) -> bool:
        """Lightweight homepage visit to keep server-side session alive."""
        if not self._context:
            return False
        page = None
        try:
            page = await self.new_page()
            await page.goto(CHAOXING_URL, wait_until="domcontentloaded", timeout=15_000)
            if "passport" in page.url:
                logger.warning("[SESSION] keepalive failed — redirected to passport")
                return False
            logger.debug("[SESSION] keepalive ok")
            return True
        except Exception as e:
            logger.warning("[SESSION] keepalive error: %s", e)
            return False
        finally:
            if page:
                try:
                    await page.close()
                except Exception:
                    pass

    async def new_page(self) -> Page:
        """Create a new page in the browser context.
        Validates context health before creating.
        """
        if not self._context:
            raise RuntimeError("Browser not started. Call start() first.")
        try:
            # Quick health check: try listing existing pages
            _ = self._context.pages
        except Exception:
            logger.warning("[BROWSER] context unhealthy, restarting...")
            await self.stop()
            await self.start()
        page = await self._context.new_page()
        logger.debug("[PAGE] created")
        return page

    async def check_session_valid(self) -> bool:
        """Check if the stored auth session is still valid.

        Creates a temp page, navigates to Chaoxing, and checks
        for login indicators. Returns True if session is active.
        """
        if not self._context:
            return False
        page: Page | None = None
        try:
            page = await self.new_page()
            await page.goto(CHAOXING_URL, wait_until="domcontentloaded", timeout=30_000)
            url = page.url
            # If redirected to passport, cookies expired
            if "passport" in url:
                logger.warning("[SESSION] expired — redirected to passport login")
                return False
            # Check login success indicators
            for selector in LOGIN_SUCCESS_SELECTORS:
                try:
                    if await page.locator(selector).count() > 0:
                        logger.debug("[SESSION] valid — found: %s", selector)
                        return True
                except Exception:
                    pass
            logger.warning("[SESSION] no login indicators found — may be expired")
            return False
        except Exception as e:
            logger.warning("[SESSION] check failed: %s", e)
            return False
        finally:
            if page:
                try:
                    await page.close()
                except Exception:
                    pass


async def login_and_save_state(
    state_file: str = "data/chaoxing_state.json",
    timeout_seconds: int = 300,
) -> bool:
    """Interactive login: open browser, wait for user to log in, save state.

    Blocking — user must complete login in the browser window.
    Returns True if login was successful.
    """
    import asyncio

    state_path = Path(state_file)
    if state_path.exists():
        logger.info("State file exists: %s — skipping login", state_path)
        return True

    logger.info("Opening browser for manual Chaoxing login...")

    pw = await async_playwright().start()
    browser = None
    try:
        browser = await pw.chromium.launch(headless=False)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 720},
            locale="zh-CN",
        )
        page = await context.new_page()

        await page.goto(CHAOXING_URL, wait_until="domcontentloaded", timeout=30_000)

        logger.info("Please log in manually in the browser window...")
        logger.info("Program will auto-detect successful login.")

        deadline = asyncio.get_event_loop().time() + timeout_seconds
        success = False

        while asyncio.get_event_loop().time() < deadline:
            for selector in LOGIN_SUCCESS_SELECTORS:
                try:
                    if await page.locator(selector).count() > 0:
                        logger.info("Login detected via: %s", selector)
                        await page.wait_for_timeout(2000)
                        success = True
                        break
                except Exception:
                    pass

            if success:
                break

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

        if success:
            state_path.parent.mkdir(parents=True, exist_ok=True)
            await context.storage_state(path=str(state_path))
            logger.info("State saved to: %s", state_path)
        else:
            logger.error("Login timeout — state not saved")

        return success

    finally:
        await browser.close()
        await pw.stop()
