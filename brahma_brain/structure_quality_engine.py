#!/usr/bin/env python3
"""
structure_quality_engine.py — 结构质量引擎 v1.0
设计院 · 2026-05-31

核心哲学（十年交易视角）：
  好信号不是「评分高」，是「入场区有真实价格结构支撑」
  无结构入场 = 赌博。有结构入场 = 交易。

五级结构评分：
  S (90-100): FVG + OB双重确认，结构完美
  A (70-89):  FVG或强OB，单重确认
  B (50-69):  摆动高低点或Fib黄金位
  C (30-49):  弱结构（Fib普通位 / 远期OB）
  X (0-29):   无结构，入场区≈现价，拒绝
"""

import math
from typing import Optional

# ── 结构等级定义 ─────────────────────────────────────────────────────────────
GRADE_S = 90   # FVG+OB双重
GRADE_A = 70   # 单重强结构
GRADE_B = 50   # 弱结构
GRADE_C = 30   # 极弱
GRADE_X = 0    # 无结构 → 拒绝

def evaluate_structure_quality(
    symbol: str,
    signal_dir: str,         # 'SHORT' or 'LONG'
    price: float,
    entry_lo: float,
    entry_hi: float,
    smc: dict,               # brahma_brain SMC数据
    swing_4h: dict,          # 4H摆动结构
    key_levels: dict,        # Fib/关键位
    momentum: dict,          # ATR等动量数据
    **kwargs,                # v24.3: trigger_confidence等扩展参数
) -> dict:
    """
    评估入场区的结构质量。
    返回: {'grade': int, 'label': str, 'sources': [...], 'reject': bool, 'reason': str}
    """
    sources  = []
    score    = 0
    entry_mid = (entry_lo + entry_hi) / 2 if entry_lo and entry_hi else price

    # ── 1. 入场区是否有偏离（基础条件）─────────────────────────────────────
    entry_gap_pct = abs(entry_mid - price) / price * 100 if price > 0 else 0

    # 无结构：入场区≈现价（gap<0.1%，几乎重合才拒绝）[v24.3: 0.2%→0.1%]
    # 原逻辑0.2%过严：价格在入场区内时gap≈0，正常信号被误杀
    # 当价格在入场区内(gap<0.2%)且有15M触发时，应继续评分而非直接拒绝
    _trigger_conf = kwargs.get('trigger_confidence', 0) or 0
    if entry_gap_pct < 0.1 and _trigger_conf < 40:
        return {
            'grade': GRADE_X, 'label': 'X-无结构',
            'sources': ['入场区≈现价(gap<0.1%)'],
            'reject': True,
            'reason': f'入场区距现价仅{entry_gap_pct:.2f}%，无结构锚点，拒绝',
            'entry_gap_pct': entry_gap_pct,
        }
    # 价格在入场区内（gap<0.2%）但有15M触发 → 给予基础分继续评分
    _in_entry_zone = entry_gap_pct < 0.2

    # ── 动态对齐阈值（按体制/入场区距离自适应）[2026-06-03 根治修复] ─────────
    # 根因: BEAR_TREND下入场区距现价2-4%是正常的，OB/FVG锚点也在同等距离
    # 原来固定阈值<1.5%→ob_score=0→grade=19→INVALID（误杀高分信号）
    # 修复: 以 entry_gap_pct 为基准，对齐阈值 = max(entry_gap_pct × 1.2, 1.5%)
    _align_tol = max(entry_gap_pct * 1.2, 1.5)   # 动态容忍度
    _align_tight = max(entry_gap_pct * 0.4, 0.5)  # 精确对齐阈值

    # ── 2. FVG（公平价值缺口）验证 ───────────────────────────────────────────
    fvg = smc.get('fvg', {}) if smc else {}
    fvg_key = 'nearest_bear' if signal_dir == 'SHORT' else 'nearest_bull'
    fvg_zone = fvg.get(fvg_key)

    fvg_score = 0
    if fvg_zone:
        fvg_gap = fvg_zone.get('gap_pct', 0) or 0
        fvg_mid = (fvg_zone.get('bottom', 0) + fvg_zone.get('top', 0)) / 2
        fvg_dist = abs(fvg_mid - entry_mid) / entry_mid * 100 if entry_mid > 0 else 999

        if fvg_gap >= 0.5 and fvg_dist < _align_tight:
            fvg_score = 40   # FVG完美对齐
            sources.append(f'FVG={fvg_gap:.2f}% 完美对齐(tol={_align_tight:.1f}%)')
        elif fvg_gap >= 0.3 and fvg_dist < _align_tol:
            fvg_score = 25
            sources.append(f'FVG={fvg_gap:.2f}% 近似对齐(tol={_align_tol:.1f}%)')
        elif fvg_gap >= 0.2 and fvg_dist < _align_tol * 1.5:
            fvg_score = 10
            sources.append(f'FVG={fvg_gap:.2f}% 弱对齐')

    score += fvg_score

    # ── 3. Order Block 验证 ──────────────────────────────────────────────────
    obs = smc.get('order_blocks', {}) if smc else {}
    ob_key = 'nearest_bear_ob' if signal_dir == 'SHORT' else 'nearest_bull_ob'
    ob = obs.get(ob_key)

    ob_score = 0
    if ob:
        ob_lo = float(ob.get('low', 0) or 0)
        ob_hi = float(ob.get('high', 0) or 0)
        ob_mid = ob.get('mid') or ((ob_lo + ob_hi) / 2 if ob_lo and ob_hi else 0)
        # [v24.5-fix] OB距离改用现价(price)为参考基准，而非entry_mid
        # 根因：入场区中点比现价高0.3-0.5%，导致OB距离被人为放大，卡在70分边界
        # 修复逻辑：OB是否「贴近当前行情」应以现价为准；入场区是未来预期位置，不是当前锚点
        _ref_price = price if price > 0 else entry_mid  # 以现价为距离参考
        ob_dist = abs(ob_mid - _ref_price) / _ref_price * 100 if _ref_price > 0 and ob_mid > 0 else 999
        ob_dist_entry = abs(ob_mid - entry_mid) / entry_mid * 100 if entry_mid > 0 and ob_mid > 0 else 999
        # [v24.3-fix] smc_engine的OB无strength字段，用dist_pct判断质量
        # dist_pct<0.3% = 精确对齐(强) / dist_pct<1.0% = 对齐(中) / 其他 = 弱
        ob_dist_pct = float(ob.get('dist_pct', ob_dist) or ob_dist)
        ob_quality = 'strong' if ob_dist_pct < 0.3 else ('medium' if ob_dist_pct < 1.0 else 'weak')

        # [v24.3-fix2] OB与入场区重叠判断：OB区间与entry区间有交集 = 精确对齐
        ob_overlap = (ob_lo <= entry_hi and ob_hi >= entry_lo) if ob_lo and ob_hi else False
        # 价格在OB区间内也算精确对齐
        price_in_ob = (ob_lo <= _ref_price <= ob_hi) if ob_lo and ob_hi else False
        if ob_overlap or price_in_ob or (ob_dist < _align_tight and ob_quality in ('strong', 'medium')):
            ob_score = 35
            sources.append(f'强OB精确对齐(price_dist={ob_dist:.2f}% in_ob={price_in_ob} overlap={ob_overlap})')
        elif ob_dist < _align_tol:
            ob_score = 20
            sources.append(f'OB对齐(price_dist={ob_dist:.1f}%)')
        elif ob_dist < _align_tol * 1.5:
            ob_score = 8
            sources.append(f'弱OB price_dist={ob_dist:.1f}%')

    score += ob_score

    # ── 4. 摆动结构验证（4H高低点）──────────────────────────────────────────
    swing_score = 0
    sw_highs = swing_4h.get('highs', []) if swing_4h else []
    sw_lows  = swing_4h.get('lows', [])  if swing_4h else []

    # Swing对齐阈值同步动态化
    _swing_tol = max(entry_gap_pct * 1.5, 1.0)  # 动态容忍度 [v24.4: 0.5→1.0% BTC ATR4H≈1-1.5%，0.5%下限会误杀真实供给区]
    if signal_dir == 'SHORT' and sw_highs:
        nearby_highs = [h for h in sw_highs if abs(h - entry_mid) / entry_mid < _swing_tol]
        if nearby_highs:
            swing_score = 20
            sources.append(f'4H摆动高点={nearby_highs[0]:.4g} 对齐')
    elif signal_dir == 'LONG' and sw_lows:
        nearby_lows = [l for l in sw_lows if abs(l - entry_mid) / entry_mid < _swing_tol]
        if nearby_lows:
            swing_score = 20
            sources.append(f'4H摆动低点={nearby_lows[0]:.4g} 对齐')

    score += swing_score

    # ── 5. Fib关键位验证 ─────────────────────────────────────────────────────
    fib = key_levels.get('fib', {}) if key_levels else {}
    fib_score = 0

    # Fib对齐阈值同步动态化
    _fib_tight = max(entry_gap_pct * 0.3, 0.5)
    _fib_loose = max(entry_gap_pct * 0.6, 1.0)
    golden_fibs = [('0.618', 15), ('0.786', 12), ('0.500', 8), ('0.382', 5)]
    for fib_key, fib_val in golden_fibs:
        if fib_key in fib:
            fib_level = float(fib[fib_key])
            dist = abs(fib_level - entry_mid) / entry_mid * 100 if entry_mid > 0 else 999
            if dist < _fib_tight:
                fib_score = max(fib_score, fib_val)
                sources.append(f'Fib{fib_key}={fib_level:.4g} 对齐')
                break
            elif dist < _fib_loose:
                fib_score = max(fib_score, fib_val // 2)

    score += fib_score

    # ── 6. 入场区宽度质量（避免零宽度入场）──────────────────────────────────
    zone_width_pct = (entry_hi - entry_lo) / entry_lo * 100 if entry_lo > 0 else 0
    if zone_width_pct < 0.05:
        score -= 15   # 入场区太窄，缺乏流动性缓冲
        sources.append(f'⚠️ 入场区过窄({zone_width_pct:.2f}%)')
    elif zone_width_pct >= 0.2:
        score += 5    # 合理宽度加分
        sources.append(f'入场区合理({zone_width_pct:.2f}%)')

    # ── 7. 15M触发15M触发置信度加分（v24.3新增）──────────────────────────
    # 15M触发置信度是最强的结构确认信号，应直接贡献gradeuff08此前被忽视）
    trigger_confidence = kwargs.get('trigger_confidence', 0)
    if trigger_confidence >= 80:
        score += 25
        sources.append(f'15M触发置信={trigger_confidence}满分(+25)')
    elif trigger_confidence >= 60:
        score += 15
        sources.append(f'15M触发置信={trigger_confidence}(+15)')
    elif trigger_confidence >= 40:
        score += 8
        sources.append(f'15M触发置信={trigger_confidence}(+8)')

    # ── 评级 ─────────────────────────────────────────────────────────────────
    score = max(0, min(100, score))

    if score >= GRADE_S:
        label = 'S-完美结构'
    elif score >= GRADE_A:
        label = 'A-强结构'
    elif score >= GRADE_B:
        label = 'B-弱结构'
    elif score >= GRADE_C:
        label = 'C-极弱结构'
    else:
        label = 'X-无结构'

    # [2026-06-03 动态门槛] reject阈值从GRADE_C(30)降至15
    # 依据: 武曲Paper 121笔实盘 grade>=25 WR=82.5%
    #       grade=19的score=174信号（ETH BEAR_TREND(熊市趋势)）是真实高质量信号
    #       被grade<30误杀，损失大量有效机会
    # 新规则: grade<15才真正"无结构"，15-29是"极弱结构但有锚点"
    _reject_threshold = 15
    reject = (score < _reject_threshold or entry_gap_pct < 0.2)
    reason = ''
    if reject:
        reason = f'结构质量不足(grade={score}<{_reject_threshold})，无有效锚点'

    return {
        'grade':         score,
        'label':         label,
        'sources':       sources,
        'reject':        reject,
        'reason':        reason,
        'entry_gap_pct': round(entry_gap_pct, 2),
        'fvg_score':     fvg_score,
        'ob_score':      ob_score,
        'swing_score':   swing_score,
        'fib_score':     fib_score,
    }


def get_time_weight(utc_hour: int) -> float:
    """
    实证时间权重（UTC小时）
    实盘数据：08-13 WR=56-100%，14-16 WR=0%
    """
    # 高质量窗口
    if utc_hour in (7, 8, 9, 10, 11, 12, 13):
        return 1.15   # 欧洲+美国早盘
    # 低质量窗口（美国午后）
    if utc_hour in (14, 15, 16):
        return 0.70   # WR=0%实证，大幅折扣
    # 亚洲时段（19-22 UTC）
    if utc_hour in (19, 20, 21, 22):
        return 1.10
    # 深夜低流动性
    if utc_hour in (0, 1, 2, 3, 4, 5):
        return 0.85
    return 1.0


def kelly_position(wr: float, rr: float, nav: float, max_pct: float = 0.10) -> float:
    """
    半Kelly仓位计算
    wr: 胜率(0-1)  rr: 盈亏比  nav: 净值  max_pct: 单笔上限
    """
    if rr <= 0 or wr <= 0: return 0
    kelly = wr - (1 - wr) / rr
    if kelly <= 0: return 0
    half_kelly = kelly / 2
    position = min(half_kelly, max_pct) * nav
    return round(position, 2)


if __name__ == '__main__':
    # 自测
    print("=== structure_quality_engine 自测 ===")

    # 模拟BTC场景（有FVG结构）
    r1 = evaluate_structure_quality(
        'BTCUSDT', 'SHORT', 73900, 75900, 76300,
        smc={'fvg': {'nearest_bear': {'bottom': 75950, 'top': 76250, 'gap_pct': 0.4}}, 'order_blocks': {}},
        swing_4h={'highs': [76100, 76500], 'lows': [73000]},
        key_levels={'fib': {'0.618': 76000}},
        momentum={'atr_1h': 185}
    )
    print(f"BTC SHORT(FVG结构): grade={r1['grade']} label={r1['label']} sources={r1['sources']}")

    # 模拟LTC场景（无结构）
    r2 = evaluate_structure_quality(
        'LTCUSDT', 'SHORT', 50.93, 50.93, 50.93,
        smc={'fvg': {}, 'order_blocks': {}},
        swing_4h={'highs': [52.0, 53.5], 'lows': [49.0]},
        key_levels={'fib': {}},
        momentum={'atr_1h': 0.19}
    )
    print(f"LTC SHORT(无结构): grade={r2['grade']} label={r2['label']} reject={r2['reject']} reason={r2['reason']}")

    # Kelly计算
    print(f"\nKelly仓位示例:")
    print(f"  BTC WR=92% RR=0.26 NAV=127: ${kelly_position(0.92, 0.26, 127)}")
    print(f"  LTC WR=50% RR=3.0  NAV=127: ${kelly_position(0.50, 3.0, 127)}")
    print(f"  DOGE WR=59% RR=2.0 NAV=127: ${kelly_position(0.59, 2.0, 127)}")

    # 时间权重
    print(f"\n时间权重(UTC):")
    for h in [7, 8, 14, 15, 20]:
        print(f"  {h:02d}:00 → {get_time_weight(h)}x")


# ════════════════════════════════════════════════════════
# TODO-008: ETH 失效OB降级机制 (2026-06-06)
# ════════════════════════════════════════════════════════

def apply_ob_decay_penalty(grade: float, symbol: str, ob_data: dict,
                            current_price: float, timeframe: str = '1h') -> tuple:
    """
    失效OB降级惩罚：
    当OB已被价格穿越（失效），grade -= 30
    
    Args:
        grade: 当前结构评级
        symbol: 交易对
        ob_data: OB数据字典 {ob_high, ob_low, ob_ts}
        current_price: 当前价格
        timeframe: OB所在时间框架
    
    Returns:
        (adjusted_grade, penalty_reason)
    """
    if not ob_data:
        return grade, "无OB数据"
    
    ob_high = ob_data.get('ob_high', 0)
    ob_low  = ob_data.get('ob_low', 0)
    direction = ob_data.get('direction', 'SHORT')
    
    if not (ob_high and ob_low and current_price):
        return grade, "数据不完整"
    
    penalty = 0
    reason = ""
    
    # 空单OB：价格需在OB上方（OB low > current_price = OB失效）
    if direction in ('SHORT', '做空'):
        if current_price < ob_low * 0.995:  # 价格穿越OB下沿 = OB失效
            penalty = 30
            reason = f"SHORT OB失效(价格{current_price:.4f}<OB下沿{ob_low:.4f})"
        elif current_price < ob_high * 0.99:  # 价格深入OB内部
            penalty = 15
            reason = f"SHORT OB部分失效(价格在OB内{current_price:.4f})"
    
    # 多单OB：价格需在OB下方（OB high < current_price = OB失效）
    elif direction in ('LONG', '做多'):
        if current_price > ob_high * 1.005:  # 价格穿越OB上沿 = OB失效
            penalty = 30
            reason = f"LONG OB失效(价格{current_price:.4f}>OB上沿{ob_high:.4f})"
        elif current_price > ob_low * 1.01:
            penalty = 15
            reason = f"LONG OB部分失效"
    
    # 时间框架衰减：1H OB比4H OB衰减更快
    tf_penalty = {'1h': 1.0, '4h': 0.7, '1d': 0.5}.get(timeframe, 1.0)
    penalty = int(penalty * tf_penalty)
    
    adjusted = max(0, grade - penalty)
    return adjusted, reason if penalty > 0 else f"OB有效(penalty=0)"


def get_effective_grade(symbol: str, raw_grade: float, signal_data: dict,
                         current_price: float) -> tuple:
    """
    获取有效grade（应用所有衰减后）
    供brahma_analyze调用的统一入口
    
    Returns: (effective_grade, grade_detail)
    """
    grade = raw_grade
    details = []
    
    # 应用OB衰减
    ob_data = signal_data.get('ob_data') or {}
    if ob_data:
        grade, ob_reason = apply_ob_decay_penalty(grade, symbol, ob_data, current_price)
        details.append(f"OB: {ob_reason}")
    
    # Bridge-Gate/StructureGate最低门槛（grade<70直接拒绝）[v24.2]
    if grade < 70:
        details.append(f"grade={grade:.0f}<70 Bridge-Gate/StructureGate拒绝(TO率=73~100%)")
    
    return grade, " | ".join(details) if details else f"grade={grade:.0f}"
