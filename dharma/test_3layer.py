#!/usr/bin/env python3
"""
test_3layer.py · 达摩院三层方案回测
设计院 2026-06-01

三层架构：
  Layer 1: 4H 体制识别 (BEAR_EARLY/CHOP等)
  Layer 2: 1H OB/FVG 入场区定位
  Layer 3: 15M CHoCH 精确触发

对比：
  A. 当前方案（1H单层）
  B. 新方案（15M+1H+4H三层）

标的：BTC/ETH（有完整15M OOS数据）
OOS窗口：2025-01-01 ~ 2026-05-28
IS 截止：2024-12-31
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from datetime import timezone

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
IS_CUTOFF  = '2025-01-01'
SYMBOLS    = ['btcusdt', 'ethusdt']

# ── 参数 ─────────────────────────────────────────────────────
RR_TARGET   = 2.5      # TP = entry ± SL × RR
SL_MULT_1H  = 1.5      # 1H方案：ATR×1.5止损
SL_MULT_15M = 0.8      # 15M方案：收窄至ATR×0.8
MAX_BARS_1H = 48       # 超时：48根1H
MAX_BARS_15M= 24       # 超时：24根15M（等价6H）
GAP_MIN     = 0.005    # GapGate: <0.5% 拒绝
MIN_SCORE   = 0        # 回测不依赖AI评分，测纯结构信号

print("=" * 62)
print("  达摩院三层方案回测  (IS截止=2025-01-01)")
print("=" * 62)

# ── 工具函数 ──────────────────────────────────────────────────
def load(sym, tf):
    path = os.path.join(DATA_DIR, f'{sym}_{tf}_2018_2026.parquet')
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index, utc=True)
    return df

def ema(s, n): return s.ewm(span=n, adjust=False).mean()

def calc_atr(df, n=14):
    h,l,c = df['high'], df['low'], df['close']
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()

def detect_regime_4h(df4h, i):
    """
    简化体制识别（基于4H EMA结构）
    返回: BEAR_EARLY / BEAR_TREND / BULL_TREND / CHOP_MID
    """
    if i < 50: return 'CHOP_MID'
    w = df4h.iloc[i-50:i+1]
    c = w['close'].values
    e20 = float(ema(pd.Series(c), 20).iloc[-1])
    e50 = float(ema(pd.Series(c), 50).iloc[-1])
    price = c[-1]
    # 计算近20根4H涨跌
    chg = (price - c[-20]) / c[-20]
    if price < e20 < e50:
        return 'BEAR_TREND' if chg < -0.08 else 'BEAR_EARLY'
    if price > e20 > e50:
        return 'BULL_TREND'
    return 'CHOP_MID'

def find_ob_1h(df1h, i, direction):
    """在最近40根1H K线找OB入场区"""
    if i < 10: return None, None
    w = df1h.iloc[max(0,i-40):i+1]
    o,h,l,c = w['open'].values, w['high'].values, w['low'].values, w['close'].values
    if direction == 'SHORT':
        # 找最近一次：阳线→阴线 (熊OB)
        for j in range(len(c)-2, 2, -1):
            if (c[j-1]-o[j-1])/o[j-1] > 0.003 and c[j] < o[j]:
                return h[j-1], l[j-1]   # OB = 前阳线 high/low
    else:
        # 阴线→阳线 (牛OB)
        for j in range(len(c)-2, 2, -1):
            if (o[j-1]-c[j-1])/o[j-1] > 0.003 and c[j] > o[j]:
                return h[j-1], l[j-1]
    return None, None

def find_choch_15m(df15m, i, direction):
    """检测最近30根15M K线是否出现CHoCH"""
    if i < 10: return False
    w = df15m.iloc[max(0,i-30):i+1]
    h = w['high'].values
    l = w['low'].values
    c = w['close'].values
    n = len(c)
    if direction == 'SHORT':
        # 连续LH + 跌破近期低点
        lh = sum(1 for x in range(n-3, n-1) if h[x] < h[x-2])
        recent_low = l[-5:].min()
        return lh >= 1 and c[-1] < recent_low * 1.002
    else:
        hl = sum(1 for x in range(n-3, n-1) if l[x] > l[x-2])
        recent_high = h[-5:].max()
        return hl >= 1 and c[-1] > recent_high * 0.998

# ── 核心回测函数 ──────────────────────────────────────────────
def backtest(sym, scheme='1H'):
    """
    scheme: '1H' = 当前单层方案
            '3L' = 三层方案（15M+1H+4H）
    """
    df1h = load(sym, '1h')
    df4h = load(sym, '4h') if scheme == '3L' else None
    df15m= load(sym, '15m') if scheme == '3L' else None

    oos = df1h[df1h.index >= IS_CUTOFF].copy()
    atr1h = calc_atr(df1h, 14).reindex(df1h.index)

    trades = []
    skip_until = None

    for i in range(50, len(df1h)):
        ts = df1h.index[i]
        if ts < pd.Timestamp(IS_CUTOFF, tz='UTC'):
            continue
        if skip_until and ts < skip_until:
            continue

        price = df1h['close'].iloc[i]
        atr   = atr1h.iloc[i]
        if pd.isna(atr) or atr <= 0: continue

        # ── 方向识别（1H RSI简化）──
        rsi_w = df1h['close'].iloc[max(0,i-14):i+1]
        delta = rsi_w.diff()
        gain  = delta.clip(lower=0).mean()
        loss  = (-delta.clip(upper=0)).mean()
        rsi   = 100 - 100/(1+gain/loss) if loss > 0 else 50
        if rsi > 55: direction = 'SHORT'
        elif rsi < 45: direction = 'LONG'
        else: continue  # 中性不做

        if scheme == '3L':
            # ── Layer 1: 4H体制过滤 ──────────────────────────
            ts4h_idx = df4h.index.searchsorted(ts)
            if ts4h_idx < 50: continue
            regime = detect_regime_4h(df4h, ts4h_idx-1)
            # 体制封锁
            if (regime == 'BEAR_TREND' and direction == 'SHORT'): pass  # 允许
            if (regime == 'BULL_TREND' and direction == 'LONG'):  pass  # 允许
            if (regime == 'BEAR_TREND' and direction == 'LONG'):  continue
            if (regime == 'BULL_TREND' and direction == 'SHORT'): continue
            if regime == 'CHOP_MID' and direction == 'SHORT':    continue  # CHOP不做短

            # ── Layer 2: 1H OB入场区 ─────────────────────────
            ob_hi, ob_lo = find_ob_1h(df1h, i, direction)
            if ob_hi is None: continue

            if direction == 'SHORT':
                entry_lo = max(ob_lo, price * 1.002)
                entry_hi = ob_hi
                if entry_hi <= entry_lo: continue
                gap = (entry_lo - price) / price
            else:
                entry_lo = ob_lo
                entry_hi = min(ob_hi, price * 0.998)
                if entry_hi <= entry_lo: continue
                gap = (price - entry_hi) / price

            if gap < GAP_MIN: continue  # GapGate

            # ── Layer 3: 15M CHoCH确认 ────────────────────────
            ts15_idx = df15m.index.searchsorted(ts)
            if ts15_idx < 30: continue
            confirmed = find_choch_15m(df15m, ts15_idx-1, direction)
            if not confirmed: continue

            entry = (entry_lo + entry_hi) / 2
            sl_dist = atr * SL_MULT_15M
        else:
            # ── 1H单层方案 ───────────────────────────────────
            if rsi > 55: entry = price * 1.010
            else:        entry = price * 0.990
            sl_dist = atr * SL_MULT_1H

        # ── 止损/TP ──────────────────────────────────────────
        if direction == 'SHORT':
            sl  = entry + sl_dist
            tp1 = entry - sl_dist * RR_TARGET
        else:
            sl  = entry - sl_dist
            tp1 = entry + sl_dist * RR_TARGET

        if tp1 <= 0: continue

        # ── 逐K结算 ──────────────────────────────────────────
        max_bars = MAX_BARS_15M if scheme == '3L' else MAX_BARS_1H
        result = 'TIMEOUT'
        for j in range(i+1, min(i+1+max_bars, len(df1h))):
            hi = df1h['high'].iloc[j]
            lo = df1h['low'].iloc[j]
            if direction == 'SHORT':
                if hi >= sl:
                    result = 'SL'; break
                if lo <= tp1:
                    result = 'TP'; break
            else:
                if lo <= sl:
                    result = 'SL'; break
                if hi >= tp1:
                    result = 'TP'; break

        if result == 'TIMEOUT': continue

        pnl = RR_TARGET if result == 'TP' else -1.0
        trades.append({'ts': ts, 'dir': direction, 'result': result,
                        'pnl': pnl, 'price': price})
        # 冷却90分钟
        skip_until = ts + pd.Timedelta(minutes=90)

    return pd.DataFrame(trades)

# ── 执行 ─────────────────────────────────────────────────────
results = {}
for sym in SYMBOLS:
    print(f"\n{'─'*30} {sym.upper()} {'─'*30}")
    for scheme, label in [('1H','当前1H单层'), ('3L','新三层15M+1H+4H')]:
        df = backtest(sym, scheme)
        if len(df) == 0:
            print(f"  [{label}] 无交易记录")
            continue
        n     = len(df)
        wins  = (df['result']=='TP').sum()
        wr    = wins/n*100
        pf    = df[df['pnl']>0]['pnl'].sum() / max(abs(df[df['pnl']<0]['pnl'].sum()), 1e-9)
        total = df['pnl'].sum()
        print(f"  [{label}]  n={n:3d}  WR={wr:.1f}%  PF={pf:.2f}  总盈亏={total:+.1f}R")
        results[f"{sym}_{scheme}"] = dict(n=n, wr=wr, pf=pf, total=total)

# ── 汇总 ─────────────────────────────────────────────────────
print(f"\n{'='*62}")
print("  汇总对比  (OOS 2025-01-01 ~ 2026-05-28)")
print(f"{'='*62}")
print(f"{'标的+方案':<22} {'笔数':>6} {'WR':>7} {'PF':>7} {'总R':>7}")
print(f"{'─'*22} {'─'*6} {'─'*7} {'─'*7} {'─'*7}")
for k,v in results.items():
    tag = '⬅ 当前' if '1H' in k else '🆕 三层'
    sym_tag = k.replace('_1H','').replace('_3L','').upper()
    layer = '1H单层' if '1H' in k else '15M+1H+4H'
    print(f"  {sym_tag} {layer:<12} {v['n']:>5}  {v['wr']:>5.1f}%  {v['pf']:>6.2f}  {v['total']:>+6.1f}R  {tag}")
print()
