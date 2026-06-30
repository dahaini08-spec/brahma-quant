#!/usr/bin/env python3
"""
volume_ratio_engine.py — s22: 成交量比率引擎
设计院 · 2026-06-09

核心逻辑：
  量价关系是价格行为的灵魂
  
  放量突破 + 顺势 = 信号可靠度+（趋势确认）
  缩量回撤 + 顺势 = 入场好时机（趋势中继）
  放量反转 = 主力换手，方向改变（反转信号）
  缩量横盘 = 没有信号，不参与（CHOP无效）

量比分级：
  >3.0：超大量（主力异动）
  2.0~3.0：大量（趋势加速）
  1.5~2.0：放量（正常趋势）
  0.8~1.5：正常量
  <0.8：缩量（观望/衰竭）
  <0.5：极度缩量（市场死寂）

评分范围：-5 ~ +8
"""

from typing import List

def _volume_ratio(volumes: List[float], period: int = 20) -> float:
    """当前量 / MA(period)均量"""
    if len(volumes) < 2:
        return 1.0
    current = volumes[-1]
    avg_vol = sum(volumes[-min(period+1, len(volumes)):-1]) / min(period, len(volumes)-1)
    if avg_vol == 0:
        return 1.0
    return current / avg_vol


def _is_up_candle(open_: float, close: float) -> bool:
    return close > open_


def volume_ratio_score(
    closes:  List[float],
    opens:   List[float],
    volumes: List[float],
    signal_dir: str,
    regime:  str = 'UNKNOWN',
    period:  int = 20,
) -> tuple:
    """
    计算成交量比率评分 (s22)
    返回: (score: float, report: dict)
    """
    if len(closes) < 3 or len(volumes) < 3:
        return 0.0, {"error": "数据不足", "score": 0}

    vr = _volume_ratio(volumes, period)
    is_chop  = 'CHOP' in regime.upper()
    is_trend = not is_chop

    # 最近一根K线方向
    last_up = _is_up_candle(opens[-1], closes[-1])
    
    # 价格趋势（最近3根方向）
    up_count   = sum(1 for i in range(-3, 0) if closes[i] > opens[i])
    down_count = 3 - up_count
    price_trend_up   = up_count >= 2
    price_trend_down = down_count >= 2

    score   = 0.0
    signals = []

    # ── 超大量（主力异动）> 3.0 ─────────────────────────────────
    if vr >= 3.0:
        if signal_dir == 'SHORT' and (last_up or price_trend_up):
            # 放量拉升后做空：主力出货信号
            score += 6
            signals.append(f'超大量{vr:.1f}x拉升后做空+6（主力出货）')
        elif signal_dir == 'LONG' and (not last_up or price_trend_down):
            # 放量下跌后做多：恐慌抛售
            score += 6
            signals.append(f'超大量{vr:.1f}x下跌做多+6（恐慌抛售）')
        elif signal_dir == 'SHORT' and price_trend_down:
            # 放量下跌顺势做空
            score += 4
            signals.append(f'超大量{vr:.1f}x放量下跌做空+4')
        elif signal_dir == 'LONG' and price_trend_up:
            score += 4
            signals.append(f'超大量{vr:.1f}x放量上涨做多+4')

    # ── 大量（2.0~3.0）──────────────────────────────────────────
    elif vr >= 2.0:
        if signal_dir == 'SHORT' and price_trend_down:
            score += 5 if is_trend else 3
            signals.append(f'大量{vr:.1f}x下跌做空+{5 if is_trend else 3}')
        elif signal_dir == 'LONG' and price_trend_up:
            score += 5 if is_trend else 3
            signals.append(f'大量{vr:.1f}x上涨做多+{5 if is_trend else 3}')
        elif signal_dir == 'SHORT' and price_trend_up:
            # 放量拉升做空：反转信号
            score += 4
            signals.append(f'大量{vr:.1f}x拉升反转做空+4')
        elif signal_dir == 'LONG' and price_trend_down:
            score += 4
            signals.append(f'大量{vr:.1f}x下跌反转做多+4')

    # ── 放量（1.5~2.0）──────────────────────────────────────────
    elif vr >= 1.5:
        if (signal_dir == 'SHORT' and price_trend_down) or \
           (signal_dir == 'LONG' and price_trend_up):
            score += 3
            signals.append(f'放量{vr:.1f}x顺势+3')
        else:
            score += 1
            signals.append(f'放量{vr:.1f}x逆势+1')

    # ── 缩量（<0.8）──────────────────────────────────────────────
    elif vr < 0.8:
        if is_chop:
            # 震荡缩量：价格将突破或假突破，SCALP机会
            score += 2
            signals.append(f'震荡缩量{vr:.1f}x SCALP机会+2')
        elif is_trend:
            # 趋势缩量回撤：好的入场时机
            if (signal_dir == 'SHORT' and not price_trend_down) or \
               (signal_dir == 'LONG' and not price_trend_up):
                score += 3
                signals.append(f'趋势缩量回撤{vr:.1f}x入场+3')
            else:
                # 趋势缩量继续：衰竭信号
                score -= 2
                signals.append(f'趋势缩量{vr:.1f}x衰竭-2')
        
        # 极度缩量
        if vr < 0.5:
            score -= 2
            signals.append(f'极度缩量{vr:.1f}x市场死寂-2')

    # ── 正常量（0.8~1.5）─────────────────────────────────────────
    else:
        score = 0
        signals.append(f'正常量{vr:.1f}x无加分')

    # 限制范围
    score = max(-5, min(8, score))

    report = {
        "score": score,
        "volume_ratio": round(vr, 2),
        "price_trend_up": price_trend_up,
        "price_trend_down": price_trend_down,
        "last_up": last_up,
        "signals": signals,
        "regime": regime,
        "signal_dir": signal_dir,
    }
    return score, report


if __name__ == "__main__":
    import random
    random.seed(42)
    n = 25
    closes  = [1700 - i * 3 for i in range(n)]
    opens   = [c + random.uniform(-5, 5) for c in closes]
    volumes = [500 + random.uniform(-100, 100) for _ in range(n)]
    volumes[-1] = 2000  # 放量
    closes[-1]  = closes[-2] - 20  # 下跌
    
    s, r = volume_ratio_score(closes, opens, volumes, 'SHORT', 'BEAR_TREND')
    print(f"BEAR_TREND SHORT 放量下跌: score={s} vr={r['volume_ratio']} signals={r['signals']}")

    volumes[-1] = 150   # 缩量
    s2, r2 = volume_ratio_score(closes, opens, volumes, 'SHORT', 'CHOP_LOW')
    print(f"CHOP_LOW SHORT 缩量: score={s2} vr={r2['volume_ratio']} signals={r2['signals']}")
