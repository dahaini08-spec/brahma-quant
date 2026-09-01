"""
smc_resonance.py — 梵天强制前置链路模块
2026-08-31 苏摩111封印

职责：
  P1: FVG磁铁→OB有效性→清算地图→共振点 四步强制链路
  P2: 姓赵不宣VIP模版自动生成

接入位置：brahma_full_report.run_full_analysis() 精度执行层之后
"""

from __future__ import annotations
from typing import Optional


# ─────────────────────────────────────────────
# P1: 强制前置链路四步分析
# ─────────────────────────────────────────────

def run_smc_resonance(r: dict) -> dict:
    """
    强制走FVG→OB→清算→共振点四步链路
    返回结构化结果供VIP模版使用
    """
    price    = r.get('price', 0)
    symbol   = r.get('symbol', '')
    smc      = r.get('smc', {})
    ob_data  = smc.get('order_blocks', {})
    fvg_data = smc.get('fvg', {})
    liq_heat = r.get('_liq_heatmap', {})
    clusters = liq_heat.get('clusters', []) if isinstance(liq_heat, dict) else []
    atr1h    = r.get('momentum', {}).get('atr_1h', price * 0.01)
    min_sl   = atr1h * 1.5

    result = {
        'price': price,
        'symbol': symbol,
        'atr1h': round(atr1h, 2),
        'min_sl_dist': round(min_sl, 2),
        # Step1
        'bull_fvg': [],
        'bear_fvg': [],
        'fvg_magnet_dir': None,
        'fvg_magnet_target': None,
        # Step2
        'valid_bear_obs': [],
        'valid_bull_obs': [],
        # Step3
        'liq_up': [],
        'liq_dn': [],
        # Step4
        'resonance_short': None,
        'resonance_long': None,
        'verdict': 'WAIT',  # WAIT / SHORT / LONG
        'verdict_reason': '',
    }

    # ── Step1: FVG磁铁 ──
    bull_fvgs = fvg_data.get('bull_fvg', [])
    bear_fvgs = fvg_data.get('bear_fvg', [])

    for f in bull_fvgs:
        lo = f.get('bottom', 0); hi = f.get('top', 0); mid = f.get('mid', 0)
        filled = f.get('filled', False)
        if not filled and hi > 0:
            result['bull_fvg'].append({'lo': lo, 'hi': hi, 'mid': mid, 'gap_pct': f.get('gap_pct', 0)})

    for f in bear_fvgs:
        lo = f.get('bottom', 0); hi = f.get('top', 0); mid = f.get('mid', 0)
        filled = f.get('filled', False)
        if not filled and hi > 0:
            result['bear_fvg'].append({'lo': lo, 'hi': hi, 'mid': mid, 'gap_pct': f.get('gap_pct', 0)})

    # 磁铁方向判断
    bull_above = [f for f in result['bull_fvg'] if f['lo'] > price]
    bear_below = [f for f in result['bear_fvg'] if f['hi'] < price]
    bull_contain = [f for f in result['bull_fvg'] if f['lo'] <= price <= f['hi']]

    if bull_contain:
        # 价格在Bull FVG内 = 磁铁往上拉至中点
        nearest = sorted(bull_contain, key=lambda x: abs(x['mid'] - price))[0]
        result['fvg_magnet_dir'] = 'UP'
        result['fvg_magnet_target'] = nearest['mid']
    elif bull_above:
        nearest = sorted(bull_above, key=lambda x: x['lo'])[0]
        result['fvg_magnet_dir'] = 'UP'
        result['fvg_magnet_target'] = nearest['lo']
    elif bear_below:
        nearest = sorted(bear_below, key=lambda x: x['hi'], reverse=True)[0]
        result['fvg_magnet_dir'] = 'DOWN'
        result['fvg_magnet_target'] = nearest['hi']

    # ── Step2: OB有效性 ──
    for ob in ob_data.get('bear_obs', []):
        age = ob.get('age_bars', 999)
        broken = ob.get('broken', False)
        if age < 50 and not broken and ob.get('high', 0) > price:
            result['valid_bear_obs'].append({
                'lo': round(ob['low'], 2),
                'hi': round(ob['high'], 2),
                'age': age,
                'mid': round((ob['low'] + ob['high']) / 2, 2),
            })

    for ob in ob_data.get('bull_obs', []):
        age = ob.get('age_bars', 999)
        broken = ob.get('broken', False)
        if age < 50 and not broken and ob.get('low', 0) < price:
            result['valid_bull_obs'].append({
                'lo': round(ob['low'], 2),
                'hi': round(ob['high'], 2),
                'age': age,
                'mid': round((ob['low'] + ob['high']) / 2, 2),
            })

    # ── Step3: 清算地图 ──
    for c in clusters:
        if not isinstance(c, dict): continue
        cp = c.get('price', 0); cnt = c.get('count', 1); sz = c.get('size', 0)
        if cp > price:
            result['liq_up'].append({'price': cp, 'count': cnt, 'size': sz})
        else:
            result['liq_dn'].append({'price': cp, 'count': cnt, 'size': sz})

    result['liq_up'] = sorted(result['liq_up'], key=lambda x: x['count'], reverse=True)
    result['liq_dn'] = sorted(result['liq_dn'], key=lambda x: x['count'], reverse=True)

    # ── Step4: 共振点识别 ──
    # 做空共振：FVG中点（上方）+ 有效Bear OB + 上方清算山
    short_resonance = _find_short_resonance(
        price, result['bull_fvg'], result['valid_bear_obs'], result['liq_up'], min_sl
    )
    # 做多共振：FVG中点（下方）+ 有效Bull OB + 下方清算池
    long_resonance = _find_long_resonance(
        price, result['bear_fvg'], result['valid_bull_obs'], result['liq_dn'], min_sl
    )

    result['resonance_short'] = short_resonance
    result['resonance_long']  = long_resonance

    # 裁决
    if short_resonance and short_resonance.get('score', 0) >= 2:
        result['verdict'] = 'SHORT'
        result['verdict_reason'] = short_resonance.get('reason', '')
    elif long_resonance and long_resonance.get('score', 0) >= 2:
        result['verdict'] = 'LONG'
        result['verdict_reason'] = long_resonance.get('reason', '')
    else:
        result['verdict'] = 'WAIT'
        missing = []
        if not short_resonance: missing.append('做空无共振点')
        if not long_resonance:  missing.append('做多无共振点')
        result['verdict_reason'] = '，'.join(missing) or '等待结构确认'

    return result


def _find_short_resonance(price, bull_fvgs, valid_bear_obs, liq_up, min_sl):
    """寻找做空共振点：FVG中点/上沿 + 有效Bear OB + 清算山"""
    candidates = []

    # FVG提供的做空目标位
    fvg_targets = []
    for f in bull_fvgs:
        if f['mid'] > price:
            fvg_targets.append(f['mid'])   # FVG中点
        if f['hi'] > price:
            fvg_targets.append(f['hi'])    # FVG上沿

    # OB提供的做空目标位
    ob_targets = [ob['hi'] for ob in valid_bear_obs if ob['hi'] > price]

    # 清算山提供的做空目标位
    liq_targets = [c['price'] for c in liq_up if c['count'] >= 3]

    if not (fvg_targets or ob_targets):
        return None

    # 找三者交叉点（在容忍范围内）
    tol = min_sl * 0.8  # 容忍区间
    best = None
    best_score = 0

    all_targets = set()
    for t in fvg_targets + ob_targets + liq_targets:
        all_targets.add(round(t, 0))

    for target in sorted(all_targets):
        if target <= price: continue
        score = 0
        reasons = []

        # FVG命中
        for f in bull_fvgs:
            if abs(f['mid'] - target) <= tol:
                score += 1; reasons.append(f"FVG中点${target:,.2f}")
            if abs(f['hi'] - target) <= tol:
                score += 1; reasons.append(f"FVG上沿${target:,.2f}")

        # OB命中
        for ob in valid_bear_obs:
            if abs(ob['hi'] - target) <= tol or (ob['lo'] <= target <= ob['hi']):
                score += 1; reasons.append(f"Bear OB(age={ob['age']}bars)")

        # 清算命中
        for c in liq_up:
            if abs(c['price'] - target) <= tol:
                score += 1; reasons.append(f"清算山×{c['count']} ${c['size']/1e6:.0f}M")

        if score > best_score:
            best_score = score
            best = {
                'entry': round(target, 2),
                'score': score,
                'reason': ' + '.join(reasons[:3]),
                'sl': round(target + max(min_sl, tol) + 0.5, 2),
                'wait_dist': round(target - price, 2),
            }

    return best if best and best_score >= 2 else None


def _find_long_resonance(price, bear_fvgs, valid_bull_obs, liq_dn, min_sl):
    """寻找做多共振点：Bear FVG中点 + 有效Bull OB + 清算池"""
    candidates = []

    fvg_targets = []
    for f in bear_fvgs:
        if f['mid'] < price:
            fvg_targets.append(f['mid'])
        if f['lo'] < price:
            fvg_targets.append(f['lo'])

    ob_targets = [ob['lo'] for ob in valid_bull_obs if ob['lo'] < price]
    liq_targets = [c['price'] for c in liq_dn if c['count'] >= 3]

    if not (fvg_targets or ob_targets):
        return None

    tol = min_sl * 0.8
    best = None
    best_score = 0

    all_targets = set()
    for t in fvg_targets + ob_targets + liq_targets:
        all_targets.add(round(t, 0))

    for target in sorted(all_targets, reverse=True):
        if target >= price: continue
        score = 0
        reasons = []

        for f in bear_fvgs:
            if abs(f['mid'] - target) <= tol:
                score += 1; reasons.append(f"Bear FVG中点${target:,.2f}")
            if abs(f['lo'] - target) <= tol:
                score += 1; reasons.append(f"Bear FVG下沿${target:,.2f}")

        for ob in valid_bull_obs:
            if abs(ob['lo'] - target) <= tol or (ob['lo'] <= target <= ob['hi']):
                score += 1; reasons.append(f"Bull OB(age={ob['age']}bars)")

        for c in liq_dn:
            if abs(c['price'] - target) <= tol:
                score += 1; reasons.append(f"清算池×{c['count']} ${c['size']/1e6:.0f}M")

        if score > best_score:
            best_score = score
            best = {
                'entry': round(target, 2),
                'score': score,
                'reason': ' + '.join(reasons[:3]),
                'sl': round(target - max(min_sl, tol) - 0.5, 2),
                'wait_dist': round(price - target, 2),
            }

    return best if best and best_score >= 2 else None


# ─────────────────────────────────────────────
# P2: 姓赵不宣VIP模版自动生成
# ─────────────────────────────────────────────

def format_vip_card(r: dict, res: dict) -> str:
    """
    自动生成姓赵不宣VIP卡片格式
    严格按照截图模版，不自创格式
    """
    import requests as _req
    from datetime import datetime

    price   = res['price']
    symbol  = res['symbol']
    verdict = res['verdict']

    # 24H涨跌幅
    try:
        r24 = _req.get('https://fapi.binance.com/fapi/v1/ticker/24hr',
            params={'symbol': symbol}, timeout=5).json()
        chg_pct = float(r24['priceChangePercent'])
        chg_str = f"+{chg_pct:.2f}%" if chg_pct >= 0 else f"{chg_pct:.2f}%"
    except:
        chg_str = "N/A"

    # 标的简写
    sym_short = symbol.replace('USDT', '')
    ts = datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')

    lines = []
    lines.append(f"🌿 **姓赵不宣 丨 {sym_short} ({chg_str})** 今日布局")

    rs  = res.get('resonance_short')
    rl  = res.get('resonance_long')
    liq_dn = res.get('liq_dn', [])
    liq_up = res.get('liq_up', [])

    # ── 空单 ──
    if rs:
        entry = rs['entry']
        sl    = rs['sl']
        sl_dist = sl - entry

        # TP = 下方清算池
        tps = sorted([c['price'] for c in liq_dn if c['price'] < entry], reverse=True)
        tp1 = tps[0] if len(tps) > 0 else round(entry * 0.98, 2)
        tp2 = tps[1] if len(tps) > 1 else round(entry * 0.97, 2)
        tp3 = tps[2] if len(tps) > 2 else round(entry * 0.96, 2)

        # 杠杆判断（100X清算位 = entry×1.0095，若SL>清算位 → 降杠杆）
        liq_100x = round(entry * 1.0095, 2)
        lev = '20x' if liq_100x < sl else '100x'
        nav = '2%'

        lines.append(
            f"🔴 **空单** 等 **${entry:,.2f}** 反弹入场 "
            f"止损 **${sl:,.2f}** "
            f"目标 ${tp1:,.2f} / ${tp2:,.2f} / ${tp3:,.2f} "
            f"杠杆{lev} 仓{nav}"
        )
    else:
        lines.append(f"🔴 **空单** 暂无共振点，等待结构")

    # ── 多单 ──
    if rl:
        entry_l = rl['entry']
        sl_l    = rl['sl']

        tps_up = sorted([c['price'] for c in liq_up if c['price'] > entry_l])
        tp1_l = tps_up[0] if len(tps_up) > 0 else round(entry_l * 1.02, 2)
        tp2_l = tps_up[1] if len(tps_up) > 1 else round(entry_l * 1.03, 2)

        # 做多方式：猎杀被扫后接
        liq_near = sorted([c for c in liq_dn if c['price'] > sl_l and c['price'] < entry_l],
                          key=lambda x: x['price'])
        if liq_near:
            scan_price = liq_near[0]['price']
            zone_lo = round(scan_price - res['atr1h'] * 0.3, 2)
            zone_hi = round(scan_price + res['atr1h'] * 0.3, 2)
            lines.append(
                f"🟢 **多单** 等 **${scan_price:,.2f}** 猎杀被扫后 "
                f"${zone_lo:,.2f}~${zone_hi:,.2f} 接 "
                f"止损 **${sl_l:,.2f}** "
                f"目标 ${tp1_l:,.2f} / ${tp2_l:,.2f} "
                f"杠杆20x 仓1%"
            )
        else:
            lines.append(
                f"🟢 **多单** 等 **${entry_l:,.2f}** 接 "
                f"止损 **${sl_l:,.2f}** "
                f"目标 ${tp1_l:,.2f} / ${tp2_l:,.2f} "
                f"杠杆20x 仓1%"
            )
    else:
        lines.append(f"🟢 **多单** 暂无共振点，等待结构")

    # ── 主方向逻辑 ──
    if verdict == 'SHORT' and rs:
        core = f"主方向做空，{rs['reason']}，等触及+15M顶背离再入"
    elif verdict == 'LONG' and rl:
        core = f"主方向做多，{rl['reason']}，等猎杀确认+15M企稳再入"
    else:
        core = f"等待，无共振点（{res['verdict_reason']}），不操作"

    lines.append(f"⚠️ {core}")
    lines.append(f"*{ts} | price_ts实时 ${price:,.2f}*")

    return '\n'.join(lines)


# ─────────────────────────────────────────────
# 主入口：输出完整强制链路板块
# ─────────────────────────────────────────────

def format_smc_block(r: dict) -> str:
    """完整输出强制前置链路 + VIP卡片"""
    lines = []
    lines.append('')
    lines.append('╬══════════════════════════════════════════════════════════')
    lines.append('  🏛️ 强制前置链路 FVG→OB→清算→共振')
    lines.append('╬══════════════════════════════════════════════════════════')

    try:
        res = run_smc_resonance(r)
        price = res['price']

        # Step1 FVG
        lines.append(f"  【Step1 FVG磁铁】")
        for f in res['bull_fvg'][:2]:
            lines.append(f"  Bull FVG: ${f['lo']:,.2f}~${f['hi']:,.2f} 中点=${f['mid']:,.2f} gap={f['gap_pct']:.2f}% 磁铁↑")
        for f in res['bear_fvg'][:2]:
            lines.append(f"  Bear FVG: ${f['lo']:,.2f}~${f['hi']:,.2f} 中点=${f['mid']:,.2f} gap={f['gap_pct']:.2f}% 磁铁↓")
        if res['fvg_magnet_dir']:
            lines.append(f"  磁铁方向: {res['fvg_magnet_dir']} → 目标 ${res['fvg_magnet_target']:,.2f}")
        else:
            lines.append(f"  磁铁方向: 无明确FVG")

        # Step2 OB
        lines.append(f"  【Step2 OB有效性】")
        if res['valid_bear_obs']:
            for ob in res['valid_bear_obs'][:2]:
                lines.append(f"  Bear OB: ${ob['lo']:,.2f}~${ob['hi']:,.2f} age={ob['age']}bars ✅有效")
        else:
            lines.append(f"  Bear OB: 无有效（已穿越或age>50）")
        if res['valid_bull_obs']:
            for ob in res['valid_bull_obs'][:2]:
                lines.append(f"  Bull OB: ${ob['lo']:,.2f}~${ob['hi']:,.2f} age={ob['age']}bars ✅有效")
        else:
            lines.append(f"  Bull OB: 无有效")

        # Step3 清算
        lines.append(f"  【Step3 清算地图】")
        if res['liq_up']:
            lines.append(f"  上方止损山: " + " | ".join([f"${c['price']:,.2f}×{c['count']} ${c['size']/1e6:.0f}M" for c in res['liq_up'][:3]]))
        if res['liq_dn']:
            lines.append(f"  下方止损池: " + " | ".join([f"${c['price']:,.2f}×{c['count']} ${c['size']/1e6:.0f}M" for c in res['liq_dn'][:3]]))
        lines.append(f"  100X清算: 空头${price*1.0095:,.2f} / 多头${price*0.9905:,.2f}")

        # Step4 共振
        lines.append(f"  【Step4 共振点识别】")
        rs = res['resonance_short']
        rl = res['resonance_long']
        if rs:
            lines.append(f"  做空共振: ${rs['entry']:,.2f} score={rs['score']} ({rs['reason']}) 等+${rs['wait_dist']:.2f}")
        else:
            lines.append(f"  做空共振: ❌无")
        if rl:
            lines.append(f"  做多共振: ${rl['entry']:,.2f} score={rl['score']} ({rl['reason']}) 等-${rl['wait_dist']:.2f}")
        else:
            lines.append(f"  做多共振: ❌无")

        # 裁决
        lines.append(f"  【裁决】{res['verdict']} — {res['verdict_reason']}")
        lines.append('')

        # VIP卡片
        lines.append('╬══════════════════════════════════════════════════════════')
        lines.append('  🌿 VIP策略（姓赵不宣格式）')
        lines.append('╬══════════════════════════════════════════════════════════')
        vip = format_vip_card(r, res)
        lines.append(vip)

    except Exception as e:
        lines.append(f"  ⚠️ 强制链路异常: {e}")

    lines.append('╬══════════════════════════════════════════════════════════')
    return '\n'.join(lines)
