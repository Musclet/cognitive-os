"""Assignment scraper for Chaoxing.

Extracted from old project: app/chaoxing/parser.py + app/extractor/assignment_extractor.py
Refactored: async, returns structured dict, no file writes, no workspace.
"""

from __future__ import annotations

import logging
import re
from typing import Any, TYPE_CHECKING
from urllib.parse import urlparse, parse_qs

if TYPE_CHECKING:
    from playwright.async_api import Page
else:
    Page = Any

from src.connector.chaoxing.browser import ChaoxingBrowser
from src.domain.course_topology import infer_teacher, normalize_course_name

logger = logging.getLogger(__name__)


def _extract_params_from_url(url: str) -> dict:
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    return {k: qs[k][0] for k in ["courseid", "clazzid", "cpi"] if k in qs}


async def _click_work_nav(page: Page) -> bool:
    """Click the assignment nav tab in a course page."""
    methods = [
        lambda: page.locator("a[title='作业']").click(force=True, timeout=5000),
        lambda: page.locator("a:has-text('作业')").first.click(timeout=5000),
        lambda: page.evaluate("""() => {
            const links = document.querySelectorAll('a[title="作业"], a');
            for (const link of links) {
                if (link.textContent.includes('作业') || link.title === '作业') {
                    link.click(); return 'clicked';
                }
            }
            return 'not found';
        }"""),
    ]

    for method in methods:
        try:
            await method()
            await page.wait_for_timeout(2000)
            return True
        except Exception:
            continue
    return False


async def _parse_work_iframe(frame_or_page) -> list[dict]:
    """Parse assignment items from the work list iframe."""
    assignments = []
    seen_work_ids = set()

    items = await frame_or_page.locator("li[onclick*='goTask']").all()
    if not items:
        return assignments

    for li in items:
        try:
            url = (await li.get_attribute("data")) or ""
            if not url:
                continue
            m = re.search(r'workId=(\d+)', url)
            work_id = m.group(1) if m else url
            if work_id in seen_work_ids:
                continue
            seen_work_ids.add(work_id)

            aria_label = (await li.get_attribute("aria-label")) or ""
            title = ""
            status = ""
            if " ; " in aria_label:
                parts = aria_label.split(" ; ")
                title = parts[0].strip()
                status = parts[1].strip() if len(parts) > 1 else ""

            if not title:
                try:
                    p = li.locator("p.overHidden2").first
                    if await p.count() > 0:
                        title = (await p.inner_text()).strip()
                except Exception:
                    pass
            if not status:
                try:
                    p = li.locator("p.status").first
                    if await p.count() > 0:
                        status = (await p.inner_text()).strip()
                except Exception:
                    pass

            ddl = ""
            try:
                time_div = li.locator("div.time").first
                if await time_div.count() > 0:
                    ddl = (await time_div.inner_text()).strip()
            except Exception:
                pass

            assignments.append({
                "title": title[:256],
                "detail_url": url,
                "deadline": ddl[:64],
                "status": status[:32],
            })
        except Exception:
            continue

    return assignments


async def fetch_course_assignments(
    browser: ChaoxingBrowser,
    course_name: str,
    course_url: str,
) -> list[dict]:
    """Fetch all assignments for a single course.

    Returns list of {title, detail_url, deadline, status} dicts.
    Per-course error isolation: catches TargetClosedError and returns empty.
    """
    assignments: list[dict] = []
    logger.info("[PAGE] opening for course: %s", course_name[:30])
    
    try:
        page = await browser.new_page()
    except Exception as exc:
        logger.error(
            "[PAGE] new_page failed for '%s' error_type=%s",
            course_name[:30],
            type(exc).__name__,
        )
        return assignments

    try:
        course_params = _extract_params_from_url(course_url)
        course_id = course_params.get("courseid", "")
        clazz_id = course_params.get("clazzid", "")
        cpi = course_params.get("cpi", "")

        # 1. Enter course page
        await page.goto(course_url, wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(5000)

        # 2. Click assignment tab
        clicked = await _click_work_nav(page)
        if clicked:
            await page.wait_for_timeout(2000)
            try:
                await page.wait_for_selector("#frame_content-zy", timeout=10_000)
                await page.wait_for_timeout(3000)
            except Exception:
                pass

        # 3. Parse from iframe
        frame = page.frame(name="frame_content-zy")
        target = frame if frame else page
        if target:
            assignments = await _parse_work_iframe(target)

        # 4. Fallback: direct work list URL
        if not assignments and course_id:
            logger.info("Fallback: direct work list URL for %s", course_name[:20])
            work_enc = ""
            try:
                work_enc = (await page.locator("#workEnc").get_attribute("value")) or ""
            except Exception:
                pass

            params = f"courseId={course_id}&classId={clazz_id}&cpi={cpi}&ut=s"
            if work_enc:
                params += f"&enc={work_enc}"
            work_url = f"https://mooc1.chaoxing.com/mooc2/work/list?{params}"

            await page.goto(work_url, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(5000)

            try:
                await page.evaluate("""() => {
                    const btn = document.querySelector('.agreeStart');
                    if (btn) btn.click();
                }""")
                await page.wait_for_timeout(3000)
            except Exception:
                pass

            frame2 = page.frame(name="frame_content-zy")
            target2 = frame2 if frame2 else page
            assignments = await _parse_work_iframe(target2)

        summary = f"{len(assignments)} items" if assignments else "0 items"
        logger.info("[%s]: %s", course_name[:20], summary)

    except Exception as exc:
        logger.error(
            "[COURSE] failed '%s' error_type=%s",
            course_name[:30],
            type(exc).__name__,
        )
    finally:
        try:
            await page.close()
            logger.debug("[PAGE] closed for: %s", course_name[:20])
        except Exception:
            pass  # page may already be closed

    import hashlib, re
    from datetime import datetime, timedelta, timezone

    _STATUS_MAP = {
        "待批阅": "submitted",
        "未交": "pending",
        "未提交": "pending",
        "已提交": "submitted",
        "已批阅": "reviewed",
        "已完成": "completed",
        "进行中": "in_progress",
        "已过期": "expired",
    }

    for a in assignments:
        raw_status = a.get("status", "")
        a["raw_status"] = raw_status
        a["status"] = _STATUS_MAP.get(raw_status, raw_status or "pending")
        teacher = infer_teacher(course_name)
        a["course"] = normalize_course_name(course_name, teacher)
        a["teacher"] = teacher
        # Stable ID for dedup: hash of course + title
        raw_key = f"{course_name}||{a.get('title', '')}"
        a["id"] = hashlib.sha256(raw_key.encode()).hexdigest()[:16]

        # Convert "剩余X小时Y分钟" deadline to ISO format
        ddl = a.get("deadline", "")
        if "剩余" in ddl or "小时" in ddl:
            hours = 0
            minutes = 0
            h_match = re.search(r"(\d+)\s*小时", ddl)
            m_match = re.search(r"(\d+)\s*分钟", ddl)
            if h_match:
                hours = int(h_match.group(1))
            if m_match:
                minutes = int(m_match.group(1))
            if hours or minutes:
                abs_dl = datetime.now(timezone.utc) + timedelta(hours=hours, minutes=minutes)
                a["deadline"] = abs_dl.strftime("%Y-%m-%dT%H:%M:%SZ")
                a["deadline_raw"] = ddl
        elif ddl:
            # Try parsing known date formats
            try:
                for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%Y年%m月%d日 %H:%M"):
                    try:
                        parsed = datetime.strptime(ddl, fmt)
                        a["deadline"] = parsed.strftime("%Y-%m-%dT%H:%M:%SZ")
                        a["deadline_raw"] = ddl
                        break
                    except ValueError:
                        continue
            except Exception:
                pass

    return assignments


async def fetch_all_assignments(
    browser: ChaoxingBrowser,
    courses: list[dict],
    on_progress=None,
    batch_size: int = 5,
) -> list[dict]:
    """Fetch assignments for all courses.

    Args:
        browser: Authenticated browser instance.
        courses: List of {name, url} from fetch_course_list().

    Returns:
        Flat list of {course, title, detail_url, deadline, status} dicts.
    """
    all_assignments: list[dict] = []
    total = len(courses)
    logger.info("[ASSIGNMENT] fetching for %d courses...", total)

    for i, course in enumerate(courses):
        try:
            assignments = await fetch_course_assignments(
                browser, course["name"], course["url"]
            )
            all_assignments.extend(assignments)
            if (i + 1) % batch_size == 0 or (i + 1) == total:
                logger.info("[ASSIGNMENT] progress: %d/%d courses, %d items", i+1, total, len(all_assignments))
                if on_progress:
                    await on_progress(i + 1, total, len(all_assignments))
        except Exception as exc:
            logger.error(
                "[ASSIGNMENT] course %d/%d failed '%s' error_type=%s",
                i + 1,
                total,
                course.get("name", "?")[:30],
                type(exc).__name__,
            )
            all_assignments.append({
                "course": course.get("name", "?"),
                "error": type(exc).__name__,
                "title": "",
                "detail_url": "",
                "deadline": "",
                "status": "error",
            })

    
    logger.info("[ASSIGNMENT] complete: %d courses, %d assignments total", total, len(all_assignments))
    return all_assignments
