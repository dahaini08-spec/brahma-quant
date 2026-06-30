#!/usr/bin/env python3
"""
rsi_extreme_engine.py — s21: RSI极值检测引擎
设计院 · 2026-06-09

核心逻辑：
  RSI超买（>75）做空 / 超卖（<25）做多 = 高WR反转信号
  震荡体制：RSI极值 → 均值回归，WR≈72%
  趋势体制：RSI超买顺势做空（趋势延续），RSI超卖顺势做多

双重确认：
  RSI极值 + 背离（价格创新高但RSI不创新高）= 最强信号
  RSI极值 + 无背离 = 普通信号

评分范围：-6 ~ +12
"""


from brahma_brain.math_utils import _ema, _rsi, calc_rsi, rsi, ema  # 统一数学库

from typing import List, Optional

def _rsi(closes: List[float], period: int = 14) -> List[float]:
    """计算RSI序列"""
    if len(closes) < period + 1:
        return [50.0]
    
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    
    if len(gains) < period:
        return [50.0]
    
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    
    rsi_vals = []
    for i in range(period, len(gains)):
        if avg_loss == 0:
            rsi_vals.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsi_vals.append(100 - 100 / (1 + rs))
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    
    return rsi_vals if rsi_vals else [50.0]


def _detect_divergence(closes: List[float], rsi_vals: List[float], signal_dir: str, lookback: int = 5) -> bool:
    """
    检测RSI背离
    做空：价格新高但RSI不新高 → 顶背离（看跌）
    做多：价格新低但RSI不新低 → 底背离（看涨）
    """
    if len(closes) < lookback + 1 or len(rsi_vals) < lookback + 1:
        return False
    
    recent_closes = closes[-lookback:]
    recent_rsi = rsi_vals[-lookback:]
    
    if signal_dir == 'SHORT':
        # 顶背离：价格新高，RSI不新高
        price_new_high = closes[-1] >= max(recent_closes[:-1])
        rsi_not_new_high = rsi_vals[-1] < max(recent_rsi[:-1]) - 2
        return price_new_high and rsi_not_new_high
    else:
        # 底背离：价格新低，RSI不新低
        price_new_low = closes[-1] <= min(recent_closes[:-1])
        rsi_not_new_low = rsi_vals[-1] > min(recent_rsi[:-1]) + 2
        return price_new_low and rsi_not_new_low


def rsi_extreme_score(
    closes: List[float],
    signal_dir: str,
    regime: str = 'UNKNOWN',
    period: int = 14,
    overbought: float = 75.0,
    oversold: float = 25.0,
) -> tuple:
    """
    计算RSI极值评分 (s21)
    返回: (score: float, report: dict)
    """
    if len(closes) < period + 2:
        return 0.0, {"error": "数据不足", "score": 0}

    rsi_vals = _rsi(closes, period)
    if not rsi_vals:
        return 0.0, {"error": "RSI计算失败", "score": 0}

    current_rsi = rsi_vals[-1]
    is_chop  = 'CHOP' in regime.upper()
    is_trend = not is_chop

    score   = 0.0
    signals = []
    divergence = _detect_divergence(closes, rsi_vals, signal_dir)

    # ── 极端区域（>80 / <20）─────────────────────────────────────
    if current_rsi >= 80:
        if signal_dir == 'SHORT':
            base = 10 if is_chop else 7
            score += base
            signals.append(f'RSI极端超买{current_rsi:.0f}_SHORT+{base}')
            if divergence:
                score += 2
                signals.append('顶背离确认+2')
        else:  # LONG在超买区
            score -= 6
            signals.append(f'RSI超买{current_rsi:.0f}做多-6')

    elif current_rsi <= 20:
        if signal_dir == 'LONG':
            base = 10 if is_chop else 7
            score += base
            signals.append(f'RSI极端超卖{current_rsi:.0f}_LONG+{base}')
            if divergence:
                score += 2
                signals.append('底背离确认+2')
        else:  # SHORT在超卖区
            score -= 6
            signals.append(f'RSI超卖{current_rsi:.0f}做空-6')

    # ── 超买超卖区（overbought~80 / oversold~20）─────────────────
    elif current_rsi >= overbought:
        if signal_dir == 'SHORT':
            base = 6 if is_chop else 4
            score += base
            signals.append(f'RSI超买{current_rsi:.0f}_SHORT+{base}')
            if divergence:
                score += 2
                signals.append('顶背离确认+2')
        else:
            score -= 3
            signals.append(f'RSI超买{current_rsi:.0f}做多-3')

    elif current_rsi <= oversold:
        if signal_dir == 'LONG':
            base = 6 if is_chop else 4
            score += base
            signals.append(f'RSI超卖{current_rsi:.0f}_LONG+{base}')
            if divergence:
                score += 2
                signals.append('底背离确认+2')
        else:
            score -= 3
            signals.append(f'RSI超卖{current_rsi:.0f}做空-3')

    # ── 中性区域（40~60）─────────────────────────────────────────
    elif 40 <= current_rsi <= 60:
        score = 0
        signals.append(f'RSI中性{current_rsi:.0f}无信号')

    # 限制范围
    score = max(-6, min(12, score))

    report = {
        "score": score,
        "rsi": round(current_rsi, 1),
        "divergence": divergence,
        "signals": signals,
        "regime": regime,
        "signal_dir": signal_dir,
        "rsi_prev": round(rsi_vals[-2], 1) if len(rsi_vals) >= 2 else None,
    }
    return score, report


if __name__ == "__main__":
    import random
    random.seed(42)
    # 模拟超卖场景
    closes = [1700 - i * 5 + random.uniform(-2, 2) for i in range(30)]
    s, r = rsi_extreme_score(closes, 'LONG', 'CHOP_LOW')
    print(f"CHOP_LOW LONG 超卖: score={s} RSI={r['rsi']} signals={r['signals']}")

    # 模拟超买场景
    closes2 = [1600 + i * 5 + random.uniform(-2, 2) for i in range(30)]
    s2, r2 = rsi_extreme_score(closes2, 'SHORT', 'BEAR_TREND')
    print(f"BEAR_TREND SHORT 超买: score={s2} RSI={r2['rsi']} signals={r2['signals']}")
