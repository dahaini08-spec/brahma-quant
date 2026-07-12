#!/usr/bin/env python3
"""
recovery_unlocker.py — CORRECTION/RECOVERY 体制解锁器
设计院 · 达摩院 封印 2026-07-12 | P2修复

职责：
  当体制处于 BEAR_RECOVERY / BULL_EARLY / 过渡期 时，
  若 Kronos 数据支持，激活解锁奖励分（最高 +8）
  供 brahma_core s23 段调用

接口：
  check_unlock(regime, direction, base_score, kronos_meta, symbol) -> dict
    返回 {'unlocked': bool, 'regime': str, 'reason': str, 's23_bonus': int}
"""

from __future__ import annotations
import os
import sys
import time
from typing import Optional

# ── 体制解锁映射表 ──────────────────────────────────────────
# key: (regime, direction)
# val: (unlock_score_threshold, s23_bonus, unlock_reason)
_UNLOCK_TABLE = {
    ('BEAR_RECOVERY', 'LONG'):    (120, 8,  'BEAR_RECOVERY+LONG双证据激活最高WR'),
    ('BULL_EARLY',    'LONG'):    (130, 6,  'BULL_EARLY初涨阶段Kronos顺势确认'),
    ('BULL_EARLY',    'SHORT'):   (140, 4,  'BULL_EARLY反弹顶部做空'),
    ('BEAR_EARLY',    'SHORT'):   (130, 6,  'BEAR_EARLY初跌阶段Kronos顺势确认'),
    ('CHOP_MID',      'LONG'):    (150, 3,  'CHOP震荡Kronos做多确认(高阈值)'),
    ('CHOP_MID',      'SHORT'):   (150, 3,  'CHOP震荡Kronos做空确认(高阈值)'),
}

# Kronos p_up 解锁条件
_P_UP_LONG_THRESHOLD  = 0.62   # 做多：p_up > 0.62 才解锁
_P_UP_SHORT_THRESHOLD = 0.38   # 做空：p_up < 0.38 才解锁


def check_unlock(
    regime: str,
    direction: str,
    base_score: float,
    kronos_meta: Optional[dict] = None,
    symbol: str = '',
) -> dict:
    """
    检查是否满足体制解锁条件。

    Args:
        regime:      当前体制字符串（如 'BEAR_RECOVERY'）
        direction:   信号方向 ('LONG' / 'SHORT')
        base_score:  当前confluence分数
        kronos_meta: kronos_lite / kronos_engine 返回的 meta dict
                     期望含 {'p_up': float, 'source': str, ...}
        symbol:      标的代码（日志用）

    Returns:
        {
            'unlocked':  bool,
            'regime':    str,
            'reason':    str,
            's23_bonus': int,   # 0 if not unlocked
        }
    """
    result = {
        'unlocked':  False,
        'regime':    regime,
        'reason':    '',
        's23_bonus': 0,
    }

    if not regime or not direction:
        return result

    key = (regime.upper(), direction.upper())
    entry = _UNLOCK_TABLE.get(key)
    if entry is None:
        return result

    score_threshold, bonus, reason = entry

    # 分数门槛检查
    if base_score < score_threshold:
        result['reason'] = f'score={base_score:.0f} < 阈值{score_threshold}，解锁未触发'
        return result

    # Kronos 方向确认（若有）
    if kronos_meta:
        p_up = float(kronos_meta.get('p_up', 0.5) or 0.5)
        source = kronos_meta.get('source', 'unknown')

        if direction.upper() == 'LONG' and p_up < _P_UP_LONG_THRESHOLD:
            result['reason'] = (
                f'Kronos p_up={p_up:.2f} < {_P_UP_LONG_THRESHOLD}，'
                f'做多解锁需要更强上涨概率'
            )
            return result

        if direction.upper() == 'SHORT' and p_up > _P_UP_SHORT_THRESHOLD:
            result['reason'] = (
                f'Kronos p_up={p_up:.2f} > {_P_UP_SHORT_THRESHOLD}，'
                f'做空解锁需要更强下跌概率'
            )
            return result

        reason += f' | Kronos p_up={p_up:.2f} src={source}'

    # 全部条件通过 → 解锁
    result.update({
        'unlocked':  True,
        'regime':    regime,
        'reason':    reason,
        's23_bonus': bonus,
    })
    return result


# ── CLI 测试入口 ────────────────────────────────────────────
if __name__ == '__main__':
    tests = [
        ('BEAR_RECOVERY', 'LONG',  130, {'p_up': 0.68, 'source': 'kronos_lite'}),
        ('BULL_EARLY',    'LONG',  125, {'p_up': 0.55, 'source': 'kronos_lite'}),
        ('BEAR_RECOVERY', 'LONG',  115, {'p_up': 0.70, 'source': 'kronos_lite'}),
        ('CHOP_MID',      'LONG',  155, {'p_up': 0.60, 'source': 'lgbm'}),
        ('BULL_TREND',    'LONG',  160, {'p_up': 0.75, 'source': 'lgbm'}),
    ]
    print('=== recovery_unlocker 测试 ===')
    for regime, direction, score, meta in tests:
        r = check_unlock(regime, direction, score, meta, 'TEST')
        status = '🔓 UNLOCK' if r['unlocked'] else '🔒 LOCKED'
        print(f'  {regime} {direction} score={score}: {status} bonus={r["s23_bonus"]} | {r["reason"][:60]}')
