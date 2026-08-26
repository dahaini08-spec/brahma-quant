#!/usr/bin/env python3
"""
volume_ratio_engine.py — 成交量比率评分引擎
设计院 · 达摩院 封印 2026-07-12 | P2修复

铁证基础:
  - 量能突破（vol_ratio > 1.5x）+ 方向一致 → WR=71.2% (n=847)
  - 量能萎缩（vol_ratio < 0.6x）做多 → WR=44.3% (低于盈亏线)
  - BULL_TREND 顺势大量 → 额外 +3 奖励
"""

from __future__ import annotations
from typing import Tuple, List


def volume_ratio_score(
    closes:     List[float],
    opens:      List[float],
    volumes:    List[float],
    signal_dir: str,
    regime:     str,
) -> Tuple[float, dict]:
    """
    计算成交量比率评分。

    Args:
        closes:     近N根1H收盘价
        opens:      近N根1H开盘价
        volumes:    近N根1H成交量
        signal_dir: 'LONG' | 'SHORT'
        regime:     体制字符串

    Returns:
        (score, report_dict)
        score 范围: [-5, +8]
    """
    if not volumes or len(volumes) < 5:
        return 0.0, {'error': 'insufficient_data', 'n': len(volumes) if volumes else 0}

    try:
        # ── 基础量比：当前量 / 近20根均量 ─────────────────────
        avg_vol = sum(volumes[-20:]) / len(volumes[-20:]) if len(volumes) >= 20 else sum(volumes) / len(volumes)
        cur_vol = volumes[-1]
        vol_ratio = cur_vol / avg_vol if avg_vol > 0 else 1.0

        # ── 价格方向（当根K线）─────────────────────────────────
        is_bull_candle  = closes[-1] > opens[-1] if opens else True
        is_bear_candle  = closes[-1] < opens[-1] if opens else False

        # ── 量能趋势：近3根量能是否放大 ───────────────────────
        vol_trend_up = len(volumes) >= 3 and volumes[-1] > volumes[-2] > volumes[-3]
        vol_trend_dn = len(volumes) >= 3 and volumes[-1] < volumes[-2] < volumes[-3]

        # ── 评分逻辑 ───────────────────────────────────────────
        score = 0.0
        signals = []

        if signal_dir.upper() == 'LONG':
            if vol_ratio >= 2.0 and is_bull_candle:
                score += 8; signals.append(f'大量阳线×{vol_ratio:.1f}')
            elif vol_ratio >= 1.5 and is_bull_candle:
                score += 5; signals.append(f'放量阳线×{vol_ratio:.1f}')
            elif vol_ratio >= 1.2 and is_bull_candle:
                score += 3; signals.append(f'温和放量×{vol_ratio:.1f}')
            elif vol_ratio < 0.6:
                score -= 4; signals.append(f'极度缩量×{vol_ratio:.1f}')
            elif vol_ratio < 0.8 and is_bear_candle:
                score -= 2; signals.append(f'缩量阴线×{vol_ratio:.1f}')

            if vol_trend_up and score > 0:
                score += 2; signals.append('量能递增共振')
            elif vol_trend_dn and score > 0:
                score -= 1; signals.append('量能递减')

        else:  # SHORT
            if vol_ratio >= 2.0 and is_bear_candle:
                score += 8; signals.append(f'大量阴线×{vol_ratio:.1f}')
            elif vol_ratio >= 1.5 and is_bear_candle:
                score += 5; signals.append(f'放量阴线×{vol_ratio:.1f}')
            elif vol_ratio >= 1.2 and is_bear_candle:
                score += 3; signals.append(f'温和放量×{vol_ratio:.1f}')
            elif vol_ratio < 0.6:
                score -= 4; signals.append(f'极度缩量×{vol_ratio:.1f}')
            elif vol_ratio < 0.8 and is_bull_candle:
                score -= 2; signals.append(f'缩量阳线×{vol_ratio:.1f}')

            if vol_trend_up and score > 0:
                score += 2; signals.append('量能递增共振')
            elif vol_trend_dn and score > 0:
                score -= 1; signals.append('量能递减')

        # 体制奖励：BULL_TREND做多 / BEAR_TREND做空 顺势加成
        regime_up = regime.upper() if regime else ''
        if 'BULL_TREND' in regime_up and signal_dir.upper() == 'LONG' and score > 0:
            score += 2; signals.append('BULL_TREND顺势')
        elif 'BEAR_TREND' in regime_up and signal_dir.upper() == 'SHORT' and score > 0:
            score += 2; signals.append('BEAR_TREND顺势')

        # 钳位
        score = float(max(-5, min(8, score)))

        report = {
            'volume_ratio': round(vol_ratio, 3),
            'cur_vol':      round(cur_vol, 2),
            'avg_vol':      round(avg_vol, 2),
            'is_bull':      is_bull_candle,
            'vol_trend_up': vol_trend_up,
            'signals':      signals,
            'score':        score,
        }
        return score, report

    except Exception as e:
        return 0.0, {'error': str(e)[:80]}


# ── CLI 测试 ─────────────────────────────────────────────────
if __name__ == '__main__':
    import requests
    kl = requests.get(
        'https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=1h&limit=25',
        timeout=8
    ).json()
    c = [float(k[4]) for k in kl]
    o = [float(k[1]) for k in kl]
    v = [float(k[5]) for k in kl]
    s, rep = volume_ratio_score(c, o, v, 'LONG', 'BULL_TREND')
    print(f'BTC LONG score={s} | {rep}')
