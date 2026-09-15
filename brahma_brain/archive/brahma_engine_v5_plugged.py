#!/usr/bin/env python3
"""
brahma_engine_v5_plugged.py — v5.0 + Plugin接入版
对比 有/无Plugin 的回测结果
"""
import sys, math, time, gzip, json, random, numpy as np
from pathlib import Path
from collections import defaultdict

BRAIN = Path(__file__).parent
sys.path.insert(0, str(BRAIN))
sys.path.insert(0, str(BRAIN.parent))

from jesse.indicators import rsi as jesse_rsi, ema as jesse_ema, bollinger_bands_width as jesse_bbw, atr as jesse_atr
from brahma_plugins import PluginManager, RegimePlugin, SMCPlugin, RiskPlugin

DATA_DIR = BRAIN.parent / "data" / "historical"

def load_candles(symbol):
    with gzip.open(f"{DATA_DIR}/{symbol}_1h.jsonl.gz", 'rt') as f:
        bars = sorted([json.loads(l) for l in f if l.strip()], key=lambda x: x['ts'])
    return np.array([[b['ts'], b['o'], b['h'], b['l'], b['c'], b.get('v', 0)] for b in bars], dtype=np.float64)

def run_backtest_plugged(candles, regimes, rsi_s, ema_s, bbw_s, atr_s,
                         symbol='BTCUSDT', use_smc=False, use_risk_plugin=False,
                         params=None):
    """
    v5回测 + Plugin选项
    use_smc: 是否用SMCPlugin增强信号
    use_risk_plugin: 是否用RiskPlugin计算仓位（替代简化版）
    """
    p = {
        'rsi_long': 40, 'rsi_short': 69, 'bb_squeeze': 0.049,
        'atr_sl_mult': 2.3, 'rr_ratio': 2.9, 'holding_bars': 43,
        'maker_cost': 0.0007, 'warmup': 210,
        'bear_early_block': True,
        **(params or {})
    }
    
    # 初始化Plugins
    pm = PluginManager.create(mode="backtest")
    
    n = len(candles)
    warmup = p['warmup']
    holding = int(p['holding_bars'])
    
    trades = []
    nav = 10000.0
    peak_nav = 10000.0
    open_pos = None
    smc_cache = {}  # SMC结果缓存
    
    for i in range(warmup, n):
        if nav > peak_nav: peak_nav = nav
        
        # 检查持仓退出
        if open_pos:
            bar = candles[i]
            hit_sl = hit_tp = False
            if open_pos['signal'] == 1:
                if bar[2] <= open_pos['sl']: hit_sl = True
                elif bar[3] >= open_pos['tp']: hit_tp = True
            else:
                if bar[3] >= open_pos['sl']: hit_sl = True
                elif bar[2] <= open_pos['tp']: hit_tp = True
            
            if hit_sl:
                exit_p = open_pos['sl']; reason = 'sl'
            elif hit_tp:
                exit_p = open_pos['tp']; reason = 'tp'
            elif i - open_pos['entry_idx'] >= holding:
                exit_p = bar[4]; reason = 'time'
            else:
                continue
            
            gross = (exit_p - open_pos['entry']) / open_pos['entry'] * open_pos['signal']
            net = gross - p['maker_cost']
            pnl = net * open_pos['pct'] * nav
            nav += pnl
            trades.append({
                'signal': open_pos['signal'], 'net_ret': net,
                'regime': open_pos['regime'], 'reason': reason,
                'pct': open_pos['pct'], 'pnl': pnl,
                'smc_used': open_pos.get('smc_used', False),
            })
            open_pos = None
            continue
        
        # 无持仓 → 检查入场
        if math.isnan(rsi_s[i]) or math.isnan(ema_s[i]) or math.isnan(bbw_s[i]) or math.isnan(atr_s[i]):
            continue
        
        ts = int(candles[i][0])
        regime = regimes.get(ts)
        if regime is None: continue
        if p['bear_early_block'] and 'BEAR_EARLY' in regime: continue
        
        regime_dir = 1 if 'BULL' in regime else (-1 if 'BEAR' in regime else 0)
        if regime_dir == 0: continue
        
        rsi_signal = 1 if rsi_s[i] < p['rsi_long'] else (-1 if rsi_s[i] > p['rsi_short'] else 0)
        bb_sq = 1 if bbw_s[i] < p['bb_squeeze'] else 0
        
        if regime_dir == rsi_signal and rsi_signal != 0:
            signal = rsi_signal
        elif regime_dir != 0 and rsi_signal == 0 and bb_sq:
            signal = regime_dir
        else:
            continue
        
        # SMC增强（可选）
        smc_used = False
        smc_resonance = False
        if use_smc:
            # 缓存SMC结果（每6根K线刷新一次）
            cache_key = i // 6
            if cache_key not in smc_cache:
                try:
                    has_resonance, res_score = pm.smc.check_resonance(
                        symbol, 'LONG' if signal == 1 else 'SHORT')
                    smc_cache[cache_key] = (has_resonance, res_score)
                except:
                    smc_cache[cache_key] = (False, 0)
            smc_resonance, smc_score = smc_cache[cache_key]
            # SMC共振要求：只有共振时才入场
            if not smc_resonance:
                continue
            smc_used = True
        
        entry = candles[i][4]
        sl_dist = atr_s[i] * p['atr_sl_mult']
        if signal == 1: sl = entry - sl_dist; tp = entry + sl_dist * p['rr_ratio']
        else: sl = entry + sl_dist; tp = entry - sl_dist * p['rr_ratio']
        
        # 仓位计算
        if use_risk_plugin:
            direction = 'LONG' if signal == 1 else 'SHORT'
            risk_result = pm.risk.size(
                entry=entry, sl=sl, regime=regime, direction=direction,
                nav=nav, peak_nav=peak_nav, symbol=symbol)
            position_pct = risk_result['pct']
        else:
            # 简化版仓位（v5原始）
            sl_pct = abs(entry - sl) / entry
            if sl_pct < 0.01: base = 0.05
            elif sl_pct < 0.015: base = 0.02
            elif sl_pct < 0.02: base = 0.03
            else: base = 0.10 * (0.02 / max(sl_pct, 0.001))
            
            mults = {'BULL_TREND': {'L':1.3,'S':0.5}, 'BEAR_TREND': {'L':0.5,'S':1.6},
                     'BEAR_RECOVERY': {'L':1.15,'S':0.5}, 'BULL_EARLY': {'L':1.0,'S':0.5},
                     'BEAR_EARLY': {'L':0.5,'S':1.2}, 'CHOP_MID': {'L':0.5,'S':0.88}}
            d = 'L' if signal == 1 else 'S'
            rm = mults.get(regime, {'L':1.0,'S':1.0}).get(d, 1.0)
            
            dd = (peak_nav - nav) / peak_nav if peak_nav > 0 else 0
            if dd >= 0.15: position_pct = 0
            elif dd >= 0.10: position_pct = base * rm * 0.5
            elif dd >= 0.05: position_pct = base * rm * 0.7
            else: position_pct = base * rm
        
        if position_pct <= 0: continue
        position_pct = min(position_pct, 0.10)
        
        open_pos = {
            'signal': signal, 'entry': entry, 'sl': sl, 'tp': tp,
            'entry_idx': i, 'entry_ts': ts, 'regime': regime,
            'pct': position_pct, 'smc_used': smc_used,
        }
    
    # 强平最后持仓
    if open_pos:
        exit_p = candles[-1][4]
        gross = (exit_p - open_pos['entry']) / open_pos['entry'] * open_pos['signal']
        net = gross - p['maker_cost']
        pnl = net * open_pos['pct'] * nav
        nav += pnl
        trades.append({
            'signal': open_pos['signal'], 'net_ret': net,
            'regime': open_pos['regime'], 'reason': 'time',
            'pct': open_pos['pct'], 'pnl': pnl,
            'smc_used': open_pos.get('smc_used', False),
        })
    
    if not trades:
        return {'n': 0, 'wr': 0, 'ev': 0, 'nav': nav}
    
    n_t = len(trades)
    wr = sum(1 for t in trades if t['net_ret'] > 0) / n_t * 100
    ev = sum(t['net_ret'] for t in trades) / n_t * 100
    
    by_regime = defaultdict(list)
    for t in trades: by_regime[t['regime']].append(t)
    regime_stats = {r: {'n': len(ts), 'wr': sum(1 for t in ts if t['net_ret']>0)/len(ts)*100,
                        'ev': sum(t['net_ret'] for t in ts)/len(ts)*100}
                    for r, ts in by_regime.items()}
    
    return {
        'n': n_t, 'wr': wr, 'ev': ev, 'nav': nav,
        'return_pct': (nav/10000-1)*100,
        'regime_stats': regime_stats,
        'trades': trades,
    }

if __name__ == '__main__':
    print("=" * 70)
    print("v5.0 + Plugin 对比回测")
    print("=" * 70)
    
    for symbol in ['BTCUSDT', 'ETHUSDT']:
        candles = load_candles(symbol)
        # Regime labels
        rp = RegimePlugin(mode="backtest")
        regimes = rp.load_labels(symbol)
        
        rsi_s = jesse_rsi(candles, 14, sequential=True)
        ema_s = jesse_ema(candles, 200, sequential=True)
        bbw_s = jesse_bbw(candles, 20, sequential=True)
        atr_s = jesse_atr(candles, 14, sequential=True)
        
        print(f"\n{'='*50}")
        print(f"{symbol} ({len(candles)}K线)")
        print(f"{'='*50}")
        
        # 1. 原始v5（无Plugin）
        t0 = time.time()
        r1 = run_backtest_plugged(candles, regimes, rsi_s, ema_s, bbw_s, atr_s,
                                   symbol=symbol, use_smc=False, use_risk_plugin=False)
        print(f"\n[1] 原始v5 (无Plugin): {time.time()-t0:.1f}s")
        print(f"  n={r1['n']} WR={r1['wr']:.1f}% EV={r1['ev']:+.3f}% NAV=${r1['nav']:,.0f} 收益={r1['return_pct']:+.1f}%")
        for rg, st in sorted(r1['regime_stats'].items()):
            print(f"    {rg:15s}: n={st['n']:4d} WR={st['wr']:.1f}% EV={st['ev']:+.3f}%")
        
        # 2. v5 + RiskPlugin
        t0 = time.time()
        r2 = run_backtest_plugged(candles, regimes, rsi_s, ema_s, bbw_s, atr_s,
                                   symbol=symbol, use_smc=False, use_risk_plugin=True)
        print(f"\n[2] v5 + RiskPlugin: {time.time()-t0:.1f}s")
        print(f"  n={r2['n']} WR={r2['wr']:.1f}% EV={r2['ev']:+.3f}% NAV=${r2['nav']:,.0f} 收益={r2['return_pct']:+.1f}%")
        for rg, st in sorted(r2['regime_stats'].items()):
            print(f"    {rg:15s}: n={st['n']:4d} WR={st['wr']:.1f}% EV={st['ev']:+.3f}%")
        
        # 3. v5 + SMC + RiskPlugin
        t0 = time.time()
        r3 = run_backtest_plugged(candles, regimes, rsi_s, ema_s, bbw_s, atr_s,
                                   symbol=symbol, use_smc=True, use_risk_plugin=True)
        print(f"\n[3] v5 + SMC + Risk: {time.time()-t0:.1f}s")
        print(f"  n={r3['n']} WR={r3['wr']:.1f}% EV={r3['ev']:+.3f}% NAV=${r3['nav']:,.0f} 收益={r3['return_pct']:+.1f}%")
        if r3['n'] > 0:
            smc_used = sum(1 for t in r3['trades'] if t.get('smc_used'))
            print(f"  SMC过滤: {r3['n']}信号 (原始{r1['n']}→过滤{r3['n']}, 保留{r3['n']/r1['n']*100:.1f}%)")
            for rg, st in sorted(r3['regime_stats'].items()):
                print(f"    {rg:15s}: n={st['n']:4d} WR={st['wr']:.1f}% EV={st['ev']:+.3f}%")
        
        # 对比
        print(f"\n  对比:")
        print(f"    原始 → +Risk → +SMC+Risk")
        print(f"    EV: {r1['ev']:+.3f}% → {r2['ev']:+.3f}% → {r3['ev']:+.3f}%")
        print(f"    NAV: ${r1['nav']:,.0f} → ${r2['nav']:,.0f} → ${r3['nav']:,.0f}")
        print(f"    信号: {r1['n']} → {r2['n']} → {r3['n']}")
