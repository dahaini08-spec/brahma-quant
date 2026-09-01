# ponytail: volume_engine 301行，独立计算引擎，功能内聚，拆分条件: 单引擎>3000行且有完整测试
"""

# ── STATUS: ACTIVE ──────────────────────────────────────────
# 成交量分析，s4维度辅助
# LAST_REVIEW: 2026-07-01 | 属于辅助计算层，修改前确认调用链
# ─────────────────────────────────────────────────────────────
volume_engine.py · 量能分析引擎
brahma_brain · Phase 2

功能：
  - OBV（能量潮）趋势与背离
  - 成交量背离检测（价量关系）
  - VWAP 计算与方向判断
  - 成交量分布（简化Volume Profile）
  - 量能综合评分（0~20分）
"""
import math

# ═══════════════════════════════════════════════════════════════
# 一、OBV（能量潮）
# ═══════════════════════════════════════════════════════════════

def calc_obv(closes: list, volumes: list) -> list:
    """计算OBV序列"""
    if not closes or len(closes) != len(volumes):
        return []
    obv = [0.0]
    for i in range(1, len(closes)):
        if closes[i] > closes[i-1]:
            obv.append(obv[-1] + volumes[i])
        elif closes[i] < closes[i-1]:
            obv.append(obv[-1] - volumes[i])
        else:
            obv.append(obv[-1])
    return obv

def detect_obv_divergence(closes: list, volumes: list) -> dict:
    """OBV背离检测"""
    obv = calc_obv(closes, volumes)
    if len(obv) < 10:
        return {'bull_div': False, 'bear_div': False, 'trend_match': True, 'details': []}

    # 比较最近20根的价格与OBV走势
    n = min(20, len(closes))
    p_start, p_end = closes[-n],  closes[-1]
    o_start, o_end = obv[-n],     obv[-1]

    p_up  = p_end > p_start
    o_up  = o_end > o_start

    # OBV创新高而价格未到 → 多头蓄力
    obv_new_high  = obv[-1] > max(obv[-n:-1]) if len(obv) >= n+1 else False
    price_new_high = closes[-1] > max(closes[-n:-1]) if len(closes) >= n+1 else False
    obv_new_low   = obv[-1] < min(obv[-n:-1]) if len(obv) >= n+1 else False
    price_new_low  = closes[-1] < min(closes[-n:-1]) if len(closes) >= n+1 else False

    details = []
    bull_div = False
    bear_div = False

    # 看多背离：价格LL，OBV HL
    if not p_up and o_up:
        bull_div = True
        details.append('OBV看多背离：价格下跌但OBV上升，多头积累')
    # 看空背离：价格HH，OBV LH
    if p_up and not o_up:
        bear_div = True
        details.append('OBV看空背离：价格上涨但OBV下降，多头衰竭')
    # OBV领先信号
    if obv_new_high and not price_new_high:
        bull_div = True
        details.append('OBV创新高价格未到，多头蓄力')
    if obv_new_low and not price_new_low:
        bear_div = True
        details.append('OBV创新低价格未到，空头蓄力')

    return {
        'bull_div':    bull_div,
        'bear_div':    bear_div,
        'trend_match': p_up == o_up,   # 量价同向
        'obv_now':     round(obv[-1], 2),
        'details':     details,
    }

# ═══════════════════════════════════════════════════════════════
# 二、成交量分析
# ═══════════════════════════════════════════════════════════════

def analyze_volume(closes: list, volumes: list, n_ma: int = 20) -> dict:
    """分析成交量质量"""
    if len(volumes) < n_ma:
        return {'quality': 'NORMAL', 'details': [], 'score_long': 0, 'score_short': 0}

    vol_ma = sum(volumes[-n_ma:]) / n_ma
    vol_now = volumes[-1]
    vol_ratio = vol_now / vol_ma if vol_ma > 0 else 1.0

    price_up = closes[-1] > closes[-2]
    details  = []
    score_long = score_short = 0

    # 量增价涨 = 健康上涨
    if price_up and vol_ratio > 1.5:
        details.append(f'量增价涨(量={vol_ratio:.1f}x) 强多确认')
        score_long += 5
    # 量减价涨 = 动能衰竭
    elif price_up and vol_ratio < 0.7:
        details.append(f'量减价涨(量={vol_ratio:.1f}x) 上涨动能衰竭')
        score_short += 3
    # 量增价跌 = 恐慌抛售
    elif not price_up and vol_ratio > 1.5:
        details.append(f'量增价跌(量={vol_ratio:.1f}x) 恐慌抛售/主力出货')
        score_short += 4
    # 量减价跌 = 下跌尾声
    elif not price_up and vol_ratio < 0.7:
        details.append(f'量减价跌(量={vol_ratio:.1f}x) 下跌尾声，空头衰竭')
        score_long += 3

    # 超量（>3x均量）
    if vol_ratio > 3.0:
        details.append(f'极端成交量({vol_ratio:.1f}x) 主力入场/反转信号')
        if price_up:   score_long += 3
        else:          score_short += 2

    # 量能萎缩后放量突破
    recent_vols = volumes[-10:-1]
    if recent_vols:
        recent_avg = sum(recent_vols) / len(recent_vols)
        if recent_avg < vol_ma * 0.6 and vol_now > vol_ma * 1.5:
            details.append('量能萎缩后放量突破，方向确认')
            if price_up:   score_long += 5
            else:          score_short += 4

    quality = 'STRONG' if vol_ratio > 2.0 else ('WEAK' if vol_ratio < 0.5 else 'NORMAL')

    return {
        'quality':     quality,
        'vol_ratio':   round(vol_ratio, 2),
        'vol_ma':      round(vol_ma, 2),
        'details':     details,
        'score_long':  min(score_long, 10),
        'score_short': min(score_short, 10),
    }

# ═══════════════════════════════════════════════════════════════
# 三、VWAP 计算
# ═══════════════════════════════════════════════════════════════

def calc_vwap(highs: list, lows: list, closes: list, volumes: list,
              session_bars: int = 96) -> dict:
    """
    计算VWAP（按session，默认96根1H K线≈4天）
    """
    n = min(session_bars, len(closes))
    h = highs[-n:]
    l = lows[-n:]
    c = closes[-n:]
    v = volumes[-n:]

    tp_v = sum(((h[i]+l[i]+c[i])/3) * v[i] for i in range(n))
    total_v = sum(v)
    if total_v == 0:
        return {'vwap': closes[-1], 'above_vwap': True, 'dist_pct': 0}

    vwap = tp_v / total_v
    price = closes[-1]
    above = price >= vwap
    dist  = (price - vwap) / vwap * 100

    return {
        'vwap':       round(vwap, 6),
        'above_vwap': above,
        'dist_pct':   round(dist, 3),
        'note': f'价格在VWAP{"上方" if above else "下方"} {abs(dist):.2f}%',
    }

# ═══════════════════════════════════════════════════════════════
# 四、简化Volume Profile（关键成交量节点）
# ═══════════════════════════════════════════════════════════════

def calc_volume_profile(highs: list, lows: list, closes: list,
                         volumes: list, bins: int = 20) -> dict:
    """简化成交量分布，识别HVN/LVN"""
    if len(closes) < bins:
        return {'poc': closes[-1], 'hvn': [], 'lvn': []}

    price_min = min(lows[-100:])
    price_max = max(highs[-100:])
    if price_max <= price_min:
        return {'poc': closes[-1], 'hvn': [], 'lvn': []}

    bin_size  = (price_max - price_min) / bins
    vol_bins  = [0.0] * bins

    for i in range(len(closes)-100 if len(closes)>100 else 0, len(closes)):
        mid_price = (highs[i] + lows[i]) / 2
        idx = int((mid_price - price_min) / bin_size)
        idx = max(0, min(idx, bins-1))
        vol_bins[idx] += volumes[i]

    max_vol = max(vol_bins) if vol_bins else 1
    poc_idx = vol_bins.index(max_vol)
    poc = price_min + (poc_idx + 0.5) * bin_size

    avg_vol = sum(vol_bins) / bins
    hvn = []  # 高成交量节点（>均量150%）
    lvn = []  # 低成交量节点（<均量50%）

    for i, v in enumerate(vol_bins):
        level = price_min + (i + 0.5) * bin_size
        price = closes[-1]
        if v > avg_vol * 1.5:
            hvn.append(round(level, 4))
        elif v < avg_vol * 0.5:
            lvn.append(round(level, 4))

    return {
        'poc':    round(poc, 4),
        'hvn':    hvn,
        'lvn':    lvn,
        'note':  f'POC(最大成交量价格)=${poc:.4f}',
    }

# ═══════════════════════════════════════════════════════════════
# 五、量能综合评分（0~20分）
# ═══════════════════════════════════════════════════════════════

def volume_score(highs: list, lows: list, closes: list,
                  volumes: list, signal_dir: str) -> dict:
    """量能综合评分"""
    obv_div = detect_obv_divergence(closes, volumes)
    vol_ana = analyze_volume(closes, volumes)
    vwap    = calc_vwap(highs, lows, closes, volumes)
    vp      = calc_volume_profile(highs, lows, closes, volumes)

    score   = 0
    details = []

    # OBV背离
    if signal_dir == 'LONG' and obv_div['bull_div']:
        score += 5; details.append('OBV看多背离 +5')
    if signal_dir == 'SHORT' and obv_div['bear_div']:
        score += 5; details.append('OBV看空背离 +5')
    # 量价同向
    if obv_div['trend_match']:
        score += 2; details.append('量价同向 +2')

    # 成交量分析
    if signal_dir == 'LONG':
        score += vol_ana['score_long']
        details += [d for d in vol_ana['details'] if '多' in d or '上涨' in d or '尾声' in d]
    else:
        score += vol_ana['score_short']
        details += [d for d in vol_ana['details'] if '空' in d or '衰竭' in d or '出货' in d]

    # VWAP方向
    if signal_dir == 'LONG' and vwap['above_vwap']:
        score += 3; details.append(f'价格在VWAP上方 +3')
    elif signal_dir == 'LONG' and not vwap['above_vwap']:
        score += 1; details.append(f'价格在VWAP下方(折价区) +1')
    if signal_dir == 'SHORT' and not vwap['above_vwap']:
        score += 3; details.append(f'价格在VWAP下方 +3')

    # POC磁吸
    price = closes[-1]
    poc_dist = abs(vp['poc'] - price) / price * 100
    if poc_dist < 1.0:
        score += 2; details.append(f'POC磁吸区附近({poc_dist:.2f}%) +2')

    score = min(score, 20)
    return {
        'score':   score,
        'max':     20,
        'details': details,
        'obv':     obv_div,
        'vol':     vol_ana,
        'vwap':    vwap,
        'vp':      vp,
    }

# ─── 快速测试 ────────────────────────────────────────────────
if __name__ == '__main__':
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from data_cache import get_klines, klines_to_ohlcv

    sym = sys.argv[1] if len(sys.argv) > 1 else 'ETHUSDT'
    direction = sys.argv[2] if len(sys.argv) > 2 else 'SHORT'

    k1h = klines_to_ohlcv(get_klines(sym, '1h', 200))
    res = volume_score(k1h['h'], k1h['l'], k1h['c'], k1h['v'], direction)

    pass  # [静默]
    print(f'  量能评分: {res["score"]}/20')
    print(f'  VWAP:    ${res["vwap"]["vwap"]:,.4f}  {res["vwap"]["note"]}')
    print(f'  POC:     ${res["vp"]["poc"]:,.4f}')
    print(f'  量比:    {res["vol"]["vol_ratio"]}x')
    for d in res['details']:
        print(f'  + {d}')
    pass  # [静默]
#!/usr/bin/env python3
# ponytail: volume_exhaustion_engine 371行，独立计算引擎，功能内聚，拆分条件: 单引擎>3000行且有完整测试
"""
╔══════════════════════════════════════════════════════════════════╗
║  梵天大脑 · volume_exhaustion_engine.py                          ║
║  量能衰竭引擎 — 底部识别核心武器                                  ║
║                                                                  ║
║  识别：                                                           ║
║    1. 放量暴跌后量能萎缩（卖压耗尽）                              ║
║    2. 底部Pin Bar（插针+收复）                                    ║
║    3. 量价底背离（价格创新低，量能不创新高）                       ║
║    4. 成交量衰减序列（连续缩量=主动抛压结束）                      ║
║                                                                  ║
║  评分贡献：0~15分（注入 s_vol_exhaustion 独立维度）               ║
║  设计院 v1.0 · 2026-06-05                                        ║
╚══════════════════════════════════════════════════════════════════╝
"""
import statistics
from typing import Optional


# ═══════════════════════════════════════════════════════════════
# 一、放量暴跌后量能萎缩检测
# ═══════════════════════════════════════════════════════════════

def detect_volume_exhaustion(highs: list, lows: list, closes: list,
                              volumes: list, signal_dir: str,
                              lookback: int = 20) -> dict:
    """
    核心检测：
    - LONG方向：寻找放量下跌后量能骤降（卖方衰竭）
    - SHORT方向：寻找放量上涨后量能骤降（买方衰竭）

    返回：{'detected': bool, 'score': int, 'note': str, 'exhaustion_ratio': float}
    """
    if len(closes) < lookback or len(volumes) < lookback:
        return {'detected': False, 'score': 0, 'note': '数据不足', 'exhaustion_ratio': 1.0}

    h = highs[-lookback:]
    l = lows[-lookback:]
    c = closes[-lookback:]
    v = volumes[-lookback:]

    # 找最大成交量K线（主力动作）
    max_vol_idx = v.index(max(v))
    max_vol = v[max_vol_idx]
    avg_vol = statistics.mean(v)

    # 最近3根K线均量
    recent_avg_vol = statistics.mean(v[-3:]) if len(v) >= 3 else v[-1]

    # 衰竭比率：最近量 / 峰值量
    exhaustion_ratio = recent_avg_vol / max_vol if max_vol > 0 else 1.0

    score = 0
    notes = []

    if signal_dir == 'LONG':
        # 寻找：峰值量出现在下跌K线（恐慌抛售），之后量萎缩
        if max_vol_idx < len(v) - 2:  # 峰值不在最近
            # 峰值K线是否是下跌（收盘 < 开盘 的近似）
            peak_bearish = c[max_vol_idx] < h[max_vol_idx] * 0.995
            if peak_bearish and exhaustion_ratio < 0.5:
                score += 8
                notes.append(f'放量暴跌后缩量{exhaustion_ratio:.0%} → 卖压耗尽 +8')
            elif peak_bearish and exhaustion_ratio < 0.7:
                score += 5
                notes.append(f'放量暴跌后量能回落{exhaustion_ratio:.0%} +5')

        # 额外：最近3根均量 < 总体均量的60%（整体缩量）
        if recent_avg_vol < avg_vol * 0.6:
            score += 3
            notes.append(f'近期量能低迷({recent_avg_vol:.0f}<均值{avg_vol:.0f}×60%) +3')

        # 峰值量超过均量的2倍（真正放量）
        if max_vol > avg_vol * 2.0 and exhaustion_ratio < 0.5:
            score += 2
            notes.append(f'峰值量={max_vol:.0f}(均值{avg_vol:.0f}×{max_vol/avg_vol:.1f}倍) 真实恐慌 +2')

    elif signal_dir == 'SHORT':
        # 寻找：峰值量出现在上涨K线（狂热追涨），之后量萎缩
        if max_vol_idx < len(v) - 2:
            peak_bullish = c[max_vol_idx] > l[max_vol_idx] * 1.005
            if peak_bullish and exhaustion_ratio < 0.5:
                score += 8
                notes.append(f'放量拉升后缩量{exhaustion_ratio:.0%} → 买压耗尽 +8')
            elif peak_bullish and exhaustion_ratio < 0.7:
                score += 5
                notes.append(f'放量拉升后量能回落{exhaustion_ratio:.0%} +5')

        if recent_avg_vol < avg_vol * 0.6:
            score += 3
            notes.append(f'近期量能低迷 → 追涨动能枯竭 +3')

        if max_vol > avg_vol * 2.0 and exhaustion_ratio < 0.5:
            score += 2
            notes.append(f'峰值量超均值{max_vol/avg_vol:.1f}倍 确认买方高潮 +2')

    return {
        'detected': score > 0,
        'score': min(score, 10),
        'note': ' | '.join(notes) if notes else '无量能衰竭信号',
        'exhaustion_ratio': round(exhaustion_ratio, 3),
        'max_vol_idx': max_vol_idx,
        'peak_vol_mult': round(max_vol / avg_vol, 2) if avg_vol > 0 else 0,
    }


# ═══════════════════════════════════════════════════════════════
# 二、底部Pin Bar识别
# ═══════════════════════════════════════════════════════════════

def detect_pin_bar(highs: list, lows: list, opens: list, closes: list,
                   signal_dir: str, lookback: int = 5) -> dict:
    """
    Pin Bar（钉形K线）：
    - LONG底部pin bar：长下影线 > 实体2倍，下影>上影3倍，收盘在上半段
    - SHORT顶部pin bar：长上影线 > 实体2倍，上影>下影3倍，收盘在下半段
    """
    if len(closes) < lookback:
        return {'detected': False, 'score': 0, 'note': ''}

    results = []
    for i in range(-min(lookback, 3), 0):  # 检测最近3根
        if abs(i) > len(closes):
            continue
        h = highs[i]
        l = lows[i]
        o = opens[i] if opens else closes[i - 1]
        c = closes[i]

        total_range = h - l
        if total_range < 1e-9:
            continue

        body = abs(c - o)
        upper_wick = h - max(c, o)
        lower_wick = min(c, o) - l

        # LONG底部pin bar
        if signal_dir == 'LONG':
            if (lower_wick > body * 2 and
                lower_wick > upper_wick * 3 and
                c > (h + l) / 2 and  # 收盘在上半段
                body / total_range < 0.35):
                score = 8 if lower_wick > body * 3 else 5
                results.append({
                    'bar_idx': i,
                    'score': score,
                    'note': f'底部Pin Bar 下影/实体={lower_wick/body:.1f}倍 +{score}',
                    'lower_wick_pct': round(lower_wick / total_range * 100, 1),
                })

        # SHORT顶部pin bar
        elif signal_dir == 'SHORT':
            if (upper_wick > body * 2 and
                upper_wick > lower_wick * 3 and
                c < (h + l) / 2 and  # 收盘在下半段
                body / total_range < 0.35):
                score = 8 if upper_wick > body * 3 else 5
                results.append({
                    'bar_idx': i,
                    'score': score,
                    'note': f'顶部Pin Bar 上影/实体={upper_wick/body:.1f}倍 +{score}',
                    'upper_wick_pct': round(upper_wick / total_range * 100, 1),
                })

    if not results:
        return {'detected': False, 'score': 0, 'note': '无Pin Bar'}

    best = max(results, key=lambda x: x['score'])
    return {'detected': True, **best}


# ═══════════════════════════════════════════════════════════════
# 三、量价底背离
# ═══════════════════════════════════════════════════════════════

def detect_volume_price_divergence(lows: list, highs: list,
                                   volumes: list, signal_dir: str,
                                   lookback: int = 20) -> dict:
    """
    底背离：价格创新低，但对应成交量不创新高（卖方越来越没力气）
    顶背离：价格创新高，但对应成交量不创新高（买方越来越没动力）
    """
    if len(lows) < lookback:
        return {'detected': False, 'score': 0, 'note': ''}

    prices = lows[-lookback:] if signal_dir == 'LONG' else highs[-lookback:]
    vols = volumes[-lookback:]

    # 找最低/最高价格点
    if signal_dir == 'LONG':
        p1_idx = prices.index(min(prices))  # 最低价
        p2_prices = prices[:p1_idx]
        if len(p2_prices) < 3:
            return {'detected': False, 'score': 0, 'note': '样本不足'}
        p2_idx = p2_prices.index(min(p2_prices))  # 前一个低点

        # 价格新低
        if prices[p1_idx] < prices[p2_idx]:
            # 量能不创新高（新低时量能更小）
            v1 = vols[p1_idx]
            v2 = vols[p2_idx]
            if v1 < v2 * 0.85:  # 新低时量能明显萎缩
                ratio = v1 / v2
                score = 8 if ratio < 0.6 else (5 if ratio < 0.75 else 3)
                return {
                    'detected': True,
                    'score': score,
                    'note': f'量价底背离：新低${prices[p1_idx]:.2f} 量能仅前低{ratio:.0%} +{score}',
                    'vol_ratio': round(ratio, 3),
                }

    elif signal_dir == 'SHORT':
        p1_idx = prices.index(max(prices))
        p2_prices = prices[:p1_idx]
        if len(p2_prices) < 3:
            return {'detected': False, 'score': 0, 'note': '样本不足'}
        p2_idx = p2_prices.index(max(p2_prices))

        if prices[p1_idx] > prices[p2_idx]:
            v1 = vols[p1_idx]
            v2 = vols[p2_idx]
            if v1 < v2 * 0.85:
                ratio = v1 / v2
                score = 8 if ratio < 0.6 else (5 if ratio < 0.75 else 3)
                return {
                    'detected': True,
                    'score': score,
                    'note': f'量价顶背离：新高${prices[p1_idx]:.2f} 量能仅前高{ratio:.0%} +{score}',
                    'vol_ratio': round(ratio, 3),
                }

    return {'detected': False, 'score': 0, 'note': '无量价背离'}


# ═══════════════════════════════════════════════════════════════
# 四、连续缩量序列（主动抛压结束）
# ═══════════════════════════════════════════════════════════════

def detect_volume_decay(volumes: list, lookback: int = 5) -> dict:
    """
    连续3根以上缩量 = 主动抛压/追涨动能终结
    """
    if len(volumes) < lookback:
        return {'detected': False, 'score': 0, 'consecutive_shrink': 0}

    v = volumes[-lookback:]
    consecutive = 0
    for i in range(len(v) - 1, 0, -1):
        if v[i] < v[i - 1] * 0.92:  # 缩量8%以上算一次
            consecutive += 1
        else:
            break

    score = 0
    if consecutive >= 4:
        score = 4
    elif consecutive >= 3:
        score = 3
    elif consecutive >= 2:
        score = 1

    return {
        'detected': consecutive >= 2,
        'score': score,
        'consecutive_shrink': consecutive,
        'note': f'连续{consecutive}根缩量 +{score}' if score > 0 else '',
    }


# ═══════════════════════════════════════════════════════════════
# 五、主接口：综合量能衰竭评分
# ═══════════════════════════════════════════════════════════════

def volume_exhaustion_score(highs: list, lows: list, opens: list,
                             closes: list, volumes: list,
                             signal_dir: str) -> dict:
    """
    综合量能衰竭评分，最高15分
    用于 brahma_brain 评分流水线注入

    返回：
    {
        'score': int,          # 0~15
        'components': dict,    # 各子检测结果
        'notes': list,         # 文字说明
        'exhaustion_level': str  # NONE/MILD/STRONG/EXTREME
    }
    """
    if not volumes or all(v == 0 for v in volumes[-10:]):
        return {
            'score': 0,
            'components': {},
            'notes': ['无成交量数据'],
            'exhaustion_level': 'NONE',
        }

    notes = []
    total = 0
    components = {}

    # 1. 主衰竭检测
    exh = detect_volume_exhaustion(highs, lows, closes, volumes, signal_dir, 20)
    components['exhaustion'] = exh
    if exh['score'] > 0:
        total += exh['score']
        notes.append(exh['note'])

    # 2. Pin Bar
    pin = detect_pin_bar(highs, lows, opens, closes, signal_dir, 5)
    components['pin_bar'] = pin
    if pin['detected']:
        total += pin['score']
        notes.append(pin['note'])

    # 3. 量价背离
    vpd = detect_volume_price_divergence(lows, highs, volumes, signal_dir, 20)
    components['vol_price_div'] = vpd
    if vpd['detected']:
        total += vpd['score']
        notes.append(vpd['note'])

    # 4. 连续缩量
    vd = detect_volume_decay(volumes, 6)
    components['vol_decay'] = vd
    if vd['detected']:
        total += vd['score']
        notes.append(vd['note'])

    # 衰竭等级
    total = min(total, 15)
    if total >= 12:
        level = 'EXTREME'
    elif total >= 8:
        level = 'STRONG'
    elif total >= 4:
        level = 'MILD'
    else:
        level = 'NONE'

    return {
        'score': total,
        'components': components,
        'notes': notes,
        'exhaustion_level': level,
        'exhaustion_ratio': exh.get('exhaustion_ratio', 1.0),
    }


if __name__ == '__main__':
    import math, random
    random.seed(42)
    n = 30
    # 模拟下跌后缩量底部
    prices = [100 - i * 0.5 + random.uniform(-0.3, 0.3) for i in range(n)]
    highs  = [p + random.uniform(0.2, 1.5) for p in prices]
    lows   = [p - random.uniform(0.2, 1.5) for p in prices]
    opens  = [prices[i-1] if i > 0 else prices[0] for i in range(n)]
    # 前段放量暴跌，后段缩量
    vols   = [5000 + random.uniform(0,1000) for _ in range(15)] + \
             [500 + random.uniform(0,200) for _ in range(15)]
    # 模拟底部pin bar
    lows[-1] = min(lows) - 1.0
    highs[-1] = prices[-1] + 0.3

    r = volume_exhaustion_score(highs, lows, opens, prices, vols, 'LONG')
    print(f'量能衰竭评分: {r["score"]}/15  等级: {r["exhaustion_level"]}')
    for note in r['notes']:
        print(f'  → {note}')
"""
volume_profile.py — 成交量分布密度分析（Volume Profile）
设计院·达摩院 三院审核修复 2026-07-08

职责：
  1. 计算当前价格区间的历史成交密度
  2. 识别高密度支撑区 / 低密度空洞区
  3. 为 brahma_core s8 提供 VolProfile 评分

结论映射：
  密度 > 1.5x 均值 → 高密度支撑区 → 做多 +8 / 做空 -5
  密度 > 1.2x 均值 → 中密度支撑区 → 做多 +4 / 做空 -2
  密度 < 0.8x 均值 → 低密度空洞区 → 做多 -8（下跌加速风险）
  密度 < 0.6x 均值 → 极低密度空洞 → 做多 -15（踩踏风险）

数据源：Binance fapi/v1/klines（近96根1H K线，免费）
"""

import requests
import time
from typing import Tuple
try:
    from brahma_brain.data_cache import get_klines as _dc_get_klines
except ImportError:
    _dc_get_klines = None
try:
    from brahma_brain.brahma_bus import get_price as _bus_get_price
except ImportError:
    _bus_get_price = None

_CACHE: dict = {}
_CACHE_TTL = 300  # 5分钟，1H K线数据更新慢


def _fetch_klines(symbol: str, limit: int = 96) -> list:
    """拉取近96根1H K线"""
    cache_key = f'vp_klines_{symbol}'
    now = time.time()
    if cache_key in _CACHE and now - _CACHE[cache_key]['ts'] < _CACHE_TTL:
        return _CACHE[cache_key]['data']
    try:
        if _dc_get_klines:
            data = _dc_get_klines(symbol, '1h', limit)
            if data:
                _CACHE[cache_key] = {'ts': now, 'data': data}
                return data
        url = f'https://fapi.binance.com/fapi/v1/klines'
        r = requests.get(url, params={'symbol': symbol, 'interval': '1h', 'limit': limit}, timeout=8)
        data = r.json()
        _CACHE[cache_key] = {'ts': now, 'data': data}
        return data
    except Exception:
        return []


def get_volume_profile(symbol: str, price: float, bin_width_pct: float = 0.5) -> dict:
    """
    计算当前价格区间的成交密度

    返回：
      density_ratio   : 当前区间密度 / 均值密度
      density_label   : HIGH_DENSITY / NORMAL / LOW_DENSITY / VOID
      score_adj       : 评分调整（做多视角）
      nearby_hvn      : 附近高密度价值区（HVN）
      nearby_lvn      : 附近低密度价值区（LVN）
      poc             : Point of Control（最高成交密度价位）
    """
    klines = _fetch_klines(symbol, 96)
    if not klines or price <= 0:
        return _empty(price)

    bin_size = price * bin_width_pct / 100
    price_bins: dict = {}
    all_volumes = []

    for k in klines:
        try:
            lo = float(k[3]); hi = float(k[2]); vol = float(k[5])
            # 将成交量分配到价格区间
            mid = (lo + hi) / 2
            bin_key = round(mid / bin_size) * bin_size
            price_bins[bin_key] = price_bins.get(bin_key, 0) + vol
            all_volumes.append(vol)
        except Exception:
            continue

    if not price_bins:
        return _empty(price)

    # 均值密度
    avg_density = sum(price_bins.values()) / len(price_bins)
    if avg_density <= 0:
        return _empty(price)

    # 当前价格区间密度
    cur_bin = round(price / bin_size) * bin_size
    cur_density = price_bins.get(cur_bin, 0)
    # 扩展±1档搜索（防止边界效应）
    for adj in [-bin_size, bin_size]:
        adj_bin = round((price + adj) / bin_size) * bin_size
        cur_density = max(cur_density, price_bins.get(adj_bin, 0))

    density_ratio = round(cur_density / avg_density, 2)

    # 分类
    if density_ratio >= 1.5:
        label = 'HIGH_DENSITY'
        score_adj_long = +8
        score_adj_desc = f'高密度筹码区{density_ratio:.1f}x→支撑强'
    elif density_ratio >= 1.2:
        label = 'NORMAL_HIGH'
        score_adj_long = +4
        score_adj_desc = f'中密度筹码区{density_ratio:.1f}x→支撑中等'
    elif density_ratio >= 0.8:
        label = 'NORMAL'
        score_adj_long = 0
        score_adj_desc = f'普通密度区{density_ratio:.1f}x→中性'
    elif density_ratio >= 0.6:
        label = 'LOW_DENSITY'
        score_adj_long = -8
        score_adj_desc = f'低密度空洞{density_ratio:.1f}x→支撑薄弱'
    else:
        label = 'VOID'
        score_adj_long = -15
        score_adj_desc = f'极低密度空洞{density_ratio:.1f}x→踩踏风险!'

    # POC（最高成交密度价位）
    poc_bin = max(price_bins, key=lambda k: price_bins[k])

    # 附近HVN（高密度区，>1.5x，价格±5%范围内）
    hvn_list = sorted(
        [(b, v) for b, v in price_bins.items()
         if v > avg_density * 1.5 and abs(b - price) / price < 0.05],
        key=lambda x: abs(x[0] - price)
    )
    nearby_hvn = [round(b, 2) for b, _ in hvn_list[:3]]

    # 附近LVN（低密度区，<0.6x，价格±5%范围内）
    lvn_list = sorted(
        [(b, v) for b, v in price_bins.items()
         if v < avg_density * 0.6 and abs(b - price) / price < 0.05],
        key=lambda x: abs(x[0] - price)
    )
    nearby_lvn = [round(b, 2) for b, _ in lvn_list[:3]]

    return {
        'density_ratio':  density_ratio,
        'density_label':  label,
        'score_adj_long': score_adj_long,
        'score_adj_short': -score_adj_long,  # 做空视角相反
        'desc':           score_adj_desc,
        'poc':            round(poc_bin, 2),
        'nearby_hvn':     nearby_hvn,
        'nearby_lvn':     nearby_lvn,
        'avg_density':    round(avg_density, 2),
        'cur_density':    round(cur_density, 2),
    }


def get_vp_score(symbol: str, price: float, signal_dir: str) -> Tuple[int, str]:
    """供 brahma_core s8 调用的评分接口"""
    try:
        vp = get_volume_profile(symbol, price)
        if signal_dir == 'LONG':
            score = vp['score_adj_long']
        else:
            score = vp['score_adj_short']
        desc = vp['desc']
        return score, desc
    except Exception:
        return 0, 'VolProfile N/A'


def _empty(price: float) -> dict:
    return {
        'density_ratio': 1.0, 'density_label': 'NORMAL',
        'score_adj_long': 0, 'score_adj_short': 0,
        'desc': 'VolProfile 数据不足',
        'poc': price, 'nearby_hvn': [], 'nearby_lvn': [],
        'avg_density': 0, 'cur_density': 0,
    }


if __name__ == '__main__':
    import requests as _r
    for sym in ['BTCUSDT', 'ETHUSDT']:
        px = float(_r.get('https://fapi.binance.com/fapi/v1/ticker/price',
                          params={'symbol': sym}, timeout=8).json()['price'])
        vp = get_volume_profile(sym, px)
        score_l, desc_l = get_vp_score(sym, px, 'LONG')
        print(f"{sym} ${px:.2f}")
        print(f"  密度: {vp['density_ratio']}x ({vp['density_label']})")
        print(f"  POC: ${vp['poc']:.2f}")
        print(f"  HVN: {vp['nearby_hvn']}")
        print(f"  LVN: {vp['nearby_lvn']}")
        print(f"  做多评分: {score_l:+d} | {desc_l}")
        print()
#!/usr/bin/env python3
"""

# ── STATUS: ACTIVE ──────────────────────────────────────────
# CVD累计成交量差，s12维度
# LAST_REVIEW: 2026-07-01 | 属于辅助计算层，修改前确认调用链
# ─────────────────────────────────────────────────────────────
cvd_engine.py — 多周期 CVD 引擎 v1.0
星枢引擎 Layer 1 · 设计院 2026-06-09

CVD（Cumulative Volume Delta）= 主动买方成交量 - 主动卖方成交量

数据来源（优先级）：
  1. Binance Taker Long/Short Ratio（周期级别，最准）
  2. Binance aggTrades（短周期微观，最新500条）
  3. Kline takerBuyBaseAssetVolume（降级fallback）

多周期输出：
  - micro:  5m × 12根（1H微观买卖压力）
  - meso:   1h × 24根（日内中期趋势）
  - macro:  4h × 14根（多日宏观方向）
  - signal: 综合方向 + 背离 + 梯度

评分贡献（接入 enhanced_signal_engine）：
  SHORT方向：macro SELL + meso SELL → +6
             macro SELL + micro SELL → +4
             单独 meso SELL → +2
             背离（价格涨但CVD降）→ +2
  LONG方向：反向同理
"""

import time
import json
import urllib.request

FAPI = "https://fapi.binance.com"
_cache: dict = {}


def _get(url: str, ttl: int = 30):
    now = time.time()
    if url in _cache and now - _cache[url][0] < ttl:
        return _cache[url][1]
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())
            _cache[url] = (now, data)
            return data
    except Exception:
        return None


def _taker_cvd(symbol: str, period: str, limit: int) -> list[float]:
    """用 Taker Long/Short Ratio 计算各周期 CVD 序列（买卖量差）"""
    url = (f"{FAPI}/futures/data/takerlongshortRatio"
           f"?symbol={symbol}&period={period}&limit={limit}")
    data = _get(url, ttl=60)
    if not data:
        return []
    series = []
    for d in data:
        buy_v  = float(d.get("buyVol",  0) or 0)
        sell_v = float(d.get("sellVol", 0) or 0)
        series.append(buy_v - sell_v)
    return series


def _kline_cvd(symbol: str, interval: str, limit: int) -> list[float]:
    """用 Kline takerBuyBaseAssetVolume 计算 CVD（降级方案）"""
    url = (f"{FAPI}/fapi/v1/klines"
           f"?symbol={symbol}&interval={interval}&limit={limit}")
    data = _get(url, ttl=60)
    if not data:
        return []
    series = []
    for k in data:
        total_vol = float(k[5])
        taker_buy = float(k[9])
        taker_sell = total_vol - taker_buy
        series.append(taker_buy - taker_sell)
    return series


def _aggTrades_cvd(symbol: str) -> float:
    """最新500条 aggTrades 微观CVD（绝对量）"""
    url = f"{FAPI}/fapi/v1/aggTrades?symbol={symbol}&limit=500"
    trades = _get(url, ttl=15)
    if not trades:
        return 0.0
    buy_vol  = sum(float(t["q"]) for t in trades if not t["m"])
    sell_vol = sum(float(t["q"]) for t in trades if t["m"])
    return buy_vol - sell_vol


def _classify(series: list[float]) -> dict:
    """分析 CVD 序列方向、强度、梯度"""
    if not series:
        return {"direction": "UNKNOWN", "strength": 0, "gradient": 0.0, "score": 0}

    recent = sum(series[-5:])
    total  = sum(series)
    n      = len(series)

    # 梯度：后半段 vs 前半段
    half = n // 2
    front = sum(series[:half]) if half else 0
    back  = sum(series[half:]) if half else 0
    gradient = (back - front) / (abs(front) + 1e-9)

    direction = "SELL" if recent < 0 else "BUY"
    # 强度：连续同向比例
    same_dir = sum(1 for v in series[-5:] if (v < 0) == (recent < 0))
    strength = same_dir  # 0~5

    # 评分：连续性 + 梯度加强
    score = strength
    if abs(gradient) > 0.3 and (gradient < 0) == (recent < 0):
        score += 1  # 加速

    return {
        "direction":  direction,
        "strength":   strength,
        "gradient":   round(gradient, 3),
        "recent_sum": round(recent, 2),
        "score":      min(score, 6),
    }


def get_multi_tf_cvd(symbol: str) -> dict:
    """
    主接口：获取多周期 CVD 分析
    返回 micro / meso / macro 三层 + 综合评分
    """
    sym = symbol.upper()
    if not sym.endswith("USDT"):
        sym += "USDT"

    # 三个周期
    micro_series = _taker_cvd(sym, "5m",  12) or _kline_cvd(sym, "5m",  12)
    meso_series  = _taker_cvd(sym, "1h",  24) or _kline_cvd(sym, "1h",  24)
    macro_series = _taker_cvd(sym, "4h",  14) or _kline_cvd(sym, "4h",  14)

    micro = _classify(micro_series)
    meso  = _classify(meso_series)
    macro = _classify(macro_series)

    # 微观绝对量（aggTrades）
    spot_cvd = _aggTrades_cvd(sym)

    # 当前价格趋势（1H）
    k1h = _get(f"{FAPI}/fapi/v1/klines?symbol={sym}&interval=1h&limit=6", ttl=60)
    if k1h and len(k1h) >= 2:
        price_up = float(k1h[-1][4]) > float(k1h[0][4])
    else:
        price_up = None

    # 背离检测
    divergence = None
    divergence_type = None
    if price_up is not None and macro["direction"] != "UNKNOWN":
        macro_buy = macro["direction"] == "BUY"
        if price_up and not macro_buy:
            divergence = True
            divergence_type = "BEARISH_DIV"   # 价格涨但宏观CVD降 → 顶背离
        elif not price_up and macro_buy:
            divergence = True
            divergence_type = "BULLISH_DIV"   # 价格跌但宏观CVD升 → 底背离

    # 综合评分（供 enhanced_signal_engine 调用）
    def score_for_dir(direction: str) -> tuple[int, list[str]]:
        is_sell = direction == "SHORT"
        notes = []
        s = 0
        macro_match = (macro["direction"] == "SELL") == is_sell
        meso_match  = (meso["direction"]  == "SELL") == is_sell
        micro_match = (micro["direction"] == "SELL") == is_sell

        if macro_match and meso_match:
            s += 6; notes.append(f"CVD宏观+中期{'卖方' if is_sell else '买方'}主导 +6")
        elif macro_match and micro_match:
            s += 4; notes.append(f"CVD宏观+微观{'卖方' if is_sell else '买方'}主导 +4")
        elif macro_match:
            s += 3; notes.append(f"CVD宏观{'卖方' if is_sell else '买方'}主导 +3")
        elif meso_match:
            s += 2; notes.append(f"CVD中期{'卖方' if is_sell else '买方'}主导 +2")

        # 背离加分
        if divergence:
            if is_sell and divergence_type == "BEARISH_DIV":
                s += 2; notes.append("CVD顶背离 +2")
            elif not is_sell and divergence_type == "BULLISH_DIV":
                s += 2; notes.append("CVD底背离 +2")

        return min(s, 8), notes

    long_score,  long_notes  = score_for_dir("LONG")
    short_score, short_notes = score_for_dir("SHORT")

    return {
        "symbol":     sym,
        "micro":      micro,
        "meso":       meso,
        "macro":      macro,
        "spot_cvd":   round(spot_cvd, 2),
        "divergence": divergence,
        "divergence_type": divergence_type,
        "price_up":   price_up,
        "scores": {
            "LONG":  {"score": long_score,  "notes": long_notes},
            "SHORT": {"score": short_score, "notes": short_notes},
        },
    }


def cvd_score_for_signal(symbol: str, direction: str) -> tuple[int, list[str]]:
    """
    简化接口：供 enhanced_signal_engine.enhanced_score() 调用
    返回 (score, notes)
    """
    try:
        result = get_multi_tf_cvd(symbol)
        d = direction.upper()
        if d in ("做多", "LONG"):
            d = "LONG"
        elif d in ("做空", "SHORT"):
            d = "SHORT"
        else:
            return 0, []
        entry = result["scores"].get(d, {})
        return entry.get("score", 0), entry.get("notes", [])
    except Exception:
        return 0, []


if __name__ == "__main__":
    import sys
    symbols = sys.argv[1:] if len(sys.argv) > 1 else ["ETHUSDT", "BTCUSDT"]
    for sym in symbols:
        print(f"\n{'='*55}")
        print(f"  {sym} 多周期 CVD")
        print('='*55)
        r = get_multi_tf_cvd(sym)
        print(f"  Macro(4H): {r['macro']['direction']}  strength={r['macro']['strength']}  gradient={r['macro']['gradient']}")
        print(f"  Meso (1H): {r['meso']['direction']}  strength={r['meso']['strength']}")
        print(f"  Micro(5M): {r['micro']['direction']}  strength={r['micro']['strength']}")
        print(f"  Spot CVD:  {r['spot_cvd']:+.2f}")
        print(f"  背离: {r['divergence_type'] or '无'}")
        print(f"  SHORT评分: {r['scores']['SHORT']['score']}  {r['scores']['SHORT']['notes']}")
        print(f"  LONG 评分: {r['scores']['LONG']['score']}   {r['scores']['LONG']['notes']}")
