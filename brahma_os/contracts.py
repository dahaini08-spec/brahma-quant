"""Frozen objects that cross module boundaries. No free-form dicts on the spine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Side = Literal["LONG", "SHORT"]
Regime = str
IntentStatus = Literal["NEW", "ARMED", "WORKING", "FILLED", "EXPIRED", "REJECTED", "CANCELLED"]
FillRole = Literal["ENTRY", "STOP", "TARGET", "MANUAL", "TIMEOUT"]
Outcome = Literal["WIN", "LOSS", "TIMEOUT", "FLAT", "OPEN"]


@dataclass(frozen=True)
class Signal:
    signal_id: str
    ts: float
    symbol: str
    side: Side
    regime: Regime
    score: float
    grade: float
    entry_lo: float
    entry_hi: float
    stop: float
    target: float
    valid_until: float
    source: str = "brain"
    extras: dict = field(default_factory=dict)

    @property
    def entry_mid(self) -> float:
        return (self.entry_lo + self.entry_hi) / 2.0

    @property
    def risk_pct(self) -> float:
        mid = self.entry_mid
        if mid <= 0:
            return 0.0
        return abs(mid - self.stop) / mid

    @property
    def rr(self) -> float:
        risk = abs(self.entry_mid - self.stop)
        reward = abs(self.target - self.entry_mid)
        if risk <= 0:
            return 0.0
        return reward / risk


@dataclass(frozen=True)
class Intent:
    intent_id: str
    signal_id: str
    ts: float
    symbol: str
    side: Side
    qty: float
    limit_price: float
    stop: float
    target: float
    status: IntentStatus = "NEW"
    reason: str = ""


@dataclass(frozen=True)
class Fill:
    fill_id: str
    intent_id: str
    signal_id: str
    ts: float
    symbol: str
    side: Side
    role: FillRole
    qty: float
    price: float
    fee: float
    slippage: float = 0.0
    source: str = "paper"


@dataclass
class Position:
    position_id: str
    signal_id: str
    symbol: str
    side: Side
    qty: float
    avg_entry: float
    stop: float
    target: float
    opened_ts: float
    fees_paid: float = 0.0
    funding_paid: float = 0.0
    realized: float = 0.0
    closed_ts: float | None = None
    outcome: Outcome = "OPEN"

    @property
    def is_open(self) -> bool:
        return self.qty > 0 and self.outcome == "OPEN"
