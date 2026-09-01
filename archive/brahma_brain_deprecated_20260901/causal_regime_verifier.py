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
    'dead_zone':        -25,   # 真正熊市中段 RSI_4H<40 — 宦法级死穴
    'dead_zone_border': -15,   # 体制边界 40≤RSI_4H<50 — 六方封印 2026-07-30
    'dead_zone_weak':    -8,   # 体制塬化期 RSI_4H≥50 — 死穴豆免通道
    'adverse':          -12,   # Pro版: CausalVerifier -12
    'neutral':            0,
    'favorable':         +5,   # Pro版: 顺势加分
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

        # 死穴检查（宦法级）—六方封印 2026-07-30：加入RSI_4H边界豆免条件
        if combo in _DEAD_ZONES:
            # BEAR_TREND+LONG 边界体制动态豆免：当RSI_4H在边界区不再简单-25
            rsi_4h = float((ms or {}).get('rsi_4h', (ms or {}).get('RSI_4H', 50)) or 50)
            if combo == ('BEAR_TREND', 'LONG') and rsi_4h >= 50:
                # RSI_4H≥50：体制塬化期，岞弱惩罚
                adj_key = 'dead_zone_weak'
                verdict = 'WARN'
                reason = f'体制塬化期豆免: {regime}+{signal_dir} RSI_4H={rsi_4h:.0f}≥50，容许CHoCH入场'
            elif combo == ('BEAR_TREND', 'LONG') and rsi_4h >= 40:
                # RSI_4H 40~50：边界区，渐进惩罚
                adj_key = 'dead_zone_border'
                verdict = 'WARN'
                reason = f'边界体制豆免: {regime}+{signal_dir} RSI_4H={rsi_4h:.0f}⋈40~50，惩罚减半'
            elif combo == ('BULL_TREND', 'SHORT') and ms:
                # [P2 2026-08-23 苏摩111] BULL_TREND+SHORT 超买极端区豁免
                # 条件：RSI_4H>73 + RSI_1D>72 + PD>82% → 升级为精准做空机会
                try:
                    _rsi_4h_p2 = float(ms.get('rsi_4h') or ms.get('RSI_4H') or 0)
                    _rsi_1d_p2 = float(ms.get('rsi_1d') or ms.get('RSI_1D') or 0)
                    _pd_pos_p2 = float(ms.get('pd_zone', {}).get('position', 0) if isinstance(ms.get('pd_zone'), dict) else 0)
                    _p2_ok = (_rsi_4h_p2 > 73 and _rsi_1d_p2 > 72 and _pd_pos_p2 > 0.82)
                    if _p2_ok:
                        return {
                            'verdict':      'WARN',
                            'score_adj':    +5,
                            'reason':       f'P2超买激活: BULL_TREND+SHORT RSI_4H={_rsi_4h_p2:.0f}>73 RSI_1D={_rsi_1d_p2:.0f}>72 PD={_pd_pos_p2:.2f}>0.82',
                            'is_dead_zone': False,
                            'p2_short':     True,
                            '_pro_note':    'P2 2026-08-23 苏摩111封印',
                        }
                except Exception:
                    pass
                # 超买条件不满足 → 维持死穴-25
                adj_key = 'dead_zone'
                verdict = 'BLOCKED'
                reason = f'死穴: {regime}+{signal_dir} 超买条件不满足 RSI_4H={rsi_4h:.0f}'
            else:
                # RSI_4H<40：真正熊市中段，保持死穴
                adj_key = 'dead_zone'
                verdict = 'BLOCKED'
                reason = f'死穴: {regime}+{signal_dir} RSI_4H={rsi_4h:.0f}<40，WR过低严禁入场'
            return {
                'verdict':            verdict,
                'score_adj':          _OSS_SCORE_ADJ[adj_key],
                'reason':             reason,
                'is_dead_zone':       adj_key == 'dead_zone',
                'causal_confidence':  rsi_4h / 100,
                '_pro_note':          'Pro版含精确分层惩罚逻辑|六方封印 2026-07-30',
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

        # [P2 2026-08-23 苏摩111封印] 超买极端区精准做空激活通道
        # 铁证：BULL_TREND做空 score=54.9 距门槛120差65分
        # 根因：regime_mult=0.5 + CausalVerifier-12 = 永久关闭
        # 修复：三重超买条件同时满足时 CausalVerifier从-12升级为+5
        # 激活条件（全部满足）：
        #   1. RSI_4H > 73（超买区）
        #   2. RSI_1D > 72（日线超买）
        #   3. PD区=PREMIUM>82%（溢价区，机构倒货带）
        # 设计意图：不开放常规做空，只在超买极端区触发
        if is_adverse and signal_dir == 'SHORT' and ('BULL' in regime) and ms:
            try:
                _rsi_4h  = float(ms.get('rsi_4h') or ms.get('RSI_4H') or 0)
                _rsi_1d  = float(ms.get('rsi_1d') or ms.get('RSI_1D') or 0)
                # PD区位置：0~1，>0.82=溢价区
                _pd_pos = float(ms.get('pd_zone', {}).get('position', 0) if isinstance(ms.get('pd_zone'), dict) else 0)
                _p2_activated = (_rsi_4h > 73 and _rsi_1d > 72 and _pd_pos > 0.82)
                if _p2_activated:
                    return {
                        'verdict':      'WARN',
                        'score_adj':    +5,  # 从-12翻转+5，净提升+17分
                        'reason':       f'P2超买激活: {regime}+SHORT RSI_4H={_rsi_4h:.0f}>73 RSI_1D={_rsi_1d:.0f}>72 PD={_pd_pos:.2f}>0.82',
                        'is_dead_zone': False,
                        'p2_short':     True,
                        '_pro_note':    'P2 2026-08-23 苏摩111封印',
                    }
            except Exception:
                pass  # P2异常不阻断主流程
        # [P2 END]

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
