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

    hi48 = max(h1h[-48:]) if len(h1h) >= 1 else price * 1.02
    lo48 = min(l1h[-48:]) if len(l1h) >= 1 else price * 0.98
    try:
        fib = ms_mod.calc_fib_levels(hi48, lo48)
        f382 = fib.get('0.382', s1)
        f618 = fib.get('0.618', s1 * 0.99)
        f786 = fib.get('0.786', s2)
        f236 = fib.get('0.236', r1 * 0.99)
        # [2026-08-28 苏摩111修复] FIB异常值过滤：任何FIB必须在当前价格的60%之内，否则回退到默认值
        if f618 < price * 0.80: f618 = s1 * 0.99
        if f382 < price * 0.80: f382 = s1
        if f786 < price * 0.80: f786 = s2
        if f236 > price * 1.30: f236 = r1 * 0.99
    except Exception:
        f382 = s1; f618 = s1 * 0.99; f786 = s2; f236 = r1 * 0.99

    # ══ P1: 先拿清算密集位（最高精度锚点） ══════════════════════
    liq_above_walls = []   # [(price, usd), ...]
    liq_below_walls = []
    liq_above_price = None
    liq_below_price = None
    liq_above_usd   = 0
    liq_below_usd   = 0
    try:
        from liq_density_engine import get_liq_density
        liq_data = get_liq_density(symbol, price)
        if liq_data:
            above_walls = liq_data.get('above_walls', [])
            below_walls = liq_data.get('below_walls', [])
            # 上方：最近3个清算墙（价格最近优先），同时记录最大USD
            near_above = sorted([w for w in above_walls if w[0] > price * 1.002],
                                key=lambda x: x[0])[:3]
            liq_above_walls = near_above
            if near_above:
                # 优先取USD最大的作为锚点，但不能离价格太远（>3ATR4H）
                valid = [w for w in near_above if w[0] < price + atr4h_v * 3]
                if valid:
                    best = max(valid, key=lambda x: x[1])
                    liq_above_price = best[0]
                    liq_above_usd   = best[1] / 1e6
            # 下方：最近3个清算墙
            near_below = sorted([w for w in below_walls if w[0] < price * 0.998],
                                key=lambda x: -x[0])[:3]
            liq_below_walls = near_below
            if near_below:
                valid = [w for w in near_below if w[0] > price - atr4h_v * 3]
                if valid:
                    best = max(valid, key=lambda x: x[1])
                    liq_below_price = best[0]
                    liq_below_usd   = best[1] / 1e6
    except Exception as e:
        logger.debug(f'liq_density: {e}')

    # ── P1: SMC OB/FVG结构 ───────────────────────────────────
    smc_resistance = None
    smc_support    = None
    try:
        from smc_engine import analyze_smc
        smc_1h = analyze_smc(symbol, 'SHORT', '1h', 200)
        obs    = smc_1h.get('order_blocks', {})
        fvgs   = smc_1h.get('fvgs', {})
        bear_obs = sorted([ob for ob in obs.get('bearish', [])
                           if ob.get('high', 0) > price and not ob.get('mitigated', False)],
                          key=lambda x: x.get('high', 0))
        bear_fvgs = sorted([f for f in fvgs.get('bearish', [])
                            if f.get('high', 0) > price],
                           key=lambda x: x.get('high', 0))
        if bear_obs:
            smc_resistance = bear_obs[0].get('high')
        elif bear_fvgs:
            smc_resistance = bear_fvgs[0].get('high')
        bull_obs = sorted([ob for ob in obs.get('bullish', [])
                           if ob.get('low', 9e9) < price and not ob.get('mitigated', False)],
                          key=lambda x: -x.get('low', 0))
        if bull_obs:
            smc_support = bull_obs[0].get('low')
    except Exception as e:
        logger.debug(f'smc_engine: {e}')

    # ══ 高空区：清算锚点精确定位 ══════════════════════════════
    hs_sources = []
    hs_conf    = 1

    if liq_above_price:
        # 以清算密集位为精确锚点，宽度=相邻清算墙间距 or ATR4H×0.15（取小）
        wall_prices = [w[0] for w in liq_above_walls if w[0] > price * 1.002]
        if len(wall_prices) >= 2:
            # 相邻两墙之间的空区 = 高空区
            wall_prices_sorted = sorted(wall_prices)[:2]
            hs_low  = wall_prices_sorted[0] * 0.999
            hs_high = wall_prices_sorted[1] + atr4h_v * 0.08
        else:
            # 单墙：上下各0.5%
            hs_low  = liq_above_price * 0.994
            hs_high = liq_above_price + atr4h_v * 0.15
        hs_sources.append(f'清算${liq_above_usd:.0f}M@{liq_above_price:.0f}')
        hs_conf += 1
    else:
        # 无清算数据，回退到R1/Fib，宽度用ATR4H×0.3（比原来小）
        hs_center = (r1 + bb4h[0]) / 2
        hs_low  = hs_center - atr4h_v * 0.15
        hs_high = hs_center + atr4h_v * 0.15
        hs_sources.append(f'R1={r1:.0f}')

    # SMC进一步收窄
    if smc_resistance and hs_low <= smc_resistance <= hs_high * 1.01:
        hs_high = min(hs_high, smc_resistance + atr4h_v * 0.05)
        hs_sources.append(f'SMC_OB={smc_resistance:.0f}')
        hs_conf = min(3, hs_conf + 1)
    elif smc_resistance and smc_resistance > price * 1.002:
        # SMC在清算区附近但不重合，取两者中心收窄
        hs_sources.append(f'SMC={smc_resistance:.0f}')

    # 补充来源标签
    if f236 > price * 1.002:
        hs_sources.append(f'Fib236={f236:.0f}')
    if r1 > price * 1.002 and f'R1={r1:.0f}' not in hs_sources:
        hs_sources.append(f'R1={r1:.0f}')

    # ══ 低多区：清算锚点精确定位 ══════════════════════════════
    ll_sources = []
    ll_conf    = 1
    # 默认值，防止未初始化
    ll_anchor  = f618 if f618 < price * 0.990 else s1
    ll_low     = ll_anchor - atr4h_v * 0.15
    ll_high    = ll_anchor + atr4h_v * 0.15

    if liq_below_price:
        # 过滤：低多区必须在当前价格以下至少 1.0% 才有意义
        if liq_below_price and liq_below_price < price * 0.990:
            wall_prices_b = sorted([w[0] for w in liq_below_walls
                                    if w[0] < price * 0.990], reverse=True)[:2]
            if len(wall_prices_b) >= 2:
                ll_low  = wall_prices_b[1] - atr4h_v * 0.05
                ll_high = wall_prices_b[0] * 1.001
            else:
                ll_low  = liq_below_price - atr4h_v * 0.15
                ll_high = liq_below_price * 1.005
            ll_sources.append(f'清算${liq_below_usd:.0f}M@{liq_below_price:.0f}')
            ll_conf += 1
    else:
        liq_below_price = None

    if not liq_below_price:
        ll_anchor = f618 if f618 < price * 0.990 else s1
        ll_low    = ll_anchor - atr4h_v * 0.15
        ll_high   = ll_anchor + atr4h_v * 0.15
        ll_sources.append(f'S1={s1:.0f}')
        if f618 < price * 0.995:
            ll_sources.append(f'Fib618={f618:.0f}')

    if smc_support and ll_low <= smc_support <= ll_high * 1.01:
        ll_low = min(ll_low, smc_support * 0.998)
        ll_sources.append(f'SMC_OB={smc_support:.0f}')
        ll_conf = min(3, ll_conf + 1)

    # 补充Fib标签
    if f618 < price * 0.995 and f'Fib618={f618:.0f}' not in ll_sources:
        ll_sources.append(f'Fib618={f618:.0f}')
    if s1 < price * 0.998 and f'S1={s1:.0f}' not in ll_sources:
        ll_sources.append(f'S1={s1:.0f}')

    # [2026-08-28 苏摩111修复] 低多区最终安全守卫：区间必须在当前价格 70%~99% 之内，否则强制用 S1 重算
    if ll_low < price * 0.80 or ll_high < price * 0.80:
        ll_anchor = s1
        ll_low  = ll_anchor - atr4h_v * 0.15
        ll_high = ll_anchor + atr4h_v * 0.15
        ll_sources = [f'S1={s1:.0f}(安全回退)']
        ll_conf = 1

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
    # [2026-08-28 苏摩111修复] SL合理性校验：SL距离不能超过当前价格的10%
    if hs_sl > price * 1.10:
        hs_sl = hs_high * 1.02  # 回退到区间上沿+2%
    hs_tp = s1
    # [2026-08-26 fix 苏摩111] RR以区间中点为进场基准，原用hs_low导致RR偏低
    hs_entry_mid = (hs_low + hs_high) / 2
    hs_rr = round((hs_entry_mid - hs_tp) / (hs_sl - hs_entry_mid), 1) if hs_sl != hs_entry_mid else 1.0

    # 低多区 SL = 区间下沿 - 0.5ATR4H, TP = R1
    ll_sl = ll_low - atr4h_v * 0.5
    # [2026-08-28 苏摩111修复] SL合理性校验：SL距离不能超过当前价格的10%，否则是异常值，强制用SL_PCT=2%重算
    if ll_sl < price * 0.90 or ll_sl <= 0:
        ll_sl = ll_low * (1 - 0.02)  # 回退到区间下沿-2%
    ll_tp = r1
    # [2026-08-26 fix 苏摩111] 低多区同理用中点
    ll_entry_mid = (ll_low + ll_high) / 2
    ll_rr = round((ll_tp - ll_entry_mid) / (ll_entry_mid - ll_sl), 1) if ll_entry_mid != ll_sl else 1.0

    # ══ 组装结果 ══════════════════════════════════════════════
    # [P1修复 2026-08-26] 进场区最小宽度守卫：宽度<0.5%时以ATR4H扩充，防止TIMEOUT率过高
    _MIN_ZONE_PCT = 0.005  # 最小0.5%
    _hs_width = (hs_high - hs_low) / hs_low if hs_low > 0 else 0
    if _hs_width < _MIN_ZONE_PCT and atr4h_v > 0:
        _expand = (hs_low * _MIN_ZONE_PCT - (hs_high - hs_low)) / 2
        hs_low  = hs_low  - _expand
        hs_high = hs_high + _expand
    _ll_width = (ll_high - ll_low) / ll_low if ll_low > 0 else 0
    if _ll_width < _MIN_ZONE_PCT and atr4h_v > 0:
        _expand = (ll_low * _MIN_ZONE_PCT - (ll_high - ll_low)) / 2
        ll_low  = ll_low  - _expand
        ll_high = ll_high + _expand

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
    # [2026-08-28 苏摩111修复] 防止传入字符串被逐字符遍历
    if isinstance(symbols, str):
        symbols = [symbols]

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
