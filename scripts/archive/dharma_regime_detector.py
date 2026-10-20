#!/usr/bin/env python3
"""dharma_regime_detector.py — 失效期检测器
2026-09-10 苏摩111批准 | 达摩院Step 2

目标：事前识别策略失效期，不是事后跳过F8/F9

事前信号（4个独立维度）：
  1. Hurst指数 < 0.5（随机游走 → alpha减弱）
  2. 体制切换频率 > 3次/30天（市场不稳定）
  3. 信号密度 < 50%基线（ms=110信号变少）
  4. ATR突变 > 2x（波动率突变）

判定规则：
  RED（失效期）：≥2/4信号触发 → 暂停交易
  YELLOW（警戒）：1/4信号触发 → 仓位减半
  GREEN（正常）：0/4信号触发 → 正常交易

验证方法：
  1. 用BTC 2022-01~2026-09历史数据计算每日4维信号
  2. 标记RED/YELLOW/GREEN区间
  3. 对比F8/F9期间是否被RED覆盖
  4. 计算检测器的事前准确率（在F8/F9之前就标记RED）

输出：data/dharma_regime_detector_result.json
"""
import gzip, json, math, time, sys
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict

BASE = Path(__file__).parent.parent
DATA = BASE / "data" / "historical"
OUT  = BASE / "data"

def load_klines(symbol, tf):
    fname = DATA / f"{symbol}_{tf}.jsonl.gz"
    if not fname.exists(): return []
    with gzip.open(fname, "rt") as f:
        return sorted([json.loads(l) for l in f if l.strip()], key=lambda x: x['ts'])

def load_regime(symbol):
    fname = DATA / f"{symbol}_regime_labels.jsonl.gz"
    if not fname.exists(): return []
    with gzip.open(fname, "rt") as f:
        return [json.loads(l) for l in f if l.strip()]

def calc_atr(bars, period=14):
    atrs = [0.0] * len(bars)
    for i in range(period, len(bars)):
        tr = max(bars[i]['h']-bars[i]['l'],
                 abs(bars[i]['h']-bars[i-1]['c']),
                 abs(bars[i]['l']-bars[i-1]['c']))
        if atrs[i-1] == 0:
            atrs[i] = tr
            for j in range(i-period+1, i):
                atrs[j] = max(bars[j]['h']-bars[j]['l'],
                             abs(bars[j]['h']-bars[j-1]['c']),
                             abs(bars[j]['l']-bars[j-1]['c'])) if j > 0 else bars[j]['h']-bars[j]['l']
            atrs[i] = sum(atrs[i-period+1:i+1]) / period
        else:
            atrs[i] = (atrs[i-1] * (period-1) + tr) / period
    return atrs

def hurst_exponent(series):
    """简化Hurst指数计算（R/S法）"""
    n = len(series)
    if n < 20:
        return 0.5
    # R/S法
    mean = sum(series) / n
    deviations = [s - mean for s in series]
    cumulative = []
    cumsum = 0
    for d in deviations:
        cumsum += d
        cumulative.append(cumsum)
    r = max(cumulative) - min(cumulative)
    std = math.sqrt(sum(d*d for d in deviations) / n)
    if std == 0 or r == 0:
        return 0.5
    rs = r / std
    # H = log(R/S) / log(n)
    if rs > 0:
        return min(1.0, max(0.0, math.log(rs) / math.log(n)))
    return 0.5

def regime_switch_count(regimes, start_ts, end_ts):
    """计算时间段内体制切换次数"""
    switches = 0
    prev = None
    for r in regimes:
        ts = r.get('ts', 0)
        if ts < start_ts or ts > end_ts:
            continue
        regime = r.get('regime', '')
        if prev and regime != prev:
            switches += 1
        prev = regime
    return switches

def calc_atr_ratio(atrs, bars, window=30*24):  # 30天（1H K线）
    """当前ATR / 历史均值ATR"""
    if len(atrs) < window:
        return 1.0
    recent = atrs[-1] if atrs else 0
    hist_avg = sum(atrs[-window:-1]) / (window - 1) if len(atrs) > window else sum(atrs[:-1]) / max(len(atrs) - 1, 1)
    if hist_avg == 0:
        return 1.0
    return recent / hist_avg

def main():
    t0 = time.time()
    
    # 加载BTC数据
    print("加载数据...")
    klines_1h = load_klines('BTCUSDT', '1h')
    regimes = load_regime('BTCUSDT')
    
    if not klines_1h:
        print("❌ 无BTC 1H K线数据")
        return
    print(f"  BTC 1H: {len(klines_1h)} 根")
    print(f"  BTC regime: {len(regimes)} 标签")
    
    atrs = calc_atr(klines_1h, 14)
    
    # 计算每日4维信号（从第60天开始，确保有足够数据）
    daily_signals = []
    window_days = 30
    window_bars = window_days * 24  # 30天1H K线
    
    print(f"\n计算每日4维信号（{window_days}天滚动窗口）...")
    
    for i in range(window_bars, len(klines_1h), 24):  # 每天计算一次
        ts = klines_1h[i]['ts']
        dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
        
        # 1. Hurst指数（30天收益率序列）
        window_prices = [b['c'] for b in klines_1h[i-window_bars:i]]
        returns = [(window_prices[j] - window_prices[j-1]) / window_prices[j-1]
                   for j in range(1, len(window_prices)) if window_prices[j-1] > 0]
        h = hurst_exponent(returns)
        
        # 2. [V2.0改革 2026-09-20 苏摩111] 无结构锚点 = 真失效期
        # 旧: 体制切换>5次 → 新: 最近24h价格波动率<0.3%（无波动=无结构=失效）
        # 用价格波动率替代体制切换：体制已不做门控，切换次数无意义
        # 波动率低=市场无结构=主力没有猎杀目标=真失效期
        recent_24h_returns = [(klines_1h[j]['c'] - klines_1h[j-1]['c']) / klines_1h[j-1]['c']
                             for j in range(max(0, i-24), i) if klines_1h[j-1]['c'] > 0]
        if recent_24h_returns:
            vol_24h = math.sqrt(sum(r*r for r in recent_24h_returns) / len(recent_24h_returns))
        else:
            vol_24h = 0
        no_structure = vol_24h < 0.003  # 24h波动率<0.3%=无结构=失效
        
        # 3. [V2.0改革] 清算区距离>10% = 主力没有猎杀目标 = 失效期
        # 旧: 信号密度<30% → 新: 30天最高价最低价差/当前价<8%=低波动=失效
        # 用价格区间替代信号密度：旧架构信号密度受score门控影响，V2.0后不可靠
        # 价格区间窄=主力没有空间猎杀=真失效期
        period_high = max(b['h'] for b in klines_1h[i-window_bars:i])
        period_low = min(b['l'] for b in klines_1h[i-window_bars:i])
        current_price = klines_1h[i]['c']
        if current_price > 0:
            price_range_pct = (period_high - period_low) / current_price
        else:
            price_range_pct = 0
        low_range = price_range_pct < 0.08  # 30天价格区间<8%=低波动=失效
        
        # 4. ATR突变系数（保留不变）
        atr_ratio = calc_atr_ratio(atrs[:i+1], klines_1h[:i+1], window_bars)
        
        # 信号触发判断 [V2.0改革]
        signals = {
            'hurst_low': h < 0.45,        # Hurst<0.45=随机游走（保留）
            'no_structure': no_structure,   # 24h波动率<0.3%=无结构（新）
            'low_range': low_range,         # 30天价格区间<8%=低波动（新）
            'atr_spike': atr_ratio > 2.5,   # ATR突变（保留）
        }
        trigger_count = sum(signals.values())
        
        # 状态判定
        if trigger_count >= 2:
            state = 'RED'    # 失效期
        elif trigger_count == 1:
            state = 'YELLOW'  # 警戒
        else:
            state = 'GREEN'   # 正常
        
        daily_signals.append({
            'ts': ts,
            'date': dt.strftime('%Y-%m-%d'),
            'hurst': round(h, 3),
            'vol_24h': round(vol_24h, 4),      # [V2.0] 替代switches
            'price_range_pct': round(price_range_pct, 3),  # [V2.0] 替代sig_density
            'atr_ratio': round(atr_ratio, 2),
            'signals': signals,
            'trigger_count': trigger_count,
            'state': state,
        })
    
    # 统计
    total = len(daily_signals)
    red_count = sum(1 for d in daily_signals if d['state'] == 'RED')
    yellow_count = sum(1 for d in daily_signals if d['state'] == 'YELLOW')
    green_count = sum(1 for d in daily_signals if d['state'] == 'GREEN')
    
    print(f"\n{'='*70}")
    print(f"📊 失效期检测器结果")
    print(f"{'='*70}")
    print(f"\n总天数: {total}")
    print(f"  🟢 GREEN（正常）: {green_count} ({green_count/total*100:.1f}%)")
    print(f"  🟡 YELLOW（警戒）: {yellow_count} ({yellow_count/total*100:.1f}%)")
    print(f"  🔴 RED（失效期）: {red_count} ({red_count/total*100:.1f}%)")
    
    # 信号触发频率
    print(f"\n信号触发频率:")
    for sig_name in ['hurst_low', 'no_structure', 'low_range', 'atr_spike']:
        count = sum(1 for d in daily_signals if d['signals'].get(sig_name))
        print(f"  {sig_name}: {count}/{total} ({count/total*100:.1f}%)")
    
    # RED区间分布
    red_periods = []
    in_red = False
    start_ts = 0
    for d in daily_signals:
        if d['state'] == 'RED' and not in_red:
            in_red = True
            start_ts = d['ts']
            start_date = d['date']
        elif d['state'] != 'RED' and in_red:
            in_red = False
            red_periods.append((start_date, d['date']))
    if in_red:
        red_periods.append((start_date, daily_signals[-1]['date']))
    
    print(f"\n🔴 RED区间（共{len(red_periods)}段）:")
    for start, end in red_periods[-10:]:  # 最近10段
        print(f"  {start} ~ {end}")
    
    # 最近30天状态
    print(f"\n最近30天状态:")
    for d in daily_signals[-30:]:
        emoji = {'GREEN': '🟢', 'YELLOW': '🟡', 'RED': '🔴'}[d['state']]
        triggers = d['trigger_count']
        print(f"  {emoji} {d['date']} H={d['hurst']:.2f} V24h={d.get('vol_24h',0):.4f} PR={d.get('price_range_pct',0):.2f} ATR={d['atr_ratio']:.1f}x [{triggers}/4]")
    
    # 当前状态
    current = daily_signals[-1] if daily_signals else None
    if current:
        print(f"\n{'='*70}")
        print(f"🎯 当前状态: {current['state']}")
        print(f"  Hurst: {current['hurst']:.3f} ({'低<0.45' if current['signals']['hurst_low'] else '正常'})")
        print(f"  24h波动率: {current.get('vol_24h',0):.4f} ({'无结构<0.3%' if current['signals'].get('no_structure') else '有结构'})")
        print(f"  30天价格区间: {current.get('price_range_pct',0):.1%} ({'低<8%' if current['signals'].get('low_range') else '正常'})")
        print(f"  ATR突变: {current['atr_ratio']:.2f}x ({'突变>2.5x' if current['signals']['atr_spike'] else '正常'})")
        print(f"  触发: {current['trigger_count']}/4 → {current['state']}")
        
        if current['state'] == 'RED':
            print(f"\n  ⚠️ 失效期！建议暂停交易或仓位减半")
        elif current['state'] == 'YELLOW':
            print(f"\n  ⚠️ 警戒期！建议仓位减半")
        else:
            print(f"\n  ✅ 正常期！正常交易")
    
    # 保存结果
    result = {
        'ts': datetime.utcnow().isoformat() + 'Z',
        'total_days': total,
        'green_pct': round(green_count / total * 100, 1),
        'yellow_pct': round(yellow_count / total * 100, 1),
        'red_pct': round(red_count / total * 100, 1),
        'red_periods': red_periods,
        'current_state': current['state'] if current else 'N/A',
        'current_signals': current['signals'] if current else {},
        'daily_signals': daily_signals,  # 全部历史
    }
    
    with open(OUT / 'dharma_regime_detector_result.json', 'w') as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    
    print(f"\n结果已保存: data/dharma_regime_detector_result.json")
    print(f"耗时: {time.time()-t0:.1f}s")

if __name__ == '__main__':
    main()
