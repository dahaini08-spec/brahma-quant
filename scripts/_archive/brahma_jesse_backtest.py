#!/usr/bin/env python3
"""
brahma_jesse_backtest.py — 梵天12维策略 Jesse指标回测
不依赖PostgreSQL/Redis，直接用Jesse的indicators库做纯Python回测

12维 = regime_direction + rsi_extreme + bb_squeeze
+ 辅助维度: atr(止损), volume确认, EMA趋势强度

Phase 2 验证目标:
  1. Jesse回测WR > 52%
  2. EV > 0.1%
  3. 成本后仍盈利
  4. Optuna参数优化
  5. Monte Carlo验证
"""

import gzip, json, math, os, sys, time, random
import numpy as np
from collections import defaultdict
from pathlib import Path

# Jesse指标
from jesse.indicators import rsi as jesse_rsi
from jesse.indicators import ema as jesse_ema
from jesse.indicators import bollinger_bands_width as jesse_bbw
from jesse.indicators import atr as jesse_atr

DATA_DIR = Path(__file__).parent.parent / "data" / "historical"
OUT_DIR = Path(__file__).parent.parent / "data"

# 回测参数（可Optuna优化）
PARAMS = {
    'rsi_long_threshold': 35,    # RSI < 35 → 做多信号
    'rsi_short_threshold': 65,    # RSI > 65 → 做空信号
    'bb_squeeze_threshold': 0.03, # BB width < 0.03 → squeeze
    'ema_period': 200,            # EMA周期
    'rsi_period': 14,             # RSI周期
    'bb_period': 20,              # BB周期
    'atr_period': 14,             # ATR周期
    'atr_sl_mult': 1.5,           # ATR止损倍数
    'rr_ratio': 1.5,              # 风险回报比
    'position_pct': 0.10,         # 仓位占比 (10% NAV)
    'cost': 0.0008,               # 手续费 0.08%
    'slippage': 0.0005,            # 滑点 5bp
    'holding_bars': 24,            # 持仓K线数(24h)
}

def load_klines(symbol, tf="1h"):
    fname = DATA_DIR / f"{symbol}_{tf}.jsonl.gz"
    if not fname.exists(): return None
    with gzip.open(fname, 'rt') as f:
        bars = sorted([json.loads(l) for l in f if l.strip()], key=lambda x: x['ts'])
    # 转换为numpy candles格式: [timestamp, open, high, low, close, volume]
    candles = np.array([
        [b['ts'], b['o'], b['h'], b['l'], b['c'], b.get('v', 0)]
        for b in bars
    ], dtype=np.float64)
    return candles, bars

def load_regimes(symbol):
    fname = DATA_DIR / f"{symbol}_regime_labels.jsonl.gz"
    if not fname.exists(): return {}
    with gzip.open(fname, 'rt') as f:
        result = {}
        for l in f:
            l = l.strip()
            if not l: continue
            obj = json.loads(l)
            if isinstance(obj, dict) and 'ts' in obj and 'regime' in obj:
                result[obj['ts']] = obj['regime']
        return result

def run_backtest(symbol, params=None, use_regime_labels=True, verbose=False):
    """运行Jesse指标回测"""
    if params:
        p = {**PARAMS, **params}
    else:
        p = PARAMS
    
    data = load_klines(symbol)
    if data is None: return None
    candles, bars = data
    n = len(candles)
    
    regimes = load_regimes(symbol) if use_regime_labels else {}
    
    # Jesse指标计算（sequential模式）
    rsi_seq = jesse_rsi(candles, p['rsi_period'], sequential=True)
    ema_seq = jesse_ema(candles, p['ema_period'], sequential=True)
    bbw_seq = jesse_bbw(candles, p['bb_period'], sequential=True)
    atr_seq = jesse_atr(candles, p['atr_period'], sequential=True)
    
    # 回测
    trades = []
    warmup = max(p['ema_period'], 200)
    
    for i in range(warmup, n - p['holding_bars']):
        if rsi_seq[i] is None or math.isnan(rsi_seq[i]): continue
        if math.isnan(ema_seq[i]) or math.isnan(bbw_seq[i]) or math.isnan(atr_seq[i]): continue
        
        close_price = candles[i][4]
        ts = int(candles[i][0])
        
        # 体制方向
        if use_regime_labels:
            regime = regimes.get(ts, None)
            if regime is None: continue
            regime_dir = 1 if 'BULL' in regime else (-1 if 'BEAR' in regime else 0)
        else:
            regime_dir = 1 if close_price > ema_seq[i] else -1
        
        # 12维信号
        rsi_val = rsi_seq[i]
        rsi_signal = 1 if rsi_val < p['rsi_long_threshold'] else (-1 if rsi_val > p['rsi_short_threshold'] else 0)
        bb_squeeze = 1 if bbw_seq[i] < p['bb_squeeze_threshold'] else 0
        
        # 信号逻辑
        if regime_dir != 0 and rsi_signal != 0 and regime_dir == rsi_signal:
            signal = rsi_signal
        elif regime_dir != 0 and rsi_signal == 0 and bb_squeeze:
            signal = regime_dir
        else:
            signal = 0
        
        if signal == 0: continue
        
        # ATR止损
        atr_val = atr_seq[i]
        sl_distance = atr_val * p['atr_sl_mult']
        
        # 入场 = 当前收盘价
        entry = close_price
        if signal == 1:
            sl = entry - sl_distance
            tp = entry + sl_distance * p['rr_ratio']
        else:
            sl = entry + sl_distance
            tp = entry - sl_distance * p['rr_ratio']
        
        # 持仓到 holding_bars 后收盘
        exit_idx = min(i + p['holding_bars'], n - 1)
        exit_price = candles[exit_idx][4]
        
        # 检查是否触发止损/止盈（简化：检查最高/最低价）
        for j in range(i + 1, exit_idx + 1):
            if signal == 1 and candles[j][2] <= sl:  # 低点触及止损
                exit_price = sl
                exit_idx = j
                break
            elif signal == 1 and candles[j][3] >= tp:  # 高点触及止盈
                exit_price = tp
                exit_idx = j
                break
            elif signal == -1 and candles[j][3] >= sl:  # 高点触及止损
                exit_price = sl
                exit_idx = j
                break
            elif signal == -1 and candles[j][2] <= tp:  # 低点触及止盈
                exit_price = tp
                exit_idx = j
                break
        
        # 计算收益
        gross_ret = (exit_price - entry) / entry * signal
        cost = p['cost'] + p['slippage']
        net_ret = gross_ret - cost
        
        trades.append({
            'symbol': symbol,
            'signal': signal,
            'entry': entry,
            'exit': exit_price,
            'sl': sl,
            'tp': tp,
            'gross_ret': gross_ret,
            'net_ret': net_ret,
            'rsi': rsi_val,
            'atr': atr_val,
            'regime': regimes.get(ts, 'CHOP_MID') if use_regime_labels else 'PROXY',
            'ts': ts,
        })
    
    if not trades:
        return {'symbol': symbol, 'n': 0, 'wr': 0, 'ev': 0, 'trades': []}
    
    # 统计
    n_trades = len(trades)
    wins = sum(1 for t in trades if t['net_ret'] > 0)
    wr = wins / n_trades * 100
    ev = sum(t['net_ret'] for t in trades) / n_trades * 100
    total_pnl = sum(t['net_ret'] for t in trades) * p['position_pct'] * 10000  # $10000 starting NAV
    
    # 按体制分组
    by_regime = defaultdict(list)
    for t in trades:
        by_regime[t['regime']].append(t)
    
    result = {
        'symbol': symbol,
        'n': n_trades,
        'wr': wr,
        'ev': ev,
        'total_pnl': total_pnl,
        'trades': trades,
        'by_regime': {r: {'n': len(ts), 'wr': sum(1 for t in ts if t['net_ret'] > 0)/len(ts)*100, 'ev': sum(t['net_ret'] for t in ts)/len(ts)*100} for r, ts in by_regime.items()},
    }
    
    if verbose:
        print(f"\n{symbol}: n={n_trades} WR={wr:.1f}% EV={ev:+.3f}% P&L=${total_pnl:,.0f}")
        for r, stats in sorted(result['by_regime'].items()):
            print(f"  {r:15s}: n={stats['n']:4d} WR={stats['wr']:.1f}% EV={stats['ev']:+.3f}%")
    
    return result

def monte_carlo(trades, n_sims=5000, block_size=6, position_pct=0.10, start_nav=10000.0):
    """Monte Carlo block bootstrap"""
    random.seed(42)
    n = len(trades)
    if n == 0: return {}
    
    navs = []
    sharpes = []
    max_dds = []
    
    for _ in range(n_sims):
        nav = start_nav
        peak = nav
        max_dd = 0
        returns = []
        remaining = n
        
        while remaining > 0:
            block_start = random.randint(0, max(0, n - block_size))
            block_end = min(block_start + block_size, n, block_start + remaining)
            for j in range(block_start, block_end):
                r = trades[j]['net_ret'] * position_pct
                nav *= (1 + r)
                returns.append(r)
                if nav > peak: peak = nav
                dd = (peak - nav) / peak
                if dd > max_dd: max_dd = dd
            remaining -= (block_end - block_start)
        
        navs.append(nav)
        max_dds.append(max_dd)
        
        if len(returns) > 1:
            mean_r = sum(returns) / len(returns)
            var_r = sum((r - mean_r) ** 2 for r in returns) / len(returns)
            sd_r = math.sqrt(var_r) if var_r > 0 else 0.001
            sharpes.append(mean_r / sd_r * math.sqrt(252) if sd_r > 0 else 0)
    
    navs.sort()
    sharpes.sort()
    max_dds.sort()
    
    return {
        'p05_nav': navs[int(0.05 * len(navs))],
        'p50_nav': navs[int(0.5 * len(navs))],
        'p95_nav': navs[int(0.95 * len(navs))],
        'ruin_rate': sum(1 for x in navs if x < 5000) / len(navs),
        'median_sharpe': sharpes[int(0.5 * len(sharpes))] if sharpes else 0,
        'p5_sharpe': sharpes[int(0.05 * len(sharpes))] if sharpes else 0,
        'median_max_dd': max_dds[int(0.5 * len(max_dds))],
        'p95_max_dd': max_dds[int(0.95 * len(max_dds))],
    }

def optuna_optimize(symbol, n_trials=50):
    """Optuna参数优化"""
    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    
    def objective(trial):
        params = {
            'rsi_long_threshold': trial.suggest_int('rsi_long', 25, 40),
            'rsi_short_threshold': trial.suggest_int('rsi_short', 60, 75),
            'bb_squeeze_threshold': trial.suggest_float('bb_squeeze', 0.02, 0.05),
            'atr_sl_mult': trial.suggest_float('atr_sl', 1.0, 2.5),
            'rr_ratio': trial.suggest_float('rr', 1.0, 3.0),
            'holding_bars': trial.suggest_int('holding', 12, 48),
        }
        result = run_backtest(symbol, params, use_regime_labels=True, verbose=False)
        if result['n'] < 100: return -100  # 信号太少
        return result['ev']  # 最大化EV
    
    study = optuna.create_study(direction='maximize', sampler=optuna.samplers.TPESampler(seed=42))
    study.optimize(objective, n_trials=n_trials, show_progress_bar=False)
    
    return study.best_params, study.best_value

if __name__ == '__main__':
    print("=" * 70)
    print("梵天12维 Jesse指标回测 — Phase 2")
    print("=" * 70)
    
    # Step 1: 基础回测
    print("\n[Step 1] 基础回测 (默认参数, 成本后)")
    results = {}
    for symbol in ['BTCUSDT', 'ETHUSDT']:
        results[symbol] = run_backtest(symbol, verbose=True)
    
    # Step 2: Monte Carlo
    print("\n[Step 2] Monte Carlo (5000次, 10%仓位)")
    for symbol, result in results.items():
        mc = monte_carlo(result['trades'])
        print(f"\n  {symbol}:")
        print(f"    P05=${mc['p05_nav']:,.0f} P50=${mc['p50_nav']:,.0f} P95=${mc['p95_nav']:,.0f}")
        print(f"    破产率={mc['ruin_rate']*100:.1f}% 中位Sharpe={mc['median_sharpe']:+.2f}")
        print(f"    中位MaxDD={mc['median_max_dd']*100:.1f}% P95 MaxDD={mc['p95_max_dd']*100:.1f}%")
    
    # Step 3: Optuna优化
    print("\n[Step 3] Optuna参数优化 (50 trials)")
    for symbol in ['BTCUSDT', 'ETHUSDT']:
        print(f"\n  {symbol} 优化中...")
        best_params, best_ev = optuna_optimize(symbol, n_trials=50)
        print(f"    最优EV={best_ev:+.3f}%")
        print(f"    最优参数: {best_params}")
        
        # 用最优参数重跑
        param_map = {
            'rsi_long': 'rsi_long_threshold',
            'rsi_short': 'rsi_short_threshold',
            'bb_squeeze': 'bb_squeeze_threshold',
            'atr_sl': 'atr_sl_mult',
            'rr': 'rr_ratio',
            'holding': 'holding_bars',
        }
        full_params = {param_map[k]: v for k, v in best_params.items()}
        result_opt = run_backtest(symbol, full_params, verbose=True)
        
        # 最优参数MC
        mc_opt = monte_carlo(result_opt['trades'])
        print(f"    MC: P05=${mc_opt['p05_nav']:,.0f} P50=${mc_opt['p50_nav']:,.0f} P95=${mc_opt['p95_nav']:,.0f}")
        print(f"    破产率={mc_opt['ruin_rate']*100:.1f}% 中位Sharpe={mc_opt['median_sharpe']:+.2f}")
    
    print("\n" + "=" * 70)
    print("Phase 2 验证完成")
    print("=" * 70)
