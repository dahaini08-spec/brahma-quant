#!/usr/bin/env python3
"""
post_gate.py — 新闻局发帖统一守门 v1.0
==========================================
设计院 · 梵天系统 2026-05-28

所有广场发帖必须通过此模块校验。
任何脚本、cron、子流程发帖前，调用 gate_check() 。

守门规则：
  G1 — 方向一致性：点位结构必须与信号方向匹配
       做多：止损 < 入场 < 目标
       做空：止损 > 入场 > 目标
  G2 — R:R门槛：≥ 1.5（广场发帖标准）
  G3 — 点位完整性：entry / stop / tp1 三者必须存在
  G4 — 价格合理性：各价格字段 > 0 且非 NaN

返回：
  (ok: bool, reason: str)
  ok=True  → 允许发帖
  ok=False → 拦截，reason说明原因

使用示例：
  from post_gate import gate_check
  ok, reason = gate_check(symbol, direction, entry, stop, tp1, rr1)
  if not ok:
      print(f"[拦截] {reason}")
      sys.exit(0)
"""

import math


# ── 主校验函数 ────────────────────────────────────────
def gate_check(
    symbol:    str,
    direction: str,      # 'LONG' / 'SHORT' / '做多' / '做空'
    entry:     float,    # 入场价（entry_lo）
    stop:      float,    # 止损价
    tp1:       float,    # 目标价
    rr1:       float,    # 风险回报比
    price:     float = 0,# 现价（可选，用于额外检查）
    min_rr:    float = 1.5,
    verbose:   bool = False,
) -> tuple:
    """
    返回 (ok: bool, reason: str)
    """
    reasons = []

    _dstr = str(direction).upper().strip()
    is_short = 'SHORT' in _dstr or _dstr == '空'
    is_long  = 'LONG'  in _dstr or _dstr == '多'
    # NEUTRAL/空字符串 → 直接拦截，不允许发帖
    if not is_short and not is_long:
        return False, ['G0-方向未定义: 方向为NEUTRAL或空，禁止发帖']

    # ── G3 点位完整性 ─────────────────────────────────
    for name, val in [('entry', entry), ('stop', stop), ('tp1', tp1)]:
        if not val or val <= 0 or math.isnan(float(val)):
            reasons.append(f'G3-点位不完整: {name}={val}')

    if reasons:
        return False, ' | '.join(reasons)

    entry = float(entry)
    stop  = float(stop)
    tp1   = float(tp1)
    rr1   = float(rr1)

    # ── G4 价格合理性 ─────────────────────────────────
    for name, val in [('entry', entry), ('stop', stop), ('tp1', tp1)]:
        if val <= 0:
            reasons.append(f'G4-价格异常: {name}={val}')

    # ── G1 方向一致性（最重要）────────────────────────
    if is_short:
        # 做空：止损 > 入场 > 目标
        if not (stop > entry):
            reasons.append(f'G1-方向冲突[做空]: 止损({stop:.4f}) 应 > 入场({entry:.4f})')
        if not (tp1 < entry):
            reasons.append(f'G1-方向冲突[做空]: 目标({tp1:.4f}) 应 < 入场({entry:.4f})')
    elif is_long:
        # 做多：止损 < 入场 < 目标
        if not (stop < entry):
            reasons.append(f'G1-方向冲突[做多]: 止损({stop:.4f}) 应 < 入场({entry:.4f})')
        if not (tp1 > entry):
            reasons.append(f'G1-方向冲突[做多]: 目标({tp1:.4f}) 应 > 入场({entry:.4f})')
    else:
        reasons.append(f'G1-方向未知: direction={direction}')

    # ── G2 R:R门槛 ────────────────────────────────────
    if rr1 < min_rr:
        reasons.append(f'G2-R:R不达标: {rr1:.2f} < {min_rr}（广场最低要求）')

    if reasons:
        if verbose:
            print(f'[PostGate] 🚫 {symbol} {direction} 拦截:')
            for r in reasons:
                print(f'  → {r}')
        return False, ' | '.join(reasons)

    if verbose:
        print(f'[PostGate] ✅ {symbol} {direction} 通过 (R:R={rr1:.1f}x)')
    return True, 'OK'


# ── 从params dict直接校验 ─────────────────────────────
def gate_check_params(
    symbol:    str,
    direction: str,
    params:    dict,
    min_rr:    float = 1.5,
    verbose:   bool = False,
) -> tuple:
    """
    从 get_brahma_params() 返回的 params dict 直接校验
    """
    if not params or params.get('error'):
        return False, f'G0-无params数据: {params.get("error","") if params else "empty"}'

    entry = float(params.get('entry_lo', 0))
    stop  = float(params.get('stop', 0))
    tp1   = float(params.get('tp1', 0))
    rr1   = float(params.get('rr1', 0))
    price = float(params.get('price', 0))

    return gate_check(symbol, direction, entry, stop, tp1, rr1, price, min_rr, verbose)


# ── 快速校验dd1格式（entry/stop_loss/tp1字段名）─────────
def gate_check_dd1(
    symbol:    str,
    direction: str,
    entry_lo:  float,
    stop_loss: float,
    tp1:       float,
    rr1:       float,
    min_rr:    float = 1.5,
    verbose:   bool = False,
) -> tuple:
    return gate_check(symbol, direction, entry_lo, stop_loss, tp1, rr1, 0, min_rr, verbose)


if __name__ == '__main__':
    print('=== PostGate 自检 ===')

    # 测试1: 正常做空
    ok, r = gate_check('ETH', 'SHORT', 2580, 2630, 2420, 2.3, verbose=True)
    assert ok, f'预期通过但拦截: {r}'
    print()

    # 测试2: 方向冲突（LONG但点位是空单结构）
    ok, r = gate_check('BTC', 'LONG', 74743, 75857, 72933, 1.1, verbose=True)
    assert not ok, '预期拦截但通过了'
    print()

    # 测试3: R:R不足
    ok, r = gate_check('SOL', 'SHORT', 180, 185, 176, 1.2, verbose=True)
    assert not ok, '预期R:R拦截但通过了'
    print()

    # 测试4: 正常做多
    ok, r = gate_check('BNB', 'LONG', 600, 585, 640, 2.0, verbose=True)
    assert ok, f'预期通过但拦截: {r}'
    print()

    print('✅ 全部4个测试通过')
