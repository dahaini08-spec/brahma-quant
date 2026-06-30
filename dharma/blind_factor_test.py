#!/usr/bin/env python3
"""
达摩院量化研究引擎 - 全指标盲测
BTC+ETH 8年期货数据 (1H主力 + 4H体制)
"""

import pandas as pd
import numpy as np
from itertools import combinations
import json
import warnings
from datetime import datetime

warnings.filterwarnings('ignore')

DATA_DIR = "/root/.openclaw/workspace/trading-system/data/backtest/fixed/"
OUT_DIR  = "/root/.openclaw/workspace/trading-system/dharma/results/"
TAG = datetime.now().strftime("%Y%m%d_%H%M%S")

COMMISSION = 0.0004  # 0.04%
SL_MULT    = 2.0
TP_MULT    = 1.5
HOLD_MAX   = 24       # 1H bars
SCAN_STEP  = 4        # scan every 4 bars
COOLDOWN   = 12       # bars cooldown per combo

# ─────────────────────────────────────────
# 1. Indicator computation
# ─────────────────────────────────────────
def compute_ema(series, n):
    return series.ewm(span=n, adjust=False).mean()

def compute_rsi(close, n=14):
    delta = close.diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)
    avg_g = gain.ewm(alpha=1/n, adjust=False).mean()
    avg_l = loss.ewm(alpha=1/n, adjust=False).mean()
    rs    = avg_g / avg_l.replace(0, np.nan)
    return 100 - 100 / (1 + rs)

def compute_macd(close, fast=12, slow=26, sig=9):
    ema_f = compute_ema(close, fast)
    ema_s = compute_ema(close, slow)
    macd  = ema_f - ema_s
    signal = compute_ema(macd, sig)
    hist   = macd - signal
    return macd, signal, hist

def compute_stoch(high, low, close, k=14, d=3):
    low_k  = low.rolling(k).min()
    high_k = high.rolling(k).max()
    k_pct  = 100 * (close - low_k) / (high_k - low_k + 1e-10)
    d_pct  = k_pct.rolling(d).mean()
    return k_pct, d_pct

def compute_bollinger(close, n=20, std=2):
    mid   = close.rolling(n).mean()
    sigma = close.rolling(n).std()
    upper = mid + std * sigma
    lower = mid - std * sigma
    return upper, mid, lower

def compute_atr(high, low, close, n=14):
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low  - close.shift()).abs()
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, adjust=False).mean()

def swing_highs(high, lookback):
    sh = pd.Series(False, index=high.index)
    arr = high.values
    for i in range(lookback, len(arr)-lookback):
        window = arr[i-lookback:i+lookback+1]
        if arr[i] == max(window):
            sh.iloc[i] = True
    return sh

def swing_lows(low, lookback):
    sl = pd.Series(False, index=low.index)
    arr = low.values
    for i in range(lookback, len(arr)-lookback):
        window = arr[i-lookback:i+lookback+1]
        if arr[i] == min(window):
            sl.iloc[i] = True
    return sl

def add_indicators(df):
    c, h, l, v = df['close'], df['high'], df['low'], df['volume']
    df['ema13']  = compute_ema(c, 13)
    df['ema21']  = compute_ema(c, 21)
    df['ema55']  = compute_ema(c, 55)
    df['ema200'] = compute_ema(c, 200)
    df['rsi14']  = compute_rsi(c, 14)
    df['rsi7']   = compute_rsi(c, 7)
    df['macd'], df['macd_sig'], df['macd_hist'] = compute_macd(c)
    df['stoch_k'], df['stoch_d']  = compute_stoch(h, l, c)
    df['atr14']  = compute_atr(h, l, c, 14)
    df['atr_ma20'] = df['atr14'].rolling(20).mean()
    df['atr_ratio'] = df['atr14'] / df['atr_ma20'].replace(0, np.nan)
    df['vol_ma20']  = v.rolling(20).mean()
    df['vol_ratio'] = v / df['vol_ma20'].replace(0, np.nan)
    bb_up, bb_mid, bb_lo = compute_bollinger(c)
    df['bb_upper'] = bb_up
    df['bb_mid']   = bb_mid
    df['bb_lower'] = bb_lo
    # Swing highs/lows (pre-compute for bar i using past data only)
    df['sh10'] = swing_highs(h, 10)
    df['sl10'] = swing_lows(l, 10)
    df['sh20'] = swing_highs(h, 20)
    df['sl20'] = swing_lows(l, 20)
    # MACD histogram sign change
    df['macd_hist_prev'] = df['macd_hist'].shift(1)
    return df

# ─────────────────────────────────────────
# 2. Regime classification (4H-based)
# ─────────────────────────────────────────
def classify_regime(row):
    p = row['close']
    e21, e55, e200 = row['ema21'], row['ema55'], row['ema200']
    rsi = row['rsi14']
    if p > e21 and e21 > e55 and e55 > e200 and rsi > 60:
        return 'BULL_TREND'
    elif p > e55 and e55 > e200 and 50 < rsi <= 60:
        return 'BULL_EARLY'
    elif p > e200 and p < e55 and rsi > 45:
        return 'BULL_CORRECTION'
    elif p < e21 and e21 < e55 and e55 < e200 and rsi < 40:
        return 'BEAR_TREND'
    elif p < e55 and e55 < e200 and 40 <= rsi < 50:
        return 'BEAR_EARLY'
    elif p < e200 and p > e55 and rsi < 55:
        return 'BEAR_RECOVERY'
    else:
        return 'CHOP'

def get_regime_series(df4h):
    df4h = df4h.copy()
    df4h = add_indicators(df4h)
    df4h['regime'] = df4h.apply(classify_regime, axis=1)
    return df4h['regime']

# ─────────────────────────────────────────
# 3. Signal conditions (per bar)
# ─────────────────────────────────────────
def check_trigger(row, t_id, direction):
    """Returns True if trigger t_id fires for given direction at row."""
    c   = row['close']
    rsi = row['rsi14']
    bb_up = row['bb_upper']
    bb_lo = row['bb_lower']
    sh10  = row['sh10']
    sl10  = row['sl10']
    sh20  = row['sh20']
    sl20  = row['sl20']
    macd  = row['macd']
    hist  = row['macd_hist']
    hist_prev = row['macd_hist_prev']
    e13   = row['ema13']
    e21   = row['ema21']
    e55   = row['ema55']
    vol_r = row['vol_ratio']

    if direction == 'LONG':
        if t_id == 'T1': return rsi < 30
        if t_id == 'T2': return rsi < 35
        if t_id == 'T3': return c <= bb_lo
        if t_id == 'T4': return sl10
        if t_id == 'T5': return sl20
        if t_id == 'T6': return (macd < 0) and (hist > 0) and (hist_prev <= 0)
        if t_id == 'T7': return e13 > e21
        if t_id == 'T8': return (vol_r > 1.5) and (c > e55)
    else:  # SHORT
        if t_id == 'T1': return rsi > 70
        if t_id == 'T2': return rsi > 65
        if t_id == 'T3': return c >= bb_up
        if t_id == 'T4': return sh10
        if t_id == 'T5': return sh20
        if t_id == 'T6': return (macd > 0) and (hist < 0) and (hist_prev >= 0)
        if t_id == 'T7': return e13 < e21
        if t_id == 'T8': return (vol_r > 1.5) and (c < e55)
    return False

# ─────────────────────────────────────────
# 4. Main backtest engine
# ─────────────────────────────────────────
TRIGGER_IDS = ['T1','T2','T3','T4','T5','T6','T7','T8']
ALL_COMBOS  = list(combinations(TRIGGER_IDS, 2))  # 28 combos

def run_backtest(df1h, regime_map, symbol):
    """
    regime_map: pd.Series indexed like df1h (forward-filled 4H regime mapped to 1H timestamps)
    Returns list of trade dicts
    """
    df = df1h.copy()
    df = add_indicators(df)
    df['regime'] = regime_map.reindex(df.index, method='ffill').fillna('CHOP')

    bars = df.values
    cols = {c: i for i, c in enumerate(df.columns)}

    # Cooldown tracker per (combo, direction)
    DIRECTIONS = ['LONG', 'SHORT']
    cooldown_tracker = {}
    for c in ALL_COMBOS:
        for d in DIRECTIONS:
            cooldown_tracker[(c, d)] = -9999

    trades = []
    n = len(df)
    indices = range(200, n - HOLD_MAX, SCAN_STEP)  # warm-up 200

    for i in indices:
        row = df.iloc[i]
        regime = row['regime']
        entry  = row['close']
        atr    = row['atr14']
        if np.isnan(atr) or atr == 0:
            continue

        for combo in ALL_COMBOS:
            for direction in DIRECTIONS:
                key = (combo, direction)
                if i - cooldown_tracker[key] < COOLDOWN:
                    continue

                # Check both triggers fire
                t1_fire = check_trigger(row, combo[0], direction)
                t2_fire = check_trigger(row, combo[1], direction)
                if not (t1_fire and t2_fire):
                    continue

                # Mark cooldown
                cooldown_tracker[key] = i

                # Compute SL/TP
                if direction == 'LONG':
                    sl_price = entry - SL_MULT * atr
                    tp_price = entry + TP_MULT * atr
                else:
                    sl_price = entry + SL_MULT * atr
                    tp_price = entry - TP_MULT * atr

                # Forward simulation
                result = 'TIMEOUT'
                exit_price = entry
                for j in range(i+1, min(i+1+HOLD_MAX, n)):
                    future = df.iloc[j]
                    hi = future['high']
                    lo = future['low']
                    if direction == 'LONG':
                        if lo <= sl_price:
                            result = 'SL'
                            exit_price = sl_price
                            break
                        if hi >= tp_price:
                            result = 'TP'
                            exit_price = tp_price
                            break
                    else:
                        if hi >= sl_price:
                            result = 'SL'
                            exit_price = sl_price
                            break
                        if lo <= tp_price:
                            result = 'TP'
                            exit_price = tp_price
                            break

                if result == 'TIMEOUT':
                    exit_price = df.iloc[min(i+HOLD_MAX, n-1)]['close']

                # PnL
                if direction == 'LONG':
                    pnl_pct = (exit_price - entry) / entry - 2 * COMMISSION
                else:
                    pnl_pct = (entry - exit_price) / entry - 2 * COMMISSION

                combo_name = f"{combo[0]}+{combo[1]}"
                trades.append({
                    'symbol':    symbol,
                    'ts':        str(df.index[i]),
                    'combo':     combo_name,
                    'direction': direction,
                    'regime':    regime,
                    'result':    result,
                    'pnl_pct':   pnl_pct,
                    'entry':     entry,
                    'exit':      exit_price,
                    'is_train':  df.index[i].year <= 2023,
                })

    return trades

# ─────────────────────────────────────────
# 5. Statistics & leaderboards
# ─────────────────────────────────────────
def win_rate(trades):
    if not trades: return 0, 0
    wins = sum(1 for t in trades if t['pnl_pct'] > 0)
    return wins / len(trades), len(trades)

def compute_leaderboards(all_trades):
    df = pd.DataFrame(all_trades)
    df['win'] = df['pnl_pct'] > 0

    print(f"\nTotal trades: {len(df):,}")
    print(f"Symbols: {df['symbol'].unique()}")

    # ──────────────────────────────────────
    # Leaderboard 1: regime × direction × combo TOP20 WR (n≥100)
    # ──────────────────────────────────────
    grp1 = df.groupby(['regime','direction','combo'])
    lb1_rows = []
    for key, g in grp1:
        if len(g) < 100: continue
        wr = g['win'].mean()
        avg_pnl = g['pnl_pct'].mean()
        lb1_rows.append({'regime': key[0], 'direction': key[1], 'combo': key[2],
                         'win_rate': round(wr, 4), 'avg_pnl': round(avg_pnl, 5), 'n': len(g)})
    lb1 = sorted(lb1_rows, key=lambda x: -x['win_rate'])[:20]

    print("\n" + "="*70)
    print("📊 LEADERBOARD 1: Regime × Direction × Combo  TOP20 WR (n≥100)")
    print("="*70)
    print(f"{'Rank':<5} {'Regime':<20} {'Dir':<7} {'Combo':<10} {'WR%':>7} {'AvgPnL%':>9} {'N':>6}")
    print("-"*70)
    for rank, r in enumerate(lb1, 1):
        print(f"{rank:<5} {r['regime']:<20} {r['direction']:<7} {r['combo']:<10} "
              f"{r['win_rate']*100:>6.1f}% {r['avg_pnl']*100:>8.3f}% {r['n']:>6,}")

    # ──────────────────────────────────────
    # Leaderboard 2: regime × direction WR matrix (n≥200)
    # ──────────────────────────────────────
    grp2 = df.groupby(['regime','direction'])
    lb2_rows = []
    for key, g in grp2:
        if len(g) < 200: continue
        wr = g['win'].mean()
        lb2_rows.append({'regime': key[0], 'direction': key[1],
                         'win_rate': round(wr, 4), 'n': len(g)})
    lb2 = sorted(lb2_rows, key=lambda x: -x['win_rate'])

    print("\n" + "="*55)
    print("📊 LEADERBOARD 2: Regime × Direction WR Matrix (n≥200)")
    print("="*55)
    print(f"{'Regime':<22} {'Direction':<10} {'WR%':>7} {'N':>7}")
    print("-"*55)
    for r in lb2:
        print(f"{r['regime']:<22} {r['direction']:<10} {r['win_rate']*100:>6.1f}% {r['n']:>7,}")

    # ──────────────────────────────────────
    # Leaderboard 3: combo WR across all regimes (n≥200)
    # ──────────────────────────────────────
    grp3 = df.groupby('combo')
    lb3_rows = []
    for key, g in grp3:
        if len(g) < 200: continue
        wr = g['win'].mean()
        avg_pnl = g['pnl_pct'].mean()
        lb3_rows.append({'combo': key, 'win_rate': round(wr, 4),
                         'avg_pnl': round(avg_pnl, 5), 'n': len(g)})
    lb3 = sorted(lb3_rows, key=lambda x: -x['win_rate'])

    print("\n" + "="*50)
    print("📊 LEADERBOARD 3: Combo WR All Regimes (n≥200)")
    print("="*50)
    print(f"{'Rank':<5} {'Combo':<10} {'WR%':>7} {'AvgPnL%':>9} {'N':>7}")
    print("-"*50)
    for rank, r in enumerate(lb3, 1):
        print(f"{rank:<5} {r['combo']:<10} {r['win_rate']*100:>6.1f}% {r['avg_pnl']*100:>8.3f}% {r['n']:>7,}")

    # ──────────────────────────────────────
    # Leaderboard 4: avg PnL TOP20 (n≥100)
    # ──────────────────────────────────────
    grp4 = df.groupby(['regime','direction','combo'])
    lb4_rows = []
    for key, g in grp4:
        if len(g) < 100: continue
        wr = g['win'].mean()
        avg_pnl = g['pnl_pct'].mean()
        lb4_rows.append({'regime': key[0], 'direction': key[1], 'combo': key[2],
                         'win_rate': round(wr, 4), 'avg_pnl': round(avg_pnl, 5), 'n': len(g)})
    lb4 = sorted(lb4_rows, key=lambda x: -x['avg_pnl'])[:20]

    print("\n" + "="*75)
    print("📊 LEADERBOARD 4: Avg PnL TOP20 (n≥100, highest expectation)")
    print("="*75)
    print(f"{'Rank':<5} {'Regime':<20} {'Dir':<7} {'Combo':<10} {'AvgPnL%':>9} {'WR%':>7} {'N':>6}")
    print("-"*75)
    for rank, r in enumerate(lb4, 1):
        print(f"{rank:<5} {r['regime']:<20} {r['direction']:<7} {r['combo']:<10} "
              f"{r['avg_pnl']*100:>8.3f}% {r['win_rate']*100:>6.1f}% {r['n']:>6,}")

    # ──────────────────────────────────────
    # Walk-Forward OOS validation (TOP10 combos by WR)
    # ──────────────────────────────────────
    top10_combos = [r['combo'] for r in lb3[:10]]
    wf_rows = []
    print("\n" + "="*70)
    print("📊 WALK-FORWARD OOS VALIDATION (TOP10 combos, train≤2023, OOS 2024+)")
    print("="*70)
    print(f"{'Combo':<10} {'Train WR%':>10} {'Train N':>8} {'OOS WR%':>9} {'OOS N':>7} {'Overfit?':<10}")
    print("-"*70)
    for combo in top10_combos:
        sub = df[df['combo'] == combo]
        train = sub[sub['is_train'] == True]
        oos   = sub[sub['is_train'] == False]
        twr = train['win'].mean() if len(train) > 0 else 0
        owr = oos['win'].mean()   if len(oos)   > 0 else 0
        overfit = 'OVERFIT ⚠️' if abs(twr - owr) > 0.15 else 'OK ✅'
        print(f"{combo:<10} {twr*100:>9.1f}% {len(train):>8,} {owr*100:>8.1f}% {len(oos):>7,} {overfit:<10}")
        wf_rows.append({'combo': combo, 'train_wr': round(twr,4), 'train_n': len(train),
                        'oos_wr': round(owr,4), 'oos_n': len(oos), 'overfit': overfit})

    return {
        'leaderboard1': lb1,
        'leaderboard2': lb2,
        'leaderboard3': lb3,
        'leaderboard4': lb4,
        'walkforward':  wf_rows,
        'total_trades': len(df),
    }

# ─────────────────────────────────────────
# 6. MAIN
# ─────────────────────────────────────────
def main():
    print("🔬 达摩院量化研究引擎 — 全指标盲测启动")
    print(f"TAG: {TAG}")

    all_trades = []

    for symbol in ['btcusdt', 'ethusdt']:
        print(f"\n{'='*60}")
        print(f"Processing {symbol.upper()} ...")

        # Load 1H and 4H
        df1h = pd.read_parquet(f"{DATA_DIR}{symbol}_1h_fixed.parquet")
        df4h = pd.read_parquet(f"{DATA_DIR}{symbol}_4h_fixed.parquet")

        # Compute 4H regime
        print(f"  Computing 4H indicators & regime ({len(df4h)} bars)...")
        regime_4h = get_regime_series(df4h)

        # Forward-fill 4H regime to 1H timestamps
        # Reindex to 1H index then ffill
        regime_1h = regime_4h.reindex(df1h.index, method='ffill').fillna('CHOP')

        print(f"  Running 1H backtest ({len(df1h)} bars, {len(ALL_COMBOS)} combos × 2 dirs)...")
        trades = run_backtest(df1h, regime_1h, symbol.upper())
        print(f"  {len(trades):,} trades generated for {symbol.upper()}")
        all_trades.extend(trades)

    print(f"\n{'='*60}")
    print(f"Total trades across all symbols: {len(all_trades):,}")

    # Compute leaderboards
    results = compute_leaderboards(all_trades)

    # Save JSON
    out_path = f"{OUT_DIR}blind_factor_report_{TAG}.json"
    with open(out_path, 'w') as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\n✅ Results saved to: {out_path}")

if __name__ == '__main__':
    main()
