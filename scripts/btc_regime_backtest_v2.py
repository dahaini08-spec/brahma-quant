#!/usr/bin/env python3
"""
BTC Regime-Aligned EMA Crossover Strategy — Backtest v2
=========================================================
策略名称: Brahma 体制对齐 EMA21穿越策略 v2.0

策略逻辑（精简，高胜率优先）:
──────────────────────────────────────────────
  体制门控 (铁律)
  ─────────────────
  LONG  → BULL_EARLY / BULL_TREND / BULL_CORRECTION
  SHORT → BEAR_EARLY / BEAR_TREND
  其余体制 → 不开仓

  入场信号（必须同时满足）
  ─────────────────
  LONG :
    ① 体制在多头允许列表
    ② EMA21 > EMA55 (大趋势多头，不要求全排列)
    ③ 前一根收盘 < EMA21 且当前收盘 > EMA21（金叉穿越，进入EMA21上方）
    ④ RSI < 65（避免追顶）
    ⑤ ATR > ATR_min（避免无波动区间建仓）

  SHORT:
    ① 体制在空头允许列表
    ② EMA21 < EMA55
    ③ 前一根收盘 > EMA21 且当前收盘 < EMA21（死叉穿越，跌破EMA21）
    ④ RSI > 35（避免追杀）
    ⑤ ATR > ATR_min

  止盈/止损（动态 ATR 定位）
  ─────────────────
  SL  = entry ∓ 2.0×ATR14
  TP1 = entry ± 2.5×ATR14  → 平仓 50%
  TP2 = entry ± 5.0×ATR14  → 平仓 50%
  最大持仓: 72H = 72根1H K线

  仓位控制
  ─────────────────
  每笔固定风险: 1% NAV
  size(BTC) = capital × 1% / (SL距离)

  冷却期
  ─────────────────
  同方向 SL 出场后，冷却 12 根 K 线再开新仓（避免连续止损）
"""

import pandas as pd
import numpy as np
import json
from datetime import datetime

# ─── 参数 ──────────────────────────────────────────────
ATR_SL_MULT   = 2.0
ATR_TP1_MULT  = 2.5
ATR_TP2_MULT  = 5.0
MAX_BARS      = 72
RISK_PCT      = 0.01
INIT_CAPITAL  = 10_000.0
COOLDOWN_SL   = 12   # SL后冷却K线数
ATR_MIN_PCT   = 0.003  # ATR / close > 0.3% 才建仓（过滤低波动）

LONG_REGIMES  = {"BULL_EARLY", "BULL_TREND", "BULL_CORRECTION"}
SHORT_REGIMES = {"BEAR_EARLY", "BEAR_TREND"}

DATA_PATH = "/root/.openclaw/workspace/trading-system/data/backtest/BTCUSDT_1h_full.parquet"
OUT_PATH  = "/root/.openclaw/workspace/trading-system/data/backtest_results/btc_regime_v2.json"

# ─── 加载数据 ─────────────────────────────────────────
df = pd.read_parquet(DATA_PATH)
df = df.dropna(subset=["regime", "ema21", "ema55", "rsi", "atr14"])
df = df.sort_index()
df = df[df.index >= "2020-01-01"]

# 前一根收盘（shift 1）
df["prev_close"] = df["close"].shift(1)
df["prev_ema21"] = df["ema21"].shift(1)
df = df.dropna(subset=["prev_close", "prev_ema21"])

print(f"[Data] {len(df)} bars  |  {df.index[0].date()} ~ {df.index[-1].date()}")

# ─── 信号生成 ─────────────────────────────────────────
def signal(row):
    atr_ratio = row["atr14"] / row["close"]
    if atr_ratio < ATR_MIN_PCT:
        return None  # 波动太低，跳过

    # LONG: 穿越 EMA21 向上
    if row["regime"] in LONG_REGIMES:
        if (row["ema21"] > row["ema55"] and
            row["prev_close"] < row["prev_ema21"] and
            row["close"] > row["ema21"] and
            row["rsi"] < 65):
            return "LONG"

    # SHORT: 跌破 EMA21 向下
    elif row["regime"] in SHORT_REGIMES:
        if (row["ema21"] < row["ema55"] and
            row["prev_close"] > row["prev_ema21"] and
            row["close"] < row["ema21"] and
            row["rsi"] > 35):
            return "SHORT"
    return None

# ─── 回测 ─────────────────────────────────────────────
trades = []
capital = INIT_CAPITAL
in_trade = False
trade = {}
bars_held = 0
cooldown_long  = 0
cooldown_short = 0

data_list = list(df.itertuples())

i = 0
while i < len(data_list):
    row = data_list[i]
    ts     = row.Index
    c      = row.close
    h      = row.high
    l      = row.low
    atr    = row.atr14
    regime = row.regime

    row_dict = {k: getattr(row, k) for k in
                ["regime","close","ema21","ema55","rsi","atr14","prev_close","prev_ema21"]}

    # 冷却计数
    if cooldown_long  > 0: cooldown_long  -= 1
    if cooldown_short > 0: cooldown_short -= 1

    if not in_trade:
        sig = signal(row_dict)
        if sig == "LONG" and cooldown_long == 0:
            sl  = c - ATR_SL_MULT  * atr
            tp1 = c + ATR_TP1_MULT * atr
            tp2 = c + ATR_TP2_MULT * atr
            risk = c - sl
            if risk > 0:
                size = (capital * RISK_PCT) / risk
                trade = dict(open_time=ts, entry=c, sl=sl, tp1=tp1, tp2=tp2,
                             dir="LONG", size=size, regime=regime, tp1_hit=False,
                             tp1_pnl=0.0)
                in_trade = True; bars_held = 0
        elif sig == "SHORT" and cooldown_short == 0:
            sl  = c + ATR_SL_MULT  * atr
            tp1 = c - ATR_TP1_MULT * atr
            tp2 = c - ATR_TP2_MULT * atr
            risk = sl - c
            if risk > 0:
                size = (capital * RISK_PCT) / risk
                trade = dict(open_time=ts, entry=c, sl=sl, tp1=tp1, tp2=tp2,
                             dir="SHORT", size=size, regime=regime, tp1_hit=False,
                             tp1_pnl=0.0)
                in_trade = True; bars_held = 0
    else:
        bars_held += 1
        entry = trade["entry"]
        sl    = trade["sl"]
        tp1   = trade["tp1"]
        tp2   = trade["tp2"]
        dir_  = trade["dir"]
        size  = trade["size"]

        exit_price  = None
        exit_reason = None

        if dir_ == "LONG":
            if l <= sl:
                exit_price = sl; exit_reason = "SL"
            elif not trade["tp1_hit"] and h >= tp1:
                # TP1: 平 50%，记录 PnL
                pnl1 = 0.5 * size * (tp1 - entry)
                capital += pnl1
                trade["tp1_hit"] = True
                trade["tp1_pnl"] = pnl1
                trade["size"]    = size * 0.5
                i += 1; continue
            elif trade["tp1_hit"] and h >= tp2:
                exit_price = tp2; exit_reason = "TP2"
            elif bars_held >= MAX_BARS:
                exit_price = c; exit_reason = "TIMEOUT"
        else:
            if h >= sl:
                exit_price = sl; exit_reason = "SL"
            elif not trade["tp1_hit"] and l <= tp1:
                pnl1 = 0.5 * size * (entry - tp1)
                capital += pnl1
                trade["tp1_hit"] = True
                trade["tp1_pnl"] = pnl1
                trade["size"]    = size * 0.5
                i += 1; continue
            elif trade["tp1_hit"] and l <= tp2:
                exit_price = tp2; exit_reason = "TP2"
            elif bars_held >= MAX_BARS:
                exit_price = c; exit_reason = "TIMEOUT"

        if exit_price is not None:
            cur_size = trade["size"]
            if dir_ == "LONG":
                pnl2 = cur_size * (exit_price - entry)
            else:
                pnl2 = cur_size * (entry - exit_price)

            total_pnl_dollar = trade["tp1_pnl"] + pnl2
            capital += pnl2

            total_entry_value = trade["size"] / (0.5 if trade["tp1_hit"] else 1) * entry
            pnl_pct = total_pnl_dollar / (total_entry_value) * 100 if total_entry_value > 0 else 0

            trades.append({
                "open_time":      str(trade["open_time"]),
                "close_time":     str(ts),
                "dir":            dir_,
                "regime":         trade["regime"],
                "entry":          round(entry, 2),
                "exit":           round(exit_price, 2),
                "pnl_pct":        round(pnl_pct, 4),
                "pnl_dollar":     round(total_pnl_dollar, 4),
                "exit_reason":    exit_reason,
                "tp1_hit":        trade["tp1_hit"],
                "capital_after":  round(capital, 2),
                "bars":           bars_held,
            })

            if exit_reason == "SL":
                if dir_ == "LONG":  cooldown_long  = COOLDOWN_SL
                else:               cooldown_short = COOLDOWN_SL

            in_trade = False; trade = {}; bars_held = 0

    i += 1

# ─── 统计 ─────────────────────────────────────────────
df_t = pd.DataFrame(trades)
total = len(df_t)
wins  = (df_t["pnl_dollar"] > 0).sum()
losses= (df_t["pnl_dollar"] <= 0).sum()
wr    = wins / total * 100 if total else 0

gross_win  = df_t[df_t["pnl_dollar"]>0]["pnl_dollar"].sum()
gross_loss = abs(df_t[df_t["pnl_dollar"]<=0]["pnl_dollar"].sum())
pf         = round(gross_win / gross_loss, 4) if gross_loss > 0 else float("inf")

avg_win_d  = round(df_t[df_t["pnl_dollar"]>0]["pnl_dollar"].mean(), 2)
avg_loss_d = round(df_t[df_t["pnl_dollar"]<=0]["pnl_dollar"].mean(), 2)

total_return = (capital - INIT_CAPITAL) / INIT_CAPITAL * 100

# Max drawdown
equity_list = [INIT_CAPITAL]
for t in trades:
    equity_list.append(t["capital_after"])
eq = pd.Series(equity_list)
rolling_max = eq.cummax()
dd = (eq - rolling_max) / rolling_max * 100
max_dd = round(dd.min(), 4)

# Sharpe
df_t["close_time_dt"] = pd.to_datetime(df_t["close_time"]).dt.tz_localize(None)
df_t["month"] = df_t["close_time_dt"].dt.to_period("M")
monthly_pnl = df_t.groupby("month")["pnl_dollar"].sum() / INIT_CAPITAL * 100
sharpe = round(monthly_pnl.mean() / monthly_pnl.std() * (12**0.5), 4) if monthly_pnl.std() > 0 else 0

# Regime breakdown
regime_stats = {}
for r, g in df_t.groupby("regime"):
    w = (g["pnl_dollar"] > 0).sum()
    regime_stats[r] = {
        "n": len(g),
        "wr_pct": round(w/len(g)*100, 1),
        "avg_pnl_dollar": round(g["pnl_dollar"].mean(), 2),
        "total_pnl_dollar": round(g["pnl_dollar"].sum(), 2),
    }

exit_counts = df_t["exit_reason"].value_counts().to_dict()
dir_counts  = df_t["dir"].value_counts().to_dict()

result = {
    "strategy": "Brahma Regime-Aligned EMA21 Crossover v2.0",
    "symbol":   "BTCUSDT",
    "interval": "1H",
    "period":   f"{df.index[0].date()} ~ {df.index[-1].date()}",
    "params": {
        "ATR_SL_MULT": ATR_SL_MULT, "ATR_TP1_MULT": ATR_TP1_MULT,
        "ATR_TP2_MULT": ATR_TP2_MULT, "MAX_BARS": MAX_BARS,
        "RISK_PCT": RISK_PCT, "COOLDOWN_SL": COOLDOWN_SL,
        "ATR_MIN_PCT": ATR_MIN_PCT,
    },
    "initial_capital": INIT_CAPITAL,
    "final_capital":   round(capital, 2),
    "total_return_pct": round(total_return, 2),
    "max_drawdown_pct": max_dd,
    "sharpe_ratio": sharpe,
    "stats": {
        "total_trades": total,
        "win_trades":   int(wins),
        "loss_trades":  int(losses),
        "win_rate_pct": round(wr, 2),
        "profit_factor": pf,
        "avg_win_dollar":  avg_win_d,
        "avg_loss_dollar": avg_loss_d,
        "exit_reasons":    exit_counts,
        "direction_split": dir_counts,
    },
    "regime_breakdown": regime_stats,
    "trades": trades,
    "run_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
}

with open(OUT_PATH, "w") as f:
    json.dump(result, f, indent=2, ensure_ascii=False)

# ─── 打印摘要 ─────────────────────────────────────────
print("\n" + "="*60)
print("  Brahma 体制对齐 EMA21穿越策略 v2.0  —  BTC 1H 回测报告")
print("="*60)
print(f"  期间        : {result['period']}")
print(f"  总收益      : {result['total_return_pct']:+.2f}%  (${INIT_CAPITAL:,.0f} → ${capital:,.2f})")
print(f"  最大回撤    : {max_dd:.2f}%")
print(f"  年化夏普    : {sharpe}")
print(f"  总交易次数  : {total}  (多:{dir_counts.get('LONG',0)}  空:{dir_counts.get('SHORT',0)})")
print(f"  胜率        : {wr:.2f}%   ({int(wins)}W / {int(losses)}L)")
print(f"  盈亏比(PF)  : {pf}")
print(f"  均赢(美元)  : +${avg_win_d}   均亏: -${abs(avg_loss_d)}")
print(f"  离场原因    : {exit_counts}")
print("\n  [ 体制拆解 ]")
for r, s in sorted(regime_stats.items(), key=lambda x: -x[1]["total_pnl_dollar"]):
    print(f"    {r:<22} n={s['n']:3d}  WR={s['wr_pct']:5.1f}%  avg=${s['avg_pnl_dollar']:+.2f}  sum=${s['total_pnl_dollar']:+.2f}")
print("="*60)
print(f"  结果已写入: {OUT_PATH}")
