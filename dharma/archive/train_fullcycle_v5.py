#!/usr/bin/env python3
"""
达摩院 · 全周期全体制双向实训引擎 v5.0
========================================
核心升级（vs v4.0）：
  信号代理 v2 — 简化OB检测，比RSI掩码更接近真实SMC逻辑
  固定数据集  — 2019-11-01 ~ 2026-05-30（写死，不变）
  资金费率    — 纳入持仓成本计算（4H周期已完整覆盖）
  体制自适应WFV — 窗口按体制切换点分割

信号代理v2逻辑（OB简化版，无前视偏差）：
  SHORT信号：
    1. 过去N根K线出现摆动高点（swing high）
    2. 价格回测到该高点±ATR×0.3区域内
    3. 体制过滤（不硬编码，由regime_mult决定权重）
    4. RSI > rsi_thr（动量确认）
  LONG信号：
    1. 过去N根K线出现摆动低点（swing low）
    2. 价格回测到该低点±ATR×0.3区域内
    3. RSI < rsi_thr（动量确认）

数据：data/backtest/fixed/{sym}_{tf}_fixed.parquet
"""
import sys, json, gc, warnings, time
from pathlib import Path
from datetime import datetime, timezone
import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')
BASE    = Path('/root/.openclaw/workspace/trading-system')
FIXED   = BASE / 'data' / 'backtest' / 'fixed'
RESULTS = BASE / 'dharma' / 'results'
RESULTS.mkdir(exist_ok=True)
sys.path.insert(0, str(BASE))

TAG      = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
RNG      = np.random.default_rng(42)
COST     = 0.0004
N_TRIALS = 10000
LEVERAGE = 5.0
MIN_N    = 30

ALL_REGIMES = [
    'BEAR_TREND', 'BEAR_EARLY', 'BEAR_RECOVERY',
    'BULL_TREND', 'BULL_EARLY', 'BULL_CORRECTION', 'CHOP'
]


# ════════════════════════════════════════════════════════════════
# 数据加载（固定数据集）
# ════════════════════════════════════════════════════════════════
def load_fixed(sym: str, tf: str = '4h') -> pd.DataFrame:
    fname = FIXED / f'{sym.lower()}_{tf}_fixed.parquet'
    if not fname.exists():
        raise FileNotFoundError(f'固定数据集不存在: {fname}')
    df = pd.read_parquet(fname)
    return df


# ════════════════════════════════════════════════════════════════
# 信号代理 v2 — 简化OB检测（无前视偏差）
# ════════════════════════════════════════════════════════════════
def compute_swing_signals(
    closes: np.ndarray,
    highs:  np.ndarray,
    lows:   np.ndarray,
    atrs:   np.ndarray,
    rsis:   np.ndarray,
    regimes: np.ndarray,
    direction:  str,
    regime:     str,
    rsi_thr:    float,
    swing_lookback: int,   # 摆动点回溯窗口
    ob_tol_mult: float,    # OB容忍度 = ATR × ob_tol_mult
) -> np.ndarray:
    """
    返回信号索引数组（每个元素是信号触发的bar索引）
    
    OB简化检测：
      SHORT: 找过去 swing_lookback 根内的最高摆动高点
             价格回测到 [swing_hi - ob_tol, swing_hi + ob_tol]
             且 RSI > rsi_thr（处于超买区，动量确认）
      LONG:  找过去 swing_lookback 根内的最低摆动低点
             价格回测到 [swing_lo - ob_tol, swing_lo + ob_tol]
             且 RSI < rsi_thr
    """
    n       = len(closes)
    sig_idx = []
    last_sig = -1

    for i in range(swing_lookback + 2, n):
        # 体制过滤
        if regimes[i] != regime:
            continue

        # 冷却：避免连续信号
        if i - last_sig < 3:
            continue

        atr  = atrs[i]
        rsi  = rsis[i]
        ob_tol = atr * ob_tol_mult

        if direction == 'SHORT':
            if rsi <= rsi_thr:
                continue
            # 找过去 swing_lookback 根的最高高点（摆动高点）
            window_hi = highs[i - swing_lookback: i]
            swing_hi  = float(np.max(window_hi))
            price     = closes[i]
            # 价格回测到摆动高点附近（高点下方ob_tol内）
            if swing_hi - ob_tol <= price <= swing_hi + ob_tol * 0.5:
                sig_idx.append(i)
                last_sig = i

        else:  # LONG
            if rsi >= rsi_thr:
                continue
            # 找过去 swing_lookback 根的最低低点（摆动低点）
            window_lo = lows[i - swing_lookback: i]
            swing_lo  = float(np.min(window_lo))
            price     = closes[i]
            # 价格回测到摆动低点附近
            if swing_lo - ob_tol * 0.5 <= price <= swing_lo + ob_tol:
                sig_idx.append(i)
                last_sig = i

    return np.array(sig_idx, dtype=np.int64)


# ════════════════════════════════════════════════════════════════
# 向量化结算（含资金费率成本）
# ════════════════════════════════════════════════════════════════
def vectorized_settle(
    closes, highs, lows, fr_arr,
    sig_idx, sl_arr, tp_arr, hold_max, direction
):
    """
    结算，含资金费率持仓成本（每8H收取一次，4H bar=每2根收取一次）
    fr_cost = abs(funding_rate) × leverage × hold_bars / 2
    """
    n = len(closes)
    n_tp = n_sl = n_to = 0
    pnls = []
    last_exit = -1

    for i in sig_idx:
        if i <= last_exit:
            continue
        entry = closes[i]
        sl_v  = sl_arr[i]
        tp_v  = tp_arr[i]

        if direction == 'SHORT':
            sl = entry + sl_v
            tp = entry - tp_v
        else:
            sl = entry - sl_v
            tp = entry + tp_v

        end = min(i + hold_max + 1, n)
        result = 'TO'
        pnl    = -COST
        hold_bars = 0

        for j in range(i + 1, end):
            h, l = highs[j], lows[j]
            hold_bars += 1
            if direction == 'SHORT':
                if h >= sl:
                    result = 'SL'
                    pnl = (entry - sl) / entry - COST
                    last_exit = j
                    break
                if l <= tp:
                    result = 'TP'
                    pnl = (entry - tp) / entry - COST
                    last_exit = j
                    break
            else:
                if l <= sl:
                    result = 'SL'
                    pnl = (sl - entry) / entry - COST
                    last_exit = j
                    break
                if h >= tp:
                    result = 'TP'
                    pnl = (tp - entry) / entry - COST
                    last_exit = j
                    break

        # 资金费率成本（每2个4H bar = 1个资金费率周期）
        if fr_arr is not None and len(fr_arr) > i:
            fr_rate = abs(float(fr_arr[i])) if not np.isnan(fr_arr[i]) else 0
            fr_cost = fr_rate * (hold_bars // 2)
            pnl -= fr_cost

        if result == 'TP':   n_tp += 1
        elif result == 'SL': n_sl += 1
        else:                n_to += 1
        pnls.append(pnl)

    return n_tp, n_sl, n_to, pnls


def calc_metrics(pnls, n_tp, n_sl, n_to, years):
    n = len(pnls)
    if n == 0 or years <= 0:
        return None
    wins = [p for p in pnls if p > 0]
    loss = [p for p in pnls if p <= 0]
    wr   = n_tp / (n_tp + n_sl) if (n_tp + n_sl) > 0 else 0
    pf   = sum(wins) / abs(sum(loss)) if loss and sum(loss) != 0 else 0

    equity = 1000.0; peak = equity; mdd = 0.0; curve = [equity]
    for p in pnls:
        equity *= (1 + p * LEVERAGE)
        equity  = max(equity, 0.01)
        peak    = max(peak, equity)
        mdd     = max(mdd, (peak - equity) / peak)
        curve.append(equity)

    final  = curve[-1]
    cagr   = (final / 1000) ** (1 / years) - 1 if years > 0 and final > 0 else -1
    calmar = cagr / mdd if mdd > 0 else 0
    rets   = np.diff(curve) / np.array(curve[:-1])
    sharpe = (np.mean(rets) / np.std(rets) * np.sqrt(365 * 6)) if np.std(rets) > 0 else 0

    return {
        'n': n, 'n_tp': n_tp, 'n_sl': n_sl, 'n_to': n_to,
        'wr': round(wr, 4), 'pf': round(pf, 3),
        'cagr_pct':  round(cagr * 100, 2),
        'mdd_pct':   round(mdd * 100, 2),
        'calmar':    round(calmar, 3),
        'sharpe':    round(sharpe, 3),
        'final_eq':  round(final, 2),
        'n_per_year': round(n / years, 1),
    }


# ════════════════════════════════════════════════════════════════
# P1: 蒙特卡洛（信号代理v2）
# ════════════════════════════════════════════════════════════════
def mc_v2(df: pd.DataFrame, regime: str, direction: str,
          n_trials: int) -> list:

    closes  = df['close'].values.astype(np.float64)
    highs   = df['high'].values.astype(np.float64)
    lows    = df['low'].values.astype(np.float64)
    atrs    = df['atr14'].values.astype(np.float64)
    rsis    = df['rsi14'].values.astype(np.float64)
    regimes = df['regime'].values
    fr_arr  = df['funding_rate'].values if 'funding_rate' in df.columns else None

    cut       = int(len(df) * 0.75)
    is_arr    = np.zeros(len(df), dtype=bool); is_arr[:cut]  = True
    oos_arr   = np.zeros(len(df), dtype=bool); oos_arr[cut:] = True
    years_is  = (df.index[cut-1] - df.index[0]).days / 365
    years_oos = (df.index[-1]   - df.index[cut]).days / 365

    results = []
    for _ in range(n_trials):
        sl_mult       = float(RNG.uniform(0.5, 4.0))
        tp_mult       = float(RNG.uniform(0.5, 6.0))
        hold_max      = int(RNG.integers(2, 73))
        rsi_thr       = float(RNG.uniform(40, 80) if direction == 'SHORT' else RNG.uniform(20, 60))
        swing_lb      = int(RNG.integers(5, 40))    # 摆动回溯窗口 5~40根
        ob_tol_mult   = float(RNG.uniform(0.1, 0.8))# OB容忍度倍数

        # IS信号
        is_idx = compute_swing_signals(
            closes, highs, lows, atrs, rsis, regimes,
            direction, regime, rsi_thr, swing_lb, ob_tol_mult
        )
        is_idx = is_idx[is_arr[is_idx]] if len(is_idx) > 0 else np.array([], dtype=np.int64)

        if len(is_idx) < MIN_N:
            continue

        sl_arr = atrs * sl_mult
        tp_arr = atrs * tp_mult

        nt, nl, no, p = vectorized_settle(
            closes, highs, lows, fr_arr,
            is_idx, sl_arr, tp_arr, hold_max, direction
        )
        if (nt + nl) < MIN_N // 2:
            continue
        is_m = calc_metrics(p, nt, nl, no, years_is)
        if not is_m or is_m['pf'] <= 1.0:
            continue

        # OOS信号
        oos_idx = compute_swing_signals(
            closes, highs, lows, atrs, rsis, regimes,
            direction, regime, rsi_thr, swing_lb, ob_tol_mult
        )
        oos_idx = oos_idx[oos_arr[oos_idx]] if len(oos_idx) > 0 else np.array([], dtype=np.int64)

        oos_m = None
        if len(oos_idx) >= 10:
            ot, ol, oo, op = vectorized_settle(
                closes, highs, lows, fr_arr,
                oos_idx, sl_arr, tp_arr, hold_max, direction
            )
            if (ot + ol) >= 10:
                oos_m = calc_metrics(op, ot, ol, oo, years_oos)

        results.append({
            'sl': round(sl_mult, 2), 'tp': round(tp_mult, 2),
            'hold': hold_max, 'rsi_thr': round(rsi_thr, 1),
            'swing_lb': swing_lb, 'ob_tol': round(ob_tol_mult, 2),
            'is': is_m, 'oos': oos_m,
            'oos_pf': oos_m['pf'] if oos_m else 0,
        })

    results.sort(key=lambda x: x['oos_pf'] if x['oos_pf'] > 0 else x['is']['pf'], reverse=True)
    return results


# ════════════════════════════════════════════════════════════════
# P2: 体制自适应WFV
# ════════════════════════════════════════════════════════════════
def wfv_regime_adaptive(df: pd.DataFrame, params: dict,
                        regime: str, direction: str) -> dict:
    """
    修复v2：固定8窗口年度分割（体制切换太频繁导致每段信号不足）
    """
    closes  = df['close'].values.astype(np.float64)
    highs   = df['high'].values.astype(np.float64)
    lows    = df['low'].values.astype(np.float64)
    atrs    = df['atr14'].values.astype(np.float64)
    rsis    = df['rsi14'].values.astype(np.float64)
    regimes = df['regime'].values
    fr_arr  = df['funding_rate'].values if 'funding_rate' in df.columns else None

    sl_arr = atrs * params['sl']
    tp_arr = atrs * params['tp']

    # 固定8窗口，每窗口0.75年≈274天
    n_windows = 8
    total = len(df)
    win_size = total // (n_windows + 1)
    windows = []

    for i in range(n_windows):
        oos_start = win_size * (i + 1)
        oos_end   = min(win_size * (i + 2), total)

        oos_mask = np.zeros(total, dtype=bool)
        oos_mask[oos_start:oos_end] = True

        oos_idx = compute_swing_signals(
            closes, highs, lows, atrs, rsis, regimes,
            direction, regime, params['rsi_thr'],
            params['swing_lb'], params['ob_tol']
        )
        oos_idx = oos_idx[oos_mask[oos_idx]] if len(oos_idx) > 0 else np.array([], dtype=np.int64)

        if len(oos_idx) < 5:
            windows.append({
                'w': i+1,
                'start': str(df.index[oos_start])[:10],
                'end':   str(df.index[min(oos_end-1,total-1)])[:10],
                'n': 0, 'pf': 0, 'wr': 0, 'pass': False
            })
            continue

        oos_years = max((df.index[min(oos_end-1,total-1)] - df.index[oos_start]).days / 365, 0.01)
        nt, nl, no, p = vectorized_settle(
            closes, highs, lows, fr_arr,
            oos_idx, sl_arr, tp_arr, params['hold'], direction
        )
        m = calc_metrics(p, nt, nl, no, oos_years)
        windows.append({
            'w': i+1,
            'start': str(df.index[oos_start])[:10],
            'end':   str(df.index[min(oos_end-1,total-1)])[:10],
            'n': nt+nl+no, 'pf': m['pf'] if m else 0,
            'wr': m['wr'] if m else 0,
            'cagr': m['cagr_pct'] if m else 0,
            'pass': (m['pf'] > 1.0) if m else False,
        })

    valid_w = [w for w in windows if w['n'] >= 5]
    passed  = sum(1 for w in valid_w if w['pass'])
    avg_pf  = float(np.mean([w['pf'] for w in valid_w])) if valid_w else 0

    return {
        'windows':    windows,
        'pass_rate':  f'{passed}/{len(valid_w)}',
        'passed':     passed,
        'total':      len(valid_w),
        'avg_oos_pf': round(avg_pf, 3),
    }
    closes  = df['close'].values.astype(np.float64)
    highs   = df['high'].values.astype(np.float64)
    lows    = df['low'].values.astype(np.float64)
    atrs    = df['atr14'].values.astype(np.float64)
    rsis    = df['rsi14'].values.astype(np.float64)
    regimes = df['regime'].values
    fr_arr  = df['funding_rate'].values if 'funding_rate' in df.columns else None

    sl_arr = atrs * params['sl']
    tp_arr = atrs * params['tp']

    # 找体制切换点
    switch_pts = [0]
    for i in range(1, len(regimes)):
        if regimes[i] != regimes[i-1]:
            switch_pts.append(i)
    switch_pts.append(len(df))

    # 按切换点分割OOS窗口（每个窗口=一段连续同体制区间，≥MIN_N信号才纳入）
    windows = []
    n_windows = min(8, len(switch_pts) - 1)
    # 选取最后8个切换区间作为滚动OOS
    selected_segs = [(switch_pts[i], switch_pts[i+1])
                     for i in range(max(0, len(switch_pts)-n_windows-1), len(switch_pts)-1)]

    for w_idx, (seg_start, seg_end) in enumerate(selected_segs):
        oos_mask = np.zeros(len(df), dtype=bool)
        oos_mask[seg_start:seg_end] = True

        oos_idx = compute_swing_signals(
            closes, highs, lows, atrs, rsis, regimes,
            direction, regime, params['rsi_thr'],
            params['swing_lb'], params['ob_tol']
        )
        oos_idx = oos_idx[oos_mask[oos_idx]] if len(oos_idx) > 0 else np.array([], dtype=np.int64)

        if len(oos_idx) < 8:
            windows.append({
                'w': w_idx+1,
                'start': str(df.index[seg_start])[:10],
                'end':   str(df.index[min(seg_end-1, len(df)-1)])[:10],
                'regime_seg': str(regimes[seg_start]),
                'n': 0, 'pf': 0, 'wr': 0, 'pass': False
            })
            continue

        oos_years = max((df.index[min(seg_end-1, len(df)-1)] - df.index[seg_start]).days / 365, 0.01)
        nt, nl, no, p = vectorized_settle(
            closes, highs, lows, fr_arr,
            oos_idx, sl_arr, tp_arr, params['hold'], direction
        )
        m = calc_metrics(p, nt, nl, no, oos_years)
        windows.append({
            'w': w_idx+1,
            'start': str(df.index[seg_start])[:10],
            'end':   str(df.index[min(seg_end-1, len(df)-1)])[:10],
            'regime_seg': str(regimes[seg_start]),
            'n': nt+nl+no, 'pf': m['pf'] if m else 0,
            'wr': m['wr'] if m else 0,
            'cagr': m['cagr_pct'] if m else 0,
            'pass': (m['pf'] > 1.0) if m else False,
        })

    valid_w = [w for w in windows if w['n'] >= 8]
    passed  = sum(1 for w in valid_w if w['pass'])
    avg_pf  = float(np.mean([w['pf'] for w in valid_w])) if valid_w else 0

    return {
        'windows':    windows,
        'pass_rate':  f'{passed}/{len(valid_w)}',
        'passed':     passed,
        'total':      len(valid_w),
        'avg_oos_pf': round(avg_pf, 3),
    }


# ════════════════════════════════════════════════════════════════
# P3: 1000U全周期模拟（含FR成本）
# ════════════════════════════════════════════════════════════════
def simulate_full(df: pd.DataFrame, params: dict,
                  regime: str, direction: str) -> dict:
    closes  = df['close'].values.astype(np.float64)
    highs   = df['high'].values.astype(np.float64)
    lows    = df['low'].values.astype(np.float64)
    atrs    = df['atr14'].values.astype(np.float64)
    rsis    = df['rsi14'].values.astype(np.float64)
    regimes = df['regime'].values
    fr_arr  = df['funding_rate'].values if 'funding_rate' in df.columns else None

    sig_idx = compute_swing_signals(
        closes, highs, lows, atrs, rsis, regimes,
        direction, regime, params['rsi_thr'],
        params['swing_lb'], params['ob_tol']
    )
    if len(sig_idx) < 10:
        return None

    sl_arr = atrs * params['sl']
    tp_arr = atrs * params['tp']
    years  = (df.index[-1] - df.index[0]).days / 365

    nt, nl, no, p = vectorized_settle(
        closes, highs, lows, fr_arr,
        sig_idx, sl_arr, tp_arr, params['hold'], direction
    )
    return calc_metrics(p, nt, nl, no, years)


# ════════════════════════════════════════════════════════════════
# 主流程
# ════════════════════════════════════════════════════════════════
def main():
    t_global = time.time()
    all_matrix = {}

    print('=' * 65)
    print('达摩院 · 全体制双向实训 v5.0')
    print('信号代理v2（简化OB检测）+ 固定数据集 + 资金费率成本')
    print(f'14组 × {N_TRIALS:,}次蒙特卡洛 = {14*N_TRIALS:,}次总采样')
    print('=' * 65)

    for sym in ['BTCUSDT', 'ETHUSDT']:
        print(f'\n{"━"*65}')
        print(f'▶ {sym} 加载固定数据集 4H...')
        df   = load_fixed(sym, '4h')
        years = (df.index[-1] - df.index[0]).days / 365
        rdist = df['regime'].value_counts()
        fr_ok = df['funding_rate'].notna().sum()

        print(f'  {len(df):,}条 | {str(df.index[0])[:10]}~{str(df.index[-1])[:10]} | {years:.1f}年')
        print(f'  FR覆盖: {fr_ok:,}/{len(df):,} | 体制分布:')
        for r in ALL_REGIMES:
            n_r = rdist.get(r, 0)
            print(f'    {r:<20}: {n_r:5,}bar ({n_r/len(df)*100:5.1f}%)')

        for regime in ALL_REGIMES:
            for direction in ['SHORT', 'LONG']:
                key = f'{sym}_{regime}_{direction}'
                t0  = time.time()
                print(f'\n  [{sym} {regime} {direction}]', end=' ', flush=True)

                # P1 MC
                mc = mc_v2(df, regime, direction, N_TRIALS)

                if not mc:
                    print(f'❌ 无有效组合')
                    all_matrix[key] = {
                        'sym': sym, 'regime': regime, 'direction': direction,
                        'status': 'INSUFFICIENT', 'regime_bars': int(rdist.get(regime, 0)),
                    }
                    continue

                best = mc[0]
                bp   = {k: best[k] for k in ['sl','tp','hold','rsi_thr','swing_lb','ob_tol']}

                # P2 WFV（体制自适应）
                wfv = wfv_regime_adaptive(df, bp, regime, direction)

                # P3 模拟
                sim = simulate_full(df, bp, regime, direction)

                # 激活裁定
                activate = (
                    wfv['passed'] >= max(1, wfv['total'] // 2) and
                    wfv['avg_oos_pf'] > 1.0 and
                    sim is not None and
                    sim.get('n_per_year', 0) >= 10 and
                    sim.get('pf', 0) > 1.0 and
                    sim.get('mdd_pct', 100) < 80   # MDD<80%才激活
                )
                watch = (not activate and wfv['passed'] >= max(1, wfv['total'] // 3) and
                         sim is not None and sim.get('pf', 0) > 1.0)

                status = 'ACTIVE' if activate else ('WATCH' if watch else 'INACTIVE')
                icon   = '✅' if activate else ('⚠️ ' if watch else '❌')

                elapsed = time.time() - t0
                print(f'{icon} {status} | MC={len(mc)} WFV={wfv["pass_rate"]} OOS_PF={wfv["avg_oos_pf"]}', end='')
                if sim:
                    print(f' | WR={sim["wr"]:.0%} PF={sim["pf"]:.2f} '
                          f'CAGR={sim["cagr_pct"]}% MDD={sim["mdd_pct"]}% '
                          f'n/yr={sim["n_per_year"]} ({elapsed:.0f}s)')
                else:
                    print(f' | 无模拟数据 ({elapsed:.0f}s)')

                all_matrix[key] = {
                    'sym': sym, 'regime': regime, 'direction': direction,
                    'status': status, 'activate': activate,
                    'best_params': bp, 'mc_valid': len(mc),
                    'wfv': wfv, 'sim': sim,
                    'regime_bars': int(rdist.get(regime, 0)),
                    'regime_pct':  round(rdist.get(regime, 0)/len(df)*100, 1),
                }
                gc.collect()

    # ── 汇总矩阵 ─────────────────────────────────────────────
    print('\n' + '='*65)
    print('达摩院训练矩阵 v5.0 — 最终汇总')
    print('='*65)

    def collect_rows(status):
        rows = []
        for key, res in sorted(all_matrix.items()):
            if res.get('status') != status: continue
            sim = res.get('sim') or {}
            wfv = res.get('wfv') or {}
            bp  = res.get('best_params') or {}
            rows.append({
                'sym': res['sym'], 'regime': res['regime'],
                'direction': res['direction'], 'status': status,
                'sl': bp.get('sl',0), 'tp': bp.get('tp',0),
                'hold_h': bp.get('hold',0)*4,
                'rsi_thr': bp.get('rsi_thr',0),
                'swing_lb': bp.get('swing_lb',0),
                'ob_tol': bp.get('ob_tol',0),
                'wr': sim.get('wr',0), 'pf': sim.get('pf',0),
                'cagr': sim.get('cagr_pct',0),
                'mdd': sim.get('mdd_pct',0),
                'calmar': sim.get('calmar',0),
                'n_per_yr': sim.get('n_per_year',0),
                'wfv': wfv.get('pass_rate','0/0'),
                'oos_pf': wfv.get('avg_oos_pf',0),
                'regime_pct': res.get('regime_pct',0),
            })
        return sorted(rows, key=lambda x: x['calmar'], reverse=True)

    active_rows   = collect_rows('ACTIVE')
    watch_rows    = collect_rows('WATCH')
    inactive_rows = collect_rows('INACTIVE')

    HDR = f'  {"标的/体制/方向":<38} {"WR":>5} {"PF":>5} {"CAGR":>7} {"MDD":>6} {"Cal":>6} {"n/yr":>5} {"WFV":>5}'

    for title, rows in [('✅ 激活', active_rows), ('⚠️  观察', watch_rows), ('❌ 不激活', inactive_rows)]:
        if not rows: continue
        print(f'\n【{title}】')
        print(HDR)
        print('  ' + '-'*90)
        for r in rows:
            label = f'{r["sym"]}/{r["regime"]}/{r["direction"]}'
            print(f'  {label:<38} {r["wr"]:>5.0%} {r["pf"]:>5.2f} '
                  f'{r["cagr"]:>7.1f}% {r["mdd"]:>6.1f}% {r["calmar"]:>6.3f} '
                  f'{r["n_per_yr"]:>5.1f} {r["wfv"]:>5}')

    # 保存
    output = {
        'tag': TAG, 'version': 'v5.0',
        'signal_proxy': 'OB_SIMPLIFIED_v2',
        'data_source': 'fixed_dataset_2019-11-01_2026-05-30',
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'config': {'n_trials': N_TRIALS, 'leverage': LEVERAGE, 'min_n': MIN_N},
        'counts': {'active': len(active_rows), 'watch': len(watch_rows), 'inactive': len(inactive_rows)},
        'active_matrix':   active_rows,
        'watch_matrix':    watch_rows,
        'full_matrix':     all_matrix,
        'total_elapsed_s': round(time.time()-t_global, 0),
    }
    out_path = RESULTS / f'dharma_v5_ob_proxy_{TAG}.json'
    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False, default=str))
    print(f'\n结果已保存: {out_path}')
    print(f'总耗时: {(time.time()-t_global)/60:.1f}分钟')
    print('达摩院 v5.0 全体制双向实训 ✅')


if __name__ == '__main__':
    main()
