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

# ── v4.2 改进④ 7月减半仓策略 2026-07-01 苏摩111批准 ──────────────────────────
# score 160~169 区间在7月1~15日临时从EXPLORING(2%/3%)降至1%
# score ≥170 维持正常执行
# 有效期: 2026-07-01 ~ 2026-07-15
JULY_HALF_POSITION = True   # 到2026-07-15自动失效（由get_position_pct内部检查）
JULY_HALF_SCORE_RANGE = (160, 169)  # score区间
JULY_HALF_NAV = 1.0  # 降至1%NAV

_JULY_HALF_TABLE_SHADOW = {}  # shadow占位，待填充

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


# ── FearGreed_PositionGuard (修复二 2026-07-08 设计院自主决策) ────────────────
# 极度恐惧环境下自动缩减仓位上限，防止在恐慌市场开大仓
# FG ≤ 20: 上限0.5%NAV + 额外-10分惩罚（由brahma_core注入fg_penalty后调用）
# FG 21~25: 上限1.0%NAV
# FG 26~40: 上限2.0%NAV
# FG > 40: 正常规则，不限制
FEAR_GREED_POSITION_CAPS = [
    (0,  20, 0.5,  'FG极度恐惧上限'),
    (21, 25, 1.0,  'FG恐惧上限'),
    (26, 40, 2.0,  'FG偏恐惧上限'),
]


def get_fg_position_cap(fear_greed_index: float) -> tuple:
    """根据恐贪指数返回仓位上限和说明
    返回: (cap_pct: float, reason: str) | None表示不限制"""
    if fear_greed_index is None:
        return None, ''
    for lo, hi, cap, reason in FEAR_GREED_POSITION_CAPS:
        if lo <= fear_greed_index <= hi:
            return cap, f'{reason}(FG={fear_greed_index:.0f})'
    return None, ''


def get_position_pct(symbol: str, score: float, direction: str,
                     nav: float = 0.0, fear_greed: float = None) -> dict:
    """
    返回：{
      'pct': 建议仓位百分比（0~10）,
      'usdt': 对应金额（如传入nav）,
      'level': 置信等级,
      'reason': 说明,
      'allowed': True/False
    }
    """
    import datetime as _dt_ps
    _now_ps = _dt_ps.datetime.utcnow()

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

    # ── v4.2 改进④ 7月减半仓策略 ─────────────────────────────────────────
    # 有效期 2026-07-01 ~ 2026-07-15，score 160~169 → 强制1%NAV
    _july_half_active = (
        JULY_HALF_POSITION
        and _now_ps.month == 7
        and 1 <= _now_ps.day <= 15
        and JULY_HALF_SCORE_RANGE[0] <= score <= JULY_HALF_SCORE_RANGE[1]
    )
    if _july_half_active and max_pct > JULY_HALF_NAV:
        max_pct = JULY_HALF_NAV
        level = f'{level}+7月减半'
    # ──────────────────────────────────────────────────────

    # ── FearGreed_PositionGuard (修复二 2026-07-08) ───────────────────────────
    # 恐贪指数小于等于40时强制容网仓位上限，防止恐慌市场开大仓五项修复之一
    _fg_cap, _fg_reason = get_fg_position_cap(fear_greed)
    _fg_applied = False
    if _fg_cap is not None and max_pct > _fg_cap:
        max_pct = _fg_cap
        level = f'{level}+FG仓位容网'
        _fg_applied = True
    # ──────────────────────────────────────────────────────

    allowed = (max_pct > 0)
    usdt = nav * max_pct / 100 if nav > 0 else 0

    return {
        'pct':     max_pct,
        'usdt':    round(usdt, 2),
        'level':   level,
        'reason':  f'{symbol} score={score:.0f}({sr}) dir={direction} → {level}'
                   + (' [7月上旬减半仓]' if _july_half_active else '')
                   + (f' [{_fg_reason}]' if _fg_applied else ''),
        'allowed': allowed,
        'fg_cap':  _fg_cap,
        'fg_applied': _fg_applied,
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


def sync_confidence_table_from_wr(min_n: int = 10, dry_run: bool = False) -> dict:
    """
    断点B修复：学习闭环 2026-07-03
    从 wr_matrix_realtime.json 自动同步更新 CONFIDENCE_TABLE
    规则：
      n >= min_n AND WR >= 75%  → PROVEN  max_pct=8.0
      n >= min_n AND WR >= 55%  → VALIDATED max_pct=5.0
      n >= min_n AND WR < 35%   → BANNED  max_pct=0.0
      n < min_n                 → 不修改（样本不足）
    """
    global CONFIDENCE_TABLE
    wr_path = BASE / 'data' / 'wr_matrix_realtime.json'
    if not wr_path.exists():
        return {'updated': 0, 'skipped': 0, 'msg': 'wr_matrix_realtime.json不存在'}

    matrix = json.loads(wr_path.read_text())
    updates, skipped = [], []

    for key, m in matrix.items():
        n = m.get('n', 0)
        wr = m.get('wr', 0.0)
        direction = m.get('direction', '')
        score_bin = m.get('score_bin', '')

        if n < min_n:
            skipped.append(f'{key} n={n}<{min_n}')
            continue

        # 将 wr_matrix key 转换为 CONFIDENCE_TABLE score_range
        if score_bin == '160+':
            sr = '160+'
        elif score_bin in ('140-159', '120-139'):
            sr = '120~159'
        else:
            continue

        target_syms = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT']
        dir_upper = direction.upper() if direction else 'ANY'

        if wr >= 0.75:
            new_level, new_pct = 'PROVEN', 8.0
        elif wr >= 0.55:
            new_level, new_pct = 'VALIDATED', 5.0
        elif wr < 0.35:
            new_level, new_pct = 'BANNED', 0.0
        else:
            continue  # 35-55% 区间，不自动修改

        for sym in target_syms:
            for ct_key in [(sym, sr, dir_upper), (sym, sr, 'ANY')]:
                old = CONFIDENCE_TABLE.get(ct_key)
                if old and old[0] != new_level:
                    if not dry_run:
                        CONFIDENCE_TABLE[ct_key] = (new_level, new_pct)
                    updates.append({
                        'key': str(ct_key), 'old': old,
                        'new': (new_level, new_pct), 'n': n, 'wr': wr
                    })

    result = {
        'updated': len(updates),
        'skipped': len(skipped),
        'changes': updates,
        'skipped_keys': skipped,
    }
    if updates:
        pass  # [静默]
        for u in updates:
            print(f'  {u["key"]}: {u["old"]} → {u["new"]} (n={u["n"]} WR={u["wr"]:.1%})')
    return result
