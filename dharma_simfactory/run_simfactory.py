"""
run_simfactory.py — SimFactory CLI 入口
封印: Brahma 2.0 P0-Plus 2026-07-11

用法:
  python -m dharma_simfactory.run_simfactory data-audit \
      --data-root data/historical \
      --symbols BTCUSDT ETHUSDT \
      --timeframes 15m 1h 4h 1d

  python -m dharma_simfactory.run_simfactory baseline \
      --data-root data/historical \
      --symbols BTCUSDT ETHUSDT \
      --timeframes 1h 4h \
      --cost-multiplier 1.0
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .data_audit import run_data_audit
from .baseline_replay import run_baseline


def _find_file(data_root: str, symbol: str, timeframe: str) -> Path:
    root = Path(data_root)
    candidates = [
        root / symbol.lower() / f"{symbol}_{timeframe}.parquet",
        root / symbol / f"{symbol}_{timeframe}.parquet",
        root / f"{symbol}_{timeframe}.parquet",
    ]
    for p in candidates:
        if p.exists():
            return p
    raise FileNotFoundError(
        f"Cannot find {symbol}_{timeframe}.parquet in {data_root}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="brahma-simfactory",
        description="Brahma Dharma SimFactory — Minimal Institutional Replay",
    )
    sub = parser.add_subparsers(dest="mode", required=True)

    # --- data-audit ---
    p_audit = sub.add_parser("data-audit", help="Audit OHLCV data quality")
    p_audit.add_argument("--data-root", default="data/historical")
    p_audit.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT"])
    p_audit.add_argument(
        "--timeframes", nargs="+", default=["15m", "1h", "4h", "1d"]
    )
    p_audit.add_argument(
        "--output",
        default="reports/simfactory/data_audit_report.json",
    )

    # --- baseline ---
    p_base = sub.add_parser("baseline", help="Run EMA-crossover baseline replay")
    p_base.add_argument("--data-root", default="data/historical")
    p_base.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT"])
    p_base.add_argument("--timeframes", nargs="+", default=["1h", "4h"])
    p_base.add_argument("--cost-multiplier", type=float, default=1.0)
    p_base.add_argument(
        "--output-dir", default="reports/simfactory/baseline"
    )

    args = parser.parse_args()

    if args.mode == "data-audit":
        report = run_data_audit(
            data_root=args.data_root,
            output=args.output,
            symbols=args.symbols,
            timeframes=args.timeframes,
        )
        print(json.dumps(report["summary"], ensure_ascii=False, indent=2))

    elif args.mode == "baseline":
        for symbol in args.symbols:
            for tf in args.timeframes:
                try:
                    path = _find_file(args.data_root, symbol, tf)
                    report = run_baseline(
                        parquet_path=str(path),
                        symbol=symbol,
                        timeframe=tf,
                        output_dir=args.output_dir,
                        cost_multiplier=args.cost_multiplier,
                    )
                    print(json.dumps(report, ensure_ascii=False, indent=2))
                except FileNotFoundError as e:
                    print(f"[SKIP] {symbol}_{tf}: {e}")


if __name__ == "__main__":
    main()
