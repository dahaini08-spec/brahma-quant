"""
data_audit.py — OHLCV 数据质量审计
封印: Brahma 2.0 P0-Plus 2026-07-11

验收标准（is_clean=True 条件）:
  ✅ 必要列全部存在
  ✅ 时间戳单调递增
  ✅ 无重复时间戳
  ✅ OHLC 关系合法（H≥L, H≥O/C, L≤O/C）
  ✅ Volume ≥ 0
  ✅ 无 NaN
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

REQUIRED_COLUMNS = ["open", "high", "low", "close", "volume"]


def normalize_df(df: pd.DataFrame) -> pd.DataFrame:
    """标准化列名 + 时间索引"""
    df = df.copy()
    df.columns = [str(c).lower() for c in df.columns]

    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
        df = df.set_index("timestamp")

    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index, utc=True, errors="coerce")

    return df.sort_index()


def audit_ohlcv(df: pd.DataFrame, symbol: str, timeframe: str) -> dict:
    df = normalize_df(df)
    issues: list[str] = []

    # 必要列检查
    missing_cols = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing_cols:
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "is_clean": False,
            "issues": [f"missing_columns={missing_cols}"],
        }

    if df.empty:
        issues.append("empty_dataframe")

    # 时间戳单调性
    if not df.index.is_monotonic_increasing:
        issues.append("timestamp_not_monotonic")

    # 重复时间戳
    dup_count = int(df.index.duplicated().sum())
    if dup_count > 0:
        issues.append(f"duplicate_timestamp={dup_count}")

    # OHLC 合法性
    invalid_ohlc = (
        (df["high"] < df["low"])
        | (df["high"] < df[["open", "close"]].max(axis=1))
        | (df["low"] > df[["open", "close"]].min(axis=1))
    )
    invalid_ohlc_count = int(invalid_ohlc.sum())
    if invalid_ohlc_count > 0:
        issues.append(f"invalid_ohlc={invalid_ohlc_count}")

    # 负成交量
    neg_vol = int((df["volume"] < 0).sum())
    if neg_vol > 0:
        issues.append(f"negative_volume={neg_vol}")

    # NaN
    nan_cells = int(df[REQUIRED_COLUMNS].isna().sum().sum())
    if nan_cells > 0:
        issues.append(f"nan_cells={nan_cells}")

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "bars": int(len(df)),
        "start": str(df.index.min()) if len(df) else None,
        "end": str(df.index.max()) if len(df) else None,
        "duplicate_timestamp": dup_count,
        "invalid_ohlc": invalid_ohlc_count,
        "negative_volume": neg_vol,
        "nan_cells": nan_cells,
        "issues": issues,
        "is_clean": len(issues) == 0,
    }


def _find_file(data_root: Path, symbol: str, timeframe: str) -> Path | None:
    candidates = [
        data_root / symbol.lower() / f"{symbol}_{timeframe}.parquet",
        data_root / symbol / f"{symbol}_{timeframe}.parquet",
        data_root / f"{symbol}_{timeframe}.parquet",
    ]
    return next((p for p in candidates if p.exists()), None)


def run_data_audit(
    data_root: str = "data/historical",
    output: str = "reports/simfactory/data_audit_report.json",
    symbols: list[str] | None = None,
    timeframes: list[str] | None = None,
) -> dict:
    symbols = symbols or ["BTCUSDT", "ETHUSDT"]
    timeframes = timeframes or ["15m", "1h", "4h", "1d"]
    root = Path(data_root)
    results: dict[str, dict] = {}

    for symbol in symbols:
        for tf in timeframes:
            key = f"{symbol}_{tf}"
            path = _find_file(root, symbol, tf)

            if path is None:
                results[key] = {
                    "symbol": symbol,
                    "timeframe": tf,
                    "is_clean": False,
                    "issues": ["file_not_found"],
                }
                continue

            try:
                df = pd.read_parquet(path)
                results[key] = audit_ohlcv(df, symbol, tf)
            except Exception as exc:
                results[key] = {
                    "symbol": symbol,
                    "timeframe": tf,
                    "is_clean": False,
                    "issues": [f"read_failed={repr(exc)}"],
                }

    report = {
        "summary": {
            "files": len(results),
            "clean": sum(1 for r in results.values() if r.get("is_clean")),
            "dirty": sum(1 for r in results.values() if not r.get("is_clean")),
        },
        "results": results,
    }

    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    return report
