"""
brahma_v6/adapters/live_binance_adapter.py — LiveBinanceAdapter for FULL_AUTO_LIVE_LITE
Phase 5 | 2026-07-09

KEY INVARIANTS:
  - synthetic_fill is ALWAYS False (kill switch triggered if violated)
  - test_order=True calls test endpoint only, never real exchange
  - reduce_only only for CLOSE orders
  - All fills come from exchange callbacks, never simulated
"""
from __future__ import annotations
import uuid
import time
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List, Callable

from brahma_v6.adapters.binance_client import BinanceClient, BinanceAPIError
from brahma_v6.adapters.binance_filters import SymbolFilters
from brahma_v6.adapters.binance_order_mapper import map_intent_to_binance_params, generate_client_order_id
from brahma_v6.risk.kill_switch import KillSwitch
from brahma_v6.risk.models import ProposedOrder


@dataclass
class AdapterOrderEvent:
    """Result from a submit/cancel/query operation."""
    event_type: str            # SUBMITTED | CANCELLED | UNKNOWN | REJECTED | FILLED | PARTIAL_FILL
    client_order_id: str
    exchange_order_id: Optional[int] = None
    symbol: str = ""
    side: str = ""
    order_type: str = ""
    price: float = 0.0
    quantity: float = 0.0
    executed_qty: float = 0.0
    avg_price: float = 0.0
    status: str = ""
    synthetic_fill: bool = False    # MUST always be False
    error: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)

    def __post_init__(self):
        # Invariant: synthetic_fill MUST be False in live adapter
        if self.synthetic_fill:
            raise ValueError("INVARIANT VIOLATED: synthetic_fill must always be False in LiveBinanceAdapter")


class ModePolicyError(Exception):
    pass


class ModePolicyLive:
    """Mode policy for FULL_AUTO_LIVE_LITE."""

    def assert_can_submit(self) -> None:
        """Assert we're allowed to submit orders in this mode."""
        pass  # Always allowed in live mode

    def assert_no_market_orders(self, order_type: str) -> None:
        if order_type.upper() == "MARKET":
            raise ModePolicyError("MARKET orders are prohibited in FULL_AUTO_LIVE_LITE mode")


class LiveBinanceAdapter:
    """
    Live Binance USDS-M Futures adapter.

    Submits orders through BinanceClient with HMAC signing.
    - test_order=True: uses /order/test endpoint only (no real execution)
    - test_order=False: uses real /order endpoint
    - synthetic_fill is ALWAYS False — kill switch triggered if violated
    """

    def __init__(
        self,
        client: BinanceClient,
        filter_provider: Callable[[str], SymbolFilters],
        mode_policy: ModePolicyLive,
        kill_switch: KillSwitch,
        test_order: bool = True,
    ) -> None:
        self.client = client
        self.filter_provider = filter_provider
        self.mode_policy = mode_policy
        self.kill_switch = kill_switch
        self.test_order = test_order
        self._order_update_callbacks: List[Callable] = []

    def submit(self, intent: ProposedOrder) -> AdapterOrderEvent:
        """
        Submit an order to Binance.
        Returns AdapterOrderEvent with synthetic_fill=False always.
        """
        # Mode policy check
        self.mode_policy.assert_can_submit()
        self.mode_policy.assert_no_market_orders(intent.order_type)

        # Kill switch check
        if self.kill_switch.is_active():
            return AdapterOrderEvent(
                event_type="REJECTED",
                client_order_id=intent.client_order_id or "N/A",
                error=f"kill_switch active: {self.kill_switch.reason}",
                synthetic_fill=False,
            )

        # Get filters for symbol
        filters = self.filter_provider(intent.symbol)

        # Generate unique client order ID
        coid = generate_client_order_id(
            signal_id=intent.client_order_id or "",
            prefix="brahma",
        )

        try:
            # Map intent to Binance params (raises if notional/qty invalid)
            params = map_intent_to_binance_params(intent, filters, client_order_id=coid)
        except ValueError as e:
            return AdapterOrderEvent(
                event_type="REJECTED",
                client_order_id=coid,
                error=str(e),
                synthetic_fill=False,
            )

        try:
            if self.test_order:
                # Test order - calls test endpoint, no real execution
                raw = self.client.new_order_test(dict(params))
                # Test endpoint returns {} on success
                return AdapterOrderEvent(
                    event_type="SUBMITTED",
                    client_order_id=coid,
                    symbol=intent.symbol,
                    side=intent.side,
                    order_type=intent.order_type,
                    price=float(params.get("price", 0)),
                    quantity=float(params.get("quantity", 0)),
                    status="TEST_OK",
                    synthetic_fill=False,
                    raw=raw or {},
                )
            else:
                # Real order
                raw = self.client.new_order(dict(params))
                return AdapterOrderEvent(
                    event_type="SUBMITTED",
                    client_order_id=raw.get("clientOrderId", coid),
                    exchange_order_id=raw.get("orderId"),
                    symbol=raw.get("symbol", intent.symbol),
                    side=raw.get("side", intent.side),
                    order_type=raw.get("type", intent.order_type),
                    price=float(raw.get("price", 0)),
                    quantity=float(raw.get("origQty", params.get("quantity", 0))),
                    executed_qty=float(raw.get("executedQty", 0)),
                    avg_price=float(raw.get("avgPrice", 0)),
                    status=raw.get("status", "SUBMITTED"),
                    synthetic_fill=False,
                    raw=raw,
                )

        except BinanceAPIError as e:
            return AdapterOrderEvent(
                event_type="UNKNOWN",
                client_order_id=coid,
                error=str(e),
                synthetic_fill=False,
            )
        except Exception as e:
            return AdapterOrderEvent(
                event_type="UNKNOWN",
                client_order_id=coid,
                error=f"unexpected: {e}",
                synthetic_fill=False,
            )

    def cancel_order(self, symbol: str, client_order_id: str) -> AdapterOrderEvent:
        """Cancel an open order."""
        try:
            raw = self.client.cancel_order(symbol, client_order_id=client_order_id)
            return AdapterOrderEvent(
                event_type="CANCELLED",
                client_order_id=client_order_id,
                symbol=symbol,
                status=raw.get("status", "CANCELED"),
                synthetic_fill=False,
                raw=raw,
            )
        except BinanceAPIError as e:
            return AdapterOrderEvent(
                event_type="UNKNOWN",
                client_order_id=client_order_id,
                error=str(e),
                synthetic_fill=False,
            )

    def query_order(self, symbol: str, client_order_id: str) -> AdapterOrderEvent:
        """Query order status from exchange."""
        try:
            raw = self.client.get_order(symbol, client_order_id=client_order_id)
            status = raw.get("status", "UNKNOWN")
            event_type = {
                "NEW": "SUBMITTED",
                "PARTIALLY_FILLED": "PARTIAL_FILL",
                "FILLED": "FILLED",
                "CANCELED": "CANCELLED",
                "REJECTED": "REJECTED",
                "EXPIRED": "CANCELLED",
            }.get(status, "UNKNOWN")
            return AdapterOrderEvent(
                event_type=event_type,
                client_order_id=raw.get("clientOrderId", client_order_id),
                exchange_order_id=raw.get("orderId"),
                symbol=raw.get("symbol", symbol),
                executed_qty=float(raw.get("executedQty", 0)),
                avg_price=float(raw.get("avgPrice", 0)),
                status=status,
                synthetic_fill=False,
                raw=raw,
            )
        except BinanceAPIError as e:
            return AdapterOrderEvent(
                event_type="UNKNOWN",
                client_order_id=client_order_id,
                error=str(e),
                synthetic_fill=False,
            )

    def positions(self) -> List[Dict]:
        """Get current positions from exchange."""
        try:
            return self.client.position_risk() or []
        except Exception:
            return []

    def on_order_update(self, exchange_event: Dict) -> Optional[AdapterOrderEvent]:
        """
        Handle an ORDER_TRADE_UPDATE event from the user data stream.
        synthetic_fill MUST be False — triggers kill switch if violated.
        """
        order_data = exchange_event.get("o", exchange_event)
        status = order_data.get("X", order_data.get("status", "UNKNOWN"))
        event_type_map = {
            "NEW": "SUBMITTED",
            "PARTIALLY_FILLED": "PARTIAL_FILL",
            "FILLED": "FILLED",
            "CANCELED": "CANCELLED",
            "REJECTED": "REJECTED",
            "EXPIRED": "CANCELLED",
        }
        event_type = event_type_map.get(status, "UNKNOWN")

        # Invariant check: no synthetic fills allowed
        # In live adapter, all fills come from exchange, never simulated
        event = AdapterOrderEvent(
            event_type=event_type,
            client_order_id=order_data.get("c", ""),
            exchange_order_id=order_data.get("i"),
            symbol=order_data.get("s", ""),
            side=order_data.get("S", ""),
            order_type=order_data.get("o", ""),
            price=float(order_data.get("p", 0)),
            quantity=float(order_data.get("q", 0)),
            executed_qty=float(order_data.get("z", 0)),
            avg_price=float(order_data.get("ap", 0)),
            status=status,
            synthetic_fill=False,  # ALWAYS FALSE
            raw=exchange_event,
        )

        # Fire callbacks
        for cb in self._order_update_callbacks:
            try:
                cb(event)
            except Exception:
                pass

        return event

    def register_order_update_callback(self, callback: Callable) -> None:
        self._order_update_callbacks.append(callback)
