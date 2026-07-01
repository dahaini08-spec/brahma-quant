#!/usr/bin/env python3
"""
达摩院 · 全体制双向训练 v1.0
设计院 · 2026-06-11

目标：补齐做多短板，建立完整体制×方向×评分门槛矩阵

训练设计：
  - 48个标的 × 1H+4H双周期
  - 全部6个体制：BULL_TREND / BULL_PEAK / BEAR_TREND / BEAR_EARLY / BEAR_RECOVERY / CHOP
  - 双向：LONG + SHORT 均完整训练
  - 迭代：每个体制×方向组合 n_iter 次随机参数采样
  - 评分门槛扫描：100~170（步长5），每格计算PF+Wilson CI

输出：dharma/results/full_regime_bidirectional_{ts}.json
  matrix[sym][regime][direction] = {
      n, wr, pf, ci95_lo, ci95_hi,
      by_threshold: {100: {n,pf,ci,stable}, ...}
      optimal_threshold, best_pf, best_n
  }

N14铁证问题修复：
  原N14只训练顺势方向（BEAR做空/BULL做多）
  本脚本强制双向采样，填补逆势方向的空白数据
"""

import sys, json, time, warnings, random, math
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / 'brahma_brain'))
warnings.filterwarnings('ignore')

import numpy as np
import pandas as pd

BASE     = Path(__file__).parent.parent
DATA_DIR = Path(__file__).parent / 'data'
RESULTS  = Path(__file__).parent / 'results'
RESULTS.mkdir(exist_ok=True)

# ── 配置 ──────────────────────────────────────────────
N_ITER_PER_CELL   = 5000    # 每个体制×方向×标的 采样次数（总10万+）
SCORE_THRESHOLDS  = list(range(100, 175, 5))   # 门槛扫描
TIMEFRAMES        = ['1h', '4h']
MIN_N_FOR_STABLE  = 30      # 最少样本量才算"稳定"

# 目标标的（优先高流动性）
CORE_SYMS = [
    'btcusdt', 'ethusdt', 'bnbusdt', 'solusdt', 'dogeusdt',
    'adausdt', 'avaxusdt', 'linkusdt', 'dotusdt', 'atomusdt',
    'trxusdt', 'ltcusdt', 'xauusdt', 'tiausdt', 'wldusdt',
]

# 体制定义（通过RSI+EMA+ATR计算）
ALL_REGIMES = [
    'BULL_TREND', 'BULL_PEAK',
    'BEAR_TREND', 'BEAR_EARLY', 'BEAR_RECOVERY',
    'CHOP',
]

BOTH_DIRECTIONS = ['LONG', 'SHORT']

# ── 指标计算 ──────────────────────────────────────────
def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    c = df['close'].astype(float)
    h = df['high'].astype(float)
    l = df['low'].astype(float)

    # EMA
    df['ema21']  = c.ewm(span=21).mean()
    df['ema55']  = c.ewm(span=55).mean()
    df['ema200'] = c.ewm(span=200).mean()

    # RSI
    delta = c.diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    df['rsi'] = 100 - 100 / (1 + gain / loss.replace(0, 1e-9))

    # ATR
    tr = pd.concat([
        h - l,
        (h - c.shift()).abs(),
        (l - c.shift()).abs(),
    ], axis=1).max(axis=1)
    df['atr'] = tr.rolling(14).mean()

    # Bollinger Band width
    ma20 = c.rolling(20).mean()
    std20 = c.rolling(20).std()
    df['bb_width'] = (std20 * 4) / ma20.replace(0, 1e-9)

    # Volume MA ratio
    v = df['volume'].astype(float)
    df['vol_ratio'] = v / v.rolling(20).mean().replace(0, 1e-9)

    return df

def classify_regime(row) -> str:
    """基于指标分类体制"""
    rsi  = row.get('rsi', 50)
    c    = row.get('close', 0)
    e21  = row.get('ema21', c)
    e55  = row.get('ema55', c)
    e200 = row.get('ema200', c)
    bbw  = row.get('bb_width', 0.05)

    if pd.isna(rsi) or pd.isna(e200): return 'CHOP'

    above200 = c > e200
    e21_above_e55 = e21 > e55

    # BULL体制
    if above200 and e21_above_e55:
        if rsi > 70: return 'BULL_PEAK'
        return 'BULL_TREND'

    # BEAR体制
    if not above200:
        if rsi < 30: return 'BEAR_RECOVERY'
        if c < e200 * 0.85: return 'BEAR_TREND'
        return 'BEAR_EARLY'

    # 恢复期（在200MA上但趋势弱）
    if above200 and not e21_above_e55:
        if rsi > 40: return 'BEAR_RECOVERY'
        return 'BEAR_EARLY'

    # CHOP（低波动）
    return 'CHOP'


# ── 单标的回测引擎 ──────────────────────────────────────
def simulate_trade(
    df: pd.DataFrame,
    entry_idx: int,
    direction: str,
    sl_mult: float,     # ATR倍数止损
    tp_mult: float,     # ATR倍数止盈
    hold_max: int,      # 最大持仓K线数
) -> dict:
    """模拟单笔交易"""
    row  = df.iloc[entry_idx]
    atr  = row.get('atr', row['close'] * 0.01)
    if pd.isna(atr) or atr == 0: atr = row['close'] * 0.01

    entry = float(row['close'])
    if direction == 'SHORT':
        sl = entry + sl_mult * atr
        tp = entry - tp_mult * atr
    else:
        sl = entry - sl_mult * atr
        tp = entry + tp_mult * atr

    # 向后遍历
    for j in range(1, min(hold_max + 1, len(df) - entry_idx)):
        future = df.iloc[entry_idx + j]
        hi = float(future['high'])
        lo = float(future['low'])

        if direction == 'SHORT':
            if lo <= tp: return {'result': 'WIN', 'pnl': tp_mult / sl_mult}
            if hi >= sl: return {'result': 'LOSS', 'pnl': -1.0}
        else:
            if hi >= tp: return {'result': 'WIN', 'pnl': tp_mult / sl_mult}
            if lo <= sl: return {'result': 'LOSS', 'pnl': -1.0}

    return {'result': 'TIMEOUT', 'pnl': 0.0}


def run_sym_regime_backtest(sym: str, tf: str, n_iter: int) -> dict:
    """
    单标的全体制双向回测
    返回：{regime: {direction: {by_threshold: {...}}}}
    """
    # 加载数据
    parquet = DATA_DIR / f'{sym}_{tf}_2018_2026.parquet'
    if not parquet.exists():
        return {}

    try:
        df = pd.read_parquet(parquet).copy()
        df = df.reset_index()
        if 'timestamp' in df.columns: df = df.rename(columns={'timestamp':'ts'})
        elif 'open_time' in df.columns: df = df.rename(columns={'open_time':'ts'})
        df = add_indicators(df)
        df = df.dropna(subset=['rsi','ema200','atr'])
    except Exception as e:
        return {}

    n = len(df)
    if n < 500: return {}

    # 按体制分组采样点
    df['regime'] = df.apply(classify_regime, axis=1)

    # 结构：regime → dir → threshold → [trades]
    results = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))

    # 随机采样 n_iter 个入场点
    valid_idx = list(range(200, n - 200))  # 留头尾避免边界
    if len(valid_idx) == 0: return {}

    sample_idx = random.choices(valid_idx, k=min(n_iter, len(valid_idx) * 3))

    for idx in sample_idx:
        row    = df.iloc[idx]
        regime = row.get('regime', 'CHOP')
        if regime not in ALL_REGIMES: continue

        # 模拟随机参数
        sl_mult = random.uniform(0.8, 2.5)
        tp_mult = sl_mult * random.uniform(1.2, 3.5)   # TP > SL
        hold    = random.randint(8, 72)

        # 计算模拟评分（基于几个指标的简单线性组合）
        rsi    = row.get('rsi', 50)
        volr   = min(row.get('vol_ratio', 1.0), 5.0)
        bbw    = row.get('bb_width', 0.05)

        # SHORT信号评分：BEAR体制下RSI偏高 = 空头信号强
        # LONG信号评分：BULL体制下RSI偏低 = 多头信号强
        for direction in BOTH_DIRECTIONS:
            if direction == 'SHORT':
                base_score = 100 + (rsi - 50) * 0.8 + (volr - 1) * 10
            else:  # LONG
                base_score = 100 + (50 - rsi) * 0.8 + (volr - 1) * 10

            score = max(80, min(185, base_score + random.gauss(0, 15)))

            trade = simulate_trade(df, idx, direction, sl_mult, tp_mult, hold)
            result = trade['result']

            # 记录到各门槛桶
            for thr in SCORE_THRESHOLDS:
                if score >= thr:
                    results[regime][direction][thr].append(result)

    # 聚合统计
    output = {}
    for regime in ALL_REGIMES:
        output[regime] = {}
        for direction in BOTH_DIRECTIONS:
            by_thr = {}
            prev_stable = None

            for thr in SCORE_THRESHOLDS:
                trades = results[regime][direction][thr]
                n_t = len(trades)
                wins = sum(1 for t in trades if t == 'WIN')
                losses = sum(1 for t in trades if t == 'LOSS')
                n_settled = wins + losses

                if n_settled == 0:
                    by_thr[thr] = {'n': 0, 'wr': 0, 'pf': 0, 'ci': [0,0], 'stable': False}
                    continue

                wr = wins / n_settled
                pf = wins / losses if losses > 0 else (99.0 if wins > 0 else 0.0)

                # Wilson CI for PF proxy (wr based)
                z = 1.96
                d = 1 + z**2/n_settled
                cp = (wr + z**2/(2*n_settled)) / d
                cm = z * math.sqrt(wr*(1-wr)/n_settled + z**2/(4*n_settled**2)) / d
                ci_lo = max(0, cp - cm)
                ci_hi = min(1, cp + cm)
                # PF CI
                pf_lo = ci_lo / (1 - ci_lo + 1e-9)
                pf_hi = ci_hi / (1 - ci_hi + 1e-9)

                stable = (n_settled >= MIN_N_FOR_STABLE and ci_lo > 0.5)

                by_thr[thr] = {
                    'n':      n_settled,
                    'wr':     round(wr, 4),
                    'pf':     round(pf, 4),
                    'ci':     [round(pf_lo, 3), round(pf_hi, 3)],
                    'stable': stable,
                }

            # 找最优门槛（最大PF且stable）
            stable_entries = [(thr, by_thr[thr]) for thr in SCORE_THRESHOLDS
                              if by_thr[thr].get('stable')]
            if stable_entries:
                best_thr, best_data = max(stable_entries, key=lambda x: x[1]['pf'])
            elif any(by_thr[t]['n'] > 0 for t in SCORE_THRESHOLDS):
                # 退而求其次：取n最大的
                best_thr = max(SCORE_THRESHOLDS, key=lambda t: by_thr[t].get('n', 0))
                best_data = by_thr[best_thr]
            else:
                best_thr, best_data = SCORE_THRESHOLDS[0], {'n':0,'wr':0,'pf':0,'ci':[0,0]}

            output[regime][direction] = {
                'optimal_threshold': best_thr,
                'best_pf':           round(best_data.get('pf', 0), 4),
                'best_wr':           round(best_data.get('wr', 0), 4),
                'best_n':            best_data.get('n', 0),
                'by_threshold':      by_thr,
            }

    return output


# ── 主训练循环 ──────────────────────────────────────────
def run_full_training(n_iter_per_cell: int = N_ITER_PER_CELL,
                       syms: list = None) -> dict:
    if syms is None:
        syms = CORE_SYMS

    ts_start = time.time()
    ts_str   = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')

    all_results = {
        '_meta': {
            'ts':               ts_str,
            'version':          'full_regime_bidirectional_v1',
            'n_iter_per_cell':  n_iter_per_cell,
            'syms':             syms,
            'regimes':          ALL_REGIMES,
            'directions':       BOTH_DIRECTIONS,
            'thresholds':       SCORE_THRESHOLDS,
            'note':             '补齐N14铁证做多短板，全体制双向完整训练',
        },
        'results': {},
    }

    print(f'[全体制双向训练] 启动')
    print(f'  标的: {len(syms)}个  体制: {len(ALL_REGIMES)}种  方向: 双向  迭代/格: {n_iter_per_cell:,}')
    print(f'  总格数: {len(syms) * len(ALL_REGIMES) * 2 * len(TIMEFRAMES)}')
    print(f'  预计总迭代: ~{len(syms) * n_iter_per_cell:,}')
    print()

    for i, sym in enumerate(syms):
        t0 = time.time()
        sym_results = {}

        for tf in TIMEFRAMES:
            r = run_sym_regime_backtest(sym, tf, n_iter_per_cell)
            if r:
                sym_results[tf] = r

        elapsed = time.time() - t0
        all_results['results'][sym] = sym_results

        # 打印进度
        if sym_results:
            # 统计这个标的的关键数字
            summary_parts = []
            for tf in TIMEFRAMES:
                if tf not in sym_results: continue
                for regime in ['BEAR_TREND', 'BULL_TREND', 'CHOP']:
                    if regime not in sym_results[tf]: continue
                    for d in ['LONG', 'SHORT']:
                        data = sym_results[tf][regime].get(d, {})
                        n = data.get('best_n', 0)
                        pf = data.get('best_pf', 0)
                        thr = data.get('optimal_threshold', '?')
                        if n > 0:
                            summary_parts.append(f'{tf}_{regime[:4]}_{d[0]}(n={n},pf={pf:.2f},thr={thr})')
            print(f'  [{i+1}/{len(syms)}] {sym.upper():<15} {elapsed:.1f}s | {" | ".join(summary_parts[:3])}')
        else:
            print(f'  [{i+1}/{len(syms)}] {sym.upper():<15} 无数据')

    # 计算跨标的汇总矩阵
    print('\n[汇总] 计算跨标的平均矩阵...')
    summary_matrix = {}
    for regime in ALL_REGIMES:
        summary_matrix[regime] = {}
        for direction in BOTH_DIRECTIONS:
            pfs = []
            wrs = []
            ns  = []
            opt_thrs = []
            for sym in syms:
                for tf in TIMEFRAMES:
                    d = all_results['results'].get(sym, {}).get(tf, {}).get(regime, {}).get(direction, {})
                    if d.get('best_n', 0) >= MIN_N_FOR_STABLE:
                        pfs.append(d['best_pf'])
                        wrs.append(d['best_wr'])
                        ns.append(d['best_n'])
                        opt_thrs.append(d['optimal_threshold'])

            if pfs:
                avg_pf  = sum(pfs) / len(pfs)
                avg_wr  = sum(wrs) / len(wrs)
                med_thr = sorted(opt_thrs)[len(opt_thrs)//2]
                summary_matrix[regime][direction] = {
                    'avg_pf':           round(avg_pf, 4),
                    'avg_wr':           round(avg_wr, 4),
                    'median_opt_thr':   med_thr,
                    'n_syms_stable':    len(pfs),
                    'preferred':        direction if avg_pf > 1.1 else 'WEAK',
                }
            else:
                summary_matrix[regime][direction] = {
                    'avg_pf': 0, 'avg_wr': 0, 'median_opt_thr': 150,
                    'n_syms_stable': 0, 'preferred': 'INSUFFICIENT_DATA',
                }

    all_results['summary_matrix'] = summary_matrix
    all_results['elapsed_s'] = round(time.time() - ts_start, 1)

    # 保存
    out_path = RESULTS / f'full_regime_bidirectional_{ts_str}.json'
    out_path.write_text(json.dumps(all_results, ensure_ascii=False, indent=2))
    print(f'\n✅ 结果已保存: {out_path}')
    print(f'   总耗时: {all_results["elapsed_s"]}s ({all_results["elapsed_s"]/60:.1f}min)')

    return all_results


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--n-iter', type=int, default=N_ITER_PER_CELL)
    p.add_argument('--syms',   nargs='+', default=None)
    p.add_argument('--quick',  action='store_true', help='快速测试(3个标的×1000次)')
    args = p.parse_args()

    if args.quick:
        run_full_training(n_iter_per_cell=1000, syms=['btcusdt','ethusdt','bnbusdt'])
    else:
        run_full_training(n_iter_per_cell=args.n_iter, syms=args.syms)
