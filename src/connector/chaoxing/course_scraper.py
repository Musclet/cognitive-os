"""Course list scraper for Chaoxing.

Extracted from old project: app/chaoxing/crawler.py
Refactored: async, returns list[dict], no side effects.
"""

from __future__ import annotations

import logging

from playwright.async_api import Page

from src.connector.chaoxing.browser import ChaoxingBrowser, CHAOXING_URL


def _extract_course_id(url: str) -> str:
    from urllib.parse import urlparse, parse_qs
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    cid = qs.get("courseid", [""])[0]
    clid = qs.get("clazzid", [""])[0]
    return f"{cid}_{clid}" if cid and clid else ""


logger = logging.getLogger(__name__)

NOISE_TEXTS = ["课程已结束", "已结束", "已归档", "移动到", "退课", "置顶"]


def _is_noise(text: str) -> bool:
    for noise in NOISE_TEXTS:
        if noise == text or text.startswith(noise):
            return True
    return len(text) <= 2


async def _click_course_menu(page: Page) -> bool:
    """Click the course menu in the sidebar."""
    for selector in ["div[name='课程']", "[name='课程']", "h3:has-text('课程')"]:
        try:
            el = page.locator(selector).first
            if await el.count() > 0:
                await el.click()
                await page.wait_for_timeout(3000)
                return True
        except Exception:
            continue
    logger.warning("Course menu not found")
    return False


async def _parse_courses(page: Page) -> list[dict]:
    """Parse course list from the page."""
    selectors = [
        "a.color1",
        "#courseList a.color1",
        "#stuNormalCourseListDiv a.color1",
        "#stuTopCourseListDiv a.color1",
        "a[class*='color']",
    ]
    seen_urls = set()

    for sel in selectors:
        try:
            elements = await page.locator(sel).all()
            if not elements:
                continue

            courses = []
            for el in elements:
                try:
                    text = (await el.inner_text()).strip()
                    href = (await el.get_attribute("href")) or ""
                    if not text or not href or "courseid" not in href:
                        continue
                    if _is_noise(text):
                        continue
                    if href in seen_urls:
                        continue
                    seen_urls.add(href)
                    course_id = _extract_course_id(href)
                    courses.append({"name": text[:128], "url": href, "course_id": course_id})
                except Exception:
                    continue

            if courses:
                logger.info("Found %d courses via '%s'", len(courses), sel)
                return courses
        except Exception:
            continue

    # Fallback: find all links with courseid
    logger.info("Fallback: searching all courseid links...")
    elements = await page.locator("a[href*='courseid']").all()
    courses = []
    for el in elements:
        try:
            text = (await el.inner_text()).strip()
            href = (await el.get_attribute("href")) or ""
            if not text or _is_noise(text):
                continue
            if href in seen_urls:
                continue
            seen_urls.add(href)
            course_id = _extract_course_id(href)
            courses.append({"name": text[:128], "url": href, "course_id": course_id})
        except Exception:
            continue
    return courses


async def fetch_course_list(browser: ChaoxingBrowser) -> list[dict]:
    """Fetch all courses from Chaoxing.

    Returns list of {name, url} dicts. Pure data, no side effects.
    """
    page = await browser.new_page()
    try:
        logger.info("Navigating to Chaoxing personal space...")
        await page.goto(CHAOXING_URL, wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(3000)

        if not await _click_course_menu(page):
            return []

        await page.wait_for_timeout(5000)

        frame = page.frame(name="frame_content")
        target = frame if frame else page
        source = "iframe" if frame else "main"
        logger.info("Parsing courses from %s...", source)
        courses = await _parse_courses(target)

        logger.info("Total: %d courses", len(courses))
        return courses

    finally:
        await page.close()
