#!/usr/bin/env python3
"""
market_quadrant.py — 梵天大脑 Layer B1: 四象限市场状态
设计院 2026-08-25 苏摩111立项封印

20年老手的第一判断框架:
         散户多头拥挤
              ↑
  大户做空 ←─────────── 大户做多
              ↓
         散户空头拥挤

四象限:
  Q1: 散户多 + 大户空 → 做空机会最高 ★★★
  Q2: 散户空 + 大户多 → 做多机会最高 ★★★
  Q3: 散户多 + 大户多 → 趋势持续做多 ★★
  Q4: 散户空 + 大户空 → 趋势持续做空 ★★

三阶段节奏识别 (Layer B2):
  ACCUMULATION  积累期: 横盘+量萎+OI升
  EXPANSION     爆发期: 放量突破+OI急升
  EXHAUSTION    衰竭期: 量价背离+OI顶+LSR极端
"""
from __future__ import annotations
import os, sys, time, logging
from typing import Optional

_BB = os.path.dirname(os.path.abspath(__file__))
if _BB not in sys.path: sys.path.insert(0, _BB)

logger = logging.getLogger('market_quadrant')


# ═══════════════════════════════════════════════════════════════
# B1: 四象限判断
# ═══════════════════════════════════════════════════════════════

def get_quadrant(symbol: str) -> dict:
    """
    返回当前四象限状态 + 操作建议

    Returns:
        {
          'quadrant': 'Q1'|'Q2'|'Q3'|'Q4'|'NEUTRAL',
          'retail_bias': 'LONG'|'SHORT'|'NEUTRAL',  # 散户方向
          'whale_bias':  'LONG'|'SHORT'|'NEUTRAL',  # 大户方向
          'signal':      'SHORT'|'LONG'|'TREND_SHORT'|'TREND_LONG'|'NEUTRAL',
          'stars':       int,   # 1~3 机会等级
          'confidence':  float, # 0~1
          'lsr':         float,
          'whale_net':   float, # 正=大户净多, 负=大户净空 (百万USD)
          'whale_diverge': bool,
          'note':        str,
          'raw': dict,
        }
    """
    sym = symbol.upper()

    # ── 散户维度: LSR ────────────────────────────────────────
    retail_bias = 'NEUTRAL'
    lsr = 1.0
    try:
        from data_cache import get_long_short_ratio
        lsr = get_long_short_ratio(sym)
        # LSR = 多/空比例，>1多头占优
        # 换算为多头百分比: lsr/(1+lsr)
        long_pct = lsr / (1.0 + lsr) * 100 if lsr > 0 else 50.0
        if long_pct > 60:
            retail_bias = 'LONG'    # 散户多头拥挤
        elif long_pct < 40:
            retail_bias = 'SHORT'   # 散户空头拥挤
        # 注: lsr_oi_engine 的 lsr 可能直接是百分比，做容错
        if lsr > 10:  # 像70.91这样的直接是%
            long_pct = lsr
            if long_pct > 60:   retail_bias = 'LONG'
            elif long_pct < 40: retail_bias = 'SHORT'
            else:               retail_bias = 'NEUTRAL'
            lsr_display = long_pct
        else:
            lsr_display = long_pct
    except Exception as e:
        logger.debug(f'LSR: {e}')
        lsr_display = 50.0

    # ── 大户维度: whale_engine ───────────────────────────────
    whale_bias = 'NEUTRAL'
    whale_net  = 0.0
    whale_diverge = False
    whale_notes = []
    big_buy = big_sell = 0.0
    try:
        from whale_engine import get_whale_activity
        wa = get_whale_activity(sym)
        whale_net   = wa.get('whale_net', 0.0)    # 正=净买，负=净卖
        whale_diverge = wa.get('diverge', False)
        whale_notes = wa.get('notes', [])
        big_buy  = wa.get('big_buy', 0.0)
        big_sell = wa.get('big_sell', 0.0)
        wd = wa.get('whale_dir', 'NEUTRAL')
        if wd == 'BUY':   whale_bias = 'LONG'
        elif wd == 'SELL': whale_bias = 'SHORT'
    except Exception as e:
        logger.debug(f'whale_activity: {e}')

    # 补充: smart_money_engine 大户持仓方向
    smart_bias = 'NEUTRAL'
    try:
        from smart_money_engine import get_smart_money_signal
        sm = get_smart_money_signal(sym)
        sm_dir = sm.get('direction', 'NEUTRAL')
        if sm_dir in ('LONG', 'SHORT'):
            smart_bias = sm_dir
            # 与whale_engine综合
            if whale_bias == 'NEUTRAL':
                whale_bias = smart_bias
            elif whale_bias != smart_bias:
                whale_bias = 'NEUTRAL'  # 两源冲突→中性
    except Exception as e:
        logger.debug(f'smart_money: {e}')

    # ── 四象限映射 ───────────────────────────────────────────
    if retail_bias == 'LONG' and whale_bias == 'SHORT':
        quadrant = 'Q1'   # 散户多 + 大户空
        signal   = 'SHORT'
        stars    = 3
        note     = f'散户多头拥挤({lsr_display:.0f}%) + 大户净空 → 做空机会最高'
        confidence = 0.80

    elif retail_bias == 'SHORT' and whale_bias == 'LONG':
        quadrant = 'Q2'   # 散户空 + 大户多
        signal   = 'LONG'
        stars    = 3
        note     = f'散户空头拥挤({lsr_display:.0f}%) + 大户净多 → 做多机会最高'
        confidence = 0.80

    elif retail_bias == 'LONG' and whale_bias == 'LONG':
        quadrant = 'Q3'   # 散户多 + 大户多
        signal   = 'TREND_LONG'
        stars    = 2
        note     = f'多头共振({lsr_display:.0f}%) → 趋势持续做多，不逆势'
        confidence = 0.60

    elif retail_bias == 'SHORT' and whale_bias == 'SHORT':
        quadrant = 'Q4'   # 散户空 + 大户空
        signal   = 'TREND_SHORT'
        stars    = 2
        note     = f'空头共振({lsr_display:.0f}%) → 趋势持续做空，不逆势'
        confidence = 0.60

    else:
        quadrant = 'NEUTRAL'
        signal   = 'NEUTRAL'
        stars    = 1
        note     = f'散户{retail_bias} 大户{whale_bias} → 信号不明，观望'
        confidence = 0.30

    # 大户背离加权
    if whale_diverge and stars >= 2:
        stars = min(3, stars + 1)
        confidence = min(0.90, confidence + 0.10)
        note += f' | ⚡背离信号确认'

    return {
        'quadrant':      quadrant,
        'retail_bias':   retail_bias,
        'whale_bias':    whale_bias,
        'signal':        signal,
        'stars':         stars,
        'confidence':    round(confidence, 2),
        'lsr':           round(lsr_display, 1),
        'whale_net':     round(whale_net, 2),
        'whale_diverge': whale_diverge,
        'note':          note,
        'raw': {
            'big_buy': big_buy, 'big_sell': big_sell,
            'whale_notes': whale_notes,
            'smart_bias': smart_bias,
        },
    }


# ═══════════════════════════════════════════════════════════════
# B2: 三阶段市场节奏识别
# ═══════════════════════════════════════════════════════════════

def get_market_phase(symbol: str) -> dict:
    """
    识别当前市场所处阶段

    Returns:
        {
          'phase': 'ACCUMULATION'|'EXPANSION'|'EXHAUSTION'|'UNKNOWN',
          'confidence': float,
          'signals': list,
          'action': str,
        }
    """
    sym = symbol.upper()
    phase_signals = []
    acc_score = exp_score = exh_score = 0

    try:
        from data_cache import get_klines, get_long_short_ratio
        from math_utils import atr, calc_rsi, ema

        kl1h = get_klines(sym, '1h', 72)
        c1h = [float(k[4]) for k in kl1h]
        h1h = [float(k[2]) for k in kl1h]
        l1h = [float(k[3]) for k in kl1h]
        v1h = [float(k[5]) for k in kl1h]

        # 价格区间宽度（24H）
        hi24 = max(h1h[-24:]); lo24 = min(l1h[-24:])
        price_range_pct = (hi24 - lo24) / lo24 * 100 if lo24 > 0 else 0

        # 量能趋势（近12根vs前12根）
        vol_recent = sum(v1h[-12:]) / 12
        vol_prior  = sum(v1h[-24:-12]) / 12
        vol_ratio  = vol_recent / vol_prior if vol_prior > 0 else 1.0

        # OI变化方向（通过RSI代理）
        rsi = calc_rsi(c1h, 14)

        # LSR极值
        lsr_raw = get_long_short_ratio(sym)
        lsr_pct = lsr_raw if lsr_raw > 10 else lsr_raw / (1 + lsr_raw) * 100

        # ── 积累期特征 ──
        if price_range_pct < 3.0:
            acc_score += 2; phase_signals.append(f'价格横盘({price_range_pct:.1f}%)')
        if vol_ratio < 0.8:
            acc_score += 2; phase_signals.append(f'量能萎缩({vol_ratio:.2f}x)')
        if 40 < rsi < 60:
            acc_score += 1; phase_signals.append(f'RSI中性({rsi:.1f})')

        # ── 爆发期特征 ──
        if vol_ratio > 1.5:
            exp_score += 2; phase_signals.append(f'量能放大({vol_ratio:.2f}x)')
        if price_range_pct > 5.0:
            exp_score += 2; phase_signals.append(f'价格突破({price_range_pct:.1f}%)')
        if rsi > 65 or rsi < 35:
            exp_score += 1; phase_signals.append(f'RSI方向明确({rsi:.1f})')

        # ── 衰竭期特征 ──
        if vol_ratio < 0.7 and price_range_pct > 3.0:
            exh_score += 2; phase_signals.append(f'量价背离(量{vol_ratio:.2f}x 价{price_range_pct:.1f}%)')
        if lsr_pct > 70 or lsr_pct < 30:
            exh_score += 2; phase_signals.append(f'持仓极端({lsr_pct:.0f}%)')
        if rsi > 75 or rsi < 25:
            exh_score += 2; phase_signals.append(f'RSI超买/超卖({rsi:.1f})')

    except Exception as e:
        logger.debug(f'market_phase: {e}')
        return {'phase': 'UNKNOWN', 'confidence': 0.0, 'signals': [], 'action': '数据获取失败'}

    # 取最高分阶段
    scores = {'ACCUMULATION': acc_score, 'EXPANSION': exp_score, 'EXHAUSTION': exh_score}
    phase = max(scores, key=scores.get)
    max_score = scores[phase]
    total = sum(scores.values())
    confidence = max_score / total if total > 0 else 0.0

    action_map = {
        'ACCUMULATION': '等待爆发，轻仓试探，不追涨',
        'EXPANSION':    '跟随趋势，顺势加仓，不逆势',
        'EXHAUSTION':   '反转信号，做反方向，严控仓位',
    }

    return {
        'phase':      phase,
        'confidence': round(confidence, 2),
        'signals':    phase_signals,
        'action':     action_map.get(phase, '观望'),
        'scores':     scores,
    }


# ═══════════════════════════════════════════════════════════════
# 统一入口: 四象限 + 三阶段
# ═══════════════════════════════════════════════════════════════

def get_market_context(symbol: str) -> dict:
    """完整市场认知上下文，供analyze()和price_zone_engine使用"""
    quadrant = get_quadrant(symbol)
    phase    = get_market_phase(symbol)

    # 综合操作建议
    q_signal = quadrant['signal']
    p_phase  = phase['phase']

    if q_signal in ('SHORT', 'LONG') and p_phase == 'EXHAUSTION':
        master_signal = q_signal
        master_conf   = (quadrant['confidence'] + phase['confidence']) / 2 * 1.2
        master_note   = f'四象限{q_signal} + 衰竭期共振 → 强信号'
    elif q_signal in ('SHORT', 'LONG') and p_phase == 'EXPANSION':
        master_signal = q_signal
        master_conf   = quadrant['confidence'] * 0.8
        master_note   = f'四象限{q_signal} 但处于爆发期，需谨慎逆势'
    elif q_signal in ('TREND_SHORT', 'TREND_LONG') and p_phase == 'EXPANSION':
        master_signal = q_signal.replace('TREND_', '')
        master_conf   = (quadrant['confidence'] + phase['confidence']) / 2
        master_note   = f'共振趋势 + 爆发期 → 顺势最优'
    else:
        master_signal = q_signal if q_signal != 'NEUTRAL' else 'NEUTRAL'
        master_conf   = min(quadrant['confidence'], 0.5)
        master_note   = f'{quadrant["note"]} | {phase["action"]}'

    return {
        'symbol':        symbol,
        'quadrant':      quadrant,
        'phase':         phase,
        'master_signal': master_signal,
        'master_conf':   round(min(1.0, master_conf), 2),
        'master_note':   master_note,
        'ts':            time.time(),
    }


def format_quadrant_report(ctx: dict) -> str:
    """格式化四象限报告"""
    q = ctx['quadrant']
    p = ctx['phase']
    sym = ctx['symbol'][:3]
    stars = '★' * q['stars'] + '☆' * (3 - q['stars'])

    lines = [
        f'🧠 梵天四象限 | {sym}',
        f'象限: {q["quadrant"]} {stars} | {q["note"]}',
        f'散户: LSR={q["lsr"]:.1f}% ({q["retail_bias"]}) | 大户净: ${q["whale_net"]:+.1f}M ({q["whale_bias"]})',
    ]
    if q['whale_diverge']:
        lines.append(f'⚡ 背离信号: {" | ".join(q["raw"]["whale_notes"][:2])}')
    lines += [
        f'市场阶段: {p["phase"]} (conf={p["confidence"]:.0%})',
        f'节奏信号: {" | ".join(p["signals"][:3])}',
        f'综合操作: {ctx["master_signal"]} (置信={ctx["master_conf"]:.0%})',
        f'判断: {ctx["master_note"]}',
    ]
    return '\n'.join(lines)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--symbols', nargs='+', default=['BTCUSDT', 'ETHUSDT'])
    args = parser.parse_args()
    for sym in args.symbols:
        ctx = get_market_context(sym)
        print(format_quadrant_report(ctx))
        print()
