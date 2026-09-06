#!/usr/bin/env python3
"""Research entry: synthetic-signal Monte Carlo. Not a live switch."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from brahma_os.config import Settings
from brahma_os.contracts import Signal
from brahma_os.monte_carlo import run_worlds
from brahma_os.research import verdict


def demo_signals() -> list[Signal]:
    out = []
    for i in range(12):
        ts = 1_700_000_000.0 + i * 3600.0
        out.append(
            Signal(
                signal_id=f"demo-{i}",
                ts=ts,
                symbol="BTCUSDT",
                side="LONG" if i % 2 == 0 else "SHORT",
                regime="BULL_EARLY" if i % 2 == 0 else "BEAR_EARLY",
                score=150,
                grade=85,
                entry_lo=99.6,
                entry_hi=100.4,
                stop=97.0 if i % 2 == 0 else 103.0,
                target=106.0 if i % 2 == 0 else 94.0,
                valid_until=ts + 24 * 3600,
                source="demo",
            )
        )
    return out


def main() -> int:
    settings = Settings(start_nav=10_000)
    mc = run_worlds(
        demo_signals(),
        settings=settings,
        n_worlds=30,
        n_bars=600,
        base_seed=42,
        cost_mults=(1.0, 2.0),
        same_bars=("stop",),
    )
    v = verdict(mc, settings)
    print(json.dumps({"promote": v.promote, "fails": list(v.fails), "mc": v.summary}, ensure_ascii=False, indent=2))
    return 0 if v.promote else 2


if __name__ == "__main__":
    raise SystemExit(main())
