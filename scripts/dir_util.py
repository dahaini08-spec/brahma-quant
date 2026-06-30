#!/usr/bin/env python3
"""
dir_util.py — 梵天系统方向判断标准库
设计院 · 2026-05-28

规则：
  SHORT / short / 空 / 做空 / 空单  → '做空'  📉  ▼
  LONG  / long  / 多 / 做多 / 多单  → '做多'  📈  ▲
  其他（NEUTRAL / '' / None / ？） → '观望'  ⏸  —

禁止:
  ❌ '做空' if SHORT else '做多'  （NEUTRAL默认变做多，已导致严重错误）
  ✅ 使用 parse_direction() 或 dir_str()
"""

# 方向常量
DIRECTION_SHORT   = 'SHORT'
DIRECTION_LONG    = 'LONG'
DIRECTION_NEUTRAL = 'NEUTRAL'

# 中文常量
DIR_CN_SHORT   = '做空'
DIR_CN_LONG    = '做多'
DIR_CN_NEUTRAL = '观望'


def parse_direction(raw) -> str:
    """
    将任意方向原始值标准化为 'SHORT' / 'LONG' / 'NEUTRAL'

    >>> parse_direction('SHORT')  → 'SHORT'
    >>> parse_direction('做空')   → 'SHORT'
    >>> parse_direction('LONG')   → 'LONG'
    >>> parse_direction('多')     → 'LONG'
    >>> parse_direction('')       → 'NEUTRAL'
    >>> parse_direction(None)     → 'NEUTRAL'
    >>> parse_direction('NEUTRAL')→ 'NEUTRAL'
    """
    s = str(raw).strip() if raw is not None else ''
    u = s.upper()
    if 'SHORT' in u or '空' in s:
        return DIRECTION_SHORT
    if 'LONG' in u or '多' in s:
        return DIRECTION_LONG
    return DIRECTION_NEUTRAL


def dir_cn(raw) -> str:
    """返回中文方向：做空 / 做多 / 观望"""
    d = parse_direction(raw)
    return DIR_CN_SHORT if d == 'SHORT' else (DIR_CN_LONG if d == 'LONG' else DIR_CN_NEUTRAL)


def dir_str(raw) -> tuple:
    """
    返回 (dir_cn, emoji, arrow, color) 四元组
    用于帖子渲染
    """
    d = parse_direction(raw)
    if d == DIRECTION_SHORT:
        return DIR_CN_SHORT, '📉', '▼', '🔴'
    if d == DIRECTION_LONG:
        return DIR_CN_LONG, '📈', '▲', '🟢'
    return DIR_CN_NEUTRAL, '⏸', '—', '⚪'


def is_short(raw) -> bool:
    return parse_direction(raw) == DIRECTION_SHORT


def is_long(raw) -> bool:
    return parse_direction(raw) == DIRECTION_LONG


def is_neutral(raw) -> bool:
    return parse_direction(raw) == DIRECTION_NEUTRAL


# ──────────────────────────────────────────────
# 自测
# ──────────────────────────────────────────────
if __name__ == '__main__':
    CASES = [
        ('SHORT',    'SHORT'), ('short',    'SHORT'), ('做空',   'SHORT'),
        ('空',       'SHORT'), ('空单',     'SHORT'), ('LONG',   'LONG'),
        ('long',     'LONG'),  ('做多',     'LONG'),  ('多',     'LONG'),
        ('多单',     'LONG'),  ('NEUTRAL',  'NEUTRAL'),('',      'NEUTRAL'),
        (None,       'NEUTRAL'),('UNKNOWN', 'NEUTRAL'),
    ]
    ok = fail = 0
    for raw, expected in CASES:
        got = parse_direction(raw)
        if got == expected:
            ok += 1
        else:
            print(f'  ❌ parse_direction({raw!r}) = {got!r}, 期望 {expected!r}')
            fail += 1
    print(f'dir_util 自测: {ok}✅ {fail}❌')
