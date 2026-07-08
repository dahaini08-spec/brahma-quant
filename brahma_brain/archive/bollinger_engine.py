#!/usr/bin/env python3
"""

# ── STATUS: AUXILIARY ──────────────────────────────────────────
# 布林带指标，被market_state调用
# LAST_REVIEW: 2026-07-01 | 属于辅助计算层，修改前确认调用链
# ─────────────────────────────────────────────────────────────
bollinger_engine.py — s20: 布林带偏离度引擎
设计院 · 2026-06-09

核心逻辑：
  布林带3σ外 = 价格极端偏离，均值回归概率高
  震荡体制：偏离越大 → 反向信号越强（SCALP）
  趋势体制：偏离越大 → 趋势延续信号（TREND）

评分范围：-8 ~ +10
  趋势体制做空在上轨外：+8（顺势超卖）
  震荡体制在下轨外做多：+10（均值回归）
  价格在中轨附近：0（无信号）
  逆势：-5（惩罚）
"""


from brahma_brain.math_utils import _ema, _rsi, calc_rsi, rsi, ema  # 统一数学库

import math
from typing import Optional, List

def _ema(data: List[float], period: int) -> List[float]:
    """指数移动平均"""
    if len(data) < period:
        return [data[-1]] * len(data) if data else []
    result = [sum(data[:period]) / period]
    k = 2 / (period + 1)
    for d in data[period:]:
        result.append(d * k + result[-1] * (1 - k))
    return result

def _sma(data: List[float], period: int) -> float:
    """简单移动平均"""
    if len(data) < period:
        return sum(data) / len(data)
    return sum(data[-period:]) / period

def _stdev(data: List[float], period: int) -> float:
    """标准差"""
    if len(data) < period:
        return 0.0
    sub = data[-period:]
    mean = sum(sub) / len(sub)
    variance = sum((x - mean) ** 2 for x in sub) / len(sub)
    return math.sqrt(variance)

def bollinger_score(
    closes: List[float],        # 收盘价序列（至少20根）
    signal_dir: str,            # 'SHORT' or 'LONG'
    regime: str = 'UNKNOWN',    # 体制
    period: int = 20,           # 布林带周期
    std_mult: float = 2.0,      # 标准差倍数
) -> tuple:
    """
    计算布林带偏离评分 (s20)
    返回: (score: float, report: dict)
    """
    if len(closes) < period:
        return 0.0, {"error": "数据不足", "score": 0}

    mid = _sma(closes, period)
    sd  = _stdev(closes, period)
    price = closes[-1]

    if sd == 0 or mid == 0:
        return 0.0, {"error": "标准差为0", "score": 0}

    upper = mid + std_mult * sd
    lower = mid - std_mult * sd
    upper3 = mid + 3.0 * sd
    lower3 = mid - 3.0 * sd

    # 偏离度：+1=上轨，-1=下轨，0=中轨
    bb_pos = (price - mid) / (std_mult * sd)

    is_chop = 'CHOP' in regime.upper()
    is_trend = 'TREND' in regime.upper() or 'BEAR' in regime.upper() or 'BULL' in regime.upper()

    score = 0.0
    signals = []

    # ── 极端偏离（3σ外）──────────────────────────────────────────
    if price > upper3:
        if signal_dir == 'SHORT':
            if is_chop:
                score += 10   # 震荡3σ外做空：均值回归最强信号
                signals.append('CHOP_3σ_SHORT+10')
            else:
                score += 6    # 趋势超买做空：顺势
                signals.append('TREND_3σ_SHORT+6')
        else:  # LONG
            score -= 6        # 价格在3σ上轨做多：逆势惩罚
            signals.append('3σ上轨做多-6')

    elif price < lower3:
        if signal_dir == 'LONG':
            if is_chop:
                score += 10   # 震荡3σ外做多：均值回归最强
                signals.append('CHOP_3σ_LONG+10')
            else:
                score += 6
                signals.append('TREND_3σ_LONG+6')
        else:  # SHORT
            score -= 6
            signals.append('3σ下轨做空-6')

    # ── 2σ~3σ区间 ────────────────────────────────────────────────
    elif price > upper:
        if signal_dir == 'SHORT':
            score += 5 if is_chop else 3
            signals.append(f'2σ上轨SHORT+{5 if is_chop else 3}')
        else:
            score -= 3
            signals.append('2σ上轨做多-3')

    elif price < lower:
        if signal_dir == 'LONG':
            score += 5 if is_chop else 3
            signals.append(f'2σ下轨LONG+{5 if is_chop else 3}')
        else:
            score -= 3
            signals.append('2σ下轨做空-3')

    # ── 中轨附近（无信号区）────────────────────────────────────
    else:
        score = 0
        signals.append('中轨附近无信号')

    # 限制范围
    score = max(-8, min(10, score))

    report = {
        "score": score,
        "bb_pos": round(bb_pos, 3),
        "price": price,
        "upper": round(upper, 4),
        "lower": round(lower, 4),
        "upper3": round(upper3, 4),
        "lower3": round(lower3, 4),
        "mid": round(mid, 4),
        "signals": signals,
        "regime": regime,
        "signal_dir": signal_dir,
    }
    return score, report


if __name__ == "__main__":
    # 简单测试
    import random
    random.seed(42)
    closes = [1700 + random.uniform(-50, 50) for _ in range(25)]
    closes[-1] = 1580  # 模拟下轨外
    s, r = bollinger_score(closes, 'LONG', 'CHOP_LOW')
    print(f"测试CHOP_LOW LONG 下轨外: score={s} signals={r['signals']}")
    s2, r2 = bollinger_score(closes, 'SHORT', 'BEAR_TREND')
    closes[-1] = 1850  # 上轨外
    s3, r3 = bollinger_score(closes, 'SHORT', 'CHOP_HIGH')
    print(f"测试CHOP_HIGH SHORT 上轨外: score={s3} signals={r3['signals']}")
