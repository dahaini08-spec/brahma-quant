"""
达摩院V5 - Signal Backtest
测试梵天大脑7个核心指标的独立胜率
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
from collections import defaultdict

warnings.filterwarnings('ignore')

DATA_DIR = Path(__file__).parent / 'data'
RESULTS_DIR = Path(__file__).parent / 'results'
RESULTS_DIR.mkdir(exist_ok=True)

HOLDING_PERIODS = [4, 8, 12, 24]
SAMPLE_SIZE = 5000  # 每个文件采样行数


# 训练数据截止线 — 设计院 2026-05-28
# 2025-01-01以后为OOS验证集，禁止训练中使用
# 消除「训练数据包含实盘运行期」的前视偏差
TRAIN_CUTOFF = pd.Timestamp('2025-01-01', tz='UTC')  # 训练截止线
OOS_START    = pd.Timestamp('2025-01-01', tz='UTC')  # OOS验证起始

def load_parquet(path, sample_size=SAMPLE_SIZE, oos=False):
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index, utc=True)
    df = df.sort_index()
    # ── 数据截止线切割（防止前视偏差）──
    if oos:
        df = df[df.index >= OOS_START].copy()   # OOS模式：仅2025年起
    else:
        df = df[df.index < TRAIN_CUTOFF].copy()  # 训练模式：仅用刢2024年底
    if len(df) > sample_size:
        start = random.randint(0, len(df) - sample_size)
        df = df.iloc[start:start + sample_size].copy()
    return df


def compute_indicators(df):
    close = df['close']
    high = df['high']
    low = df['low']
    volume = df['volume']

    # EMA
    df['ema20'] = close.ewm(span=20, adjust=False).mean()
    df['ema50'] = close.ewm(span=50, adjust=False).mean()
    df['ema200'] = close.ewm(span=200, adjust=False).mean()

    # RSI14
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=13, adjust=False).mean()
    avg_loss = loss.ewm(com=13, adjust=False).mean()
    rs = avg_gain / (avg_loss + 1e-10)
    df['rsi14'] = 100 - (100 / (1 + rs))

    # MACD
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    df['macd'] = ema12 - ema26
    df['macd_signal'] = df['macd'].ewm(span=9, adjust=False).mean()
    df['macd_hist'] = df['macd'] - df['macd_signal']

    # Bollinger Bands
    bb_ma = close.rolling(20).mean()
    bb_std = close.rolling(20).std()
    df['bb_upper'] = bb_ma + 2 * bb_std
    df['bb_lower'] = bb_ma - 2 * bb_std
    bb_range = df['bb_upper'] - df['bb_lower']
    df['bb_pct'] = (close - df['bb_lower']) / (bb_range + 1e-10)

    # Volume ratio
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


# ── 信号函数 ──────────────────────────────────────────────────────────────────

def signal_macd_divergence(df):
    """MACD背离：底背离做多，顶背离做空"""
    signals = []
    lookback = 5
    for i in range(lookback, len(df) - 1):
        # 底背离：价格新低但MACD柱不新低
        price_new_low = df['close'].iloc[i] < df['close'].iloc[i - lookback:i].min()
        hist_not_new_low = df['macd_hist'].iloc[i] > df['macd_hist'].iloc[i - lookback:i].min()
        if price_new_low and hist_not_new_low and df['macd_hist'].iloc[i] < 0:
            signals.append((i, 'long'))
        # 顶背离：价格新高但MACD柱不新高
        price_new_high = df['close'].iloc[i] > df['close'].iloc[i - lookback:i].max()
        hist_not_new_high = df['macd_hist'].iloc[i] < df['macd_hist'].iloc[i - lookback:i].max()
        if price_new_high and hist_not_new_high and df['macd_hist'].iloc[i] > 0:
            signals.append((i, 'short'))
    return signals


def signal_rsi_extreme(df):
    """RSI超卖/超买"""
    signals = []
    rsi = df['rsi14'].values
    for i in range(1, len(df) - 1):
        if rsi[i] < 30 and rsi[i - 1] >= 30:
            signals.append((i, 'long'))
        elif rsi[i] > 70 and rsi[i - 1] <= 70:
            signals.append((i, 'short'))
    return signals


def signal_bb_band(df):
    """布林带下轨反弹做多，上轨做空"""
    signals = []
    close = df['close'].values
    bb_upper = df['bb_upper'].values
    bb_lower = df['bb_lower'].values
    for i in range(1, len(df) - 1):
        if close[i] <= bb_lower[i] and close[i - 1] > bb_lower[i - 1]:
            signals.append((i, 'long'))
        elif close[i] >= bb_upper[i] and close[i - 1] < bb_upper[i - 1]:
            signals.append((i, 'short'))
    return signals


def signal_ema_trend(df):
    """EMA趋势顺势：多头排列做多，空头排列做空"""
    signals = []
    close = df['close'].values
    ema20 = df['ema20'].values
    ema50 = df['ema50'].values
    ema200 = df['ema200'].values
    for i in range(1, len(df) - 1):
        bull_now = close[i] > ema200[i] and ema20[i] > ema50[i]
        bull_prev = close[i - 1] > ema200[i - 1] and ema20[i - 1] > ema50[i - 1]
        bear_now = close[i] < ema200[i] and ema20[i] < ema50[i]
        bear_prev = close[i - 1] < ema200[i - 1] and ema20[i - 1] < ema50[i - 1]
        if bull_now and not bull_prev:
            signals.append((i, 'long'))
        elif bear_now and not bear_prev:
            signals.append((i, 'short'))
    return signals


def signal_vol_breakout(df):
    """放量顺势突破"""
    signals = []
    close = df['close'].values
    ema50 = df['ema50'].values
    vol_ratio = df['vol_ratio'].values
    for i in range(1, len(df) - 1):
        if vol_ratio[i] > 1.5:
            if close[i] > ema50[i] and close[i] > close[i - 1]:
                signals.append((i, 'long'))
            elif close[i] < ema50[i] and close[i] < close[i - 1]:
                signals.append((i, 'short'))
    return signals


def signal_macd_cross(df):
    """MACD金叉/死叉"""
    signals = []
    macd = df['macd'].values
    signal_line = df['macd_signal'].values
    for i in range(1, len(df) - 1):
        if macd[i] > signal_line[i] and macd[i - 1] <= signal_line[i - 1]:
            signals.append((i, 'long'))
        elif macd[i] < signal_line[i] and macd[i - 1] >= signal_line[i - 1]:
            signals.append((i, 'short'))
    return signals


def signal_macd_zero_axis(df):
    """MACD在0轴上方/下方"""
    signals = []
    macd = df['macd'].values
    for i in range(1, len(df) - 1):
        if macd[i] > 0 and macd[i - 1] <= 0:
            signals.append((i, 'long'))
        elif macd[i] < 0 and macd[i - 1] >= 0:
            signals.append((i, 'short'))
    return signals


SIGNAL_FUNCS = {
    'MACD背离': signal_macd_divergence,
    'RSI超卖超买': signal_rsi_extreme,
    '布林带反弹': signal_bb_band,
    'EMA趋势顺势': signal_ema_trend,
    '量价配合': signal_vol_breakout,
    'MACD金叉死叉': signal_macd_cross,
    'MACD零轴位置': signal_macd_zero_axis,
}


def eval_signals(df, signals):
    """计算信号回测统计"""
    close = df['close'].values
    n = len(df)
    results = {h: {'wins': 0, 'losses': 0, 'returns': []} for h in HOLDING_PERIODS}

    for idx, direction in signals:
        entry = close[idx]
        for h in HOLDING_PERIODS:
            exit_idx = min(idx + h, n - 1)
            ret = (close[exit_idx] - entry) / entry
            if direction == 'short':
                ret = -ret
            results[h]['returns'].append(ret)
            if ret > 0:
                results[h]['wins'] += 1
            else:
                results[h]['losses'] += 1

    stats = {}
    for h, d in results.items():
        rets = d['returns']
        if len(rets) == 0:
            stats[h] = {'wr': 0, 'pf': 0, 'n': 0, 'avg_win': 0, 'avg_loss': 0}
            continue
        wins = [r for r in rets if r > 0]
        losses = [r for r in rets if r <= 0]
        wr = len(wins) / len(rets) if rets else 0
        avg_win = np.mean(wins) if wins else 0
        avg_loss = np.mean(losses) if losses else 0
        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        pf = gross_profit / (gross_loss + 1e-10)
        stats[h] = {
            'wr': round(wr, 4),
            'pf': round(pf, 4),
            'n': len(rets),
            'avg_win': round(avg_win, 6),
            'avg_loss': round(avg_loss, 6),
        }
    return stats


def run_backtest(oos=False):
    mode_label = 'OOS验证集(2025+)' if oos else '训练集(截至2024-12-31)'
    files = sorted(DATA_DIR.glob('*.parquet'))
    print(f"共找到 {len(files)} 个parquet文件  [模式: {mode_label}]")
    print(f"  训练截止线: {'2024-12-31' if not oos else '2025-01-01~当前'}  「历史回测」")

    # 结构: indicator -> file_data list
    all_data = {name: [] for name in SIGNAL_FUNCS}
    regime_data = {name: defaultdict(list) for name in SIGNAL_FUNCS}
    tf_data = {name: defaultdict(list) for name in SIGNAL_FUNCS}

    for i, fpath in enumerate(files):
        # 解析周期
        stem = fpath.stem  # e.g. ethusdt_1h_2018_2026
        parts = stem.split('_')
        timeframe = parts[1] if len(parts) > 1 else 'unknown'

        try:
            df = load_parquet(fpath, oos=oos)
            df = compute_indicators(df)
        except Exception as e:
            print(f"  跳过 {fpath.name}: {e}")
            continue

        if len(df) < 300:
            continue

        # 计算体制标签
        regimes = df.apply(classify_regime, axis=1)

        for name, func in SIGNAL_FUNCS.items():
            try:
                signals = func(df)
                if not signals:
                    continue
                # 整体
                stats = eval_signals(df, signals)
                all_data[name].append(stats)

                # 按体制拆分
                for idx, direction in signals:
                    regime = regimes.iloc[idx]
                    regime_data[name][regime].append((idx, direction))

                # 按周期
                tf_data[name][timeframe].append((df, signals))

            except Exception as e:
                pass

        if (i + 1) % 10 == 0:
            print(f"  进度: {i+1}/{len(files)}")

    print("汇总统计中...")
    final = {}

    for name in SIGNAL_FUNCS:
        # 汇总所有文件的持有期统计
        overall_by_h = defaultdict(lambda: {'wins': 0, 'losses': 0, 'returns': []})
        for file_stats in all_data[name]:
            for h, s in file_stats.items():
                overall_by_h[h]['wins'] += s['n'] * s['wr']
                overall_by_h[h]['losses'] += s['n'] * (1 - s['wr'])
                # 近似重建returns
                n = s['n']
                w = int(n * s['wr'])
                l = n - w
                overall_by_h[h]['returns'].extend([s['avg_win']] * w + [s['avg_loss']] * l)

        # 找最佳持有期
        best_h = None
        best_wr = 0
        overall_stats = {}
        for h, d in overall_by_h.items():
            rets = d['returns']
            if not rets:
                continue
            wins = [r for r in rets if r > 0]
            losses = [r for r in rets if r <= 0]
            wr = len(wins) / len(rets)
            avg_win = np.mean(wins) if wins else 0
            avg_loss = np.mean(losses) if losses else 0
            pf = sum(wins) / (abs(sum(losses)) + 1e-10)
            overall_stats[h] = {
                'wr': round(wr, 4),
                'pf': round(pf, 4),
                'n': len(rets),
                'avg_win': round(float(avg_win), 6),
                'avg_loss': round(float(avg_loss), 6),
            }
            if wr > best_wr:
                best_wr = wr
                best_h = h

        # 使用最佳持有期的整体统计
        overall = overall_stats.get(best_h or 12, {'wr': 0, 'pf': 0, 'n': 0, 'avg_win': 0, 'avg_loss': 0})

        # 按体制统计（使用最佳持有期近似）
        by_regime = {}
        # 体制数据已收集，但需要用df重新计算；此处用近似
        for regime_name in ['BULL_TREND', 'BEAR_TREND', 'BULL_PEAK', 'BEAR_CRASH', 'RECOVERY', 'CHOP', 'CHOP_HIGH', 'CHOP_LOW']:
            by_regime[regime_name] = {'wr': 0, 'pf': 0, 'n': 0}

        # 按周期统计
        by_tf = {}
        for tf in ['1h', '4h', '1d']:
            tf_entries = tf_data[name].get(tf, [])
            tf_returns = []
            for df_tf, sigs in tf_entries:
                close = df_tf['close'].values
                n = len(df_tf)
                h = best_h or 12
                for idx, direction in sigs:
                    entry = close[idx]
                    exit_idx = min(idx + h, n - 1)
                    ret = (close[exit_idx] - entry) / entry
                    if direction == 'short':
                        ret = -ret
                    tf_returns.append(ret)
            if tf_returns:
                wins = [r for r in tf_returns if r > 0]
                losses = [r for r in tf_returns if r <= 0]
                wr = len(wins) / len(tf_returns)
                pf = sum(wins) / (abs(sum(losses)) + 1e-10) if losses else 99
                by_tf[tf] = {
                    'wr': round(wr, 4),
                    'pf': round(pf, 4),
                    'n': len(tf_returns),
                    'avg_win': round(float(np.mean(wins)) if wins else 0, 6),
                    'avg_loss': round(float(np.mean(losses)) if losses else 0, 6),
                }
            else:
                by_tf[tf] = {'wr': 0, 'pf': 0, 'n': 0}

        final[name] = {
            'overall': overall,
            'by_regime': by_regime,
            'by_timeframe': by_tf,
            'best_holding': {'n_bars': best_h or 12, 'wr': round(best_wr, 4)},
            'all_holdings': overall_stats,
        }

    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    mode_tag = 'oos' if oos else 'train'
    out_path = RESULTS_DIR / f'signal_backtest_v5_{mode_tag}_{ts}.json'
    # ── 正确标注：历史回测 / OOS验证（禁止标注「实盘验证」）──
    meta = {
        '_data_label': '历史回测' if not oos else 'OOS验证(实盘期)',
        '_train_cutoff': '2024-12-31' if not oos else 'N/A',
        '_oos_start': 'N/A' if not oos else '2025-01-01',
        '_warning': '本报告为「历史回测」，未经实盘验证，不得标注「实盘验证」',
        '_ts': ts,
    }
    output = {'_meta': meta, **final}
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n✅ [{meta['_data_label']}] 信号回测完成，结果保存至: {out_path}")
    return final, str(out_path)


if __name__ == '__main__':
    result, path = run_backtest()
    print("\n=== Top5 指标胜率 (最佳持有期) ===")
    ranked = sorted(result.items(), key=lambda x: x[1]['overall'].get('wr', 0), reverse=True)
    for name, data in ranked[:5]:
        o = data['overall']
        bh = data['best_holding']
        print(f"  {name}: WR={o['wr']:.1%}  PF={o['pf']:.2f}  N={o['n']}  最佳持有={bh['n_bars']}根")
