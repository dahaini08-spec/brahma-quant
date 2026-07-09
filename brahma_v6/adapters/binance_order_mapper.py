"""
brahma_v6/adapters/binance_order_mapper.py — Maps OrderIntent to Binance API params
Phase 5 | 2026-07-09
"""
from __future__ import annotations
import uuid
from typing import Dict, Any, Optional

from brahma_v6.adapters.binance_filters import SymbolFilters
from brahma_v6.risk.models import ProposedOrder


def map_intent_to_binance_params(
    intent: ProposedOrder,
    filters: SymbolFilters,
    client_order_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Map a ProposedOrder + SymbolFilters into Binance Futures API params dict.

    Returns dict with:
      symbol, side, type, quantity, price, timeInForce, newClientOrderId
      positionSide (BOTH for one-way mode)
      reduceOnly (if applicable)
    """
    # Adjust price and qty to exchange precision
    adj_price = filters.adjust_price(intent.price)
    adj_qty = filters.adjust_qty(intent.quantity)

    # Validate after adjustment
    if adj_qty <= 0:
        raise ValueError(f"Adjusted quantity {adj_qty} <= 0 for {intent.symbol}")
    if not filters.check_min_qty(adj_qty):
        raise ValueError(f"Adjusted qty {adj_qty} < min_qty {filters.min_qty}")
    if not filters.check_notional(adj_price, adj_qty) and not intent.reduce_only:
        raise ValueError(
            f"Notional {adj_price * adj_qty:.4f} < min_notional {filters.min_notional} for {intent.symbol}"
        )

    order_type = intent.order_type.upper()

    params: Dict[str, Any] = {
        "symbol": intent.symbol,
        "side": intent.side.upper(),
        "type": order_type,
        "quantity": str(adj_qty),
        "price": str(adj_price),
        "newClientOrderId": client_order_id or intent.client_order_id,
        "positionSide": "BOTH",  # one-way mode
    }

    # LIMIT_MAKER does not use timeInForce
    if order_type == "LIMIT":
        params["timeInForce"] = intent.time_in_force or "GTC"

    if intent.reduce_only:
        params["reduceOnly"] = "true"

    return params


def generate_client_order_id(signal_id: str = "", prefix: str = "brahma") -> str:
    """Generate a unique client order ID (max 36 chars for Binance)."""
    uid = uuid.uuid4().hex[:16]
    if signal_id:
        return f"{prefix}_{signal_id[:8]}_{uid}"[:36]
    return f"{prefix}_{uid}"[:36]
