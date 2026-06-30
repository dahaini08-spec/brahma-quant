"""
达摩院V5 - Combo Test
测试指标组合的协同效应
"""
import os
import json
import time
import random
import warnings
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime
from itertools import combinations
from collections import defaultdict

warnings.filterwarnings('ignore')

DATA_DIR = Path(__file__).parent / 'data'
RESULTS_DIR = Path(__file__).parent / 'results'
RESULTS_DIR.mkdir(exist_ok=True)

SAMPLE_SIZE = 5000
HOLDING_PERIOD = 12  # 固定使用12根K线


def load_parquet(path, sample_size=SAMPLE_SIZE):
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index, utc=True)
    df = df.sort_index()
    if len(df) > sample_size:
        start = random.randint(0, len(df) - sample_size)
        df = df.iloc[start:start + sample_size].copy()
    return df


def compute_indicators(df):
    close = df['close']
    high = df['high']
    low = df['low']
    volume = df['volume']

    df['ema20'] = close.ewm(span=20, adjust=False).mean()
    df['ema50'] = close.ewm(span=50, adjust=False).mean()
    df['ema200'] = close.ewm(span=200, adjust=False).mean()

    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=13, adjust=False).mean()
    avg_loss = loss.ewm(com=13, adjust=False).mean()
    rs = avg_gain / (avg_loss + 1e-10)
    df['rsi14'] = 100 - (100 / (1 + rs))

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    df['macd'] = ema12 - ema26
    df['macd_signal'] = df['macd'].ewm(span=9, adjust=False).mean()
    df['macd_hist'] = df['macd'] - df['macd_signal']

    bb_ma = close.rolling(20).mean()
    bb_std = close.rolling(20).std()
    df['bb_upper'] = bb_ma + 2 * bb_std
    df['bb_lower'] = bb_ma - 2 * bb_std
    bb_range = df['bb_upper'] - df['bb_lower']
    df['bb_pct'] = (close - df['bb_lower']) / (bb_range + 1e-10)

    df['vol_ma20'] = volume.rolling(20).mean()
    df['vol_ratio'] = volume / (df['vol_ma20'] + 1e-10)

    return df.dropna()


def classify_regime(row):
    rsi = row.get('rsi14', 50)
    bb_pct = row.get('bb_pct', 0.5)
    ema20 = row.get('ema20', row['close'])
    ema50 = row.get('ema50', row['close'])
    ema200 = row.get('ema200', row['close'])
    close = row['close']
    trend_bull = close > ema200 and ema20 > ema50
    trend_bear = close < ema200 and ema20 < ema50
    if rsi > 70 and trend_bull:
        return 'BULL_PEAK'
    elif rsi < 30 and trend_bear:
        return 'BEAR_CRASH'
    elif trend_bull and rsi > 50:
        return 'BULL_TREND'
    elif trend_bear and rsi < 50:
        return 'BEAR_TREND'
    elif close > ema200:
        return 'RECOVERY'
    elif bb_pct > 0.7:
        return 'CHOP_HIGH'
    elif bb_pct < 0.3:
        return 'CHOP_LOW'
    else:
        return 'CHOP'


def get_signal_matrix(df):
    """返回每个bar的信号字典: {bar_idx: {indicator: direction}}"""
    n = len(df)
    close = df['close'].values
    rsi = df['rsi14'].values
    macd = df['macd'].values
    macd_sig = df['macd_signal'].values
    macd_hist = df['macd_hist'].values
    ema20 = df['ema20'].values
    ema50 = df['ema50'].values
    ema200 = df['ema200'].values
    bb_upper = df['bb_upper'].values
    bb_lower = df['bb_lower'].values
    vol_ratio = df['vol_ratio'].values

    matrix = defaultdict(dict)
    lookback = 5

    for i in range(lookback, n - 1):
        # MACD背离
        if close[i] < close[i-lookback:i].min() and macd_hist[i] > macd_hist[i-lookback:i].min() and macd_hist[i] < 0:
            matrix[i]['MACD背离'] = 'long'
        elif close[i] > close[i-lookback:i].max() and macd_hist[i] < macd_hist[i-lookback:i].max() and macd_hist[i] > 0:
            matrix[i]['MACD背离'] = 'short'

        # RSI
        if rsi[i] < 30 and rsi[i-1] >= 30:
            matrix[i]['RSI超卖超买'] = 'long'
        elif rsi[i] > 70 and rsi[i-1] <= 70:
            matrix[i]['RSI超卖超买'] = 'short'

        # 布林带
        if close[i] <= bb_lower[i] and close[i-1] > bb_lower[i-1]:
            matrix[i]['布林带反弹'] = 'long'
        elif close[i] >= bb_upper[i] and close[i-1] < bb_upper[i-1]:
            matrix[i]['布林带反弹'] = 'short'

        # EMA趋势
        bull_now = close[i] > ema200[i] and ema20[i] > ema50[i]
        bull_prev = close[i-1] > ema200[i-1] and ema20[i-1] > ema50[i-1]
        bear_now = close[i] < ema200[i] and ema20[i] < ema50[i]
        bear_prev = close[i-1] < ema200[i-1] and ema20[i-1] < ema50[i-1]
        if bull_now and not bull_prev:
            matrix[i]['EMA趋势顺势'] = 'long'
        elif bear_now and not bear_prev:
            matrix[i]['EMA趋势顺势'] = 'short'

        # 量价
        if vol_ratio[i] > 1.5:
            if close[i] > ema50[i] and close[i] > close[i-1]:
                matrix[i]['量价配合'] = 'long'
            elif close[i] < ema50[i] and close[i] < close[i-1]:
                matrix[i]['量价配合'] = 'short'

        # MACD金叉
        if macd[i] > macd_sig[i] and macd[i-1] <= macd_sig[i-1]:
            matrix[i]['MACD金叉死叉'] = 'long'
        elif macd[i] < macd_sig[i] and macd[i-1] >= macd_sig[i-1]:
            matrix[i]['MACD金叉死叉'] = 'short'

        # MACD零轴
        if macd[i] > 0 and macd[i-1] <= 0:
            matrix[i]['MACD零轴位置'] = 'long'
        elif macd[i] < 0 and macd[i-1] >= 0:
            matrix[i]['MACD零轴位置'] = 'short'

    return matrix


def eval_combo_signals(df, signals, h=HOLDING_PERIOD):
    close = df['close'].values
    n = len(df)
    returns = []
    for idx, direction in signals:
        entry = close[idx]
        exit_idx = min(idx + h, n - 1)
        ret = (close[exit_idx] - entry) / entry
        if direction == 'short':
            ret = -ret
        returns.append(ret)
    if not returns:
        return {'wr': 0, 'pf': 0, 'n': 0, 'avg_win': 0, 'avg_loss': 0}
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r <= 0]
    wr = len(wins) / len(returns)
    pf = sum(wins) / (abs(sum(losses)) + 1e-10)
    return {
        'wr': round(wr, 4),
        'pf': round(pf, 4),
        'n': len(returns),
        'avg_win': round(float(np.mean(wins)) if wins else 0, 6),
        'avg_loss': round(float(np.mean(losses)) if losses else 0, 6),
    }


INDICATORS = ['MACD背离', 'RSI超卖超买', '布林带反弹', 'EMA趋势顺势', '量价配合', 'MACD金叉死叉', 'MACD零轴位置']
REGIMES = ['BULL_TREND', 'BEAR_TREND', 'BULL_PEAK', 'BEAR_CRASH', 'RECOVERY', 'CHOP']


def run_combo_test():
    files = sorted(DATA_DIR.glob('*.parquet'))
    print(f"共 {len(files)} 个文件，测试组合...")

    # 收集所有文件的组合信号
    combo2_agg = defaultdict(list)   # (ind1, ind2) -> list of (df, [(idx, dir)])
    combo3_agg = defaultdict(list)   # (ind1, ind2, ind3) -> ...
    regime_combo_agg = defaultdict(list)  # (regime, indicator) -> list of returns

    for i, fpath in enumerate(files):
        try:
            df = load_parquet(fpath)
            df = compute_indicators(df)
        except:
            continue
        if len(df) < 300:
            continue

        regimes = df.apply(classify_regime, axis=1)
        matrix = get_signal_matrix(df)

        # 2指标组合
        for ind1, ind2 in combinations(INDICATORS, 2):
            combo_signals = []
            for idx, sigs in matrix.items():
                if ind1 in sigs and ind2 in sigs:
                    dir1 = sigs[ind1]
                    dir2 = sigs[ind2]
                    if dir1 == dir2:
                        combo_signals.append((idx, dir1))
            if combo_signals:
                combo2_agg[(ind1, ind2)].append((df, combo_signals))

        # 3指标组合（关键：MACD背离+RSI+布林带）
        key_combos_3 = [
            ('MACD背离', 'RSI超卖超买', '布林带反弹'),
            ('MACD背离', 'RSI超卖超买', 'MACD金叉死叉'),
            ('EMA趋势顺势', 'MACD金叉死叉', '量价配合'),
            ('MACD背离', 'MACD金叉死叉', '布林带反弹'),
            ('RSI超卖超买', 'EMA趋势顺势', 'MACD零轴位置'),
        ]
        for ind1, ind2, ind3 in key_combos_3:
            combo_signals = []
            for idx, sigs in matrix.items():
                if ind1 in sigs and ind2 in sigs and ind3 in sigs:
                    dirs = [sigs[ind1], sigs[ind2], sigs[ind3]]
                    if len(set(dirs)) == 1:
                        combo_signals.append((idx, dirs[0]))
            if combo_signals:
                combo3_agg[(ind1, ind2, ind3)].append((df, combo_signals))

        # 体制+指标组合
        for regime in REGIMES:
            for ind in INDICATORS:
                regime_signals = []
                for idx, sigs in matrix.items():
                    if ind in sigs and regimes.iloc[idx] == regime:
                        regime_signals.append((idx, sigs[ind]))
                if regime_signals:
                    close = df['close'].values
                    n = len(df)
                    for idx, direction in regime_signals:
                        entry = close[idx]
                        exit_idx = min(idx + HOLDING_PERIOD, n - 1)
                        ret = (close[exit_idx] - entry) / entry
                        if direction == 'short':
                            ret = -ret
                        regime_combo_agg[(regime, ind)].append(ret)

        if (i + 1) % 10 == 0:
            print(f"  进度: {i+1}/{len(files)}")

    print("汇总组合统计...")
    results = {}

    # 2指标组合汇总
    combo2_stats = {}
    for combo_key, file_list in combo2_agg.items():
        all_returns = []
        for df, signals in file_list:
            close = df['close'].values
            n = len(df)
            for idx, direction in signals:
                entry = close[idx]
                exit_idx = min(idx + HOLDING_PERIOD, n - 1)
                ret = (close[exit_idx] - entry) / entry
                if direction == 'short':
                    ret = -ret
                all_returns.append(ret)
        if not all_returns:
            continue
        wins = [r for r in all_returns if r > 0]
        losses = [r for r in all_returns if r <= 0]
        wr = len(wins) / len(all_returns)
        pf = sum(wins) / (abs(sum(losses)) + 1e-10)
        combo2_stats['+'.join(combo_key)] = {
            'wr': round(wr, 4),
            'pf': round(pf, 4),
            'n': len(all_returns),
            'avg_win': round(float(np.mean(wins)) if wins else 0, 6),
            'avg_loss': round(float(np.mean(losses)) if losses else 0, 6),
        }
    results['2指标组合'] = combo2_stats

    # 3指标组合汇总
    combo3_stats = {}
    for combo_key, file_list in combo3_agg.items():
        all_returns = []
        for df, signals in file_list:
            close = df['close'].values
            n = len(df)
            for idx, direction in signals:
                entry = close[idx]
                exit_idx = min(idx + HOLDING_PERIOD, n - 1)
                ret = (close[exit_idx] - entry) / entry
                if direction == 'short':
                    ret = -ret
                all_returns.append(ret)
        if not all_returns:
            continue
        wins = [r for r in all_returns if r > 0]
        losses = [r for r in all_returns if r <= 0]
        wr = len(wins) / len(all_returns)
        pf = sum(wins) / (abs(sum(losses)) + 1e-10)
        combo3_stats['+'.join(combo_key)] = {
            'wr': round(wr, 4),
            'pf': round(pf, 4),
            'n': len(all_returns),
            'avg_win': round(float(np.mean(wins)) if wins else 0, 6),
            'avg_loss': round(float(np.mean(losses)) if losses else 0, 6),
        }
    results['3指标组合'] = combo3_stats

    # 体制+指标组合汇总
    regime_stats = {}
    for (regime, ind), rets in regime_combo_agg.items():
        if len(rets) < 20:
            continue
        wins = [r for r in rets if r > 0]
        losses = [r for r in rets if r <= 0]
        wr = len(wins) / len(rets)
        pf = sum(wins) / (abs(sum(losses)) + 1e-10)
        key = f"{regime}+{ind}"
        regime_stats[key] = {
            'wr': round(wr, 4),
            'pf': round(pf, 4),
            'n': len(rets),
        }
    results['体制+指标组合'] = regime_stats

    # 筛选高质量组合 WR>65% AND PF>1.5 AND n>100
    good_combos = {}
    for category, combos in results.items():
        good = {k: v for k, v in combos.items()
                if v.get('wr', 0) > 0.65 and v.get('pf', 0) > 1.5 and v.get('n', 0) > 100}
        if good:
            good_combos[category] = good
    results['高质量组合_WR65_PF1.5_N100'] = good_combos

    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    out_path = RESULTS_DIR / f'combo_test_v5_{ts}.json'
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 组合测试完成，结果保存至: {out_path}")
    return results, str(out_path)


if __name__ == '__main__':
    results, path = run_combo_test()
    # 显示Top5组合（按WR排序）
    all_combos = {}
    for cat in ['2指标组合', '3指标组合', '体制+指标组合']:
        for k, v in results.get(cat, {}).items():
            all_combos[f"[{cat}] {k}"] = v
    ranked = sorted(all_combos.items(), key=lambda x: x[1].get('wr', 0), reverse=True)
    print("\n=== Top5 组合 ===")
    for name, stats in ranked[:5]:
        print(f"  {name}: WR={stats['wr']:.1%}  PF={stats['pf']:.2f}  N={stats['n']}")
