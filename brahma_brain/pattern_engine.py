#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║  梵天大脑 · pattern_engine.py  · P2a 形态门派引擎                ║
║  识别：旗形/楔形/三角/谐波/双顶底/头肩                           ║
║  评分贡献：形态成熟度 0~15分                                      ║
╚══════════════════════════════════════════════════════════════════╝
"""
import statistics
from typing import Optional

# ═══════════════════════════════════════════════════════════════
# 一、旗形/楔形识别
# ═══════════════════════════════════════════════════════════════

def detect_flag_wedge(highs: list, lows: list, closes: list,
                      signal_dir: str, lookback: int = 20) -> dict:
    """
    旗形：强趋势后横盘收紧，等待突破
    楔形：收紧三角，方向性突破前
    """
    if len(closes) < lookback:
        return {'pattern': None, 'score': 0, 'note': ''}

    h = highs[-lookback:]
    l = lows[-lookback:]
    c = closes[-lookback:]

    # 波动收窄率（ATR 衰减）
    atr_early = statistics.mean([h[i]-l[i] for i in range(lookback//2)])
    atr_late  = statistics.mean([h[i]-l[i] for i in range(lookback//2, lookback)])
    tighten_ratio = atr_late / atr_early if atr_early > 0 else 1.0

    # 高点/低点趋势
    h_slope = (h[-1] - h[0]) / len(h)
    l_slope = (l[-1] - l[0]) / len(l)

    pattern = None
    score   = 0
    note    = ''

    if tighten_ratio < 0.65:
        # 波动大幅收窄
        if h_slope < 0 and l_slope > 0:
            pattern = 'SYMMETRICAL_TRIANGLE'
            score   = 8
            note    = '对称三角收紧，等待突破方向'
        elif h_slope < -0.0001 and l_slope < -0.0001 and abs(h_slope) > abs(l_slope):
            pattern = 'DESCENDING_WEDGE'
            score   = 10 if signal_dir == 'LONG' else 3
            note    = '下降楔形，看涨形态' if signal_dir == 'LONG' else '下降楔形，做多更优'
        elif h_slope > 0.0001 and l_slope > 0.0001 and l_slope > h_slope:
            pattern = 'ASCENDING_WEDGE'
            score   = 10 if signal_dir == 'SHORT' else 3
            note    = '上升楔形，看跌形态' if signal_dir == 'SHORT' else '上升楔形，做空更优'
        else:
            pattern = 'TIGHT_RANGE'
            score   = 5
            note    = '极度压缩，蓄力等突破'
    elif tighten_ratio < 0.80:
        # 波动温和收窄
        price_trend = (c[-1] - c[0]) / c[0] * 100
        if abs(price_trend) > 2 and tighten_ratio < 0.75:
            pattern = 'FLAG'
            score   = 8
            is_bull = price_trend > 0
            note    = f"{'多头' if is_bull else '空头'}旗形，趋势延续形态"

    return {'pattern': pattern, 'score': score, 'note': note,
            'tighten_ratio': round(tighten_ratio, 3)}


# ═══════════════════════════════════════════════════════════════
# 二、双顶/双底识别
# ═══════════════════════════════════════════════════════════════

def detect_double_pattern(highs: list, lows: list, closes: list,
                          signal_dir: str, lookback: int = 30) -> dict:
    if len(highs) < lookback:
        return {'pattern': None, 'score': 0, 'note': ''}

    h = highs[-lookback:]
    l = lows[-lookback:]
    tol = 0.015  # 1.5% 容差

    # 双顶：两个相近高点
    max1_idx = h.index(max(h))
    h2 = h[:]
    h2[max1_idx] = 0
    max2_idx = h2.index(max(h2))
    if max1_idx != max2_idx:
        h1, h2v = h[max1_idx], h[max2_idx]
        if abs(h1 - h2v) / h1 < tol and max(max1_idx, max2_idx) > lookback * 0.6:
            if signal_dir == 'SHORT':
                return {'pattern': 'DOUBLE_TOP', 'score': 12,
                        'note': f'双顶${h1:.1f}≈${h2v:.1f}，强看跌信号'}

    # 双底：两个相近低点
    min1_idx = l.index(min(l))
    l2 = l[:]
    l2[min1_idx] = float('inf')
    min2_idx = l2.index(min(l2))
    if min1_idx != min2_idx:
        l1, l2v = l[min1_idx], l[min2_idx]
        if abs(l1 - l2v) / l1 < tol and max(min1_idx, min2_idx) > lookback * 0.6:
            if signal_dir == 'LONG':
                return {'pattern': 'DOUBLE_BOTTOM', 'score': 12,
                        'note': f'双底${l1:.1f}≈${l2v:.1f}，强看涨信号'}

    return {'pattern': None, 'score': 0, 'note': ''}


# ═══════════════════════════════════════════════════════════════
# 三、头肩形（反转信号）
# ═══════════════════════════════════════════════════════════════

def detect_head_shoulders(highs: list, lows: list,
                          signal_dir: str, lookback: int = 40) -> dict:
    if len(highs) < lookback:
        return {'pattern': None, 'score': 0, 'note': ''}

    h = highs[-lookback:]
    tol = 0.02

    # 简化版：寻找左肩-头-右肩结构（三个局部高点）
    peaks = []
    for i in range(2, len(h)-2):
        if h[i] > h[i-1] and h[i] > h[i-2] and h[i] > h[i+1] and h[i] > h[i+2]:
            peaks.append((i, h[i]))

    if len(peaks) >= 3:
        # 取最高的三个峰
        peaks_sorted = sorted(peaks, key=lambda x: x[1], reverse=True)[:3]
        peaks_sorted = sorted(peaks_sorted, key=lambda x: x[0])  # 按时间排序
        if len(peaks_sorted) == 3:
            ls, head, rs = peaks_sorted
            # 头部高于两肩
            if head[1] > ls[1] * (1 + tol) and head[1] > rs[1] * (1 + tol):
                # 两肩基本等高
                if abs(ls[1] - rs[1]) / ls[1] < tol * 2:
                    if signal_dir == 'SHORT':
                        return {'pattern': 'HEAD_SHOULDERS', 'score': 13,
                                'note': f'头肩顶，头部${head[1]:.1f}，强看跌'}

    # 反向：头肩底
    l = lows[-lookback:]
    troughs = []
    for i in range(2, len(l)-2):
        if l[i] < l[i-1] and l[i] < l[i-2] and l[i] < l[i+1] and l[i] < l[i+2]:
            troughs.append((i, l[i]))
    if len(troughs) >= 3:
        tr_sorted = sorted(troughs, key=lambda x: x[1])[:3]
        tr_sorted = sorted(tr_sorted, key=lambda x: x[0])
        if len(tr_sorted) == 3:
            ls, head, rs = tr_sorted
            if head[1] < ls[1] * (1 - tol) and head[1] < rs[1] * (1 - tol):
                if abs(ls[1] - rs[1]) / ls[1] < tol * 2:
                    if signal_dir == 'LONG':
                        return {'pattern': 'INV_HEAD_SHOULDERS', 'score': 13,
                                'note': f'头肩底，头部${head[1]:.1f}，强看涨'}

    return {'pattern': None, 'score': 0, 'note': ''}


# ═══════════════════════════════════════════════════════════════
# 四、主接口：形态综合评分
# ═══════════════════════════════════════════════════════════════

def pattern_score(highs: list, lows: list, closes: list,
                  signal_dir: str) -> dict:
    """
    综合形态评分，最高15分
    """
    results = []

    # 旗形/楔形（短周期）
    fw = detect_flag_wedge(highs, lows, closes, signal_dir, 20)
    if fw['pattern']:
        results.append(fw)

    # 双顶/双底
    db = detect_double_pattern(highs, lows, closes, signal_dir, 30)
    if db['pattern']:
        results.append(db)

    # 头肩
    hs = detect_head_shoulders(highs, lows, signal_dir, 40)
    if hs['pattern']:
        results.append(hs)

    if not results:
        return {'score': 0, 'patterns': [], 'note': '无明显形态'}

    # 取最高分形态（不叠加，避免重复）
    best = max(results, key=lambda x: x['score'])
    all_patterns = [r['pattern'] for r in results if r['pattern']]

    return {
        'score':    min(best['score'], 15),
        'patterns': all_patterns,
        'best':     best['pattern'],
        'note':     best['note'],
    }


if __name__ == '__main__':
    # 快速自测
    import math, random
    random.seed(42)
    n = 60
    prices = [100 + math.sin(i/5)*2 + random.uniform(-0.5,0.5) for i in range(n)]
    highs  = [p * 1.005 for p in prices]
    lows   = [p * 0.995 for p in prices]
    r = pattern_score(highs, lows, prices, 'LONG')
    print(f"形态评分: {r['score']}/15  {r['patterns']}  {r['note']}")
