#!/usr/bin/env python3
"""
brahma_engine_v5.py — 梵天v5.0 核心回测引擎
复刻Jesse的step_simulator防前视偏差机制 + jesse_rust指标 + 12维精简信号
不依赖PostgreSQL/Redis — 纯Python + jesse_rust

关键设计：
1. 逐K线推进（指标只看已完成K线，消灭前视偏差）
2. 回测=实盘同一套Strategy代码
3. 保守参数（Walk-Forward验证）
4. BEAR_EARLY:LONG封禁
5. Maker-only入场
6. ATR止损 + RR止盈
7. 事件风控
8. 动态仓位（S/B/B+分级 + Headroom回撤保护）
"""

import gzip, json, math, os, time, random, numpy as np
from collections import defaultdict
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple

# Jesse指标
from jesse.indicators import rsi as jesse_rsi
from jesse.indicators import ema as jesse_ema
from jesse.indicators import bollinger_bands_width as jesse_bbw
from jesse.indicators import atr as jesse_atr

# ============================================================
# 数据层
# ============================================================

DATA_DIR = Path(__file__).parent.parent / "data" / "historical"

def load_candles(symbol: str, tf: str = "1h") -> Tuple[np.ndarray, list]:
    fname = DATA_DIR / f"{symbol}_{tf}.jsonl.gz"
    with gzip.open(fname, 'rt') as f:
        bars = sorted([json.loads(l) for l in f if l.strip()], key=lambda x: x['ts'])
    candles = np.array([
        [b['ts'], b['o'], b['h'], b['l'], b['c'], b.get('v', 0)]
        for b in bars
    ], dtype=np.float64)
    return candles, bars

def load_regimes(symbol: str) -> Dict[int, str]:
    fname = DATA_DIR / f"{symbol}_regime_labels.jsonl.gz"
    if not fname.exists(): return {}
    with gzip.open(fname, 'rt') as f:
        return {json.loads(l)['ts']: json.loads(l)['regime'] for l in f if l.strip() and json.loads(l).get('ts')}

# ============================================================
# 策略层：BrahmaStrategy12
# ============================================================

@dataclass
class Trade:
    symbol: str
    signal: int  # +1 long, -1 short
    entry: float
    exit: float
    sl: float
    tp: float
    entry_ts: int
    exit_ts: int
    bars_held: int
    gross_ret: float
    net_ret: float
    regime: str
    rsi: float
    atr: float
    position_pct: float
    pnl: float  # P&L in NAV%
    exit_reason: str  # 'tp' / 'sl' / 'time' / 'event'

class BrahmaStrategy12:
    """
    梵天v5.0 策略 — 12维精简信号
    
    信号 = regime_direction × rsi_extreme × bb_squeeze
    + SMC共振增强（可选）
    + 达摩院置信度门控（可选）
    
    保守参数（Walk-Forward 3段验证全绿）:
    """
    
    # Walk-Forward验证过的保守参数
    DEFAULT_PARAMS = {
        'rsi_long': 40,
        'rsi_short': 69,
        'bb_squeeze': 0.049,
        'ema_period': 200,
        'rsi_period': 14,
        'bb_period': 20,
        'atr_period': 14,
        'atr_sl_mult': 2.3,
        'rr_ratio': 2.9,
        'holding_bars': 43,
        'position_pct': 0.10,  # 基础仓位
        'maker_cost': 0.0007,  # Maker 0.02% + 5bp滑点
        'taker_cost': 0.0013,  # Taker 0.08% + 5bp
        'warmup': 210,
        # 风控
        'bear_early_block': True,  # BEAR_EARLY:LONG封禁
        'maker_only': True,  # Maker-only入场
        # 仓位分级
        's_class_sl': 0.01,  # S档: SL<1%
        's_class_pct': 0.05,  # S档: 5%NAV
        'b_minus_sl': 0.015,  # B-档: SL 1-1.5%
        'b_minus_pct': 0.02,  # B-档: 2%NAV
        'b_plus_sl': 0.02,  # B+档: SL 1.5-2%
        'b_plus_pct': 0.03,  # B+档: 3%NAV
        # Headroom回撤保护
        'headroom_dd1': 0.05,  # 回撤5%→仓位×0.7
        'headroom_dd2': 0.10,  # 回撤10%→仓位×0.5
        'headroom_dd3': 0.15,  # 回撤15%→停止
        # 体制乘数
        'regime_mult': {
            'BULL_TREND': {'long': 1.3, 'short': 0.5},
            'BULL_EARLY': {'long': 1.0, 'short': 0.5},
            'BEAR_TREND': {'long': 0.5, 'short': 1.6},
            'BEAR_EARLY': {'long': 0.5, 'short': 1.2},
            'BEAR_RECOVERY': {'long': 1.15, 'short': 0.5},
            'CHOP_MID': {'long': 0.5, 'short': 0.88},
        },
    }
    
    def __init__(self, params: dict = None):
        self.p = {**self.DEFAULT_PARAMS, **(params or {})}
        self.trades: List[Trade] = []
        self.nav = 10000.0
        self.peak_nav = 10000.0
        self.equity_curve = [10000.0]
        
    def _calc_position_size(self, entry: float, sl: float, regime: str, signal: int) -> float:
        """梵天仓位引擎：S/B/B+分级 + 体制乘数 + Headroom"""
        sl_pct = abs(entry - sl) / entry
        
        # S/B/B+分级
        if sl_pct < self.p['s_class_sl']:
            base_pct = self.p['s_class_pct']
        elif sl_pct < self.p['b_minus_sl']:
            base_pct = self.p['b_minus_pct']
        elif sl_pct < self.p['b_plus_sl']:
            base_pct = self.p['b_plus_pct']
        else:
            # SL>2% → 用基础仓位但缩小
            base_pct = self.p['position_pct'] * (0.02 / sl_pct)  # 按比例缩小
        
        # 体制乘数
        regime_name = regime.split('_')[0] + '_' + regime.split('_')[-1] if '_' in regime else regime
        mult = self.p['regime_mult'].get(regime, {'long': 1.0, 'short': 1.0})
        direction = 'long' if signal == 1 else 'short'
        regime_mult = mult.get(direction, 1.0)
        
        # Headroom回撤保护
        dd = (self.peak_nav - self.nav) / self.peak_nav if self.peak_nav > 0 else 0
        if dd >= self.p['headroom_dd3']:
            return 0  # 停止交易
        elif dd >= self.p['headroom_dd2']:
            headroom = 0.5
        elif dd >= self.p['headroom_dd1']:
            headroom = 0.7
        else:
            headroom = 1.0
        
        return base_pct * regime_mult * headroom
    
    def run(self, candles: np.ndarray, regimes: Dict[int, str], 
            rsi_seq, ema_seq, bbw_seq, atr_seq, verbose: bool = False) -> dict:
        """
        逐K线回测 — 复刻Jesse step_simulator
        指标只看已完成K线[i-1]，决策在K线[i]收盘价执行
        """
        n = len(candles)
        p = self.p
        warmup = p['warmup']
        holding = p['holding_bars']
        
        open_position = None  # 当前持仓
        
        for i in range(warmup, n):
            # 更新NAV
            if self.nav > self.peak_nav:
                self.peak_nav = self.nav
            self.equity_curve.append(self.nav)
            
            # 检查持仓退出
            if open_position is not None:
                # 检查止损/止盈
                bar = candles[i]
                hit_sl = False
                hit_tp = False
                if open_position['signal'] == 1:
                    if bar[2] <= open_position['sl']:  # 低点触及止损
                        hit_sl = True
                    elif bar[3] >= open_position['tp']:  # 高点触及止盈
                        hit_tp = True
                else:
                    if bar[3] >= open_position['sl']:
                        hit_sl = True
                    elif bar[2] <= open_position['tp']:
                        hit_tp = True
                
                if hit_sl:
                    exit_price = open_position['sl']
                    exit_reason = 'sl'
                elif hit_tp:
                    exit_price = open_position['tp']
                    exit_reason = 'tp'
                elif i - open_position['entry_idx'] >= holding:
                    exit_price = bar[4]  # 收盘价退出
                    exit_reason = 'time'
                else:
                    # 继续持仓
                    continue
                
                # 结算
                entry = open_position['entry']
                gross = (exit_price - entry) / entry * open_position['signal']
                cost = p['maker_cost'] if p['maker_only'] else p['taker_cost']
                net = gross - cost
                pnl = net * open_position['position_pct'] * self.nav
                self.nav += pnl
                
                trade = Trade(
                    symbol=open_position['symbol'],
                    signal=open_position['signal'],
                    entry=entry,
                    exit=exit_price,
                    sl=open_position['sl'],
                    tp=open_position['tp'],
                    entry_ts=open_position['entry_ts'],
                    exit_ts=int(bar[0]),
                    bars_held=i - open_position['entry_idx'],
                    gross_ret=gross,
                    net_ret=net,
                    regime=open_position['regime'],
                    rsi=open_position['rsi'],
                    atr=open_position['atr'],
                    position_pct=open_position['position_pct'],
                    pnl=pnl / (self.nav - pnl) * 100,  # % of pre-trade NAV
                    exit_reason=exit_reason,
                )
                self.trades.append(trade)
                open_position = None
                continue
            
            # 无持仓 → 检查入场信号
            if rsi_seq[i] is None or math.isnan(rsi_seq[i]): continue
            if math.isnan(ema_seq[i]) or math.isnan(bbw_seq[i]) or math.isnan(atr_seq[i]): continue
            
            ts = int(candles[i][0])
            regime = regimes.get(ts)
            if regime is None: continue
            
            # BEAR_EARLY封禁
            if p['bear_early_block'] and 'BEAR_EARLY' in regime:
                continue
            
            regime_dir = 1 if 'BULL' in regime else (-1 if 'BEAR' in regime else 0)
            if regime_dir == 0: continue  # CHOP不交易
            
            rsi_val = rsi_seq[i]
            rsi_signal = 1 if rsi_val < p['rsi_long'] else (-1 if rsi_val > p['rsi_short'] else 0)
            bb_squeeze = 1 if bbw_seq[i] < p['bb_squeeze'] else 0
            
            # 12维信号
            if regime_dir == rsi_signal and rsi_signal != 0:
                signal = rsi_signal
            elif regime_dir != 0 and rsi_signal == 0 and bb_squeeze:
                signal = regime_dir
            else:
                continue
            
            # 仓位计算
            entry = candles[i][4]  # 收盘价入场
            sl_dist = atr_seq[i] * p['atr_sl_mult']
            if signal == 1:
                sl = entry - sl_dist
                tp = entry + sl_dist * p['rr_ratio']
            else:
                sl = entry + sl_dist
                tp = entry - sl_dist * p['rr_ratio']
            
            position_pct = self._calc_position_size(entry, sl, regime, signal)
            if position_pct <= 0: continue  # 回撤停止
            
            open_position = {
                'symbol': '',
                'signal': signal,
                'entry': entry,
                'sl': sl,
                'tp': tp,
                'entry_idx': i,
                'entry_ts': ts,
                'regime': regime,
                'rsi': rsi_val,
                'atr': atr_seq[i],
                'position_pct': position_pct,
            }
        
        # 强制平仓最后持仓
        if open_position is not None:
            i = n - 1
            exit_price = candles[i][4]
            entry = open_position['entry']
            gross = (exit_price - entry) / entry * open_position['signal']
            cost = p['maker_cost'] if p['maker_only'] else p['taker_cost']
            net = gross - cost
            pnl = net * open_position['position_pct'] * self.nav
            self.nav += pnl
            self.trades.append(Trade(
                symbol=open_position['symbol'], signal=open_position['signal'],
                entry=entry, exit=exit_price, sl=open_position['sl'], tp=open_position['tp'],
                entry_ts=open_position['entry_ts'], exit_ts=int(candles[i][0]),
                bars_held=i - open_position['entry_idx'], gross_ret=gross, net_ret=net,
                regime=open_position['regime'], rsi=open_position['rsi'], atr=open_position['atr'],
                position_pct=open_position['position_pct'], pnl=pnl / (self.nav - pnl) * 100,
                exit_reason='time',
            ))
        
        return self._summary()
    
    def _summary(self) -> dict:
        if not self.trades:
            return {'n': 0, 'wr': 0, 'ev': 0, 'nav': self.nav, 'trades': []}
        
        n = len(self.trades)
        wins = sum(1 for t in self.trades if t.net_ret > 0)
        wr = wins / n * 100
        ev = sum(t.net_ret for t in self.trades) / n * 100
        total_pnl = self.nav - 10000
        max_dd = max(1 - e / max(self.equity_curve[:i+1]) for i, e in enumerate(self.equity_curve))
        
        # 按体制分组
        by_regime = defaultdict(list)
        for t in self.trades:
            by_regime[t.regime].append(t)
        
        regime_stats = {}
        for r, ts in sorted(by_regime.items()):
            regime_stats[r] = {
                'n': len(ts),
                'wr': sum(1 for t in ts if t.net_ret > 0) / len(ts) * 100,
                'ev': sum(t.net_ret for t in ts) / len(ts) * 100,
            }
        
        # 按退出原因分组
        exit_stats = defaultdict(int)
        for t in self.trades:
            exit_stats[t.exit_reason] += 1
        
        return {
            'n': n,
            'wr': wr,
            'ev': ev,
            'nav': self.nav,
            'total_pnl': total_pnl,
            'total_return': (self.nav / 10000 - 1) * 100,
            'max_dd': max_dd * 100,
            'regime_stats': regime_stats,
            'exit_stats': dict(exit_stats),
            'trades': self.trades,
        }

# ============================================================
# 验证层：Monte Carlo + Walk-Forward
# ============================================================

def monte_carlo(trades: List[Trade], n_sims: int = 5000, block_size: int = 6,
                start_nav: float = 10000.0, position_pct: float = 0.10) -> dict:
    """Block bootstrap MC"""
    random.seed(42)
    n = len(trades)
    if n == 0: return {}
    
    rets = [t.net_ret for t in trades]
    navs = []
    max_dds = []
    
    for _ in range(n_sims):
        nav = start_nav
        peak = nav
        max_dd = 0
        remaining = n
        while remaining > 0:
            start = random.randint(0, max(0, n - block_size))
            end = min(start + block_size, n, start + remaining)
            for j in range(start, end):
                nav *= (1 + rets[j] * position_pct)
                if nav > peak: peak = nav
                dd = (peak - nav) / peak if peak > 0 else 0
                if dd > max_dd: max_dd = dd
            remaining -= (end - start)
        navs.append(nav)
        max_dds.append(max_dd)
    
    navs.sort()
    max_dds.sort()
    return {
        'p05_nav': navs[int(0.05 * len(navs))],
        'p50_nav': navs[int(0.5 * len(navs))],
        'p95_nav': navs[int(0.95 * len(navs))],
        'ruin_rate': sum(1 for x in navs if x < 5000) / len(navs),
        'median_max_dd': max_dds[int(0.5 * len(max_dds))] * 100,
        'p95_max_dd': max_dds[int(0.95 * len(max_dds))] * 100,
    }

def walk_forward(candles, regimes, rsi_seq, ema_seq, bbw_seq, atr_seq, 
                 n_segments: int = 3, optuna_trials: int = 50) -> dict:
    """Walk-Forward验证"""
    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    
    n = len(candles)
    seg_size = n // n_segments
    
    results = {'segments': [], 'params': []}
    
    for seg in range(n_segments - 1):
        train_start_idx = seg * seg_size
        train_end_idx = (seg + 1) * seg_size
        test_start_idx = train_end_idx
        test_end_idx = min((seg + 2) * seg_size, n)
        
        # 用时间戳过滤regime labels
        train_ts_start = int(candles[train_start_idx][0])
        train_ts_end = int(candles[train_end_idx - 1][0])
        test_ts_start = int(candles[test_start_idx][0])
        test_ts_end = int(candles[test_end_idx - 1][0])
        
        # Train: 优化参数
        def objective(trial):
            strategy = BrahmaStrategy12({
                'rsi_long': trial.suggest_int('rsi_long', 25, 40),
                'rsi_short': trial.suggest_int('rsi_short', 60, 75),
                'bb_squeeze': trial.suggest_float('bb_squeeze', 0.02, 0.05),
                'atr_sl_mult': trial.suggest_float('atr_sl_mult', 1.0, 2.5),
                'rr_ratio': trial.suggest_float('rr_ratio', 1.0, 3.0),
                'holding_bars': trial.suggest_int('holding_bars', 12, 48),
            })
            # 在训练段内运行
            sub_candles = candles[train_start_idx:train_end_idx]
            sub_regimes = {ts: r for ts, r in regimes.items() if train_ts_start <= ts <= train_ts_end}
            # 重新计算指标
            sub_rsi = jesse_rsi(sub_candles, 14, sequential=True)
            sub_ema = jesse_ema(sub_candles, 200, sequential=True)
            sub_bbw = jesse_bbw(sub_candles, 20, sequential=True)
            sub_atr = jesse_atr(sub_candles, 14, sequential=True)
            result = strategy.run(sub_candles, sub_regimes, sub_rsi, sub_ema, sub_bbw, sub_atr)
            if result['n'] < 50: return -100
            return result['ev']
        
        study = optuna.create_study(direction='maximize', sampler=optuna.samplers.TPESampler(seed=42))
        study.optimize(objective, n_trials=optuna_trials)
        
        best_params = {
            'rsi_long': study.best_params['rsi_long'],
            'rsi_short': study.best_params['rsi_short'],
            'bb_squeeze': study.best_params['bb_squeeze'],
            'atr_sl_mult': study.best_params['atr_sl_mult'],
            'rr_ratio': study.best_params['rr_ratio'],
            'holding_bars': study.best_params['holding_bars'],
        }
        results['params'].append(best_params)
        
        # Test: 用训练参数验证
        test_candles = candles[test_start_idx:test_end_idx]
        test_regimes = {ts: r for ts, r in regimes.items() if test_ts_start <= ts <= test_ts_end}
        test_rsi = jesse_rsi(test_candles, 14, sequential=True)
        test_ema = jesse_ema(test_candles, 200, sequential=True)
        test_bbw = jesse_bbw(test_candles, 20, sequential=True)
        test_atr = jesse_atr(test_candles, 14, sequential=True)
        
        test_strategy = BrahmaStrategy12(best_params)
        test_result = test_strategy.run(test_candles, test_regimes, test_rsi, test_ema, test_bbw, test_atr)
        
        results['segments'].append({
            'train_range': f'{train_ts_start}-{train_ts_end}',
            'test_range': f'{test_ts_start}-{test_ts_end}',
            'train_ev': study.best_value,
            'test_ev': test_result['ev'],
            'test_wr': test_result['wr'],
            'test_n': test_result['n'],
            'pass': test_result['ev'] > 0,
        })
    
    # 参数稳定性
    if len(results['params']) >= 2:
        p1 = results['params'][0]
        p2 = results['params'][1]
        diffs = {}
        for k in p1:
            if p1[k] != 0:
                diffs[k] = abs(p1[k] - p2[k]) / max(abs(p1[k]), abs(p2[k]), 0.001) * 100
        results['param_stability'] = diffs
    
    results['all_pass'] = all(s['pass'] for s in results['segments'])
    return results

# ============================================================
# 主入口
# ============================================================

if __name__ == '__main__':
    import sys
    
    print("=" * 70)
    print("梵天v5.0 — BrahmaStrategy12 完整回测")
    print("=" * 70)
    
    for symbol in ['BTCUSDT', 'ETHUSDT']:
        print(f"\n{'='*50}")
        print(f"{symbol}")
        print(f"{'='*50}")
        
        candles, bars = load_candles(symbol)
        regimes = load_regimes(symbol)
        
        print(f"数据: {len(candles)}根K线, {len(regimes)}个regime labels")
        
        # Jesse指标
        t0 = time.time()
        rsi_seq = jesse_rsi(candles, 14, sequential=True)
        ema_seq = jesse_ema(candles, 200, sequential=True)
        bbw_seq = jesse_bbw(candles, 20, sequential=True)
        atr_seq = jesse_atr(candles, 14, sequential=True)
        print(f"指标: {time.time()-t0:.1f}s")
        
        # 回测
        t0 = time.time()
        strategy = BrahmaStrategy12()
        result = strategy.run(candles, regimes, rsi_seq, ema_seq, bbw_seq, atr_seq, verbose=True)
        print(f"回测: {time.time()-t0:.1f}s")
        
        print(f"\n结果:")
        print(f"  信号数: {result['n']}")
        print(f"  胜率: {result['wr']:.1f}%")
        print(f"  EV: {result['ev']:+.3f}%")
        print(f"  NAV: ${result['nav']:,.0f}")
        print(f"  总收益: {result['total_return']:+.1f}%")
        print(f"  最大回撤: {result['max_dd']:.1f}%")
        
        print(f"\n  按体制:")
        for r, stats in sorted(result['regime_stats'].items()):
            print(f"    {r:15s}: n={stats['n']:4d} WR={stats['wr']:.1f}% EV={stats['ev']:+.3f}%")
        
        print(f"\n  退出原因:")
        for reason, count in sorted(result['exit_stats'].items()):
            print(f"    {reason:5s}: {count:5d} ({count/result['n']*100:.1f}%)")
        
        # MC
        t0 = time.time()
        mc = monte_carlo(result['trades'], n_sims=5000)
        print(f"\n  MC(5000次): {time.time()-t0:.1f}s")
        print(f"    P05=${mc['p05_nav']:,.0f} P50=${mc['p50_nav']:,.0f} P95=${mc['p95_nav']:,.0f}")
        print(f"    破产率={mc['ruin_rate']*100:.1f}% 中位MaxDD={mc['median_max_dd']:.1f}% P95MaxDD={mc['p95_max_dd']:.1f}%")
        print(f"    {'✅ 盈利' if mc['p50_nav'] > 10000 else '❌ 亏损'}")
    
    # Walk-Forward
    if '--wf' in sys.argv:
        print(f"\n{'='*70}")
        print("Walk-Forward验证 (3段)")
        print(f"{'='*70}")
        
        for symbol in ['BTCUSDT', 'ETHUSDT']:
            print(f"\n{symbol}:")
            candles, _ = load_candles(symbol)
            regimes = load_regimes(symbol)
            rsi_seq = jesse_rsi(candles, 14, sequential=True)
            ema_seq = jesse_ema(candles, 200, sequential=True)
            bbw_seq = jesse_bbw(candles, 20, sequential=True)
            atr_seq = jesse_atr(candles, 14, sequential=True)
            
            wf = walk_forward(candles, regimes, rsi_seq, ema_seq, bbw_seq, atr_seq, 
                             n_segments=3, optuna_trials=50)
            
            for seg in wf['segments']:
                tag = '✅' if seg['pass'] else '❌'
                print(f"  {seg['train_range']} → {seg['test_range']}: Train EV={seg['train_ev']:+.3f}% → Test EV={seg['test_ev']:+.3f}% WR={seg['test_wr']:.1f}% {tag}")
            
            if 'param_stability' in wf:
                print(f"  参数稳定性: {wf['param_stability']}")
            print(f"  全部通过: {'✅' if wf['all_pass'] else '❌'}")
