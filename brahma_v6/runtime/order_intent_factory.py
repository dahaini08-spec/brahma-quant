"""
brahma_v6/runtime/order_intent_factory.py — Builds OrderIntent from signal + risk approval
Phase 5 | 2026-07-09
"""
from __future__ import annotations
import uuid
import time
from dataclasses import dataclass, field
from typing import Optional

from brahma_v6.risk.models import RiskDecision, ProposedOrder
from brahma_v6.runtime.signal_consumer import OrderIntentRequest


@dataclass
class OrderIntent:
    """
    Fully resolved order intent ready to be submitted to the adapter.
    All IDs are set: trace_id, signal_id, risk_id, intent_id.
    """
    # Identity
    intent_id: str
    trace_id: str
    signal_id: str
    risk_id: str
    # Order params
    symbol: str
    side: str
    order_type: str
    quantity: float
    price: float
    client_order_id: str
    reduce_only: bool = False
    time_in_force: str = "GTC"
    # Metadata
    score: float = 0.0
    regime: str = "NEUTRAL"
    created_at: float = field(default_factory=time.time)

    def to_proposed_order(self) -> ProposedOrder:
        return ProposedOrder(
            symbol=self.symbol,
            side=self.side,
            order_type=self.order_type,
            quantity=self.quantity,
            price=self.price,
            client_order_id=self.client_order_id,
            reduce_only=self.reduce_only,
            time_in_force=self.time_in_force,
        )


class OrderIntentFactory:
    """
    Creates OrderIntent from signal request + approved RiskDecision.
    Assigns all required IDs for full chain traceability.
    """

    def build(
        self,
        request: OrderIntentRequest,
        decision: RiskDecision,
        trace_id: Optional[str] = None,
    ) -> Optional[OrderIntent]:
        """
        Build an OrderIntent from an approved risk decision.
        Returns None if decision is not APPROVED.
        """
        if not decision.is_approved():
            return None

        proposed = decision.proposed_order
        if proposed is None:
            return None

        _trace_id = trace_id or uuid.uuid4().hex
        intent_id = uuid.uuid4().hex

        return OrderIntent(
            intent_id=intent_id,
            trace_id=_trace_id,
            signal_id=request.signal_id,
            risk_id=decision.risk_id,
            symbol=proposed.symbol,
            side=proposed.side,
            order_type=proposed.order_type,
            quantity=proposed.quantity,
            price=proposed.price,
            client_order_id=proposed.client_order_id or f"brahma_{intent_id[:16]}",
            reduce_only=proposed.reduce_only,
            time_in_force=proposed.time_in_force or "GTC",
            score=request.score,
            regime=request.regime,
        )
