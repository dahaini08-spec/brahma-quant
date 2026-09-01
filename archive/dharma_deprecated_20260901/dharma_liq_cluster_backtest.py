#!/usr/bin/env python3
"""
达摩院 × 梵天 · 清算集群TP回测引擎
Liquidation Cluster as TP Target — Alpha Source Verification
6.5年全量数据 | 2020-01-01 ~ 2026-07-10 | 无上帝视角

核心假设：
  "清算集群（Liquidation Cluster）作为TP目标，是真实alpha来源"

方法论：
  1. 在信号产生时（蜡烛收盘后），用 **历史** 订单簿代理数据估算清算集群位置
     代理方法：K线数据中，成交量突出 + 价格回调后快速反转的区域 = 历史清算集群
  2. 信号策略：RSI + 结构突破（CHoCH）入场（无上帝视角）
  3. TP1=固定比例(2%)、TP2=清算集群位置 对比实测
  4. 统计：胜率、盈亏比、期望值、最大回撤

清算集群代理计算（无上帝视角实战标准）：
  方法A: 成交量加权价格密度峰值（Volume Profile基于历史100根K线）
  方法B: 近期高低点 × 杠杆倍数矩阵（基于上一个波段高低点）
  方法C: 两者取均值，距离最近且在方向上的集群作为TP
"""
import json, sys, os
import numpy as np
from pathlib import Path
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass, field

BASE = Path(__file__).parent.parent
DATA = BASE / "data" / "backtest"

# ─────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────
@dataclass
class Candle:
    ts: int
    o: float
    h: float
    l: float
    c: float
    v: float

@dataclass
class Trade:
    entry_ts: int
    entry_price: float
    direction: str          # 'long' | 'short'
    sl: float
    tp_fixed: float         # 固定2% TP
    tp_liq: float           # 清算集群TP（代理）
    tp_liq_source: str      # 'vol_profile' | 'leverage_matrix' | 'combined'
    exit_ts: Optional[int] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None   # 'tp_fixed'|'tp_liq'|'sl'|'timeout'

@dataclass
class BacktestResult:
    symbol: str
    timeframe: str
    total_trades: int = 0
    # 固定TP结果
    fixed_tp_wins: int = 0
    fixed_tp_losses: int = 0
    fixed_tp_ev: float = 0.0
    # 清算集群TP结果
    liq_tp_wins: int = 0
    liq_tp_losses: int = 0
    liq_tp_ev: float = 0.0
    # 清算集群相比固定TP的改进
    liq_vs_fixed_improvement: float = 0.0
    # 清算集群预测准确率
    liq_hit_rate: float = 0.0
    liq_avg_dist_pct: float = 0.0    # 清算集群TP平均距离%
    trades: List[dict] = field(default_factory=list)

# ─────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────
def load_klines(sym: str, tf: str) -> List[Candle]:
    f = DATA / f"{sym}_{tf}.json"
    raw = json.load(open(f))
    return [Candle(
        ts=int(r[0]),
        o=float(r[1]), h=float(r[2]),
        l=float(r[3]), c=float(r[4]),
        v=float(r[5])
    ) for r in raw]

def compute_rsi(closes: np.ndarray, period: int = 14) -> np.ndarray:
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = np.zeros(len(closes))
    avg_loss = np.zeros(len(closes))
    # 初始EMA
    avg_gain[period] = np.mean(gains[:period])
    avg_loss[period] = np.mean(losses[:period])
    for i in range(period + 1, len(closes)):
        avg_gain[i] = (avg_gain[i-1] * (period-1) + gains[i-1]) / period
        avg_loss[i] = (avg_loss[i-1] * (period-1) + losses[i-1]) / period
    rs = np.where(avg_loss > 0, avg_gain / avg_loss, 100.0)
    rsi = 100 - 100 / (1 + rs)
    return rsi

def compute_ema(closes: np.ndarray, period: int) -> np.ndarray:
    alpha = 2.0 / (period + 1)
    ema = np.zeros(len(closes))
    ema[0] = closes[0]
    for i in range(1, len(closes)):
        ema[i] = closes[i] * alpha + ema[i-1] * (1 - alpha)
    return ema

def compute_atr(candles: List[Candle], period: int = 14) -> np.ndarray:
    trs = []
    for i in range(1, len(candles)):
        hl = candles[i].h - candles[i].l
        hc = abs(candles[i].h - candles[i-1].c)
        lc = abs(candles[i].l - candles[i-1].c)
        trs.append(max(hl, hc, lc))
    trs = [trs[0]] + trs
    atr = np.zeros(len(candles))
    atr[period] = np.mean(trs[:period])
    for i in range(period+1, len(candles)):
        atr[i] = (atr[i-1] * (period-1) + trs[i]) / period
    return atr

# ─────────────────────────────────────────
# 核心：清算集群代理计算
# 无上帝视角 — 只用历史已知数据
# ─────────────────────────────────────────
def estimate_liq_cluster_target(
    candles: List[Candle],
    current_idx: int,
    direction: str,
    current_price: float,
    lookback: int = 100,
    levers: List[int] = [3, 5, 10, 20, 50]
) -> Tuple[float, str, float]:
    """
    在信号产生时（current_idx收盘后），估算方向上的清算集群位置。
    
    方法A: Volume Profile峰值（近lookback根K线）
    方法B: 近期波段高低点 × 各杠杆清算价位矩阵 → 密度最高区域
    
    返回: (target_price, source, distance_pct)
    """
    start = max(0, current_idx - lookback)
    hist = candles[start:current_idx]
    
    if len(hist) < 20:
        # 数据不足 → 回退到固定3%
        return current_price * (1.03 if direction == 'long' else 0.97), 'fallback', 0.03

    closes = np.array([c.c for c in hist])
    highs  = np.array([c.h for c in hist])
    lows   = np.array([c.l for c in hist])
    vols   = np.array([c.v for c in hist])

    # ── 方法A: Volume Profile ────────────────
    # 以当前价格为中心，划分±15%的价格区间，统计每个桶的成交量
    price_min = current_price * 0.85
    price_max = current_price * 1.15
    n_buckets = 60
    bucket_size = (price_max - price_min) / n_buckets
    vol_buckets = np.zeros(n_buckets)

    for c in hist:
        mid = (c.h + c.l) / 2
        b = int((mid - price_min) / bucket_size)
        if 0 <= b < n_buckets:
            vol_buckets[b] += c.v

    # 找方向上的成交量峰值
    center_bucket = int((current_price - price_min) / bucket_size)
    
    if direction == 'long':
        # 上方清算集群（空头被轧目标）
        search_range = range(min(center_bucket + 1, n_buckets-1), n_buckets)
    else:
        # 下方清算集群（多头被洗目标）
        search_range = range(center_bucket - 1, -1, -1)

    vol_a_price = None
    best_vol = 0
    for b in search_range:
        if vol_buckets[b] > best_vol:
            best_vol = vol_buckets[b]
            vol_a_price = price_min + (b + 0.5) * bucket_size

    # ── 方法B: 杠杆清算矩阵 ────────────────────
    # 取近期最显著的波段高低点
    # 近lookback根K线的最高/最低点作为"大量开仓区域"代理
    swing_high = np.max(highs[-30:]) if len(highs) >= 30 else np.max(highs)
    swing_low  = np.min(lows[-30:]) if len(lows) >= 30 else np.min(lows)
    
    # 从这些开仓区域出发，计算各杠杆的清算价格
    liq_prices = []
    for lev in levers:
        if direction == 'long':
            # 空头从swing_high开空 → 清算价 = swing_high * (1 + 1/lev)
            liq = swing_high * (1 + 1.0 / lev)
            if liq > current_price:
                liq_prices.append(liq)
        else:
            # 多头从swing_low做多 → 清算价 = swing_low * (1 - 1/lev)
            liq = swing_low * (1 - 1.0 / lev)
            if liq < current_price:
                liq_prices.append(liq)
    
    # 找距离当前价格最近的清算价位集群
    vol_b_price = None
    if liq_prices:
        if direction == 'long':
            vol_b_price = min(liq_prices)  # 最近的上方清算
        else:
            vol_b_price = max(liq_prices)  # 最近的下方清算

    # ── 综合 ─────────────────────────────────
    if vol_a_price and vol_b_price:
        # 取平均，但要确保方向正确
        combined = (vol_a_price + vol_b_price) / 2
        if direction == 'long' and combined > current_price:
            target = combined
            source = 'combined'
        elif direction == 'short' and combined < current_price:
            target = combined
            source = 'combined'
        else:
            target = vol_b_price  # 退回方法B
            source = 'leverage_matrix'
    elif vol_a_price and ((direction == 'long' and vol_a_price > current_price) or 
                           (direction == 'short' and vol_a_price < current_price)):
        target = vol_a_price
        source = 'vol_profile'
    elif vol_b_price:
        target = vol_b_price
        source = 'leverage_matrix'
    else:
        # 最终回退
        target = current_price * (1.03 if direction == 'long' else 0.97)
        source = 'fallback'

    dist_pct = abs(target - current_price) / current_price
    return target, source, dist_pct

# ─────────────────────────────────────────
# 信号引擎：RSI + 结构（无上帝视角）
# ─────────────────────────────────────────
def generate_signals(candles: List[Candle], rsi_period: int = 14) -> List[Dict]:
    """
    生成交易信号（无上帝视角，信号在i根K线收盘后产生，入场在i+1开盘价）
    宽松三选一条件：确保有足够样本
    """
    closes = np.array([c.c for c in candles])
    highs  = np.array([c.h for c in candles])
    lows   = np.array([c.l for c in candles])

    rsi   = compute_rsi(closes, rsi_period)
    ema50 = compute_ema(closes, 50)
    atr   = compute_atr(candles, 14)

    signals = []

    for i in range(60, len(candles) - 1):
        c       = candles[i]
        rsi_val = rsi[i]
        atr_val = atr[i]
        if atr_val <= 0:
            continue

        recent_high_20 = float(np.max(highs[i-20:i]))
        recent_low_20  = float(np.min(lows[i-20:i]))
        recent_high_5  = float(np.max(highs[i-5:i]))
        recent_low_5   = float(np.min(lows[i-5:i]))

        bullish_bar = c.c > c.o
        bearish_bar = c.c < c.o
        prev_close  = candles[i-1].c

        # 做多条件（三选一）
        sig_long_A = (rsi_val < 40 and bullish_bar and c.c > prev_close)
        sig_long_B = (rsi_val > 50 and c.c > recent_high_5 * 1.001)
        sig_long_C = (c.c > recent_high_20 * 1.002 and rsi_val < 75)

        # 做空条件（三选一）
        sig_short_A = (rsi_val > 60 and bearish_bar and c.c < prev_close)
        sig_short_B = (rsi_val < 50 and c.c < recent_low_5 * 0.999)
        sig_short_C = (c.c < recent_low_20 * 0.998 and rsi_val > 25)

        if sig_long_A or sig_long_B or sig_long_C:
            entry_price = candles[i+1].o
            sl = entry_price - atr_val * 1.5
            regime = 'long_A' if sig_long_A else ('long_B' if sig_long_B else 'long_C')
            signals.append({'idx': i+1, 'ts': candles[i+1].ts, 'direction': 'long',
                             'entry': entry_price, 'sl': sl,
                             'rsi': rsi_val, 'atr': atr_val, 'regime_proxy': regime})
        elif sig_short_A or sig_short_B or sig_short_C:
            entry_price = candles[i+1].o
            sl = entry_price + atr_val * 1.5
            regime = 'short_A' if sig_short_A else ('short_B' if sig_short_B else 'short_C')
            signals.append({'idx': i+1, 'ts': candles[i+1].ts, 'direction': 'short',
                             'entry': entry_price, 'sl': sl,
                             'rsi': rsi_val, 'atr': atr_val, 'regime_proxy': regime})

    return signals

def run_backtest(sym: str, tf: str, 
                 tp_fixed_pct: float = 0.02,
                 sl_pct_override: float = None,
                 max_hold_bars: int = 48) -> BacktestResult:
    """
    双TP对比回测：
    - TP_A: 固定%（基准）
    - TP_B: 清算集群代理（被测假说）
    """
    print(f"\n{'='*60}")
    print(f"  {sym} {tf} | TP固定={tp_fixed_pct*100:.1f}% vs 清算集群TP")
    print(f"{'='*60}")
    
    candles = load_klines(sym, tf)
    signals = generate_signals(candles)
    
    print(f"  K线总数: {len(candles)} | 信号数: {len(signals)}")
    
    result = BacktestResult(symbol=sym, timeframe=tf)
    
    fixed_pnls = []
    liq_pnls   = []
    
    for sig in signals:
        idx = sig['idx']
        if idx >= len(candles) - 1:
            continue
        
        entry = sig['entry']
        sl    = sig['sl']
        dirn  = sig['direction']
        
        # 固定TP
        if dirn == 'long':
            tp_fixed_price = entry * (1 + tp_fixed_pct)
        else:
            tp_fixed_price = entry * (1 - tp_fixed_pct)
        
        # 清算集群TP（仅用当前idx之前的数据）
        tp_liq_price, liq_source, liq_dist = estimate_liq_cluster_target(
            candles, idx, dirn, entry
        )
        
        # 确保SL有效
        sl_dist = abs(entry - sl)
        if sl_dist < entry * 0.003:
            sl = entry * (0.98 if dirn == 'long' else 1.02)
            sl_dist = abs(entry - sl)
        
        result.total_trades += 1
        
        # 模拟持仓（逐bar forward simulation）
        fixed_exit = None
        liq_exit   = None
        
        for j in range(idx + 1, min(idx + max_hold_bars + 1, len(candles))):
            bar = candles[j]
            
            # 固定TP
            if fixed_exit is None:
                if dirn == 'long':
                    if bar.l <= sl:
                        fixed_exit = (bar.ts, min(bar.o, sl), 'sl')
                    elif bar.h >= tp_fixed_price:
                        fixed_exit = (bar.ts, min(bar.h, tp_fixed_price), 'tp_fixed')
                else:
                    if bar.h >= sl:
                        fixed_exit = (bar.ts, max(bar.o, sl), 'sl')
                    elif bar.l <= tp_fixed_price:
                        fixed_exit = (bar.ts, max(bar.l, tp_fixed_price), 'tp_fixed')
            
            # 清算集群TP
            if liq_exit is None:
                if dirn == 'long':
                    if bar.l <= sl:
                        liq_exit = (bar.ts, min(bar.o, sl), 'sl')
                    elif bar.h >= tp_liq_price:
                        liq_exit = (bar.ts, min(bar.h, tp_liq_price), 'tp_liq')
                else:
                    if bar.h >= sl:
                        liq_exit = (bar.ts, max(bar.o, sl), 'sl')
                    elif bar.l <= tp_liq_price:
                        liq_exit = (bar.ts, max(bar.l, tp_liq_price), 'tp_liq')
            
            if fixed_exit and liq_exit:
                break
        
        # 超时出场
        if fixed_exit is None:
            last = candles[min(idx + max_hold_bars, len(candles)-1)]
            fixed_exit = (last.ts, last.c, 'timeout')
        if liq_exit is None:
            last = candles[min(idx + max_hold_bars, len(candles)-1)]
            liq_exit = (last.ts, last.c, 'timeout')
        
        # 计算PnL
        def calc_pnl(ep, xp, d):
            if d == 'long':
                return (xp - ep) / ep
            else:
                return (ep - xp) / ep
        
        fixed_pnl = calc_pnl(entry, fixed_exit[1], dirn)
        liq_pnl   = calc_pnl(entry, liq_exit[1], dirn)
        
        fixed_pnls.append(fixed_pnl)
        liq_pnls.append(liq_pnl)
        
        # 记录固定TP结果
        if fixed_pnl > 0:
            result.fixed_tp_wins += 1
        else:
            result.fixed_tp_losses += 1
        
        # 记录清算集群TP结果
        if liq_pnl > 0:
            result.liq_tp_wins += 1
        else:
            result.liq_tp_losses += 1
        
        # 记录清算集群命中
        if liq_exit[2] == 'tp_liq':
            result.liq_hit_rate += 1
        
        result.liq_avg_dist_pct += liq_dist
        
        result.trades.append({
            'ts': sig['ts'],
            'direction': dirn,
            'entry': round(entry, 2),
            'sl': round(sl, 2),
            'tp_fixed': round(tp_fixed_price, 2),
            'tp_liq': round(tp_liq_price, 2),
            'liq_source': liq_source,
            'liq_dist_pct': round(liq_dist * 100, 2),
            'fixed_exit_reason': fixed_exit[2],
            'liq_exit_reason': liq_exit[2],
            'fixed_pnl_pct': round(fixed_pnl * 100, 3),
            'liq_pnl_pct': round(liq_pnl * 100, 3),
        })
    
    # 汇总
    n = result.total_trades
    if n == 0:
        return result
    
    fixed_pnls_np = np.array(fixed_pnls)
    liq_pnls_np   = np.array(liq_pnls)
    
    result.fixed_tp_ev = float(np.mean(fixed_pnls_np) * 100)
    result.liq_tp_ev   = float(np.mean(liq_pnls_np) * 100)
    result.liq_vs_fixed_improvement = result.liq_tp_ev - result.fixed_tp_ev
    result.liq_hit_rate = result.liq_hit_rate / n * 100
    result.liq_avg_dist_pct = result.liq_avg_dist_pct / n * 100
    
    # 打印摘要
    print(f"\n  📊 总交易数: {n}")
    print(f"\n  【基准：固定{tp_fixed_pct*100:.0f}%TP】")
    print(f"  胜率: {result.fixed_tp_wins/n*100:.1f}% ({result.fixed_tp_wins}W/{result.fixed_tp_losses}L)")
    print(f"  期望值(EV): {result.fixed_tp_ev:+.3f}%/笔")
    print(f"\n  【清算集群TP】")
    print(f"  胜率: {result.liq_tp_wins/n*100:.1f}% ({result.liq_tp_wins}W/{result.liq_tp_losses}L)")
    print(f"  期望值(EV): {result.liq_tp_ev:+.3f}%/笔")
    print(f"  清算集群命中率: {result.liq_hit_rate:.1f}%")
    print(f"  平均清算集群距离: {result.liq_avg_dist_pct:.2f}%")
    print(f"\n  📈 清算集群TP vs 固定TP改进: {result.liq_vs_fixed_improvement:+.3f}%/笔")
    
    return result

# ─────────────────────────────────────────
# 方向细分分析
# ─────────────────────────────────────────
def analyze_by_direction(result: BacktestResult) -> Dict:
    longs = [t for t in result.trades if t['direction'] == 'long']
    shorts = [t for t in result.trades if t['direction'] == 'short']
    
    def stats(trades, tp_key='liq_pnl_pct'):
        if not trades:
            return {'n': 0, 'wr': 0, 'ev': 0, 'hit_rate': 0}
        pnls = [t[tp_key] for t in trades]
        wins = sum(1 for p in pnls if p > 0)
        return {
            'n': len(trades),
            'wr': round(wins / len(trades) * 100, 1),
            'ev': round(np.mean(pnls), 3),
            'hit_rate': round(sum(1 for t in trades if t['liq_exit_reason'] == 'tp_liq') / len(trades) * 100, 1)
        }
    
    return {
        'long_fixed': stats(longs, 'fixed_pnl_pct'),
        'long_liq': stats(longs, 'liq_pnl_pct'),
        'short_fixed': stats(shorts, 'fixed_pnl_pct'),
        'short_liq': stats(shorts, 'liq_pnl_pct'),
    }

# ─────────────────────────────────────────
# 主程序
# ─────────────────────────────────────────
def main():
    print("\n" + "="*70)
    print("  达摩院 × 梵天 · 清算集群作为TP目标 Alpha验证回测")
    print("  数据: BTC/ETH 2020-01-01~2026-07-10 | 无上帝视角")
    print("="*70)
    
    test_configs = [
        ('BTCUSDT', '1h'),
        ('BTCUSDT', '4h'),
        ('ETHUSDT', '1h'),
        ('ETHUSDT', '4h'),
    ]
    
    all_results = {}
    
    for sym, tf in test_configs:
        r = run_backtest(sym, tf, tp_fixed_pct=0.02, max_hold_bars=48)
        all_results[f'{sym}_{tf}'] = r
        
        # 方向细分
        dir_stats = analyze_by_direction(r)
        print(f"\n  方向细分：")
        print(f"  做多 | 固定TP: WR={dir_stats['long_fixed']['wr']}% EV={dir_stats['long_fixed']['ev']:+.3f}%")
        print(f"  做多 | 清算TP: WR={dir_stats['long_liq']['wr']}% EV={dir_stats['long_liq']['ev']:+.3f}% Hit={dir_stats['long_liq']['hit_rate']}%")
        print(f"  做空 | 固定TP: WR={dir_stats['short_fixed']['wr']}% EV={dir_stats['short_fixed']['ev']:+.3f}%")
        print(f"  做空 | 清算TP: WR={dir_stats['short_liq']['wr']}% EV={dir_stats['short_liq']['ev']:+.3f}% Hit={dir_stats['short_liq']['hit_rate']}%")
    
    # ── 最终汇总报告 ─────────────────────────
    print("\n\n" + "="*70)
    print("  📋 总评：清算集群TP作为Alpha来源 — 综合评估")
    print("="*70)
    print(f"\n  {'品种+周期':<20} {'固定TP EV':>12} {'清算TP EV':>12} {'改进':>10} {'命中率':>10}")
    print(f"  {'-'*65}")
    
    total_improvement = 0
    count = 0
    for key, r in all_results.items():
        if r.total_trades > 0:
            imp = r.liq_vs_fixed_improvement
            total_improvement += imp
            count += 1
            sign = '✅' if imp > 0 else '❌'
            print(f"  {key:<20} {r.fixed_tp_ev:>+12.3f}% {r.liq_tp_ev:>+12.3f}% {imp:>+10.3f}% {r.liq_hit_rate:>9.1f}% {sign}")
    
    avg_improvement = total_improvement / count if count > 0 else 0
    
    print(f"\n  {'平均改进':>45} {avg_improvement:>+10.3f}%")
    
    print("\n  ─────────────────────────────────────")
    if avg_improvement > 0.1:
        verdict = "✅ 清算集群TP确实是Alpha来源 — 平均EV改进正向显著"
    elif avg_improvement > 0:
        verdict = "⚠️ 清算集群TP有边际改进，但不显著"
    else:
        verdict = "❌ 清算集群TP代理方法无效 — 需要真实订单簿数据"
    
    print(f"  {verdict}")
    print(f"\n  🔬 关键发现：")
    print(f"  - 清算集群TP距离代理方法使用 Vol Profile + 杠杆矩阵")
    print(f"  - 仅用历史K线数据，无实时订单簿（主要限制）")
    print(f"  - 若使用真实清算热力图数据，预期改进幅度更大")
    
    # 保存结果
    out_file = BASE / 'data' / 'backtest' / 'liq_cluster_tp_backtest.json'
    summary = {
        'meta': {
            'description': '清算集群作为TP目标 Alpha验证',
            'data_range': '2020-01-01 ~ 2026-07-10',
            'method': 'Vol Profile + 杠杆矩阵代理清算集群，无上帝视角'
        },
        'results': {
            k: {
                'total_trades': v.total_trades,
                'fixed_tp_wr': round(v.fixed_tp_wins / v.total_trades * 100, 1) if v.total_trades else 0,
                'fixed_tp_ev': round(v.fixed_tp_ev, 3),
                'liq_tp_wr': round(v.liq_tp_wins / v.total_trades * 100, 1) if v.total_trades else 0,
                'liq_tp_ev': round(v.liq_tp_ev, 3),
                'liq_hit_rate': round(v.liq_hit_rate, 1),
                'liq_avg_dist_pct': round(v.liq_avg_dist_pct, 2),
                'improvement': round(v.liq_vs_fixed_improvement, 3),
            }
            for k, v in all_results.items()
        },
        'verdict': verdict,
        'avg_improvement': round(avg_improvement, 3)
    }
    json.dump(summary, open(out_file, 'w'), indent=2, ensure_ascii=False)
    print(f"\n  💾 结果已保存: {out_file}")
    print("="*70)

if __name__ == '__main__':
    main()
