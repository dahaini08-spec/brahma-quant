#!/usr/bin/env python3
"""
battlefield_intel_v2.py — 梵天战场情报融合引擎 V2
设计院封印 2026-09-03 苏摩111

架构思想：
  从「展示数据」→「回答交易员三个问题」

  问题1：我在哪？（战场定位）
    → GEX磁铁位 + ATR分位 + HCME历史相似度 + 体制乘数
  
  问题2：主力往哪打？（方向与猎杀目标）
    → 聪明钱方向 + CVD买卖压力 + 跨所FR套利 + 清算密度地图
    → 期权PC比 + OI异动 + 宏观日历压制
  
  问题3：怎么进？（精确执行条件）
    → 时段WR窗口 + FIB共振 + OB/FVG入场区
    → VaR风险校验 + SL位计算 + 信号置信度

覆盖维度：74项中的核心35项直接注入战场报告
"""
import json, sys, time, urllib.request
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))

# ── 数据源 ─────────────────────────────────────────────────────────
GEX_STATE      = BASE / 'data' / 'gex_state.json'
VOL_BETA_STATE = BASE / 'data' / 'vol_beta_state.json'
OI_WATCHLIST   = BASE / 'data' / 'oi_watchlist.json'
BRAHMA_STATE   = BASE / 'data' / 'brahma_state.json'
INTEL_LAST     = BASE / 'data' / 'battlefield_intel_v2_last.json'

def load_json(path):
    try:
        p = Path(path)
        if p.exists():
            return json.loads(p.read_text())
    except Exception:
        pass
    return {}

def live_price(symbol):
    try:
        url = f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={symbol}'
        return float(json.loads(urllib.request.urlopen(url, timeout=5).read())['price'])
    except Exception:
        return 0.0

# ═══════════════════════════════════════════════════════════════════
# 问题1：我在哪？
# ═══════════════════════════════════════════════════════════════════

def q1_where_am_i(state: dict, gex: dict, vb: dict, sym: str) -> dict:
    """
    战场定位：综合判断当前价格在战场中的位置
    返回：zone(强势/弱势/无人区/雷区)，支撑/阻力，历史相似度
    """
    extra = state.get('extra', {})
    bd    = state.get('confluence', {}).get('breakdown', {})
    price = state.get('price', 0) or live_price(f'{sym}USDT')
    regime= state.get('regime', 'CHOP_MID')

    # GEX定位
    g     = gex.get(sym, {})
    max_s = g.get('max_gex_strike', 0)
    min_s = g.get('min_gex_strike', 0)
    flip  = g.get('zero_flip', 0)
    fib382= g.get('fib_382', 0)
    fib618= g.get('fib_618', 0)
    fib500= g.get('fib_500', 0)

    dist_max = (max_s - price) / price * 100 if price else 0
    dist_min = (price - min_s) / price * 100 if price else 0

    # ATR分位：<15th=压缩期(即将爆发) >85th=扩张期(回调风险)
    atr_p  = extra.get('atr_percentile', {})
    atr_pct= atr_p.get('atr_percentile', 50)
    atr_regime = atr_p.get('regime', 'NORMAL')

    # HCME历史相似度
    hcme_score  = float(bd.get('HCME情境匹配', 0) or 0)
    extreme_evt = str(bd.get('extreme_event', '') or '')

    # VaR风险
    var_d   = extra.get('var', {})
    var95   = var_d.get('var_95_pct', 0.5)

    # 基差（现货-期货价差 → 正=期货溢价=多方强/负=期货折价=空方强）
    basis_d = extra.get('basis', {})
    basis   = basis_d.get('basis_pct', 0)

    # 综合定位
    if price > fib618 and dist_max < 2:
        zone = '🔴 高空区（紧贴钉子墙，做多风险极高）'
        zone_code = 'DANGER_TOP'
    elif price < fib382 and dist_min < 2:
        zone = '🟢 低多区（接近支撑墙，做多性价比高）'
        zone_code = 'BUY_ZONE'
    elif fib382 <= price <= fib618:
        zone = '⚖️ 中性区（FIB黄金分割带，等方向选择）'
        zone_code = 'NEUTRAL'
    elif price > max_s:
        zone = '🚨 钉子墙上方（极度超买，警惕急速回落）'
        zone_code = 'ABOVE_WALL'
    elif price < min_s:
        zone = '⚡ 支撑墙下方（已穿越，加速下行风险）'
        zone_code = 'BELOW_SUPPORT'
    else:
        zone = '🔵 观察区'
        zone_code = 'WATCHING'

    return {
        'price':      price,
        'zone':       zone,
        'zone_code':  zone_code,
        'regime':     regime,
        'fib382':     fib382,
        'fib500':     fib500,
        'fib618':     fib618,
        'wall_up':    max_s,
        'wall_dn':    min_s,
        'zero_flip':  flip,
        'dist_up':    round(dist_max, 1),
        'dist_dn':    round(dist_min, 1),
        'atr_pct':    round(atr_pct, 1),
        'atr_regime': atr_regime,
        'hcme_sim':   round(hcme_score, 2),
        'extreme_evt':extreme_evt[:60] if extreme_evt else '',
        'basis':      round(basis, 4),
        'var95':      round(var95, 2),
    }

# ═══════════════════════════════════════════════════════════════════
# 问题2：主力往哪打？
# ═══════════════════════════════════════════════════════════════════

def q2_where_is_target(state: dict, vb: dict, oi_wl: dict, sym: str) -> dict:
    """
    方向判断：多维度合力，判断主力下一猎杀目标
    返回：direction_bias(LONG/SHORT/NEUTRAL)，置信度，主要驱动因子
    """
    extra = state.get('extra', {})
    bd    = state.get('confluence', {}).get('breakdown', {})
    price = state.get('price', 0)

    bull_signals = []
    bear_signals = []

    # ── 聪明钱（大户 vs 散户分歧）──────────────────────────
    smart = extra.get('smart_money', {})
    big_acct_long = smart.get('big_acct_long', 0.5)
    big_pos_long  = smart.get('big_pos_long', 0.5)
    retail_long   = smart.get('retail_long', 0.5)
    if big_pos_long > 0.6 and big_pos_long > retail_long + 0.05:
        bull_signals.append(f'聪明钱多头({big_pos_long:.0%}↑ 散户{retail_long:.0%})')
    elif big_pos_long < 0.45 and big_pos_long < retail_long - 0.05:
        bear_signals.append(f'聪明钱空头({big_pos_long:.0%}↓ 散户{retail_long:.0%})')

    # ── CVD买卖压力 ─────────────────────────────────────────
    enhanced = extra.get('enhanced', {})
    cvd_score = enhanced.get('breakdown', {}).get('cvd', 0)
    if cvd_score >= 6:
        bull_signals.append(f'CVD买压主导(+{cvd_score})')
    elif cvd_score <= -6:
        bear_signals.append(f'CVD卖压主导({cvd_score})')

    # ── 跨所FR套利信号 ─────────────────────────────────────
    cross_fr = extra.get('cross_fr_basis', {})
    fr_b = cross_fr.get('binance_fr', 0)
    fr_y = cross_fr.get('bybit_fr', 0)
    fr_avg = (fr_b + fr_y) / 2
    if fr_avg < -0.01:
        bull_signals.append(f'FR极端负值({fr_avg:.4f}%) → 轧空燃料')
    elif fr_avg > 0.05:
        bear_signals.append(f'FR极端多头({fr_avg:.4f}%) → 多头过热')

    # ── 期权Put/Call比 ────────────────────────────────────
    deribit = extra.get('deribit_pc', {})
    pc_ratio = deribit.get('pc_oi_ratio', 1.0)
    pc_signal = deribit.get('signal', '')
    if pc_ratio < 0.7:  # Call远多于Put → 多头狂热
        bull_signals.append(f'期权多头狂热(PC={pc_ratio:.2f} Call重)')
    elif pc_ratio > 1.3:  # Put远多于Call → 市场恐慌
        bear_signals.append(f'期权恐慌(PC={pc_ratio:.2f} Put重)')

    # ── VolBeta偏斜 ──────────────────────────────────────
    v = vb.get(sym, {})
    kappa = v.get('kappa', 0)
    if kappa < -0.08:
        bull_signals.append(f'期权多头偏斜(κ={kappa:.3f})')
    elif kappa > 0.08:
        bear_signals.append(f'期权空头偏斜(κ={kappa:.3f})')

    # ── 链上OI/LS/Taker ──────────────────────────────────
    oc = extra.get('onchain_ws', {})
    oc_bd = oc.get('breakdown', {})
    taker = oc_bd.get('taker_score', 0)
    ls_s  = oc_bd.get('ls_score', 0)
    if taker >= 5:
        bull_signals.append(f'Taker主动买强(+{taker})')
    elif taker <= -5:
        bear_signals.append(f'Taker主动卖强({taker})')
    if ls_s >= 4:
        bull_signals.append(f'多空比偏多(+{ls_s})')
    elif ls_s <= -4:
        bear_signals.append(f'多空比偏空({ls_s})')

    # ── 宏观日历压制 ──────────────────────────────────────
    mac_cal = extra.get('macro_calendar', {})
    cal_score = mac_cal.get('score', 0)
    events = mac_cal.get('upcoming_events', [])
    upcoming_high = [e for e in events if e.get('impact') == 'HIGH']
    if upcoming_high:
        bear_signals.append(f'宏观风险({upcoming_high[0].get("event","?")} {upcoming_high[0].get("date","?")})')
    elif cal_score > 5:
        bull_signals.append(f'宏观顺风(score={cal_score})')

    # ── OI Watchlist异动 ────────────────────────────────
    oi_alerts = []
    for osym, v_oi in oi_wl.items():
        if isinstance(v_oi, dict) and v_oi.get('triggered'):
            oi_alerts.append(f'{osym} {v_oi.get("direction","?")}触发')

    # ── HCME历史情境 ─────────────────────────────────────
    bd_data = state.get('confluence', {}).get('breakdown', {})
    extreme = str(bd_data.get('extreme_event', '') or '')
    if 'UP' in extreme and '相似' in extreme:
        bull_signals.append(f'历史相似情境看涨({extreme[:30]})')
    elif 'DOWN' in extreme and '相似' in extreme:
        bear_signals.append(f'历史相似情境看跌({extreme[:30]})')

    # ── 综合方向判断 ──────────────────────────────────────
    bull_count = len(bull_signals)
    bear_count = len(bear_signals)
    total = bull_count + bear_count or 1

    bull_pct = round(bull_count / total * 100)
    bear_pct = 100 - bull_pct

    if bull_pct >= 65:
        bias = 'LONG'
        bias_icon = '🟢'
        bias_text = '多方主导'
    elif bear_pct >= 65:
        bias = 'SHORT'
        bias_icon = '🔴'
        bias_text = '空方主导'
    else:
        bias = 'NEUTRAL'
        bias_icon = '⚖️'
        bias_text = '多空均势'

    confidence = round(max(bull_pct, bear_pct))

    return {
        'bias':         bias,
        'bias_icon':    bias_icon,
        'bias_text':    bias_text,
        'bull_pct':     bull_pct,
        'bear_pct':     bear_pct,
        'confidence':   confidence,
        'bull_signals': bull_signals,
        'bear_signals': bear_signals,
        'oi_alerts':    oi_alerts,
        'smart_money':  {'big': round(big_pos_long, 3), 'retail': round(retail_long, 3)},
        'cvd':          cvd_score,
        'fr_avg':       round(fr_avg, 4),
        'pc_ratio':     pc_ratio,
        'kappa':        kappa,
        'upcoming_events': [f'{e.get("date","?")} {e.get("event","?")}' for e in upcoming_high[:2]],
    }

# ═══════════════════════════════════════════════════════════════════
# 问题3：怎么进？
# ═══════════════════════════════════════════════════════════════════

def q3_how_to_enter(state: dict, q1: dict, q2: dict, sym: str) -> dict:
    """
    精确执行条件：不是分析，是「等价格到XX做XX，SL放XX」
    """
    extra  = state.get('extra', {})
    bd     = state.get('confluence', {}).get('breakdown', {})
    price  = q1['price']
    bias   = q2['bias']
    regime = q1['regime']

    # ── 时段WR窗口 ───────────────────────────────────────
    n03 = str(bd.get('N03时段奖励', '') or '')
    n06 = str(bd.get('N06持仓建议', '') or '')
    in_high_wr_window = 'WR=' in n03 and ('+10' in n03 or '+8' in n03 or '+6' in n03)

    # ── OB/FVG 结构入场位 ────────────────────────────────
    fvg_4h = str(bd.get('FVG_4H_LONG', '') or bd.get('FVG_4H_SHORT', '') or '')
    fvg_15m= str(bd.get('FVG_15M_LONG','') or bd.get('FVG_15M_SHORT','') or '')
    ob_1h  = str(bd.get('OB新鲜度_1H_LONG','') or bd.get('OB新鲜度_1H_SHORT','') or '')

    # 从FVG描述提取磁铁位
    entry_zone = ''
    fvg_magnet = 0
    for fvg_str in [fvg_15m, fvg_4h]:
        if '磁铁' in fvg_str:
            try:
                mag_part = fvg_str.split('磁铁')[1].split(']')[0].strip()
                fvg_magnet = float(mag_part)
                break
            except Exception:
                pass

    # ── ATR计算入场区间 ───────────────────────────────────
    atr_1h = (state.get('momentum') or {}).get('atr_1h', 0)
    if not atr_1h:
        atr_1h = price * 0.005  # fallback 0.5%

    if bias == 'LONG':
        entry_lo = round(price - atr_1h * 0.5, 1)
        entry_hi = round(price, 1)
        sl_price = round(price - atr_1h * 2.0, 1)
        tp1      = fvg_magnet if fvg_magnet > price else round(price + atr_1h * 2.5, 1)
        tp2      = round(price + atr_1h * 4.0, 1)
        sl_note  = f'SL在1H ATR×2.0下方 ({sl_price:,.1f})'
    elif bias == 'SHORT':
        entry_lo = round(price, 1)
        entry_hi = round(price + atr_1h * 0.5, 1)
        sl_price = round(price + atr_1h * 2.0, 1)
        tp1      = fvg_magnet if fvg_magnet and fvg_magnet < price else round(price - atr_1h * 2.5, 1)
        tp2      = round(price - atr_1h * 4.0, 1)
        sl_note  = f'SL在1H ATR×2.0上方 ({sl_price:,.1f})'
    else:
        entry_lo = round(price - atr_1h, 1)
        entry_hi = round(price + atr_1h, 1)
        sl_price = 0
        tp1 = 0
        tp2 = 0
        sl_note = 'NEUTRAL体制：无入场建议，等方向确认'

    sl_pct = abs(price - sl_price) / price * 100 if price and sl_price else 0
    rr1 = abs(tp1 - price) / abs(price - sl_price) if sl_price and tp1 and price != sl_price else 0

    # ── VaR验证 ──────────────────────────────────────────
    var95 = q1['var95']
    var_pass = sl_pct >= var95 * 1.5

    # ── 死穴检查 ─────────────────────────────────────────
    DEAD = {('BEAR_TREND','LONG'), ('BULL_TREND','SHORT'),
            ('CHOP_MID','LONG'), ('CHOP_MID','SHORT')}
    is_dead = (regime, bias) in DEAD

    # ── 置信度综合 ────────────────────────────────────────
    score = state.get('score_final') or state.get('score') or 0
    try:
        score = float(str(score).split()[0])
    except Exception:
        score = 0

    if is_dead:
        execute_verdict = '🚫 死穴封禁，禁止入场'
        execute_code = 'BLOCKED'
    elif not in_high_wr_window:
        execute_verdict = '⏳ 等待高胜率时段窗口'
        execute_code = 'WAIT_TIMING'
    elif score < 80:
        execute_verdict = f'📊 信号分数不足({score:.0f}<80)，继续观察'
        execute_code = 'WAIT_SCORE'
    elif rr1 < 1.0:
        execute_verdict = f'⚠️ RR={rr1:.2f}<1.0，入场性价比不足'
        execute_code = 'WAIT_RR'
    else:
        execute_verdict = f'✅ 条件达标，可挂单等待触达入场区'
        execute_code = 'READY'

    return {
        'bias':            bias,
        'entry_lo':        entry_lo,
        'entry_hi':        entry_hi,
        'sl_price':        sl_price,
        'sl_pct':          round(sl_pct, 2),
        'tp1':             tp1,
        'tp2':             tp2,
        'rr1':             round(rr1, 2),
        'sl_note':         sl_note,
        'atr_1h':          round(atr_1h, 1),
        'var95':           var95,
        'var_pass':        var_pass,
        'in_timing_window':in_high_wr_window,
        'timing_note':     n03[:60],
        'hold_note':       n06[:40],
        'fvg_magnet':      fvg_magnet,
        'score':           score,
        'execute_verdict': execute_verdict,
        'execute_code':    execute_code,
        'is_dead':         is_dead,
    }

# ═══════════════════════════════════════════════════════════════════
# 报告组装
# ═══════════════════════════════════════════════════════════════════

def build_v2_report(sym: str, q1: dict, q2: dict, q3: dict) -> str:
    now   = datetime.now(timezone.utc).strftime('%m/%d %H:%M UTC')
    price = q1['price']
    sym_full = f'{sym}/USDT'

    lines = [
        f'━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━',
        f'🗺️ 梵天战场情报 V2 | {sym_full} ${price:,.1f} | {now}',
        f'━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━',
        '',
        # ── 问题1：我在哪 ────────────────────────────────
        f'【一】战场定位 — 我在哪？',
        f'  体制: {q1["regime"]}  当前区域: {q1["zone"]}',
        f'  上方钉子墙: ${q1["wall_up"]:,.0f} (+{q1["dist_up"]:.1f}%)  下方支撑: ${q1["wall_dn"]:,.0f} (-{q1["dist_dn"]:.1f}%)',
        f'  ZeroFlip翻转点: ${q1["zero_flip"]:,.0f}',
    ]

    if q1['fib382']:
        lines.append(f'  FIB支撑: ${q1["fib382"]:,.0f}  中轴: ${q1["fib500"]:,.0f}  阻力: ${q1["fib618"]:,.0f}')

    atr_note = {
        'COMPRESSED': '⚡压缩期(pct<15th)→波动率即将释放，方向待选',
        'LOW':        '🔵低波动区→入场成本低，等催化剂',
        'NORMAL':     '正常波动区',
        'HIGH':       '⚠️高波动区→SL需放宽，仓位压缩',
        'EXTREME':    '🚨极端波动→谨慎，滑点大',
    }.get(q1['atr_regime'], '')
    if atr_note:
        lines.append(f'  波动率: ATR分位{q1["atr_pct"]:.0f}th {atr_note}')

    if q1['extreme_evt']:
        lines.append(f'  🕰️ 历史情境: {q1["extreme_evt"]}')
    if q1['basis'] != 0:
        lines.append(f'  基差: {q1["basis"]:+.4f}%（{"期货溢价→多头强" if q1["basis"] > 0 else "期货折价→空头强"}）')
    lines.append('')

    # ── 问题2：往哪打 ────────────────────────────────────
    lines += [
        f'【二】主力猎杀方向 — 往哪打？',
        f'  {q2["bias_icon"]} 综合判断: {q2["bias_text"]}  置信度: {q2["confidence"]}%',
        f'  多空比: 多{q2["bull_pct"]}% vs 空{q2["bear_pct"]}%',
        '',
    ]

    if q2['bull_signals']:
        lines.append(f'  🟢 看多信号({len(q2["bull_signals"])}个):')
        for s in q2['bull_signals']:
            lines.append(f'    · {s}')

    if q2['bear_signals']:
        lines.append(f'  🔴 看空信号({len(q2["bear_signals"])}个):')
        for s in q2['bear_signals']:
            lines.append(f'    · {s}')

    # 聪明钱 vs 散户分歧
    sm = q2['smart_money']
    lines.append(f'  🐋 聪明钱: 大户持仓多{sm["big"]:.0%} vs 散户多{sm["retail"]:.0%}')

    if q2['upcoming_events']:
        lines.append(f'  📅 宏观雷区: {" / ".join(q2["upcoming_events"])}')

    if q2['oi_alerts']:
        lines.append(f'  🔔 OI已触发: {" | ".join(q2["oi_alerts"])}')
    lines.append('')

    # ── 问题3：怎么进 ────────────────────────────────────
    lines += [
        f'【三】精确执行条件 — 怎么进？',
        f'  {q3["execute_verdict"]}',
        '',
    ]

    if q3['execute_code'] == 'READY':
        lines += [
            f'  📍 入场区间: ${q3["entry_lo"]:,.1f} ~ ${q3["entry_hi"]:,.1f}',
            f'  🛑 止损位:   ${q3["sl_price"]:,.1f}  ({q3["sl_pct"]:.2f}%)',
            f'  🎯 目标1:    ${q3["tp1"]:,.1f}  RR={q3["rr1"]:.2f}x',
            f'  🎯 目标2:    ${q3["tp2"]:,.1f}',
            f'  📏 ATR1H={q3["atr_1h"]:.0f}  {q3["sl_note"]}',
        ]
        if q3['fvg_magnet']:
            lines.append(f'  🧲 FVG磁铁: ${q3["fvg_magnet"]:,.1f}（结构目标位）')
    elif q3['execute_code'] in ('WAIT_TIMING', 'WAIT_SCORE', 'WAIT_RR'):
        lines += [
            f'  📍 备用入场区: ${q3["entry_lo"]:,.1f} ~ ${q3["entry_hi"]:,.1f}',
            f'  🛑 参考止损:   ${q3["sl_price"]:,.1f}  ({q3["sl_pct"]:.2f}%)',
            f'  🎯 参考目标:   ${q3["tp1"]:,.1f}  RR={q3["rr1"]:.2f}x',
        ]

    if q3['timing_note']:
        lines.append(f'  ⏰ 时段窗口: {q3["timing_note"]}')
    if q3['hold_note']:
        lines.append(f'  ⏱️ 建议持仓: {q3["hold_note"]}')
    lines.append(f'  🔒 VaR95={q3["var95"]:.2f}%  SL{"✅达标" if q3["var_pass"] else "⚠️偏窄"}')
    lines.append('')
    lines.append(f'📡 梵天量化 · 74维分析 · 数据驱动 · 非投资建议')

    return '\n'.join(lines)

# ── 主函数 ─────────────────────────────────────────────────────────
def main(symbols=None):
    symbols = symbols or ['BTC']

    gex_state  = load_json(GEX_STATE)
    vb_state   = load_json(VOL_BETA_STATE)
    oi_wl      = load_json(OI_WATCHLIST)
    b_state    = load_json(BRAHMA_STATE)

    reports = []
    for sym in symbols:
        sym_state = b_state  # 默认BTC主态，后续可扩展多标的state
        if sym == 'ETH':
            eth_state_path = BASE / 'data' / 'brahma_state_eth.json'
            if eth_state_path.exists():
                sym_state = load_json(eth_state_path)
            # ETH fallback: 用BTC state但更新价格
            if not sym_state.get('price') or sym_state.get('price', 0) > 10000:
                p = live_price('ETHUSDT')
                if p:
                    sym_state = dict(sym_state)
                    sym_state['price'] = p

        q1 = q1_where_am_i(sym_state, gex_state, vb_state, sym)
        q2 = q2_where_is_target(sym_state, vb_state, oi_wl, sym)
        q3 = q3_how_to_enter(sym_state, q1, q2, sym)

        report = build_v2_report(sym, q1, q2, q3)
        reports.append(report)
        print(report)
        print()

    full = '\n\n'.join(reports)
    try:
        Path(INTEL_LAST).write_text(json.dumps({
            'ts': time.time(),
            'report': full,
        }, ensure_ascii=False))
    except Exception:
        pass

    return full

if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--symbols', nargs='+', default=['BTC'])
    args = ap.parse_args()
    main(symbols=args.symbols)
