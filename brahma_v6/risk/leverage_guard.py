"""
brahma_v6/risk/leverage_guard.py — Enforces max_leverage=5, LIMIT_ONLY
Phase 5 | 2026-07-09
"""
from __future__ import annotations
from typing import Set


ALLOWED_ORDER_TYPES: Set[str] = {"LIMIT", "LIMIT_MAKER"}


class LeverageGuard:
    """
    Enforces maximum leverage limit and order type restrictions.
    No market orders. Only LIMIT and LIMIT_MAKER.
    """

    def __init__(self, max_leverage: int = 5) -> None:
        self.max_leverage = max_leverage

    def check_leverage(self, leverage: int) -> tuple:
        """Returns (ok, reason)."""
        if leverage > self.max_leverage:
            return False, f"leverage={leverage} exceeds max={self.max_leverage}"
        if leverage <= 0:
            return False, f"leverage={leverage} must be > 0"
        return True, "ok"

    def check_order_type(self, order_type: str) -> tuple:
        """Returns (ok, reason). Market orders are always rejected."""
        if order_type.upper() == "MARKET":
            return False, "MARKET orders are prohibited in FULL_AUTO_LIVE_LITE mode"
        if order_type.upper() not in ALLOWED_ORDER_TYPES:
            return False, f"order_type={order_type} not in allowed set {ALLOWED_ORDER_TYPES}"
        return True, "ok"

    def check_all(self, order_type: str, leverage: int) -> tuple:
        """Returns (ok, reason)."""
        ok, reason = self.check_order_type(order_type)
        if not ok:
            return False, reason
        ok, reason = self.check_leverage(leverage)
        if not ok:
            return False, reason
        return True, "ok"
