"""
brahma_v6/ops/ten_million/scenario.py — EventScenario dataclass for 10M simulation
Phase 5 | 2026-07-09
"""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class EventScenario:
    """
    Represents a single simulated trading event.
    All fault flags drive invariant checking.
    """
    # Signal attributes
    event_type: str = "SIGNAL"           # SIGNAL | FILL | CANCEL | REJECT | UNKNOWN
    symbol: str = "ETHUSDT"
    signal_score: float = 0.8
    regime: str = "NEUTRAL"
    ev_bucket_action: str = "ALLOW"

    # Fill attributes
    fill_qty: float = 0.01
    fill_price: float = 2000.0

    # Fault flags
    is_unknown: bool = False             # Order stuck in UNKNOWN state
    is_reject: bool = False              # Exchange rejected order
    is_partial: bool = False             # Partial fill
    ledger_fail: bool = False            # Ledger write failure
    websocket_gap: bool = False          # WebSocket message gap
    kill_switch_trigger: bool = False    # Event that should trigger kill switch

    # Derived fields
    nav: float = 10000.0
    daily_pnl: float = 0.0
    open_positions: int = 0
    open_orders: int = 0
    trades_today: int = 0
    consecutive_losses: int = 0
