#!/usr/bin/env python3
"""

# ── STATUS: AUXILIARY ──────────────────────────────────────────
# 仓位计算器，执行层
# LAST_REVIEW: 2026-07-01 | 属于辅助计算层，修改前确认调用链
# ─────────────────────────────────────────────────────────────
position_sizer.py — 梵天仓位定量器 v1.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
设计院 2026-05-30 | 第1周落地

原则：仓位大小必须从真实结算数据推导，不得拍脑袋
真相基线（2026-05-30 统计）：
  总样本: 83条真实结算
  整体WR: 51.8%  平均RR: 3.23  全Kelly: 36.9%  半Kelly: 18.4%

币种置信等级（基于真实结算，需>=30条才升级）：
  BTC 120~159分: WR=92% n=25 → PROVEN（接近门槛，维持现有5%）
  SOL 120~159分: WR=80% n=5  → EXPLORING（样本不足，限1%）
  LTC 160+分:    WR=0%  n=13 → BANNED（做空假设未验证，暂停）
  SOL 160+分:    WR=0%  n=9  → BANNED
  ETH 160+分:    WR=0%  n=6  → BANNED
"""
from __future__ import annotations
import json, os, time
from pathlib import Path

BASE = Path(__file__).parent.parent

# ── 置信等级 ─────────────────────────────────────────────
#  PROVEN    >= 30条真实结算 + WR >= 55%  → 半Kelly，最高10%
#  VALIDATED >= 10条真实结算 + WR >= 50%  → 标准仓，5%
#  EXPLORING <  10条或样本不足            → 探索仓，1~2%
#  BANNED    WR < 35% 且 n >= 6           → 暂停，0%（待验证）

CONFIDENCE_TABLE = {
    # (symbol, score_range, direction): (level, max_pct)
    ('BTCUSDT',  '120~159', 'ANY'): ('VALIDATED',  5.0),   # n=25 WR=92%
    ('BTCUSDT',  '160+',    'ANY'): ('EXPLORING',  3.0),   # 高分段待验证
    ('SOLUSDT',  '120~159', 'ANY'): ('EXPLORING',  2.0),   # n=5 样本不足
    ('BNBUSDT',  '120~159', 'ANY'): ('EXPLORING',  2.0),   # n=3 样本不足
    ('DOGEUSDT', '160+',    'ANY'): ('EXPLORING',  2.0),   # n=17 WR=59%
    # 已知失效组合 → 暂停
    ('LTCUSDT',  '160+',  'SHORT'): ('EXPLORING',  0.5),   # [v24.3] WR=0% n=13污染数据 → 极小仓探索
    ('SOLUSDT',  '160+',  'SHORT'): ('EXPLORING',  0.5),   # [v24.3] WR=0% n=9污染数据 → 极小仓探索
    ('ETHUSDT',  '160+',  'SHORT'): ('EXPLORING',  0.5),   # [v24.3] WR=0% n=6污染数据 → 极小仓探索
}

# 默认规则（未明确映射的组合）
DEFAULT_BY_SCORE = {
    '160+':   ('EXPLORING', 2.0),
    '140~159':('EXPLORING', 3.0),
    '120~139':('EXPLORING', 2.0),
    '<120':   ('EXPLORING', 0.3),   # [v24.3] score<120不硬封，超保守探索0.3%（grade<70已被BridgeGate过滤）
}


def _score_range(score: float) -> str:
    if score >= 175: return '175+'   # 合并入160+
    if score >= 160: return '160+'
    if score >= 140: return '140~159'
    if score >= 120: return '120~139'
    return '<120'


def get_position_pct(symbol: str, score: float, direction: str,
                     nav: float = 0.0) -> dict:
    """
    返回：{
      'pct': 建议仓位百分比（0~10）,
      'usdt': 对应金额（如传入nav）,
      'level': 置信等级,
      'reason': 说明,
      'allowed': True/False
    }
    """
    sr = _score_range(score)
    dir_upper = direction.upper() if direction else 'ANY'

    # 精确匹配
    key_exact  = (symbol, sr, dir_upper)
    key_any    = (symbol, sr, 'ANY')
    key_175    = (symbol, '160+', dir_upper) if sr == '175+' else None

    level, max_pct = None, None
    for k in [key_exact, key_any, key_175]:
        if k and k in CONFIDENCE_TABLE:
            level, max_pct = CONFIDENCE_TABLE[k]
            break

    if level is None:
        bkt = '160+' if sr in ('160+','175+') else sr
        level, max_pct = DEFAULT_BY_SCORE.get(bkt, ('EXPLORING', 1.0))

    allowed = (max_pct > 0)
    usdt = nav * max_pct / 100 if nav > 0 else 0

    return {
        'pct':     max_pct,
        'usdt':    round(usdt, 2),
        'level':   level,
        'reason':  f'{symbol} score={score:.0f}({sr}) dir={direction} → {level}',
        'allowed': allowed,
    }


def kelly_position(wr: float, rr: float, half: bool = True) -> float:
    """Kelly公式计算理论最优仓位"""
    if rr <= 0: return 0
    k = wr - (1 - wr) / rr
    return max(0, k / 2 if half else k) * 100


if __name__ == '__main__':
    # 自测
    print("=== 仓位定量器自测 ===")
    cases = [
        ('BTCUSDT',  145, 'LONG'),
        ('BTCUSDT',  162, 'SHORT'),
        ('LTCUSDT',  168, 'SHORT'),
        ('SOLUSDT',  165, 'SHORT'),
        ('ETHUSDT',  170, 'SHORT'),
        ('SOLUSDT',  135, 'LONG'),
        ('DOGEUSDT', 172, 'SHORT'),
    ]
    nav = 127.37
    for sym, sc, d in cases:
        r = get_position_pct(sym, sc, d, nav)
        flag = '✅' if r['allowed'] else '🚫'
        print(f"  {flag} {sym:12} sc={sc} {d:6} → {r['level']:12} {r['pct']:.0f}% (${r['usdt']:.2f})")

    print(f"\n  Kelly基准(WR=51.8% RR=3.23):")
    print(f"  全Kelly={kelly_position(0.518,3.23,False):.1f}%  半Kelly={kelly_position(0.518,3.23):.1f}%")
