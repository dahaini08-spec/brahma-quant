"""
brahma_signal_replay.py — 真实信号 Replay
封印: P3 SimFactory 真实信号接入 2026-07-11

功能：
  读取 live_signal_log.jsonl（历史真实信号）
  → 对每条 valid=True 的信号，用历史K线重算入场/出场
  → 计算实际WR/PnL，与 MEMORY.md 封印数据对比
  → 输出 reports/simfactory/signal_replay_report.json
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from .cost_model import apply_cost
from .metrics import calc_metrics

SIGNAL_LOG  = Path("data/live_signal_log.jsonl")
OUTPUT_DIR  = Path("reports/simfactory")
OUTPUT_FILE = OUTPUT_DIR / "signal_replay_report.json"


# ── 历史K线获取 ─────────────────────────────────────────────────
def _fetch_klines(symbol: str, interval: str, start_ts: int, limit: int = 60) -> list:
    """从 Binance 拉取历史K线（公开API，无需Key）"""
    url = "https://fapi.binance.com/fapi/v1/klines"
    params = {"symbol": symbol, "interval": interval, "startTime": start_ts, "limit": limit}
    try:
        r = requests.get(url, params=params, timeout=8)
        return r.json() if r.status_code == 200 else []
    except Exception:
        return []


def _ts_to_ms(ts_str: str) -> int:
    """ISO字符串 → 毫秒时间戳"""
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return int(dt.timestamp() * 1000)
    except Exception:
        return int(time.time() * 1000)


# ── 单条信号回放 ─────────────────────────────────────────────────
def _replay_one(sig: dict, hold_bars: int = 8) -> dict | None:
    """
    对单条信号重算结果。
    hold_bars: 固定持仓K线数（默认8根1H = 8小时）
    """
    symbol    = sig.get("symbol", "")
    direction = sig.get("direction", "LONG")
    ts_str    = sig.get("ts", "")
    entry     = float(sig.get("entry_hi") or sig.get("entry_price") or 0)
    sl        = float(sig.get("sl_price") or sig.get("stop_loss") or 0)
    tp1       = float(sig.get("tp1_price") or sig.get("take_profit") or 0)

    if not symbol or entry <= 0:
        return None

    ts_ms = _ts_to_ms(ts_str)
    klines = _fetch_klines(symbol, "1h", ts_ms, limit=hold_bars + 5)
    if len(klines) < hold_bars + 1:
        return None

    closes = [float(k[4]) for k in klines]
    highs  = [float(k[2]) for k in klines]
    lows   = [float(k[3]) for k in klines]

    # 逐根检查SL/TP触发
    exit_price = closes[hold_bars]  # 默认：超时平仓
    exit_reason = "TIMEOUT"
    exit_bar    = hold_bars

    for i in range(1, hold_bars + 1):
        if direction == "LONG":
            if sl > 0 and lows[i] <= sl:
                exit_price  = sl
                exit_reason = "SL"
                exit_bar    = i
                break
            if tp1 > 0 and highs[i] >= tp1:
                exit_price  = tp1
                exit_reason = "TP1"
                exit_bar    = i
                break
        else:  # SHORT
            if sl > 0 and highs[i] >= sl:
                exit_price  = sl
                exit_reason = "SL"
                exit_bar    = i
                break
            if tp1 > 0 and lows[i] <= tp1:
                exit_price  = tp1
                exit_reason = "TP1"
                exit_bar    = i
                break

    # 计算毛收益
    if direction == "LONG":
        gross = (exit_price - entry) / entry
    else:
        gross = (entry - exit_price) / entry

    net = apply_cost(gross, symbol, "1h")

    return {
        "symbol":       symbol,
        "direction":    direction,
        "ts":           ts_str,
        "score":        sig.get("score", 0),
        "regime":       sig.get("regime", "?"),
        "entry":        entry,
        "sl":           sl,
        "tp1":          tp1,
        "exit_price":   round(exit_price, 8),
        "exit_reason":  exit_reason,
        "exit_bar":     exit_bar,
        "gross_return": round(gross, 6),
        "net_return":   round(net, 6),
        "win":          net > 0,
    }


# ── 主入口 ────────────────────────────────────────────────────────
def run_signal_replay(
    signal_log: str = str(SIGNAL_LOG),
    output:     str = str(OUTPUT_FILE),
    max_signals: int = 100,
    only_valid:  bool = True,
) -> dict:
    """
    读取 live_signal_log.jsonl，回放所有 valid=True 的信号，
    计算实际 WR/PnL 并与封印值对比。
    """
    log_path = Path(signal_log)
    if not log_path.exists():
        return {"error": f"signal_log not found: {signal_log}"}

    raw_lines = [l for l in log_path.read_text().splitlines() if l.strip()]
    signals   = []
    for line in raw_lines:
        try:
            s = json.loads(line)
            if only_valid and not s.get("valid", False):
                continue
            signals.append(s)
        except Exception:
            continue

    signals = signals[:max_signals]
    print(f"[SignalReplay] 加载 {len(signals)} 条有效信号，开始回放...")

    results   = []
    skipped   = 0
    for i, sig in enumerate(signals):
        r = _replay_one(sig)
        if r is None:
            skipped += 1
            continue
        results.append(r)
        if (i + 1) % 10 == 0:
            print(f"  进度: {i+1}/{len(signals)} ...")
        time.sleep(0.15)   # 避免触发API限速

    import pandas as pd
    if results:
        df = pd.DataFrame(results)
        net_returns = df["net_return"]
        metrics = calc_metrics(net_returns)

        # 体制分层WR
        regime_stats: dict = {}
        for regime, grp in df.groupby("regime"):
            regime_stats[regime] = {
                "trades":   int(len(grp)),
                "win_rate": round(float((grp["net_return"] > 0).mean()), 4),
                "avg_net":  round(float(grp["net_return"].mean()), 6),
            }

        # 封印WR对比（MEMORY.md 关键数值）
        sealed_wr = {
            "BEAR_TREND_SHORT": 0.681,
            "BULL_TREND_LONG":  0.756,
            "CHOP_SHORT":       0.50,
        }
        comparison = {}
        for label, sealed in sealed_wr.items():
            regime_key = label.split("_")[0] + "_" + label.split("_")[1] if "_" in label else label
            actual = regime_stats.get(regime_key, {}).get("win_rate")
            if actual is not None:
                comparison[label] = {
                    "sealed_wr": sealed,
                    "actual_wr": actual,
                    "delta":     round(actual - sealed, 4),
                    "status":    "✅" if abs(actual - sealed) < 0.10 else "⚠️ 偏差>10%",
                }
    else:
        metrics       = calc_metrics(pd.Series(dtype=float))
        regime_stats  = {}
        comparison    = {}

    report = {
        "generated_at":   datetime.now(timezone.utc).isoformat(),
        "signals_loaded": len(signals),
        "signals_replayed": len(results),
        "skipped":        skipped,
        "metrics":        metrics,
        "regime_stats":   regime_stats,
        "sealed_comparison": comparison,
        "trades":         results[:50],  # 最多保存前50条明细
    }

    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[SignalReplay] 完成 → {output}")
    return report
