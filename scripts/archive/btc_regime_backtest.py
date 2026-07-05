#!/usr/bin/env python3
"""
BTC Regime-Aligned Trend Strategy — Backtest
=============================================
策略名称: Brahma 体制对齐趋势策略 v1.0

策略逻辑:
  ① 体制过滤（铁律）：
       LONG  → 仅在 BULL_EARLY / BULL_TREND / BULL_CORRECTION 做多
       SHORT → 仅在 BEAR_EARLY / BEAR_TREND 做空
       CHOP/CRASH/BEAR_RECOVERY → 不开仓
  ② 入场信号（EMA + RSI 动量确认）：
       LONG : EMA21 > EMA55 > EMA200（多头排列）+ RSI 回调到 40~58（不追顶）+ 收盘价 > EMA21
       SHORT: EMA21 < EMA55 < EMA200（空头排列）+ RSI 反弹到 42~60（不追杀）+ 收盘价 < EMA21
  ③ 仓位管理：每笔风险固定 1%NAV，SL=1.5×ATR14
  ④ 止盈 / 止损：
       TP1 = 2.0×ATR（平 50% 仓）
       TP2 = 4.0×ATR（平剩余 50% 仓）
       SL  = 1.5×ATR  （硬止损）
       最大持仓：60 根 1H K线（2.5天）
  ⑤ 同一方向每次只持 1 笔仓位，不重复建仓

参数（可调）:
  ATR_SL_MULT = 1.5
  ATR_TP1_MULT = 2.0
  ATR_TP2_MULT = 4.0
  MAX_BARS = 60
  RISK_PCT = 0.01

数据: BTCUSDT_1h_full.parquet（2017-2026, 含预计算 regime/EMA/RSI/ATR）
"""

import pandas as pd
import numpy as np
import json
from datetime import datetime

# ─── 参数 ──────────────────────────────────────────────
ATR_SL_MULT   = 1.5
ATR_TP1_MULT  = 2.0
ATR_TP2_MULT  = 4.0
MAX_BARS      = 60
RISK_PCT      = 0.01
INIT_CAPITAL  = 10_000.0

LONG_REGIMES  = {"BULL_EARLY", "BULL_TREND", "BULL_CORRECTION"}
SHORT_REGIMES = {"BEAR_EARLY", "BEAR_TREND"}

DATA_PATH = "/root/.openclaw/workspace/trading-system/data/backtest/BTCUSDT_1h_full.parquet"
OUT_PATH  = "/root/.openclaw/workspace/trading-system/data/backtest_results/btc_regime_v1.json"

# ─── 加载数据 ─────────────────────────────────────────
df = pd.read_parquet(DATA_PATH)
df = df.dropna(subset=["regime", "ema21", "ema55", "ema200", "rsi", "atr14"])
df = df.sort_index()
# 只回测 2020-01-01 之后（含足够历史数据）
df = df[df.index >= "2020-01-01"]

print(f"[Data] {len(df)} bars  |  {df.index[0].date()} ~ {df.index[-1].date()}")

# ─── 信号生成 ─────────────────────────────────────────
def is_long_signal(row):
    if row["regime"] not in LONG_REGIMES:
        return False
    ema_bull = row["ema21"] > row["ema55"] > row["ema200"]
    rsi_ok   = 40 <= row["rsi"] <= 58
    price_ok = row["close"] > row["ema21"]
    return ema_bull and rsi_ok and price_ok

def is_short_signal(row):
    if row["regime"] not in SHORT_REGIMES:
        return False
    ema_bear = row["ema21"] < row["ema55"] < row["ema200"]
    rsi_ok   = 42 <= row["rsi"] <= 60
    price_ok = row["close"] < row["ema21"]
    return ema_bear and rsi_ok and price_ok

# ─── 回测核心 ─────────────────────────────────────────
trades = []
capital = INIT_CAPITAL
in_trade = False
trade = {}
bars_held = 0

rows = df.itertuples()
data_list = [(row.Index, row.open, row.high, row.low, row.close, row.rsi,
              row.ema21, row.ema55, row.ema200, row.atr14, row.regime)
             for row in df.itertuples()]

i = 0
while i < len(data_list):
    ts, o, h, l, c, rsi, e21, e55, e200, atr, regime = data_list[i]
    row_dict = {"regime": regime, "close": c, "ema21": e21, "ema55": e55,
                "ema200": e200, "rsi": rsi, "atr14": atr}

    if not in_trade:
        # 尝试开仓
        if is_long_signal(row_dict):
            sl  = c - ATR_SL_MULT  * atr
            tp1 = c + ATR_TP1_MULT * atr
            tp2 = c + ATR_TP2_MULT * atr
            risk_per_unit = c - sl
            if risk_per_unit > 0:
                size = (capital * RISK_PCT) / risk_per_unit  # BTC size
                trade = {"open_time": ts, "entry": c, "sl": sl, "tp1": tp1,
                         "tp2": tp2, "dir": "LONG", "size": size,
                         "regime": regime, "tp1_hit": False}
                in_trade = True
                bars_held = 0

        elif is_short_signal(row_dict):
            sl  = c + ATR_SL_MULT  * atr
            tp1 = c - ATR_TP1_MULT * atr
            tp2 = c - ATR_TP2_MULT * atr
            risk_per_unit = sl - c
            if risk_per_unit > 0:
                size = (capital * RISK_PCT) / risk_per_unit
                trade = {"open_time": ts, "entry": c, "sl": sl, "tp1": tp1,
                         "tp2": tp2, "dir": "SHORT", "size": size,
                         "regime": regime, "tp1_hit": False}
                in_trade = True
                bars_held = 0
    else:
        bars_held += 1
        entry = trade["entry"]
        sl    = trade["sl"]
        dir_  = trade["dir"]
        size  = trade["size"]

        exit_price  = None
        exit_reason = None

        if dir_ == "LONG":
            # SL hit?
            if l <= sl:
                exit_price  = sl
                exit_reason = "SL"
            # TP1 hit (first half)?
            elif not trade["tp1_hit"] and h >= trade["tp1"]:
                # 平 50%，记录 TP1 部分
                pnl1 = (trade["tp1"] - entry) / entry * 0.5
                capital += pnl1 * capital
                trade["tp1_hit"] = True
                trade["size"]    = size * 0.5
                # 继续持仓等 TP2
                i += 1
                continue
            # TP2 hit?
            elif trade["tp1_hit"] and h >= trade["tp2"]:
                exit_price  = trade["tp2"]
                exit_reason = "TP2"
            # TIMEOUT?
            elif bars_held >= MAX_BARS:
                exit_price  = c
                exit_reason = "TIMEOUT"
        else:  # SHORT
            if h >= sl:
                exit_price  = sl
                exit_reason = "SL"
            elif not trade["tp1_hit"] and l <= trade["tp1"]:
                pnl1 = (entry - trade["tp1"]) / entry * 0.5
                capital += pnl1 * capital
                trade["tp1_hit"] = True
                trade["size"]    = size * 0.5
                i += 1
                continue
            elif trade["tp1_hit"] and l <= trade["tp2"]:
                exit_price  = trade["tp2"]
                exit_reason = "TP2"
            elif bars_held >= MAX_BARS:
                exit_price  = c
                exit_reason = "TIMEOUT"

        if exit_price is not None:
            if dir_ == "LONG":
                pnl_pct = (exit_price - entry) / entry
                if trade["tp1_hit"]:
                    pnl_pct = pnl_pct * 0.5  # 已平 50%，剩余 50%
            else:
                pnl_pct = (entry - exit_price) / entry
                if trade["tp1_hit"]:
                    pnl_pct = pnl_pct * 0.5

            pnl_dollar = pnl_pct * capital
            capital += pnl_dollar

            trades.append({
                "open_time":   str(trade["open_time"]),
                "close_time":  str(ts),
                "dir":         dir_,
                "regime":      trade["regime"],
                "entry":       round(entry, 2),
                "exit":        round(exit_price, 2),
                "pnl_pct":     round(pnl_pct * 100, 4),
                "pnl_dollar":  round(pnl_dollar, 4),
                "exit_reason": exit_reason,
                "tp1_hit":     trade["tp1_hit"],
                "capital_after": round(capital, 2),
                "bars":        bars_held,
            })
            in_trade    = False
            trade       = {}
            bars_held   = 0

    i += 1

# ─── 统计 ─────────────────────────────────────────────
df_t = pd.DataFrame(trades)
total = len(df_t)
wins  = (df_t["pnl_pct"] > 0).sum()
losses= (df_t["pnl_pct"] < 0).sum()
wr    = wins / total * 100 if total else 0

gross_win  = df_t[df_t["pnl_pct"]>0]["pnl_pct"].sum()
gross_loss = abs(df_t[df_t["pnl_pct"]<0]["pnl_pct"].sum())
pf         = round(gross_win / gross_loss, 4) if gross_loss > 0 else float("inf")

avg_win  = round(df_t[df_t["pnl_pct"]>0]["pnl_pct"].mean(), 4)
avg_loss = round(df_t[df_t["pnl_pct"]<0]["pnl_pct"].mean(), 4)

total_return = (capital - INIT_CAPITAL) / INIT_CAPITAL * 100

# Max drawdown
equity = [INIT_CAPITAL] + df_t["capital_after"].tolist()
equity_s = pd.Series(equity)
rolling_max = equity_s.cummax()
drawdown    = (equity_s - rolling_max) / rolling_max * 100
max_dd      = round(drawdown.min(), 4)

# Sharpe (monthly returns proxy)
df_t["close_time"] = pd.to_datetime(df_t["close_time"])
df_t["month"] = df_t["close_time"].dt.to_period("M")
monthly_pnl = df_t.groupby("month")["pnl_pct"].sum()
sharpe = round(monthly_pnl.mean() / monthly_pnl.std() * (12**0.5), 4) if monthly_pnl.std() > 0 else 0

# By regime
regime_stats = {}
for r, g in df_t.groupby("regime"):
    w = (g["pnl_pct"] > 0).sum()
    regime_stats[r] = {
        "n": len(g),
        "wr_pct": round(w/len(g)*100, 1),
        "avg_pnl_pct": round(g["pnl_pct"].mean(), 4),
        "total_pnl_pct": round(g["pnl_pct"].sum(), 4),
    }

# Exit reason
exit_counts = df_t["exit_reason"].value_counts().to_dict()
dir_counts  = df_t["dir"].value_counts().to_dict()

result = {
    "strategy": "Brahma Regime-Aligned Trend v1.0",
    "symbol": "BTCUSDT",
    "interval": "1H",
    "period": f"{df.index[0].date()} ~ {df.index[-1].date()}",
    "initial_capital": INIT_CAPITAL,
    "final_capital": round(capital, 2),
    "total_return_pct": round(total_return, 2),
    "max_drawdown_pct": max_dd,
    "sharpe_ratio": sharpe,
    "stats": {
        "total_trades": total,
        "win_trades": int(wins),
        "loss_trades": int(losses),
        "win_rate_pct": round(wr, 2),
        "profit_factor": pf,
        "avg_win_pct": avg_win,
        "avg_loss_pct": avg_loss,
        "exit_reasons": exit_counts,
        "direction_split": dir_counts,
    },
    "regime_breakdown": regime_stats,
    "trades": trades,
    "run_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
}

with open(OUT_PATH, "w") as f:
    json.dump(result, f, indent=2, ensure_ascii=False)

# ─── 打印摘要 ─────────────────────────────────────────
print("\n" + "="*55)
print("  Brahma 体制对齐趋势策略 v1.0  —  BTC 1H 回测报告")
print("="*55)
print(f"  期间      : {result['period']}")
print(f"  总收益    : {result['total_return_pct']:+.2f}%  (${INIT_CAPITAL:,.0f} → ${capital:,.0f})")
print(f"  最大回撤  : {max_dd:.2f}%")
print(f"  夏普比率  : {sharpe}")
print(f"  总交易次数: {total}  (多:{dir_counts.get('LONG',0)}  空:{dir_counts.get('SHORT',0)})")
print(f"  胜率      : {wr:.2f}%   ({int(wins)}W / {int(losses)}L)")
print(f"  盈亏比    : PF={pf}")
print(f"  均赢      : +{avg_win}%   均亏: {avg_loss}%")
print(f"  离场原因  : {exit_counts}")
print("\n  [ 体制拆解 ]")
for r, s in sorted(regime_stats.items(), key=lambda x: -x[1]["total_pnl_pct"]):
    print(f"    {r:<22} n={s['n']:3d}  WR={s['wr_pct']:5.1f}%  avg={s['avg_pnl_pct']:+.4f}%  sum={s['total_pnl_pct']:+.2f}%")
print("="*55)
print(f"  结果已写入: {OUT_PATH}")
