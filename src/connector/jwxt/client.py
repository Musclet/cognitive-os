"""JWXT connector.

Fetches ZFSoft/JWXT schedule data and normalizes it to TimeBlock events.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, time, timezone, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import NAMESPACE_URL, uuid4, uuid5

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from src.connector.base import Connector
from src.core.events import AggregateType, Event, EventType
from src.core.temporal import TemporalSource, TimeBlock, TimeBlockType
from src.domain.course_topology import normalize_course_name
from src.infrastructure.config import Settings

logger = logging.getLogger(__name__)

LOCAL_TZ = timezone(timedelta(hours=8))
SCHEDULE_PATH = "/kbcx/xskbcx_cxXskbcxIndex.html?gnmkdm=N2151&layout=default"

PERIOD_TIMES = {
    1: ("08:20", "09:00"),
    2: ("09:10", "09:50"),
    3: ("10:10", "10:50"),
    4: ("11:00", "11:40"),
    5: ("14:30", "15:10"),
    6: ("15:20", "16:00"),
    7: ("16:20", "17:00"),
    8: ("17:10", "17:50"),
    9: ("18:40", "19:20"),
    10: ("19:25", "20:05"),
    11: ("20:15", "20:55"),
    12: ("21:00", "21:40"),
}


class JwxtConnector(Connector):
    """教务系统课表 connector: produces TimeBlock events."""

    source_name = "jwxt"

    def __init__(self, use_mock: bool = True, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()
        self._use_mock = use_mock
        self._authenticated = False

    async def authenticate(self) -> bool:
        self._authenticated = bool(
            self._use_mock
            or self.settings.jwxt_username
            or Path(self.settings.jwxt_cookies_path).exists()
        )
        return self._authenticated

    async def fetch(self, params: dict[str, Any]) -> dict[str, Any]:
        query = params.get("query", "")
        if self._use_mock:
            if query == "weekly_schedule":
                return self._mock_weekly_schedule()
            if query == "today_schedule":
                return self._mock_today_schedule()

        if query in ("weekly_schedule", "today_schedule"):
            return await self._real_weekly_schedule()

        return {"source": self.source_name, "error": f"unsupported query: {query}"}

    async def _real_weekly_schedule(self) -> dict[str, Any]:
        # Prefer httpx (lightweight, works on Render), fall back to Playwright
        try:
            raw = await self._fetch_schedule_api_httpx()
        except Exception as e:
            logger.warning("httpx fetch failed, falling back to Playwright: %s", e)
            raw = await self._fetch_schedule_api_playwright()
        kb_list = raw.get("kbList", [])
        semester_start = _parse_date(self.settings.jwxt_semester_start)
        today = datetime.now(LOCAL_TZ).date()
        week_start = today - timedelta(days=today.weekday())
        current_week = _teaching_week(today, semester_start)
        window_days = max(1, int(getattr(self.settings, "jwxt_schedule_window_days", 14) or 14))
        window_end = today + timedelta(days=window_days - 1)
        weeks_ahead = max(1, (window_days + today.weekday() + 6) // 7)

        blocks: list[TimeBlock] = []
        all_courses: set[str] = set()
        skipped_by_week = 0

        for item in kb_list:
            raw_title = str(item.get("kcmc", "")).strip()
            teacher = str(item.get("xm", "")).strip()
            title = normalize_course_name(raw_title, teacher)
            if title:
                all_courses.add(title)
            if not raw_title:
                continue

            weeks_raw = str(item.get("zcd", "")).strip()
            weekday = _weekday_to_num(str(item.get("xqjmc", "")))
            if not weekday:
                continue

            start_text, end_text = _jcs_to_time(str(item.get("jcs", "")))
            if start_text == "00:00":
                continue

            room = str(item.get("cdmc", "")).strip() or "未提供地址"
            jcs = str(item.get("jcs", "")).strip()
            matched_window = False
            if current_week and semester_start:
                week_numbers = range(current_week, current_week + weeks_ahead + 1)
            else:
                week_numbers = [None]

            for teaching_week in week_numbers:
                if teaching_week is None:
                    class_date = week_start + timedelta(days=weekday - 1)
                else:
                    if weeks_raw and not _week_matches(weeks_raw, teaching_week):
                        continue
                    class_date = semester_start + timedelta(weeks=teaching_week - 1, days=weekday - 1)

                if class_date < today or class_date > window_end:
                    continue

                matched_window = True
                block_id = str(uuid5(
                    NAMESPACE_URL,
                    f"jwxt|{title}|{class_date.isoformat()}|{start_text}|{end_text}|{room}|{teacher}|{weeks_raw}",
                ))

                blocks.append(TimeBlock(
                    block_id=block_id,
                    source=TemporalSource.JWXT,
                    block_type=_infer_block_type(room, jcs),
                    start=_combine_local(class_date, start_text),
                    end=_combine_local(class_date, end_text),
                    title=title,
                    location=room,
                    description=teacher,
                    metadata={
                        "teacher": teacher,
                        "raw_title": raw_title,
                        "room": room,
                        "weeks": weeks_raw,
                        "jcs": jcs,
                        "weekday": weekday,
                        "teaching_week": teaching_week or current_week,
                    },
                ))

            if current_week and weeks_raw and not matched_window:
                skipped_by_week += 1

        blocks = _dedupe_blocks(blocks)
        return {
            "source": self.source_name,
            "blocks": [b.to_dict() for b in blocks],
            "count": len(blocks),
            "course_names": sorted(all_courses),
            "raw_count": len(kb_list),
            "skipped_by_week": skipped_by_week,
            "week_start": week_start.isoformat(),
            "window_days": window_days,
            "teaching_week": current_week,
        }

    async def _fetch_schedule_api_httpx(self) -> dict[str, Any]:
        """Fetch schedule via httpx using saved cookies — no browser needed (Render-safe)."""
        import httpx

        cookie_path = Path(self.settings.jwxt_cookies_path)
        logger.info("JWXT httpx: cookie_path=%s exists=%s", cookie_path, cookie_path.exists())
        if not cookie_path.exists():
            raise RuntimeError(f"JWXT cookie file not found: {cookie_path}")

        cookies_list = json.loads(cookie_path.read_text("utf-8"))
        cookies_jar: dict[str, str] = {}
        for c in cookies_list:
            cookies_jar[c["name"]] = c["value"]
        logger.info("JWXT httpx: loaded %d cookies: %s", len(cookies_jar), list(cookies_jar.keys()))

        api_url = self._absolute_url("/kbcx/xskbcx_cxXsgrkb.html?gnmkdm=N2151")
        logger.info("JWXT httpx: POST %s", api_url)
        async with httpx.AsyncClient(cookies=cookies_jar, timeout=30, follow_redirects=False) as client:
            r = await client.post(
                api_url,
                data={
                    "xnm": self.settings.jwxt_schedule_year,
                    "xqm": self.settings.jwxt_schedule_semester,
                },
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Referer": self._absolute_url("/kbcx/xskbcx_cxXskbcxIndex.html?gnmkdm=N2151&layout=default"),
                },
            )
            logger.info("JWXT httpx: response status=%s len=%d", r.status_code, len(r.text))
            r.raise_for_status()
            return r.json()

    async def _fetch_schedule_api_playwright(self) -> dict[str, Any]:
        p = await async_playwright().start()
        browser: Browser | None = None
        try:
            browser = await p.chromium.launch(
                headless=self.settings.jwxt_headless,
                args=["--disable-gpu", "--no-sandbox", "--disable-dev-shm-usage"],
            )
            context = await browser.new_context(
                viewport={"width": 1280, "height": 800},
                locale="zh-CN",
            )
            await self._load_cookies(context)
            page = await context.new_page()
            await page.goto(
                self.settings.jwxt_login_url,
                wait_until="domcontentloaded",
                timeout=30000,
            )
            await page.wait_for_load_state("networkidle", timeout=10000)

            if not await self._is_authenticated(page):
                await self._login(page)
                try:
                    await page.wait_for_load_state("networkidle", timeout=20000)
                except Exception:
                    pass
                await page.wait_for_timeout(5000)

            if not await self._is_authenticated(page):
                raise RuntimeError("教务登录失败：cookie 失效或账号密码未配置/不正确")

            await self._save_cookies(context)
            await page.goto(self._absolute_url(SCHEDULE_PATH), wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_load_state("networkidle", timeout=10000)
            api_text = await page.evaluate(
                """(params) => {
                    return fetch("/kbcx/xskbcx_cxXsgrkb.html?gnmkdm=N2151", {
                        method: "POST",
                        headers: { "Content-Type": "application/x-www-form-urlencoded" },
                        body: new URLSearchParams(params).toString()
                    }).then(r => r.text());
                }""",
                {
                    "xnm": self.settings.jwxt_schedule_year,
                    "xqm": self.settings.jwxt_schedule_semester,
                },
            )
            return json.loads(api_text)
        finally:
            if browser:
                await browser.close()
            await p.stop()

    async def _load_cookies(self, context: BrowserContext) -> bool:
        path = Path(self.settings.jwxt_cookies_path)
        if not path.exists():
            return False
        try:
            cookies = json.loads(path.read_text(encoding="utf-8"))
            if cookies:
                await context.add_cookies(cookies)
                logger.info("JWXT cookies loaded from %s", path)
                return True
        except Exception as exc:
            logger.warning("Failed to load JWXT cookies: %s", exc)
        return False

    async def _save_cookies(self, context: BrowserContext) -> None:
        path = Path(self.settings.jwxt_cookies_path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            cookies = await context.cookies()
            path.write_text(json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.warning("Failed to save JWXT cookies: %s", exc)

    async def _is_authenticated(self, page: Page) -> bool:
        if "index_initMenu" in page.url or "index_menu" in page.url:
            return True
        try:
            return bool(await page.locator("text=信息查询").first.text_content(timeout=2000))
        except Exception:
            return False

    async def _login(self, page: Page) -> None:
        if not self.settings.jwxt_username or not self.settings.jwxt_password:
            raise RuntimeError("JWXT_USERNAME/JWXT_PASSWORD 未配置")
        await page.fill("#yhm", self.settings.jwxt_username)
        await page.fill("#mm", self.settings.jwxt_password)
        await page.click("#dl")

    def _absolute_url(self, path: str) -> str:
        parsed = urlparse(self.settings.jwxt_login_url)
        return f"{parsed.scheme}://{parsed.netloc}{path}"

    def _mock_weekly_schedule(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        monday = now - timedelta(days=now.weekday())
        monday = monday.replace(hour=0, minute=0, second=0, microsecond=0)

        blocks = []
        blocks.append(_make_block(monday, 8, 0, 9, 40, "影视特效技术", "教学楼A-301", TimeBlockType.CLASS_LECTURE))
        blocks.append(_make_block(monday, 10, 0, 11, 40, "大学英语", "教学楼B-202", TimeBlockType.CLASS_LECTURE))
        tue = monday + timedelta(days=1)
        blocks.append(_make_block(tue, 14, 0, 15, 40, "计算机图形学", "实验楼C-101", TimeBlockType.CLASS_LAB))
        blocks.append(_make_block(tue, 16, 0, 17, 40, "C4D设计技术", "实验楼C-203", TimeBlockType.CLASS_LAB))
        wed = monday + timedelta(days=2)
        blocks.append(_make_block(wed, 8, 0, 9, 40, "大学英语", "教学楼A-301", TimeBlockType.CLASS_LECTURE))
        blocks.append(_make_block(wed, 10, 0, 11, 40, "虚拟现实技术", "实验楼D-203", TimeBlockType.CLASS_LAB))
        thu = monday + timedelta(days=3)
        blocks.append(_make_block(thu, 14, 0, 17, 40, "UE引擎应用", "实验楼C-101", TimeBlockType.CLASS_LAB))
        fri = monday + timedelta(days=4)
        blocks.append(_make_block(fri, 8, 0, 9, 40, "影视特效技术", "教学楼A-201", TimeBlockType.CLASS_LECTURE))
        blocks.append(_make_block(fri, 10, 0, 11, 40, "虚拟现实技术", "实验楼D-203", TimeBlockType.CLASS_LAB))

        return {
            "source": self.source_name,
            "blocks": [b.to_dict() for b in blocks],
            "count": len(blocks),
            "week_start": monday.isoformat(),
        }

    def _mock_today_schedule(self) -> dict[str, Any]:
        full = self._mock_weekly_schedule()
        now = datetime.now(timezone.utc)
        today_blocks = []
        for b_dict in full["blocks"]:
            b = TimeBlock.from_dict(b_dict)
            if b.start.date() == now.date():
                today_blocks.append(b_dict)
        return {
            "source": self.source_name,
            "blocks": today_blocks,
            "count": len(today_blocks),
            "date": now.strftime("%Y-%m-%d"),
        }

    async def handle_fetch_request(self, event: Event) -> list[Event]:
        """Fetch schedule, then emit temporal/course/completion events."""
        if event.payload.get("source") != self.source_name:
            return []

        try:
            data = await self.fetch(event.payload)
            if data.get("error"):
                raise RuntimeError(data["error"])

            blocks_raw = data.get("blocks", [])
            produced: list[Event] = []

            for b_dict in blocks_raw:
                produced.append(Event(
                    event_type=EventType.TEMPORAL_BLOCK_ADDED,
                    aggregate_id=b_dict["block_id"],
                    aggregate_type=AggregateType.TEMPORAL,
                    causation_id=event.event_id,
                    payload=b_dict,
                    metadata={"source": self.source_name},
                ))

            unique_courses: dict[str, str] = {}
            for course_name in data.get("course_names", []):
                if course_name:
                    unique_courses[course_name] = ""
            for b_dict in blocks_raw:
                title = b_dict.get("title", "")
                if title:
                    unique_courses[title] = (b_dict.get("metadata") or {}).get("teacher", "")

            for course_name, teacher in unique_courses.items():
                produced.append(Event(
                    event_type=EventType.COURSE_ACTIVATED,
                    aggregate_id=course_name,
                    aggregate_type=AggregateType.COURSE,
                    causation_id=event.event_id,
                    payload={
                        "course_name": course_name,
                        "teacher": teacher,
                        "source": self.source_name,
                        "semester": "current",
                    },
                    metadata={"source": self.source_name},
                ))

            produced.append(Event(
                event_type=EventType.CONNECTOR_FETCH_COMPLETED,
                aggregate_id=event.aggregate_id,
                aggregate_type=AggregateType.SYSTEM,
                causation_id=event.event_id,
                payload={
                    "source": self.source_name,
                    "course_count": len(unique_courses),
                    "block_count": len(blocks_raw),
                    "raw_count": data.get("raw_count"),
                    "teaching_week": data.get("teaching_week"),
                    "intent": event.payload.get("intent", ""),
                },
                metadata={"source": self.source_name},
            ))

            return produced

        except Exception as exc:
            logger.exception("JWXT fetch failed")
            return [Event(
                event_type=EventType.CONNECTOR_FETCH_FAILED,
                aggregate_id=event.aggregate_id,
                aggregate_type=AggregateType.SYSTEM,
                causation_id=event.event_id,
                payload={"source": self.source_name, "error": str(exc)},
                metadata={"source": self.source_name},
            )]


def _make_block(
    day: datetime,
    start_h: int,
    start_m: int,
    end_h: int,
    end_m: int,
    title: str,
    location: str,
    block_type: TimeBlockType,
) -> TimeBlock:
    start = day.replace(hour=start_h, minute=start_m)
    end = day.replace(hour=end_h, minute=end_m)
    return TimeBlock(
        block_id=str(uuid4()),
        source=TemporalSource.JWXT,
        block_type=block_type,
        start=start,
        end=end,
        title=title,
        location=location,
    )


def _parse_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _teaching_week(today: date, semester_start: date | None) -> int | None:
    if semester_start is None:
        return None
    delta = (today - semester_start).days
    if delta < 0:
        return None
    return delta // 7 + 1


def _weekday_to_num(name: str) -> int:
    mapping = {
        "星期一": 1,
        "星期二": 2,
        "星期三": 3,
        "星期四": 4,
        "星期五": 5,
        "星期六": 6,
        "星期日": 7,
        "星期天": 7,
    }
    return mapping.get(name, 0)


def _jcs_to_time(jcs: str) -> tuple[str, str]:
    try:
        parts = jcs.split("-")
        start_p = int(parts[0])
        end_p = int(parts[1]) if len(parts) > 1 else start_p
        start_t = PERIOD_TIMES.get(start_p, ("00:00", "00:00"))
        end_t = PERIOD_TIMES.get(end_p, ("00:00", "00:00"))
        return start_t[0], end_t[1]
    except (ValueError, IndexError):
        return "00:00", "00:00"


def _combine_local(day: date, hhmm: str) -> datetime:
    hour, minute = [int(p) for p in hhmm.split(":", 1)]
    return datetime.combine(day, time(hour, minute), tzinfo=LOCAL_TZ)


def _week_matches(raw: str, week: int) -> bool:
    text = raw.strip()
    odd_only = "(单)" in text or "单周" in text
    even_only = "(双)" in text or "双周" in text
    if odd_only and week % 2 == 0:
        return False
    if even_only and week % 2 == 1:
        return False
    text = text.replace("(单)", "").replace("(双)", "").replace("单周", "").replace("双周", "")
    for part in re.split(r"[,;，；]", text):
        part = part.strip()
        if not part:
            continue
        single = re.match(r"^(\d+)\s*周?$", part)
        if single and int(single.group(1)) == week:
            return True
        span = re.match(r"^(\d+)\s*-\s*(\d+)\s*周?$", part)
        if span and int(span.group(1)) <= week <= int(span.group(2)):
            return True
    return False


def _infer_block_type(room: str, jcs: str) -> TimeBlockType:
    if any(marker in room for marker in ("实验", "实训", "机房", "工作室")):
        return TimeBlockType.CLASS_LAB
    try:
        parts = [int(p) for p in jcs.split("-") if p]
        if len(parts) == 2 and parts[1] - parts[0] >= 3:
            return TimeBlockType.CLASS_LAB
    except ValueError:
        pass
    return TimeBlockType.CLASS_LECTURE


def _dedupe_blocks(blocks: list[TimeBlock]) -> list[TimeBlock]:
    seen: set[tuple[str, str, str, str, str]] = set()
    result: list[TimeBlock] = []
    for block in blocks:
        key = (
            block.title,
            block.start.isoformat(),
            block.end.isoformat(),
            block.location,
            block.metadata.get("teacher", ""),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(block)
    return result
