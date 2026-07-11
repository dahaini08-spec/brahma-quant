#!/usr/bin/env python3
"""
confluence_by_tf.py — 多时间框架汇合评分引擎 P2-A
设计院六方联合 | 苏摩111批准 2026-07-11

职责:
  接收 confluence_score() 的 breakdown 字典，
  按各维度所属时间框架（15M/1H/4H/1D）进行加权汇合，
  输出 tf_weighted_score 和 tf_boost（注入 brahma_core 的 extra_data）

  现有 brahma_core 后处理已用 get_score_multiplier() 做标的层级调整（P2-A基础版）
  本模块补充：各技术维度的跨周期共振奖励（+0 ~ +8分）

维度 → 周期归属表:
  1H维度: RSI_1H / EMA趋势1H / OB_1H / 成交量1H / MACD_1H
  4H维度: OB_4H / ATR_4H / 趋势一致性4H / 关键位4H
  多周期: multitf共振 (1H+4H对齐加分)
  1D维度: 宏观趋势 / 量价背离1D

共振规则:
  同向信号在 ≥2 个时间框架出现 → +3分奖励（每对）
  ≥3 个时间框架同向 → +6分（三周期共振）
  4个时间框架同向 → +8分（全周期共振，极罕见）
  上限: +8分
"""
from __future__ import annotations
import sys
from pathlib import Path

_root = Path(__file__).parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from brahma_brain.confluence_tf_weights import get_tf_weights, _get_tier

# ── 维度→周期映射表 ──────────────────────────────────────
# breakdown key → 主要时间框架标签
_DIM_TF_MAP: dict[str, str] = {
    # 1H 维度
    '趋势一致性':    '1h',
    'EMA顺势':       '1h',
    'RSI超卖超买':   '1h',
    'RSI极端加分':   '1h',
    'MACD金叉死叉':  '1h',
    'MACD零轴位置':  '1h',
    '布林带反弹':    '1h',
    '布林带收缩突破':'1h',
    '量价配合':      '1h',
    '成交量比率':    '1h',
    'OBV方向':       '1h',
    'OB_1H_LONG':    '1h',
    'OB_1H_SHORT':   '1h',
    'OB新鲜度_1H':   '1h',
    # 4H 维度
    '关键位精确度':  '4h',
    'OB_4H_LONG':    '4h',
    'OB_4H_SHORT':   '4h',
    'OB新鲜度_4H':   '4h',
    '多周期对齐':    '4h',
    '形态成熟度':    '4h',
    # 宏观维度
    '宏观+事件':     '1d',
    '宏观趋势':      '1d',
    # 清算/OI (跨周期)
    '清算/OI':       '1h',
    '研究增强层':    '4h',
    # 其他
    '动量背离':      '1h',
    'MACD背离':      '1h',
}

# 有效信号判断门槛（各维度得分>此值才算"方向一致"）
_TF_SIGNAL_THRESHOLD = 3


def get_tf_consensus(breakdown: dict, direction: str) -> dict:
    """
    从breakdown中提取各时间框架的方向共识

    Returns:
        {
          '15m': 0,   # 有效信号数
          '1h':  3,
          '4h':  2,
          '1d':  1,
          'resonance_count': 2,  # 共振的时间框架数
          'tf_boost': 4,         # 共振奖励分
          'summary': '1H+4H双周期共振',
        }
    """
    tf_signals: dict[str, int] = {'15m': 0, '1h': 0, '4h': 0, '1d': 0}

    for dim_key, score in breakdown.items():
        if not isinstance(score, (int, float)):
            continue
        tf = _DIM_TF_MAP.get(dim_key)
        if tf and score >= _TF_SIGNAL_THRESHOLD:
            tf_signals[tf] += 1

    # 共振计算（有效信号的时间框架数）
    active_tfs = [tf for tf, cnt in tf_signals.items() if cnt >= 1]
    resonance_count = len(active_tfs)

    # 共振奖励
    if resonance_count >= 4:
        tf_boost = 8
        summary = '四周期全共振 +8'
    elif resonance_count >= 3:
        tf_boost = 6
        summary = f"三周期共振({'+'.join(t.upper() for t in active_tfs)}) +6"
    elif resonance_count >= 2:
        tf_boost = 3
        summary = f"双周期共振({'+'.join(t.upper() for t in active_tfs)}) +3"
    else:
        tf_boost = 0
        summary = f"单周期({active_tfs[0].upper() if active_tfs else 'NONE'})"

    return {
        **tf_signals,
        'active_tfs':       active_tfs,
        'resonance_count':  resonance_count,
        'tf_boost':         tf_boost,
        'summary':          summary,
    }


def apply_tf_confluence(
    score: float,
    breakdown: dict,
    symbol: str,
    direction: str,
    primary_tf: str = '1h',
    signal_source: str = 'default',
) -> tuple[float, dict]:
    """
    P2-A 主入口: 在 confluence_score 输出后注入多TF共振奖励

    Args:
        score:          原始 confluence_score 总分
        breakdown:      confluence_score 的维度明细
        symbol:         合约名
        direction:      LONG / SHORT
        primary_tf:     主要入场周期
        signal_source:  信号来源类型

    Returns:
        (adjusted_score, tf_meta)
        tf_meta 注入 extra_data['tf_confluence']
    """
    tf_result = get_tf_consensus(breakdown, direction)
    tf_boost   = tf_result['tf_boost']
    tier       = _get_tier(symbol)
    weights    = get_tf_weights(symbol, primary_tf, signal_source)

    # L4/L5小币减半奖励（高周期信号可信度低）
    if tier >= 4:
        tf_boost = tf_boost // 2

    adjusted_score = score + tf_boost

    tf_meta = {
        **tf_result,
        'tier':           tier,
        'weights':        weights,
        'original_score': score,
        'tf_boost':       tf_boost,
        'adjusted_score': adjusted_score,
        'primary_tf':     primary_tf,
        'signal_source':  signal_source,
    }

    return adjusted_score, tf_meta


# ── 快速验证 ─────────────────────────────────────────────
if __name__ == '__main__':
    test_breakdown = {
        '趋势一致性':    15,  # 1H
        'RSI超卖超买':   8,   # 1H
        'MACD金叉死叉':  6,   # 1H
        '关键位精确度':  12,  # 4H
        'OB_4H_SHORT':   10,  # 4H
        '宏观+事件':     5,   # 1D
        '多周期对齐':    8,   # 4H
        '清算/OI':       6,   # 1H
    }

    for sym, tf, src in [
        ('BTCUSDT', '1h', 'ob_trend'),
        ('ETHUSDT', '4h', 'ob_trend'),
        ('REUSDT',  '15m','pump_auto'),
        ('XPINUSDT','1h', 'default'),
    ]:
        adj, meta = apply_tf_confluence(140.0, test_breakdown, sym, 'SHORT', tf, src)
        print(f"{sym:12s} tf={tf:4s} src={src:12s} "
              f"score=140→{adj:.0f}(+{meta['tf_boost']}) "
              f"[{meta['summary']}] tier=L{meta['tier']}")
