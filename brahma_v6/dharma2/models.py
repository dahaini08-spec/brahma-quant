"""
brahma_v6/dharma2/models.py
TradeRecord + PnLAttribution — 强制证据链
设计院 · 2026-07-09
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional


@dataclass(frozen=True)
class PnLAttribution:
    """
    Complete cost/drag breakdown for one trade.
    Rule: gross_pnl + all drags == net_pnl  (tolerance 1e-8)
    Drags are negative values representing costs.
    """
    gross_pnl:      float
    fee_drag:       float   # broker/exchange fees (≤ 0)
    slippage_drag:  float   # market impact on entry/exit (≤ 0)
    funding_drag:   float   # perp funding payments (any sign)
    impact_drag:    float   # residual market impact (≤ 0)
    net_pnl:        float   # must equal sum above

    def validate(self) -> None:
        """
        Mandatory call before persisting any TradeRecord.
        Raises ValueError if attribution does not balance.
        """
        expected = (
            self.gross_pnl
            + self.fee_drag
            + self.slippage_drag
            + self.funding_drag
            + self.impact_drag
        )
        if abs(expected - self.net_pnl) > 1e-8:
            raise ValueError(
                f"PnLAttribution mismatch: "
                f"sum({expected:.10f}) != net_pnl({self.net_pnl:.10f}), "
                f"delta={abs(expected - self.net_pnl):.2e}"
            )


@dataclass(frozen=True)
class TradeRecord:
    """
    Immutable ledger entry for one complete trade lifecycle.
    Every field traces back to a specific system component:
      signal_id  → brahma_analysis_runner
      risk_id    → risk.kernel
      intent_id  → execution.order_intent
      ticket_id  → execution.order_ticket
      trace_id   → end-to-end correlation (uuid4)
    """
    trade_id:           str
    trace_id:           str          # end-to-end correlation
    signal_id:          str          # originating brahma signal
    risk_id:            str          # risk kernel approval ref
    intent_id:          str          # OrderIntent id
    ticket_id:          str          # OrderTicket id
    order_event_ids:    List[str]    # all exchange event ids

    symbol:     str
    direction:  str    # "LONG" | "SHORT"
    regime:     str    # e.g. "BEAR_TREND"
    score:      float

    entry_price:  float
    exit_price:   Optional[float]   # None while open
    quantity:     float

    attribution: PnLAttribution     # must be .validate()'d before append

    mae:                  float   # Maximum Adverse Excursion (negative)
    mfe:                  float   # Maximum Favorable Excursion (positive)
    holding_time_seconds: int

    opened_at:  datetime
    closed_at:  Optional[datetime]
    created_at: datetime

    def __post_init__(self) -> None:
        """Auto-validate attribution on construction."""
        self.attribution.validate()

    @property
    def is_closed(self) -> bool:
        return self.exit_price is not None and self.closed_at is not None

    @property
    def realized_pnl(self) -> Optional[float]:
        """Returns net_pnl only when trade is closed."""
        return self.attribution.net_pnl if self.is_closed else None
