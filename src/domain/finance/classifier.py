"""Transaction classifier — keyword-based category detection.

Deterministic, no LLM. Categories:
- outing/date
- necessary
- art_learning_investment
- fitness_health
- system_subscription
- emotional
- other
"""

from __future__ import annotations

# ── Category keyword groups ──────────────────────────────────────────────────

_OUTING_KEYWORDS = {"对象", "约会", "出去玩", "电影", "逛街", "情侣", "和对象"}
_NECESSARY_KEYWORDS = {"食堂", "饭", "交通", "地铁", "日用品", "菜", "水果", "午餐", "晚餐", "早饭", "早餐", "午饭", "晚饭",
                       "公交", "打车", "通勤"}
_ART_LEARNING_KEYWORDS = {"画材", "课程", "书", "软件", "教程", "颜料", "笔", "画布", "素描纸", "水彩纸", "教材", "brush"}
_FITNESS_HEALTH_KEYWORDS = {"健身", "蛋白粉", "运动", "药", "祛痘", "维生素", "鱼油", "膏药", "创可贴"}
_SYSTEM_SUBSCRIPTION_KEYWORDS = {"订阅", "API", "工具", "服务器", "域名", "云", "vpn", "加速器"}
_EMOTIONAL_KEYWORDS = {"奶茶", "零食", "冲动", "奖励自己", "深夜", "饮料", "可乐", "薯片", "甜品", "蛋糕", "冰淇淋",
                       "咖啡"}

CATEGORY_LABELS = {
    "outing": "约会/出去玩",
    "necessary": "必要开销",
    "art_learning_investment": "学习投资",
    "fitness_health": "健康/健身",
    "system_subscription": "订阅/工具",
    "emotional": "情绪消费",
    "other": "其他",
}

CATEGORY_KEYWORD_MAP: list[tuple[str, set[str]]] = [
    ("outing", _OUTING_KEYWORDS),
    ("necessary", _NECESSARY_KEYWORDS),
    ("art_learning_investment", _ART_LEARNING_KEYWORDS),
    ("fitness_health", _FITNESS_HEALTH_KEYWORDS),
    ("system_subscription", _SYSTEM_SUBSCRIPTION_KEYWORDS),
    ("emotional", _EMOTIONAL_KEYWORDS),
]


def classify_transaction(text: str) -> str:
    """Classify a transaction description into a category string.

    Returns one of: outing, necessary, art_learning_investment,
    fitness_health, system_subscription, emotional, other.
    """
    for category, keywords in CATEGORY_KEYWORD_MAP:
        for kw in keywords:
            if kw in text:
                return category
    return "other"
