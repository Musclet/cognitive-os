"""Telegram message router — raw message → Command → Event.

Stateless. Pure translation layer.
Includes art planning pattern detection.
"""

from __future__ import annotations

import re
from typing import Any

from src.core.events import Command, Event, EventType, AggregateType


COMMANDS: dict[str, str] = {
    # English
    "/sync_homework": "sync_homework",
    "/jwxt_sync": "jwxt_sync",
    "/sync_schedule": "sync_schedule",
    "/homework": "check_homework",
    "/schedule": "check_schedule",
    "/today": "show_today",
    "/free_today": "show_free_today",
    "/week_load": "show_week_load",
    "/state": "show_state",
    "/stress": "show_stress",
    "/capacity": "show_capacity",
    "/plan_today": "plan_today",
    "/plan_tomorrow": "plan_tomorrow",
    "/focus_window": "focus_window",
    "/done": "task_done",
    "/complete": "completion_prompt",
    "/skip": "task_skip",
    "/delay": "task_delay",
    "/behavior": "show_behavior",
    "/adaptive": "show_adaptive",
    "/patterns": "show_patterns",
    "/reflection": "show_reflection",
    "/trends": "show_trends",
    "/adaptation": "show_adaptation",
    "/propose": "request_proposals",
    "/drink": "drink",
    "/registry": "show_registry",
    "/rebuild": "rebuild_state",
    "/calendar_sync": "calendar_sync",
    "/calendar_today": "calendar_today",
    "/calendar_context": "calendar_context",
    "/checkin": "cognitive_checkin",
    "/nightly_review": "nightly_review",
    "/replan_today": "art_replan",
    "/sync_refresh": "sync_refresh",
    "/menu": "show_menu",
    "/ping": "ping",
    "/help": "help",
    "/start": "show_menu",
    # 中文
    "/同步作业": "sync_homework",
    "/同步教务": "jwxt_sync",
    "/同步课表": "sync_schedule",
    "/作业": "check_homework",
    "/课表": "check_schedule",
    "/今天": "show_today",
    "/今日空闲": "show_free_today",
    "/周负载": "show_week_load",
    "/状态": "show_state",
    "/压力": "show_stress",
    "/容量": "show_capacity",
    "/今日计划": "plan_today",
    "/明日计划": "plan_tomorrow",
    "/专注": "focus_window",
    "/完成": "task_done",
    "/记录完成": "completion_prompt",
    "/跳过": "task_skip",
    "/推迟": "task_delay",
    "/行为": "show_behavior",
    "/自适应": "show_adaptive",
    "/模式": "show_patterns",
    "/反思": "show_reflection",
    "/趋势": "show_trends",
    "/适应": "show_adaptation",
    "/建议": "request_proposals",
    "/饮水": "drink",
    "/课程": "show_registry",
    "/重算": "rebuild_state",
    "/日历同步": "calendar_sync",
    "/今日日历": "calendar_today",
    "/日历情境": "calendar_context",
    "/状态填报": "cognitive_checkin",
    "/今晚总结": "nightly_review",
    "/重排今天": "art_replan",
    "/没按计划": "art_replan_prompt",
    "/同步刷新数据": "sync_refresh",
    "/菜单": "show_menu",
    "/按钮": "show_menu",
    "/刷新按钮": "show_menu",
    "/帮助": "help",
    "/开始": "show_menu",
    # 主观输入
    "/mood": "record_mood",
    "/note": "record_note",
    "/context": "record_context",
    "/leave": "record_school_leave",
    "/情绪": "record_mood",
    "/记录": "record_note",
    "/情境": "record_context",
    "/请假": "record_school_leave",
    # Reply keyboard surface
    "今日状态": "show_today",
    "今日安排": "show_today",
    "刷新按钮": "show_menu",
    "今日课表": "check_schedule",
    "查课表": "check_schedule",
    "今日空闲": "show_free_today",
    "可用时间": "show_free_today",
    "作业列表": "check_homework",
    "记录完成": "completion_prompt",
    "完成记录": "completion_prompt",
    "我完成了": "completion_prompt",
    "课程范围": "show_registry",
    "状态差": "record_bad_state",
    "今天状态差": "record_bad_state",
    "今晚有安排": "evening_plan_options",
    "补水记录": "quick_hydration",
    "同步课表": "sync_schedule",
    "同步作业": "sync_homework",
    "同步任务": "legacy_sync_tasks",
    "同步日历": "calendar_sync",
    "同步刷新数据": "sync_refresh",
    "今日时间状态": "calendar_today",
    "状态重算": "rebuild_state",
    "认知学习": "cognitive_learning",
    "状态填报": "cognitive_checkin",
    "今晚总结": "nightly_review",
    "晚间总结": "nightly_review",
    "重排今天": "art_replan",
    "没按计划": "art_replan_prompt",
    "没照计划": "art_replan_prompt",
    "口述排期": "verbal_scheduling",
    "请假": "record_school_leave",
    # ── System operations ─────────────────────────────────────────
    "/selfcheck": "selfcheck",
    "/系统自检": "selfcheck",
    "系统自检": "selfcheck",
    # ── Finance / Money Reality ────────────────────────────────────────
    "本月资金": "finance_monthly",
    "出去玩额度": "finance_outing",
    "攒钱进度": "finance_savings",
    "要钱计划": "parent_fund_plan",
    "30天要钱排期": "parent_fund_30d_schedule",
    "要钱排期": "parent_fund_30d_schedule",
    "记一笔": "finance_help",
    "/selftest": "selftest",
    "/真实链路烟测": "selftest",
    "真实链路烟测": "selftest",
    "/storage_status": "storage_status",
    "/存储状态": "storage_status",
    "存储状态": "storage_status",
    "/storage_vacuum": "storage_vacuum",
    "/obsidian_status": "obsidian_status",
    "/Obsidian状态": "obsidian_status",
    "Obsidian状态": "obsidian_status",
}


_COGNITIVE_LEARNING_TRIGGERS = {"认知学习", "/认知学习"}
_VERBAL_SCHEDULING_TRIGGERS = {"口述排期", "/口述排期"}

# ── Date schedule query patterns ───────────────────────────────────────

_DATE_SCHEDULE_PREFIXES: list[tuple[str, str]] = [
    ("查课表", "query_schedule_date"),
    ("今日课表", "query_schedule_date"),
    ("今天课表", "query_schedule_date"),
    ("明天课表", "query_schedule_date"),
    ("明日课表", "query_schedule_date"),
    ("查看课表", "query_schedule_date"),
]

_DATE_KEYWORDS: dict[str, str] = {
    "今天": "today",
    "明日": "tomorrow",
    "明天": "tomorrow",
}

# ── Deviation / plan drift patterns ────────────────────────────────────────

_DEVIATION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"没画画(?:，|,|$)", re.IGNORECASE),
    re.compile(r"(?:计划崩了|计划乱了|今天废了|摆烂|不想动了)", re.IGNORECASE),
    re.compile(r"(?:健身|运动|锻炼)太累.*?(?:不想学|不学了|学不动)", re.IGNORECASE),
    re.compile(r"画了.*?但.*?(?:状态差|状态不好|画不动|没感觉|很差)", re.IGNORECASE),
    re.compile(r"(?:下午|晚上|今天).*(?:一直在写代码|敲代码|改bug|coding|vibecoding)", re.IGNORECASE),
    re.compile(r"(?:没照计划|没按计划|没完成|没做)", re.IGNORECASE),
]


def _is_deviation_input(text: str) -> bool:
    return any(p.search(text) for p in _DEVIATION_PATTERNS)


def _parse_date_schedule_input(text: str) -> str | None:
    """Parse date from natural Chinese input like '今日课表 2026-06-01' or '查课表 明天'.

    Returns ISO date string or None.
    """
    import re
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    # Pattern: ISO date after prefix
    iso_match = re.search(r"(\d{4}-\d{2}-\d{2})", text)
    if iso_match:
        return iso_match.group(1)

    # Pattern: relative keyword after "课表" or "查"
    for keyword, relative in _DATE_KEYWORDS.items():
        if keyword in text:
            now = datetime.now(ZoneInfo("Asia/Singapore"))
            if relative == "today":
                return now.strftime("%Y-%m-%d")
            elif relative == "tomorrow":
                return (now + timedelta(days=1)).strftime("%Y-%m-%d")

    return None


def _parse_verbal_scheduling_early(text: str, user_id: str) -> Command | None:
    """Detect clear verbal scheduling before art/insertion patterns.

    Trigger: explicit future day/date + time indicator + activity verb.
    This runs BEFORE _parse_art_message so that sentences like
    "明天中午十二点吃饭" route to verbal_scheduling instead of
    art_reality_insertion.

    Excludes patterns already handled by higher-priority routes
    (finance, leave, schedule query, completion).
    """
    text_stripped = text.strip()
    if not text_stripped or text_stripped.startswith("/"):
        return None

    # ── Must have a future-day reference ──────────────────────────────────
    has_future_keyword = any(kw in text_stripped for kw in _NL_FUTURE_DAY)
    has_weekday_future = bool(re.search(r"下[周星期]([一二三四五六日天])", text_stripped))
    has_specific_date = bool(_NL_SPECIFIC_DATE_PATTERN.search(text_stripped))

    if not (has_future_keyword or has_weekday_future or has_specific_date):
        return None

    # ── Must have time indicator AND activity verb ────────────────────────
    has_time = any(kw in text_stripped for kw in _NL_TIME_INDICATORS)
    has_activity = any(kw in text_stripped for kw in _NL_ACTIVITY_VERBS)
    if not (has_time and has_activity):
        return None

    # ── Exclude patterns handled by higher-priority routes ────────────────
    if "请假" in text_stripped or "课表" in text_stripped or "作业" in text_stripped:
        return None

    # Exclude finance patterns
    for kw in _FINANCE_INCOME_KEYWORDS | _FINANCE_PARENT_ACTUAL | _FINANCE_PARENT_PLAN:
        if kw in text_stripped:
            return None

    # Exclude completion patterns (prefix)
    if re.search(r"^(?:我)?(?:完成了|做完了|搞定了|弄完了|已完成)", text_stripped):
        return None

    return Command(
        command_type="verbal_scheduling",
        user_id=user_id,
        params={"raw_text": text_stripped},
        source="telegram",
    )


_UNDO_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^(?:撤回|撤销|撤销上一条|撤销刚才|取消|取消上一条)\s*$"),
    re.compile(r"^(?:刚才那个错了|刚才的不对|上一条错了|发错了)\s*$"),
    re.compile(r"^(?:撤回上一条|撤回刚才那个|撤销操作)\s*$"),
]


def _parse_undo_request(text: str, user_id: str) -> Command | None:
    """Detect undo/revoke requests from natural language."""
    text_stripped = text.strip()
    if not text_stripped or text_stripped.startswith("/"):
        return None
    for pattern in _UNDO_PATTERNS:
        if pattern.search(text_stripped):
            return Command(
                command_type="undo_last_action",
                user_id=user_id,
                params={"raw_text": text_stripped},
                source="telegram",
            )
    return None


def parse_message(text: str, user_id: int) -> Command | None:
    text = text.strip()

    # Exact match in COMMANDS dict
    if text in COMMANDS:
        command_type = COMMANDS[text]
        return Command(
            command_type=command_type,
            user_id=str(user_id),
            params={"raw_text": text},
            source="telegram",
        )

    # Date schedule query patterns (check before standard prefix to intercept
    # natural language date queries like "查课表 2026-06-01" or "今日课表 明天")
    for prefix, cmd_type in _DATE_SCHEDULE_PREFIXES:
        if text == prefix or text.startswith(prefix + " "):
            date_str = text[len(prefix):].strip()
            parsed = _parse_date_schedule_input(text)
            if parsed:
                return Command(
                    command_type=cmd_type,
                    user_id=str(user_id),
                    params={"raw_text": text, "date": parsed, "args": date_str},
                    source="telegram",
                )

    # Standard COMMANDS prefix match (e.g., "/课表 2026-06-01")
    for cmd_prefix, cmd_type in COMMANDS.items():
        if text.startswith(cmd_prefix + " "):
            return Command(
                command_type=cmd_type,
                user_id=str(user_id),
                params={"raw_text": text, "args": text[len(cmd_prefix):].strip()},
                source="telegram",
            )

    # Undo / revoke request — catch early before finance/art/verbal scheduling
    _undo_result = _parse_undo_request(text, str(user_id))
    if _undo_result is not None:
        return _undo_result

    # Finance inputs can look like generic "today inserted reality" text.
    # Route them before art/reality insertion so income is not misclassified.
    # Verbal scheduling (before finance — explicit future-day + time +
    # activity must route to verbal_scheduling before the general
    # transaction catch-all in finance mistaking "3点开会" for a transaction)
    vs_result = _parse_verbal_scheduling_early(text, str(user_id))
    if vs_result is not None:
        return vs_result

    # Finance batch intake detection (before ordinary finance — catches complex multi-fact text)
    if _is_finance_batch_intake(text):
        return Command(
            command_type="finance_batch_intake",
            user_id=str(user_id),
            params={"raw_text": text},
            source="telegram",
        )

    finance_result = _parse_finance_input(text, str(user_id))
    if finance_result is not None:
        return finance_result

    # Art pattern detection — "早安" / progress / insertion
    if _is_good_morning(text):
        params: dict[str, Any] = {"raw_text": text}
        # Check for combined greeting with extra content
        content = _strip_greeting_prefix(text)
        if content:
            params["morning_parsed"] = parse_morning_combined(text)
        return Command(
            command_type="art_plan_greeting",
            user_id=str(user_id),
            params=params,
            source="telegram",
        )

    progress_result = _parse_art_message(text)
    if progress_result is not None:
        return Command(
            command_type=progress_result["command_type"],
            user_id=str(user_id),
            params={"raw_text": text, **progress_result.get("params", {})},
            source="telegram",
        )

    completed_task = _parse_generic_completion(text)
    if completed_task:
        return Command(
            command_type="generic_completion",
            user_id=str(user_id),
            params={"raw_text": text, "task_text": completed_task, "args": completed_task},
            source="telegram",
        )

    # Deviation / plan drift detection
    if _is_deviation_input(text):
        return Command(
            command_type="plan_deviation",
            user_id=str(user_id),
            params={"raw_text": text, "deviation_text": text},
            source="telegram",
        )

    # Natural language intent parsing (fallback for unmatched NL input)
    nl_result = _parse_natural_intent(text, str(user_id))
    if nl_result is not None:
        return nl_result

    return None


# ── Natural language intent parser (fallback) ──────────────────────────────

# Leave intent keywords
_NL_LEAVE_KEYWORDS = {"请假"}

# Schedule query context
_NL_COURSE_KEYWORDS = {"课表", "课程", "上课", "有什么课"}

# Relative date offsets from today
_RELATIVE_DATE_OFFSETS: dict[str, int] = {
    "今天": 0,
    "今日": 0,
    "明天": 1,
    "明日": 1,
    "后天": 2,
}

# Chinese weekday → Python weekday (Monday=0)
_WEEKDAY_MAP: dict[str, int] = {
    "一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6,
}

# Homework query keywords
_NL_HOMEWORK_QUERY = {"还有什么", "还有哪些", "还有多少", "有哪些", "剩下", "查看", "看看", "列表"}

# Sync exact-match keywords
_NL_SYNC_EXACT = {"同步一下", "刷新数据", "更新数据", "刷新一下"}

# Verbal scheduling: time indicators + activity verbs
_NL_TIME_INDICATORS = {"点", "中午", "下午", "上午", "晚上", "早上", "时", "半"}
_NL_ACTIVITY_VERBS = {"吃饭", "去", "到", "开会", "上课", "见面", "看", "做", "参加", "约", "办", "买", "吃", "健身", "跑步", "运动", "游泳", "打球", "练", "喝", "玩"}
_NL_FUTURE_DAY = {"明天", "后天", "下周", "下个月"}

# Verbal scheduling: date pattern like "6月5日"
_NL_SPECIFIC_DATE_PATTERN = re.compile(r"\d{1,2}月\d{1,2}日")


def _resolve_relative_date(text: str) -> str | None:
    """Resolve Chinese relative date expressions to ISO date string.

    Handles: 今天, 明天, 后天, 下周三／下周一, 周三 alone.
    Returns ISO date string or None if nothing recognizable.
    """
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    now = datetime.now(ZoneInfo("Asia/Singapore"))
    today = now.date()

    # Direct offset keywords: 今天 / 明天 / 后天
    for kw, offset in _RELATIVE_DATE_OFFSETS.items():
        if kw in text:
            return (today + timedelta(days=offset)).isoformat()

    # Explicit ISO date in text
    m = re.search(r"(\d{4}-\d{2}-\d{2})", text)
    if m:
        return m.group(1)

    # "下周三" / "下周一" (next-week weekday)
    m = re.search(r"下[周星期]([一二三四五六日天])", text)
    if m:
        target = _WEEKDAY_MAP.get(m.group(1))
        if target is not None:
            days_ahead = target - today.weekday()
            if days_ahead <= 0:
                days_ahead += 7
            return (today + timedelta(days=days_ahead + 7)).isoformat()

    # Standalone "周三" (current-or-next-week weekday)
    m = re.search(r"(?<!下)[周星期]([一二三四五六日天])", text)
    if m:
        target = _WEEKDAY_MAP.get(m.group(1))
        if target is not None:
            days_ahead = target - today.weekday()
            if days_ahead < 0:
                days_ahead += 7
            return (today + timedelta(days=days_ahead)).isoformat()

    return None


def _parse_natural_intent(text: str, user_id: str) -> Command | None:
    """Fallback natural language intent parser.

    Runs after all other routing attempts fail.
    Covers: leave, schedule query, today plan, homework, sync, verbal scheduling.
    Priority: leave > schedule > plan > homework > sync > verbal.
    """
    text_stripped = text.strip()
    if not text_stripped or text_stripped.startswith("/"):
        return None

    # Priority 1: Leave — "我明天请假", "我要请假"
    if any(kw in text_stripped for kw in _NL_LEAVE_KEYWORDS):
        date_str = _resolve_relative_date(text_stripped) or ""
        args = text_stripped.replace("请假", "").strip() or "今日请假"
        return Command(
            command_type="record_school_leave",
            user_id=user_id,
            params={"raw_text": text_stripped, "date": date_str, "args": args},
            source="telegram",
        )

    # Priority 2: Schedule query — "查明天课表", "下周三课表", "看后天课程"
    has_course = any(kw in text_stripped for kw in _NL_COURSE_KEYWORDS)
    has_query = any(kw in text_stripped for kw in ["查", "看", "问", "什么时候", "几号"])
    has_date = any(kw in text_stripped for kw in list(_RELATIVE_DATE_OFFSETS) + list(_NL_FUTURE_DAY)) or \
        any(f"周{wd}" in text_stripped or f"星期{wd}" in text_stripped for wd in _WEEKDAY_MAP) or \
        any(f"下{pre}{wd}" in text_stripped for pre in ["周", "星期"] for wd in _WEEKDAY_MAP)

    if has_course and (has_query or has_date):
        date_str = _resolve_relative_date(text_stripped)
        if date_str:
            return Command(
                command_type="query_schedule_date",
                user_id=user_id,
                params={"raw_text": text_stripped, "date": date_str},
                source="telegram",
            )

    # Priority 3: Today's plan — "今天有什么安排", "今日安排"
    if ("今天" in text_stripped or "今日" in text_stripped) and "安排" in text_stripped:
        return Command(
            command_type="show_today",
            user_id=user_id,
            params={"raw_text": text_stripped},
            source="telegram",
        )

    # Priority 4: Homework query — "作业还有什么", "还有哪些作业"
    if "作业" in text_stripped and any(w in text_stripped for w in _NL_HOMEWORK_QUERY):
        return Command(
            command_type="check_homework",
            user_id=user_id,
            params={"raw_text": text_stripped},
            source="telegram",
        )

    # Priority 5: Sync — "同步一下", "刷新数据"
    if text_stripped in _NL_SYNC_EXACT:
        return Command(
            command_type="sync_refresh",
            user_id=user_id,
            params={"raw_text": text_stripped},
            source="telegram",
        )

    # Priority 6: Verbal scheduling — "明天中午十二点吃饭"
    has_time = any(kw in text_stripped for kw in _NL_TIME_INDICATORS)
    has_activity = any(kw in text_stripped for kw in _NL_ACTIVITY_VERBS)
    has_future = any(kw in text_stripped for kw in _NL_FUTURE_DAY)

    if (has_future or has_time) and has_activity:
        # Avoid catching patterns already handled by higher-priority routes
        if "请假" not in text_stripped and "课表" not in text_stripped and "作业" not in text_stripped:
            return Command(
                command_type="verbal_scheduling",
                user_id=user_id,
                params={"raw_text": text_stripped},
                source="telegram",
            )

    return None


# ── Art pattern helpers ────────────────────────────────────────────────────

_GOOD_MORNING_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^早安\s*$"),
    re.compile(r"^早\s*[~～]?$"),
    re.compile(r"^早上好\s*$"),
]

# ── Combined morning greeting parser ────────────────────────────────────────

_GOOD_MORNING_COMBINED_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^早安[\s,，:：]+(.+)"),
    re.compile(r"^早[\s~～]*[\s,，:：]+(.+)"),
    re.compile(r"^早上好[\s,，:：]+(.+)"),
]

# Mood keyword → score mappings
_MOOD_KEYWORDS: dict[str, int] = {
    "心情好": 7, "心情不错": 7, "状态好": 7, "状态不错": 7,
    "心情很好": 8, "心情非常好": 9, "状态很好": 8, "状态非常好": 9,
    "心情一般": 5, "状态一般": 5,
    "心情差": 3, "心情不好": 3, "状态差": 3, "状态不好": 3,
    "心情很差": 2, "状态很差": 2, "心情极差": 1, "状态极差": 1,
}

_MOOD_NUM_PATTERN = re.compile(r"心情(\d+)")
_MOOD_KEYWORD_PATTERN = re.compile("|".join(re.escape(k) for k in _MOOD_KEYWORDS))

# Arrangement/time periods for extraction
_TIME_PERIODS = ["上午", "下午", "晚上", "中午", "今天", "明天"]

# Art target pattern
_ART_TARGET_PATTERN = re.compile(r"(?:画画|绘画)\s*(\d+(?:\.\d+)?)\s*(?:小时|h)", re.IGNORECASE)


def parse_morning_combined(text: str) -> dict:
    """Parse combined morning greeting with mood, arrangements, and art targets.

    Returns dict with keys:
      - mood_score: int or None (1-10)
      - arrangements: list of activity text strings (e.g. "下午三点健身")
      - art_minutes: int or None
      - arrangement_text: raw trailing text for context storage
    """
    result: dict = {"mood_score": None, "arrangements": [], "art_minutes": None, "arrangement_text": ""}

    # Strip greeting prefix first so "早安"/"早上好" never leaks into content
    remaining = _strip_greeting_prefix(text)
    if not remaining:
        return result

    # 1) Extract mood from number pattern first (most specific)
    mood_num_match = _MOOD_NUM_PATTERN.search(remaining)
    if mood_num_match:
        score = int(mood_num_match.group(1))
        if 1 <= score <= 10:
            result["mood_score"] = score
        remaining = _MOOD_NUM_PATTERN.sub("", remaining, count=1)

    # 2) Extract mood from keyword pattern
    if result["mood_score"] is None:
        mood_kw_match = _MOOD_KEYWORD_PATTERN.search(remaining)
        if mood_kw_match:
            result["mood_score"] = _MOOD_KEYWORDS[mood_kw_match.group(0)]
            remaining = _MOOD_KEYWORD_PATTERN.sub("", remaining, count=1)

    # Save text before art removal for arrangement extraction, so that
    # e.g. "下午画画2h" is preserved intact instead of fragmented into "下午"
    pre_art_remaining = remaining

    # 3) Extract art target
    art_match = _ART_TARGET_PATTERN.search(remaining)
    if art_match:
        result["art_minutes"] = int(float(art_match.group(1)) * 60)
        remaining = _ART_TARGET_PATTERN.sub("", remaining, count=1)

    # 4) Extract time-arrangement segments from pre-art text
    arrangement_source = pre_art_remaining.strip()
    # Strip "今天安排：" prefix which is not an arrangement itself
    arrangement_source = re.sub(r"今天安排[：:]", "", arrangement_source)
    # Split by Chinese/English comma, semicolon, OR whitespace
    segments = re.split(r"[，,、;；\s]+", arrangement_source)
    arrangements = []
    for seg in segments:
        seg = seg.strip()
        if not seg:
            continue
        # Normalize: remove internal whitespace
        cleaned = re.sub(r"\s+", "", seg)
        if not cleaned:
            continue

        # Strip leading filler words that are not part of the activity itself
        cleaned = re.sub(r"^(?:但想|想要|打算|准备|就是|只是)\s*", "", cleaned)
        if not cleaned:
            continue

        # Filter out pure noise / filler words
        noise_set = {"早安", "早上好", "但想", "安排"}
        if cleaned in noise_set:
            continue
        if len(cleaned) <= 1:
            continue
        # Filter out bare time-period words standing alone
        if cleaned in _TIME_PERIODS:
            continue

        # Keep segments that reference a time period or a known activity keyword
        has_period = any(p in cleaned for p in _TIME_PERIODS)
        has_activity = any(
            w in cleaned
            for w in ("上课", "健身", "画画", "吃饭", "学习", "色彩", "休息", "绘画", "练习")
        )
        if has_period or has_activity:
            arrangements.append(cleaned)
        elif len(cleaned) >= 2:
            # Activity text without explicit period keyword — keep if non-trivial
            arrangements.append(cleaned)

    result["arrangements"] = arrangements
    result["arrangement_text"] = remaining.strip()

    return result


def _is_good_morning(text: str) -> bool:
    # Pure greeting first
    for p in _GOOD_MORNING_PATTERNS:
        if p.match(text.strip()):
            return True
    # Combined greeting with trailing content
    full_text = text.strip()
    # Check combined patterns — only match when there IS trailing content
    for p in _GOOD_MORNING_COMBINED_PATTERNS:
        m = p.match(full_text)
        if m:
            return True
    return False


def _strip_greeting_prefix(text: str) -> str:
    """Strip the greeting prefix and return the remaining content.
    Returns empty string if it's a pure greeting with no extra content.
    """
    stripped = text.strip()
    # Check combined patterns first (has trailing content)
    for p in _GOOD_MORNING_COMBINED_PATTERNS:
        m = p.match(stripped)
        if m:
            return m.group(1).strip()
    # Pure greeting → no extra content
    return ""


_PROGRESS_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # "完成 画画 2小时" — art with duration parseable by domain handler
    (re.compile(r"完成(?:了)?\s*(画画|绘画|art).*?(\d+(?:\.\d+)?)\s*(?:小时|h|hr)", re.IGNORECASE), "art_progress"),
    # "完成了画画" — art but caught by generic completion for structured parse (art handler too strict)
    (re.compile(r"完成(?:了)?\s*(画画|绘画|art)\s*$", re.IGNORECASE), "art_progress"),
    (re.compile(r"(画不动|画累了|不想画|画不下去)", re.IGNORECASE), "art_progress"),
    (re.compile(r"(画画|绘画).*?(\d+)\s*%", re.IGNORECASE), "art_progress"),
    (re.compile(r"跳过.*?(画画|绘画|作品|人体|临摹)", re.IGNORECASE), "art_progress"),
    (re.compile(r"完成.*健身.*(\d+(?:\.\d+)?)\s*(小时|分钟|h|min)", re.IGNORECASE), "fitness_progress"),
]

_INSERTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(下午|上午|晚上|中午|今天).*?(去|到|去办|吃|要)", re.IGNORECASE),
    re.compile(r"(临时|突然).*?(\d+)\s*(小时|分钟)", re.IGNORECASE),
    re.compile(r"(\d+)[:：点].*?(到|至)[\s]*(\d+)[:：点]", re.IGNORECASE),
    re.compile(r"(出门|外出|去一趟|跑一趟)", re.IGNORECASE),
    re.compile(r"(\d+)[:：点].*?(吃饭|办卡|开会|面试|看牙|体检|上课|约)", re.IGNORECASE),
]


def _parse_art_message(text: str) -> dict | None:
    """Detect art progress, insertion, or fitness messages.

    Returns dict with command_type and optional params, or None.
    """
    text_stripped = text.strip()

    # Check progress patterns first
    for pattern, cmd_type in _PROGRESS_PATTERNS:
        if pattern.search(text_stripped):
            return {"command_type": cmd_type}

    # Check insertion patterns
    for pattern in _INSERTION_PATTERNS:
        if pattern.search(text_stripped):
            return {"command_type": "art_reality_insertion"}

    return None


# ── Duration units for parsing ────────────────────────────────────────────

_DURATION_PATTERN = re.compile(
    r"(\d+(?:\.\d+)?)\s*(h|hr|hour|hours|小时|分钟|min|mins|分鐘|小時|hrs)",
    re.IGNORECASE,
)

_ART_KEYWORDS_PATTERN = re.compile(
    r"(画画|绘画|人体|速写|临摹|素描|水彩|油画|插画|art)",
    re.IGNORECASE,
)


def _extract_duration(text: str) -> tuple[float | None, str]:
    """Extract duration in minutes from text.

    Returns (duration_minutes, text_with_duration_removed).
    """
    m = _DURATION_PATTERN.search(text)
    if not m:
        return None, text

    val = float(m.group(1))
    unit = m.group(2).lower()
    if unit in ("h", "hr", "hour", "hours", "小时", "小時", "hrs"):
        minutes = val * 60
    else:
        minutes = val

    # Remove the duration token from text
    cleaned = text[:m.start()] + text[m.end():]
    return minutes, cleaned.strip()


def _is_art_completion(text: str) -> bool:
    """Check if text describes an art-related completion."""
    return bool(_ART_KEYWORDS_PATTERN.search(text))


def parse_completion_detail(text: str) -> dict | None:
    """Parse a natural completion message into structured parts.

    Handles formats:
      - 完成了0.5h的画画，色彩
      - 完成了 0.5h 画画 色彩
      - 画画0.5小时 色彩完成
      - 完成了30分钟英语听力
      - 做完了 数据结构作业

    Returns dict with keys: task, duration_min, focus, is_art, raw_text
    or None if nothing parseable.
    """
    text = text.strip()
    if not text:
        return None

    # Remove common completion prefixes
    clean = re.sub(
        r"^(?:我)?(?:完成了|做完了|弄完了|搞定了|已完成|做完|完成)\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )

    # If nothing remains after stripping prefix, try suffix pattern
    if not clean:
        suffix_match = re.search(r"^(.+?)完成$", text)
        if suffix_match:
            clean = suffix_match.group(1).strip(" ：:，,。")
        else:
            return None

    # Handle trailing "完成" that wasn't caught by prefix
    clean = re.sub(r"\s*完成$", "", clean, flags=re.IGNORECASE)

    # Extract duration
    duration_min, remaining = _extract_duration(clean)
    remaining = remaining.strip(" ：:，,。")

    # Split on Chinese/English comma (strongest separator) to isolate focus
    focus = None
    task_text = remaining
    for comma_pat in [r"[，,]", r"\s+的\s+"]:
        parts = re.split(comma_pat, task_text, maxsplit=1)
        if len(parts) > 1 and parts[1].strip():
            task_text = parts[0].strip()
            focus = parts[1].strip()
            break

    # Strip leading "的" from task (artifact of "duration的任务" pattern)
    task_text = re.sub(r"^的\s*", "", task_text).strip()

    # If no comma/focus was split, try space-delimited heuristic:
    # if there are 2+ words and no focus yet, last word may be focus
    # Only when task_text looks like "word1 word2" (not a phrase)
    if focus is None:
        tokens = task_text.split()
        if len(tokens) >= 2:
            # Last space-separated token is likely focus detail
            task_text = " ".join(tokens[:-1])
            focus = tokens[-1]

    # Clean up whitespace
    task_text = re.sub(r"\s+", " ", task_text).strip() if task_text else ""
    if focus:
        focus = re.sub(r"\s+", " ", focus).strip()

    is_art = _is_art_completion(text)

    result = {
        "task": task_text if task_text else None,
        "duration_min": duration_min,
        "focus": focus,
        "is_art": is_art,
        "raw_text": text,
    }
    return result


def _parse_generic_completion(text: str) -> str | None:
    """Parse natural completion messages that are not handled by domain parsers.

    Returns task text string or None.
    """
    text_stripped = text.strip()
    patterns = [
        r"^(?:我)?完成了\s*(.+)$",
        r"^(?:我)?做完了\s*(.+)$",
        r"^(?:我)?弄完了\s*(.+)$",
        r"^(?:我)?搞定了\s*(.+)$",
        r"^已完成\s*(.+)$",
    ]
    for pattern in patterns:
        match = re.match(pattern, text_stripped, re.IGNORECASE)
        if match:
            task = match.group(1).strip(" ：:，,。")
            return task or None

    # Suffix pattern: "X完成", "X做完了"
    suffix_patterns = [
        r"^(.+?)(?:做完了|完成了|搞定了|弄完了)\s*$",
        r"^(.+?)(?:做完|完成)\s*$",
    ]
    for pattern in suffix_patterns:
        match = re.match(pattern, text_stripped, re.IGNORECASE)
        if match:
            task = match.group(1).strip(" ：:，,。")
            if task:
                return task or None

    return None


# ── Finance / Money Reality detection ─────────────────────────────────────

_FINANCE_INCOME_KEYWORDS = {"生活费到账", "生活费", "到账", "发生活费", "收入"}
_FINANCE_PARENT_ACTUAL = {"找爸爸要了", "跟爸爸要了", "找妈妈要了", "跟妈妈要了", "今天要了", "爸爸给了", "妈妈给了"}
_FINANCE_PARENT_PLAN = {
    "想找爸爸", "想找妈妈", "什么时候要", "要钱计划", "下次提醒我",
    "想跟爸爸", "想跟妈妈", "叫爸爸", "叫妈妈", "问爸爸", "问妈妈",
    "找爸爸", "找妈妈", "跟爸爸", "跟妈妈",
}

_BATCH_SENTENCE_SEP = re.compile(r"[。！？\n;；]+")
_BATCH_TRIGGER_KEYWORDS = {"报销", "借给", "欠款"}
_BATCH_PARENT_LANG = {"妈妈", "爸爸", "要钱", "要了"}
_BATCH_PARTNER_LANG = {"对象", "报销"}


def _is_finance_batch_intake(text: str) -> bool:
    """Detect complex multi-fact finance input that needs batch intake flow.

    Returns True when text:
    - Has multiple Chinese sentences (3+)
    - Contains batch trigger keywords (报销, 借给, 欠款)
    - Has both partner language AND parent language
    """
    # Skip known single-intent commands
    if text.startswith("/"):
        return False

    parts = _BATCH_SENTENCE_SEP.split(text)
    parts = [p.strip() for p in parts if p.strip()]

    # Multiple sentences
    if len(parts) >= 4:
        return True

    # Has batch trigger keywords
    for kw in _BATCH_TRIGGER_KEYWORDS:
        if kw in text:
            return True

    # Has recurring interval language
    if re.search(r"每\s*\d+\s*天", text):
        return True

    # Mix of partner and parent language
    has_partner = any(kw in text for kw in _BATCH_PARTNER_LANG)
    has_parent = any(kw in text for kw in _BATCH_PARENT_LANG)
    if has_partner and has_parent:
        return True

    # "还没要" / "什么时候要" in combination with other finance facts
    if ("还没要" in text or "什么时候要" in text) and len(parts) >= 2:
        return True

    return False


def _parse_finance_input(text: str, user_id: str = "0") -> Command | None:
    """Detect finance/parent-fund natural language input.

    Returns a Command with command_type='finance_transaction' for
    transaction/income/parent-fund input, which the domain handler
    will further classify.
    """
    text_stripped = text.strip()
    if not text_stripped or text_stripped.startswith("/"):
        return None

    # Check income keywords
    for kw in _FINANCE_INCOME_KEYWORDS:
        if kw in text_stripped:
            return Command(
                command_type="finance_transaction",
                user_id=user_id,
                params={"raw_text": text_stripped},
                source="telegram",
            )

    # Check parent actual request keywords
    for kw in _FINANCE_PARENT_ACTUAL:
        if kw in text_stripped:
            return Command(
                command_type="finance_transaction",
                user_id=user_id,
                params={"raw_text": text_stripped},
                source="telegram",
            )

    # Check parent planning keywords
    for kw in _FINANCE_PARENT_PLAN:
        if kw in text_stripped:
            return Command(
                command_type="finance_transaction",
                user_id=user_id,
                params={"raw_text": text_stripped},
                source="telegram",
            )

    # General transaction: text contains a number and not obviously art/deviation
    if re.search(r"\d+", text_stripped):
        # Skip art patterns and completion patterns
        skip_patterns = [
            r"早安", r"早[\s~～]", r"完成", r"做完了", r"搞定了", r"画了", r"跳过",
            r"健身", r"运动", r"饮水",
        ]
        for pat in skip_patterns:
            if re.search(pat, text_stripped):
                return None

        # Has a positive number that looks like an amount
        m = re.search(r"(\d+(?:\.\d+)?)", text_stripped)
        if m and float(m.group(1)) > 0:
            return Command(
                command_type="finance_transaction",
                user_id=user_id,
                params={"raw_text": text_stripped},
                source="telegram",
            )

    return None


def command_to_event(command: Command) -> Event:
    return Event(
        event_type=EventType.USER_COMMAND_RECEIVED,
        aggregate_id=command.user_id,
        aggregate_type=AggregateType.USER,
        payload={
            "command": command.command_type,
            "params": command.params,
        },
        metadata={"source": command.source},
    )
