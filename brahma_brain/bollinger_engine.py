#!/usr/bin/env python3
"""
bollinger_engine.py — 布林带偏离度评分 (s20)
[设计院封印 2026-08-11 苏摩111]

重新实现：纯Python，无外部依赖
输入：ms dict
输出：int -8 ~ +10
"""
import math


def bollinger_score(ms: dict, signal_dir: str = 'SHORT') -> int:
    """
    布林带偏离度评分

    规则（封印 v4.0）:
      SHORT:
        价格 > 上轨 (>0.9)         → +8  (极端超买，空单顺势)
        价格 在上半段 (0.6~0.9)    → +4
        价格 在中间 (0.4~0.6)      → 0
        价格 在下半段 (0.1~0.4)    → -4  (多头区间，空单逆势)
        价格 < 下轨 (<0.1)         → -8
      LONG: 镜像
    """
    try:
        # 从 ms.momentum.bb 读取位置
        mom = ms.get('momentum', {})
        bb = mom.get('bb') if mom else None
        if bb:
            pos = float(bb.get('pos', 0.5))
            width = float(bb.get('width', 0.02))
        else:
            # 自行计算布林带位置
            closes = ms.get('closes_1h') or ms.get('raw_closes') or []
            if len(closes) < 20:
                return 0
            arr = [float(c) for c in closes[-20:]]
            mean = sum(arr) / len(arr)
            std  = math.sqrt(sum((x - mean)**2 for x in arr) / len(arr))
            if std < 1e-9:
                return 0
            price = float(ms.get('close', ms.get('price', mean)))
            upper = mean + 2 * std
            lower = mean - 2 * std
            rng   = upper - lower
            pos   = (price - lower) / rng if rng > 0 else 0.5
            width = rng / mean if mean > 0 else 0.02

        is_short = 'SHORT' in signal_dir.upper()

        if is_short:
            if pos > 0.90:   return +8
            if pos > 0.60:   return +4
            if pos > 0.40:   return  0
            if pos > 0.10:   return -4
            return -8
        else:  # LONG
            if pos < 0.10:   return +8
            if pos < 0.40:   return +4
            if pos < 0.60:   return  0
            if pos < 0.90:   return -4
            return -8

    except Exception:
        return 0
