"""Replay recorded klines through the same simulator used in Monte Carlo."""

from __future__ import annotations

from brahma_os.config import Settings
from brahma_os.contracts import Signal
from brahma_os.paths import Bar
from brahma_os.simulator import PathSimulator, SimReport


def klines_to_bars(raw: list, *, ts_unit: str = "auto") -> list[Bar]:
    bars: list[Bar] = []
    for row in raw:
        if isinstance(row, dict):
            ts = float(row.get("ts") or row.get("open_time") or row.get("time") or 0)
            o = float(row["open"])
            h = float(row["high"])
            l = float(row["low"])
            c = float(row["close"])
        else:
            ts = float(row[0])
            o, h, l, c = (float(row[1]), float(row[2]), float(row[3]), float(row[4]))
        if ts_unit == "ms" or (ts_unit == "auto" and ts > 10_000_000_000):
            ts = ts / 1000.0
        if h < l:
            h, l = l, h
        bars.append(Bar(ts=ts, open=o, high=h, low=l, close=c))
    bars.sort(key=lambda b: b.ts)
    return bars


def replay_signals(
    signals: list[Signal],
    klines: list,
    *,
    settings: Settings | None = None,
    symbol: str | None = None,
) -> SimReport:
    path = klines_to_bars(klines)
    return PathSimulator(settings or Settings()).run(signals, path, symbol=symbol)
