"""Spending warning logic — firm tone, no gentle nudges.

Generates warnings for:
- Outing/date budget approaching or exceeded (200/250 thresholds).
- Savings target compression.
"""

from __future__ import annotations

from typing import Any


def generate_spending_warnings(
    current_outing_spent: float,
    outing_budget: float = 250,
    savings_target: float = 500,
    estimated_savings: float | None = None,
    current_month_outflow: float = 0,
    current_month_inflow: float = 0,
) -> list[str]:
    """Generate firm spending warnings.

    Returns list of warning strings (empty if all clear).
    """
    warnings: list[str] = []

    # Outing budget warnings
    if current_outing_spent >= outing_budget:
        warnings.append(
            f"⚠️ 本月约会/出去玩支出已达 {current_outing_spent:.0f}/{outing_budget} 元。"
            "不建议继续出去玩消费。再花就是透支了。"
        )
    elif current_outing_spent >= outing_budget * 0.8:
        remaining = outing_budget - current_outing_spent
        warnings.append(
            f"⚠️ 本月约会/出去玩已花 {current_outing_spent:.0f}/{outing_budget} 元"
            f"（剩余 {remaining:.0f} 元）。注意控制。"
        )

    # Savings target check
    if estimated_savings is not None and estimated_savings < savings_target:
        shortfall = savings_target - estimated_savings
        warnings.append(
            f"⚠️ 预计本月只能存 {estimated_savings:.0f} 元（目标 {savings_target} 元），"
            f"还差 {shortfall:.0f} 元。这笔支出在压缩储蓄空间。"
        )

    # General overspend if outflow exceeds inflow significantly
    if current_month_outflow > current_month_inflow > 0:
        deficit = current_month_outflow - current_month_inflow
        warnings.append(
            f"⚠️ 本月支出已超过收入 {deficit:.0f} 元。入不敷出，需要控制开销。"
        )

    return warnings
