#!/usr/bin/env python3
"""
causal_regime_verifier — 因果体制验证器 stub
设计院 2026-09-16 | 模块原始版本已废弃，此stub保持接口兼容

接口: verify(symbol, regime, signal_dir, ms, timeout_ms) -> dict
返回: {'score_adj': int, 'verdict': str, 'causal_confidence': float}

规则映射（基于MEMORY.md死穴铁律）:
- BEAR_TREND + LONG → score_adj=-30, verdict=REJECTED (WR=45%死穴)
- BEAR_TREND + SHORT → score_adj=0, verdict=STRONG (顺势)
- CHOP_MID + ANY → score_adj=-5, verdict=MODERATE (不确定)
- BULL_TREND + SHORT → score_adj=-15, verdict=MODERATE (逆势)
- BULL_TREND + LONG → score_adj=0, verdict=STRONG (顺势)
- 其他 → score_adj=0, verdict=NEUTRAL
"""
import time


def verify(symbol, regime, signal_dir, ms=None, timeout_ms=5000) -> dict:
    """因果体制验证 stub — 基于宪法铁律的静态规则"""
    _ = symbol  # unused
    regime = (regime or '').upper()
    signal_dir = (signal_dir or '').upper()
    ms = ms or {}

    # 死穴: BEAR_TREND_LONG WR=45%
    if regime == 'BEAR_TREND' and signal_dir == 'LONG':
        return {
            'score_adj': -30,
            'verdict': 'REJECTED',
            'causal_confidence': 0.95,
            'reason': 'BEAR_TREND_LONG WR=45% 死穴',
        }

    # 顺势: BEAR_TREND + SHORT
    if regime == 'BEAR_TREND' and signal_dir == 'SHORT':
        return {
            'score_adj': 0,
            'verdict': 'STRONG',
            'causal_confidence': 0.80,
            'reason': '顺势做空',
        }

    # 逆势: BULL_TREND + SHORT
    if regime == 'BULL_TREND' and signal_dir == 'SHORT':
        return {
            'score_adj': -15,
            'verdict': 'MODERATE',
            'causal_confidence': 0.65,
            'reason': '逆势做空',
        }

    # 顺势: BULL_TREND + LONG
    if regime == 'BULL_TREND' and signal_dir == 'LONG':
        return {
            'score_adj': 0,
            'verdict': 'STRONG',
            'causal_confidence': 0.80,
            'reason': '顺势做多',
        }

    # CHOP_MID: 不确定
    if regime == 'CHOP_MID':
        return {
            'score_adj': -5,
            'verdict': 'MODERATE',
            'causal_confidence': 0.50,
            'reason': 'CHOP_MID 不确定',
        }

    # BEAR_RECOVERY + SHORT 严禁
    if 'BEAR_RECOVERY' in regime and signal_dir == 'SHORT':
        return {
            'score_adj': -25,
            'verdict': 'REJECTED',
            'causal_confidence': 0.90,
            'reason': 'BEAR_RECOVERY_SHORT 严禁',
        }

    # BEAR_RECOVERY + LONG
    if 'BEAR_RECOVERY' in regime and signal_dir == 'LONG':
        return {
            'score_adj': 0,
            'verdict': 'STRONG',
            'causal_confidence': 0.75,
            'reason': 'BEAR_RECOVERY 顺势做多',
        }

    # 默认
    return {
        'score_adj': 0,
        'verdict': 'NEUTRAL',
        'causal_confidence': 0.50,
        'reason': f'{regime}+{signal_dir} 无特殊规则',
    }
