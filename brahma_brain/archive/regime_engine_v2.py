#!/usr/bin/env python3
"""
regime_engine_v2.py — 新体制识别引擎 v2.0
2026-09-12 苏摩111封印 · 推倒Hurst，改为价格位置+事件驱动

8种体制：
  BULL_TREND   价格在止损墙上方+大户多>60%
  BEAR_TREND   价格在支撑池下方+大户多<50%
  CHOP_RANGE   价格在支撑池和止损墙之间
  SQUEEZE      OI=SHORT_BUILD+价格接近止损墙=即将逼空
  LIQUIDATION  OI=LONG_UNWIND+价格接近支撑池=即将清算
  BREAKOUT     价格刚突破止损墙+1H收阳
  BREAKDOWN    价格刚跌破支撑池+1H收阴
  EVENT_BULL/BEAR CPI/FOMC后1H收阳/收阴+OI翻转
"""
from typing import Dict

def detect_regime(price: float, liq_short: float, liq_long: float,
                  oi_signal: str, big_long_pct: float,
                  event_type: str = '', recent_candle: str = '') -> Dict:
    """
    实时体制识别 — 不用Hurst，用价格位置+事件

    参数:
      price: 当前价格
      liq_short: 止损墙价位（上方）
      liq_long: 支撑池价位（下方）
      oi_signal: OI信号 (SHORT_BUILD/LONG_UNWIND/SHORT_SQUEEZE/LONG_BUILD)
      big_long_pct: 大户多仓比例
      event_type: 宏观事件 (CPI/FOMC/NFP/空)
      recent_candle: 最近1H K线方向 (GREEN/RED/空)

    返回:
      regime: 体制名
      direction: 方向 (LONG/SHORT/NONE)
      confidence: 置信度基础 (0~1)
      reason: 体制原因
    """
    # 距离计算
    dist_to_wall = (liq_short - price) / price if liq_short > 0 else 1.0
    dist_to_support = (price - liq_long) / price if liq_long > 0 else 1.0

    # ── 事件驱动优先（最高优先级）──
    if event_type and recent_candle:
        if recent_candle == 'GREEN' and oi_signal in ('SHORT_SQUEEZE', 'LONG_BUILD'):
            return {
                'regime': 'EVENT_BULL', 'direction': 'LONG',
                'confidence': 0.9, 'reason': f'{event_type}后1H收阳+OI={oi_signal}=事件做多'
            }
        elif recent_candle == 'RED' and oi_signal in ('LONG_UNWIND', 'SHORT_BUILD'):
            return {
                'regime': 'EVENT_BEAR', 'direction': 'SHORT',
                'confidence': 0.9, 'reason': f'{event_type}后1H收阴+OI={oi_signal}=事件做空'
            }

    # ── 突破检测 ──
    if liq_short > 0 and price > liq_short * 1.002 and recent_candle == 'GREEN':
        return {
            'regime': 'BREAKOUT', 'direction': 'LONG',
            'confidence': 0.85, 'reason': f'价格突破止损墙${liq_short:.0f}+1H收阳=突破做多'
        }
    if liq_long > 0 and price < liq_long * 0.998 and recent_candle == 'RED':
        return {
            'regime': 'BREAKDOWN', 'direction': 'SHORT',
            'confidence': 0.85, 'reason': f'价格跌破支撑池${liq_long:.0f}+1H收阴=跌破做空'
        }

    # ── 逼空/清算检测 ──
    if oi_signal == 'SHORT_BUILD' and 0 < dist_to_wall < 0.03:
        return {
            'regime': 'SQUEEZE', 'direction': 'LONG',
            'confidence': 0.75, 'reason': f'空头建仓+价格接近止损墙(±{dist_to_wall*100:.1f}%)=即将逼空'
        }
    if oi_signal == 'LONG_UNWIND' and 0 < dist_to_support < 0.03:
        return {
            'regime': 'LIQUIDATION', 'direction': 'SHORT',
            'confidence': 0.75, 'reason': f'多头撤退+价格接近支撑池(±{dist_to_support*100:.1f}%)=即将清算'
        }

    # ── 趋势检测（价格位置定义）──
    if liq_long > 0 and price < liq_long * 0.99 and big_long_pct < 50:
        return {
            'regime': 'BEAR_TREND', 'direction': 'SHORT',
            'confidence': 0.7, 'reason': f'价格在支撑池下方+大户多{big_long_pct:.0f}%<50%=空头趋势'
        }
    if liq_short > 0 and price > liq_short * 1.01 and big_long_pct > 60:
        return {
            'regime': 'BULL_TREND', 'direction': 'LONG',
            'confidence': 0.7, 'reason': f'价格在止损墙上方+大户多{big_long_pct:.0f}%>60%=多头趋势'
        }

    # ── 默认：区间震荡 ──
    return {
        'regime': 'CHOP_RANGE', 'direction': 'NONE',
        'confidence': 0.3, 'reason': f'价格在支撑池${liq_long:.0f}和止损墙${liq_short:.0f}之间=区间震荡'
    }


def calc_confidence(regime: str, resonance_score: int, oi_match: bool,
                    sm_match: bool, event_driven: bool) -> float:
    """
    计算置信度（替代score）
    共振数量+OI一致+大户一致+事件驱动 → 0~1.5
    """
    base = resonance_score / 5.0  # 5/5=1.0
    if oi_match: base += 0.2
    if sm_match: base += 0.2
    if event_driven: base += 0.3
    return min(base, 1.5)
