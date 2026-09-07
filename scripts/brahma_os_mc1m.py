#!/usr/bin/env python3
"""1e6 Monte Carlo on costed trades. Not a live switch."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from brahma_os.mega_mc import bootstrap_trades, promote_mega, shuffle_trades


def demo_pnls() -> list[float]:
    return [42.0] * 22 + [-38.0] * 18


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--n", type=int, default=1_000_000)
    p.add_argument("--nav", type=float, default=10_000.0)
    p.add_argument("--mode", choices=("bootstrap", "shuffle"), default="bootstrap")
    p.add_argument("--seed", type=int, default=7)
    args = p.parse_args()
    fn = bootstrap_trades if args.mode == "bootstrap" else shuffle_trades
    summary = fn(demo_pnls(), start_nav=args.nav, n=args.n, seed=args.seed)
    ok, fails = promote_mega(summary)
    print(json.dumps({"promote": ok, "fails": fails, "mc": summary.as_dict()}, indent=2))
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
