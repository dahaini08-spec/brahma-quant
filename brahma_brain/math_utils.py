"""
math_utils.py — 梵天数学工具统一库
设计院·达摩院 深度排查 2026-06-29

问题根因：
  _ema 在 8 个文件中重复定义
  _rsi 在 7 个文件中重复定义
  calc_rsi 在 3 个文件中重复定义
  → 任何修复需要改 8 处，极易漏改、不一致

解决方案：
  统一到本文件，其他文件 from brahma_brain.math_utils import _ema, _rsi
  改一处 = 全系统生效
"""

from __future__ import annotations
import math
from typing import Sequence, Optional


# ─────────────────────────────────────────────────────────
# EMA
# ─────────────────────────────────────────────────────────

def _ema(series: Sequence[float], period: int) -> list[float]:
    """
    指数移动平均（Wilder/标准EMA）
    series: 收盘价序列（升序，最新在末尾）
    period: EMA周期
    返回：与 series 等长的 EMA 序列
    """
    if not series or period <= 0:
        return []
    k = 2.0 / (period + 1)
    result = [float(series[0])]
    for price in series[1:]:
        result.append(float(price) * k + result[-1] * (1 - k))
    return result


def ema(series: Sequence[float], period: int) -> float:
    """返回最新一根 EMA 值"""
    vals = _ema(series, period)
    return vals[-1] if vals else float('nan')


# ─────────────────────────────────────────────────────────
# RSI
# ─────────────────────────────────────────────────────────

def _rsi(series: Sequence[float], period: int = 14) -> list[float]:
    """
    RSI 序列（Wilder平滑）
    series: 收盘价序列（升序，最新在末尾）
    period: RSI周期（默认14）
    返回：RSI 序列（长度 = len(series) - period）
    """
    closes = [float(x) for x in series]
    if len(closes) < period + 1:
        return [50.0] * max(1, len(closes) - period)

    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains  = [max(d, 0) for d in deltas]
    losses = [max(-d, 0) for d in deltas]

    # 初始 Wilder 平均
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    results = []
    for i in range(period, len(deltas)):
        if avg_loss == 0:
            results.append(100.0)
        else:
            rs = avg_gain / avg_loss
            results.append(round(100 - 100 / (1 + rs), 2))
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    return results if results else [50.0]


def rsi(series: Sequence[float], period: int = 14) -> float:
    """返回最新一根 RSI 值（0~100）"""
    vals = _rsi(series, period)
    return vals[-1] if vals else 50.0


def calc_rsi(closes: Sequence[float], period: int = 14) -> float:
    """兼容别名（scripts/ 层大量使用）"""
    return rsi(closes, period)


# ─────────────────────────────────────────────────────────
# ATR
# ─────────────────────────────────────────────────────────

def _atr(highs: Sequence[float], lows: Sequence[float],
         closes: Sequence[float], period: int = 14) -> list[float]:
    """
    ATR 序列（Wilder平滑）
    返回与输入等长（首元素用 high-low 代替）
    """
    n = min(len(highs), len(lows), len(closes))
    if n < 2:
        return [highs[0] - lows[0]] if n == 1 else []

    trs = [highs[0] - lows[0]]
    for i in range(1, n):
        tr = max(highs[i] - lows[i],
                 abs(highs[i] - closes[i - 1]),
                 abs(lows[i] - closes[i - 1]))
        trs.append(tr)

    # Wilder 平滑
    atr_vals = [sum(trs[:period]) / period]
    for i in range(period, len(trs)):
        atr_vals.append((atr_vals[-1] * (period - 1) + trs[i]) / period)

    return atr_vals


def atr(highs: Sequence[float], lows: Sequence[float],
        closes: Sequence[float], period: int = 14) -> float:
    """返回最新 ATR 值"""
    vals = _atr(highs, lows, closes, period)
    return vals[-1] if vals else 0.0


# ─────────────────────────────────────────────────────────
# MACD
# ─────────────────────────────────────────────────────────

def macd(series: Sequence[float],
         fast: int = 12, slow: int = 26, signal: int = 9
         ) -> tuple[float, float, float]:
    """
    返回 (macd_line, signal_line, histogram) 最新值
    """
    if len(series) < slow + signal:
        return 0.0, 0.0, 0.0
    fast_ema  = _ema(series, fast)
    slow_ema  = _ema(series, slow)
    macd_line = [f - s for f, s in zip(fast_ema, slow_ema)]
    sig_line  = _ema(macd_line, signal)
    hist      = macd_line[-1] - sig_line[-1]
    return round(macd_line[-1], 6), round(sig_line[-1], 6), round(hist, 6)


# ─────────────────────────────────────────────────────────
# Bollinger Bands
# ─────────────────────────────────────────────────────────

def bollinger(series: Sequence[float], period: int = 20,
              stddev: float = 2.0) -> tuple[float, float, float]:
    """
    返回 (upper, mid, lower) 最新值
    """
    if len(series) < period:
        mid = float(series[-1]) if series else 0.0
        return mid, mid, mid
    window = [float(x) for x in series[-period:]]
    mid = sum(window) / period
    std = math.sqrt(sum((x - mid) ** 2 for x in window) / period)
    return round(mid + stddev * std, 6), round(mid, 6), round(mid - stddev * std, 6)


# ─────────────────────────────────────────────────────────
# 价格工具
# ─────────────────────────────────────────────────────────

def pct_change(old: float, new: float) -> float:
    """百分比变化（0~100 scale）"""
    if old == 0:
        return 0.0
    return round((new - old) / old * 100, 4)


def round_price(price: float, tick: float = 0.01) -> float:
    """按 tick_size 精度取整"""
    if tick <= 0:
        return price
    return round(round(price / tick) * tick, 10)


if __name__ == '__main__':
    # 快速自测
    import random
    prices = [100.0]
    for _ in range(50):
        prices.append(prices[-1] * (1 + random.uniform(-0.02, 0.02)))

    r = rsi(prices)
    e = ema(prices, 14)
    m, s, h = macd(prices)
    ub, mb, lb = bollinger(prices)

    print(f'RSI={r:.1f}  EMA14={e:.2f}  MACD={m:.4f}/{s:.4f}/{h:.4f}')
    print(f'Bollinger: upper={ub:.2f}  mid={mb:.2f}  lower={lb:.2f}')
    print('math_utils 自测通过 ✅')
