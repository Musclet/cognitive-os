"""Batch parser — deterministic extraction of multiple finance facts from one text.

No LLM import. Pure regex + rule-based.
Outputs a draft dict with items, questions, and summary counts.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone, timedelta
from typing import Any
from uuid import uuid4

# ── Reusable patterns ────────────────────────────────────────────────────────

_EXPRESSION = re.compile(r"(\d+)\s*\+\s*(\d+)")
_ANY_AMOUNT = re.compile(r"(\d+(?:\.\d+)?)")

# ── Sentence-level detection patterns ─────────────────────────────────────────

# "报销百分之40" / "报销50%"
_REIMBURSE_PERCENT = re.compile(r"报销\s*(?:百分之|%)\s*(\d+)")

# "报销1.7元" / "报销50"
_REIMBURSE_AMOUNT = re.compile(r"报销\s*(\d+(?:\.\d+)?)\s*(?:元|块钱|块)?")

# "5月3号借给了对象500元"
_PARTNER_DEBT = re.compile(
    r"(\d{1,2})\s*月\s*(\d{1,2})\s*[号日]?.*?借给(?:了)?.*?对象.*?(\d+(?:\.\d+)?)"
)

# "妈妈这个月会给300，每9天要一次"
_PARENT_RULE = re.compile(r"(妈妈|爸爸).*?每\s*(\d+)\s*天")

# "今天已经找妈妈要了100了" / "找爸爸要了150"
_PARENT_REQUEST_RECORDED = re.compile(
    r"(?:找|跟|叫)?(妈妈|爸爸).*?要了.*?(\d+(?:\.\d+)?)"
)

# "买画材的钱100还没要"
_PARENT_PLAN_LONG = re.compile(r"买(.+?)的钱\s*(\d+(?:\.\d+)?)")

# "洗面奶钱60，面膜钱70，鞋子钱200" — match ALL occurrences
_PARENT_PLAN_SHORT = re.compile(r"(.+?)钱\s*(\d+(?:\.\d+)?)")

# ── Batch detection (used by router) ─────────────────────────────────────────

_BATCH_SENTENCE_SEPARATORS = re.compile(r"[。！？\n;；]+")

_BATCH_TRIGGER_KEYWORDS = {"报销", "借给", "欠款"}
_BATCH_PARTNER_LANG = {"对象", "报销", "借给"}
_BATCH_PARENT_LANG = {"妈妈", "爸爸", "要钱", "要了"}


def is_batch_intake(text: str) -> bool:
    """Return True if text looks like a multi-fact finance batch input."""
    parts = _BATCH_SENTENCE_SEPARATORS.split(text)
    parts = [p.strip() for p in parts if p.strip()]
    if len(parts) >= 4:
        return True
    for kw in _BATCH_TRIGGER_KEYWORDS:
        if kw in text:
            return True
    if re.search(r"每\s*\d+\s*天", text):
        return True
    has_partner = any(kw in text for kw in _BATCH_PARTNER_LANG)
    has_parent = any(kw in text for kw in _BATCH_PARENT_LANG)
    if has_partner and has_parent:
        return True
    if ("还没要" in text or "什么时候要" in text) and len(parts) >= 2:
        return True
    return False


# ── Parse helpers ─────────────────────────────────────────────────────────────

def _resolve_date_md(month: int, day: int, now: datetime) -> str:
    """Resolve M/D to ISO date string relative to now."""
    year = now.year
    candidate = datetime(year, month, day, tzinfo=now.tzinfo)
    if candidate > now:
        return candidate.isoformat()
    return datetime(year, month, day, tzinfo=now.tzinfo).isoformat()


def _evaluate_expression(text: str) -> float | None:
    """Evaluate simple expression like '155+15', return 170.0 or None."""
    m = _EXPRESSION.search(text)
    if m:
        return float(int(m.group(1)) + int(m.group(2)))
    return None


def _first_amount(text: str) -> float | None:
    """Return the first number found in text."""
    m = _ANY_AMOUNT.search(text)
    return float(m.group(1)) if m else None


def _parse_sentence(sentence: str, now: datetime) -> list[dict[str, Any]]:
    """Parse one sentence into zero or more item dicts.

    Returns a list (may be empty). A sentence may produce multiple items
    when it contains multiple short-form planned requests etc.
    """
    text = sentence.strip()
    if not text:
        return []

    # ── Pre-check: evaluate expression for gross amount ──────────────
    expr_val = _evaluate_expression(text)
    effective_gross = expr_val if expr_val is not None else _first_amount(text)

    # ── 1. Partner debt ──────────────────────────────────────────────
    m = _PARTNER_DEBT.search(text)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        amount = float(m.group(3))
        date_iso = _resolve_date_md(month, day, now)
        return [{
            "type": "partner_debt_created",
            "amount": amount,
            "date": date_iso,
            "counterparty": "对象",
            "raw": text,
        }]

    # ── 2. Parent fund rule ──────────────────────────────────────────
    m = _PARENT_RULE.search(text)
    if m:
        person = m.group(1)
        interval_days = int(m.group(2))
        amount = effective_gross or 0.0
        items = [{
            "type": "parent_fund_rule_configured",
            "person": person,
            "amount": amount,
            "interval_days": interval_days,
            "raw": text,
        }]
        recorded = _PARENT_REQUEST_RECORDED.search(text)
        if recorded:
            items.append({
                "type": "parent_fund_request_recorded",
                "person": recorded.group(1),
                "amount": float(recorded.group(2)),
                "date": now.isoformat(),
                "raw": text,
            })
        return items

    # ── 3. Parent fund request recorded ──────────────────────────────
    m = _PARENT_REQUEST_RECORDED.search(text)
    if m:
        person = m.group(1)
        amount = float(m.group(2))
        return [{
            "type": "parent_fund_request_recorded",
            "person": person,
            "amount": amount,
            "date": now.isoformat(),
            "raw": text,
        }]

    # ── 4. Parent fund planned — long form ───────────────────────────
    m = _PARENT_PLAN_LONG.search(text)
    if m:
        item_name = m.group(1).strip()
        amount = float(m.group(2))
        return [{
            "type": "parent_fund_request_planned",
            "description": item_name,
            "amount": amount,
            "raw": text,
        }]

    # ── 5. Parent fund planned — short form (multiple per sentence) ──
    short_matches = list(_PARENT_PLAN_SHORT.finditer(text))
    if short_matches:
        items = []
        for m in short_matches:
            item_name = m.group(1).strip().rstrip("，,、，")
            # Strip leading punctuation/spaces
            item_name = re.sub(r"^[，,、。.\s]+", "", item_name).strip()
            if not item_name:
                continue
            amount = float(m.group(2))
            items.append({
                "type": "parent_fund_request_planned",
                "description": item_name,
                "amount": amount,
                "raw": text,
            })
        if items:
            return items

    # ── 6. Reimbursement with percent ────────────────────────────────
    m_pct = _REIMBURSE_PERCENT.search(text)
    if m_pct:
        pct = int(m_pct.group(1))
        gross = effective_gross or 0.0
        reimbursed = round(gross * pct / 100.0, 2)
        net = round(gross - reimbursed, 2)
        return [{
            "type": "reimbursement",
            "gross_amount": gross,
            "reimbursed_amount": reimbursed,
            "net_amount": net,
            "percent": pct,
            "raw": text,
        }]

    # ── 7. Reimbursement with fixed amount ───────────────────────────
    m_amt = _REIMBURSE_AMOUNT.search(text)
    if m_amt:
        reimbursed = float(m_amt.group(1))
        gross = effective_gross or reimbursed
        net = round(gross - reimbursed, 2)
        item: dict[str, Any] = {
            "type": "reimbursement",
            "gross_amount": gross,
            "reimbursed_amount": reimbursed,
            "net_amount": net,
            "raw": text,
        }
        if reimbursed <= 5 and gross >= 100:
            item["_question"] = (
                f"报销金额 {reimbursed} 元是否正确？"
                f"可能是 {gross * 0.1:.0f} 或 {gross * 0.5:.0f}？"
            )
        return [item]

    # ── 8. Expense (generic) ─────────────────────────────────────────
    amount = effective_gross
    if amount:
        category = "other"
        if "吃" in text or "饭" in text:
            category = "necessary"
        if "减脂" in text or "健身" in text:
            category = "fitness_health"
        if "画材" in text or "书" in text:
            category = "art_learning_investment"

        title_clean = re.sub(r"花了\d+(?:\.\d+)?\s*元", "", text).strip()
        title_clean = re.sub(r"\d+(?:\.\d+)?\s*元", "", title_clean).strip()
        title_clean = re.sub(r"今天", "", title_clean).strip()
        title = title_clean if title_clean and len(title_clean) <= 20 else text

        return [{
            "type": "expense",
            "amount": amount,
            "title": title,
            "category": category,
            "raw": text,
        }]

    return []


# ── Public API ────────────────────────────────────────────────────────────────

def parse_batch(text: str, now: datetime | None = None) -> dict[str, Any]:
    """Parse multi-fact finance text into a structured draft.

    Returns dict with:
      - draft_id: str (UUID)
      - raw_text: str
      - items: list[dict]
      - questions: list[str]
      - summary: dict with counts and totals
    """
    if now is None:
        now = datetime.now(timezone.utc)

    sentences = _BATCH_SENTENCE_SEPARATORS.split(text)
    sentences = [s.strip() for s in sentences if s.strip()]

    items: list[dict[str, Any]] = []
    questions: list[str] = []

    for sentence in sentences:
        parsed = _parse_sentence(sentence, now)
        for part in parsed:
            if "_question" in part:
                questions.append(part.pop("_question"))
            items.append(part)

    expenses = [i for i in items if i["type"] == "expense"]
    reimbursements = [i for i in items if i["type"] == "reimbursement"]
    debts = [i for i in items if i["type"] == "partner_debt_created"]
    rules = [i for i in items if i["type"] == "parent_fund_rule_configured"]
    pf_records = [i for i in items if i["type"] == "parent_fund_request_recorded"]
    pf_plans = [i for i in items if i["type"] == "parent_fund_request_planned"]

    for plan in pf_plans:
        if plan["amount"] >= 200:
            questions.append(
                f"计划要钱「{plan['description']}」金额 {plan['amount']:.0f} 元较大，"
                f"建议分笔或确认是否有必要。"
            )

    total_expense = sum(i["amount"] for i in expenses)
    total_reimbursement = sum(i["reimbursed_amount"] for i in reimbursements)
    net_personal = total_expense + sum(
        i["net_amount"] for i in reimbursements
    )

    draft_id = str(uuid4())
    return {
        "draft_id": draft_id,
        "raw_text": text,
        "items": items,
        "questions": questions,
        "summary": {
            "expense_count": len(expenses),
            "expense_total": total_expense,
            "reimbursement_count": len(reimbursements),
            "reimbursement_total": total_reimbursement,
            "net_personal_cost": net_personal,
            "debt_count": len(debts),
            "debt_total": sum(i["amount"] for i in debts),
            "rule_count": len(rules),
            "pf_record_count": len(pf_records),
            "pf_record_total": sum(i["amount"] for i in pf_records),
            "pf_plan_count": len(pf_plans),
            "pf_plan_total": sum(i["amount"] for i in pf_plans),
        },
    }
