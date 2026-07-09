"""
brahma_v6/risk/exposure_guard.py — max_open_positions=1, max_open_orders=1, max_symbols=1
Phase 5 | 2026-07-09
"""
from __future__ import annotations
from typing import List


class ExposureGuard:
    """
    Enforces hard limits on open positions, open orders, and active symbols.
    """

    def __init__(
        self,
        max_open_positions: int = 1,
        max_open_orders: int = 1,
        max_symbols: int = 1,
        symbol_allowlist: List[str] = None,
    ) -> None:
        self.max_open_positions = max_open_positions
        self.max_open_orders = max_open_orders
        self.max_symbols = max_symbols
        self.symbol_allowlist = set(symbol_allowlist) if symbol_allowlist else {"ETHUSDT"}

    def check_positions(self, open_positions: int) -> bool:
        """Return True if within limit."""
        return open_positions < self.max_open_positions

    def check_orders(self, open_orders: int) -> bool:
        """Return True if within limit."""
        return open_orders < self.max_open_orders

    def check_symbol(self, symbol: str) -> bool:
        """Return True if symbol is in allowlist."""
        return symbol in self.symbol_allowlist

    def check_all(self, symbol: str, open_positions: int, open_orders: int) -> tuple:
        """
        Returns (ok: bool, reason: str).
        ok=True means all checks pass.
        """
        if not self.check_symbol(symbol):
            return False, f"Symbol {symbol} not in allowlist {self.symbol_allowlist}"
        if not self.check_positions(open_positions):
            return False, f"open_positions={open_positions} >= max={self.max_open_positions}"
        if not self.check_orders(open_orders):
            return False, f"open_orders={open_orders} >= max={self.max_open_orders}"
        return True, "ok"
