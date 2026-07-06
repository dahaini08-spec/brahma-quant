#!/usr/bin/env python3
"""
timing_filter.py — 梵天时机过滤器
Brahma-Quant Open Source v3.0 | 设计院封印 2026-07-02

⚠️  PRO 版说明
════════════════════════════════════════════════
时机过滤器三层感知架构（开源框架版）：

  层1 — 价格位置评分  (0~40分)
  层2 — RSI_1H 评分   (0~35分)
  层3 — Kronos p_up  (0~20分)

  合计 ≥65 → READY | 40~64 → MONITOR | <40 → WAIT

Pro 版私有内容：
  - 各层精确权重（实盘统计后调参）
  - BTC单边下行豁免通道（4H连续3根收阴+RSI_4H<40 → WAIT阈值55）
  - BTC/ETH联动共振过滤乘数
  - 体制感知的动态阈值调整

框架完全公开，Pro 参数通过配置文件注入。
════════════════════════════════════════════════
"""
import os
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# ── 开源版默认阈值（Pro 版会覆盖） ───────────────────────────────
READY_THRESHOLD   = int(os.environ.get('TIMING_READY_THRESHOLD', '65'))
MONITOR_THRESHOLD = int(os.environ.get('TIMING_MONITOR_THRESHOLD', '40'))

# Pro 版体制感知阈值（开源版统一使用默认值）
_REGIME_THRESHOLDS = {
    'BEAR_TREND':    {'ready': 65, 'monitor': 40},  # Pro: 精调值
    'BULL_TREND':    {'ready': 60, 'monitor': 35},
    'CHOP_MID':      {'ready': 70, 'monitor': 45},
    'BEAR_EARLY':    {'ready': 65, 'monitor': 40},
    'BEAR_RECOVERY': {'ready': 62, 'monitor': 38},
}

_STATUS_BADGES = {
    'READY':   '🟢 READY',
    'MONITOR': '🟡 MONITOR',
    'WAIT':    '⏸ WAIT',
    'STANDBY': '⚫ STANDBY',
}


def _score_price_position(current_price: float, entry_lo: float,
                           entry_hi: float) -> int:
    """
    层1：价格位置评分 (0~40)

    价格在入场区间内 → 满分
    偏离越远 → 分数越低
    超过 6% → 0分（GapGate 信号过期机制）
    """
    try:
        if entry_hi <= 0:
            return 0
        mid = (entry_lo + entry_hi) / 2
        gap_pct = abs(current_price - mid) / mid * 100

        if gap_pct <= 1.0:   return 40
        if gap_pct <= 2.0:   return 35
        if gap_pct <= 3.0:   return 28
        if gap_pct <= 4.0:   return 18
        if gap_pct <= 6.0:   return 8
        return 0  # GapGate: 信号过期
    except Exception:
        return 0


def _score_rsi(rsi_1h: Optional[float], signal_dir: str) -> int:
    """
    层2：RSI_1H 评分 (0~35)

    做空：RSI 高位更佳（>60 友好）
    做多：RSI 低位更佳（<40 友好）

    Pro 版：结合 RSI_4H + EMA20_1H 价格位置（新宪法规则）
    """
    try:
        if rsi_1h is None:
            return 15  # 数据缺失给中性分

        if signal_dir == 'SHORT':
            if rsi_1h >= 70:  return 35
            if rsi_1h >= 65:  return 28
            if rsi_1h >= 55:  return 20
            if rsi_1h >= 45:  return 12
            return 5
        else:  # LONG
            if rsi_1h <= 30:  return 35
            if rsi_1h <= 35:  return 28
            if rsi_1h <= 45:  return 20
            if rsi_1h <= 55:  return 12
            return 5
    except Exception:
        return 10


def _score_kronos(p_up: Optional[float], signal_dir: str) -> int:
    """
    层3：Kronos p_up 评分 (0~20)

    p_up = 价格上涨概率 [0,1]
    做空：p_up 越低越好
    做多：p_up 越高越好

    Pro 版：接入 Kronos-mini 实时推理结果
    开源版：传入 None 时给中性分 10
    """
    try:
        if p_up is None:
            return 10

        if signal_dir == 'SHORT':
            if p_up <= 0.25:  return 20
            if p_up <= 0.35:  return 15
            if p_up <= 0.45:  return 10
            if p_up <= 0.55:  return 5
            return 0
        else:  # LONG
            if p_up >= 0.75:  return 20
            if p_up >= 0.65:  return 15
            if p_up >= 0.55:  return 10
            if p_up >= 0.45:  return 5
            return 0
    except Exception:
        return 10


def evaluate_timing(symbol: str,
                    signal_dir: str,
                    score: float,
                    grade: float,
                    entry_lo: float,
                    entry_hi: float,
                    current_price: float,
                    s23_p_up: Optional[float] = None,
                    rsi_1h: Optional[float] = None,
                    regime: Optional[str] = None,
                    **kwargs) -> Dict[str, Any]:
    """
    时机过滤器主入口

    Args:
        symbol:        交易对
        signal_dir:    'LONG' 或 'SHORT'
        score:         梵天信号总分
        grade:         结构质量分 (0~100)
        entry_lo/hi:   入场价格区间
        current_price: 当前价格
        s23_p_up:      Kronos 上涨概率 [0,1]（可选）
        rsi_1h:        当前 RSI_1H（可选）
        regime:        当前体制（可选）

    Returns:
        {
          'status': 'READY'|'MONITOR'|'WAIT'|'STANDBY',
          'score':  时机评分 (0~95),
          'badge':  展示标签,
          'breakdown': 各层评分明细,
        }
    """
    try:
        # 低质量信号直接 STANDBY
        if score < 120 or grade < 70:
            return {
                'status': 'STANDBY',
                'score': 0,
                'badge': _STATUS_BADGES['STANDBY'],
                'breakdown': {'reason': f'score={score}/grade={grade} 低于门槛'},
            }

        # 三层评分
        s_price  = _score_price_position(current_price, entry_lo, entry_hi)
        s_rsi    = _score_rsi(rsi_1h, signal_dir)
        s_kronos = _score_kronos(s23_p_up, signal_dir)
        total    = s_price + s_rsi + s_kronos

        # 体制感知阈值
        thresholds = _REGIME_THRESHOLDS.get(regime or '', {
            'ready': READY_THRESHOLD, 'monitor': MONITOR_THRESHOLD
        })
        ready_t   = thresholds['ready']
        monitor_t = thresholds['monitor']

        if total >= ready_t:
            status = 'READY'
        elif total >= monitor_t:
            status = 'MONITOR'
        else:
            status = 'WAIT'

        return {
            'status': status,
            'score': total,
            'badge': _STATUS_BADGES[status],
            'breakdown': {
                'price_position': s_price,
                'rsi_1h':         s_rsi,
                'kronos_p_up':    s_kronos,
                'total':          total,
                'ready_threshold': ready_t,
            },
        }

    except Exception as e:
        logger.error(f"[TimingFilter] evaluate_timing 异常: {e}", exc_info=True)
        return {'status': 'WAIT', 'score': 0,
                'badge': '⏸ WAIT', 'error': str(e)}


# [设计院 2026-07-06] format_timing_badge 兼容补丁
def format_timing_badge(timing_result: dict) -> str:
    """格式化时机徽章为可读字符串"""
    status = timing_result.get('status', 'UNKNOWN')
    score  = timing_result.get('score', 0)
    badge  = timing_result.get('badge', status)
    return f'  {badge} timing_score={score}'
