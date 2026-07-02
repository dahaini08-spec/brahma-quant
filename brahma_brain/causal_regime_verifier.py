#!/usr/bin/env python3
"""
causal_regime_verifier.py — 梵天因果体制验证器
Brahma-Quant Open Source v3.0 | 设计院封印 2026-07-02

⚠️  PRO 版说明
════════════════════════════════════════════════
因果验证器对信号方向与当前体制的因果一致性进行检验，
输出 score_adj（分数调整）。

Pro 版私有内容：
  - 死穴判定逻辑（BEAR_TREND_LONG WR=45% → 强惩罚）
  - 具体惩罚值（CausalVerifier: BLOCKED -12）
  - statsmodels 因果检验的置信度阈值（0.32）
  - 各体制组合的精确 score_adj 矩阵

开源版：提供框架骨架 + 方向性一致性检查（顺势正调整，逆势负调整）
Pro 版：完整因果验证 + 死穴封禁 + 统计显著性检验
════════════════════════════════════════════════
"""
import os
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# ── 死穴定义（方向性，Pro 版包含精确惩罚值） ─────────────────────
# 宪法原则：BEAR_TREND_LONG WR=45% → 严禁
_DEAD_ZONES = {
    ('BEAR_TREND', 'LONG'),    # WR=45% 死穴
    ('BULL_TREND', 'SHORT'),   # 逆势空
    ('BEAR_TREND_LONG', 'LONG'),  # 别名
}

# 顺势组合（正调整）
_FAVORABLE = {
    ('BEAR_TREND',    'SHORT'),
    ('BULL_TREND',    'LONG'),
    ('BEAR_RECOVERY', 'LONG'),
    ('BEAR_EARLY',    'SHORT'),
}

# 开源版调整值（Pro 版：从统计矩阵精确读取）
_OSS_SCORE_ADJ = {
    'dead_zone':    -25,   # Pro版: 更精确的分层惩罚
    'adverse':      -12,   # Pro版: CausalVerifier -12
    'neutral':        0,
    'favorable':     +5,   # Pro版: 顺势加分
}


def verify(symbol: str,
           regime: str,
           signal_dir: str,
           ms: Optional[Dict] = None,
           timeout_ms: int = 5000,
           **kwargs) -> Dict[str, Any]:
    """
    因果体制验证

    Args:
        symbol:     交易对
        regime:     当前体制
        signal_dir: 'LONG' 或 'SHORT'
        ms:         市场状态数据（可选）
        timeout_ms: 超时毫秒

    Returns:
        {
          'verdict':    'PASS'|'WARN'|'BLOCKED',
          'score_adj':  分数调整值（负=惩罚，正=加分）,
          'reason':     判定原因,
          'is_dead_zone': bool,
        }

    Pro 版：利用 statsmodels 做 Granger 因果检验，
           conf > 0.32 才认为因果成立（避免噪音）
    """
    try:
        combo = (regime, signal_dir)

        # 死穴检查（宪法级）
        if combo in _DEAD_ZONES:
            return {
                'verdict':      'BLOCKED',
                'score_adj':    _OSS_SCORE_ADJ['dead_zone'],
                'reason':       f'死穴: {regime}+{signal_dir} WR过低，严禁入场',
                'is_dead_zone': True,
                '_pro_note':    'Pro版含精确分层惩罚逻辑',
            }

        # 顺势检查
        if combo in _FAVORABLE:
            return {
                'verdict':      'PASS',
                'score_adj':    _OSS_SCORE_ADJ['favorable'],
                'reason':       f'顺势: {regime}+{signal_dir} 方向一致',
                'is_dead_zone': False,
            }

        # 逆势（非死穴）
        # 判断是否完全逆势
        is_bear = 'BEAR' in regime
        is_bull = 'BULL' in regime
        is_adverse = (is_bear and signal_dir == 'LONG') or (is_bull and signal_dir == 'SHORT')

        if is_adverse:
            return {
                'verdict':      'WARN',
                'score_adj':    _OSS_SCORE_ADJ['adverse'],
                'reason':       f'逆势警告: {regime}+{signal_dir}',
                'is_dead_zone': False,
                '_pro_note':    'Pro版含statsmodels因果检验(conf>0.32)',
            }

        # 中性（CHOP等）
        return {
            'verdict':      'PASS',
            'score_adj':    _OSS_SCORE_ADJ['neutral'],
            'reason':       f'中性体制: {regime}',
            'is_dead_zone': False,
        }

    except Exception as e:
        logger.error(f"[CausalVerifier] verify 异常: {e}", exc_info=True)
        return {
            'verdict':      'PASS',
            'score_adj':    0,
            'reason':       f'验证异常(安全通过): {e}',
            'is_dead_zone': False,
        }
