"""
brahma_v6/risk/models.py — Risk decision models for Full-Auto Live Lite
Phase 5 | 2026-07-09
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict, Any


class RiskAction(str, Enum):
    APPROVE = "APPROVE"
    BLOCK = "BLOCK"
    DEGRADE = "DEGRADE"


@dataclass
class ProposedOrder:
    """A proposed order produced by the risk kernel on APPROVE."""
    symbol: str
    side: str               # BUY | SELL
    order_type: str         # LIMIT | LIMIT_MAKER
    quantity: float
    price: float
    client_order_id: str
    reduce_only: bool = False
    time_in_force: str = "GTC"
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RiskDecision:
    """Immutable risk decision emitted by RiskKernel.evaluate()."""
    action: RiskAction
    reason: str
    proposed_order: Optional[ProposedOrder] = None
    risk_id: str = field(default_factory=lambda: __import__("uuid").uuid4().hex)

    def is_approved(self) -> bool:
        return self.action == RiskAction.APPROVE

    def is_blocked(self) -> bool:
        return self.action == RiskAction.BLOCK

    def __repr__(self) -> str:
        return f"RiskDecision(action={self.action}, reason={self.reason!r})"
