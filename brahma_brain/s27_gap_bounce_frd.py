"""
s27_gap_bounce_frd.py — 统计模式维度（Ponytail重构 2026-08-24）
# ponytail: 三个形态识别，每个最多5行，stdlib only，无依赖

s27: Gap Up    — 当前最低 > 前根最高
s28: Bounce    — 锤子线 + 前2根阴线 + 近MA20
s29: FRD       — 当前阴线 + 前3根以上阳线
"""

def _f(k, x): return float(k.get(x) or 0)


def s27_gap_up(sym, k1h, regime):
    # ponytail: gap检测一行，体制加权
    if len(k1h) < 2: return 0
    gap = (_f(k1h[-1],'low') - _f(k1h[-2],'high')) / (_f(k1h[-2],'high') or 1)
    if gap <= 0: return 0
    mul = 8 if 'BULL' in regime else -8 if 'BEAR' in regime else 4
    return mul if gap >= 0.003 else mul // 2 if gap >= 0.001 else 0


def s28_bounce_setup(sym, k1h, k4h, regime):
    # ponytail: 锤子线 = 下影>实体1.5倍 + 上影小
    if len(k1h) < 5: return 0
    o,h,l,c = (_f(k1h[-1],x) for x in 'open,high,low,close'.split(','))
    body, lo_shd, up_shd = abs(c-o), min(o,c)-l, h-max(o,c)
    if not (lo_shd > body*1.5 and up_shd < lo_shd*0.5): return 0
    if sum(1 for k in k1h[-4:-1] if _f(k,'close') < _f(k,'open')) < 2: return 0
    # ponytail: MA20检查，无k4h时跳过
    if k4h and len(k4h) >= 20:
        ma20 = sum(_f(k,'close') for k in k4h[-20:]) / 20
        if ma20 and abs(c-ma20)/ma20 > 0.015: return 0
    return 10 if any(x in regime for x in ('BULL','RECOVERY')) else 5 if 'CHOP' in regime else -5 if 'BEAR_TREND' in regime else 4


def s29_first_red_day(sym, k1h, regime):
    # ponytail: 首根阴线 = 当前红K + 前3+根阳线
    if len(k1h) < 6: return 0
    o,c = _f(k1h[-1],'open'), _f(k1h[-1],'close')
    if not (c < o and (o-c)/o > 0.001): return 0
    bulls = 0
    for k in k1h[-6:-1]:
        if _f(k,'close') > _f(k,'open'): bulls += 1
        else: break
    if bulls < 3: return 0
    return 8 if 'BEAR' in regime else 5 if 'CHOP' in regime else -3 if 'BULL' in regime else 4
