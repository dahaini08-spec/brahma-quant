#!/usr/bin/env python3
"""
signal_history_scorer.py
梵天 v5.1 · 历史胜率引用层（Attention Residue类比）
苏摩111封印 2026-07-19

原理：分析当前信号时，主动从历史信号中挑选有用信息
      - 同标的同体制历史胜率 → score加减分
      - 该入场区历史被扫止损记录 → score减分
      - 历史平均持仓时长 → 优化持仓建议

数据来源：data/live_signal_log.jsonl
"""
import json
import time
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).parent.parent
SIGNAL_LOG = BASE / "data" / "live_signal_log.jsonl"
LOOKBACK_DAYS = 30  # 引用最近30天历史


def load_history(symbol: str, regime: str, direction: str) -> list[dict]:
    """加载同标的同体制同方向的历史信号"""
    if not SIGNAL_LOG.exists():
        return []

    cutoff = time.time() - LOOKBACK_DAYS * 86400
    results = []

    with open(SIGNAL_LOG) as f:
        for line in f:
            try:
                d = json.loads(line.strip())
                if (
                    d.get("symbol") == symbol
                    and d.get("regime") == regime
                    and d.get("direction", d.get("signal_dir", "")) == direction
                    and d.get("ts", 0) > cutoff
                    and d.get("result") is not None
                    and d.get("result") not in ("EXPIRED", "TIMEOUT", "REGIME_EXPIRED", "PRICE_EXPIRED")  # 排除超时，只看真实结果
                ):
                    results.append(d)
            except Exception:
                pass

    return results


def get_history_score_adjustment(
    symbol: str,
    regime: str,
    direction: str,
    entry_lo: float,
    entry_hi: float,
) -> tuple[float, str]:
    """
    Attention Residue：从历史信号中挑选有用信息
    返回: (score_adjustment, reason)
    """
    history = load_history(symbol, regime, direction)
    adjustment = 0.0
    reasons = []

    if len(history) < 3:
        return 0.0, f"历史样本不足({len(history)}条，需≥3)"

    # ① 历史胜率
    wins = sum(1 for h in history if h.get("result") == "WIN")
    total = len(history)
    wr = wins / total

    if wr >= 0.70:
        adjustment += 12
        reasons.append(f"历史WR={wr:.0%}(n={total})+12")
    elif wr >= 0.60:
        adjustment += 6
        reasons.append(f"历史WR={wr:.0%}(n={total})+6")
    elif wr < 0.45:
        adjustment -= 10
        reasons.append(f"历史WR={wr:.0%}(n={total})-10")
    elif wr < 0.55:
        adjustment -= 4
        reasons.append(f"历史WR={wr:.0%}(n={total})-4")

    # ② 该入场区历史被扫止损次数
    sl_hits = 0
    for h in history:
        h_lo = float(h.get("entry_lo", 0))
        h_hi = float(h.get("entry_hi", 0))
        # 入场区重叠判断（±2%内视为同区间）
        overlap = not (entry_hi < h_lo * 0.98 or entry_lo > h_hi * 1.02)
        if overlap and h.get("result") == "SL_HIT":
            sl_hits += 1

    if sl_hits >= 3:
        adjustment -= 15
        reasons.append(f"该区间历史扫止损{sl_hits}次-15")
    elif sl_hits >= 2:
        adjustment -= 8
        reasons.append(f"该区间历史扫止损{sl_hits}次-8")
    elif sl_hits == 0 and total >= 5:
        adjustment += 5
        reasons.append(f"该区间历史未被扫止损+5")

    # ③ 历史平均PnL
    pnls = [float(h.get("pnl_pct", 0)) for h in history if h.get("pnl_pct") is not None]
    if pnls:
        avg_pnl = sum(pnls) / len(pnls)
        if avg_pnl > 0.02:
            adjustment += 5
            reasons.append(f"历史均盈{avg_pnl:.1%}+5")
        elif avg_pnl < -0.01:
            adjustment -= 5
            reasons.append(f"历史均亏{avg_pnl:.1%}-5")

    reason_str = " | ".join(reasons) if reasons else "历史记录中性"
    return round(adjustment, 1), reason_str


def get_symbol_summary(symbol: str) -> str:
    """返回标的历史表现摘要"""
    lines = [f"📈 {symbol} 历史信号摘要(30天)"]

    for regime in ["BULL_TREND", "BEAR_TREND", "CHOP_MID"]:
        for direction in ["LONG", "SHORT"]:
            history = load_history(symbol, regime, direction)
            if len(history) >= 3:
                wins = sum(1 for h in history if h.get("result") == "WIN")
                wr = wins / len(history)
                lines.append(f"  {regime} {direction}: WR={wr:.0%} n={len(history)}")

    return "\n".join(lines) if len(lines) > 1 else f"{symbol}: 暂无足够历史数据"


if __name__ == "__main__":
    import sys
    sym = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT"
    print(get_symbol_summary(sym))
    adj, reason = get_history_score_adjustment(sym, "BULL_TREND", "LONG", 64000, 64423)
    print(f"\nBULL_TREND LONG历史调整: {adj:+.1f} | {reason}")
