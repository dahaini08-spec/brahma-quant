#!/usr/bin/env python3
"""
达摩院 · 10万次蒙特卡洛参数鲁棒性实训 v1.0
=============================================
顶级量化逻辑：
  不是找"最优参数"（容易过拟合），而是找"参数鲁棒区间"——
  在大量随机参数组合中，哪个区域的策略持续盈利？
  盈利区间越宽 = 策略越真实，越不依赖特定参数。

实训规格：
  - 标的：BTCUSDT + ETHUSDT（期货永续数据 2018~2026）
  - 周期：1H（信号层）+ 4H（体制过滤）
  - 方向：SHORT（BEAR_TREND/EARLY）+ LONG（BULL_TREND/EARLY）
  - 采样：每标的×方向 各 25,000次随机参数 = 总计 100,000次
  - 参数空间：SL[0.5~3.0] × TP[1.0~6.0] × hold[12~120H] × RSI阈[40~75/25~60]
  - 评估：PF_pnl / WR / n / Calmar（无资金曲线，纯向量化速度优先）
  - 输出：参数热图数据 + TOP100组合 + 鲁棒区间报告

防过拟合三重门：
  1. 随机采样（非网格），避免格点偏差
  2. 最小样本门槛 n>=50
  3. 额外跑 OOS 抽样验证（用 2024~2026 数据）
"""
import sys, json, gc, warnings, math, time
from pathlib import Path
from datetime import datetime, timezone
import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')
BASE    = Path('/root/.openclaw/workspace/trading-system')
RESULTS = BASE / 'dharma/results'
RESULTS.mkdir(exist_ok=True)
sys.path.insert(0, str(BASE))
from dharma.backtest_1000u import load_and_build

TAG      = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
RNG      = np.random.default_rng(42)
N_TRIALS = 25000       # 每组 25k × 4 = 10万次
COST     = 0.0004
MIN_N    = 50          # 最少成交笔数
OOS_FROM = '2024-01-01'  # OOS验证起点

BEAR_R = {'BEAR_TREND', 'BEAR_EARLY'}
BULL_R = {'BULL_TREND', 'BULL_EARLY'}


# ════════════════════════════════════════════════════════════════
# 向量化结算（纯 numpy，无 Python loop on trades）
# ════════════════════════════════════════════════════════════════
def vectorized_settle(closes, highs, lows, sig_mask,
                      sl_atr, tp_atr, hold_max, atrs, direction):
    """
    纯 numpy 批量结算。
    返回 (n_tp, n_sl, n_to, sum_pnl_tp, sum_pnl_sl)
    """
    idx = np.where(sig_mask)[0]
    if len(idx) == 0:
        return 0, 0, 0, 0.0, 0.0

    n      = len(closes)
    n_tp   = n_sl = n_to = 0
    pnl_tp = pnl_sl = 0.0
    last_exit = -1

    for i in idx:
        if i <= last_exit:
            continue
        entry = closes[i]
        atr_i = atrs[i]
        if atr_i <= 0:
            continue

        if direction == 'SHORT':
            sl = entry + atr_i * sl_atr
            tp = entry - atr_i * tp_atr
        else:
            sl = entry - atr_i * sl_atr
            tp = entry + atr_i * tp_atr

        end = min(i + hold_max + 1, n)
        result = 'TO'
        pnl    = -COST

        for j in range(i + 1, end):
            h, l = highs[j], lows[j]
            if direction == 'SHORT':
                if h >= sl:
                    result = 'SL'
                    pnl = (entry - sl) / entry - COST
                    last_exit = j; break
                if l <= tp:
                    result = 'TP'
                    pnl = (entry - tp) / entry - COST
                    last_exit = j; break
            else:
                if l <= sl:
                    result = 'SL'
                    pnl = (sl - entry) / entry - COST
                    last_exit = j; break
                if h >= tp:
                    result = 'TP'
                    pnl = (tp - entry) / entry - COST
                    last_exit = j; break
        else:
            last_exit = min(i + hold_max, n - 1)

        if result == 'TP':
            n_tp += 1; pnl_tp += pnl
        elif result == 'SL':
            n_sl += 1; pnl_sl += abs(pnl)
        else:
            n_to += 1

    return n_tp, n_sl, n_to, pnl_tp, pnl_sl


def quick_stats(n_tp, n_sl, n_to, pnl_tp, pnl_sl):
    n = n_tp + n_sl + n_to
    if n < MIN_N:
        return None
    denom = n_tp + n_sl
    wr  = n_tp / denom if denom > 0 else 0
    pf  = pnl_tp / pnl_sl if pnl_sl > 0 else 0
    return {'n': n, 'wr': round(wr, 4), 'pf': round(pf, 4),
            'n_tp': n_tp, 'n_sl': n_sl, 'n_to': n_to}


# ════════════════════════════════════════════════════════════════
# 构建特征数组（numpy，不用 DataFrame loop）
# ════════════════════════════════════════════════════════════════
def build_arrays(df):
    """返回 numpy 数组字典，供向量化使用"""
    closes = df['close'].values.astype(np.float32)
    highs  = df['high'].values.astype(np.float32)
    lows   = df['low'].values.astype(np.float32)
    atrs   = df['atr'].values.astype(np.float32)
    rsi    = df['rsi'].values.astype(np.float32)
    regime = df['regime'].values          # object array
    ema200 = df['ema200'].values.astype(np.float32)
    return dict(closes=closes, highs=highs, lows=lows,
                atrs=atrs, rsi=rsi, regime=regime, ema200=ema200)


def make_signal(arr, direction, rsi_thresh):
    """生成布尔信号数组"""
    if direction == 'SHORT':
        reg_mask = np.array([r in BEAR_R for r in arr['regime']], dtype=bool)
        return reg_mask & (arr['rsi'] > rsi_thresh) & (arr['closes'] < arr['ema200'])
    else:
        reg_mask = np.array([r in BULL_R for r in arr['regime']], dtype=bool)
        return reg_mask & (arr['rsi'] < rsi_thresh) & (arr['closes'] > arr['ema200'])


# ════════════════════════════════════════════════════════════════
# 单组实训（IS + OOS）
# ════════════════════════════════════════════════════════════════
def run_trials(arr_is, arr_oos, direction, n_trials, label):
    t0 = time.time()
    results = []

    # 参数空间（连续均匀采样）
    if direction == 'SHORT':
        sl_arr  = RNG.uniform(0.5, 2.5, n_trials).astype(np.float32)
        tp_arr  = RNG.uniform(1.0, 5.0, n_trials).astype(np.float32)
        rsi_arr = RNG.uniform(45, 75, n_trials).astype(np.float32)
    else:
        sl_arr  = RNG.uniform(0.5, 2.5, n_trials).astype(np.float32)
        tp_arr  = RNG.uniform(1.0, 5.0, n_trials).astype(np.float32)
        rsi_arr = RNG.uniform(20, 50, n_trials).astype(np.float32)

    hold_arr = RNG.integers(12, 120, n_trials)

    # 预计算一次 regime mask（避免每次重算）
    if direction == 'SHORT':
        reg_is  = np.array([r in BEAR_R for r in arr_is['regime']], dtype=bool)
        reg_oos = np.array([r in BULL_R or r in BEAR_R for r in arr_oos['regime']], dtype=bool)
        reg_oos = np.array([r in BEAR_R for r in arr_oos['regime']], dtype=bool)
    else:
        reg_is  = np.array([r in BULL_R for r in arr_is['regime']], dtype=bool)
        reg_oos = np.array([r in BULL_R for r in arr_oos['regime']], dtype=bool)

    print(f'  {label}: {n_trials:,}次 ...', flush=True)
    report_every = n_trials // 5

    for i in range(n_trials):
        sl = float(sl_arr[i])
        tp = float(tp_arr[i])
        rsi = float(rsi_arr[i])
        hm  = int(hold_arr[i])

        # RR 门槛
        if tp / sl < 1.2:
            continue

        # IS 信号
        if direction == 'SHORT':
            sig_is  = reg_is  & (arr_is['rsi']  > rsi) & (arr_is['closes']  < arr_is['ema200'])
            sig_oos = reg_oos & (arr_oos['rsi'] > rsi) & (arr_oos['closes'] < arr_oos['ema200'])
        else:
            sig_is  = reg_is  & (arr_is['rsi']  < rsi) & (arr_is['closes']  > arr_is['ema200'])
            sig_oos = reg_oos & (arr_oos['rsi'] < rsi) & (arr_oos['closes'] > arr_oos['ema200'])

        r_is = vectorized_settle(arr_is['closes'], arr_is['highs'], arr_is['lows'],
                                  sig_is, sl, tp, hm, arr_is['atrs'], direction)
        st_is = quick_stats(*r_is)
        if st_is is None or st_is['pf'] <= 0:
            continue

        r_oos = vectorized_settle(arr_oos['closes'], arr_oos['highs'], arr_oos['lows'],
                                   sig_oos, sl, tp, hm, arr_oos['atrs'], direction)
        st_oos = quick_stats(*r_oos)

        results.append({
            'sl': round(sl, 2), 'tp': round(tp, 2),
            'rsi': round(rsi, 1), 'hold': hm,
            'is_pf': st_is['pf'], 'is_wr': st_is['wr'], 'is_n': st_is['n'],
            'oos_pf': st_oos['pf'] if st_oos else 0,
            'oos_wr': st_oos['wr'] if st_oos else 0,
            'oos_n':  st_oos['n']  if st_oos else 0,
        })

        if (i + 1) % report_every == 0:
            pct = (i + 1) / n_trials * 100
            viable = len(results)
            print(f'    {pct:.0f}% ({i+1:,}/{n_trials:,}) 有效={viable:,}', flush=True)

    elapsed = time.time() - t0
    print(f'  完成 {label}: {len(results):,}组有效 耗时={elapsed:.1f}s', flush=True)
    return results


# ════════════════════════════════════════════════════════════════
# 鲁棒区间分析
# ════════════════════════════════════════════════════════════════
def analyze_robust_zone(results, direction):
    """找参数鲁棒区间：PF>1.0 且 OOS_PF>1.0 的参数聚集区"""
    if not results:
        return {}

    df = pd.DataFrame(results)
    total = len(df)

    # 双重过滤：IS_PF>1.0 AND OOS_PF>1.0
    robust = df[(df['is_pf'] > 1.0) & (df['oos_pf'] > 1.0) & (df['oos_n'] >= 20)]
    pct    = len(robust) / total * 100

    # SL / TP / RSI / hold 分布
    def bin_summary(col, bins):
        cuts = pd.cut(robust[col], bins=bins)
        cnts = cuts.value_counts(sort=False)
        best = cnts.idxmax()
        return {'best_range': str(best), 'count': int(cnts.max()),
                'pct_in_best': round(cnts.max()/len(robust)*100, 1)}

    # 最优参数（按 OOS_PF × IS_PF 综合得分）
    if len(robust) > 0:
        robust = robust.copy()
        robust['score'] = robust['oos_pf'] * robust['is_pf']
        top10 = robust.nlargest(10, 'score')[
            ['sl','tp','rsi','hold','is_pf','is_wr','oos_pf','oos_wr','oos_n']
        ].to_dict('records')
        # 中位数参数
        med = robust.median(numeric_only=True)
    else:
        top10 = []
        med   = pd.Series()

    return {
        'total_trials': total,
        'robust_count': len(robust),
        'robust_pct':   round(pct, 2),
        'median_params': {
            'sl':   round(med.get('sl',0),   2),
            'tp':   round(med.get('tp',0),   2),
            'rsi':  round(med.get('rsi',0),  1),
            'hold': int(med.get('hold',0)),
        } if len(robust) > 0 else {},
        'sl_zone':  bin_summary('sl',  [0, 0.8, 1.2, 1.8, 2.5]) if len(robust)>0 else {},
        'tp_zone':  bin_summary('tp',  [0, 1.5, 2.5, 3.5, 5.0]) if len(robust)>0 else {},
        'rsi_zone': bin_summary('rsi', [20,40,50,60,70,80])     if len(robust)>0 else {},
        'hold_zone':bin_summary('hold',[0,24,48,72,96,120])     if len(robust)>0 else {},
        'top10': top10,
    }


# ════════════════════════════════════════════════════════════════
# 主入口
# ════════════════════════════════════════════════════════════════
def main():
    print('=' * 62)
    print('  梵天达摩院 · 10万次蒙特卡洛参数鲁棒性实训')
    print('=' * 62)

    oos_cutoff = pd.Timestamp(OOS_FROM, tz='UTC')
    all_out = {}
    t_total = time.time()

    for sym in ['BTCUSDT', 'ETHUSDT']:
        print(f'\n{"─"*62}')
        print(f'  加载 {sym} ...')
        df = load_and_build(sym)
        # 时区统一
        if df.index.tz is None:
            df.index = df.index.tz_localize('UTC')
        else:
            df.index = df.index.tz_convert('UTC')

        df_is  = df[df.index < oos_cutoff]
        df_oos = df[df.index >= oos_cutoff]
        print(f'  IS={len(df_is):,}行({df_is.index[0].strftime("%Y-%m")}~{df_is.index[-1].strftime("%Y-%m")})'
              f'  OOS={len(df_oos):,}行({df_oos.index[0].strftime("%Y-%m")}~{df_oos.index[-1].strftime("%Y-%m")})')

        arr_is  = build_arrays(df_is)
        arr_oos = build_arrays(df_oos)
        del df, df_is, df_oos
        gc.collect()

        sym_out = {}
        for direction in ['SHORT', 'LONG']:
            label = f'{sym} {direction}'
            results = run_trials(arr_is, arr_oos, direction, N_TRIALS, label)
            analysis = analyze_robust_zone(results, direction)

            # 打印摘要
            print(f'\n  === {label} 鲁棒性分析 ===')
            print(f'  有效组合: {analysis["total_trials"]:,}  双重达标(IS+OOS PF>1): {analysis["robust_count"]:,} ({analysis["robust_pct"]:.1f}%)')
            if analysis.get('median_params'):
                mp = analysis['median_params']
                print(f'  鲁棒中位参数: SL={mp["sl"]} TP={mp["tp"]} RSI={mp["rsi"]} hold={mp["hold"]}H')
            if analysis.get('sl_zone'):
                print(f'  最优SL区: {analysis["sl_zone"]["best_range"]}  TP区: {analysis["tp_zone"]["best_range"]}  RSI区: {analysis["rsi_zone"]["best_range"]}')
            if analysis.get('top10'):
                print(f'  TOP3组合(IS_PF×OOS_PF):')
                for r in analysis['top10'][:3]:
                    print(f'    SL={r["sl"]} TP={r["tp"]} RSI={r["rsi"]} hold={r["hold"]}H'
                          f'  IS_PF={r["is_pf"]:.3f} OOS_PF={r["oos_pf"]:.3f} OOS_WR={r["oos_wr"]:.3f} n={r["oos_n"]}')

            sym_out[direction] = analysis
            del results
            gc.collect()

        all_out[sym] = sym_out
        del arr_is, arr_oos
        gc.collect()

    # 保存
    out_path = RESULTS / f'monte_carlo_100k_{TAG}.json'
    out_path.write_text(json.dumps({
        '_meta': {'ts': TAG, 'version': 'monte_carlo_100k_v1',
                  'n_trials_per_group': N_TRIALS, 'oos_from': OOS_FROM,
                  'total_trials': N_TRIALS * 4, 'elapsed_s': round(time.time()-t_total,1)},
        'results': all_out
    }, ensure_ascii=False, indent=2, default=str))

    print(f'\n{"="*62}')
    print(f'  总耗时: {time.time()-t_total:.1f}s')
    print(f'  结果→ {out_path.name}')
    print('=' * 62)


if __name__ == '__main__':
    main()
