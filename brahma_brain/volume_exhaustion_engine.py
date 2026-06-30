#!/usr/bin/env python3
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
