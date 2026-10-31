#!/usr/bin/env python3
"""
brahma_core_final12.py — 梵天2.0 W2：12维终选打分层 [苏摩111 2026-09-28]

宪法铁律（W2新增）:
1. 94维照常计算（不删码），全部冻结进 _breakdown_full94（证据保留）
2. score_final = 12维终选合成（IC已证明或逻辑必然的维度）
3. 冷冻维度权重=0，复活需连续2周IC>+0.02+苏摩111（达摩院IC周审）
4. 本模块为纯函数：输入_result字典，输出(终选score, 12维明细)——回测同构

接入位置: brahma_brain/brahma_core.py analyze() return前（L3306前）
消费方: decision_engine / paper_executor / auto_analysis（score_final语义升级为12维口径）
"""
from __future__ import annotations


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def final12_score(_result: dict, ms: dict = None, extra_data: dict = None) -> tuple[float, dict]:
    """12维终选打分。返回(score12, detail)。

    输入: analyze()已完成的_result（含confluence/score_final/smc等全部94维证据）
    输出: 12维加权的终选分数 + 逐维明细（供breakdown审计）
    纯函数: 不改输入，不做IO，不联网。
    """
    ms = ms or {}
    extra_data = extra_data or {}
    cf = _result.get('confluence', {}) or {}
    smc = _result.get('smc', {}) or {}
    regime = str(_result.get('regime', ms.get('regime', 'UNKNOWN')) or 'UNKNOWN').upper()
    direction = str(_result.get('signal_dir', ms.get('signal_dir', 'NEUTRAL')) or 'NEUTRAL').upper()
    detail: dict = {}
    s = 0.0

    def add(name: str, pts: float, note: str):
        nonlocal s
        if pts:
            detail[name] = f'{pts:+.1f} {note}'
            s += pts

    # ── 维度1-2: 体制方向与乘数（80%胜负手，主锚） ──
    # 直接复用1.0已验证的体制→策略映射（宪法硬编码表同源）
    REGIME_MULT = {
        ('BEAR_TREND', 'SHORT'): 16.0, ('BEAR_TREND', 'LONG'): 5.0,
        ('BEAR_EARLY', 'SHORT'): 12.0, ('BEAR_EARLY', 'LONG'): 5.0,
        ('BULL_TREND', 'LONG'): 13.0, ('BULL_TREND', 'SHORT'): 5.0,
        ('BEAR_RECOVERY', 'LONG'): 12.5, ('BEAR_RECOVERY', 'SHORT'): 0.0,  # 严禁空
        ('CHOP_MID', 'LONG'): 5.0, ('CHOP_MID', 'SHORT'): 8.8,
        ('BULL_EARLY', 'LONG'): 10.0, ('BULL_EARLY', 'SHORT'): 5.0,
    }
    base = REGIME_MULT.get((regime, direction), 5.0)
    add('regime_direction', base, f'{regime}:{direction}体制锚(×10)')
    add('regime_multiplier', 0.0, '乘数已并入体制锚')

    # ── 维度3: FVG中点距离（入场位置质量） ──
    fvg = (smc.get('fvg') or {}) if isinstance(smc.get('fvg'), dict) else {}
    fvg_dist = fvg.get('mid_distance_pct') or _result.get('fvg_mid_distance_pct') or 0
    if fvg_dist:
        pts = 6.0 if fvg_dist <= 0.3 else (4.0 if fvg_dist <= 0.6 else (1.5 if fvg_dist <= 1.0 else 0.0))
        add('fvg_mid_distance', pts, f'距FVG中点{fvg_dist:.2f}%')

    # ── 维度4: OB有效性（age<50未穿越） ──
    ob = (smc.get('ob') or {}) if isinstance(smc.get('ob'), dict) else {}
    ob_age = ob.get('age_bars', 999)
    ob_touched = ob.get('touched', True)
    if ob and ob_age < 50 and not ob_touched:
        add('ob_validity', 6.0, f'有效OB age={ob_age}<50未穿越')
    elif ob:
        add('ob_validity', 0.0, f'OB失效(age={ob_age} touched={ob_touched})')

    # ── 维度5: 清算地图集群距离（猎物距离） ──
    liq = extra_data.get('liqmap') or _result.get('liqmap') or {}
    liq_dist = liq.get('nearest_cluster_pct') if isinstance(liq, dict) else None
    if liq_dist is not None:
        pts = 5.0 if liq_dist <= 0.5 else (2.5 if liq_dist <= 1.5 else 0.5)
        add('liq_cluster', pts, f'最近清算簇{liq_dist:.2f}%')

    # ── 维度6: 共振点7维（复用1.0三重共振判定） ──
    res = cf.get('triple_resonance_long') if direction == 'LONG' else cf.get('triple_resonance_short')
    if res is None:
        res = cf.get('resonance', {}).get(direction.lower()) if isinstance(cf.get('resonance'), dict) else None
    if res:
        add('resonance_score', 8.0, '三重共振成立')
    elif cf.get('l4_triple_resonance'):
        add('resonance_score', 4.0, 'L4共振部分成立')

    # ── 维度7: RSI极值 ──
    rsi = ms.get('rsi_1h') or (ms.get('momentum') or {}).get('rsi_1h')
    if rsi is not None:
        if direction == 'SHORT' and rsi >= 70:
            add('rsi_extreme', 8.0, f'RSI1h={rsi:.0f}超买做空')
        elif direction == 'SHORT' and rsi >= 60:
            add('rsi_extreme', 4.0, f'RSI1h={rsi:.0f}偏强警惕')
        elif direction == 'LONG' and rsi <= 30:
            add('rsi_extreme', 7.0, f'RSI1h={rsi:.0f}超卖做多')
        elif direction == 'LONG' and rsi <= 40:
            add('rsi_extreme', 3.5, f'RSI1h={rsi:.0f}偏弱区')
        else:
            add('rsi_extreme', 0.0, f'RSI1h={rsi:.0f}中性')

    # ── 维度8: BB挤压 ──
    squeeze = ms.get('bb_squeeze') or (ms.get('volatility') or {}).get('bb_squeeze')
    if squeeze:
        add('bb_squeeze', 5.0, 'BB挤压待突破')
    # 方向确认: 挤压后顺势加分已在体制锚中体现

    # ── 维度9: 方仓匹配（标的专属>0.4铁律） ──
    fc = _result.get('fangcang', {}) or {}
    fc_match = fc.get('match_score', 0)
    if fc_match >= 0.4:
        add('fangcang_match', 8.0, f'方仓匹配{fc_match:.2f}≥0.4')
    elif fc_match > 0:
        add('fangcang_match', -3.0, f'方仓匹配{fc_match:.2f}<0.4降权')

    # ── 维度10: Hurst体制确认 ──
    hurst = extra_data.get('hurst') or _result.get('hurst')
    if hurst is not None:
        if hurst > 0.55 and ('TREND' in regime):
            add('hurst_regime', 4.0, f'H={hurst:.2f}趋势确认')
        elif hurst < 0.45 and 'CHOP' in regime:
            add('hurst_regime', 4.0, f'H={hurst:.2f}震荡确认')
        elif hurst > 0.55 and 'CHOP' in regime:
            add('hurst_regime', -2.0, f'H={hurst:.2f}与CHOP体制矛盾')

    # ── 维度11: SL距离/ATR1H铁律比 ──
    atr1h = ms.get('atr_1h') or (ms.get('momentum') or {}).get('atr_1h') or 0
    sl_dist = (_result.get('params', {}) or {}).get('stop_loss_distance_pct') or 0
    if atr1h and sl_dist:
        ratio = sl_dist / (1.5 * atr1h)
        if ratio >= 1.0:
            add('atr_sl_ratio', 3.0, f'SL距离{sl_dist:.2%}≥1.5×ATR1H铁律达标')
        else:
            add('atr_sl_ratio', -6.0, f'SL距离{sl_dist:.2%}<1.5×ATR1H铁律({ratio:.2f}x)违规')

    # ── 维度12: 成本后EV（taker4bp+滑点3bp+funding1bp/8h） ──
    ev_net = _result.get('ev_net_pct', extra_data.get('ev_net_pct'))
    if ev_net is not None:
        if ev_net >= 0.5:
            add('cost_model', 6.0, f'成本后EV={ev_net:+.2f}%≥0.5%')
        elif ev_net >= 0:
            add('cost_model', 2.0, f'成本后EV={ev_net:+.2f}%为正')
        else:
            add('cost_model', -8.0, f'成本后EV={ev_net:+.2f}%<0拒绝')

    # ── 汇总 ──
    s = round(_clamp(s, 0, 165), 1)  # 与1.0同cap（165=体制cap惯例）
    return s, detail


def apply_final12(_result: dict, ms: dict = None, extra_data: dict = None) -> dict:
    """接线封装：冻结94维证据→写入12维终选。修改并返回_result。"""
    s12, detail = final12_score(_result, ms, extra_data)
    # 冻结94维原始终值（证据保留，不删除）
    _result['_breakdown_full94'] = {
        'score_final_94d': _result.get('score_final', _result.get('score', 0)),
        'confluence_score': (_result.get('confluence', {}) or {}).get('score', 0),
    }
    _result['confluence'] = (_result.get('confluence') or {})
    _result['confluence']['final12_detail'] = detail
    _result['confluence']['final12_total'] = s12
    _result['score_final'] = s12
    _result['score'] = s12
    _result['scoring_version'] = '2.0-final12'
    return _result
