"""Synthetic and recorded price paths. No look-ahead inside a bar."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass


@dataclass(frozen=True)
class Bar:
    ts: float
    open: float
    high: float
    low: float
    close: float

    def as_settle(self) -> tuple[float, float, float, float]:
        return (self.ts, self.high, self.low, self.close)


def generate_gbm_path(
    *,
    seed: int,
    start_px: float = 100.0,
    n_bars: int = 2_000,
    start_ts: float = 1_700_000_000.0,
    bar_seconds: float = 300.0,
    mu_per_year: float = 0.0,
    vol_per_year: float = 0.80,
    bars_per_year: float = 365.0 * 24.0 * 12.0,
) -> list[Bar]:
    rng = random.Random(seed)
    dt = 1.0 / bars_per_year
    drift = (mu_per_year - 0.5 * vol_per_year * vol_per_year) * dt
    shock = vol_per_year * math.sqrt(dt)
    px = start_px
    out: list[Bar] = []
    ts = start_ts
    for _ in range(n_bars):
        z = rng.gauss(0.0, 1.0)
        nxt = px * math.exp(drift + shock * z)
        hi = max(px, nxt) * (1.0 + abs(rng.gauss(0.0, 0.0008)))
        lo = min(px, nxt) * (1.0 - abs(rng.gauss(0.0, 0.0008)))
        if lo <= 0:
            lo = min(px, nxt) * 0.999
        out.append(Bar(ts=ts, open=px, high=hi, low=lo, close=nxt))
        px = nxt
        ts += bar_seconds
    return out


def trend_path(
    *,
    seed: int,
    side: str,
    start_px: float = 100.0,
    n_bars: int = 200,
    start_ts: float = 1_000.0,
    bar_seconds: float = 300.0,
    step: float = 0.15,
) -> list[Bar]:
    rng = random.Random(seed)
    px = start_px
    out: list[Bar] = []
    ts = start_ts
    sign = 1.0 if side == "UP" else -1.0
    for _ in range(n_bars):
        nxt = px + sign * step + rng.gauss(0.0, 0.04)
        hi = max(px, nxt) + 0.05
        lo = min(px, nxt) - 0.05
        out.append(Bar(ts=ts, open=px, high=hi, low=lo, close=nxt))
        px = nxt
        ts += bar_seconds
    return out
