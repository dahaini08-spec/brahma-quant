#!/usr/bin/env python3
"""
rsi_extreme_engine.py — RSI极值检测评分 (s21)
[设计院封印 2026-08-11 苏摩111]

重新实现：纯Python，无外部依赖
RSI来源：ms.momentum.rsi_1h / ms.rsi_1h / 自行计算
输出：int -8 ~ +10
"""


def rsi_extreme_score(ms: dict, signal_dir: str = 'SHORT') -> int:
    """
    RSI极值检测评分

    SHORT:
      RSI_1H > 75 (极度超买) → +10
      RSI_1H > 68            → +6
      RSI_1H > 60            → +3
      RSI_1H 45~60 (中性)    →  0
      RSI_1H < 35            → -4  (超卖，空单危险)
      RSI_1H < 25            → -8

    LONG: 镜像
    """
    try:
        mom = ms.get('momentum', {})
        rsi = None
        if mom:
            rsi = mom.get('rsi_1h') or mom.get('rsi_14')
        if rsi is None:
            rsi = ms.get('rsi_1h') or ms.get('rsi_14')
        if rsi is None:
            # 从klines计算
            closes = ms.get('closes_1h') or ms.get('raw_closes') or []
            if len(closes) >= 15:
                rsi = _calc_rsi(closes, period=14)
        if rsi is None:
            return 0

        rsi = float(rsi)
        is_short = 'SHORT' in signal_dir.upper()

        if is_short:
            if rsi > 75:  return +10
            if rsi > 68:  return  +6
            if rsi > 60:  return  +3
            if rsi > 45:  return   0
            if rsi > 35:  return  -4
            return -8
        else:  # LONG
            if rsi < 25:  return +10
            if rsi < 32:  return  +6
            if rsi < 40:  return  +3
            if rsi < 55:  return   0
            if rsi < 65:  return  -4
            return -8

    except Exception:
        return 0


def _calc_rsi(closes: list, period: int = 14) -> float:
    """简单RSI计算（Wilder平滑）"""
    if len(closes) < period + 1:
        return 50.0
    arr = [float(c) for c in closes[-(period + 1):]]
    gains, losses = [], []
    for i in range(1, len(arr)):
        d = arr[i] - arr[i-1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    avg_g = sum(gains) / period
    avg_l = sum(losses) / period
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return 100.0 - (100.0 / (1.0 + rs))
