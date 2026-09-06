"""Hard gates. One law. No per-script MIN_SCORE forks."""

from __future__ import annotations

from dataclasses import dataclass

from brahma_os.config import Settings
from brahma_os.contracts import Signal


@dataclass(frozen=True)
class GateDecision:
    allow: bool
    code: str
    reason: str


def evaluate_gates(
    signal: Signal,
    settings: Settings,
    *,
    open_positions: int,
    symbol_exposure: float,
    gross_exposure: float,
    breaker_on: bool = False,
    now_ts: float | None = None,
) -> GateDecision:
    ts = now_ts if now_ts is not None else signal.ts
    if settings.env == "live" and settings.max_leverage > 5:
        return GateDecision(False, "LIVE_LEVERAGE", "live max leverage is 5x")
    if breaker_on:
        return GateDecision(False, "BREAKER", "account breaker active")
    if ts > signal.valid_until:
        return GateDecision(False, "EXPIRED", "signal past valid_until")
    if signal.score < settings.min_score:
        return GateDecision(False, "SCORE", f"score {signal.score} < {settings.min_score}")
    if signal.grade < settings.min_grade:
        return GateDecision(False, "GRADE", f"grade {signal.grade} < {settings.min_grade}")
    if signal.rr < settings.min_rr:
        return GateDecision(False, "RR_LOW", f"rr {signal.rr:.2f} < {settings.min_rr}")
    if signal.rr > settings.max_rr:
        return GateDecision(False, "RR_HIGH", f"rr {signal.rr:.2f} > {settings.max_rr}")
    if signal.side == "LONG" and signal.regime in settings.dead_long_regimes:
        return GateDecision(False, "DEAD_HOLE", f"{signal.regime} x LONG")
    if signal.side == "SHORT" and signal.regime in settings.dead_short_regimes:
        return GateDecision(False, "DEAD_HOLE", f"{signal.regime} x SHORT")
    if signal.side == "LONG" and not (signal.stop < signal.entry_lo <= signal.entry_hi < signal.target):
        return GateDecision(False, "GEOMETRY", "LONG requires stop < entry < target")
    if signal.side == "SHORT" and not (signal.target < signal.entry_lo <= signal.entry_hi < signal.stop):
        return GateDecision(False, "GEOMETRY", "SHORT requires target < entry < stop")
    if open_positions >= settings.max_open_positions:
        return GateDecision(False, "POS_LIMIT", "max open positions")
    if symbol_exposure >= settings.max_symbol_weight:
        return GateDecision(False, "SYMBOL_CAP", "symbol weight cap")
    if gross_exposure >= settings.max_gross_exposure:
        return GateDecision(False, "GROSS_CAP", "gross exposure cap")
    return GateDecision(True, "PASS", "ok")
