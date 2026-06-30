#!/usr/bin/env python3
"""
达摩院 · 全周期全体制双向实训引擎 v4.0
========================================
设计院裁定：14组独立实训
  7体制 × 2方向（SHORT+LONG）= 14组
  每组独立蒙特卡洛 10,000次
  总计：14 × 10,000 = 140,000次采样

体制定义（基于EMA200+RSI+斜率）：
  BEAR_TREND      : 下跌趋势（EMA200下方，RSI<45）
  BEAR_EARLY      : 熊市初期（EMA200下方，45≤RSI<55）
  BEAR_RECOVERY   : 熊市修复反弹（EMA200下方，RSI≥55）
  BULL_TREND      : 上涨趋势（EMA200上方，RSI>55）
  BULL_EARLY      : 牛市初期（EMA200上方，45<RSI≤55）
  BULL_CORRECTION : 牛市回调（EMA200上方，RSI≤45）
  CHOP            : 震荡（价格在EMA200附近±1%）

数据：BTC+ETH 2019~2026（6.8年，4H主训练周期）
OOS：8窗口滚动Walk-Forward（复合8年周期）
评估：WR / PF / CAGR / MDD / Calmar / Sharpe / n_per_year
激活门槛：WFV≥4/8 AND OOS_PF>1.0 AND n_per_year≥15
"""
import sys, json, gc, warnings, time
from pathlib import Path
from datetime import datetime, timezone
import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')
BASE    = Path('/root/.openclaw/workspace/trading-system')
DATA    = BASE / 'data' / 'backtest'
RESULTS = BASE / 'dharma' / 'results'
RESULTS.mkdir(exist_ok=True)
sys.path.insert(0, str(BASE))

TAG      = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
RNG      = np.random.default_rng(42)
COST     = 0.0004
N_TRIALS = 10000    # 每组10,000次
MIN_N    = 30       # 每组最小样本（体制时间片段有限）
LEVERAGE = 5.0

ALL_REGIMES = [
    'BEAR_TREND', 'BEAR_EARLY', 'BEAR_RECOVERY',
    'BULL_TREND', 'BULL_EARLY', 'BULL_CORRECTION',
    'CHOP'
]
DIRECTIONS = ['SHORT', 'LONG']


# ════════════════════════════════════════════════════════════════
# 数据加载 + 精细体制标注
# ════════════════════════════════════════════════════════════════
def load_data(sym: str) -> pd.DataFrame:
    fname = DATA / f'{sym.lower()}_4h_2019_2026.parquet'
    if not fname.exists():
        raise FileNotFoundError(f'缺数据: {fname}')
    df = pd.read_parquet(fname)
    return df


def label_regime(df: pd.DataFrame) -> pd.DataFrame:
    """
    精细7体制标注
    CHOP = 价格在EMA200 ±1.5% 范围内（震荡区）
    """
    df  = df.copy()
    c   = df['close']
    e200 = df['ema200']
    rsi  = df['rsi14']

    # EMA200斜率（20根均线斜率方向）
    slope = e200.diff(20) / e200.shift(20) * 100  # 20bar斜率%

    regime = pd.Series('CHOP', index=df.index)

    above = c > e200 * 1.015   # 明显在EMA200上方
    below = c < e200 * 0.985   # 明显在EMA200下方
    chop  = ~above & ~below    # EMA200附近±1.5% = 震荡

    # BEAR系
    regime[below & (rsi < 40)]                     = 'BEAR_TREND'
    regime[below & (rsi >= 40) & (rsi < 52)]       = 'BEAR_EARLY'
    regime[below & (rsi >= 52)]                    = 'BEAR_RECOVERY'
    # BULL系
    regime[above & (rsi > 58)]                     = 'BULL_TREND'
    regime[above & (rsi >= 48) & (rsi <= 58)]      = 'BULL_EARLY'
    regime[above & (rsi < 48)]                     = 'BULL_CORRECTION'
    # CHOP
    regime[chop]                                   = 'CHOP'

    df['regime'] = regime
    df['slope200'] = slope
    return df


# ════════════════════════════════════════════════════════════════
# 向量化结算
# ════════════════════════════════════════════════════════════════
def vectorized_settle(closes, highs, lows, sig_idx, sl_arr, tp_arr, hold_max, direction):
    n      = len(closes)
    n_tp   = n_sl = n_to = 0
    pnls   = []
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
        result = 'TO'; pnl = -COST
        for j in range(i + 1, end):
            h, l = highs[j], lows[j]
            if direction == 'SHORT':
                if h >= sl: result='SL'; pnl=(entry-sl)/entry-COST; last_exit=j; break
                if l <= tp: result='TP'; pnl=(entry-tp)/entry-COST; last_exit=j; break
            else:
                if l <= sl: result='SL'; pnl=(sl-entry)/entry-COST; last_exit=j; break
                if h >= tp: result='TP'; pnl=(tp-entry)/entry-COST; last_exit=j; break
        if result == 'TP': n_tp += 1
        elif result == 'SL': n_sl += 1
        else: n_to += 1
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
    # 资金曲线
    equity = 1000.0; peak = equity; mdd = 0.0; curve = [equity]
    for p in pnls:
        equity *= (1 + p * LEVERAGE)
        equity  = max(equity, 0.01)
        peak    = max(peak, equity)
        mdd     = max(mdd, (peak - equity) / peak)
        curve.append(equity)
    final = curve[-1]
    cagr  = (final / 1000) ** (1 / years) - 1 if years > 0 and final > 0 else -1
    calmar = cagr / mdd if mdd > 0 else 0
    rets   = np.diff(curve) / np.array(curve[:-1])
    sharpe = (np.mean(rets) / np.std(rets) * np.sqrt(365*6)) if np.std(rets) > 0 else 0
    return {
        'n': n, 'n_tp': n_tp, 'n_sl': n_sl, 'n_to': n_to,
        'wr': round(wr, 4), 'pf': round(pf, 3),
        'cagr_pct': round(cagr * 100, 2),
        'mdd_pct':  round(mdd * 100, 2),
        'calmar':   round(calmar, 3),
        'sharpe':   round(sharpe, 3),
        'final_equity': round(final, 2),
        'n_per_year': round(n / years, 1),
    }


# ════════════════════════════════════════════════════════════════
# P1: 单体制单方向蒙特卡洛
# ════════════════════════════════════════════════════════════════
def mc_one_regime(df: pd.DataFrame, regime: str, direction: str, n_trials: int) -> list:
    """单体制×单方向蒙特卡洛"""
    closes  = df['close'].values.astype(np.float64)
    highs   = df['high'].values.astype(np.float64)
    lows    = df['low'].values.astype(np.float64)
    atrs    = df['atr'].values.astype(np.float64)
    rsis    = df['rsi14'].values.astype(np.float64)
    regimes = df['regime'].values

    # IS: 前75% | OOS: 后25%
    cut = int(len(df) * 0.75)
    is_arr  = np.zeros(len(df), dtype=bool); is_arr[:cut]  = True
    oos_arr = np.zeros(len(df), dtype=bool); oos_arr[cut:] = True

    regime_mask = (regimes == regime)
    years_total = (df.index[-1] - df.index[0]).days / 365
    years_is    = years_total * 0.75
    years_oos   = years_total * 0.25

    results = []
    for _ in range(n_trials):
        sl_mult  = float(RNG.uniform(0.5, 4.0))
        tp_mult  = float(RNG.uniform(0.5, 6.0))
        hold_max = int(RNG.integers(2, 73))    # 2~72 bars = 8H~12D
        # RSI条件：SHORT=过买进场 | LONG=过卖进场
        if direction == 'SHORT':
            rsi_thr = float(RNG.uniform(40, 80))
            rsi_cond = rsis > rsi_thr
        else:
            rsi_thr = float(RNG.uniform(20, 60))
            rsi_cond = rsis < rsi_thr

        sig_mask = regime_mask & rsi_cond
        is_idx   = np.where(sig_mask & is_arr)[0]
        oos_idx  = np.where(sig_mask & oos_arr)[0]

        if len(is_idx) < MIN_N:
            continue

        sl_arr = atrs * sl_mult
        tp_arr = atrs * tp_mult

        nt, nl, no, p = vectorized_settle(closes, highs, lows, is_idx, sl_arr, tp_arr, hold_max, direction)
        if (nt + nl) < MIN_N // 2:
            continue
        is_m = calc_metrics(p, nt, nl, no, years_is)
        if not is_m or is_m['pf'] <= 1.0:
            continue

        oos_m = None
        if len(oos_idx) >= 10:
            ot, ol, oo, op = vectorized_settle(closes, highs, lows, oos_idx, sl_arr, tp_arr, hold_max, direction)
            if (ot + ol) >= 10:
                oos_m = calc_metrics(op, ot, ol, oo, years_oos)

        results.append({
            'sl':  round(sl_mult, 2), 'tp': round(tp_mult, 2),
            'hold': hold_max, 'rsi_thr': round(rsi_thr, 1),
            'is': is_m, 'oos': oos_m,
            'oos_pf': oos_m['pf'] if oos_m else 0,
        })

    results.sort(key=lambda x: x['oos_pf'] if x['oos_pf'] > 0 else x['is']['pf'], reverse=True)
    return results


# ════════════════════════════════════════════════════════════════
# P2: Walk-Forward 8窗口滚动OOS
# ════════════════════════════════════════════════════════════════
def walk_forward(df: pd.DataFrame, params: dict, regime: str, direction: str) -> dict:
    closes  = df['close'].values.astype(np.float64)
    highs   = df['high'].values.astype(np.float64)
    lows    = df['low'].values.astype(np.float64)
    atrs    = df['atr'].values.astype(np.float64)
    rsis    = df['rsi14'].values.astype(np.float64)
    regimes = df['regime'].values

    sl_arr = atrs * params['sl']
    tp_arr = atrs * params['tp']

    regime_mask = regimes == regime
    if direction == 'SHORT':
        rsi_cond = rsis > params['rsi_thr']
    else:
        rsi_cond = rsis < params['rsi_thr']
    sig_mask = regime_mask & rsi_cond

    windows = []
    n_windows = 8
    total_bars = len(df)
    window_size = total_bars // (n_windows + 1)

    for i in range(n_windows):
        oos_start = window_size * (i + 1)
        oos_end   = min(window_size * (i + 2), total_bars)
        oos_idx   = np.where(sig_mask[oos_start:oos_end])[0] + oos_start

        if len(oos_idx) < 8:
            windows.append({
                'w': i+1, 'start': str(df.index[oos_start])[:10],
                'end': str(df.index[oos_end-1])[:10],
                'n': 0, 'pf': 0, 'wr': 0, 'pass': False
            })
            continue

        oos_years = (df.index[oos_end-1] - df.index[oos_start]).days / 365
        nt, nl, no, p = vectorized_settle(
            closes, highs, lows, oos_idx, sl_arr, tp_arr, params['hold'], direction)
        m = calc_metrics(p, nt, nl, no, max(oos_years, 0.01))
        windows.append({
            'w': i+1,
            'start': str(df.index[oos_start])[:10],
            'end':   str(df.index[oos_end-1])[:10],
            'n':     nt+nl+no,
            'pf':    m['pf'] if m else 0,
            'wr':    m['wr'] if m else 0,
            'cagr':  m['cagr_pct'] if m else 0,
            'pass':  (m['pf'] > 1.0) if m else False,
        })

    valid_w = [w for w in windows if w['n'] >= 8]
    passed  = sum(1 for w in valid_w if w['pass'])
    avg_pf  = np.mean([w['pf'] for w in valid_w]) if valid_w else 0
    return {
        'windows': windows,
        'pass_rate': f'{passed}/{len(valid_w)}',
        'passed': passed, 'total': len(valid_w),
        'avg_oos_pf': round(float(avg_pf), 3),
    }


# ════════════════════════════════════════════════════════════════
# P3: 1000U全周期模拟
# ════════════════════════════════════════════════════════════════
def simulate_full(df: pd.DataFrame, params: dict, regime: str, direction: str) -> dict:
    closes  = df['close'].values.astype(np.float64)
    highs   = df['high'].values.astype(np.float64)
    lows    = df['low'].values.astype(np.float64)
    atrs    = df['atr'].values.astype(np.float64)
    rsis    = df['rsi14'].values.astype(np.float64)
    regimes = df['regime'].values

    regime_mask = regimes == regime
    rsi_cond = rsis > params['rsi_thr'] if direction == 'SHORT' else rsis < params['rsi_thr']
    sig_idx  = np.where(regime_mask & rsi_cond)[0]

    if len(sig_idx) < 10:
        return None

    sl_arr = atrs * params['sl']
    tp_arr = atrs * params['tp']
    years  = (df.index[-1] - df.index[0]).days / 365

    nt, nl, no, p = vectorized_settle(closes, highs, lows, sig_idx, sl_arr, tp_arr, params['hold'], direction)
    return calc_metrics(p, nt, nl, no, years)


# ════════════════════════════════════════════════════════════════
# 主流程
# ════════════════════════════════════════════════════════════════
def main():
    t_global = time.time()

    print('=' * 65)
    print('达摩院 · 全体制双向实训引擎 v4.0')
    print(f'14组 × {N_TRIALS:,}次蒙特卡洛 = {14*N_TRIALS:,}次总采样')
    print('7体制 × 2方向 | BTC + ETH | 2019~2026')
    print('=' * 65)

    all_matrix = {}   # key: f'{sym}_{regime}_{direction}'

    for sym in ['BTCUSDT', 'ETHUSDT']:
        print(f'\n{"━"*65}')
        print(f'▶ {sym} 数据加载 + 体制标注')
        df = load_data(sym)
        df = label_regime(df)
        years = (df.index[-1] - df.index[0]).days / 365

        # 体制分布
        rdist = df['regime'].value_counts()
        rdist_pct = (rdist / len(df) * 100).round(1)
        print(f'  {len(df):,}条 | {str(df.index[0])[:10]}~{str(df.index[-1])[:10]} | {years:.1f}年')
        print('  体制分布:')
        for r in ALL_REGIMES:
            n_r = rdist.get(r, 0)
            pct = rdist_pct.get(r, 0)
            bars_per_year = n_r / years
            print(f'    {r:20s}: {n_r:5,}bar ({pct:5.1f}%) ≈ {bars_per_year:.0f}bar/年')

        for regime in ALL_REGIMES:
            n_regime = rdist.get(regime, 0)
            regime_years = n_regime * 4 / 8760  # 4H bar → 年

            for direction in DIRECTIONS:
                key = f'{sym}_{regime}_{direction}'
                t0  = time.time()
                print(f'\n  [{sym} {regime} {direction}]', end=' ', flush=True)

                # P1
                mc = mc_one_regime(df, regime, direction, N_TRIALS)

                if not mc:
                    print(f'❌ 无有效组合 (体制占比{n_regime}bar)')
                    all_matrix[key] = {
                        'sym': sym, 'regime': regime, 'direction': direction,
                        'status': 'INSUFFICIENT_SAMPLES',
                        'regime_bars': n_regime,
                    }
                    continue

                best = mc[0]
                bp   = {'sl': best['sl'], 'tp': best['tp'],
                        'hold': best['hold'], 'rsi_thr': best['rsi_thr']}

                # P2
                wfv = walk_forward(df, bp, regime, direction)

                # P3
                sim = simulate_full(df, bp, regime, direction)

                # 激活裁定
                activate = (
                    wfv['passed'] >= 4 and
                    wfv['avg_oos_pf'] > 1.0 and
                    sim is not None and
                    sim.get('n_per_year', 0) >= 10 and
                    sim.get('pf', 0) > 1.0
                )

                elapsed = time.time() - t0
                status_icon = '✅ 激活' if activate else '⚠️  观察' if (wfv['passed'] >= 3) else '❌ 不激活'

                print(f'{status_icon} | MC有效={len(mc)} | WFV={wfv["pass_rate"]} avg_OOS_PF={wfv["avg_oos_pf"]} | ', end='')
                if sim:
                    print(f'WR={sim["wr"]:.0%} PF={sim["pf"]:.2f} CAGR={sim["cagr_pct"]}% '
                          f'MDD={sim["mdd_pct"]}% n/yr={sim["n_per_year"]} ({elapsed:.0f}s)')
                else:
                    print(f'模拟无数据 ({elapsed:.0f}s)')

                all_matrix[key] = {
                    'sym': sym, 'regime': regime, 'direction': direction,
                    'status': 'ACTIVE' if activate else ('WATCH' if wfv['passed'] >= 3 else 'INACTIVE'),
                    'activate': activate,
                    'best_params': bp,
                    'mc_valid': len(mc),
                    'wfv': wfv,
                    'sim': sim,
                    'regime_bars': n_regime,
                    'regime_pct': round(n_regime / len(df) * 100, 1),
                }
                gc.collect()

    # ── 输出汇总矩阵 ──────────────────────────────────────────
    print('\n' + '=' * 65)
    print('达摩院训练矩阵 v4.0 — 汇总')
    print('=' * 65)

    active_rows = []
    watch_rows  = []
    inactive_rows = []

    for key, res in sorted(all_matrix.items()):
        if res.get('status') == 'INSUFFICIENT_SAMPLES':
            continue
        sim = res.get('sim') or {}
        wfv = res.get('wfv') or {}
        bp  = res.get('best_params') or {}
        row = {
            'key':        key,
            'sym':        res['sym'],
            'regime':     res['regime'],
            'direction':  res['direction'],
            'status':     res.get('status', '?'),
            'sl':         bp.get('sl', 0),
            'tp':         bp.get('tp', 0),
            'hold_h':     bp.get('hold', 0) * 4,
            'rsi_thr':    bp.get('rsi_thr', 0),
            'wr':         sim.get('wr', 0),
            'pf':         sim.get('pf', 0),
            'cagr':       sim.get('cagr_pct', 0),
            'mdd':        sim.get('mdd_pct', 0),
            'calmar':     sim.get('calmar', 0),
            'n_per_yr':   sim.get('n_per_year', 0),
            'wfv':        wfv.get('pass_rate', '0/0'),
            'oos_pf':     wfv.get('avg_oos_pf', 0),
            'regime_pct': res.get('regime_pct', 0),
        }
        if res.get('status') == 'ACTIVE':   active_rows.append(row)
        elif res.get('status') == 'WATCH':  watch_rows.append(row)
        else:                               inactive_rows.append(row)

    def print_rows(rows, title):
        if not rows: return
        print(f'\n【{title}】')
        print(f'  {"标的/体制/方向":<35} {"WR":>6} {"PF":>5} {"CAGR":>7} {"MDD":>6} {"Calmar":>7} {"n/yr":>5} {"WFV":>5} {"占比":>5}')
        print(f'  {"-"*100}')
        for r in sorted(rows, key=lambda x: x['calmar'], reverse=True):
            print(f'  {r["sym"]}/{r["regime"]}/{r["direction"]:<20} '
                  f'{r["wr"]:>6.0%} {r["pf"]:>5.2f} {r["cagr"]:>7.1f}% '
                  f'{r["mdd"]:>6.1f}% {r["calmar"]:>7.3f} {r["n_per_yr"]:>5.1f} '
                  f'{r["wfv"]:>5} {r["regime_pct"]:>4.1f}%')

    print_rows(active_rows,  '✅ 激活（建议纳入实盘）')
    print_rows(watch_rows,   '⚠️  观察（样本不足/边缘）')
    print_rows(inactive_rows,'❌ 不激活')

    # 保存
    output = {
        'tag': TAG,
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'config': {'n_trials': N_TRIALS, 'leverage': LEVERAGE, 'min_n': MIN_N},
        'active_count':   len(active_rows),
        'watch_count':    len(watch_rows),
        'inactive_count': len(inactive_rows),
        'active_matrix':  active_rows,
        'watch_matrix':   watch_rows,
        'full_matrix':    all_matrix,
        'total_elapsed_s': round(time.time() - t_global, 0),
    }
    out_path = RESULTS / f'dharma_v4_full14_{TAG}.json'
    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False, default=str))
    print(f'\n结果已保存: {out_path}')
    print(f'总耗时: {(time.time()-t_global)/60:.1f}分钟')
    print('达摩院全体制双向实训 ✅')


if __name__ == '__main__':
    main()
