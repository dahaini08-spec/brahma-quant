"""Perpetual cost model. Every reported PnL must pass through here."""

from __future__ import annotations

from dataclasses import dataclass

from brahma_os.config import Settings
from brahma_os.contracts import Side


@dataclass(frozen=True)
class CostModel:
    settings: Settings

    def fee(self, notional: float, *, taker: bool = True) -> float:
        bps = self.settings.taker_fee_bps if taker else self.settings.maker_fee_bps
        return abs(notional) * bps / 10_000.0

    def slip(self, notional: float) -> float:
        return abs(notional) * self.settings.slippage_bps / 10_000.0

    def round_trip_cost(self, notional: float, *, taker: bool = True) -> float:
        return 2.0 * (self.fee(notional, taker=taker) + self.slip(notional))

    def funding(self, notional: float, hours_held: float, bps_8h: float | None = None) -> float:
        rate = self.settings.funding_default_bps_8h if bps_8h is None else bps_8h
        periods = hours_held / 8.0
        return abs(notional) * rate / 10_000.0 * periods

    def signed_pnl(
        self,
        side: Side,
        qty: float,
        entry: float,
        exit: float,
        fees: float,
        funding: float,
    ) -> float:
        if side == "LONG":
            gross = (exit - entry) * qty
        else:
            gross = (entry - exit) * qty
        return gross - fees - funding
