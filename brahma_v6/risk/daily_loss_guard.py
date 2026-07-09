"""
brahma_v6/risk/daily_loss_guard.py — Tracks daily realized PnL, triggers kill switch at 10% NAV
Phase 5 | 2026-07-09
"""
from __future__ import annotations
import threading
import time
from typing import Optional


class DailyLossGuard:
    """
    Tracks daily realized PnL.
    Triggers kill switch when cumulative daily loss >= max_daily_loss_pct * nav.
    """

    def __init__(self, max_daily_loss_pct: float = 0.10) -> None:
        self._lock = threading.Lock()
        self._max_daily_loss_pct = max_daily_loss_pct
        self._daily_pnl: float = 0.0
        self._nav: float = 0.0
        self._day_start: float = time.time()

    def update_nav(self, nav: float) -> None:
        with self._lock:
            self._nav = nav

    def record_pnl(self, pnl: float) -> None:
        """Add realized PnL (positive = profit, negative = loss)."""
        with self._lock:
            self._daily_pnl += pnl

    def reset_day(self) -> None:
        with self._lock:
            self._daily_pnl = 0.0
            self._day_start = time.time()

    def is_limit_breached(self, nav: Optional[float] = None) -> bool:
        """Return True if daily loss >= max_daily_loss_pct * nav."""
        with self._lock:
            effective_nav = nav if nav is not None else self._nav
            if effective_nav <= 0:
                return False
            return self._daily_pnl <= -(self._max_daily_loss_pct * effective_nav)

    def daily_loss_pct(self, nav: Optional[float] = None) -> float:
        """Return current daily loss as fraction of nav (negative = loss)."""
        with self._lock:
            effective_nav = nav if nav is not None else self._nav
            if effective_nav <= 0:
                return 0.0
            return self._daily_pnl / effective_nav

    @property
    def daily_pnl(self) -> float:
        with self._lock:
            return self._daily_pnl
