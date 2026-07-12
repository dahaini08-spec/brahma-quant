"""
brahma_v6/ops/reconciler.py — Reconciler for unknown orders
Phase 5 | 2026-07-09

Unknown orders must be resolved within timeout (30s).
If unresolved after timeout → activate kill switch.
Resolved orders are written to TradeLedger.
"""
from __future__ import annotations
import time
import threading
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable

from brahma_v6.risk.kill_switch import KillSwitch
from brahma_v6.ops.dlq import DeadLetterQueue

logger = logging.getLogger(__name__)


@dataclass
class UnknownOrderTicket:
    """Represents an order in UNKNOWN state awaiting reconciliation."""
    client_order_id: str
    symbol: str
    created_at: float = field(default_factory=time.time)
    resolved: bool = False
    resolution: Optional[str] = None


class TradeLedger:
    """Simple in-memory trade ledger (stub for integration)."""

    def __init__(self) -> None:
        self._entries: List[Dict] = []
        self._lock = threading.Lock()

    def record(self, entry: Dict) -> None:
        with self._lock:
            self._entries.append({**entry, "recorded_at": time.time()})

    def entries(self) -> List[Dict]:
        with self._lock:
            return list(self._entries)


class Reconciler:
    """
    Reconciles unknown orders by querying the exchange.
    - On resolution: writes to TradeLedger
    - On timeout (>30s): activates kill_switch + pushes to DLQ
    """

    def __init__(
        self,
        kill_switch: KillSwitch,
        ledger: Optional[TradeLedger] = None,
        dlq: Optional[DeadLetterQueue] = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.kill_switch = kill_switch
        self.ledger = ledger or TradeLedger()
        self.dlq = dlq or DeadLetterQueue(persist=False)
        self.timeout_seconds = timeout_seconds
        self._lock = threading.Lock()

    def reconcile_unknown_orders(
        self,
        tickets: List[UnknownOrderTicket],
        adapter,  # LiveBinanceAdapter or duck-type with query_order
    ) -> Dict[str, str]:
        """
        Try to resolve unknown orders.
        Returns dict: {client_order_id → resolution_status}
        """
        results = {}
        now = time.time()

        for ticket in tickets:
            if ticket.resolved:
                results[ticket.client_order_id] = ticket.resolution or "ALREADY_RESOLVED"
                continue

            age = now - ticket.created_at

            # Timeout check — activate kill switch
            if age > self.timeout_seconds:
                reason = f"reconcile_timeout: {ticket.client_order_id} unresolved after {age:.1f}s"
                logger.error(reason)
                self.kill_switch.activate(reason)
                self.dlq.push(
                    {"client_order_id": ticket.client_order_id, "symbol": ticket.symbol, "age_s": age},
                    reason=reason,
                )
                results[ticket.client_order_id] = "TIMEOUT_KILL_SWITCH"
                continue

            # Query exchange
            try:
                event = adapter.query_order(ticket.symbol, ticket.client_order_id)
                status = event.status if hasattr(event, "status") else event.get("status", "UNKNOWN")

                if status not in ("UNKNOWN", ""):
                    ticket.resolved = True
                    ticket.resolution = status
                    self.ledger.record({
                        "client_order_id": ticket.client_order_id,
                        "symbol": ticket.symbol,
                        "status": status,
                        "resolved_at": time.time(),
                        "fill_qty": getattr(event, "executed_qty", 0),
                        "avg_price": getattr(event, "avg_price", 0),
                    })
                    results[ticket.client_order_id] = status
                else:
                    results[ticket.client_order_id] = "STILL_UNKNOWN"

            except Exception as e:
                # Query failed → treat as reconciliation failure
                reason = f"reconcile_failure: {ticket.client_order_id} query error: {e}"
                logger.error(reason)
                self.kill_switch.activate(reason)
                self.dlq.push(
                    {"client_order_id": ticket.client_order_id, "error": str(e)},
                    reason=reason,
                )
                results[ticket.client_order_id] = "RECONCILE_FAILURE"

        return results
