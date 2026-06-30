#!/usr/bin/env python3
"""
达摩院 · 4H全体制双向训练 v1.0
设计院 · 2026-06-11

目标：用真实brahma_analyze评分，对4H周期全体制×LONG+SHORT完整训练
     补齐LONG方向数据空白，建立数据驱动的完整体制×方向×门槛矩阵

架构：
  OfflineDataFeed → 历史parquet注入 → 真实brahma评分（非代理）
  每个采样点：真实score + regime → 体制×方向×门槛分桶统计

覆盖：
  - 标的：BTC/ETH/BNB/SOL/DOGE/ADA/AVAX/LINK/DOT/ATOM/TRX/LTC（12个核心）
  - 体制：BULL_TREND/BULL_PEAK/BEAR_TREND/BEAR_EARLY/BEAR_RECOVERY/CHOP（6种）
  - 方向：LONG + SHORT（双向全覆盖，不跳过任何体制）
  - 周期：4H主 + 1H/1D辅助
  - 门槛扫描：100~170（步长5）

关键设计：
  ① 不用RSI代理评分，直接调brahma_brain.analyze（真实38维）
  ② 采样点覆盖所有体制，BULL/BEAR/CHOP等比采样
  ③ 交易模拟用ATR止损+RR=2.0（标准合约交易设定）
  ④ Wilson 95%置信区间标注每个格子的统计可靠性
"""

import sys, json, time, math, random, warnings
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

# ── 配置 ───────────────────────────────────────────────────────
TARGET_SAMPLES_PER_REGIME = 800   # 每个体制目标采样数（LONG+SHORT各800）
SCORE_THRESHOLDS  = list(range(100, 175, 5))
RR_RATIO          = 2.0            # TP = RR × SL距离
ATR_SL_MULT       = 1.5            # 止损 = entry ± ATR × 1.5
HOLD_MAX_BARS     = 30             # 最大持仓4H K线数（=120H=5天）
MIN_N_STABLE      = 20             # Wilson稳定最小样本

CORE_SYMS = [
    'btcusdt', 'ethusdt', 'bnbusdt', 'solusdt', 'dogeusdt',
    'adausdt', 'avaxusdt', 'linkusdt', 'dotusdt', 'atomusdt',
    'trxusdt', 'ltcusdt',
]

ALL_REGIMES   = ['BULL_TREND','BULL_PEAK','BEAR_TREND','BEAR_EARLY','BEAR_RECOVERY','CHOP']
DIRECTIONS    = ['LONG', 'SHORT']

# ── 指标计算 ──────────────────────────────────────────────────
def add_features(df: pd.DataFrame) -> pd.DataFrame:
    c = df['close'].astype(float)
    h = df['high'].astype(float)
    l = df['low'].astype(float)

    df['ema21']  = c.ewm(span=21).mean()
    df['ema55']  = c.ewm(span=55).mean()
    df['ema200'] = c.ewm(span=200).mean()

    delta = c.diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    df['rsi'] = 100 - 100 / (1 + gain / loss.replace(0, 1e-9))

    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    df['atr'] = tr.rolling(14).mean()
    return df

def classify_regime_4h(row) -> str:
    rsi  = row.get('rsi', 50)
    c    = row.get('close', 0)
    e21  = row.get('ema21', c)
    e55  = row.get('ema55', c)
    e200 = row.get('ema200', c)
    if pd.isna(rsi) or pd.isna(e200): return 'CHOP'
    above200     = c > e200
    e21_above_e55 = e21 > e55
    if above200 and e21_above_e55:
        return 'BULL_PEAK' if rsi > 72 else 'BULL_TREND'
    if not above200:
        if rsi < 28:    return 'BEAR_RECOVERY'
        if c < e200 * 0.87: return 'BEAR_TREND'
        return 'BEAR_EARLY'
    return 'BEAR_RECOVERY' if (above200 and not e21_above_e55 and rsi > 40) else 'CHOP'

# ── 数据加载 ──────────────────────────────────────────────────
def load_sym_data(sym: str) -> dict:
    """加载标的的多周期parquet数据"""
    data = {}
    for tf in ['15m', '1h', '4h', '1d']:
        f = DATA_DIR / f'{sym}_{tf}_2018_2026.parquet'
        if f.exists():
            try:
                df = pd.read_parquet(f)
                df.index = pd.to_datetime(df.index, utc=True)
                df = df.sort_index()
                data[tf] = df
            except:
                pass
    return data

# ── 交易模拟（基于4H K线）─────────────────────────────────────
def simulate_4h_trade(df_4h: pd.DataFrame, bar_idx: int,
                       direction: str, atr: float) -> dict:
    """从bar_idx开始模拟一笔4H合约交易"""
    if atr <= 0 or pd.isna(atr): return {'result': 'ERROR', 'pnl': 0}

    entry = float(df_4h.iloc[bar_idx]['close'])
    if direction == 'SHORT':
        sl = entry + ATR_SL_MULT * atr
        tp = entry - ATR_SL_MULT * atr * RR_RATIO
    else:
        sl = entry - ATR_SL_MULT * atr
        tp = entry + ATR_SL_MULT * atr * RR_RATIO

    for j in range(1, min(HOLD_MAX_BARS + 1, len(df_4h) - bar_idx)):
        future = df_4h.iloc[bar_idx + j]
        hi = float(future['high'])
        lo = float(future['low'])

        if direction == 'SHORT':
            if lo <= tp:  return {'result': 'WIN',  'pnl': +RR_RATIO}
            if hi >= sl:  return {'result': 'LOSS', 'pnl': -1.0}
        else:
            if hi >= tp:  return {'result': 'WIN',  'pnl': +RR_RATIO}
            if lo <= sl:  return {'result': 'LOSS', 'pnl': -1.0}

    return {'result': 'TIMEOUT', 'pnl': 0.0}

# ── Wilson置信区间 ─────────────────────────────────────────────
def wilson_ci(wins: int, total: int, z: float = 1.96) -> tuple:
    if total == 0: return (0.0, 0.0)
    p = wins / total
    d = 1 + z**2 / total
    c = (p + z**2 / (2 * total)) / d
    m = (z * math.sqrt(p * (1-p) / total + z**2 / (4 * total**2))) / d
    return (round(max(0, c-m), 4), round(min(1, c+m), 4))

# ── 核心训练：单标的 ──────────────────────────────────────────
def train_sym(sym: str, feed_cls, verbose: bool = True) -> dict:
    """
    对单个标的进行4H全体制双向训练
    使用真实brahma_analyze评分
    """
    sym_up = sym.upper()
    data   = load_sym_data(sym)

    df_4h = data.get('4h')
    if df_4h is None or len(df_4h) < 500:
        return {}

    df_4h = add_features(df_4h.copy()).dropna(subset=['rsi','ema200','atr'])

    # 计算每行体制
    df_4h['regime'] = df_4h.apply(classify_regime_4h, axis=1)

    # 按体制分组索引（从200开始避免指标边界）
    regime_indices = defaultdict(list)
    for i in range(200, len(df_4h) - HOLD_MAX_BARS - 5):
        r = df_4h.iloc[i]['regime']
        regime_indices[r].append(i)

    if verbose:
        total_pts = sum(len(v) for v in regime_indices.values())
        regime_dist = {r: len(v) for r,v in regime_indices.items()}
        print(f'    体制分布（4H）: {regime_dist}  总={total_pts}')

    # 初始化OfflineDataFeed
    try:
        from dharma.offline_engine import OfflineDataFeed, FullSystemScorer
        df_map = {sym_up: data}
        feed   = OfflineDataFeed(df_map)
        scorer = FullSystemScorer(feed)
    except Exception as e:
        if verbose: print(f'    ⚠️ OfflineDataFeed初始化失败: {e}')
        return {}

    # 结果容器：regime → direction → threshold → [results]
    results = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    score_cache = {}  # bar_idx → score (避免重复计算)

    # 对每个体制等比采样
    for regime in ALL_REGIMES:
        indices = regime_indices.get(regime, [])
        if not indices:
            continue

        # 随机采样，上限TARGET_SAMPLES_PER_REGIME
        n_sample = min(len(indices), TARGET_SAMPLES_PER_REGIME)
        sampled  = random.sample(indices, n_sample)

        scored_count = 0
        for bar_idx in sampled:
            # 获取真实brahma评分（尝试SHORT和LONG两个方向）
            for direction in DIRECTIONS:
                # 缓存key
                cache_key = (bar_idx, direction)
                if cache_key in score_cache:
                    score_result = score_cache[cache_key]
                else:
                    try:
                        score_result = scorer.score(sym_up, direction, bar_idx)
                        score_cache[cache_key] = score_result
                    except Exception:
                        continue

                score = score_result.get('total', 0) or 0
                if score <= 0: continue

                # 获取ATR（4H）
                atr = float(df_4h.iloc[bar_idx].get('atr', 0) or 0)
                if atr <= 0: continue

                # 模拟交易
                trade = simulate_4h_trade(df_4h, bar_idx, direction, atr)
                result_str = trade['result']
                if result_str == 'ERROR': continue

                # 记录到各门槛桶
                for thr in SCORE_THRESHOLDS:
                    if score >= thr:
                        results[regime][direction][thr].append(result_str)

            scored_count += 1

        if verbose:
            print(f'    {regime:<16}: 采样{n_sample} 评分{scored_count}')

    # 聚合统计
    output = {}
    for regime in ALL_REGIMES:
        output[regime] = {}
        for direction in DIRECTIONS:
            by_thr = {}
            for thr in SCORE_THRESHOLDS:
                trades   = results[regime][direction][thr]
                n_t      = len(trades)
                wins     = sum(1 for t in trades if t == 'WIN')
                losses   = sum(1 for t in trades if t == 'LOSS')
                timeouts = n_t - wins - losses
                n_settled = wins + losses

                if n_settled == 0:
                    by_thr[thr] = {'n':0,'wins':0,'losses':0,'to':0,'wr':0,'pf':0,'ci':[0,0],'stable':False}
                    continue

                wr  = wins / n_settled
                pf  = wins / losses if losses > 0 else (9.99 if wins > 0 else 0.0)
                ci  = wilson_ci(wins, n_settled)
                pf_lo = ci[0]/(1-ci[0]+1e-9) if ci[0] < 1 else 99.0
                pf_hi = ci[1]/(1-ci[1]+1e-9) if ci[1] < 1 else 99.0
                stable = (n_settled >= MIN_N_STABLE and ci[0] > 0.5)

                by_thr[thr] = {
                    'n':      n_settled,
                    'wins':   wins,
                    'losses': losses,
                    'to':     timeouts,
                    'wr':     round(wr, 4),
                    'pf':     round(pf, 4),
                    'ci':     [round(pf_lo,3), round(pf_hi,3)],
                    'stable': stable,
                }

            # 最优门槛
            stable_list = [(thr, by_thr[thr]) for thr in SCORE_THRESHOLDS
                           if by_thr[thr].get('stable')]
            if stable_list:
                best_thr, best_d = max(stable_list, key=lambda x: x[1]['pf'])
            else:
                valid = [(thr, by_thr[thr]) for thr in SCORE_THRESHOLDS
                         if by_thr[thr]['n'] >= 5]
                if valid:
                    best_thr, best_d = max(valid, key=lambda x: x[1]['pf'])
                else:
                    best_thr, best_d = SCORE_THRESHOLDS[0], by_thr[SCORE_THRESHOLDS[0]]

            output[regime][direction] = {
                'best_thr': best_thr,
                'best_pf':  round(best_d.get('pf',0), 4),
                'best_wr':  round(best_d.get('wr',0), 4),
                'best_n':   best_d.get('n', 0),
                'best_ci':  best_d.get('ci', [0,0]),
                'by_threshold': by_thr,
            }

    return output


# ── 主训练入口 ──────────────────────────────────────────────────
def run_4h_training(syms: list = None, verbose: bool = True) -> dict:
    if syms is None:
        syms = CORE_SYMS

    ts_str = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    t0     = time.time()

    all_results = {
        '_meta': {
            'ts':             ts_str,
            'version':        '4h_bidirectional_v1',
            'syms':           syms,
            'regimes':        ALL_REGIMES,
            'directions':     DIRECTIONS,
            'thresholds':     SCORE_THRESHOLDS,
            'target_n/regime': TARGET_SAMPLES_PER_REGIME,
            'rr':             RR_RATIO,
            'atr_sl':         ATR_SL_MULT,
            'scorer':         'true_brahma_offline_engine',
            'note':           '4H全体制双向，真实brahma评分，填补LONG方向空白',
        },
        'sym_results': {},
    }

    print('=' * 65)
    print('  达摩院 · 4H全体制双向训练（真实brahma_analyze评分）')
    print('=' * 65)
    print(f'  标的: {len(syms)}  体制: {len(ALL_REGIMES)}  双向  目标/格: {TARGET_SAMPLES_PER_REGIME}')
    print(f'  评分器: OfflineDataFeed → 真实brahma_brain引擎')
    print()

    # 延迟导入（确保路径正确）
    try:
        from dharma.offline_engine import OfflineDataFeed, FullSystemScorer
        feed_cls = (OfflineDataFeed, FullSystemScorer)
    except Exception as e:
        print(f'⚠️ 无法导入OfflineDataFeed: {e}')
        return {}

    for i, sym in enumerate(syms):
        t_sym = time.time()
        print(f'\n[{i+1}/{len(syms)}] {sym.upper()}')

        sym_res = train_sym(sym, feed_cls, verbose=verbose)
        all_results['sym_results'][sym] = sym_res

        elapsed_sym = time.time() - t_sym

        # 打印摘要
        if sym_res:
            for regime in ['BULL_TREND', 'BEAR_TREND', 'CHOP']:
                if regime not in sym_res: continue
                parts = []
                for d in ['LONG','SHORT']:
                    dd = sym_res[regime].get(d,{})
                    n  = dd.get('best_n',0)
                    pf = dd.get('best_pf',0)
                    thr = dd.get('best_thr','?')
                    ci = dd.get('best_ci',[0,0])
                    stable = '✅' if n >= MIN_N_STABLE else '⚠️'
                    parts.append(f'{d}(n={n},PF={pf:.2f},thr={thr},CI=[{ci[0]:.2f},{ci[1]:.2f}]){stable}')
                print(f'  {regime:<16}: {" | ".join(parts)}')
        print(f'  耗时: {elapsed_sym:.1f}s')

    # 跨标的汇总矩阵
    print('\n[汇总] 计算跨标的平均矩阵...')
    summary = {}
    for regime in ALL_REGIMES:
        summary[regime] = {}
        for direction in DIRECTIONS:
            pfs, wrs, ns, thrs = [], [], [], []
            for sym in syms:
                d = all_results['sym_results'].get(sym,{}).get(regime,{}).get(direction,{})
                n = d.get('best_n',0)
                if n >= MIN_N_STABLE:
                    pfs.append(d['best_pf'])
                    wrs.append(d['best_wr'])
                    ns.append(n)
                    thrs.append(d['best_thr'])

            if pfs:
                avg_pf  = round(sum(pfs)/len(pfs), 4)
                avg_wr  = round(sum(wrs)/len(wrs), 4)
                med_thr = sorted(thrs)[len(thrs)//2]
                n_syms_stable = len(pfs)
                verdict = ('STRONG' if avg_pf >= 1.3 else
                           'POSITIVE' if avg_pf >= 1.1 else
                           'MARGINAL' if avg_pf >= 0.9 else 'NEGATIVE')
            else:
                avg_pf, avg_wr, med_thr, n_syms_stable, verdict = 0, 0, 150, 0, 'INSUFFICIENT_DATA'

            summary[regime][direction] = {
                'avg_pf':         avg_pf,
                'avg_wr':         round(avg_wr*100, 1),
                'median_opt_thr': med_thr,
                'n_syms_stable':  n_syms_stable,
                'verdict':        verdict,
            }

    all_results['summary_matrix'] = summary
    all_results['elapsed_s'] = round(time.time() - t0, 1)

    # 保存
    out_path = RESULTS / f'train_4h_bidirectional_{ts_str}.json'
    out_path.write_text(json.dumps(all_results, ensure_ascii=False, indent=2))
    print(f'\n✅ 保存: {out_path}')
    print(f'   总耗时: {all_results["elapsed_s"]:.0f}s ({all_results["elapsed_s"]/60:.1f}min)')

    # 打印汇总矩阵
    print('\n' + '='*65)
    print('  跨标的汇总矩阵（平均PF）')
    print('='*65)
    print(f'{"体制":<18} {"方向":<8} {"avg_PF":>8} {"avg_WR":>8} {"最优thr":>8} {"稳定标的":>8} {"评级"}')
    print('-'*65)
    for regime in ALL_REGIMES:
        for direction in DIRECTIONS:
            s = summary[regime][direction]
            rec = {'STRONG':'✅✅','POSITIVE':'✅','MARGINAL':'⚠️','NEGATIVE':'❌','INSUFFICIENT_DATA':'—'}.get(s['verdict'],'?')
            print(f'{regime:<18} {direction:<8} {s["avg_pf"]:>8.3f} {s["avg_wr"]:>7.1f}% {s["median_opt_thr"]:>8} {s["n_syms_stable"]:>8}  {rec} {s["verdict"]}')
        print()

    return all_results


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--syms',  nargs='+', default=None)
    p.add_argument('--quick', action='store_true', help='快速测试(BTC+ETH)')
    args = p.parse_args()

    if args.quick:
        run_4h_training(syms=['btcusdt','ethusdt'], verbose=True)
    else:
        run_4h_training(syms=args.syms, verbose=True)
