"""
brahma_v6/risk/kill_switch.py — KillSwitch: global halt for live trading
Phase 5 | 2026-07-09

Triggers:
  - daily_loss: daily realized PnL >= 10% NAV
  - duplicate_order_id: same client_order_id submitted twice
  - reconcile_failure: reconciler cannot resolve unknown order
  - synthetic_fill: live adapter reports synthetic fill (forbidden)
  - unknown_timeout: unknown order unresolved after timeout
"""
from __future__ import annotations
import threading
import time
from typing import Optional


class KillSwitch:
    """
    Thread-safe kill switch for the live trading system.
    Once activated, ALL order submissions are blocked until explicitly reset.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: bool = False
        self._reason: Optional[str] = None
        self._activated_at: Optional[float] = None

    def activate(self, reason: str) -> None:
        """Activate the kill switch. Idempotent once active."""
        with self._lock:
            if not self._active:
                self._active = True
                self._reason = reason
                self._activated_at = time.time()

    def is_active(self) -> bool:
        with self._lock:
            return self._active

    def reset(self) -> None:
        """Reset the kill switch (operator use only)."""
        with self._lock:
            self._active = False
            self._reason = None
            self._activated_at = None

    @property
    def reason(self) -> Optional[str]:
        with self._lock:
            return self._reason

    @property
    def activated_at(self) -> Optional[float]:
        with self._lock:
            return self._activated_at

    def __repr__(self) -> str:
        return f"KillSwitch(active={self._active}, reason={self._reason!r})"
