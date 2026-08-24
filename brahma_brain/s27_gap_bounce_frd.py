"""
s27_gap_bounce_frd.py — 统计模式维度（设计院封印 2026-07-03）
  s27: Gap Up（跳空向上）
  s28: Bounce Setup（超卖反弹形态）
  s29: First Red Day（首根红K回调）

签名：
  s27_gap_up(sym, klines_1h, regime) -> int  (-8 ~ +8)
  s28_bounce_setup(sym, klines_1h, klines_4h, regime) -> int  (-5 ~ +10)
  s29_first_red_day(sym, klines_1h, regime) -> int  (-5 ~ +8)

klines 格式：[{'open':x, 'high':x, 'low':x, 'close':x, 'volume':x}, ...]  时间升序
"""

from __future__ import annotations
from typing import List, Dict


def _safe_float(v, default=0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def _get_ohlcv(k: dict):
    o = _safe_float(k.get('open'))
    h = _safe_float(k.get('high'))
    l = _safe_float(k.get('low'))
    c = _safe_float(k.get('close'))
    v = _safe_float(k.get('volume'))
    return o, h, l, c, v


# ─────────────────────────────────────────────────────────────
# s27: Gap Up 跳空向上
#   定义：当前K的最低价 > 前一K的最高价（上方跳空缺口）
#   BULL体制 gap且缺口>0.3% → +8（轧空动力）
#   BEAR体制 gap → -8（回补风险）
#   其他体制 → +4 / -4
# ─────────────────────────────────────────────────────────────
def s27_gap_up(sym: str, klines_1h: List[Dict], regime: str) -> int:
    if not klines_1h or len(klines_1h) < 2:
        return 0
    try:
        k_prev = klines_1h[-2]
        k_curr = klines_1h[-1]
        _, prev_h, _, prev_c, _ = _get_ohlcv(k_prev)
        curr_o, _, curr_l, curr_c, _ = _get_ohlcv(k_curr)

        if prev_h <= 0 or prev_c <= 0:
            return 0

        gap_pct = (curr_l - prev_h) / prev_h if curr_l > prev_h else 0.0

        if gap_pct <= 0:
            return 0

        reg = (regime or '').upper()
        if gap_pct >= 0.003:
            if 'BULL' in reg:
                return +8   # 牛市跳空，轧空动力强
            elif 'BEAR' in reg:
                return -8   # 熊市跳空，缺口回补风险
            else:
                return +4   # 震荡体制，中性偏多
        elif gap_pct >= 0.001:
            if 'BULL' in reg:
                return +4
            elif 'BEAR' in reg:
                return -4
            else:
                return +2
        return 0
    except Exception:
        return 0


# ─────────────────────────────────────────────────────────────
# s28: Bounce Setup 超卖反弹形态
#   定义：1H连续2根或以上阴线后出现长下影线（下影 > K线实体×1.5）
#         且4H处于支撑区（价格在20MA附近±1.5%）
#   BULL/BEAR_RECOVERY体制 → +10
#   CHOP体制 → +5
#   BEAR_TREND体制（反弹做多风险）→ -5
# ─────────────────────────────────────────────────────────────
def s28_bounce_setup(sym: str, klines_1h: List[Dict], klines_4h: List[Dict], regime: str) -> int:
    if not klines_1h or len(klines_1h) < 5:
        return 0
    try:
        reg = (regime or '').upper()

        # 检测最近K线是否有长下影线（锤子线形态）
        k_curr = klines_1h[-1]
        o, h, l, c, _ = _get_ohlcv(k_curr)
        body = abs(c - o)
        lower_shadow = min(o, c) - l if l > 0 else 0
        upper_shadow = h - max(o, c)

        is_hammer = (
            lower_shadow > 0
            and body >= 0
            and lower_shadow >= body * 1.5
            and upper_shadow < lower_shadow * 0.5
        )

        if not is_hammer:
            return 0

        # 检测前2~4根是否连续阴线
        bearish_count = 0
        for k in klines_1h[-4:-1]:
            ko, _, _, kc, _ = _get_ohlcv(k)
            if kc < ko:
                bearish_count += 1

        if bearish_count < 2:
            return 0

        # 检测4H MA20支撑（简化：近20根4H均价）
        near_ma20 = True
        if klines_4h and len(klines_4h) >= 20:
            closes_4h = [_safe_float(k.get('close')) for k in klines_4h[-20:]]
            ma20 = sum(closes_4h) / len(closes_4h) if closes_4h else 0
            if ma20 > 0:
                dist_pct = abs(c - ma20) / ma20
                near_ma20 = dist_pct <= 0.015

        if not near_ma20:
            return 0

        # 体制打分
        if 'BULL' in reg or 'RECOVERY' in reg:
            return +10
        elif 'CHOP' in reg:
            return +5
        elif 'BEAR_TREND' in reg:
            return -5   # 熊市反弹做多危险
        else:
            return +4
    except Exception:
        return 0


# ─────────────────────────────────────────────────────────────
# s29: First Red Day 首根红K回调
#   定义：前3~5根连续阳线后，出现第一根阴线（回调信号）
#   做空方向：+8（首次回调做空，WR高）
#   做多方向：-5（顺势多单注意回调风险）
#   BEAR体制做空 → 加成×1.2，上限+8
#   BULL体制做多 → -3（警示，不封禁）
# ─────────────────────────────────────────────────────────────
def s29_first_red_day(sym: str, klines_1h: List[Dict], regime: str) -> int:
    if not klines_1h or len(klines_1h) < 6:
        return 0
    try:
        reg = (regime or '').upper()

        # 当前K是否为阴线
        k_curr = klines_1h[-1]
        o, h, l, c, _ = _get_ohlcv(k_curr)
        is_red = c < o and (o - c) / o > 0.001  # 至少0.1%跌幅才算

        if not is_red:
            return 0

        # 前3~5根是否连续阳线
        bullish_count = 0
        for k in klines_1h[-6:-1]:
            ko, _, _, kc, _ = _get_ohlcv(k)
            if kc > ko:
                bullish_count += 1
            else:
                break  # 连续中断则停止

        if bullish_count < 3:
            return 0

        # 是首根红K（确认连涨后首次回调）
        if 'BEAR' in reg:
            return +8   # 熊市首根红K做空：强信号
        elif 'BULL' in reg:
            return -3   # 牛市回调提示，不封禁
        elif 'CHOP' in reg:
            return +5   # 震荡首根红K做空
        else:
            return +4
    except Exception:
        return 0
