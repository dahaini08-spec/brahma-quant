#!/usr/bin/env python3
"""
梵天系统设计院 + 达摩院大模型深度研究系统
苏摩111最高研究指令
严格实盘条件复刻，禁止上帝视角
"""

import requests
import numpy as np
import json
import time
import math
from datetime import datetime, timezone
from scipy import stats as scipy_stats

# ============================================================
# 工具函数
# ============================================================

def get_klines_all(symbol, interval, start_ts, end_ts):
    """分批拉取完整历史数据，避免API限制"""
    all_klines = []
    limit = 1000
    current = start_ts
    retry_count = 0
    while current < end_ts:
        url = 'https://fapi.binance.com/fapi/v1/klines'
        params = {
            'symbol': symbol,
            'interval': interval,
            'startTime': current,
            'limit': limit
        }
        try:
            r = requests.get(url, params=params, timeout=30).json()
            if not isinstance(r, list) or not r:
                break
            all_klines.extend(r)
            current = r[-1][0] + 1
            if len(r) < limit:
                break
            time.sleep(0.1)  # 避免限速
            retry_count = 0
        except Exception as e:
            retry_count += 1
            if retry_count > 3:
                print(f"  ERROR: {symbol} {interval} at {current}: {e}")
                break
            time.sleep(2)
    return all_klines


def calc_ema(prices, period):
    """计算EMA，返回最后一个值"""
    if len(prices) < period:
        return prices[-1]
    k = 2.0 / (period + 1)
    ema = prices[0]
    for p in prices[1:]:
        ema = p * k + ema * (1 - k)
    return ema


def calc_ema_series(prices, period):
    """计算EMA序列"""
    if len(prices) < 2:
        return np.array(prices)
    k = 2.0 / (period + 1)
    ema = [prices[0]]
    for p in prices[1:]:
        ema.append(p * k + ema[-1] * (1 - k))
    return np.array(ema)


def calc_rsi(prices, period=14):
    """计算RSI"""
    if len(prices) < period + 1:
        return 50.0
    diffs = np.diff(prices[-(period+5):])
    gains = np.where(diffs > 0, diffs, 0.0)
    losses = np.where(diffs < 0, -diffs, 0.0)
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def extract_ohlcv(klines):
    opens  = np.array([float(k[1]) for k in klines])
    highs  = np.array([float(k[2]) for k in klines])
    lows   = np.array([float(k[3]) for k in klines])
    closes = np.array([float(k[4]) for k in klines])
    vols   = np.array([float(k[5]) for k in klines])
    times  = np.array([k[0] for k in klines])
    return opens, highs, lows, closes, vols, times


# ============================================================
# 任务1：拉取数据
# ============================================================

START = int(datetime(2019, 11, 1, tzinfo=timezone.utc).timestamp() * 1000)
END   = int(datetime(2024, 12, 31, tzinfo=timezone.utc).timestamp() * 1000)

SYMBOLS = ['BTCUSDT', 'ETHUSDT']
TIMEFRAMES = ['15m', '1h', '4h']

print("=" * 70)
print("梵天系统设计院 + 达摩院大模型深度研究系统")
print("苏摩111最高研究指令 · 实盘条件复刻")
print("=" * 70)
print(f"\n任务1：拉取历史K线数据")
print(f"时间范围：2019-11-01 → 2024-12-31")
print("-" * 50)

data_store = {}
for sym in SYMBOLS:
    data_store[sym] = {}
    for tf in TIMEFRAMES:
        print(f"  拉取 {sym} {tf} ...", end='', flush=True)
        ks = get_klines_all(sym, tf, START, END)
        data_store[sym][tf] = ks
        print(f" {len(ks)} 根K线")

print("\n数据拉取完成。\n")

# ============================================================
# 任务2：体制判断系统
# ============================================================

def detect_regime_realtime(closes_4h, idx):
    """
    模拟实盘：只使用 closes_4h[:idx+1] 的已知数据
    严禁使用 idx 之后的数据
    """
    if idx < 200:
        return 'UNKNOWN'
    hist_closes = closes_4h[:idx+1]
    ema200 = calc_ema(hist_closes, 200)
    price = hist_closes[-1]
    rsi_4h = calc_rsi(hist_closes, 14)
    
    if price > ema200 * 1.02:
        if rsi_4h > 55:
            return 'BULL_TREND'
        else:
            return 'CHOP_MID'
    elif price < ema200 * 0.98:
        if rsi_4h < 45:
            return 'BEAR_TREND'
        else:
            return 'BEAR_RECOVERY'
    else:
        return 'CHOP_MID'


print("=" * 70)
print("任务2：体制判断系统 · 逐K线标注")
print("=" * 70)

regime_data = {}
for sym in SYMBOLS:
    print(f"\n--- {sym} 体制分析 ---")
    ks_4h = data_store[sym]['4h']
    _, _, _, closes_4h, _, times_4h = extract_ohlcv(ks_4h)
    
    regimes = []
    for i in range(len(closes_4h)):
        r = detect_regime_realtime(closes_4h, i)
        regimes.append(r)
    
    regime_data[sym] = regimes
    
    # 统计各体制频率
    from collections import Counter, defaultdict
    counts = Counter(regimes)
    total = len(regimes)
    
    print(f"\n  体制分布（总共 {total} 根4H K线）：")
    regime_order = ['BULL_TREND', 'BEAR_TREND', 'CHOP_MID', 'BEAR_RECOVERY', 'UNKNOWN']
    for r in regime_order:
        cnt = counts.get(r, 0)
        pct = cnt / total * 100 if total > 0 else 0
        print(f"    {r:20s}: {cnt:5d} ({pct:5.1f}%)")
    
    # 体制持续时间分布
    print(f"\n  体制持续时间分析（单位：根4H K线，1根≈4小时）：")
    durations = defaultdict(list)
    if regimes:
        cur_regime = regimes[0]
        cur_len = 1
        for i in range(1, len(regimes)):
            if regimes[i] == cur_regime:
                cur_len += 1
            else:
                if cur_regime != 'UNKNOWN':
                    durations[cur_regime].append(cur_len)
                cur_regime = regimes[i]
                cur_len = 1
        if cur_regime != 'UNKNOWN':
            durations[cur_regime].append(cur_len)
    
    for r in ['BULL_TREND', 'BEAR_TREND', 'CHOP_MID', 'BEAR_RECOVERY']:
        if r in durations and durations[r]:
            d = durations[r]
            print(f"    {r:20s}: 中位数={np.median(d):.0f}根 "
                  f"均值={np.mean(d):.1f}根 "
                  f"最长={max(d)}根 "
                  f"最短={min(d)}根")
    
    # 体制转换频率
    switches = sum(1 for i in range(1, len(regimes))
                   if regimes[i] != regimes[i-1] and regimes[i] != 'UNKNOWN' and regimes[i-1] != 'UNKNOWN')
    # 时间跨度（月）
    if len(times_4h) > 0:
        months = (times_4h[-1] - times_4h[0]) / (1000 * 3600 * 24 * 30)
        if months > 0:
            print(f"\n  体制转换总次数: {switches}")
            print(f"  时间跨度: {months:.1f} 个月")
            print(f"  平均每月切换: {switches/months:.2f} 次")


# ============================================================
# 第二部分：三周期独立回测
# ============================================================

print("\n" + "=" * 70)
print("第二部分：三周期独立回测 · 实盘条件")
print("=" * 70)

FEE = 0.0004       # 手续费 per side
SLIP = 0.0005      # 滑点 per side
FUND_RATE = 0.00005  # 资金费率每8小时（0.005%）
RISK_PER_TRADE = 0.01  # 每笔固定风险1%

LONG_SL_PCT  = 0.020
LONG_TP_PCT  = 0.020
SHORT_SL_PCT = 0.025
SHORT_TP_PCT = 0.025

# 4H时间戳→体制映射
regime_map_btc = {}
regime_map_eth = {}
ks_4h_btc = data_store['BTCUSDT']['4h']
ks_4h_eth = data_store['ETHUSDT']['4h']
_, _, _, closes_4h_btc, _, times_4h_btc = extract_ohlcv(ks_4h_btc)
_, _, _, closes_4h_eth, _, times_4h_eth = extract_ohlcv(ks_4h_eth)

regimes_btc = regime_data['BTCUSDT']
regimes_eth = regime_data['ETHUSDT']

# 建立时间戳→体制的映射（每根4H K线的收盘时间）
# 4H K线的收盘时间 = 开盘时间 + 4*3600*1000 - 1
for i, k in enumerate(ks_4h_btc):
    open_ts = k[0]
    close_ts = open_ts + 4 * 3600 * 1000 - 1
    regime_map_btc[close_ts] = regimes_btc[i]

for i, k in enumerate(ks_4h_eth):
    open_ts = k[0]
    close_ts = open_ts + 4 * 3600 * 1000 - 1
    regime_map_eth[close_ts] = regimes_eth[i]


def get_regime_at_time(ts, regime_map):
    """获取某时刻之前最近一根已收盘4H K线的体制"""
    best_ts = None
    for k_ts in regime_map:
        if k_ts <= ts:
            if best_ts is None or k_ts > best_ts:
                best_ts = k_ts
    if best_ts is None:
        return 'UNKNOWN'
    return regime_map[best_ts]


def get_regime_at_time_fast(ts, sorted_regime_list):
    """二分查找：获取ts时刻之前最近一根已收盘4H K线的体制"""
    # sorted_regime_list: [(close_ts, regime), ...]
    lo, hi = 0, len(sorted_regime_list) - 1
    res = None
    while lo <= hi:
        mid = (lo + hi) // 2
        if sorted_regime_list[mid][0] <= ts:
            res = sorted_regime_list[mid][1]
            lo = mid + 1
        else:
            hi = mid - 1
    return res if res is not None else 'UNKNOWN'


# 构建排序后的体制列表（用于快速查找）
def build_sorted_regime(ks_4h, regimes):
    lst = []
    for i, k in enumerate(ks_4h):
        open_ts = k[0]
        close_ts = open_ts + 4 * 3600 * 1000 - 1
        lst.append((close_ts, regimes[i]))
    lst.sort()
    return lst

sorted_regime_btc = build_sorted_regime(ks_4h_btc, regimes_btc)
sorted_regime_eth = build_sorted_regime(ks_4h_eth, regimes_eth)


def run_backtest(sym, tf, sorted_regime_list, verbose=True):
    """
    实盘条件回测
    返回统计结果字典
    """
    ks = data_store[sym][tf]
    if not ks:
        return None
    opens, highs, lows, closes, vols, times = extract_ohlcv(ks)
    n = len(closes)
    
    # 从1H数据中提取用于RSI/EMA计算的数据
    # 如果当前是15m，用15m计算RSI/EMA；如果是4H，用4H自身
    # 对于1H和15m信号，用当前周期的RSI/EMA
    
    trades = []
    in_position = False
    position_side = None
    entry_price = 0.0
    entry_time = 0
    last_signal_time = {'+1': 0, '-1': 0}
    account = 1.0  # 归一化账户净值
    equity_curve = [1.0]
    
    MIN_SIGNAL_GAP = 4 * 3600 * 1000  # 4小时间隔
    
    ema20_series = calc_ema_series(closes, 20)
    
    for i in range(50, n):
        cur_ts = times[i]
        cur_close = closes[i]
        
        # 出场检查（持仓中）
        if in_position:
            # 计算持仓时间（8H资金费率周期数）
            hours_held = (cur_ts - entry_time) / (3600 * 1000)
            fund_periods = hours_held / 8.0
            fund_cost = fund_periods * FUND_RATE
            
            if position_side == 'LONG':
                sl_price = entry_price * (1 - LONG_SL_PCT)
                tp_price = entry_price * (1 + LONG_TP_PCT)
                # 检查止损止盈
                exit_price = None
                exit_reason = None
                if lows[i] <= sl_price:
                    exit_price = sl_price * (1 - SLIP)
                    exit_reason = 'SL'
                elif highs[i] >= tp_price:
                    exit_price = tp_price * (1 + SLIP) if lows[i] > sl_price else sl_price * (1 - SLIP)
                    # 优先SL
                    if lows[i] <= sl_price:
                        exit_price = sl_price * (1 - SLIP)
                        exit_reason = 'SL'
                    else:
                        exit_price = tp_price * (1 - SLIP)  # 做多出场用卖价，略低
                        exit_reason = 'TP'
                
                if exit_price is not None:
                    pnl_pct = (exit_price / entry_price - 1) - 2 * FEE - 2 * SLIP - fund_cost
                    risk_size = RISK_PER_TRADE / LONG_SL_PCT
                    pnl_account = pnl_pct * risk_size
                    account *= (1 + pnl_account)
                    trades.append({
                        'side': 'LONG', 'entry': entry_price, 'exit': exit_price,
                        'pnl_pct': pnl_pct, 'pnl_account': pnl_account,
                        'reason': exit_reason, 'ts': entry_time,
                        'fund_cost': fund_cost
                    })
                    in_position = False
                    equity_curve.append(account)
                    
            elif position_side == 'SHORT':
                sl_price = entry_price * (1 + SHORT_SL_PCT)
                tp_price = entry_price * (1 - SHORT_TP_PCT)
                exit_price = None
                exit_reason = None
                if highs[i] >= sl_price:
                    exit_price = sl_price * (1 + SLIP)
                    exit_reason = 'SL'
                elif lows[i] <= tp_price:
                    exit_price = tp_price * (1 + SLIP)  # 做空出场买回
                    exit_reason = 'TP'
                
                if exit_price is not None:
                    pnl_pct = (entry_price / exit_price - 1) - 2 * FEE - 2 * SLIP - fund_cost
                    risk_size = RISK_PER_TRADE / SHORT_SL_PCT
                    pnl_account = pnl_pct * risk_size
                    account *= (1 + pnl_account)
                    trades.append({
                        'side': 'SHORT', 'entry': entry_price, 'exit': exit_price,
                        'pnl_pct': pnl_pct, 'pnl_account': pnl_account,
                        'reason': exit_reason, 'ts': entry_time,
                        'fund_cost': fund_cost
                    })
                    in_position = False
                    equity_curve.append(account)
        
        # 入场信号（不持仓）
        if not in_position:
            # 获取4H体制（使用当前K线收盘时间之前已确认的4H体制）
            regime = get_regime_at_time_fast(cur_ts, sorted_regime_list)
            
            # 计算当前周期RSI(14) 和 EMA20
            hist_closes = closes[:i+1]
            rsi = calc_rsi(hist_closes, 14)
            ema20 = ema20_series[i]
            
            # 做多信号
            long_signal = (
                regime == 'BULL_TREND' and
                45 <= rsi <= 70 and
                cur_close > ema20 and
                (cur_ts - last_signal_time['+1']) >= MIN_SIGNAL_GAP
            )
            
            # 做空信号
            short_signal = (
                regime == 'BEAR_TREND' and
                rsi > 60 and
                cur_close < ema20 and
                (cur_ts - last_signal_time['-1']) >= MIN_SIGNAL_GAP
            )
            
            if long_signal:
                entry_price = cur_close * (1 + SLIP)
                entry_time = cur_ts
                position_side = 'LONG'
                in_position = True
                last_signal_time['+1'] = cur_ts
                
            elif short_signal:
                entry_price = cur_close * (1 - SLIP)
                entry_time = cur_ts
                position_side = 'SHORT'
                in_position = True
                last_signal_time['-1'] = cur_ts
    
    # 强制平仓未完成持仓（不计入最终统计）
    
    # 计算统计指标
    if not trades:
        return {'trades': 0, 'win_rate': 0, 'net_pnl': 0, 'max_dd': 0, 'sharpe': 0, 'calmar': 0}
    
    wins = [t for t in trades if t['pnl_pct'] > 0]
    losses = [t for t in trades if t['pnl_pct'] <= 0]
    
    win_rate = len(wins) / len(trades) * 100
    avg_win = np.mean([t['pnl_pct'] for t in wins]) * 100 if wins else 0
    avg_loss = np.mean([t['pnl_pct'] for t in losses]) * 100 if losses else 0
    profit_factor = (sum(t['pnl_pct'] for t in wins) / 
                     abs(sum(t['pnl_pct'] for t in losses))) if losses else 999
    
    net_pnl = (account - 1.0) * 100
    
    # 最大回撤
    peak = 1.0
    max_dd = 0.0
    for eq in equity_curve:
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak * 100
        if dd > max_dd:
            max_dd = dd
    
    # 夏普比率（简化：用每笔收益率序列）
    pnl_series = [t['pnl_account'] for t in trades]
    if len(pnl_series) > 1 and np.std(pnl_series) > 0:
        sharpe = np.mean(pnl_series) / np.std(pnl_series) * np.sqrt(252)
    else:
        sharpe = 0
    
    calmar = net_pnl / max_dd if max_dd > 0 else 0
    
    return {
        'trades': len(trades),
        'win_rate': win_rate,
        'avg_win_pct': avg_win,
        'avg_loss_pct': avg_loss,
        'profit_factor': profit_factor,
        'net_pnl': net_pnl,
        'max_dd': max_dd,
        'sharpe': sharpe,
        'calmar': calmar,
        'wins': len(wins),
        'losses': len(losses),
        'trades_list': trades,
        'equity_curve': equity_curve
    }


backtest_results = {}
for sym in SYMBOLS:
    backtest_results[sym] = {}
    sorted_regime = sorted_regime_btc if sym == 'BTCUSDT' else sorted_regime_eth
    print(f"\n--- {sym} 三周期回测 ---")
    print(f"{'周期':6s} | {'总交易数':8s} | {'胜率':8s} | {'平均盈利':10s} | {'平均亏损':10s} | {'盈亏比':8s} | {'净收益':10s} | {'最大回撤':8s} | {'夏普':8s} | {'Calmar':8s}")
    print("-" * 100)
    
    for tf in TIMEFRAMES:
        res = run_backtest(sym, tf, sorted_regime)
        backtest_results[sym][tf] = res
        if res and res['trades'] > 0:
            print(f"  {tf:5s} | {res['trades']:8d} | {res['win_rate']:7.1f}% | "
                  f"{res['avg_win_pct']:+9.2f}% | {res['avg_loss_pct']:+9.2f}% | "
                  f"{res['profit_factor']:8.2f} | {res['net_pnl']:+9.1f}% | "
                  f"{res['max_dd']:7.1f}% | {res['sharpe']:8.2f} | {res['calmar']:8.2f}")
        else:
            print(f"  {tf:5s} | 无有效交易")


# ============================================================
# 第三部分：宪法条款验证矩阵
# ============================================================

print("\n" + "=" * 70)
print("第三部分：宪法条款验证矩阵")
print("=" * 70)

def verify_clause(sym, tf, condition_fn, sorted_regime, claim_wr=None):
    """
    验证特定条件下的胜率
    condition_fn: (regime, rsi, price, ema20) -> 'LONG' | 'SHORT' | None
    """
    ks = data_store[sym][tf]
    if not ks:
        return None
    opens, highs, lows, closes, vols, times = extract_ohlcv(ks)
    n = len(closes)
    
    outcomes = []
    ema20_series = calc_ema_series(closes, 20)
    last_signal_ts = 0
    MIN_GAP = 4 * 3600 * 1000
    
    for i in range(50, n - 1):  # -1 for next candle outcome
        cur_ts = times[i]
        regime = get_regime_at_time_fast(cur_ts, sorted_regime)
        hist_closes = closes[:i+1]
        rsi = calc_rsi(hist_closes, 14)
        ema20 = ema20_series[i]
        price = closes[i]
        
        if (cur_ts - last_signal_ts) < MIN_GAP:
            continue
        
        side = condition_fn(regime, rsi, price, ema20)
        if side is None:
            continue
        
        last_signal_ts = cur_ts
        
        # 出场：下根K线内判断（简化：用下根收盘价和OHLC判断TP/SL）
        ep = price * (1 + SLIP) if side == 'LONG' else price * (1 - SLIP)
        
        if side == 'LONG':
            sl = ep * (1 - LONG_SL_PCT)
            tp = ep * (1 + LONG_TP_PCT)
            # 检查后续若干K线内是否触发（最多20根K线）
            result = None
            for j in range(i + 1, min(i + 21, n)):
                if lows[j] <= sl:
                    result = 'LOSS'
                    break
                elif highs[j] >= tp:
                    result = 'WIN'
                    break
            if result is None:
                result = 'WIN' if closes[min(i+5, n-1)] > ep else 'LOSS'
        else:  # SHORT
            sl = ep * (1 + SHORT_SL_PCT)
            tp = ep * (1 - SHORT_TP_PCT)
            result = None
            for j in range(i + 1, min(i + 21, n)):
                if highs[j] >= sl:
                    result = 'LOSS'
                    break
                elif lows[j] <= tp:
                    result = 'WIN'
                    break
            if result is None:
                result = 'WIN' if closes[min(i+5, n-1)] < ep else 'LOSS'
        
        outcomes.append(result)
    
    if len(outcomes) < 5:
        return {'n': len(outcomes), 'wr': 0, 'ci_low': 0, 'ci_high': 0, 'p_val': 1.0, 'significant': False}
    
    n_win = outcomes.count('WIN')
    n_total = len(outcomes)
    wr = n_win / n_total * 100
    
    # 95%置信区间（Wilson interval）
    z = 1.96
    p = n_win / n_total
    denominator = 1 + z**2 / n_total
    centre = (p + z**2 / (2 * n_total)) / denominator
    margin = z * math.sqrt(p * (1-p) / n_total + z**2 / (4 * n_total**2)) / denominator
    ci_low = max(0, (centre - margin) * 100)
    ci_high = min(100, (centre + margin) * 100)
    
    # Z检验：H0: WR=50%（随机）
    p_val_vs_50 = scipy_stats.binomtest(n_win, n_total, 0.5, alternative='greater').pvalue
    
    return {
        'n': n_total, 'wr': wr,
        'ci_low': ci_low, 'ci_high': ci_high,
        'p_val': p_val_vs_50,
        'significant': p_val_vs_50 < 0.05,
        'claim_wr': claim_wr
    }


# 条款1：BEAR_TREND_LONG WR=45%（强制做多验证）
def cond_bear_long(regime, rsi, price, ema20):
    if regime == 'BEAR_TREND':
        return 'LONG'
    return None

# 条款2：BULL_TREND回调做多 WR=71%
def cond_bull_pullback_long(regime, rsi, price, ema20):
    if regime == 'BULL_TREND' and 45 <= rsi <= 65:
        return 'LONG'
    return None

# 条款3：BEAR_TREND RSI>60做空 WR=68.1%
def cond_bear_rsi60_short(regime, rsi, price, ema20):
    if regime == 'BEAR_TREND' and rsi > 60:
        return 'SHORT'
    return None

# 条款5：任意时刻做多（无体制过滤，基准）
def cond_random_long(regime, rsi, price, ema20):
    if regime != 'UNKNOWN':
        return 'LONG'
    return None

clauses = [
    ('条款1', 'BEAR_TREND_LONG WR声称=45%', cond_bear_long, 45.0),
    ('条款2', 'BULL_TREND回调做多 WR声称=71%', cond_bull_pullback_long, 71.0),
    ('条款3', 'BEAR_TREND RSI>60做空 WR声称=68.1%', cond_bear_rsi60_short, 68.1),
    ('基准', '无过滤随机做多', cond_random_long, 50.0),
]

print(f"\n使用 BTCUSDT 1H 数据进行宪法条款验证")
print(f"\n{'条款':8s} | {'声称WR':8s} | {'实测WR':8s} | {'样本量n':8s} | {'95%CI':16s} | {'p值':8s} | {'结论'}")
print("-" * 90)

sorted_regime_btc_ref = sorted_regime_btc

clause_results = {}
for c_id, c_desc, c_fn, c_claim in clauses:
    res = verify_clause('BTCUSDT', '1h', c_fn, sorted_regime_btc_ref, claim_wr=c_claim)
    clause_results[c_id] = res
    if res:
        significant = "✅显著" if res['significant'] else "❌不显著"
        claim_str = f"{c_claim:.1f}%"
        print(f"  {c_id:6s} | {claim_str:8s} | {res['wr']:7.1f}% | "
              f"{res['n']:8d} | [{res['ci_low']:5.1f}%,{res['ci_high']:5.1f}%] | "
              f"{res['p_val']:.4f} | {significant}")
        print(f"         描述: {c_desc}")

# 条款4：BullBonus EMA多头排列
print("\n条款4：EMA多头排列 (EMA9>EMA20>EMA50) 后续涨幅分析")
for sym in ['BTCUSDT']:
    ks_4h = data_store[sym]['4h']
    _, _, _, closes_4h, _, times_4h = extract_ohlcv(ks_4h)
    
    ema9_s  = calc_ema_series(closes_4h, 9)
    ema20_s = calc_ema_series(closes_4h, 20)
    ema50_s = calc_ema_series(closes_4h, 50)
    
    returns_1h = []
    returns_4h = []
    returns_1d = []
    
    for i in range(50, len(closes_4h) - 6):
        if ema9_s[i] > ema20_s[i] > ema50_s[i]:
            if i + 1 < len(closes_4h):
                r1 = (closes_4h[i+1] - closes_4h[i]) / closes_4h[i] * 100
                returns_1h.append(r1)
            if i + 1 < len(closes_4h):
                r4 = (closes_4h[i+1] - closes_4h[i]) / closes_4h[i] * 100
                returns_4h.append(r4)
            if i + 6 < len(closes_4h):
                rd = (closes_4h[i+6] - closes_4h[i]) / closes_4h[i] * 100
                returns_1d.append(rd)
    
    if returns_1h:
        print(f"  {sym} EMA多头排列后1根4H K线涨幅：均值={np.mean(returns_1h):+.3f}% "
              f"中位数={np.median(returns_1h):+.3f}% "
              f"上涨概率={sum(1 for r in returns_1h if r>0)/len(returns_1h)*100:.1f}% "
              f"(n={len(returns_1h)})")
    if returns_1d:
        print(f"  {sym} EMA多头排列后6根4H K线（约1天）涨幅：均值={np.mean(returns_1d):+.3f}% "
              f"上涨概率={sum(1 for r in returns_1d if r>0)/len(returns_1d)*100:.1f}% "
              f"(n={len(returns_1d)})")


# 条款5：TimingFilter有效性（近似验证）
print("\n条款5：体制过滤有效性对比")
print("  验证核心假设：BULL_TREND过滤是否真的提升做多胜率")
print("")

for sym in ['BTCUSDT', 'ETHUSDT']:
    sorted_regime = sorted_regime_btc if sym == 'BTCUSDT' else sorted_regime_eth
    
    # 无过滤做多
    def no_filter_long(regime, rsi, price, ema20):
        if regime != 'UNKNOWN' and 45 <= rsi <= 70:
            return 'LONG'
        return None
    
    # 有体制过滤做多
    def with_filter_long(regime, rsi, price, ema20):
        if regime == 'BULL_TREND' and 45 <= rsi <= 70:
            return 'LONG'
        return None
    
    r_no = verify_clause(sym, '1h', no_filter_long, sorted_regime)
    r_with = verify_clause(sym, '1h', with_filter_long, sorted_regime)
    
    if r_no and r_with:
        print(f"  {sym}: 无体制过滤 WR={r_no['wr']:.1f}% (n={r_no['n']}) → "
              f"有BULL_TREND过滤 WR={r_with['wr']:.1f}% (n={r_with['n']}) "
              f"提升={r_with['wr']-r_no['wr']:+.1f}%")


# ============================================================
# 第四部分：评分系统验证
# ============================================================

print("\n" + "=" * 70)
print("第四部分：评分系统有效性 & RSI分析")
print("=" * 70)

# RSI分层分析：不同RSI区间的胜率
print("\nRSI分层做多胜率矩阵（BTCUSDT 1H）：")
print(f"{'RSI区间':15s} | {'体制':15s} | {'样本n':8s} | {'WR':8s} | {'p值':10s}")
print("-" * 65)

rsi_brackets = [
    ('<30', lambda r: r < 30),
    ('30-45', lambda r: 30 <= r < 45),
    ('45-55', lambda r: 45 <= r < 55),
    ('55-65', lambda r: 55 <= r < 65),
    ('65-70', lambda r: 65 <= r < 70),
    ('70-80', lambda r: 70 <= r < 80),
    ('>80', lambda r: r >= 80),
]

regimes_to_check = ['BULL_TREND', 'BEAR_TREND', 'CHOP_MID']

for rsi_label, rsi_fn in rsi_brackets:
    for regime_label in ['BULL_TREND', 'BEAR_TREND']:
        fn_label = rsi_label
        fn_regime = regime_label
        
        def make_cond(rl, rfn):
            def c(regime, rsi, price, ema20):
                if regime == rl and rfn(rsi):
                    return 'LONG'
                return None
            return c
        
        c = make_cond(fn_regime, rsi_fn)
        res = verify_clause('BTCUSDT', '1h', c, sorted_regime_btc)
        if res and res['n'] >= 10:
            sig = "✅" if res['significant'] else "  "
            print(f"  {rsi_label:13s} | {regime_label:15s} | {res['n']:8d} | "
                  f"{res['wr']:7.1f}% | {res['p_val']:.4f} {sig}")

# RSI>80做空 vs 不做空
print("\nRSI>80时刻不同体制下的后续走势（做空逆势验证）：")
print("验证：RSI>80时做空是否为高风险操作")

for sym in ['BTCUSDT']:
    ks = data_store[sym]['1h']
    _, highs, lows, closes, _, times = extract_ohlcv(ks)
    ema20_s = calc_ema_series(closes, 20)
    
    rsi_high_results = {r: {'up4h': 0, 'up8h': 0, 'up24h': 0, 'total': 0} 
                        for r in ['BULL_TREND', 'BEAR_TREND', 'CHOP_MID']}
    
    for i in range(50, len(closes) - 25):
        hist_closes = closes[:i+1]
        rsi = calc_rsi(hist_closes, 14)
        if rsi < 80:
            continue
        cur_ts = times[i]
        regime = get_regime_at_time_fast(cur_ts, sorted_regime_btc)
        if regime not in rsi_high_results:
            continue
        
        p0 = closes[i]
        p4 = closes[i+4] if i+4 < len(closes) else closes[-1]
        p8 = closes[i+8] if i+8 < len(closes) else closes[-1]
        p24 = closes[i+24] if i+24 < len(closes) else closes[-1]
        
        rsi_high_results[regime]['total'] += 1
        if p4 > p0: rsi_high_results[regime]['up4h'] += 1
        if p8 > p0: rsi_high_results[regime]['up8h'] += 1
        if p24 > p0: rsi_high_results[regime]['up24h'] += 1
    
    print(f"\n  {sym} RSI>80时，各体制下后续上涨概率（即做空亏损概率）：")
    print(f"  {'体制':20s} | {'n':6s} | {'4H上涨%':10s} | {'8H上涨%':10s} | {'24H上涨%':10s}")
    print("  " + "-" * 65)
    for r in ['BULL_TREND', 'BEAR_TREND', 'CHOP_MID']:
        d = rsi_high_results[r]
        if d['total'] > 0:
            p4 = d['up4h'] / d['total'] * 100
            p8 = d['up8h'] / d['total'] * 100
            p24 = d['up24h'] / d['total'] * 100
            print(f"  {r:20s} | {d['total']:6d} | {p4:9.1f}% | {p8:9.1f}% | {p24:9.1f}%")


# ============================================================
# 第五部分：达摩院最终评级
# ============================================================

print("\n" + "=" * 70)
print("第五部分：达摩院最终评级")
print("=" * 70)

print("\n## 1. 系统可信度评级")
print("\n宪法条款统计检验汇总：")

def get_credibility_grade(p_val, n):
    if n < 10: return "数据不足"
    if p_val < 0.001: return "★★★★★ 极显著"
    if p_val < 0.01:  return "★★★★☆ 高度显著"
    if p_val < 0.05:  return "★★★☆☆ 显著"
    if p_val < 0.10:  return "★★☆☆☆ 边际显著"
    return "★☆☆☆☆ 不显著"

for c_id, c_desc, _, c_claim in clauses:
    res = clause_results.get(c_id)
    if res:
        grade = get_credibility_grade(res['p_val'], res['n'])
        print(f"  {c_id}: {c_desc}")
        print(f"    声称={c_claim:.1f}% | 实测={res['wr']:.1f}% | p={res['p_val']:.4f} | {grade}")

print("\n## 2. 最优操作周期评估")
print("\n综合各周期回测指标：")

best_sym_tf = {}
for sym in SYMBOLS:
    best_sharpe = -999
    best_tf = None
    for tf in TIMEFRAMES:
        res = backtest_results[sym].get(tf)
        if res and res.get('trades', 0) >= 10:
            if res['sharpe'] > best_sharpe:
                best_sharpe = res['sharpe']
                best_tf = tf
    if best_tf:
        best_sym_tf[sym] = best_tf
        print(f"  {sym} 最优周期: {best_tf} (夏普={best_sharpe:.2f})")

print("\n## 3. 实盘偏差分析")
print("""
  主要偏差来源：
  a) 滑点估算：BTC/ETH流动性极好，0.05%可能偏高（实际可能0.01-0.02%）
  b) 资金费率：极端行情可能超过0.01%/8H，保守估算存在低估风险
  c) K线内部顺序：无法精确判断当根K线内TP/SL触发顺序（假设SL优先）
  d) 体制标注延迟：4H体制只在4H收盘后更新，信号有最多4H延迟
  e) 样本偏差：2019-2024含2020 COVID崩盘和2022熊市，覆盖多个极端事件
""")

print("## 4. 改进优先级清单")
print("""
  优先级1 【高影响】 精细化体制判断
    - 当前EMA200仅用收盘价，建议增加成交量确认
    - 增加趋势强度过滤（ADX>25=强趋势，<20=震荡）
    
  优先级2 【高影响】 动态止损优化
    - 当前固定2%止损，建议改为ATR倍数（1.5x~2x ATR(14)）
    - 盈亏比非对称（做多TP=SL=2%，做空TP=SL=2.5%）建议验证非对称比例
    
  优先级3 【中影响】 信号过滤改进
    - 4H间隔过滤可能错过强趋势中的连续机会
    - 建议趋势强度高时缩短间隔至2H，震荡时延长至8H
    
  优先级4 【中影响】 资金管理
    - 固定风险1%在高胜率系统中偏保守
    - 建议引入Kelly Criterion的半Kelly方案
    
  优先级5 【低影响】 时段过滤
    - 亚洲盘/欧美盘交叉时段流动性差，可考虑回避凌晨02-06 UTC
""")

print("\n" + "=" * 70)
print("达摩院深度研究系统执行完毕")
print("梵天系统全流程验证 · 苏摩111最高指令完成")
print("=" * 70)
