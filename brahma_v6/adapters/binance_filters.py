"""
brahma_v6/adapters/binance_filters.py — Symbol filters with price/qty adjustment
Phase 5 | 2026-07-09
"""
from __future__ import annotations
import math
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN


def _precision_from_step(step: float) -> int:
    """Compute decimal precision from a tick/step size like 0.01 → 2."""
    if step >= 1:
        return 0
    s = f"{step:.10f}".rstrip("0")
    if "." in s:
        return len(s.split(".")[1])
    return 0


def _floor_to_step(value: float, step: float) -> float:
    """Floor value to nearest step size using Decimal for precision."""
    if step <= 0:
        return value
    d_val = Decimal(str(value))
    d_step = Decimal(str(step))
    result = (d_val / d_step).to_integral_value(rounding=ROUND_DOWN) * d_step
    return float(result)


@dataclass
class SymbolFilters:
    """
    Holds exchange-provided filters for a symbol.
    Provides methods to adjust price/qty to valid values and check notional.
    """
    symbol: str
    tick_size: float        # PRICE_FILTER: minimum price movement
    step_size: float        # LOT_SIZE: minimum quantity increment
    min_notional: float     # NOTIONAL: minimum notional value in USDT
    min_qty: float          # LOT_SIZE: minimum quantity

    def adjust_price(self, price: float) -> float:
        """Floor price to nearest tick_size."""
        return _floor_to_step(price, self.tick_size)

    def adjust_qty(self, qty: float) -> float:
        """Floor quantity to nearest step_size."""
        return _floor_to_step(qty, self.step_size)

    def check_notional(self, price: float, qty: float) -> bool:
        """Return True if price * qty >= min_notional."""
        return price * qty >= self.min_notional

    def check_min_qty(self, qty: float) -> bool:
        """Return True if qty >= min_qty."""
        return qty >= self.min_qty

    @classmethod
    def from_exchange_info(cls, symbol: str, filters: list) -> "SymbolFilters":
        """Parse from Binance exchange info filter list."""
        tick_size = 0.01
        step_size = 0.001
        min_notional = 5.0
        min_qty = 0.001

        for f in filters:
            ftype = f.get("filterType", "")
            if ftype == "PRICE_FILTER":
                tick_size = float(f.get("tickSize", tick_size))
            elif ftype == "LOT_SIZE":
                step_size = float(f.get("stepSize", step_size))
                min_qty = float(f.get("minQty", min_qty))
            elif ftype == "MIN_NOTIONAL":
                min_notional = float(f.get("notional", min_notional))

        return cls(
            symbol=symbol,
            tick_size=tick_size,
            step_size=step_size,
            min_notional=min_notional,
            min_qty=min_qty,
        )

    # Default ETHUSDT filters (used when exchange info unavailable)
    @classmethod
    def ethusdt_default(cls) -> "SymbolFilters":
        return cls(
            symbol="ETHUSDT",
            tick_size=0.01,
            step_size=0.001,
            min_notional=5.0,
            min_qty=0.001,
        )
