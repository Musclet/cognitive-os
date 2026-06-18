"""JWXT connector.

Fetches ZFSoft/JWXT schedule data and normalizes it to TimeBlock events.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, time, timezone, timedelta
from pathlib import Path
from typing import Any, TYPE_CHECKING
from urllib.parse import urlparse
from uuid import NAMESPACE_URL, uuid4, uuid5

try:
    from playwright.async_api import async_playwright
except ImportError:
    async_playwright = None

if TYPE_CHECKING:
    from playwright.async_api import Browser, BrowserContext, Page
else:
    Browser = BrowserContext = Page = Any

from src.connector.base import Connector
from src.core.events import AggregateType, Event, EventType
from src.core.temporal import TemporalSource, TimeBlock, TimeBlockType
from src.domain.course_topology import normalize_course_name
from src.infrastructure.config import Settings

logger = logging.getLogger(__name__)

LOCAL_TZ = timezone(timedelta(hours=8))
SCHEDULE_PATH = "/kbcx/xskbcx_cxXskbcxIndex.html?gnmkdm=N2151&layout=default"
SCHEDULE_API_PATH = "/kbcx/xskbcx_cxXsgrkb.html?gnmkdm=N2151"

JWXT_ERROR_MESSAGES = {
    "jwxt_cookie_missing": "JWXT login cookies are not available.",
    "jwxt_cookie_expired": "JWXT login cookies have expired.",
    "jwxt_credentials_missing": "JWXT username or password is not configured.",
    "jwxt_login_failed": "JWXT automatic login failed.",
    "jwxt_auth_requires_user_action": "JWXT login requires user action.",
    "jwxt_captcha_required": "JWXT login requires captcha verification.",
    "jwxt_sso_required": "JWXT login requires SSO or QR-code authentication.",
    "jwxt_network_error": "JWXT network request failed.",
    "jwxt_parser_error": "JWXT schedule response could not be parsed.",
    "jwxt_invalid_credentials": "JWXT login failed: invalid username or password.",
    "jwxt_invalid_username": "JWXT login failed: username does not exist.",
    "jwxt_invalid_password": "JWXT login failed: incorrect password.",
    "jwxt_account_locked": "JWXT login failed: account is locked or disabled.",
    "jwxt_password_expired": "JWXT login failed: password has expired.",
    "jwxt_login_failed_unknown": "JWXT login failed: reason unknown.",
}


class JwxtSyncError(RuntimeError):
    """Structured, sanitized JWXT sync failure."""

    def __init__(self, error_code: str) -> None:
        self.error_code = error_code
        super().__init__(
            JWXT_ERROR_MESSAGES.get(
                error_code,
                JWXT_ERROR_MESSAGES["jwxt_login_failed"],
            )
        )

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
        self._last_auto_login_attempted = False

    async def authenticate(self) -> bool:
        self._authenticated = bool(
            self._use_mock
            or self._credentials_present()
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
        raw = await self._fetch_schedule_api()
        kb_list = raw.get("kbList", [])

        today = datetime.now(LOCAL_TZ).date()
        week_start = today - timedelta(days=today.weekday())
        window_days = max(1, int(getattr(self.settings, "jwxt_schedule_window_days", 14) or 14))
        semester_start = _parse_date(self.settings.jwxt_semester_start)
        current_week = _teaching_week(today, semester_start)

        block_dicts = self.parse_kb_list(kb_list)
        all_courses: set[str] = set()
        for b in block_dicts:
            t = b.get("title", "")
            if t:
                all_courses.add(t)

        return {
            "source": self.source_name,
            "blocks": block_dicts,
            "count": len(block_dicts),
            "course_names": sorted(all_courses),
            "raw_count": len(kb_list),
            "week_start": week_start.isoformat(),
            "window_days": window_days,
            "teaching_week": current_week,
        }

    async def _fetch_schedule_api(self) -> dict[str, Any]:
        """Use saved cookies first, then refresh them with local credentials."""
        cookie_present = Path(self.settings.jwxt_cookies_path).exists()
        credentials_present = self._credentials_present()
        self._last_auto_login_attempted = False
        self._log_auth_state(
            cookie_present=cookie_present,
            credentials_present=credentials_present,
            auto_login_attempted=False,
        )

        if cookie_present:
            try:
                return await self._fetch_schedule_api_httpx()
            except JwxtSyncError as exc:
                if exc.error_code != "jwxt_cookie_expired":
                    self._log_auth_state(
                        cookie_present=True,
                        credentials_present=credentials_present,
                        auto_login_attempted=False,
                        error_code=exc.error_code,
                    )
                    raise
        elif not credentials_present:
            error = JwxtSyncError("jwxt_credentials_missing")
            self._log_auth_state(
                cookie_present=False,
                credentials_present=False,
                auto_login_attempted=False,
                error_code=error.error_code,
            )
            raise error

        if not credentials_present:
            error = JwxtSyncError("jwxt_credentials_missing")
            self._log_auth_state(
                cookie_present=cookie_present,
                credentials_present=False,
                auto_login_attempted=False,
                error_code=error.error_code,
            )
            raise error

        self._last_auto_login_attempted = True
        try:
            await self._refresh_cookies_with_playwright()
        except JwxtSyncError as exc:
            self._log_auth_state(
                cookie_present=cookie_present,
                credentials_present=True,
                auto_login_attempted=True,
                error_code=exc.error_code,
            )
            raise
        except Exception:
            error = JwxtSyncError("jwxt_login_failed")
            self._log_auth_state(
                cookie_present=cookie_present,
                credentials_present=True,
                auto_login_attempted=True,
                error_code=error.error_code,
            )
            raise error from None

        try:
            return await self._fetch_schedule_api_httpx()
        except JwxtSyncError as exc:
            error = (
                JwxtSyncError("jwxt_login_failed")
                if exc.error_code in {"jwxt_cookie_missing", "jwxt_cookie_expired"}
                else exc
            )
            self._log_auth_state(
                cookie_present=Path(self.settings.jwxt_cookies_path).exists(),
                credentials_present=True,
                auto_login_attempted=True,
                error_code=error.error_code,
            )
            raise error from None

    async def _fetch_schedule_api_httpx(self) -> dict[str, Any]:
        """Fetch schedule via httpx using saved cookies."""
        import httpx

        cookie_path = Path(self.settings.jwxt_cookies_path)
        if not cookie_path.exists():
            raise JwxtSyncError("jwxt_cookie_missing")

        try:
            cookies_list = json.loads(cookie_path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            raise JwxtSyncError("jwxt_cookie_expired") from None
        if not isinstance(cookies_list, list) or not cookies_list:
            raise JwxtSyncError("jwxt_cookie_expired")

        cookies_jar: dict[str, str] = {}
        try:
            for item in cookies_list:
                if not isinstance(item, dict):
                    raise TypeError
                name = str(item["name"])
                value = str(item["value"])
                if name and value:
                    cookies_jar[name] = value
        except (KeyError, TypeError):
            raise JwxtSyncError("jwxt_cookie_expired") from None
        if not cookies_jar:
            raise JwxtSyncError("jwxt_cookie_expired")

        try:
            async with httpx.AsyncClient(
                cookies=cookies_jar,
                timeout=30,
                follow_redirects=False,
            ) as client:
                response = await client.post(
                    self._absolute_url(SCHEDULE_API_PATH),
                    data={
                        "xnm": self.settings.jwxt_schedule_year,
                        "xqm": self.settings.jwxt_schedule_semester,
                    },
                    headers={
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Referer": self._absolute_url(SCHEDULE_PATH),
                    },
                )
        except httpx.RequestError:
            raise JwxtSyncError("jwxt_network_error") from None
        return self._decode_schedule_response(response)

    def _decode_schedule_response(self, response: Any) -> dict[str, Any]:
        status_code = int(getattr(response, "status_code", 0) or 0)
        location = str(getattr(response, "headers", {}).get("location", ""))
        if 300 <= status_code < 400:
            if "login" in location.casefold() or not location:
                raise JwxtSyncError("jwxt_cookie_expired")
            raise JwxtSyncError("jwxt_network_error")
        if status_code < 200 or status_code >= 300:
            raise JwxtSyncError("jwxt_network_error")

        text = str(getattr(response, "text", "") or "")
        if self._looks_like_login_response(text):
            raise JwxtSyncError("jwxt_cookie_expired")
        try:
            data = response.json()
        except (ValueError, TypeError):
            raise JwxtSyncError("jwxt_parser_error") from None
        if not isinstance(data, dict) or not isinstance(data.get("kbList"), list):
            raise JwxtSyncError("jwxt_parser_error")
        return data

    async def _refresh_cookies_with_playwright(self) -> None:
        if async_playwright is None:
            raise JwxtSyncError("jwxt_login_failed")

        playwright = None
        browser: Browser | None = None
        try:
            try:
                playwright = await async_playwright().start()
                browser = await playwright.chromium.launch(
                    headless=self.settings.jwxt_headless,
                    args=["--disable-gpu", "--no-sandbox", "--disable-dev-shm-usage"],
                )
            except Exception:
                raise JwxtSyncError("jwxt_login_failed") from None

            context = await browser.new_context(
                viewport={"width": 1280, "height": 800},
                locale="zh-CN",
            )
            await self._load_cookies(context)
            page = await context.new_page()
            try:
                await page.goto(
                    self.settings.jwxt_login_url,
                    wait_until="domcontentloaded",
                    timeout=30000,
                )
            except Exception:
                raise JwxtSyncError("jwxt_network_error") from None

            if not await self._is_authenticated(page):
                await self._login(page)
                try:
                    await page.wait_for_load_state("networkidle", timeout=20000)
                except Exception:
                    pass
                try:
                    await page.goto(
                        self._absolute_url(SCHEDULE_PATH),
                        wait_until="domcontentloaded",
                        timeout=20000,
                    )
                except Exception:
                    raise JwxtSyncError("jwxt_network_error") from None

            if not await self._is_authenticated(page):
                raise JwxtSyncError(await self._auth_page_error_code(page))

            await self._save_cookies(context)
        finally:
            if browser is not None:
                try:
                    await browser.close()
                except Exception:
                    pass
            if playwright is not None:
                try:
                    await playwright.stop()
                except Exception:
                    pass

    async def _load_cookies(self, context: BrowserContext) -> bool:
        path = Path(self.settings.jwxt_cookies_path)
        if not path.exists():
            return False
        try:
            cookies = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(cookies, list) and cookies:
                await context.add_cookies(cookies)
                return True
        except Exception:
            return False
        return False

    async def _save_cookies(self, context: BrowserContext) -> None:
        path = Path(self.settings.jwxt_cookies_path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            cookies = await context.cookies()
            path.write_text(
                json.dumps(cookies, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            raise JwxtSyncError("jwxt_login_failed") from None

    async def _is_authenticated(self, page: Page) -> bool:
        url = str(getattr(page, "url", "") or "").casefold()
        if "index_initmenu" in url or "index_menu" in url or "/kbcx/" in url:
            return True
        if "login" in url:
            return False
        return not await self._has_login_form(page)

    async def _login(self, page: Page) -> None:
        if not self._credentials_present():
            raise JwxtSyncError("jwxt_credentials_missing")
        if await self._captcha_visible(page):
            raise JwxtSyncError("jwxt_captcha_required")
        if not await self._has_login_form(page):
            raise JwxtSyncError(await self._auth_page_error_code(page))
        try:
            await page.fill("#yhm", self.settings.jwxt_username)
            await page.fill("#mm", self.settings.jwxt_password)
            await page.click("#dl")
        except Exception:
            raise JwxtSyncError("jwxt_auth_requires_user_action") from None

    async def _has_login_form(self, page: Page) -> bool:
        return (
            await self._selector_visible(page, "#yhm")
            and await self._selector_visible(page, "#mm")
            and await self._selector_visible(page, "#dl")
        )

    async def _captcha_visible(self, page: Page) -> bool:
        selectors = (
            "#yzm",
            "#yzmPic",
            "input[name*='yzm' i]",
            "input[id*='captcha' i]",
            "[class*='captcha' i]",
            "[class*='slider' i]",
        )
        for selector in selectors:
            if await self._selector_visible(page, selector):
                return True
        return False

    async def _sso_visible(self, page: Page) -> bool:
        url = str(getattr(page, "url", "") or "").casefold()
        if any(marker in url for marker in ("sso", "cas/login", "oauth")):
            return True
        for selector in ("text=统一身份认证", "text=扫码登录", "text=二维码"):
            if await self._selector_visible(page, selector):
                return True
        return False

    async def _auth_page_error_code(self, page: Page) -> str:
        if await self._captcha_visible(page):
            return "jwxt_captcha_required"
        if not await self._has_login_form(page):
            if await self._sso_visible(page):
                return "jwxt_sso_required"
            return "jwxt_auth_requires_user_action"
        error_text = await self._extract_login_error_text(page)
        if error_text:
            classified = self._classify_login_error(error_text)
            if classified:
                return classified
        return "jwxt_login_failed_unknown"

    async def _extract_login_error_text(self, page: Page) -> str:
        """Safely extract visible error text from the login page after a failed attempt.

        Reads only visible text content from known error-display elements.
        Does NOT read form field values, hidden inputs, or full HTML.
        """
        selectors = (
            "#tips",
            "#errorMsg",
            "#error_msg",
            ".error_tip",
            ".errorTip",
            ".error",
            ".tip",
            ".tips",
            ".alert-error",
            ".alert_error",
            ".alert",
            "#msg",
            ".msg",
            ".message",
            "#message",
            ".form-error",
            "#formError",
            ".login_error",
            "#loginError",
        )
        for selector in selectors:
            try:
                el = page.locator(selector).first
                if await el.is_visible(timeout=500):
                    text = (await el.text_content() or "").strip()
                    if text:
                        return text
            except Exception:
                continue
        return ""

    @staticmethod
    def _classify_login_error(error_text: str) -> str:
        """Classify a login-page error message into a specific JWXT error code.

        Returns an empty string if the message cannot be reliably classified.
        Checks are ordered so that combined patterns (e.g. 用户名或密码)
        take precedence over single-factor patterns (e.g. 密码错误).
        """
        lowered = error_text.casefold()

        if any(
            marker in lowered
            for marker in ("用户名或密码", "账号或密码", "密码或用户名", "密码或账号")
        ):
            return "jwxt_invalid_credentials"

        if any(
            marker in lowered
            for marker in (
                "用户不存在", "账号不存在", "用户名不存在",
                "用户名错误", "用户名不正确", "账号错误", "账号不正确",
                "用户号不存在", "学号不存在", "学号错误", "学号不正确",
            )
        ):
            return "jwxt_invalid_username"

        if any(
            marker in lowered
            for marker in ("密码错误", "密码不正确", "密码有误", "密码不对")
        ):
            return "jwxt_invalid_password"

        if any(
            marker in lowered
            for marker in (
                "锁定", "禁用", "冻结", "停用", "已锁", "被锁",
            )
        ):
            return "jwxt_account_locked"

        if any(
            marker in lowered
            for marker in (
                "密码过期", "密码已过期", "密码失效", "密码已失效",
                "修改密码", "密码到期", "密码已到期", "密码超期",
            )
        ):
            return "jwxt_password_expired"

        if any(marker in lowered for marker in ("验证码",)):
            return "jwxt_auth_requires_user_action"

        return ""

    async def _selector_visible(self, page: Page, selector: str) -> bool:
        try:
            return bool(await page.locator(selector).first.is_visible(timeout=1000))
        except Exception:
            return False

    def _credentials_present(self) -> bool:
        return bool(
            str(self.settings.jwxt_username or "").strip()
            and str(self.settings.jwxt_password or "").strip()
        )

    def _log_auth_state(
        self,
        *,
        cookie_present: bool,
        credentials_present: bool,
        auto_login_attempted: bool,
        error_code: str = "",
    ) -> None:
        logger.info(
            "JWXT auth cookie_present=%s credentials_present=%s "
            "auto_login_attempted=%s error_code=%s",
            cookie_present,
            credentials_present,
            auto_login_attempted,
            error_code,
        )

    @staticmethod
    def _looks_like_login_response(text: str) -> bool:
        lowered = text.casefold()
        return (
            "login_slogin" in lowered
            or 'id="yhm"' in lowered
            or "id='yhm'" in lowered
        )

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

    def parse_kb_list(self, kb_list: list[dict]) -> list[dict]:
        """Parse raw JWXT kbList into TimeBlock dicts. Pure function, no I/O."""
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
        return [b.to_dict() for b in blocks]

    async def handle_fetch_request(self, event: Event) -> list[Event]:
        """Fetch schedule, then emit temporal/course/completion events."""
        if event.payload.get("source") != self.source_name:
            return []

        trace_id = str(event.event_id)
        started_event = Event(
            event_type=EventType.CONNECTOR_FETCH_STARTED,
            aggregate_id=event.aggregate_id,
            aggregate_type=AggregateType.SYSTEM,
            causation_id=event.event_id,
            payload={
                "source": self.source_name,
                "query": event.payload.get("query", ""),
                "intent": event.payload.get("intent", ""),
            },
            metadata={"source": self.source_name, "trace_id": trace_id},
        )
        try:
            data = await self.fetch(event.payload)
            if data.get("error"):
                raise RuntimeError(data["error"])

            blocks_raw = data.get("blocks", [])
            produced: list[Event] = [started_event]

            for b_dict in blocks_raw:
                produced.append(Event(
                    event_type=EventType.TEMPORAL_BLOCK_ADDED,
                    aggregate_id=b_dict["block_id"],
                    aggregate_type=AggregateType.TEMPORAL,
                    causation_id=event.event_id,
                    payload=b_dict,
                    metadata={"source": self.source_name, "trace_id": trace_id},
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
                    metadata={"source": self.source_name, "trace_id": trace_id},
                ))

            raw_count = data.get("raw_count")
            pulled_count = int(raw_count if raw_count is not None else len(blocks_raw))
            produced.append(Event(
                event_type=EventType.CONNECTOR_FETCH_COMPLETED,
                aggregate_id=event.aggregate_id,
                aggregate_type=AggregateType.SYSTEM,
                causation_id=event.event_id,
                payload={
                    "source": self.source_name,
                    "success": True,
                    "course_count": len(unique_courses),
                    "block_count": len(blocks_raw),
                    "temporal_blocks_count": len(blocks_raw),
                    "raw_count": raw_count,
                    "pulled_count": pulled_count,
                    "teaching_week": data.get("teaching_week"),
                    "intent": event.payload.get("intent", ""),
                    "auto_login_attempted": self._last_auto_login_attempted,
                    "last_sync_at": datetime.now(timezone.utc).isoformat(),
                },
                metadata={"source": self.source_name, "trace_id": trace_id},
            ))

            return produced

        except Exception as exc:
            error_code = _classify_jwxt_error(exc)
            message = JWXT_ERROR_MESSAGES[error_code]
            self._log_auth_state(
                cookie_present=Path(self.settings.jwxt_cookies_path).exists(),
                credentials_present=self._credentials_present(),
                auto_login_attempted=self._last_auto_login_attempted,
                error_code=error_code,
            )
            return [started_event, Event(
                event_type=EventType.CONNECTOR_FETCH_FAILED,
                aggregate_id=event.aggregate_id,
                aggregate_type=AggregateType.SYSTEM,
                causation_id=event.event_id,
                payload={
                    "source": self.source_name,
                    "success": False,
                    "error_code": error_code,
                    "error": message,
                    "message": message,
                    "pulled_count": 0,
                    "temporal_blocks_count": 0,
                    "auto_login_attempted": self._last_auto_login_attempted,
                    "last_sync_at": datetime.now(timezone.utc).isoformat(),
                },
                metadata={"source": self.source_name, "trace_id": trace_id},
            )]


def _classify_jwxt_error(exc: Exception) -> str:
    if isinstance(exc, JwxtSyncError):
        return exc.error_code
    if isinstance(exc, (json.JSONDecodeError, KeyError, TypeError, ValueError)):
        return "jwxt_parser_error"
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return "jwxt_network_error"
    return "jwxt_login_failed"


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
