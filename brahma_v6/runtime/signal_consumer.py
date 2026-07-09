"""
brahma_v6/runtime/signal_consumer.py — Reads from signal_bus, filters, emits OrderIntentRequest
Phase 5 | 2026-07-09
"""
from __future__ import annotations
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Dict, Any


@dataclass
class RawSignal:
    """Raw signal from the bus."""
    symbol: str
    side: str
    score: float
    price: float
    quantity: float
    regime: str = "NEUTRAL"
    ev_bucket_action: str = "ALLOW"
    order_type: str = "LIMIT"
    reduce_only: bool = False
    signal_id: str = field(default_factory=lambda: __import__("uuid").uuid4().hex)
    metadata: Dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)


@dataclass
class OrderIntentRequest:
    """Filtered signal ready for risk evaluation and order creation."""
    symbol: str
    side: str
    score: float
    price: float
    quantity: float
    regime: str
    ev_bucket_action: str
    order_type: str
    reduce_only: bool
    signal_id: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)


# Regimes that should be blocked regardless of signal score
BLOCKED_REGIMES = {"BEAR_TREND", "CHOP_LONG", "UNKNOWN_BROKEN"}


class SignalConsumer:
    """
    Reads signals from signal_bus (or a callable source).
    Applies score/regime/ev filters before forwarding as OrderIntentRequest.
    """

    def __init__(
        self,
        min_score: float = 0.5,
        blocked_regimes: Optional[List[str]] = None,
        symbol_allowlist: Optional[List[str]] = None,
        on_intent: Optional[Callable[[OrderIntentRequest], None]] = None,
    ) -> None:
        self.min_score = min_score
        self.blocked_regimes = set(blocked_regimes) if blocked_regimes else BLOCKED_REGIMES
        self.symbol_allowlist = set(symbol_allowlist) if symbol_allowlist else {"ETHUSDT"}
        self.on_intent = on_intent
        self._lock = threading.Lock()
        self._stats = {"received": 0, "filtered": 0, "forwarded": 0}

    def consume(self, signal: RawSignal) -> Optional[OrderIntentRequest]:
        """
        Filter a raw signal. Returns OrderIntentRequest if passes all gates, else None.
        """
        with self._lock:
            self._stats["received"] += 1

        # Score gate
        if signal.score < self.min_score:
            with self._lock:
                self._stats["filtered"] += 1
            return None

        # Regime gate
        if signal.regime.upper() in self.blocked_regimes:
            with self._lock:
                self._stats["filtered"] += 1
            return None

        # EV gate
        if (signal.ev_bucket_action or "ALLOW").upper() != "ALLOW":
            with self._lock:
                self._stats["filtered"] += 1
            return None

        # Symbol allowlist
        if signal.symbol not in self.symbol_allowlist:
            with self._lock:
                self._stats["filtered"] += 1
            return None

        # Build intent request
        intent_req = OrderIntentRequest(
            symbol=signal.symbol,
            side=signal.side,
            score=signal.score,
            price=signal.price,
            quantity=signal.quantity,
            regime=signal.regime,
            ev_bucket_action=signal.ev_bucket_action,
            order_type=signal.order_type,
            reduce_only=signal.reduce_only,
            signal_id=signal.signal_id,
            metadata=signal.metadata,
            ts=signal.ts,
        )

        with self._lock:
            self._stats["forwarded"] += 1

        if self.on_intent:
            self.on_intent(intent_req)

        return intent_req

    @property
    def stats(self) -> Dict[str, int]:
        with self._lock:
            return dict(self._stats)
