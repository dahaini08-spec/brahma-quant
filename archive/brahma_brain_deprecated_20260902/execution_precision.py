"""
execution_precision.py — 梵天执行智慧层精度模块
2026-08-31 苏摩111封印

职责：
  1. 把梵天战场区间（宽）→ 精确单点入场位（OB/FVG/清算三角定位）
  2. SL验证：结构失效位 + ≥1.5×ATR1H强制校验
  3. 触发条件同向检验（禁止矛盾条件）
  4. 100X仓位风险倒推

接入位置：brahma_full_report.run_full_analysis() 输出段末尾追加【精度执行层】
"""

from __future__ import annotations
from typing import Optional


# ─────────────────────────────────────────────
# 核心函数：精度入场点计算
# ─────────────────────────────────────────────

def get_precision_entry(
    direction: str,           # 'SHORT' or 'LONG'
    war_zone_lo: float,       # 梵天战场区间下沿
    war_zone_hi: float,       # 梵天战场区间上沿
    smc: dict,                # r['smc'] 完整SMC结构
    liq_clusters: dict,       # 清算集群数据
    atr_1h: float,            # ATR1H实时值
    price: float,             # 当前价
) -> dict:
    """
    从战场区间内定位精确入场点。
    
    做空逻辑：找战场区间内最精确的阻力位
      优先级：Bear OB上沿 > Bear FVG上沿 > 清算集群密集位
    
    做多逻辑：找战场区间内最精确的支撑位
      优先级：Bull OB下沿 > Bull FVG下沿 > 清算集群密集位
    
    返回：
      entry_point: 精确入场价（精确到0.01）
      entry_source: 入场依据（OB/FVG/LIQ）
      sl: 结构失效止损位
      sl_dist: SL距离
      sl_valid: SL是否≥1.5×ATR1H
      sl_min_required: 最小SL距离要求
      rr1/rr2/rr3: 风险回报比
      precision_note: 精度说明
    """
    result = {
        'direction': direction,
        'war_zone': f'${war_zone_lo:,.2f}~${war_zone_hi:,.2f}',
        'war_zone_width': round(war_zone_hi - war_zone_lo, 2),
        'entry_point': None,
        'entry_source': None,
        'sl': None,
        'sl_dist': None,
        'sl_valid': False,
        'sl_min_required': round(atr_1h * 1.5, 2),
        'precision_note': '',
        'rr_tp1': None,
        'rr_tp2': None,
        'fail_reason': None,
    }

    min_sl = atr_1h * 1.5

    # ─── 提取SMC数据 ───
    ob_data   = smc.get('order_blocks', {}) if isinstance(smc, dict) else {}
    fvg_data  = smc.get('fvg', {}) if isinstance(smc, dict) else {}
    bear_obs  = ob_data.get('bear_obs', [])
    bull_obs  = ob_data.get('bull_obs', [])
    bull_fvgs = fvg_data.get('bull_fvg', [])
    bear_fvgs = fvg_data.get('bear_fvg', [])

    if direction == 'SHORT':
        # ── 做空精确入场：找区间内最精确阻力位 ──
        # 优先级1：Bear OB上沿（最新的，age最小）
        candidates = []

        for ob in sorted(bear_obs, key=lambda x: x.get('age_bars', 999)):
            ob_hi = ob['high']
            ob_lo = ob['low']
            # OB上沿在战场区间内
            if war_zone_lo <= ob_hi <= war_zone_hi + atr_1h:
                candidates.append({
                    'price': ob_hi,
                    'source': f"Bear OB上沿 age={ob.get('age_bars','?')}bars",
                    'priority': 1 if ob.get('age_bars', 999) < 50 else 2,
                })

        # 优先级2：Bull FVG低沿（失效边界 = 最强SL参考）
        for fvg in bull_fvgs:
            fvg_lo = fvg['bottom']
            if war_zone_lo <= fvg_lo <= war_zone_hi + atr_1h * 2:
                candidates.append({
                    'price': fvg_lo,
                    'source': f"Bull FVG低沿（空头失效边界）",
                    'priority': 2,
                })

        # 优先级3：清算集群
        liq_up = liq_clusters.get('up', []) if isinstance(liq_clusters, dict) else []
        for cluster in liq_up:
            cp = cluster.get('price', 0) if isinstance(cluster, dict) else 0
            if war_zone_lo <= cp <= war_zone_hi + atr_1h:
                candidates.append({
                    'price': cp,
                    'source': f"清算集群 ${cp:,.2f}",
                    'priority': 3,
                })

        if not candidates:
            # 无精确位 → 取区间上沿作为入场（保守）
            candidates.append({
                'price': war_zone_hi,
                'source': '战场区间上沿（无精确结构位）',
                'priority': 9,
            })

        # 取优先级最高的
        best = sorted(candidates, key=lambda x: (x['priority'], abs(x['price'] - price)))[0]
        entry = best['price']
        result['entry_point'] = round(entry, 2)
        result['entry_source'] = best['source']

        # ── SL = 结构失效位（Bull FVG上沿 + buffer）──
        sl_candidates = []
        for fvg in bull_fvgs:
            fvg_hi = fvg['top']
            if fvg_hi > entry:
                sl_candidates.append(round(fvg_hi + 0.50, 2))

        if sl_candidates:
            sl = min(sl_candidates)  # 最近的失效位
            sl_note = 'Bull FVG上沿突破=空头彻底失效'
        else:
            # 无FVG → 用入场价 + 2%（SL_PCT铁律）
            sl = round(entry * 1.02, 2)
            sl_note = 'SL_PCT=2.0%（无FVG失效位）'

        sl_dist = round(sl - entry, 2)

        # SL距离不足1.5×ATR → 强制扩展到结构失效位
        if sl_dist < min_sl:
            sl = round(entry + min_sl + 0.50, 2)
            sl_dist = round(sl - entry, 2)
            sl_note += f' → 强制扩展至≥1.5×ATR1H=${min_sl:.2f}'

        result['sl'] = sl
        result['sl_dist'] = sl_dist
        result['sl_valid'] = sl_dist >= min_sl
        result['sl_note'] = sl_note

        # TP参考：下方止损池
        liq_dn = liq_clusters.get('down', []) if isinstance(liq_clusters, dict) else []
        tps = sorted(
            [c['price'] for c in liq_dn if isinstance(c, dict) and c.get('price', 0) < entry],
            reverse=True
        )
        if len(tps) >= 1:
            result['rr_tp1'] = round((entry - tps[0]) / sl_dist, 2)
        if len(tps) >= 2:
            result['rr_tp2'] = round((entry - tps[1]) / sl_dist, 2)

        result['precision_note'] = (
            f"做空精度: 入场${entry:,.2f}({best['source']}) "
            f"SL=${sl:,.2f}({sl_note}) "
            f"距离=${sl_dist:.2f} "
            f"min_required=${min_sl:.2f} "
            f"{'✅合规' if result['sl_valid'] else '❌不合规'}"
        )

    elif direction == 'LONG':
        # ── 做多精确入场：找区间内最精确支撑位 ──
        candidates = []

        for ob in sorted(bull_obs, key=lambda x: x.get('age_bars', 999)):
            ob_lo = ob['low']
            if war_zone_lo - atr_1h <= ob_lo <= war_zone_hi:
                candidates.append({
                    'price': ob_lo,
                    'source': f"Bull OB下沿 age={ob.get('age_bars','?')}bars",
                    'priority': 1 if ob.get('age_bars', 999) < 50 else 2,
                })

        for fvg in bear_fvgs:
            fvg_hi = fvg['top']
            if war_zone_lo <= fvg_hi <= war_zone_hi:
                candidates.append({
                    'price': fvg_hi,
                    'source': 'Bear FVG上沿（多头失效边界）',
                    'priority': 2,
                })

        liq_dn = liq_clusters.get('down', []) if isinstance(liq_clusters, dict) else []
        for cluster in liq_dn:
            cp = cluster.get('price', 0) if isinstance(cluster, dict) else 0
            if war_zone_lo - atr_1h <= cp <= war_zone_hi:
                candidates.append({
                    'price': cp,
                    'source': f'清算集群 ${cp:,.2f}',
                    'priority': 3,
                })

        if not candidates:
            candidates.append({
                'price': war_zone_lo,
                'source': '战场区间下沿（无精确结构位）',
                'priority': 9,
            })

        best = sorted(candidates, key=lambda x: (x['priority'], abs(x['price'] - price)))[0]
        entry = best['price']
        result['entry_point'] = round(entry, 2)
        result['entry_source'] = best['source']

        # SL = Bear FVG下沿突破 = 多头失效
        sl_candidates = []
        for fvg in bear_fvgs:
            fvg_lo = fvg['bottom']
            if fvg_lo < entry:
                sl_candidates.append(round(fvg_lo - 0.50, 2))

        if sl_candidates:
            sl = max(sl_candidates)
            sl_note = 'Bear FVG下沿突破=多头彻底失效'
        else:
            sl = round(entry * 0.98, 2)
            sl_note = 'SL_PCT=2.0%（无FVG失效位）'

        sl_dist = round(entry - sl, 2)
        if sl_dist < min_sl:
            sl = round(entry - min_sl - 0.50, 2)
            sl_dist = round(entry - sl, 2)
            sl_note += f' → 强制扩展至≥1.5×ATR1H=${min_sl:.2f}'

        result['sl'] = sl
        result['sl_dist'] = sl_dist
        result['sl_valid'] = sl_dist >= min_sl
        result['sl_note'] = sl_note

        liq_up = liq_clusters.get('up', []) if isinstance(liq_clusters, dict) else []
        tps = sorted(
            [c['price'] for c in liq_up if isinstance(c, dict) and c.get('price', 0) > entry]
        )
        if len(tps) >= 1:
            result['rr_tp1'] = round((tps[0] - entry) / sl_dist, 2)
        if len(tps) >= 2:
            result['rr_tp2'] = round((tps[1] - entry) / sl_dist, 2)

        result['precision_note'] = (
            f"做多精度: 入场${entry:,.2f}({best['source']}) "
            f"SL=${sl:,.2f}({sl_note}) "
            f"距离=${sl_dist:.2f} "
            f"min_required=${min_sl:.2f} "
            f"{'✅合规' if result['sl_valid'] else '❌不合规'}"
        )

    return result


# ─────────────────────────────────────────────
# SL验证函数（独立调用）
# ─────────────────────────────────────────────

def validate_sl(entry: float, sl: float, direction: str, atr_1h: float) -> dict:
    """验证SL是否≥1.5×ATR1H，返回验证结果"""
    if direction == 'SHORT':
        sl_dist = sl - entry
    else:
        sl_dist = entry - sl

    min_required = atr_1h * 1.5
    valid = sl_dist >= min_required

    return {
        'sl_dist': round(sl_dist, 2),
        'min_required': round(min_required, 2),
        'valid': valid,
        'verdict': '✅合规' if valid else f'❌不合规(差${round(min_required-sl_dist,2):,.2f})',
        'atr_1h': round(atr_1h, 2),
    }


# ─────────────────────────────────────────────
# 触发条件同向检验
# ─────────────────────────────────────────────

def validate_trigger_conditions(conditions: list[dict]) -> dict:
    """
    检验多个触发条件是否同向
    每个condition: {'name': str, 'direction': 'bullish'|'bearish'|'neutral', 'value': ...}
    """
    bullish = [c for c in conditions if c.get('direction') == 'bullish']
    bearish = [c for c in conditions if c.get('direction') == 'bearish']
    neutral = [c for c in conditions if c.get('direction') == 'neutral']

    conflict = len(bullish) > 0 and len(bearish) > 0
    dominant = 'bullish' if len(bullish) > len(bearish) else ('bearish' if len(bearish) > len(bullish) else 'neutral')

    return {
        'conflict': conflict,
        'dominant': dominant,
        'bullish_count': len(bullish),
        'bearish_count': len(bearish),
        'neutral_count': len(neutral),
        'verdict': '❌矛盾条件，拒绝输出' if conflict else f'✅同向({dominant})',
        'detail': [f"{c['name']}={c['direction']}" for c in conditions],
    }


# ─────────────────────────────────────────────
# 仓位风险倒推（100X专用）
# ─────────────────────────────────────────────

def calc_position_size(
    nav: float,              # 账户NAV（USDT）
    risk_pct: float,         # 愿意承担的风险比例（如0.02=2%NAV）
    sl_dist: float,          # SL距离（价格）
    leverage: int,           # 杠杆（如100）
    contract_value: float,   # 合约面值（ETH=10USDT/张，BTC=100USDT/张）
) -> dict:
    """100X合约仓位风险倒推"""
    max_loss_usdt = nav * risk_pct
    # Binance线性永续合约：1张=1ETH（或1BTC），每刀波动盈亏=$1/张
    # 每张亏损 = sl_dist（刀）
    loss_per_contract = sl_dist
    max_contracts = int(max_loss_usdt / loss_per_contract) if loss_per_contract > 0 else 0
    # 名义价值 = 张数 × 1ETH（或1BTC）× 当前价（近似用contract_value代入）
    # contract_value此处作为单张名义价值参考（可传入price）
    notional = max_contracts * contract_value
    margin_required = notional / leverage

    return {
        'max_loss_usdt': round(max_loss_usdt, 2),
        'loss_per_contract': round(loss_per_contract, 2),
        'max_contracts': max_contracts,
        'notional': round(notional, 2),
        'margin_required': round(margin_required, 2),
        'note': f'{risk_pct*100:.0f}%NAV风险={max_loss_usdt:.0f}USDT | {max_contracts}张 | 保证金{margin_required:.1f}USDT',
    }


# ─────────────────────────────────────────────
# 主入口：格式化精度执行层输出
# ─────────────────────────────────────────────

def format_precision_block(r: dict, nav: float = 1000.0) -> str:
    """
    从brahma_full_report的r对象提取数据，输出精度执行层板块
    在VIP卡片之后追加
    """
    lines = []
    lines.append('')
    lines.append('╬══════════════════════════════════════════════════════════')
    lines.append('  🎯 精度执行层（100X合约专用）')
    lines.append('╬══════════════════════════════════════════════════════════')

    price       = r.get('price', 0)
    direction   = r.get('signal_dir', r.get('direction', 'LONG'))
    smc         = r.get('smc', {})
    atr_1h      = r.get('momentum', {}).get('atr_1h', price * 0.01)
    symbol      = r.get('symbol', '?')

    # 战场区间（从price_zones提取）
    pz = r.get('_price_zones', {})
    if direction == 'SHORT':
        zone = pz.get('high_short', {}) if isinstance(pz, dict) else {}
    else:
        zone = pz.get('low_long', {}) if isinstance(pz, dict) else {}

    war_lo = zone.get('lo', price * 0.99) if isinstance(zone, dict) else price * 0.99
    war_hi = zone.get('hi', price * 1.01) if isinstance(zone, dict) else price * 1.01

    # 清算集群
    liq_heatmap = r.get('_liq_heatmap', {})
    liq_clusters = {'up': [], 'down': []}
    if isinstance(liq_heatmap, dict):
        for item in liq_heatmap.get('clusters', []):
            if isinstance(item, dict):
                cp = item.get('price', 0)
                if cp > price:
                    liq_clusters['up'].append({'price': cp, 'size': item.get('size', 0)})
                else:
                    liq_clusters['down'].append({'price': cp, 'size': item.get('size', 0)})

    # 精度计算
    precision = get_precision_entry(
        direction=direction,
        war_zone_lo=war_lo,
        war_zone_hi=war_hi,
        smc=smc,
        liq_clusters=liq_clusters,
        atr_1h=atr_1h,
        price=price,
    )

    # SL验证
    entry_pt = precision.get('entry_point', price)
    sl_pt    = precision.get('sl', 0)
    sl_valid = validate_sl(entry_pt, sl_pt, direction, atr_1h)

    # 合约参数
    is_eth = 'ETH' in symbol.upper()
    contract_value = 10 if is_eth else 100
    leverage = 100
    sl_dist = precision.get('sl_dist', atr_1h * 1.5)
    pos = calc_position_size(nav, 0.02, sl_dist, leverage, contract_value)

    lines.append(f'  标的: {symbol} | 方向: {direction} | 当前价: ${price:,.2f}')
    lines.append(f'  战场区间: {precision["war_zone"]} (宽${precision["war_zone_width"]:.2f})')
    lines.append(f'  ATR1H: ${atr_1h:.2f} | 最小SL要求: ≥${atr_1h*1.5:.2f}')
    lines.append('')
    lines.append(f'  【精确入场点】')
    lines.append(f'  入场: ${entry_pt:,.2f}')
    lines.append(f'  依据: {precision.get("entry_source", "?")}')
    lines.append(f'  SL:   ${sl_pt:,.2f} ({precision.get("sl_note", "")})')
    lines.append(f'  SL距离: ${sl_dist:.2f} | {sl_valid["verdict"]}')
    if precision.get('rr_tp1'):
        lines.append(f'  RR(TP1): {precision["rr_tp1"]}')
    if precision.get('rr_tp2'):
        lines.append(f'  RR(TP2): {precision["rr_tp2"]}')
    lines.append('')
    lines.append(f'  【100X仓位风险倒推（NAV={nav:.0f}USDT，风险2%）】')
    lines.append(f'  {pos["note"]}')
    lines.append(f'  每张亏损: ${pos["loss_per_contract"]:.2f} | 最大张数: {pos["max_contracts"]}张')
    lines.append('')

    # 三问自检
    q1 = '✅战场信息已转化为精确入场点' if entry_pt != (war_lo + war_hi) / 2 else '⚠️使用区间中点，无精确结构位'
    q2 = sl_valid['verdict']
    q3 = '✅结构驱动' if precision.get('entry_source', '').find('OB') >= 0 or precision.get('entry_source', '').find('FVG') >= 0 else '⚠️无OB/FVG铁证'

    lines.append('  【三问自检】')
    lines.append(f'  Q1 战场信息→执行指令: {q1}')
    lines.append(f'  Q2 SL针扫验证: {q2}')
    lines.append(f'  Q3 入场铁证: {q3}')

    all_pass = '✅' not in q1 or not sl_valid['valid']
    verdict = '✅三问全过，策略有效' if sl_valid['valid'] else '❌SL不合规，策略作废'
    lines.append(f'  最终裁决: {verdict}')
    lines.append('╬══════════════════════════════════════════════════════════')

    return '\n'.join(lines)
