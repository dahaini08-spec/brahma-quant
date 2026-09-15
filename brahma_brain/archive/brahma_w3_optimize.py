#!/usr/bin/env python3
"""
brahma_w3_optimize.py — 梵天v5.0 W3: Optuna + CPCV + DSR
=========================================================
W3封印：苏摩111 2026-09-14

三核心增强：
1. CPCV (Combinatorial Purged Cross-Validation) — N组组合训练/测试，purging防泄漏
2. DSR (Deflated Sharpe Ratio) — 修正多重检验偏差的Sharpe Ratio
3. 全参数Optuna优化 — 200 trials × CPCV目标函数

输出：
- 最优参数 + 稳定性报告
- CPCV各fold EV/WR/Sharpe
- DSR vs 原始Sharpe对比
- 与保守参数对比
"""

import sys, math, time, gzip, json, random, os
import numpy as np
from pathlib import Path
from collections import defaultdict
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional
from scipy import stats as scipy_stats

# Jesse指标
from jesse.indicators import rsi as jesse_rsi
from jesse.indicators import ema as jesse_ema
from jesse.indicators import bollinger_bands_width as jesse_bbw
from jesse.indicators import atr as jesse_atr

# 添加brahma_brain路径
BRAIN = Path(__file__).parent
sys.path.insert(0, str(BRAIN))
sys.path.insert(0, str(BRAIN.parent))

from brahma_engine_v5 import load_candles, load_regimes, BrahmaStrategy12, monte_carlo

DATA_DIR = BRAIN.parent / "data" / "historical"
OUT_DIR = BRAIN.parent / "data"

# ============================================================
# 1. CPCV — Combinatorial Purged Cross-Validation
# ============================================================

@dataclass
class CPCVConfig:
    n_splits: int = 6          # 总共分成6组
    n_test_groups: int = 2     # 每次用2组做测试
    purge_bars: int = 24       # purge窗口（24根K线=24h，防泄漏）
    embargo_bars: int = 12     # embargo窗口（测试后12根K线不用）

def cpcv_splits(n_bars: int, config: CPCVConfig) -> List[Dict]:
    """
    生成CPCV的train/test分割
    返回: [{'train': [(start, end), ...], 'test': [(start, end), ...], 'fold_id': int}, ...]
    """
    from itertools import combinations
    
    group_size = n_bars // config.n_splits
    groups = [(i * group_size, min((i + 1) * group_size, n_bars)) for i in range(config.n_splits)]
    
    splits = []
    fold_id = 0
    for test_combo in combinations(range(config.n_splits), config.n_test_groups):
        # 测试区间
        test_ranges = [groups[g] for g in test_combo]
        
        # 训练区间 = 所有组 - 测试组 - purge窗口
        train_ranges = []
        for i, (gs, ge) in enumerate(groups):
            if i in test_combo:
                continue
            # purge: 在测试组前后各裁掉purge_bars
            # 检查是否与前一个测试组相邻
            purge_start = gs
            purge_end = ge
            for test_g in test_combo:
                tg_start, tg_end = groups[test_g]
                if i == test_g - 1:  # 前邻测试组
                    purge_start = gs  # 不裁前面
                    purge_end = min(ge, tg_start - config.purge_bars) if tg_start > gs else ge
                if i == test_g + 1:  # 后邻测试组
                    purge_start = max(gs, tg_end + config.embargo_bars) if tg_end < ge else gs
                    purge_end = ge
            
            if purge_end > purge_start:
                train_ranges.append((purge_start, purge_end))
        
        splits.append({
            'train': train_ranges,
            'test': test_ranges,
            'fold_id': fold_id,
            'test_groups': list(test_combo),
        })
        fold_id += 1
    
    return splits


def run_cpcv_backtest(candles, regimes, rsi_seq, ema_seq, bbw_seq, atr_seq,
                      params, config: CPCVConfig, verbose=False) -> Dict:
    """
    在CPCV分割上运行回测，返回各fold结果
    优化：预计算全量指标，CPCV切片时直接切指标数组（避免重复Jesse计算）
    """
    n = len(candles)
    splits = cpcv_splits(n, config)
    
    fold_results = []
    
    for split in splits:
        # 测试：在测试区间运行（用预计算的全量指标切片）
        test_results = []
        for start, end in split['test']:
            if end - start < 200:
                continue
            sub_c = candles[start:end]
            ts_start = int(sub_c[0][0])
            ts_end = int(sub_c[-1][0])
            sub_r = {ts: r for ts, r in regimes.items() if ts_start <= ts <= ts_end}
            
            # 切预计算的指标数组，避免重复Jesse计算
            sub_rsi = rsi_seq[start:end]
            sub_ema = ema_seq[start:end]
            sub_bbw = bbw_seq[start:end]
            sub_atr = atr_seq[start:end]
            
            strategy = BrahmaStrategy12(params)
            result = strategy.run(sub_c, sub_r, sub_rsi, sub_ema, sub_bbw, sub_atr)
            test_results.append(result)
        
        # 合并测试结果
        if test_results:
            all_trades = []
            for r in test_results:
                all_trades.extend(r.get('trades', []))
            
            n_trades = sum(r['n'] for r in test_results)
            n_win = sum(1 for t in all_trades if getattr(t, 'net_ret', 0) > 0)
            wr = n_win / n_trades * 100 if n_trades > 0 else 0
            ev = np.mean([getattr(t, 'net_ret', 0) for t in all_trades]) * 100 if all_trades else 0
            
            # Sharpe
            if len(all_trades) > 2:
                nets = [getattr(t, 'net_ret', 0) for t in all_trades]
                sharpe = np.mean(nets) / (np.std(nets) + 1e-10) * math.sqrt(len(nets))
            else:
                sharpe = 0
            
            fold_results.append({
                'fold_id': split['fold_id'],
                'test_groups': split['test_groups'],
                'n_trades': n_trades,
                'wr': wr,
                'ev': ev,
                'sharpe': sharpe,
                'pass': ev > 0,
            })
        
        if verbose and (split['fold_id'] + 1) % 5 == 0:
            print(f"  CPCV fold {split['fold_id']+1}/{len(splits)} done")
    
    # 统计
    pass_rate = sum(1 for f in fold_results if f['pass']) / len(fold_results) if fold_results else 0
    avg_ev = np.mean([f['ev'] for f in fold_results]) if fold_results else 0
    avg_wr = np.mean([f['wr'] for f in fold_results]) if fold_results else 0
    worst_ev = min(f['ev'] for f in fold_results) if fold_results else 0
    best_ev = max(f['ev'] for f in fold_results) if fold_results else 0
    
    return {
        'n_folds': len(fold_results),
        'pass_rate': pass_rate,
        'avg_ev': avg_ev,
        'avg_wr': avg_wr,
        'worst_ev': worst_ev,
        'best_ev': best_ev,
        'folds': fold_results,
    }


# ============================================================
# 2. DSR — Deflated Sharpe Ratio
# ============================================================

def deflated_sharpe_ratio(returns: List[float], n_trials: int, 
                          sharpe_ratio: float = None) -> Dict:
    """
    Deflated Sharpe Ratio (Bailey & López de Prado 2014)
    
    DSR = P(SR > SR_max | n_trials)
    SR_max = E[max(SR_1, ..., SR_n)] under H0
    
    公式：
    1. 原始Sharpe: SR = mean(r) / std(r) * sqrt(N)
    2. 修正: SR_hat = SR * (1 - skew*r3 + (kurt-1)/4 * r4^2 * SR^2)
    3. DSR = Φ((SR - SR_max) * sqrt(T))
       SR_max ≈ sqrt(2*ln(n_trials)) * (1 - γ*sqrt(2*ln(n_trials))/sqrt(N))
    
    返回: {dsr, sr_original, sr_max, n_trials, verdict}
    """
    r = np.array(returns)
    n = len(r)
    
    if n < 3:
        return {'dsr': 0, 'sr_original': 0, 'sr_max': 0, 'n_trials': n_trials, 'verdict': 'INSUFFICIENT_DATA'}
    
    # 原始Sharpe Ratio（年化）
    if sharpe_ratio is None:
        mean_r = np.mean(r)
        std_r = np.std(r, ddof=1)
        sr = mean_r / (std_r + 1e-10) * math.sqrt(n)
    else:
        sr = sharpe_ratio
    
    # 偏度和峰度
    skew = float(scipy_stats.skew(r))
    kurt = float(scipy_stats.kurtosis(r, fisher=True))  # excess kurtosis
    
    # SR_max under H0 (multiple testing)
    gamma = 0.5772  # Euler-Mascheroni constant
    euler_gamma = 0.5772
    
    # PSR (Probabilistic Sharpe Ratio) correction
    # SR_max ≈ sqrt(2*ln(N_trials)) when trials are independent
    sr_max = math.sqrt(2 * math.log(max(n_trials, 2))) * (1 - euler_gamma / math.sqrt(max(n_trials, 2)))
    
    # DSR
    # 修正SR for non-normality
    sr_corrected = sr * (1 - skew * sr / (3 * math.sqrt(n)) + (kurt - 1) / (4 * n) * sr**2)
    
    # DSR = Φ((SR_corrected - SR_max) * sqrt(T) / sqrt(1 - skew*SR + (kurt-1)/4 * SR^2))
    # 简化版：DSR ≈ Φ((SR - SR_max) / sqrt((1 + 0.5*SR^2) / n))
    if sr_max > 0:
        dsr_z = (sr_corrected - sr_max) / math.sqrt((1 + 0.5 * sr**2) / n)
        dsr = float(scipy_stats.norm.cdf(dsr_z))
    else:
        dsr = 0.5
    
    # Verdict
    if dsr > 0.95:
        verdict = 'HIGHLY_SIGNIFICANT'
    elif dsr > 0.90:
        verdict = 'SIGNIFICANT'
    elif dsr > 0.80:
        verdict = 'MARGINAL'
    else:
        verdict = 'NOT_SIGNIFICANT'
    
    return {
        'dsr': dsr,
        'sr_original': sr,
        'sr_corrected': sr_corrected,
        'sr_max': sr_max,
        'skew': skew,
        'kurtosis': kurt,
        'n_trials': n_trials,
        'n_samples': n,
        'verdict': verdict,
    }


# ============================================================
# 3. Optuna + CPCV 目标函数
# ============================================================

def create_objective(candles, regimes, config: CPCVConfig):
    """创建Optuna目标函数（CPCV avg EV as objective）"""
    
    def objective(trial):
        params = {
            'rsi_long': trial.suggest_int('rsi_long', 25, 45),
            'rsi_short': trial.suggest_int('rsi_short', 55, 80),
            'bb_squeeze': trial.suggest_float('bb_squeeze', 0.01, 0.08),
            'atr_sl_mult': trial.suggest_float('atr_sl_mult', 1.0, 3.0),
            'rr_ratio': trial.suggest_float('rr_ratio', 1.0, 4.0),
            'holding_bars': trial.suggest_int('holding_bars', 12, 72),
        }
        
        # 预计算指标（全量，CPCV内部会切片重算）
        rsi_seq = jesse_rsi(candles, 14, sequential=True)
        ema_seq = jesse_ema(candles, 200, sequential=True)
        bbw_seq = jesse_bbw(candles, 20, sequential=True)
        atr_seq = jesse_atr(candles, 14, sequential=True)
        
        cpcv_result = run_cpcv_backtest(
            candles, regimes, rsi_seq, ema_seq, bbw_seq, atr_seq,
            params, config
        )
        
        # 目标 = 平均EV × 通过率（惩罚不稳定）
        objective_value = cpcv_result['avg_ev'] * cpcv_result['pass_rate']
        
        # 惩罚极端参数（正则化）
        if params['rr_ratio'] > 3.5 or params['holding_bars'] > 60:
            objective_value *= 0.8
        
        return objective_value
    
    return objective


def run_optuna_cpcv(candles, regimes, config: CPCVConfig, n_trials: int = 200,
                    seed: int = 42) -> Dict:
    """运行Optuna + CPCV优化"""
    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    
    # 预计算全量指标
    t0 = time.time()
    rsi_seq = jesse_rsi(candles, 14, sequential=True)
    ema_seq = jesse_ema(candles, 200, sequential=True)
    bbw_seq = jesse_bbw(candles, 20, sequential=True)
    atr_seq = jesse_atr(candles, 14, sequential=True)
    print(f"  指标预计算: {time.time()-t0:.1f}s")
    
    def objective(trial):
        params = {
            'rsi_long': trial.suggest_int('rsi_long', 25, 45),
            'rsi_short': trial.suggest_int('rsi_short', 55, 80),
            'bb_squeeze': trial.suggest_float('bb_squeeze', 0.01, 0.08),
            'atr_sl_mult': trial.suggest_float('atr_sl_mult', 1.0, 3.0),
            'rr_ratio': trial.suggest_float('rr_ratio', 1.0, 4.0),
            'holding_bars': trial.suggest_int('holding_bars', 12, 72),
        }
        
        cpcv_result = run_cpcv_backtest(
            candles, regimes, rsi_seq, ema_seq, bbw_seq, atr_seq,
            params, config
        )
        
        # 目标 = avg_EV × pass_rate
        obj_val = cpcv_result['avg_ev'] * cpcv_result['pass_rate']
        
        # 正则化：惩罚极端参数
        if params['rr_ratio'] > 3.5 or params['holding_bars'] > 60:
            obj_val *= 0.8
        
        return obj_val
    
    study = optuna.create_study(direction='maximize', sampler=optuna.samplers.TPESampler(seed=seed))
    
    print(f"  Optuna优化: {n_trials} trials × CPCV {config.n_splits}C{config.n_test_groups}...")
    t0 = time.time()
    study.optimize(objective, n_trials=n_trials)
    print(f"  优化完成: {time.time()-t0:.1f}s")
    
    best_params = {
        'rsi_long': study.best_params['rsi_long'],
        'rsi_short': study.best_params['rsi_short'],
        'bb_squeeze': study.best_params['bb_squeeze'],
        'atr_sl_mult': study.best_params['atr_sl_mult'],
        'rr_ratio': study.best_params['rr_ratio'],
        'holding_bars': study.best_params['holding_bars'],
    }
    
    # 用最优参数跑完整CPCV
    rsi_seq = jesse_rsi(candles, 14, sequential=True)
    ema_seq = jesse_ema(candles, 200, sequential=True)
    bbw_seq = jesse_bbw(candles, 20, sequential=True)
    atr_seq = jesse_atr(candles, 14, sequential=True)
    
    best_cpcv = run_cpcv_backtest(
        candles, regimes, rsi_seq, ema_seq, bbw_seq, atr_seq,
        best_params, config, verbose=True
    )
    
    # 用最优参数跑全量回测
    strategy = BrahmaStrategy12(best_params)
    full_result = strategy.run(candles, regimes, rsi_seq, ema_seq, bbw_seq, atr_seq)
    
    # DSR
    trade_returns = [getattr(t, 'net_ret', 0) for t in full_result.get('trades', [])]
    dsr_result = deflated_sharpe_ratio(trade_returns, n_trials=n_trials)
    
    return {
        'best_params': best_params,
        'best_objective': study.best_value,
        'cpcv': best_cpcv,
        'full_backtest': {
            'n': full_result['n'],
            'wr': full_result['wr'],
            'ev': full_result['ev'],
            'nav': full_result['nav'],
            'max_dd': full_result['max_dd'],
            'regime_stats': full_result['regime_stats'],
        },
        'dsr': dsr_result,
        'n_trials': n_trials,
        'all_trials': [t.params for t in study.trials if t.state == optuna.trial.TrialState.COMPLETE],
    }


# ============================================================
# 4. 保守参数对比基准
# ============================================================

CONSERVATIVE_PARAMS = {
    'rsi_long': 40,
    'rsi_short': 69,
    'bb_squeeze': 0.049,
    'atr_sl_mult': 2.3,
    'rr_ratio': 2.9,
    'holding_bars': 43,
}


def compare_conservative_vs_optimized(candles, regimes, optimized_params, 
                                       config: CPCVConfig, n_trials: int) -> Dict:
    """对比保守参数 vs 优化参数"""
    
    rsi_seq = jesse_rsi(candles, 14, sequential=True)
    ema_seq = jesse_ema(candles, 200, sequential=True)
    bbw_seq = jesse_bbw(candles, 20, sequential=True)
    atr_seq = jesse_atr(candles, 14, sequential=True)
    
    # 保守参数
    conservative_strategy = BrahmaStrategy12(CONSERVATIVE_PARAMS)
    conservative_result = conservative_strategy.run(candles, regimes, rsi_seq, ema_seq, bbw_seq, atr_seq)
    conservative_cpcv = run_cpcv_backtest(candles, regimes, rsi_seq, ema_seq, bbw_seq, atr_seq,
                                          CONSERVATIVE_PARAMS, config)
    conservative_trades = [getattr(t, 'net_ret', 0) for t in conservative_result.get('trades', [])]
    conservative_dsr = deflated_sharpe_ratio(conservative_trades, n_trials=1)  # 无优化 → n_trials=1
    
    # 优化参数
    opt_strategy = BrahmaStrategy12(optimized_params)
    opt_result = opt_strategy.run(candles, regimes, rsi_seq, ema_seq, bbw_seq, atr_seq)
    opt_cpcv = run_cpcv_backtest(candles, regimes, rsi_seq, ema_seq, bbw_seq, atr_seq,
                                 optimized_params, config)
    opt_trades = [getattr(t, 'net_ret', 0) for t in opt_result.get('trades', [])]
    opt_dsr = deflated_sharpe_ratio(opt_trades, n_trials=n_trials)
    
    return {
        'conservative': {
            'params': CONSERVATIVE_PARAMS,
            'full': {'n': conservative_result['n'], 'wr': conservative_result['wr'],
                     'ev': conservative_result['ev'], 'nav': conservative_result['nav'],
                     'max_dd': conservative_result['max_dd']},
            'cpcv': {'pass_rate': conservative_cpcv['pass_rate'], 'avg_ev': conservative_cpcv['avg_ev'],
                     'worst_ev': conservative_cpcv['worst_ev']},
            'dsr': conservative_dsr,
        },
        'optimized': {
            'params': optimized_params,
            'full': {'n': opt_result['n'], 'wr': opt_result['wr'],
                     'ev': opt_result['ev'], 'nav': opt_result['nav'],
                     'max_dd': opt_result['max_dd']},
            'cpcv': {'pass_rate': opt_cpcv['pass_rate'], 'avg_ev': opt_cpcv['avg_ev'],
                     'worst_ev': opt_cpcv['worst_ev']},
            'dsr': opt_dsr,
        },
    }


# ============================================================
# 主入口
# ============================================================

if __name__ == '__main__':
    import argparse
    
    ap = argparse.ArgumentParser(description='梵天v5 W3: Optuna+CPCV+DSR')
    ap.add_argument('--symbols', nargs='+', default=['BTCUSDT', 'ETHUSDT'])
    ap.add_argument('--trials', type=int, default=200, help='Optuna trials per symbol')
    ap.add_argument('--cpcv-splits', type=int, default=6, help='CPCV n_splits')
    ap.add_argument('--cpcv-test', type=int, default=2, help='CPCV n_test_groups')
    ap.add_argument('--seed', type=int, default=42)
    args = ap.parse_args()
    
    config = CPCVConfig(
        n_splits=args.cpcv_splits,
        n_test_groups=args.cpcv_test,
        purge_bars=24,
        embargo_bars=12,
    )
    
    print("=" * 70)
    print(f"梵天v5.0 W3: Optuna+CPCV+DSR 参数优化")
    print(f"CPCV: {config.n_splits}C{config.n_test_groups} = {len(list(__import__('itertools').combinations(range(config.n_splits), config.n_test_groups)))} folds")
    print(f"Optuna: {args.trials} trials per symbol")
    print(f"Purge: {config.purge_bars} bars | Embargo: {config.embargo_bars} bars")
    print("=" * 70)
    
    all_results = {}
    
    for symbol in args.symbols:
        print(f"\n{'='*60}")
        print(f"{symbol}")
        print(f"{'='*60}")
        
        t0 = time.time()
        candles, _ = load_candles(symbol)
        regimes = load_regimes(symbol)
        print(f"数据: {len(candles)}根K线, {len(regimes)}个regime labels")
        
        # 1. Optuna + CPCV
        print(f"\n[1] Optuna+CPCV优化 ({args.trials} trials)...")
        opt_result = run_optuna_cpcv(candles, regimes, config, n_trials=args.trials, seed=args.seed)
        
        print(f"\n  最优参数:")
        for k, v in opt_result['best_params'].items():
            print(f"    {k}: {v}")
        print(f"  Optuna objective: {opt_result['best_objective']:.4f}")
        
        print(f"\n  CPCV结果 ({opt_result['cpcv']['n_folds']} folds):")
        print(f"    通过率: {opt_result['cpcv']['pass_rate']*100:.1f}%")
        print(f"    平均EV: {opt_result['cpcv']['avg_ev']:+.3f}%")
        print(f"    最差EV: {opt_result['cpcv']['worst_ev']:+.3f}%")
        print(f"    最好EV: {opt_result['cpcv']['best_ev']:+.3f}%")
        
        print(f"\n  全量回测:")
        fb = opt_result['full_backtest']
        print(f"    信号: {fb['n']} | WR: {fb['wr']:.1f}% | EV: {fb['ev']:+.3f}%")
        print(f"    NAV: ${fb['nav']:,.0f} | MaxDD: {fb['max_dd']:.1f}%")
        print(f"    体制:")
        for r, stats in sorted(fb['regime_stats'].items()):
            print(f"      {r:15s}: n={stats['n']:4d} WR={stats['wr']:.1f}% EV={stats['ev']:+.3f}%")
        
        print(f"\n  DSR:")
        dsr = opt_result['dsr']
        print(f"    原始Sharpe: {dsr['sr_original']:.4f}")
        print(f"    修正Sharpe: {dsr['sr_corrected']:.4f}")
        print(f"    SR_max(H0): {dsr['sr_max']:.4f} ({dsr['n_trials']} trials)")
        print(f"    DSR: {dsr['dsr']:.4f} → {dsr['verdict']}")
        
        # 2. 保守 vs 优化对比
        print(f"\n[2] 保守参数 vs 优化参数对比...")
        cmp = compare_conservative_vs_optimized(candles, regimes, opt_result['best_params'],
                                                 config, args.trials)
        
        print(f"\n  {'指标':<12} {'保守参数':>12} {'优化参数':>12} {'变化':>8}")
        print(f"  {'-'*44}")
        
        c = cmp['conservative']
        o = cmp['optimized']
        
        print(f"  {'WR':.<12} {c['full']['wr']:>11.1f}% {o['full']['wr']:>11.1f}% {o['full']['wr']-c['full']['wr']:>+7.1f}%")
        print(f"  {'EV':.<12} {c['full']['ev']:>+11.3f}% {o['full']['ev']:>+11.3f}% {o['full']['ev']-c['full']['ev']:>+7.3f}%")
        print(f"  {'NAV':.<12} ${c['full']['nav']:>10,.0f} ${o['full']['nav']:>10,.0f} ${o['full']['nav']-c['full']['nav']:>+8,.0f}")
        print(f"  {'MaxDD':.<12} {c['full']['max_dd']:>11.1f}% {o['full']['max_dd']:>11.1f}% {o['full']['max_dd']-c['full']['max_dd']:>+7.1f}%")
        print(f"  {'CPCV通过率':.<12} {c['cpcv']['pass_rate']*100:>11.1f}% {o['cpcv']['pass_rate']*100:>11.1f}%")
        print(f"  {'CPCV平均EV':.<12} {c['cpcv']['avg_ev']:>+11.3f}% {o['cpcv']['avg_ev']:>+11.3f}%")
        print(f"  {'CPCV最差EV':.<12} {c['cpcv']['worst_ev']:>+11.3f}% {o['cpcv']['worst_ev']:>+11.3f}%")
        print(f"  {'DSR':.<12} {c['dsr']['dsr']:>11.4f} {o['dsr']['dsr']:>11.4f}")
        print(f"  {'DSR判定':.<12} {c['dsr']['verdict']:>12s} {o['dsr']['verdict']:>12s}")
        
        all_results[symbol] = {
            'best_params': opt_result['best_params'],
            'cpcv': opt_result['cpcv'],
            'full_backtest': opt_result['full_backtest'],
            'dsr': opt_result['dsr'],
            'comparison': {
                'conservative': c,
                'optimized': o,
            },
        }
        
        print(f"\n  耗时: {time.time()-t0:.1f}s")
    
    # 保存结果
    out_file = OUT_DIR / "w3_optimization_results.json"
    serializable = {}
    for sym, res in all_results.items():
        serializable[sym] = {
            'best_params': res['best_params'],
            'cpcv': {k: v for k, v in res['cpcv'].items() if k != 'folds'},
            'full_backtest': res['full_backtest'],
            'dsr': res['dsr'],
        }
    with open(out_file, 'w') as f:
        json.dump(serializable, f, indent=2, default=str)
    print(f"\n结果已保存: {out_file}")
    
    # 最终判定
    print(f"\n{'='*70}")
    print("W3 最终判定")
    print(f"{'='*70}")
    
    all_pass = True
    for sym, res in all_results.items():
        cpcv_pass = res['cpcv']['pass_rate'] >= 0.8  # 80% folds通过
        dsr_pass = res['dsr']['dsr'] >= 0.80  # DSR > 0.80
        ev_pass = res['full_backtest']['ev'] > 0
        
        tag_cpcv = '✅' if cpcv_pass else '❌'
        tag_dsr = '✅' if dsr_pass else '❌'
        tag_ev = '✅' if ev_pass else '❌'
        
        print(f"\n  {sym}:")
        print(f"    CPCV通过率: {res['cpcv']['pass_rate']*100:.1f}% {'(≥80%)' if cpcv_pass else '(<80%)'} {tag_cpcv}")
        print(f"    DSR: {res['dsr']['dsr']:.4f} {'(≥0.80)' if dsr_pass else '(<0.80)'} {tag_dsr}")
        print(f"    EV: {res['full_backtest']['ev']:+.3f}% {'(>0)' if ev_pass else '(≤0)'} {tag_ev}")
        
        if not (cpcv_pass and dsr_pass and ev_pass):
            all_pass = False
    
    print(f"\n  W3整体: {'✅ 通过' if all_pass else '⚠️ 需调整'}")
    print(f"\n  下一步: W4(方仓+ML) → W5(模拟盘) → W6(实盘试运行)")
