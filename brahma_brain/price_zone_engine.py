#!/usr/bin/env python3
"""
price_zone_engine.py — 梵天战场预判层 v1.0
设计院 2026-08-25 苏摩111立项封印

使命：把梵天8层武器合成「战场预判」
  输出: 高空区 / 低多区 / 偏向 / 路径概率 / 观点

四层架构:
  Layer 0: 纯计算感知层 (0 tokens)  → calc_zones()
  Layer 1: 观点合成层               → format_zone_report()
  Layer 2: 触及检测层               → check_zone_touch()
  Layer 3: 39维验证层               → 现有analyze()不变

接入武器:
  P0: R1/S1/Fib/LSR/FR/ATR/Pivot (基础版)
  P1: liq_density_engine + smc_engine
  P2: hcme_matcher + gex_engine
"""
from __future__ import annotations
import os, sys, time, json, logging
from typing import Optional

_BB = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_BB)
for _p in [_BB, _ROOT]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

logger = logging.getLogger('price_zone_engine')

# ═══════════════════════════════════════════════════════════════
# 缓存：每4H刷新一次，避免重复计算
# ═══════════════════════════════════════════════════════════════
_ZONE_CACHE: dict = {}
_CACHE_TTL = 4 * 3600  # 4H


def _cache_key(symbol: str) -> str:
    return symbol.upper()


def _get_cached(symbol: str) -> Optional[dict]:
    k = _cache_key(symbol)
    if k in _ZONE_CACHE:
        ts, data = _ZONE_CACHE[k]
        if time.time() - ts < _CACHE_TTL:
            return data
    return None


def _set_cached(symbol: str, data: dict):
    _ZONE_CACHE[_cache_key(symbol)] = (time.time(), data)


# ═══════════════════════════════════════════════════════════════
# Layer 0: 核心区间计算
# ═══════════════════════════════════════════════════════════════

def calc_zones(symbol: str, force_refresh: bool = False) -> dict:
    """
    主入口: 计算高空区 / 低多区 / 偏向 / 路径概率

    Returns:
        {
          'symbol': str,
          'price': float,
          'bias': 'LONG'|'SHORT'|'NEUTRAL',
          'bias_score': int,       # -5~+5
          'bias_reasons': list,
          'high_short': {          # 高空区
            'low': float, 'high': float,
            'confidence': int,     # 1~3 (⭐数)
            'sources': list,       # 支撑来源
            'sl': float, 'tp': float, 'rr': float,
          },
          'low_long': {            # 低多区
            'low': float, 'high': float,
            'confidence': int,
            'sources': list,
            'sl': float, 'tp': float, 'rr': float,
          },
          'scenario_prob': {'up_first': float, 'down_first': float},
          'hcme_note': str,        # 方仓历史情境
          'ts': float,
        }
    """
    if not force_refresh:
        cached = _get_cached(symbol)
        if cached:
            return cached

    sym = symbol.upper()
    result = _calc_zones_internal(sym)
    _set_cached(sym, result)
    return result


def _calc_zones_internal(symbol: str) -> dict:
    from data_cache import (get_klines, get_ticker,
                            get_funding_rate, get_long_short_ratio)
    import market_state as ms_mod
    from math_utils import calc_rsi, ema, atr, bollinger

    # ── 基础数据 ──────────────────────────────────────────────
    try:
        tk = get_ticker(symbol)
        price = float(tk.get('lastPrice', 0))
    except Exception:
        price = 0.0

    try:
        kl1h = get_klines(symbol, '1h', 200)
        kl4h = get_klines(symbol, '4h', 100)
        kl1d = get_klines(symbol, '1d', 30)
        c1h = [float(k[4]) for k in kl1h]; h1h = [float(k[2]) for k in kl1h]; l1h = [float(k[3]) for k in kl1h]
        c4h = [float(k[4]) for k in kl4h]; h4h = [float(k[2]) for k in kl4h]; l4h = [float(k[3]) for k in kl4h]
        c1d = [float(k[4]) for k in kl1d]; h1d = [float(k[2]) for k in kl1d]; l1d = [float(k[3]) for k in kl1d]
    except Exception as e:
        logger.warning(f'klines获取失败: {e}')
        return _empty_zones(symbol, price)

    # ── 核心指标 ──────────────────────────────────────────────
    rsi1h  = calc_rsi(c1h, 14)
    rsi4h  = calc_rsi(c4h, 14)
    rsi1d  = calc_rsi(c1d, 14)
    ema20  = ema(c1h, 20)
    atr4h_v = atr(h4h, l4h, c4h, 14)
    bb1h   = bollinger(c1h, 20)     # (upper, mid, lower)
    bb4h   = bollinger(c4h, 20)

    try:
        fr  = get_funding_rate(symbol)
        lsr = get_long_short_ratio(symbol)
    except Exception:
        fr = 0.0; lsr = 1.0

    # ── Pivot + Fib ───────────────────────────────────────────
    try:
        piv = ms_mod.calc_pivot_points(h1d[-2], l1d[-2], c1d[-2])
        r1  = piv.get('r1', price * 1.02)
        r2  = piv.get('r2', price * 1.04)
        s1  = piv.get('s1', price * 0.98)
        s2  = piv.get('s2', price * 0.96)
        pp  = piv.get('pp', price)
    except Exception:
        r1 = price * 1.02; r2 = price * 1.04
        s1 = price * 0.98; s2 = price * 0.96; pp = price

    hi48 = max(h1h[-48:]) if len(h1h) >= 48 else max(h1h)
    lo48 = min(l1h[-48:]) if len(l1h) >= 48 else min(l1h)
    try:
        fib = ms_mod.calc_fib_levels(hi48, lo48)
        f382 = fib.get('0.382', s1)
        f618 = fib.get('0.618', s1 * 0.99)
        f786 = fib.get('0.786', s2)
        f236 = fib.get('0.236', r1 * 0.99)
    except Exception:
        f382 = s1; f618 = s1 * 0.99; f786 = s2; f236 = r1 * 0.99

    # ══ P0: 基础高空区 ════════════════════════════════════════
    hs_sources = []
    hs_levels  = []

    # R1 Pivot
    hs_levels.append(r1); hs_sources.append(f'R1={r1:.0f}')
    # BB4H上轨
    hs_levels.append(bb4h[0]); hs_sources.append(f'BB4H上轨={bb4h[0]:.0f}')
    # Fib0.236 (回调后新高压力)
    if f236 > price * 1.005:
        hs_levels.append(f236); hs_sources.append(f'Fib0.236={f236:.0f}')

    hs_center = sum(hs_levels) / len(hs_levels)
    hs_low  = hs_center - atr4h_v * 0.3
    hs_high = hs_center + atr4h_v * 0.5
    hs_conf = 1  # P0基础置信度

    # ── P1: 接入清算密度 ──────────────────────────────────────
    liq_above_price = None
    liq_below_price = None
    try:
        from liq_density_engine import get_liq_density
        liq_data = get_liq_density(symbol, price)
        if liq_data:
            # above_walls: [(price, usd), ...] 按价格升序
            above_walls = liq_data.get('above_walls', [])
            below_walls = liq_data.get('below_walls', [])
            # 找上方最大清算密集区（按USD量排序）
            if above_walls:
                biggest_above = sorted(above_walls, key=lambda x: -x[1])[:2]
                liq_above_price = biggest_above[0][0]
                liq_above_usd   = biggest_above[0][1] / 1e6
                hs_sources.append(f'清算密集={liq_above_price:.0f}(${liq_above_usd:.0f}M)')
                hs_conf += 1
                hs_low  = min(hs_low,  liq_above_price * 0.997)
                hs_high = max(hs_high, liq_above_price + atr4h_v * 0.25)
            if below_walls:
                biggest_below = sorted(below_walls, key=lambda x: -x[1])[:2]
                liq_below_price = biggest_below[0][0]
                liq_below_usd   = biggest_below[0][1] / 1e6
    except Exception as e:
        logger.debug(f'liq_density: {e}')

    # ── P1: 接入SMC ───────────────────────────────────────────
    smc_resistance = None
    smc_support    = None
    try:
        from smc_engine import analyze_smc
        smc_1h = analyze_smc(symbol, 'SHORT', '1h', 100)
        obs    = smc_1h.get('order_blocks', {})
        fvgs   = smc_1h.get('fvgs', {})

        # 找上方最近的OB/FVG（空头压力）
        bear_obs = [ob for ob in obs.get('bearish', [])
                    if ob.get('high', 0) > price and not ob.get('mitigated', False)]
        bear_fvgs = [f for f in fvgs.get('bearish', [])
                     if f.get('high', 0) > price]
        if bear_obs:
            smc_resistance = bear_obs[0].get('high', None)
            if smc_resistance:
                hs_sources.append(f'SMC空头OB={smc_resistance:.0f}')
                hs_conf += 1
                hs_low  = min(hs_low,  smc_resistance * 0.997)
                hs_high = max(hs_high, smc_resistance + atr4h_v * 0.2)
        elif bear_fvgs:
            smc_resistance = bear_fvgs[0].get('high', None)
            if smc_resistance:
                hs_sources.append(f'SMC空头FVG={smc_resistance:.0f}')
                hs_conf += 1

        # 找下方最近的多头OB（支撑）
        bull_obs = [ob for ob in obs.get('bullish', [])
                    if ob.get('low', 9e9) < price and not ob.get('mitigated', False)]
        if bull_obs:
            smc_support = bull_obs[0].get('low', None)
    except Exception as e:
        logger.debug(f'smc_engine: {e}')

    # ══ 低多区 ════════════════════════════════════════════════
    ll_sources = []
    ll_levels  = []

    # S1 Pivot
    ll_levels.append(s1); ll_sources.append(f'S1={s1:.0f}')
    # Fib0.618 (黄金回调)
    if f618 < price * 0.995:
        ll_levels.append(f618); ll_sources.append(f'Fib0.618={f618:.0f}')
    # Fib0.382
    if f382 < price * 0.998:
        ll_levels.append(f382); ll_sources.append(f'Fib0.382={f382:.0f}')

    ll_center = sum(ll_levels) / len(ll_levels)
    ll_low    = ll_center - atr4h_v * 0.5
    ll_high   = ll_center + atr4h_v * 0.3
    ll_conf   = 1

    # 清算密集（下方）→ 低多区加持
    if liq_below_price:
        ll_sources.append(f'清算密集={liq_below_price:.0f}(${liq_below_usd:.0f}M)')
        ll_conf += 1
        ll_low  = min(ll_low,  liq_below_price * 0.998)
        ll_high = max(ll_high, liq_below_price + atr4h_v * 0.2)

    # SMC多头OB → 低多区加持
    if smc_support:
        ll_sources.append(f'SMC多头OB={smc_support:.0f}')
        ll_conf += 1
        ll_low  = min(ll_low,  smc_support * 0.997)
        ll_high = max(ll_high, smc_support + atr4h_v * 0.15)

    # ══ P2: HCME方仓历史匹配 ══════════════════════════════════
    hcme_note   = ''
    hcme_bias   = 0
    try:
        from hcme_matcher import get_hcme_matcher
        matcher = get_hcme_matcher()
        if matcher:
            # 构造简单查询向量（RSI+FR+体制）
            regime_str = ms_mod.detect_regime(c4h, h4h, l4h,
                ms_mod.trend_direction(c1h, h1h, l1h),
                ms_mod.trend_direction(c4h, h4h, l4h),
                ms_mod.trend_direction(c1d, h1d, l1d))
            query = {
                'symbol': symbol, 'regime': regime_str,
                'rsi_1h': rsi1h, 'rsi_4h': rsi4h,
                'funding_rate': fr, 'lsr': lsr,
            }
            matches = matcher.match(query, top_k=5) if hasattr(matcher, 'match') else []
            if matches:
                short_wins = sum(1 for m in matches if m.get('outcome_dir') == 'DOWN')
                long_wins  = sum(1 for m in matches if m.get('outcome_dir') == 'UP')
                total = len(matches)
                if short_wins > long_wins:
                    hcme_bias = -1
                    hcme_note = f'HCME历史{total}例: {short_wins}下/{long_wins}上 → 偏空'
                    hs_conf = min(3, hs_conf + 1)
                elif long_wins > short_wins:
                    hcme_bias = +1
                    hcme_note = f'HCME历史{total}例: {long_wins}上/{short_wins}下 → 偏多'
                    ll_conf = min(3, ll_conf + 1)
                else:
                    hcme_note = f'HCME历史{total}例: 多空均等，中性'
    except Exception as e:
        logger.debug(f'hcme_matcher: {e}')

    # ══ P2: GEX Gamma墙 ═══════════════════════════════════════
    gex_wall_above = None
    gex_wall_below = None
    try:
        from gex_engine import score_gex, compute_gex
        currency = 'BTC' if 'BTC' in symbol else 'ETH'
        gex_data = compute_gex(currency=currency)
        gex_result = score_gex(symbol, 'SHORT', gex_data)
        # 从GEX数据里找Gamma最大正向墙（价格磁铁）
        if gex_data and isinstance(gex_data, dict):
            strikes = gex_data.get('strikes', [])
            above_s = sorted([s for s in strikes if s.get('strike', 0) > price],
                             key=lambda x: -abs(x.get('net_gex', 0)))
            below_s = sorted([s for s in strikes if s.get('strike', 0) < price],
                             key=lambda x: -abs(x.get('net_gex', 0)))
            if above_s:
                gex_wall_above = above_s[0].get('strike')
                hs_sources.append(f'GEX墙={gex_wall_above:.0f}')
                hs_conf = min(3, hs_conf + 1)
            if below_s:
                gex_wall_below = below_s[0].get('strike')
                ll_sources.append(f'GEX墙={gex_wall_below:.0f}')
    except Exception as e:
        logger.debug(f'gex_engine: {e}')

    # ══ 偏向判断（5维综合）════════════════════════════════════
    bias_score = 0
    bias_reasons = []

    # LSR
    if lsr > 65:
        bias_score -= 2
        bias_reasons.append(f'LSR={lsr:.1f}多头拥挤→偏空')
    elif lsr < 35:
        bias_score += 2
        bias_reasons.append(f'LSR={lsr:.1f}空头拥挤→偏多')

    # 资金费率
    if fr > 0.01:
        bias_score -= 1
        bias_reasons.append(f'FR={fr:+.4f}多头偏贵→偏空')
    elif fr < -0.01:
        bias_score += 1
        bias_reasons.append(f'FR={fr:+.4f}空头偏贵→偏多')

    # RSI 1D
    if rsi1d > 75:
        bias_score -= 1
        bias_reasons.append(f'RSI1D={rsi1d:.1f}日线超买→偏空')
    elif rsi1d < 30:
        bias_score += 1
        bias_reasons.append(f'RSI1D={rsi1d:.1f}日线超卖→偏多')

    # MACD 4H momentum
    try:
        from math_utils import macd
        mc4h = macd(c4h)
        if mc4h[2] < 0:   # histogram < 0 = 动量转空
            bias_score -= 1
            bias_reasons.append(f'MACD4H hist={mc4h[2]:.1f}转负→偏空')
        elif mc4h[2] > 0:
            bias_score += 1
            bias_reasons.append(f'MACD4H hist={mc4h[2]:.1f}转正→偏多')
    except Exception:
        pass

    # HCME方向
    if hcme_bias < 0:
        bias_score -= 1
        bias_reasons.append('HCME历史偏空')
    elif hcme_bias > 0:
        bias_score += 1
        bias_reasons.append('HCME历史偏多')

    if bias_score <= -2:
        bias = 'SHORT'
    elif bias_score >= 2:
        bias = 'LONG'
    else:
        bias = 'NEUTRAL'

    # ══ 场景概率 ══════════════════════════════════════════════
    # 基于偏向+RSI+价格位置
    bb_pos = (price - bb1h[2]) / (bb1h[0] - bb1h[2]) if bb1h[0] != bb1h[2] else 0.5
    pos48  = (price - lo48) / (hi48 - lo48) if hi48 != lo48 else 0.5

    if bias == 'SHORT':
        up_prob = max(0.25, 0.5 - abs(bias_score) * 0.08)
    elif bias == 'LONG':
        up_prob = min(0.75, 0.5 + abs(bias_score) * 0.08)
    else:
        up_prob = 0.5
    # 价格在区间高位 → 下跌概率更高
    if pos48 > 0.7:
        up_prob -= 0.08
    elif pos48 < 0.3:
        up_prob += 0.08
    up_prob = max(0.20, min(0.80, up_prob))
    dn_prob = 1.0 - up_prob

    # ══ 止损/止盈计算 ═════════════════════════════════════════
    # 高空区 SL = 区间上沿 + 0.5ATR4H, TP = S1
    hs_sl = hs_high + atr4h_v * 0.5
    hs_tp = s1
    hs_rr = round((hs_low - hs_tp) / (hs_sl - hs_low), 1) if hs_sl != hs_low else 1.0

    # 低多区 SL = 区间下沿 - 0.5ATR4H, TP = R1
    ll_sl = ll_low - atr4h_v * 0.5
    ll_tp = r1
    ll_rr = round((ll_tp - ll_high) / (ll_high - ll_sl), 1) if ll_high != ll_sl else 1.0

    # ══ 组装结果 ══════════════════════════════════════════════
    result = {
        'symbol':        symbol,
        'price':         price,
        'bias':          bias,
        'bias_score':    bias_score,
        'bias_reasons':  bias_reasons,
        'regime':        ms_mod.detect_regime(c4h, h4h, l4h,
                            ms_mod.trend_direction(c1h, h1h, l1h),
                            ms_mod.trend_direction(c4h, h4h, l4h),
                            ms_mod.trend_direction(c1d, h1d, l1d)),
        'rsi1h':         rsi1h,
        'rsi4h':         rsi4h,
        'rsi1d':         rsi1d,
        'fr':            fr,
        'lsr':           lsr,
        'ema20_1h':      ema20,
        'high_short': {
            'low':        round(hs_low, 1),
            'high':       round(hs_high, 1),
            'confidence': min(3, hs_conf),
            'sources':    hs_sources,
            'sl':         round(hs_sl, 1),
            'tp':         round(hs_tp, 1),
            'rr':         max(0.5, hs_rr),
        },
        'low_long': {
            'low':        round(ll_low, 1),
            'high':       round(ll_high, 1),
            'confidence': min(3, ll_conf),
            'sources':    ll_sources,
            'sl':         round(ll_sl, 1),
            'tp':         round(ll_tp, 1),
            'rr':         max(0.5, ll_rr),
        },
        'scenario_prob': {
            'up_first':   round(up_prob, 2),
            'down_first': round(dn_prob, 2),
        },
        'hcme_note': hcme_note,
        'ts':        time.time(),
    }
    return result


def _empty_zones(symbol: str, price: float) -> dict:
    return {
        'symbol': symbol, 'price': price, 'bias': 'NEUTRAL',
        'bias_score': 0, 'bias_reasons': ['数据获取失败'],
        'high_short': {'low': price*1.02,'high': price*1.03,'confidence':1,'sources':[],'sl':price*1.035,'tp':price*0.98,'rr':1.0},
        'low_long':   {'low': price*0.97,'high': price*0.98,'confidence':1,'sources':[],'sl':price*0.965,'tp':price*1.02,'rr':1.0},
        'scenario_prob': {'up_first': 0.5, 'down_first': 0.5},
        'hcme_note': '', 'ts': time.time(),
    }


# ═══════════════════════════════════════════════════════════════
# Layer 1: 观点合成输出
# ═══════════════════════════════════════════════════════════════

def format_zone_report(zones: dict, compact: bool = False) -> str:
    """格式化战场预判报告（推送格式）"""
    import datetime
    sym   = zones['symbol'][:3]
    price = zones['price']
    bias  = zones['bias']
    bs    = zones['bias_score']
    hs    = zones['high_short']
    ll    = zones['low_long']
    sp    = zones['scenario_prob']
    reg   = zones.get('regime', '?')
    rsi1h = zones.get('rsi1h', 0)
    rsi4h = zones.get('rsi4h', 0)
    fr    = zones.get('fr', 0)
    lsr   = zones.get('lsr', 0)

    bias_emoji = {'SHORT': '⚫', 'LONG': '🟢', 'NEUTRAL': '⚪'}[bias]
    conf_stars = lambda c: '⭐' * c + '☆' * (3 - c)

    now_cst = datetime.datetime.utcnow().strftime('%m-%d %H:%M') + ' CST+8'

    lines = [
        f'🔱 梵天战场预判 | {sym} | {now_cst}',
        f'━━━ 当前: ${price:,.1f} | 体制: {reg} | 偏向: {bias_emoji} {bias} ({bs:+d}) ━━━',
        '',
    ]

    # 高空区
    lines += [
        f'🔴 高空区: ${hs["low"]:,.0f}–${hs["high"]:,.0f}',
        f'   置信度: {conf_stars(hs["confidence"])} | 依据: {" + ".join(hs["sources"][:3])}',
        f'   → 到达即可布空 | SL=${hs["sl"]:,.0f} TP=${hs["tp"]:,.0f} RR={hs["rr"]}',
        '',
    ]

    # 低多区
    lines += [
        f'🟢 低多区: ${ll["low"]:,.0f}–${ll["high"]:,.0f}',
        f'   置信度: {conf_stars(ll["confidence"])} | 依据: {" + ".join(ll["sources"][:3])}',
        f'   → 到达可考虑轻多 | SL=${ll["sl"]:,.0f} TP=${ll["tp"]:,.0f} RR={ll["rr"]}',
        '',
    ]

    # 方仓HCME
    if zones.get('hcme_note'):
        lines += [f'📊 {zones["hcme_note"]}', '']

    # 路径概率
    lines += [
        f'⚡ 路径概率:',
        f'   {int(sp["up_first"]*100)}% → 先涨至高空区再空',
        f'   {int(sp["down_first"]*100)}% → 直接跌向低多区',
        '',
    ]

    # 偏向原因（简洁版）
    reasons = zones.get('bias_reasons', [])
    if reasons:
        lines.append(f'📌 偏向依据: {" | ".join(reasons[:3])}')

    lines.append(f'RSI: 1H={rsi1h:.1f} 4H={rsi4h:.1f} | FR={fr:+.4f} | LSR={lsr:.2f}')
    lines.append('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')

    return '\n'.join(lines)


# ═══════════════════════════════════════════════════════════════
# Layer 2: 触及检测
# ═══════════════════════════════════════════════════════════════

def check_zone_touch(symbol: str, current_price: float = 0) -> Optional[dict]:
    """
    检测价格是否进入预设区间
    返回: {'zone': 'high_short'|'low_long', 'price': float, 'zones': dict}
    或 None（未触及）
    """
    zones = calc_zones(symbol)
    if not current_price:
        try:
            from data_cache import get_ticker
            tk = get_ticker(symbol)
            current_price = float(tk.get('lastPrice', 0))
        except Exception:
            return None

    hs = zones['high_short']
    ll = zones['low_long']

    if hs['low'] <= current_price <= hs['high']:
        return {
            'zone':   'high_short',
            'price':  current_price,
            'zones':  zones,
            'msg':    f"⚠️ {symbol[:3]}触及高空区 ${current_price:,.0f}｜布空窗口 SL=${hs['sl']:,.0f} TP=${hs['tp']:,.0f} RR={hs['rr']}",
        }

    if ll['low'] <= current_price <= ll['high']:
        return {
            'zone':   'low_long',
            'price':  current_price,
            'zones':  zones,
            'msg':    f"⚠️ {symbol[:3]}触及低多区 ${current_price:,.0f}｜轻多窗口 SL=${ll['sl']:,.0f} TP=${ll['tp']:,.0f} RR={ll['rr']}",
        }

    return None


# ═══════════════════════════════════════════════════════════════
# CLI 入口
# ═══════════════════════════════════════════════════════════════

def run_zone_report(symbols: list = None, push: bool = False):
    """生成并可选推送战场预判报告"""
    if not symbols:
        symbols = ['BTCUSDT', 'ETHUSDT']

    reports = []
    for sym in symbols:
        try:
            zones  = calc_zones(sym, force_refresh=True)
            report = format_zone_report(zones)
            reports.append(report)
            print(report)
            print()
        except Exception as e:
            print(f'[{sym}] 计算失败: {e}')

    if push and reports:
        try:
            from system_config import JARVIS_USER_ID, JARVIS_THREAD_ID
            import subprocess, json as _json
            full_msg = '\n\n'.join(reports)
            payload = _json.dumps({
                'action': 'send',
                'channel': 'jarvis',
                'target': f'{JARVIS_USER_ID}:thread:{JARVIS_THREAD_ID}',
                'message': full_msg,
            })
            subprocess.run(['openclaw', 'message', '--json', payload],
                           capture_output=True, timeout=15)
        except Exception as e:
            print(f'推送失败: {e}')

    return reports


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='梵天战场预判层')
    parser.add_argument('--symbols', nargs='+', default=['BTCUSDT', 'ETHUSDT'])
    parser.add_argument('--push', action='store_true', help='推送到Jarvis')
    parser.add_argument('--touch', action='store_true', help='检测价格触及')
    args = parser.parse_args()

    if args.touch:
        for sym in args.symbols:
            result = check_zone_touch(sym)
            if result:
                print(result['msg'])
            else:
                zones = calc_zones(sym)
                hs = zones['high_short']; ll = zones['low_long']
                p  = zones['price']
                print(f'{sym[:3]} ${p:,.1f} — 未触及 | 高空区: ${hs["low"]:,.0f}-${hs["high"]:,.0f} | 低多区: ${ll["low"]:,.0f}-${ll["high"]:,.0f}')
    else:
        run_zone_report(args.symbols, push=args.push)
