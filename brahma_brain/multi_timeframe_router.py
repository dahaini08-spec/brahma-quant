#!/usr/bin/env python3
"""
multi_timeframe_router.py — v21.0 多周期自顶向下入场区路由器
设计院 2026-06-08

核心逻辑：4H战略区 → 1H确认位（自顶向下）
  旧逻辑：1H优先找结构，1H没有才用4H（自底向上 → 导致山腰入场）
  新逻辑：先扫4H找最强结构，再用1H精确确认位（山顶入场）

复盘教训（2026-06-07）：
  BTC SHORT 09:48 → 1H OB $62,500（山腰，被扫止损）
  BTC SHORT 19:13 → 4H OB $64,500（山顶，RR=4.29，高质量）
  两者同时存在于图表，系统却只看到1H
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ─── 常量 ─────────────────────────────────────────────────────
GRADE_4H_STRONG   = 70   # 4H结构≥70 → 强制使用4H入场区
GRADE_4H_MODERATE = 50   # 4H结构≥50 → 4H/1H共振优先
GRADE_1H_MIN      = 60   # 1H结构低于此值 → 警告「弱结构」
MTF_GAP_UPGRADE   = 2.0  # 4H入场区gap>2% → 明显优于1H时强制升级


def route_entry_zone(
    symbol: str,
    signal_dir: str,       # 'LONG' / 'SHORT'
    price: float,
    smc_1h: dict,          # analyze_smc(..., '1h', ...)
    smc_4h: dict,          # analyze_smc(..., '4h', ...)
    fib_1h: dict = None,
    fib_4h: dict = None,
) -> dict:
    """
    自顶向下确定入场区。
    返回：
    {
      'entry_lo': float,
      'entry_hi': float,
      'timeframe': '4H' | '1H' | 'FIB',
      'grade': float,          # structure_quality_engine grade
      'source': str,           # 'OB' | 'FVG' | 'FIB'
      'quality': str,          # 'STRONG' | 'MODERATE' | 'WEAK'
      'warning': str,          # 逆势/弱结构警告（空字符串=无）
      '4h_zone': dict | None,  # 4H战略区（始终返回，供参考）
      '1h_zone': dict | None,  # 1H确认区
      'upgrade_reason': str,   # 升级原因说明
    }
    """
    is_short = (signal_dir == 'SHORT')
    result = {
        'entry_lo': 0, 'entry_hi': 0,
        'timeframe': '1H', 'grade': 0, 'source': 'FIB',
        'quality': 'WEAK', 'warning': '',
        '4h_zone': None, '1h_zone': None,
        'upgrade_reason': '',
    }

    # ── Step 1：提取4H结构 ───────────────────────────────────
    zone_4h = _extract_zone(smc_4h, price, is_short, '4H')
    result['4h_zone'] = zone_4h

    # ── Step 2：提取1H结构 ───────────────────────────────────
    zone_1h = _extract_zone(smc_1h, price, is_short, '1H')
    result['1h_zone'] = zone_1h

    # ── Step 3：自顶向下决策树 ──────────────────────────────

    # Case A：4H有强结构（grade≥70）→ 强制使用4H，1H只做确认
    if zone_4h and zone_4h['grade'] >= GRADE_4H_STRONG:
        result.update({
            'entry_lo':  zone_4h['lo'],
            'entry_hi':  zone_4h['hi'],
            'timeframe': '4H',
            'grade':     zone_4h['grade'],
            'source':    zone_4h['source'],
            'quality':   'STRONG',
            'upgrade_reason': f'4H {zone_4h["source"]} grade={zone_4h["grade"]:.0f}≥{GRADE_4H_STRONG}，强制自顶向下',
        })
        # 如果1H有结构且在4H区间内，收窄入场区（精确入场）
        if zone_1h and zone_1h['lo'] >= zone_4h['lo'] and zone_1h['hi'] <= zone_4h['hi'] * 1.005:
            result['entry_lo'] = zone_1h['lo']
            result['entry_hi'] = zone_1h['hi']
            result['upgrade_reason'] += f' | 1H {zone_1h["source"]} 在4H区间内，精确入场'
        return result

    # Case B：4H有中等结构（grade 50~70）且明显优于1H（gap差>2%）→ 升级到4H
    if zone_4h and zone_4h['grade'] >= GRADE_4H_MODERATE:
        _4h_gap = (zone_4h['lo'] - price) / price * 100 if is_short else (price - zone_4h['hi']) / price * 100
        _1h_gap = (zone_1h['lo'] - price) / price * 100 if (is_short and zone_1h) else 999
        gap_diff = _4h_gap - _1h_gap if zone_1h else 999

        if gap_diff >= MTF_GAP_UPGRADE or (zone_1h and zone_1h.get('grade', 0) < GRADE_1H_MIN):
            result.update({
                'entry_lo':  zone_4h['lo'],
                'entry_hi':  zone_4h['hi'],
                'timeframe': '4H',
                'grade':     zone_4h['grade'],
                'source':    zone_4h['source'],
                'quality':   'MODERATE',
                'upgrade_reason': (
                    f'4H {zone_4h["source"]} grade={zone_4h["grade"]:.0f}，'
                    f'gap差={gap_diff:.1f}%>{MTF_GAP_UPGRADE}%，升级4H入场'
                ),
            })
            if zone_1h:
                result['warning'] = f'1H结构grade={zone_1h.get("grade",0):.0f}弱于4H，已自动升级到4H入场区'
            return result

    # Case C：4H结构较弱或无，用1H（但加警告）
    if zone_1h:
        _warn = ''
        if zone_4h and zone_4h['grade'] >= GRADE_4H_MODERATE:
            _warn = f'⚠️ 4H有更强结构({zone_4h["source"]} grade={zone_4h["grade"]:.0f})，建议等待4H入场区${zone_4h["lo"]:.0f}~${zone_4h["hi"]:.0f}'
        elif zone_1h.get('grade', 0) < GRADE_1H_MIN:
            _warn = f'⚠️ 弱结构信号（grade={zone_1h.get("grade",0):.0f}<{GRADE_1H_MIN}），入场区可靠性低'

        result.update({
            'entry_lo':  zone_1h['lo'],
            'entry_hi':  zone_1h['hi'],
            'timeframe': '1H',
            'grade':     zone_1h.get('grade', 0),
            'source':    zone_1h['source'],
            'quality':   'WEAK' if zone_1h.get('grade', 0) < GRADE_1H_MIN else 'MODERATE',
            'warning':   _warn,
            'upgrade_reason': '1H结构，4H无强结构，使用1H入场',
        })
        return result

    # Case D：无任何有效结构 → FIB降级
    result.update({
        'timeframe': 'FIB',
        'grade': 0,
        'source': 'FIB',
        'quality': 'WEAK',
        'warning': '⚠️ 无SMC结构（OB/FVG均缺失），Fib降级入场，胜率较低',
        'upgrade_reason': '无1H/4H结构，降级Fib',
    })
    return result


def _extract_zone(smc: dict, price: float, is_short: bool, tf: str) -> dict | None:
    """从smc数据提取入场区，返回标准化结构"""
    if not smc:
        return None

    ob_key  = 'nearest_bear_ob' if is_short else 'nearest_bull_ob'
    fvg_key = 'nearest_bear'    if is_short else 'nearest_bull'

    ob  = smc.get('order_blocks', {}).get(ob_key)
    fvg = smc.get('fvg', {}).get(fvg_key)

    lo, hi, source, raw_grade = 0, 0, 'FIB', 0

    # FVG优先（更精确）
    _fvg_ok = (
        fvg and
        fvg.get('gap_pct', 0) >= 0.3 and
        (
            (is_short and fvg.get('bottom', 0) > price * 1.003) or
            (not is_short and fvg.get('top', 0) < price * 0.997)
        )
    )
    if _fvg_ok:
        lo = fvg.get('bottom', fvg.get('low', price))
        hi = fvg.get('top',    fvg.get('high', price * 1.005))
        source = 'FVG'
        raw_grade = 60 + min(fvg.get('gap_pct', 0) * 10, 30)  # 60~90
    elif ob:
        ob_lo = ob.get('low', 0); ob_hi = ob.get('high', 0)
        _ob_valid = (
            (is_short and ob_lo > price * 1.001) or
            (not is_short and ob_hi < price * 0.999) or
            (ob_lo <= price <= ob_hi)  # 价格在OB内
        )
        if _ob_valid and ob_lo > 0:
            lo = ob_lo; hi = ob_hi
            source = 'OB'
            # OB强度估算：距现价越近且结构越强 → grade越高
            gap = abs(lo - price) / price * 100
            raw_grade = 70 if gap <= 1.0 else (65 if gap <= 2.0 else 55)
    else:
        return None  # 无有效结构

    if lo <= 0 or hi <= 0:
        return None

    # 尝试用structure_quality_engine精确计算grade
    try:
        from brahma_brain.structure_quality_engine import evaluate_structure_quality
        _sq = evaluate_structure_quality(
            entry_lo=lo, entry_hi=hi, price=price,
            ob=ob, fvg=fvg if _fvg_ok else None,
            swing_highs=[], swing_lows=[],
        )
        raw_grade = _sq.get('grade', raw_grade)
    except Exception:
        pass  # 保留估算值

    return {
        'lo': round(lo, 6), 'hi': round(hi, 6),
        'source': source, 'grade': raw_grade,
        'tf': tf,
        'gap_pct': abs(lo - price) / price * 100,
    }


if __name__ == '__main__':
    """自测：模拟BTC SHORT 09:48（应输出升级到4H建议）"""
    # 模拟场景：1H有$62,500 OB，4H有$64,500 OB
    mock_smc_1h = {
        'order_blocks': {'nearest_bear_ob': {'low': 62500, 'high': 63200}},
        'fvg': {'nearest_bear': None},
    }
    mock_smc_4h = {
        'order_blocks': {'nearest_bear_ob': {'low': 64500, 'high': 64800}},
        'fvg': {'nearest_bear': None},
    }

    result = route_entry_zone('BTCUSDT', 'SHORT', 62000, mock_smc_1h, mock_smc_4h)
    print(f'入场区: ${result["entry_lo"]:,.0f}~${result["entry_hi"]:,.0f}')
    print(f'周期: {result["timeframe"]}  结构: {result["source"]}  grade: {result["grade"]:.0f}  质量: {result["quality"]}')
    print(f'升级原因: {result["upgrade_reason"]}')
    if result['warning']:
        print(f'警告: {result["warning"]}')
    print()
    print(f'4H战略区: ${result["4h_zone"]["lo"]:,.0f}~${result["4h_zone"]["hi"]:,.0f} grade={result["4h_zone"]["grade"]:.0f}' if result['4h_zone'] else '4H: 无')
    print(f'1H确认区: ${result["1h_zone"]["lo"]:,.0f}~${result["1h_zone"]["hi"]:,.0f} grade={result["1h_zone"]["grade"]:.0f}' if result['1h_zone'] else '1H: 无')
