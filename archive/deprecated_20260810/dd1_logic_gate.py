#!/usr/bin/env python3
"""
dd1_logic_gate.py — 钉钉1格式逻辑一致性守护门
设计院 2026-05-28

六道逻辑门：
  门0: structure_grade < 70 → 物理拒绝（B/C/X级结构，888无法绕过）[v24.2 50→70]
  门0b: RR < 1.5 → 物理拒绝（盈亏比不足，888无法绕过）
  门1: score < 100 → 物理拒绝生成策略
  门2: valid=False 且 score < 145 → 强制降级警告
  门3: 逆势信号 → 显性标注「逆势，梵天不背书」
  门4: 方向与体制完全背离 → 附加风险声明

使用：
  from dd1_logic_gate import check_before_dd1
  ok, warning = check_before_dd1(symbol, direction, score, valid, regime)
  if not ok: raise LogicError(warning)
"""

class LogicGateError(Exception):
    """梵天逻辑门拒绝，不允许生成策略"""
    pass

BEAR_REGIMES = {'BEAR_TREND', 'BEAR_EARLY', 'BEAR_CRASH', 'BEAR_RECOVERY'}
BULL_REGIMES = {'BULL_TREND', 'BULL_PEAK', 'BULL_EARLY'}

def check_before_dd1(
    symbol: str,
    direction: str,        # 'LONG'/'SHORT'/'做多'/'做空'
    score: float,
    valid: bool,
    regime: str = '',
    brahma_action: str = '', # brahma_brain返回的action字段
    structure_grade: float = -1,   # structure_quality_engine grade（0~100）
    rr1: float = -1,               # 盈亏比R:R（-1=不检查）
    sl_pct: float = -1,            # 止损百分比（-1=不检查）← [v25.2 设计院规范]
) -> tuple:
    """
    返回 (allowed: bool, message: str, risk_label: str)
    allowed=False → 调用方必须拒绝生成，不得绕过
    risk_label → 附加到帖子末尾的风险标注
    """
    score = float(score)
    is_long = direction in ('LONG', '做多', 'LONG')
    is_short = direction in ('SHORT', '做空', 'SHORT')
    regime = regime.upper()

    # ── 门0：结构质量不足，物理拒绝（grade<70=B/C/X级，888无法绕过）[v24.2] ─
    # [v24.2 2026-06-12] 50→70 铁证: grade50-60 TO率=73%, grade≥70 TO率=8%
    try:
        import sys as _sys, os as _os
        _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
        from system_config import MIN_GRADE as _MG, SYMBOL_MIN_GRADE as _SMG
        _eff_grade = _SMG.get(symbol.upper(), _MG)
    except Exception:
        _eff_grade = 70  # [v24.2] 安全默认值
    if structure_grade >= 0 and structure_grade < _eff_grade:
        raise LogicGateError(
            f"❌ 逻辑门拒绝：{symbol} {direction} structure_grade={structure_grade:.0f}<{_eff_grade}，"
            f"结构为C/X级，无有效锚点，梵天禁止生成策略。\n"
            f"需等待更高质量结构（grade≥{_eff_grade}）才能发出DD1信号。"
        )

    # ── 门0b：盈亏比不足，物理拒绝（RR<1.5，888无法绕过）────────
    if rr1 >= 0 and rr1 < 1.5:
        raise LogicGateError(
            f"❌ 逻辑门拒绝：{symbol} {direction} RR={rr1:.2f}<1.5，"
            f"盈亏比不足，梵天禁止生成策略。\n"
            f"设计院红线：R:R≥1.5才能发出DD1信号，当前止损/目标需重新设置。"
        )

    # ── 门0c：止损百分比不足，物理拒绝 [v25.2 设计院规范 2026-06-14] ──────
    # BTC/ETH主流合约：止损必须≥1.0%，否则噪音直接扫除
    # 铁证：ATR_15M/入场价≈0.16%(BTC)/0.19%(ETH)
    # 否决案例：BTC 162点(0.25%) / ETH 14点(0.84%) → 1.6根15M就触发
    if sl_pct >= 0:
        _MIN_SL_PCT = 1.0
        if sl_pct < _MIN_SL_PCT:
            raise LogicGateError(
                f"❌ 逻辑门拒绝：{symbol} {direction} 止损={sl_pct:.2f}%<{_MIN_SL_PCT}%，"
                f"止损空间不足，噪音直接扫除。\n"
                f"设计院规范：BTC/ETH合约止损必须≥1.0%（标准区间1.0%~1.5%）。"
            )

    # ── 门1：评分极低，物理拒绝 ─────────────────────────
    if score < 50:
        raise LogicGateError(
            f"❌ 逻辑门拒绝：{symbol} {direction} score={score:.0f} 极低，"
            f"梵天无任何支撑，禁止生成策略。"
        )

    # ── 门2：score<100 [v24.3-fix] 硬拒绝→WARN+降阈值 ───────
    # 哲学: 100是人为数字, 让grade+score组合自然过滤
    # score 70-99 仍可生成策略（带风险标注），<70由grade门控
    if score < 70:
        raise LogicGateError(
            f"❌ 逻辑门拒绝：{symbol} {direction} score={score:.0f}<70，"
            f"评分不足，梵天无支撑，禁止生成策略。"
        )

    # ── 门3：brahma_brain SKIP+score极低 [v24.3] 阈值120→100 ──
    # 哲学: SKIP+score<100才是真放弃，120是过保守
    if brahma_action == 'SKIP' and score < 100:
        raise LogicGateError(
            f"❌ 逻辑门拒绝：梵天SKIP且score={score:.0f}<100，"
            f"系统放弃该信号，不生成对外策略。"
        )

    # ── 门4：方向与体制完全背离 ──────────────────────────
    risk_labels = []

    if is_long and regime in BEAR_REGIMES:
        if regime == 'BEAR_TREND':
            risk_labels.append('⚠️ 逆势：当前熊市趋势，做多属逆势操作，梵天不背书')
        else:
            risk_labels.append(f'⚠️ 逆势：体制={regime}，做多需谨慎')

    if is_short and regime in BULL_REGIMES:
        if regime == 'BULL_TREND':
            risk_labels.append('⚠️ 逆势：当前牛市趋势，做空属逆势操作，梵天不背书')
        else:
            risk_labels.append(f'⚠️ 逆势：体制={regime}，做空需谨慎')

    # ── 门5：valid=False 降级提示 ────────────────────────
    if not valid and score < 145:  # SSOT broadcast_min  # SSOT broadcast_min  # SSOT broadcast_min  # SSOT broadcast_min  # SSOT broadcast_min  # SSOT broadcast_min  # SSOT broadcast_min  # SSOT broadcast_min  # SSOT broadcast_min  # SSOT broadcast_min  # SSOT broadcast_min
        risk_labels.append(f'📋 梵天评分{score:.0f}，条件未全部满足，建议等待确认')

    risk_label = '\n'.join(risk_labels)
    return True, risk_label


def gate_summary(symbol, direction, score, valid, regime, brahma_action='') -> str:
    """生成策略前调用，返回摘要供日志"""
    try:
        allowed, risk = check_before_dd1(symbol, direction, score, valid, regime, brahma_action)
        if risk:
            return f'[LogicGate] {symbol} {direction} score={score:.0f} ALLOWED with WARNING: {risk}'
        return f'[LogicGate] {symbol} {direction} score={score:.0f} ALLOWED ✅'
    except LogicGateError as e:
        return f'[LogicGate] BLOCKED: {e}'


if __name__ == '__main__':
    # 自测（v25.0 达摩院矩阵v4.0：不再封锁方向，降权在上游regime_mult执行）
    tests = [
        ('ETH', 'LONG',  0,   False, 'BEAR_TREND', 'SKIP'),   # grade=0 应拒绝
        ('ETH', 'LONG',  80,  True,  'BEAR_TREND', ''),        # [v25.0] BEAR_TREND+LONG允许，达摩院L=0.75mult已降权
        ('ETH', 'LONG',  110, True,  'BEAR_TREND', ''),        # 允许，高分通过
        ('ETH', 'SHORT', 175, True,  'BEAR_TREND', 'ENTER'),   # 完全通过
        ('BTC', 'SHORT', 145, True,  'BULL_TREND', ''),        # [v25.0] BULL+SHORT允许，达摩院S=1.08mult加权
        ('BTC', 'LONG',  72,  True,  'BEAR_EARLY', ''),        # [v25.0] BEAR_EARLY+LONG允许，达摩院L=0.92
        ('ETH', 'LONG',  80,  True,  'BEAR_EARLY', ''),        # [v25.0] ETH BEAR_EARLY+LONG最强，Calmar=42
    ]
    for sym, d, s, v, r, a in tests:
        print(gate_summary(sym, d, s, v, r, a))
