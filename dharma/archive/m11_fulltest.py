#!/usr/bin/env python3
"""
m11_fulltest.py · 达摩院M11全要素测试
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
设计：
  - BTC/ETH  百倍合约  止损1.2%  多周期
  - SOL/BNB/LTC/DOGE  20倍  止损6%  多周期
  - 周期：15M / 1H / 4H
  - OOS区间：2025-01-01 ~ 2026-05-31
  - IS区间：2018-01-01 ~ 2024-12-31
  - 方向：SHORT（BEAR主导）
  - 输出：每个标的×周期×SL 的 WR/PF/R:R/n
"""
import sys, os, warnings
warnings.filterwarnings('ignore')
import pandas as pd
import numpy as np
from datetime import datetime

DATA    = '/root/.openclaw/workspace/trading-system/dharma/data'
OUT     = '/root/.openclaw/workspace/trading-system/dharma/results'
IS_END  = '2025-01-01'
OOS_END = '2026-06-01'

# ── 标的分组 ───────────────────────────────────────────────
GROUP_A = {  # 百倍合约，止损1.2%
    'syms':    ['btcusdt', 'ethusdt'],
    'sl_pcts': [0.008, 0.010, 0.012, 0.015],   # 止损% (0.8~1.5%)
    'rr':      2.5,
    'label':   '百倍/高精度',
}
GROUP_B = {  # 20倍合约，止损6%
    'syms':    ['solusdt', 'bnbusdt', 'ltcusdt', 'dogeusdt'],
    'sl_pcts': [0.03, 0.04, 0.05, 0.06],       # 止损% (3~6%)
    'rr':      2.5,
    'label':   '20倍/主流币',
}
TFS     = ['15m', '1h', '4h']
GAP_GATES = [0.003, 0.005, 0.008, 0.010, 0.015]

# ── 工具函数 ──────────────────────────────────────────────
def load(sym, tf):
    p = f'{DATA}/{sym}_{tf}_2018_2026.parquet'
    if not os.path.exists(p):
        return None
    df = pd.read_parquet(p)
    df.index = pd.to_datetime(df.index, utc=True)
    return df

def calc_atr(df, n=14):
    h, l, c = df['high'], df['low'], df['close']
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()

def run_backtest(df, sl_pct, gap_gate, rr, direction='SHORT'):
    """
    入场逻辑：
      - 找OB区（近20根K线的最高点附近）
      - gap_gate：现价距OB区间距离 >= gap_gate
      - 触发后：止损=sl_pct，目标=sl_pct×rr
    """
    results = []
    closes = df['close'].values
    highs  = df['high'].values
    lows   = df['low'].values
    n = len(df)
    
    i = 50  # 跳过初始热身
    while i < n - 30:
        price = closes[i]
        
        if direction == 'SHORT':
            # OB = 近20根最高点 作为入场区上沿
            ob_hi = highs[i-20:i].max()
            ob_lo = ob_hi * 0.995
            gap = (ob_hi - price) / price
            
            if gap < gap_gate:
                i += 1
                continue
            
            # 等价格回踩到OB区
            entry = ob_lo
            sl    = entry * (1 + sl_pct)
            tp    = entry * (1 - sl_pct * rr)
            
        else:  # LONG
            ob_lo = lows[i-20:i].min()
            ob_hi = ob_lo * 1.005
            gap = (price - ob_lo) / price
            
            if gap < gap_gate:
                i += 1
                continue
            
            entry = ob_hi
            sl    = entry * (1 - sl_pct)
            tp    = entry * (1 + sl_pct * rr)
        
        # 最多持仓 max_bars 根K线
        max_bars = {'15m': 96, '1h': 24, '4h': 6}.get('4h', 24)
        triggered = False
        
        for j in range(i+1, min(i+max_bars, n)):
            hi_j = highs[j]
            lo_j = lows[j]
            
            # 先检查是否触发入场
            if not triggered:
                if direction == 'SHORT' and hi_j >= entry:
                    triggered = True
                elif direction == 'LONG' and lo_j <= entry:
                    triggered = True
                if not triggered:
                    continue
            
            # 已触发，检查SL/TP
            if direction == 'SHORT':
                if hi_j >= sl:
                    results.append((-sl_pct, 'SL'))
                    i = j + 1
                    break
                if lo_j <= tp:
                    results.append((sl_pct * rr, 'TP'))
                    i = j + 1
                    break
            else:
                if lo_j <= sl:
                    results.append((-sl_pct, 'SL'))
                    i = j + 1
                    break
                if hi_j >= tp:
                    results.append((sl_pct * rr, 'TP'))
                    i = j + 1
                    break
        else:
            i += max_bars
        
        if not triggered:
            i += 1
    
    if not results:
        return None
    
    wins  = [r for r,_ in results if r > 0]
    loss  = [r for r,_ in results if r < 0]
    wr    = len(wins) / len(results) * 100
    gross_profit = sum(wins) if wins else 0
    gross_loss   = abs(sum(loss)) if loss else 0.001
    pf    = gross_profit / gross_loss
    avg_win  = np.mean(wins) * 100 if wins else 0
    avg_loss = abs(np.mean(loss)) * 100 if loss else 0
    
    return {
        'n':     len(results),
        'wr':    round(wr, 1),
        'pf':    round(pf, 2),
        'avg_w': round(avg_win, 2),
        'avg_l': round(avg_loss, 2),
        'rr_actual': round(avg_win / avg_loss, 2) if avg_loss > 0 else 0,
    }

# ── 主训练循环 ─────────────────────────────────────────────
def run_group(group, tfs, gap_gates, phase='IS'):
    label = group['label']
    rr    = group['rr']
    
    print(f"\n{'='*72}")
    print(f"  {label}  ({'IS训练' if phase=='IS' else 'OOS验证'})")
    print(f"{'='*72}")
    
    all_results = {}
    
    for sym in group['syms']:
        best = None
        print(f"\n{'─'*40} {sym.upper()} {'─'*20}")
        
        for tf in tfs:
            df_full = load(sym, tf)
            if df_full is None:
                print(f"  [{tf}] ❌ 数据不存在")
                continue
            
            if phase == 'IS':
                df = df_full[df_full.index < IS_END]
            else:
                df = df_full[(df_full.index >= IS_END) & (df_full.index < OOS_END)]
            
            if len(df) < 200:
                print(f"  [{tf}] ⚠️ 样本不足 n={len(df)}")
                continue
            
            # 找最优SL×Gap组合
            print(f"\n  [{tf.upper()}]  样本={len(df)}根K线")
            print(f"  {'SL%':>6}  " + "  ".join(f"gap={g*100:.1f}%" for g in gap_gates))
            
            tf_best = None
            for sl_pct in group['sl_pcts']:
                row_parts = [f"  {sl_pct*100:.1f}%  "]
                for gap in gap_gates:
                    r = run_backtest(df, sl_pct, gap, rr)
                    if r and r['n'] >= 20:
                        cell = f"{r['wr']:.0f}%/{r['pf']:.1f}"
                        row_parts.append(f"{cell:>10}")
                        if tf_best is None or r['pf'] > tf_best['pf']:
                            tf_best = {**r, 'sl_pct': sl_pct, 'gap': gap, 'tf': tf, 'sym': sym}
                    else:
                        row_parts.append(f"{'  n<20':>10}")
                print("".join(row_parts))
            
            if tf_best:
                print(f"  ★ {tf.upper()}最优: SL={tf_best['sl_pct']*100:.1f}%  Gap={tf_best['gap']*100:.1f}%  WR={tf_best['wr']}%  PF={tf_best['pf']}  n={tf_best['n']}")
                if best is None or tf_best['pf'] > best['pf']:
                    best = tf_best
        
        if best:
            all_results[sym] = best
    
    return all_results

# ── 汇总输出 ──────────────────────────────────────────────
def print_summary(is_results, oos_results, label):
    print(f"\n{'='*72}")
    print(f"  {label} · IS→OOS 汇总")
    print(f"{'='*72}")
    print(f"  {'标的':12} {'周期':5} {'SL%':6} {'Gap%':6} {'IS WR':8} {'IS PF':7} {'OOS WR':8} {'OOS PF':7} {'OOS n':6}")
    print(f"  {'-'*72}")
    
    for sym in is_results:
        ir = is_results[sym]
        or_ = oos_results.get(sym)
        oos_wr  = f"{or_['wr']}%" if or_ else "N/A"
        oos_pf  = f"{or_['pf']}"  if or_ else "N/A"
        oos_n   = f"{or_['n']}"   if or_ else "N/A"
        print(f"  {sym.upper():12} {ir['tf'].upper():5} {ir['sl_pct']*100:.1f}% {ir['gap']*100:.1f}%  "
              f"{ir['wr']:>7}%  {ir['pf']:>7}  {oos_wr:>7}  {oos_pf:>7}  {oos_n:>5}")

# ── 主入口 ────────────────────────────────────────────────
if __name__ == '__main__':
    t0 = datetime.now()
    print(f"{'='*72}")
    print(f"  达摩院 M11 全要素训练")
    print(f"  IS: 2018-01-01 ~ 2024-12-31")
    print(f"  OOS: 2025-01-01 ~ 2026-05-31")
    print(f"  周期: 15M / 1H / 4H")
    print(f"  方向: SHORT (BEAR_TREND主导)")
    print(f"{'='*72}")
    
    # Group A: BTC/ETH
    is_a  = run_group(GROUP_A, TFS, GAP_GATES, 'IS')
    oos_a = run_group(GROUP_A, TFS, GAP_GATES, 'OOS')
    print_summary(is_a, oos_a, GROUP_A['label'])
    
    # Group B: SOL/BNB/LTC/DOGE
    is_b  = run_group(GROUP_B, TFS, GAP_GATES, 'IS')
    oos_b = run_group(GROUP_B, TFS, GAP_GATES, 'OOS')
    print_summary(is_b, oos_b, GROUP_B['label'])
    
    # 全局最优
    print(f"\n{'='*72}")
    print(f"  M11 全局结论")
    print(f"{'='*72}")
    all_oos = {**oos_a, **oos_b}
    ranked = sorted(all_oos.items(), key=lambda x: x[1]['pf'], reverse=True)
    for rank, (sym, r) in enumerate(ranked, 1):
        stars = '🏆' if r['pf'] > 10 else ('✅' if r['pf'] > 5 else '🟡')
        print(f"  #{rank} {stars} {sym.upper():12} {r['tf'].upper():5}  WR={r['wr']}%  PF={r['pf']}  n={r['n']}")
    
    elapsed = (datetime.now() - t0).seconds
    print(f"\n  耗时: {elapsed}s")
    
    # 保存结果
    import json
    out = {
        'meta': {'IS_END': IS_END, 'OOS_END': OOS_END, 'tfs': TFS},
        'group_a': {'is': {k: v for k,v in is_a.items()}, 'oos': {k: v for k,v in oos_a.items()}},
        'group_b': {'is': {k: v for k,v in is_b.items()}, 'oos': {k: v for k,v in oos_b.items()}},
        'ranked': [(s, r) for s, r in ranked],
    }
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    path = f'{OUT}/m11_fulltest_{ts}.json'
    with open(path, 'w') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"  结果已保存: {path}")
