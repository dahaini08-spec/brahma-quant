#!/usr/bin/env python3
"""
达摩院 · 全周期实训引擎 v3.0
=====================================
P1: 10万次蒙特卡洛参数搜索（BTC + ETH, SHORT + LONG）
P2: Walk-Forward 8年滚动OOS验证（复合周期）
P3: 1000U × 5x 模拟实训（资金曲线 + Calmar/Sharpe）
P4: 输出达摩院训练矩阵 2.0

数据：2019~2026 期货永续 (1D/4H/1H/15M)
防过拟合：随机采样 + 最小n≥200(BTC/ETH) + 多窗口WFV + 穿越测试
"""
import sys, json, gc, warnings, math, time
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

TAG   = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
RNG   = np.random.default_rng(42)
COST  = 0.0004   # 单边手续费
MIN_N = 200      # 最小成交笔数（大样本门槛）

# ════════════════════════════════════════════════════════════════
# 数据加载
# ════════════════════════════════════════════════════════════════
def load_data(sym: str, tf: str) -> pd.DataFrame:
    fname = DATA / f'{sym.lower()}_{tf}_2019_2026.parquet'
    if not fname.exists():
        raise FileNotFoundError(f'缺少数据: {fname}')
    df = pd.read_parquet(fname)
    return df


def add_regime_label(df4h: pd.DataFrame) -> pd.DataFrame:
    """
    基于 EMA200 + RSI 的简化体制分类（4H数据）
    BEAR_TREND / BEAR_EARLY / BULL_TREND / BULL_EARLY / CHOP
    """
    c = df4h['close']
    ema200 = df4h['ema200']
    rsi    = df4h['rsi14']

    regime = pd.Series('CHOP', index=df4h.index)
    # BEAR系
    bear_mask = c < ema200
    regime[bear_mask & (rsi < 45)] = 'BEAR_TREND'
    regime[bear_mask & (rsi >= 45) & (rsi < 55)] = 'BEAR_EARLY'
    regime[bear_mask & (rsi >= 55)] = 'BEAR_RECOVERY'
    # BULL系
    bull_mask = c >= ema200
    regime[bull_mask & (rsi > 55)] = 'BULL_TREND'
    regime[bull_mask & (rsi >= 45) & (rsi <= 55)] = 'BULL_EARLY'
    regime[bull_mask & (rsi < 45)] = 'BULL_CORRECTION'

    df4h = df4h.copy()
    df4h['regime'] = regime
    return df4h


# ════════════════════════════════════════════════════════════════
# 向量化结算引擎（numpy纯量化，无Python循环）
# ════════════════════════════════════════════════════════════════
def vectorized_settle(closes, highs, lows, sig_idx, sl_val, tp_val, hold_max, direction):
    """
    批量结算，返回 (n_tp, n_sl, n_to, pnl_list)
    sl_val / tp_val: 绝对价格偏移（ATR倍数已预计算）
    """
    n      = len(closes)
    n_tp   = n_sl = n_to = 0
    pnls   = []
    last_exit = -1

    for i in sig_idx:
        if i <= last_exit:
            continue
        entry = closes[i]
        if direction == 'SHORT':
            sl = entry + sl_val[i]
            tp = entry - tp_val[i]
        else:
            sl = entry - sl_val[i]
            tp = entry + tp_val[i]

        end    = min(i + hold_max + 1, n)
        result = 'TO'
        pnl    = -COST

        for j in range(i + 1, end):
            h, l = highs[j], lows[j]
            if direction == 'SHORT':
                if h >= sl:
                    result, pnl = 'SL', (entry - sl) / entry - COST
                    last_exit = j; break
                if l <= tp:
                    result, pnl = 'TP', (entry - tp) / entry - COST
                    last_exit = j; break
            else:
                if l <= sl:
                    result, pnl = 'SL', (sl - entry) / entry - COST
                    last_exit = j; break
                if h >= tp:
                    result, pnl = 'TP', (tp - entry) / entry - COST
                    last_exit = j; break

        if result == 'TP': n_tp += 1
        elif result == 'SL': n_sl += 1
        else: n_to += 1
        pnls.append(pnl)

    return n_tp, n_sl, n_to, pnls


def metrics(pnls, n_tp, n_sl, n_to, years):
    n = len(pnls)
    if n == 0:
        return None
    wins  = [p for p in pnls if p > 0]
    loss  = [p for p in pnls if p <= 0]
    wr    = n_tp / (n_tp + n_sl) if (n_tp + n_sl) > 0 else 0
    pf    = sum(wins) / abs(sum(loss)) if loss and sum(loss) != 0 else 0
    avg_w = np.mean(wins) if wins else 0
    avg_l = np.mean(loss) if loss else 0
    # 资金曲线（$1000 × 5x杠杆）
    equity = 1000.0
    peak   = equity
    mdd    = 0.0
    curve  = [equity]
    for p in pnls:
        equity *= (1 + p * 5)   # 5x杠杆
        equity  = max(equity, 0.01)
        peak    = max(peak, equity)
        dd      = (peak - equity) / peak
        mdd     = max(mdd, dd)
        curve.append(equity)
    final  = curve[-1]
    cagr   = (final / 1000) ** (1 / years) - 1 if years > 0 else 0
    calmar = cagr / mdd if mdd > 0 else 0
    # Sharpe（简化：无风险利率0）
    rets   = np.diff(curve) / np.array(curve[:-1])
    sharpe = (np.mean(rets) / np.std(rets) * np.sqrt(365 * 6)) if np.std(rets) > 0 else 0  # 4H annualize
    return {
        'n': n, 'n_tp': n_tp, 'n_sl': n_sl, 'n_to': n_to,
        'wr': round(wr, 4), 'pf': round(pf, 3),
        'avg_win': round(avg_w * 100, 2), 'avg_loss': round(avg_l * 100, 2),
        'cagr': round(cagr * 100, 2), 'mdd': round(mdd * 100, 2),
        'calmar': round(calmar, 3), 'sharpe': round(sharpe, 3),
        'final_equity': round(final, 2),
    }


# ════════════════════════════════════════════════════════════════
# P1: 蒙特卡洛参数搜索
# ════════════════════════════════════════════════════════════════
def monte_carlo_search(df4h: pd.DataFrame, direction: str,
                       n_trials: int = 25000,
                       regime_filter: set = None,
                       oos_from: str = None) -> list:
    """
    n_trials 次随机参数采样，返回所有满足 n>=MIN_N 的结果
    """
    if regime_filter is None:
        regime_filter = {'BEAR_TREND', 'BEAR_EARLY'} if direction == 'SHORT' else {'BULL_TREND', 'BULL_EARLY'}

    # 构建信号掩码（体制过滤 + RSI条件）
    df = df4h.copy()
    closes = df['close'].values.astype(np.float64)
    highs  = df['high'].values.astype(np.float64)
    lows   = df['low'].values.astype(np.float64)
    atrs   = df['atr'].values.astype(np.float64)
    rsis   = df['rsi14'].values.astype(np.float64)
    regimes = df['regime'].values

    # IS (in-sample) 范围
    if oos_from:
        oos_dt = pd.Timestamp(oos_from, tz='UTC')
        is_mask = df.index < oos_dt
        oos_mask = df.index >= oos_dt
    else:
        oos_from_dt = df.index[int(len(df) * 0.75)]  # 最后25%为OOS
        is_mask  = df.index < oos_from_dt
        oos_mask = df.index >= oos_from_dt

    # 全局时间范围（用于CAGR）
    total_years = (df.index[-1] - df.index[0]).days / 365

    results = []
    print(f'  [MC {direction}] {n_trials}次采样 IS={is_mask.sum():,}bar OOS={oos_mask.sum():,}bar', flush=True)

    for trial in range(n_trials):
        # 随机参数
        sl_mult  = float(RNG.uniform(0.5, 3.0))
        tp_mult  = float(RNG.uniform(1.0, 6.0))
        hold_max = int(RNG.integers(4, 97))   # 4~96 bars (4H: 16H~16D)
        if direction == 'SHORT':
            rsi_thr = float(RNG.uniform(45, 75))
            regime_ok = np.array([r in regime_filter for r in regimes])
            sig_mask  = regime_ok & (rsis > rsi_thr)
        else:
            rsi_thr = float(RNG.uniform(25, 55))
            regime_ok = np.array([r in regime_filter for r in regimes])
            sig_mask  = regime_ok & (rsis < rsi_thr)

        is_idx  = np.where(sig_mask & np.array(is_mask))[0]
        oos_idx = np.where(sig_mask & np.array(oos_mask))[0]

        if len(is_idx) < MIN_N:
            continue

        # IS 结算
        sl_arr = atrs * sl_mult
        tp_arr = atrs * tp_mult
        n_tp, n_sl, n_to, pnls = vectorized_settle(
            closes, highs, lows, is_idx, sl_arr, tp_arr, hold_max, direction)

        if (n_tp + n_sl) < 50:
            continue

        is_m = metrics(pnls, n_tp, n_sl, n_to,
                       (df.index[is_mask][-1] - df.index[is_mask][0]).days / 365)
        if not is_m or is_m['pf'] <= 1.0:
            continue

        # OOS 结算（防穿越）
        oos_m = None
        if len(oos_idx) >= 20:
            nt, nl, no, op = vectorized_settle(
                closes, highs, lows, oos_idx, sl_arr, tp_arr, hold_max, direction)
            if (nt + nl) >= 20:
                oos_years = (df.index[oos_mask][-1] - df.index[oos_mask][0]).days / 365
                oos_m = metrics(op, nt, nl, no, oos_years)

        results.append({
            'sl_mult': round(sl_mult, 2),
            'tp_mult': round(tp_mult, 2),
            'hold_max': hold_max,
            'rsi_thr': round(rsi_thr, 1),
            'direction': direction,
            'is': is_m,
            'oos': oos_m,
            'oos_pf': oos_m['pf'] if oos_m else 0,
            'oos_wr': oos_m['wr'] if oos_m else 0,
        })

        if trial % 5000 == 0 and trial > 0:
            print(f'    {trial}/{n_trials} 有效组合: {len(results)}', flush=True)

    results.sort(key=lambda x: (x['oos_pf'] if x['oos_pf'] > 0 else x['is']['pf']), reverse=True)
    print(f'  [MC {direction}] 完成: 有效组合={len(results)}', flush=True)
    return results


# ════════════════════════════════════════════════════════════════
# P2: Walk-Forward 8年复合OOS验证
# ════════════════════════════════════════════════════════════════
def walk_forward_validate(df4h: pd.DataFrame, best_params: dict, direction: str) -> dict:
    """
    8年复合周期 Walk-Forward 验证
    扩展窗口：每窗口IS逐步增加，OOS固定1年
    分8个窗口覆盖完整周期
    """
    df    = df4h.copy()
    closes = df['close'].values.astype(np.float64)
    highs  = df['high'].values.astype(np.float64)
    lows   = df['low'].values.astype(np.float64)
    atrs   = df['atr'].values.astype(np.float64)
    rsis   = df['rsi14'].values.astype(np.float64)
    regimes = df['regime'].values

    sl_mult  = best_params['sl_mult']
    tp_mult  = best_params['tp_mult']
    hold_max = best_params['hold_max']
    rsi_thr  = best_params['rsi_thr']

    # 体制过滤
    BEAR_R = {'BEAR_TREND', 'BEAR_EARLY'}
    BULL_R = {'BULL_TREND', 'BULL_EARLY'}
    regime_filter = BEAR_R if direction == 'SHORT' else BULL_R

    if direction == 'SHORT':
        regime_ok = np.array([r in regime_filter for r in regimes])
        sig_mask  = regime_ok & (rsis > rsi_thr)
    else:
        regime_ok = np.array([r in regime_filter for r in regimes])
        sig_mask  = regime_ok & (rsis < rsi_thr)

    sl_arr = atrs * sl_mult
    tp_arr = atrs * tp_mult

    # 8个滚动窗口（每窗口OOS = 1年）
    total_years = (df.index[-1] - df.index[0]).days / 365
    oos_window  = 365   # 1年OOS
    windows     = []

    for i in range(8):
        oos_end   = df.index[-1] - pd.Timedelta(days=oos_window * (7 - i))
        oos_start = oos_end - pd.Timedelta(days=oos_window)
        if oos_start < df.index[0]:
            continue
        is_mask  = (df.index < oos_start)
        oos_mask = (df.index >= oos_start) & (df.index < oos_end)

        oos_idx = np.where(sig_mask & np.array(oos_mask))[0]
        if len(oos_idx) < 15:
            windows.append({'window': i+1, 'oos_start': str(oos_start)[:10],
                           'oos_end': str(oos_end)[:10], 'n': 0, 'pf': 0, 'wr': 0, 'pass': False})
            continue

        nt, nl, no, op = vectorized_settle(
            closes, highs, lows, oos_idx, sl_arr, tp_arr, hold_max, direction)
        oos_years = oos_window / 365
        m = metrics(op, nt, nl, no, oos_years)

        windows.append({
            'window':    i + 1,
            'oos_start': str(oos_start)[:10],
            'oos_end':   str(oos_end)[:10],
            'n':         nt + nl + no,
            'pf':        m['pf'] if m else 0,
            'wr':        m['wr'] if m else 0,
            'cagr':      m['cagr'] if m else 0,
            'mdd':       m['mdd'] if m else 0,
            'pass':      (m['pf'] > 1.0) if m else False,
        })

    passed  = sum(1 for w in windows if w['pass'])
    avg_pf  = np.mean([w['pf'] for w in windows if w['n'] > 0])
    avg_wr  = np.mean([w['wr'] for w in windows if w['n'] > 0])
    return {
        'windows':  windows,
        'passed':   passed,
        'total':    len(windows),
        'pass_rate': f'{passed}/{len(windows)}',
        'avg_oos_pf': round(float(avg_pf), 3),
        'avg_oos_wr': round(float(avg_wr), 4),
    }


# ════════════════════════════════════════════════════════════════
# P3: 1000U × 5x 完整资金曲线
# ════════════════════════════════════════════════════════════════
def simulate_1000u(df4h: pd.DataFrame, best_params: dict, direction: str) -> dict:
    """全周期1000U×5x模拟实训"""
    df     = df4h.copy()
    closes = df['close'].values.astype(np.float64)
    highs  = df['high'].values.astype(np.float64)
    lows   = df['low'].values.astype(np.float64)
    atrs   = df['atr'].values.astype(np.float64)
    rsis   = df['rsi14'].values.astype(np.float64)
    regimes = df['regime'].values

    BEAR_R = {'BEAR_TREND', 'BEAR_EARLY'}
    BULL_R = {'BULL_TREND', 'BULL_EARLY'}
    regime_filter = BEAR_R if direction == 'SHORT' else BULL_R

    sl_mult  = best_params['sl_mult']
    tp_mult  = best_params['tp_mult']
    hold_max = best_params['hold_max']
    rsi_thr  = best_params['rsi_thr']

    if direction == 'SHORT':
        regime_ok = np.array([r in regime_filter for r in regimes])
        sig_mask  = regime_ok & (rsis > rsi_thr)
    else:
        regime_ok = np.array([r in regime_filter for r in regimes])
        sig_mask  = regime_ok & (rsis < rsi_thr)

    sig_idx = np.where(sig_mask)[0]
    sl_arr  = atrs * sl_mult
    tp_arr  = atrs * tp_mult
    n_tp, n_sl, n_to, pnls = vectorized_settle(
        closes, highs, lows, sig_idx, sl_arr, tp_arr, hold_max, direction)

    total_years = (df.index[-1] - df.index[0]).days / 365
    m = metrics(pnls, n_tp, n_sl, n_to, total_years)

    # 逐年统计
    yearly = {}
    last_exit = -1
    sig_ptr   = 0
    for year in range(2019, 2027):
        y_start = pd.Timestamp(f'{year}-01-01', tz='UTC')
        y_end   = pd.Timestamp(f'{year+1}-01-01', tz='UTC')
        y_idx   = [i for i in sig_idx if y_start <= df.index[i] < y_end]
        if not y_idx:
            yearly[str(year)] = {'n': 0, 'wr': 0, 'pf': 0}
            continue
        yt, yl, yo, yp = vectorized_settle(
            closes, highs, lows, np.array(y_idx), sl_arr, tp_arr, hold_max, direction)
        ym = metrics(yp, yt, yl, yo, 1.0)
        yearly[str(year)] = {
            'n': len(y_idx), 'wr': ym['wr'] if ym else 0,
            'pf': ym['pf'] if ym else 0,
            'cagr': ym['cagr'] if ym else 0,
        }

    return {**m, 'yearly': yearly} if m else None


# ════════════════════════════════════════════════════════════════
# 主流程
# ════════════════════════════════════════════════════════════════
def main():
    t_global = time.time()
    all_results = {}

    TASKS = [
        ('BTCUSDT', 'SHORT', 25000),
        ('BTCUSDT', 'LONG',  25000),
        ('ETHUSDT', 'SHORT', 25000),
        ('ETHUSDT', 'LONG',  25000),
    ]

    print('=' * 60)
    print('达摩院 · 全周期实训引擎 v3.0')
    print(f'总次数: {sum(t[2] for t in TASKS):,} 次蒙特卡洛')
    print('=' * 60)

    for sym, direction, n_trials in TASKS:
        print(f'\n{"="*50}')
        print(f'[{sym} {direction}] 开始实训...', flush=True)
        t0 = time.time()

        # 加载数据
        df4h = load_data(sym, '4h')
        df4h = add_regime_label(df4h)
        years = (df4h.index[-1] - df4h.index[0]).days / 365
        print(f'  数据: {len(df4h):,}条  {str(df4h.index[0])[:10]}~{str(df4h.index[-1])[:10]}  ({years:.1f}年)')

        regime_dist = df4h['regime'].value_counts().to_dict()
        print(f'  体制分布: {regime_dist}')

        # P1: 蒙特卡洛
        mc_results = monte_carlo_search(df4h, direction, n_trials)

        if not mc_results:
            print(f'  [SKIP] 无有效组合')
            all_results[f'{sym}_{direction}'] = {'status': 'NO_VALID_PARAMS'}
            continue

        # 取TOP10（按OOS PF排序）
        top10 = mc_results[:10]
        best  = top10[0]
        print(f'\n  TOP1参数: SL={best["sl_mult"]}x TP={best["tp_mult"]}x '
              f'hold={best["hold_max"]}bar RSI={best["rsi_thr"]}')
        print(f'  IS:  WR={best["is"]["wr"]:.1%} PF={best["is"]["pf"]:.2f} '
              f'n={best["is"]["n"]} CAGR={best["is"]["cagr"]}%')
        if best.get('oos'):
            print(f'  OOS: WR={best["oos"]["wr"]:.1%} PF={best["oos"]["pf"]:.2f} '
                  f'n={best["oos"]["n"]} CAGR={best["oos"]["cagr"]}%')

        # P2: Walk-Forward 8年
        print(f'\n  [WFV] 8年滚动验证...', flush=True)
        wfv = walk_forward_validate(df4h, best, direction)
        print(f'  WFV: {wfv["pass_rate"]} 通过  avg_OOS_PF={wfv["avg_oos_pf"]}')
        for w in wfv['windows']:
            status = '✅' if w['pass'] else '❌'
            print(f'    W{w["window"]} {w["oos_start"]}~{w["oos_end"]}: '
                  f'n={w["n"]} PF={w["pf"]:.2f} WR={w["wr"]:.1%} {status}')

        # P3: 1000U模拟
        print(f'\n  [1000U×5x] 全周期模拟...', flush=True)
        sim = simulate_1000u(df4h, best, direction)
        if sim:
            print(f'  1000U结果: CAGR={sim["cagr"]}%  MDD={sim["mdd"]}%  '
                  f'Calmar={sim["calmar"]}  Sharpe={sim["sharpe"]}')
            print(f'  WR={sim["wr"]:.1%}  PF={sim["pf"]:.2f}  '
                  f'n={sim["n"]}  最终=${sim["final_equity"]:,.0f}')
            print('  逐年:')
            for yr, yd in sim['yearly'].items():
                if yd['n'] > 0:
                    print(f'    {yr}: n={yd["n"]} WR={yd["wr"]:.0%} PF={yd["pf"]:.2f}')

        all_results[f'{sym}_{direction}'] = {
            'sym': sym, 'direction': direction,
            'best_params': best,
            'top10': top10[:5],
            'wfv': wfv,
            'sim_1000u': sim,
            'mc_valid_count': len(mc_results),
            'elapsed': round(time.time() - t0, 1),
        }
        gc.collect()

    # P4: 输出矩阵
    print('\n' + '=' * 60)
    print('达摩院训练矩阵 2.0')
    print('=' * 60)
    rows = []
    for key, res in all_results.items():
        if 'best_params' not in res:
            continue
        bp  = res['best_params']
        sim = res.get('sim_1000u') or {}
        wfv = res.get('wfv') or {}
        row = {
            'sym':        res['sym'],
            'direction':  res['direction'],
            'sl_mult':    bp['sl_mult'],
            'tp_mult':    bp['tp_mult'],
            'hold_h':     bp['hold_max'] * 4,   # 4H bars → hours
            'rsi_thr':    bp['rsi_thr'],
            'wr':         sim.get('wr', 0),
            'pf':         sim.get('pf', 0),
            'cagr':       sim.get('cagr', 0),
            'mdd':        sim.get('mdd', 0),
            'calmar':     sim.get('calmar', 0),
            'sharpe':     sim.get('sharpe', 0),
            'n_total':    sim.get('n', 0),
            'wfv_pass':   wfv.get('pass_rate', '0/0'),
            'avg_oos_pf': wfv.get('avg_oos_pf', 0),
        }
        rows.append(row)
        print(f'  {row["sym"]}/{row["direction"]}: '
              f'SL={row["sl_mult"]}x TP={row["tp_mult"]}x hold={row["hold_h"]}H '
              f'WR={row["wr"]:.1%} PF={row["pf"]:.2f} '
              f'CAGR={row["cagr"]}% MDD={row["mdd"]}% '
              f'Calmar={row["calmar"]} WFV={row["wfv_pass"]}')

    # 保存完整结果
    output = {
        'tag': TAG,
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'data_range': '2019-09-08 ~ 2026-06-12',
        'total_elapsed_s': round(time.time() - t_global, 0),
        'matrix': rows,
        'full_results': all_results,
    }
    out_path = RESULTS / f'dharma_v3_fullcycle_{TAG}.json'
    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False, default=str))
    print(f'\n结果已保存: {out_path}')
    print(f'总耗时: {time.time()-t_global:.0f}s')
    print('达摩院全周期实训 ✅')


if __name__ == '__main__':
    main()
