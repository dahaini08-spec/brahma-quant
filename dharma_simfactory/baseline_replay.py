"""
baseline_replay.py — 最小可运行 Baseline 回放
封印: Brahma 2.0 P0-Plus 2026-07-11

说明:
  - 本模块不是 Brahma 35D 信号，只是最小 EMA 交叉 baseline
  - 目的：验证 SimFactory 数据→交易→指标→报告 全链路跑通
  - 后续可替换为真实 brahma_analysis_runner 信号 replay
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from .cost_model import apply_cost
from .metrics import calc_metrics


def load_ohlcv(path: str | Path) -> pd.DataFrame:
    df = pd.read_parquet(path)
    df.columns = [str(c).lower() for c in df.columns]

    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
        df = df.set_index("timestamp")

    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index, utc=True, errors="coerce")

    return df.sort_index()


def simple_baseline(df: pd.DataFrame, hold_bars: int = 12) -> pd.DataFrame:
    """
    最小 EMA 交叉 baseline。
    EMA20 > EMA50 → LONG；EMA20 < EMA50 → SHORT
    固定持仓 hold_bars 根 K 线后平仓。
    """
    x = df.copy()
    x["ema20"] = x["close"].ewm(span=20).mean()
    x["ema50"] = x["close"].ewm(span=50).mean()

    trades = []

    for i in range(100, len(x) - hold_bars):
        direction: str | None = None

        if x["close"].iloc[i] > x["ema20"].iloc[i] > x["ema50"].iloc[i]:
            direction = "LONG"
        elif x["close"].iloc[i] < x["ema20"].iloc[i] < x["ema50"].iloc[i]:
            direction = "SHORT"

        if direction is None:
            continue

        entry = float(x["close"].iloc[i])
        exit_ = float(x["close"].iloc[i + hold_bars])

        gross = (exit_ / entry - 1) if direction == "LONG" else (entry / exit_ - 1)

        trades.append(
            {
                "entry_time": str(x.index[i]),
                "exit_time": str(x.index[i + hold_bars]),
                "direction": direction,
                "entry": entry,
                "exit": exit_,
                "gross_return": gross,
            }
        )

    return pd.DataFrame(trades)


def run_baseline(
    parquet_path: str,
    symbol: str,
    timeframe: str,
    output_dir: str = "reports/simfactory/baseline",
    cost_multiplier: float = 1.0,
) -> dict:
    df = load_ohlcv(parquet_path)
    trades = simple_baseline(df)

    if trades.empty:
        returns = pd.Series(dtype=float)
    else:
        trades["net_return"] = trades["gross_return"].apply(
            lambda r: apply_cost(r, symbol, timeframe, cost_multiplier)
        )
        returns = trades["net_return"]

    metrics = calc_metrics(returns)

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    if not trades.empty:
        trades.to_parquet(out / f"{symbol}_{timeframe}_baseline_trades.parquet")

    report = {
        "symbol": symbol,
        "timeframe": timeframe,
        "cost_multiplier": cost_multiplier,
        "metrics": metrics,
    }

    (out / f"{symbol}_{timeframe}_baseline_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return report
