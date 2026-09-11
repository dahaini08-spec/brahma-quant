#!/usr/bin/env python3
"""
trader_brain.py — 6层确定性交易员大脑
设计院封印 2026-09-11 苏摩111

替代评分器+AI议会的决策层。
全部确定性规则，无LLM，无随机性。

接入位置：brahma_manual_analysis.py step10_vip() → trader_brain.decide()
"""
import math
from typing import Dict, Any, List, Tuple

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 6层决策引擎
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def decide(
    # Layer 1: 环境
    regime: str,
    score: float,
    grade: float,
    macro: Dict,
    risk: Dict,
    hurst: float,
    # Layer 2: 结构
    fvg: Dict,
    ob: Dict,
    liq: Dict,
    atr_1h: float,
    atr_4h: float,
    price: float,
    # Layer 3: 资金流
    oi: Dict,
    sm: Dict,
    # Layer 4: 波动率
    vol: Dict,
    # 共振
    res: Dict,
    symbol: str = '',
) -> Dict[str, Any]:
    """
    40年顶级合约交易员大脑 — 6层确定性决策
    
    输出：
      action: 'ENTER' | 'WAIT'
      direction: 'LONG' | 'SHORT'
      entry_lo, entry_hi, sl, tp1, tp2, tp3
      rr, sl_pct, leverage, position_pct
      confidence, cross_check
      reason: str
      missing: list
    """

    # ── Layer 1: 大环境层 ──────────────────────────────────────
    direction = 'LONG' if 'BULL' in regime or 'RECOVERY' in regime else 'SHORT' if 'BEAR' in regime else 'NONE'
    if regime == 'CHOP_MID':
        direction = 'NONE'

    # 交易许可
    regime_state = risk.get('regime_state', 'GREEN')
    permission = True
    if regime == 'CHOP_MID' and score < 110:
        permission = False
    if regime_state == 'RED' and score < 120:
        permission = False

    # 杠杆基数
    lev_base = 10
    if regime_state == 'RED':
        lev_base = 5
    elif regime_state == 'YELLOW':
        lev_base = 7

    # 宏观压制
    if macro.get('high_impact'):
        lev_base = min(lev_base, 5)

    # Hurst可信度
    hurst_confidence = 1.0
    if hurst < 0.5:
        hurst_confidence = 0.7
    elif hurst < 0.55:
        hurst_confidence = 0.85

    # ── Layer 2: 结构层 ────────────────────────────────────────
    fvg_consensus = fvg.get('consensus', fvg.get('dir', 'NONE'))
    structure_direction = 'LONG' if fvg_consensus == 'BULL' else 'SHORT' if fvg_consensus == 'BEAR' else 'NONE'

    # 入场区 = max(共振区下沿, 支撑池)
    entry_lo = res.get('entry_lo', 0)
    entry_hi = res.get('entry_hi', 0)
    liq_support = res.get('liq_nearest_long', 0) or liq.get('nearest_long', 0)

    if direction == 'LONG' and liq_support > 0 and entry_lo > 0 and entry_lo < liq_support:
        shift = liq_support - entry_lo
        entry_lo = round(liq_support, 1)
        entry_hi = round(entry_hi + shift, 1)
        if entry_hi <= entry_lo:
            entry_hi = round(entry_lo * 1.005, 1)

    # SL = max(SL_PCT铁律, 1.5×ATR4H)
    if direction == 'LONG':
        sl_pct_required = 0.02
        min_sl = max(entry_lo * sl_pct_required, atr_4h * 1.5) if atr_4h else entry_lo * sl_pct_required
        sl = round(entry_lo - min_sl, 1) if entry_lo > 0 else 0
        sl_pct = round((entry_lo - sl) / entry_lo * 100, 2) if entry_lo > 0 and sl > 0 else 0
        # TP = 清算止损墙
        tp1 = liq.get('nearest_short', 0) if liq.get('nearest_short', 0) > price else round(price + atr_1h * 2.5, 1)
        tp2 = round(tp1 + atr_1h * 2, 1) if tp1 > 0 else 0
        tp3 = round(tp2 + atr_1h * 1.5, 1) if tp2 > 0 else 0
    elif direction == 'SHORT':
        sl_pct_required = 0.025 if 'BULL' in regime else 0.02
        min_sl = max(entry_hi * sl_pct_required, atr_4h * 1.5) if atr_4h else entry_hi * sl_pct_required
        sl = round(entry_hi + min_sl, 1) if entry_hi > 0 else 0
        sl_pct = round((sl - entry_hi) / entry_hi * 100, 2) if entry_hi > 0 and sl > 0 else 0
        tp1 = liq.get('nearest_long', 0) if liq.get('nearest_long', 0) < price and liq.get('nearest_long', 0) > 0 else round(price - atr_1h * 2.5, 1)
        tp2 = round(tp1 - atr_1h * 2, 1) if tp1 > 0 else 0
        tp3 = round(tp2 - atr_1h * 1.5, 1) if tp2 > 0 else 0
    else:
        sl = 0; sl_pct = 0; tp1 = 0; tp2 = 0; tp3 = 0

    # RR验证
    if direction == 'LONG' and entry_lo > 0 and sl > 0 and tp1 > 0:
        rr = round((tp1 - entry_lo) / (entry_lo - sl), 2)
    elif direction == 'SHORT' and entry_hi > 0 and sl > 0 and tp1 > 0:
        rr = round((entry_hi - tp1) / (sl - entry_hi), 2)
    else:
        rr = 0

    # ── Layer 3: 资金流层 ─────────────────────────────────────
    oi_signal = oi.get('signal', 'NO_DATA')
    cvd_1h = oi.get('cvd_1h', 0)
    oi_bull = oi_signal in ('LONG_BUILD', 'SHORT_SQUEEZE')
    money_flow_direction = 'LONG' if oi_bull else 'SHORT' if oi_signal in ('SHORT_BUILD', 'LONG_UNWIND') else 'NONE'

    # OI+CVD交叉验证
    cvd_consistent = True
    if oi_bull and cvd_1h < 0:
        cvd_consistent = False
    elif not oi_bull and cvd_1h > 0:
        cvd_consistent = False

    # 聪明钱 vs 散户
    big_long = sm.get('big_long', 50)
    retail_long = sm.get('retail_long', 50)
    smart_money_divergence = abs(big_long - retail_long)
    smart_money_bull = big_long > retail_long  # 大户比散户看多=正向

    # ── Layer 4: 波动率层 ──────────────────────────────────────
    kappa = vol.get('kappa', 0)
    gex_bias = vol.get('gex_bias', 'NEUTRAL')
    bb_w = vol.get('bb_width', 0)
    iv_rank = vol.get('iv_rank', 50)

    vol_direction = 'NONE'
    if kappa < -0.05 and gex_bias != 'POSITIVE':
        vol_direction = 'LONG'
    elif kappa > 0.05 and gex_bias != 'NEGATIVE':
        vol_direction = 'SHORT'

    # SL铁律验证
    sl_distance = abs(entry_lo - sl) if direction == 'LONG' else abs(sl - entry_hi) if direction == 'SHORT' else 0
    atr4h_threshold = atr_4h * 1.5 if atr_4h else 0
    sl_valid = sl_distance >= atr4h_threshold and sl_pct >= (sl_pct_required * 100 - 0.01) if sl_distance > 0 else False

    # ── Layer 5: 交叉验证层 ────────────────────────────────────
    layer_directions = {
        'regime': direction,
        'structure': structure_direction,
        'money_flow': money_flow_direction,
        'volatility': vol_direction,
    }

    # 统计一致数
    if direction == 'NONE':
        consistent_count = 0
    else:
        consistent_count = sum(1 for v in layer_directions.values() if v == direction)
    # 矛盾列表
    conflicts = []
    for name, dir_val in layer_directions.items():
        if dir_val != 'NONE' and dir_val != direction and direction != 'NONE':
            conflicts.append(f'{name}={dir_val}')

    # 置信度系数
    if consistent_count >= 3:
        confidence = 'HIGH'
        confidence_mult = 1.0
    elif consistent_count == 2:
        confidence = 'MED'
        confidence_mult = 0.7
    else:
        confidence = 'LOW'
        confidence_mult = 0.5

    # OI+CVD矛盾降置信
    if not cvd_consistent:
        confidence_mult *= 0.8
        if confidence == 'HIGH':
            confidence = 'MED'
        elif confidence == 'MED':
            confidence = 'LOW'

    # ── Layer 6: 交易员大脑（决策层）──────────────────────────

    # 检查所有许可条件
    missing = []
    
    if not permission:
        if regime == 'CHOP_MID' and score < 110:
            missing.append(f'CHOP体制score={score:.0f}<110')
        elif regime_state == 'RED' and score < 120:
            missing.append(f'失效期RED score={score:.0f}<120')
        else:
            missing.append('环境许可未通过')

    if consistent_count < 2 and direction != 'NONE':
        missing.append(f'交叉验证仅{consistent_count}/4一致')

    if entry_lo == 0 or entry_hi == 0:
        missing.append('入场区=0（方向矛盾）')

    if direction == 'NONE':
        missing.append(f'体制{regime}无方向')

    if sl > 0 and not sl_valid:
        missing.append(f'SL不通过ATR4H铁律(SL={sl_pct:.2f}%, 需≥{sl_pct_required*100:.1f}%)')

    if rr < 2.0 and rr > 0:
        missing.append(f'RR={rr:.1f}<2.0')

    if rr == 0 and direction != 'NONE':
        missing.append('RR无法计算')

    # 死穴门控
    dead_zone = False
    if 'BULL' in regime and direction == 'LONG' and score >= 140:
        dead_zone = True
        missing.append(f'死穴:BULL:LONG:score≥140→WR=0%~30%')

    # 做多但OI方向相反
    if direction == 'LONG' and oi_signal in ('SHORT_BUILD',):
        missing.append(f'做多但OI={oi_signal}')
    if direction == 'SHORT' and oi_signal in ('LONG_BUILD',):
        missing.append(f'做空但OI={oi_signal}')

    # 最终决策
    action = 'ENTER' if len(missing) == 0 and direction != 'NONE' else 'WAIT'

    # 仓位计算
    if action == 'ENTER':
        # score系数（生产评分器最佳区间120-139）
        if score >= 120 and score < 140:
            score_mult = 1.0 + (score - 120) / 100.0  # 120→1.0, 139→1.19
        elif score >= 110:
            score_mult = 0.9
        else:
            score_mult = 0.8

        nav_mult = risk.get('nav_mult', 1.0)
        base_pos = 5.0  # 基准5%NAV
        position_pct = max(1, round(base_pos * confidence_mult * nav_mult * score_mult))
        leverage = lev_base

        # 失效期再降
        if regime_state == 'RED':
            position_pct = max(1, position_pct // 2)
            leverage = max(3, leverage // 2)
    else:
        position_pct = 0
        leverage = 0

    # 确定性理由生成
    if action == 'ENTER':
        reason_parts = [f'{regime}体制顺势{direction}']
        if fvg_consensus != 'NONE':
            reason_parts.append(f'FVG{fvg_consensus}共识')
        if oi_signal != 'NO_DATA':
            reason_parts.append(f'OI={oi_signal}')
        if cvd_consistent:
            reason_parts.append('CVD一致')
        else:
            reason_parts.append('CVD矛盾')
        reason_parts.append(f'交叉验证{consistent_count}/4')
        reason_parts.append(f'置信{confidence}')
        reason = ' + '.join(reason_parts)
    else:
        reason = f'WAIT — ' + ' / '.join(missing[:3]) if missing else 'WAIT'

    # 矛盾裁决（确定性）
    conflict_resolution = ''
    if oi_bull != smart_money_bull and oi_signal != 'NO_DATA':
        oi_change = abs(oi.get('total_change', 0))
        if oi_change > 5000:
            conflict_resolution = f'OI vs 聪明钱矛盾 → 跟随OI({oi_signal})'
        else:
            conflict_resolution = f'OI vs 聪明钱矛盾 → 跟随聪明钱({sm.get("signal","")})'

    return {
        'action': action,
        'direction': direction if direction != 'NONE' else 'NONE',
        'entry_lo': entry_lo,
        'entry_hi': entry_hi,
        'sl': sl,
        'tp1': tp1,
        'tp2': tp2,
        'tp3': tp3,
        'rr': rr,
        'sl_pct': sl_pct,
        'leverage': leverage,
        'position_pct': position_pct,
        'confidence': confidence,
        'consistent_count': consistent_count,
        'cross_check': {
            'consistent': len(conflicts) == 0,
            'conflicts': conflicts,
            'layer_directions': layer_directions,
        },
        'conflict_resolution': conflict_resolution,
        'reason': reason,
        'missing': missing,
        'dead_zone': dead_zone,
        # Layer details for display
        'layer1': {
            'direction': direction,
            'permission': permission,
            'regime_state': regime_state,
            'lev_base': lev_base,
            'hurst_confidence': hurst_confidence,
        },
        'layer2': {
            'structure_direction': structure_direction,
            'entry_lo': entry_lo,
            'entry_hi': entry_hi,
            'sl': sl,
            'sl_valid': sl_valid,
            'tp1': tp1,
        },
        'layer3': {
            'money_flow_direction': money_flow_direction,
            'cvd_consistent': cvd_consistent,
            'smart_money_divergence': smart_money_divergence,
            'smart_money_bull': smart_money_bull,
        },
        'layer4': {
            'vol_direction': vol_direction,
            'kappa': kappa,
            'gex_bias': gex_bias,
        },
        'layer5': {
            'consistent_count': consistent_count,
            'confidence': confidence,
            'confidence_mult': confidence_mult,
        },
    }


def format_vip_card(result: Dict, symbol: str, price: float, regime: str) -> str:
    """格式化VIP卡片输出（姓赵不宣格式）
    ENTER → 完整VIP卡片
    WAIT  → 观点卡片（有方向+入场区+缺失条件，不是空白）
    """
    sym = symbol.replace('USDT', '')
    d = result['direction']
    emoji = '🟢' if d == 'LONG' else '🔴' if d == 'SHORT' else '⚪'
    entry_lo = result.get('entry_lo', 0)
    entry_hi = result.get('entry_hi', 0)
    sl = result.get('sl', 0)
    tp1 = result.get('tp1', 0)
    tp2 = result.get('tp2', 0)
    tp3 = result.get('tp3', 0)
    rr = result.get('rr', 0)
    sl_pct = result.get('sl_pct', 0)
    missing = result.get('missing', [])
    cross = result.get('consistent_count', 0)
    layer_dirs = result.get('cross_check', {}).get('layer_directions', {})

    if result['action'] != 'ENTER':
        # ── WAIT观点卡片：有方向就给入场区，没方向就给监测位 ──
        lines = [f'🌿 姓赵不宣 | {sym} 今日观点', '']

        if d != 'NONE' and entry_lo > 0 and entry_hi > 0:
            # 有方向+有入场区 → 给具体点位，标注条件未满
            lines.append(
                f'{emoji} {"多单" if d == "LONG" else "空单"}｜'
                f'{"回调" if d == "LONG" else "反弹"}入场区 ${entry_lo:,.1f}~${entry_hi:,.1f}'
            )
            if sl > 0:
                lines.append(f'止损 ${sl:,.1f}｜目标 ${tp1:,.0f}→${tp2:,.0f}→${tp3:,.0f}')
                lines.append(f'RR={rr:.1f}x  SL={sl_pct:.1f}%')
            lines.append(f'交叉验证 {cross}/4  ' + ' '.join(f'{k}={v}' for k,v in layer_dirs.items()))
            lines.append('')
            # 列出缺失条件
            if missing:
                lines.append(f'⏳ 待确认：{" / ".join(missing)}')
            lines.append(f'⚠️ 方向{d}，条件未满，等确认后入场')
        elif d != 'NONE':
            # 有方向但入场区=0 → 给监测位
            lines.append(f'{emoji} 偏{d}｜监测位 ${price:,.1f}')
            lines.append(f'交叉验证 {cross}/4  ' + ' '.join(f'{k}={v}' for k,v in layer_dirs.items()))
            lines.append('')
            if missing:
                lines.append(f'⏳ 待确认：{" / ".join(missing)}')
            lines.append(f'⚠️ 方向{d}但无共振入场区，等结构形成')
        else:
            # 无方向 → 给区间监测
            lines.append(f'⚪ 无方向｜现价 ${price:,.1f}')
            lines.append(f'交叉验证 {cross}/4  ' + ' '.join(f'{k}={v}' for k,v in layer_dirs.items()))
            lines.append('')
            if missing:
                lines.append(f'⏳ {" / ".join(missing)}')
            lines.append('⚠️ 体制无方向，等趋势确认')

        if result.get('conflict_resolution'):
            lines.append(f'⚙️ {result["conflict_resolution"]}')
        lines.append('📊 梵天系统｜数据驱动｜不是建议')
        return '\n'.join(lines)

    # ── ENTER: 完整VIP卡片 ──
    lev = result['leverage']
    pos = result['position_pct']

    card = (
        f'🌿 姓赵不宣 | {sym} 今日布局\n'
        f'\n'
        f'{emoji} {"多单" if d == "LONG" else "空单"}｜'
        f'{"回调" if d == "LONG" else "反弹"}入场区 ${entry_lo:,.1f}~${entry_hi:,.1f}\n'
        f'止损 ${sl:,.1f}｜目标 ${tp1:,.0f}→${tp2:,.0f}→${tp3:,.0f}\n'
        f'杠杆 {lev}x｜仓位 {pos}%  RR={rr:.1f}x  SL={sl_pct:.1f}% ✅\n'
        f'\n'
    )

    # 副方向
    if 'BULL' in regime or 'RECOVERY' in regime:
        card += f'🔴 暂无空单｜等待结构\n'
    elif 'BEAR' in regime:
        card += f'🟢 暂无多单｜等待结构\n'
    else:
        card += f'⚠️ CHOP体制｜无方向\n'

    # 作废线
    card += f'\n🚫 破${sl:,.1f}策略作废\n'
    card += f'\n⚠️ {result["reason"]}\n'
    if result['conflict_resolution']:
        card += f'⚙️ {result["conflict_resolution"]}\n'
    card += f'📊 梵天系统｜数据驱动｜不是建议'

    return card
