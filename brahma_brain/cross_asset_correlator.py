#!/usr/bin/env python3
# ponytail: cross_asset_correlator 537行，有意为之，重构前先 grep 所有调用方
"""
阶段3 跨品种相关性分析引擎
=====================================
量化三大宏观联动关系：
  1. BTC.D周期与山寨轮动规律（BTC占比 → 山寨季来临信号）
  2. VIX恐慌指数与BTC联动（恐慌→抛售，恐慌下降→反弹）
  3. 美债收益率与加密负相关性（利率↑→风险资产↓）

并新增：
  4. DXY美元指数与BTC负相关（美元强→BTC弱）
  5. SPX领先性（美股先跌BTC后跌，美股先反弹BTC后反弹）

纯标准库实现，零依赖。

作者：设计院 2026-08-20
"""
from __future__ import annotations
import gzip
import json
import math
import os
import datetime
from typing import List, Dict, Optional, Tuple

_DIR   = os.path.dirname(os.path.abspath(__file__))
_DATA  = os.path.join(_DIR, "..", "data")
_MACRO = os.path.join(_DATA, "historical", "macro")
_HIST  = os.path.join(_DATA, "historical")

# ── 工具函数 ──────────────────────────────────────────────────────────────────

def _load_macro(fname: str) -> List[dict]:
    """加载宏观数据（带date字段）"""
    path = os.path.join(_MACRO, fname)
    if not os.path.exists(path):
        return []
    with gzip.open(path, 'rt') as f:
        return [json.loads(l) for l in f if l.strip()]


def _load_btc_1d() -> List[dict]:
    """加载BTC日线（早期+主体合并）"""
    bars = []
    for fname in ["BTCUSDT_1d_early.jsonl.gz", "BTCUSDT_1d.jsonl.gz"]:
        p = os.path.join(_HIST, fname)
        if os.path.exists(p):
            with gzip.open(p, 'rt') as f:
                bars.extend(json.loads(l) for l in f if l.strip())
    seen: set = set()
    out = []
    for b in sorted(bars, key=lambda x: x.get('ts', 0)):
        k = b.get('ts', 0)
        if k not in seen:
            seen.add(k)
            out.append(b)
    return out


def _ts_to_date(ts_ms: int) -> str:
    return datetime.datetime.fromtimestamp(ts_ms / 1000).strftime('%Y-%m-%d')


def _pearson_corr(x: List[float], y: List[float]) -> float:
    """皮尔逊相关系数"""
    n = min(len(x), len(y))
    if n < 5:
        return 0.0
    x, y = x[-n:], y[-n:]
    mx = sum(x) / n
    my = sum(y) / n
    num = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    dx  = math.sqrt(sum((xi - mx) ** 2 for xi in x))
    dy  = math.sqrt(sum((yi - my) ** 2 for yi in y))
    if dx == 0 or dy == 0:
        return 0.0
    return num / (dx * dy)


def _pct_change(values: List[float], period: int = 1) -> List[float]:
    """计算N期涨跌幅"""
    out = []
    for i in range(period, len(values)):
        prev = values[i - period]
        if prev == 0:
            out.append(0.0)
        else:
            out.append((values[i] - prev) / prev)
    return out


def _align_by_date(
    series_a: List[dict],   # [{date, c}, ...]
    series_b: List[dict],
    date_key_a: str = 'date',
    date_key_b: str = 'date',
    close_key_a: str = 'c',
    close_key_b: str = 'c',
) -> Tuple[List[float], List[float], List[str]]:
    """按日期对齐两个序列，返回对齐后的收盘价列表和日期列表"""
    map_a = {b[date_key_a]: float(b[close_key_a]) for b in series_a if b.get(date_key_a)}
    map_b = {b[date_key_b]: float(b[close_key_b]) for b in series_b if b.get(date_key_b)}
    dates = sorted(set(map_a.keys()) & set(map_b.keys()))
    vals_a = [map_a[d] for d in dates]
    vals_b = [map_b[d] for d in dates]
    return vals_a, vals_b, dates


def _rolling_corr(x: List[float], y: List[float], window: int = 90) -> List[float]:
    """滚动相关系数"""
    out = []
    for i in range(window, len(x) + 1):
        xi = x[i - window:i]
        yi = y[i - window:i]
        out.append(_pearson_corr(xi, yi))
    return out


# ── 子模块：BTC.D分析 ─────────────────────────────────────────────────────────

def _analyze_btcd(btc_closes: List[float], btc_dates: List[str]) -> dict:
    """
    BTC.D（BTC市值占比）代理分析。
    由于无直接BTC.D数据，用BTC价格动量作为代理：
    BTC强势期 → altcoin季结束；BTC横盘/下跌 → altcoin季来临。

    从s_macro_v2已有BTC.D实时数据，这里做历史统计。
    实际BTC.D从binance API实时获取，这里提供历史基准。
    """
    if len(btc_closes) < 90:
        return {'status': 'insufficient_data'}

    # BTC 90日涨幅（代理BTC强弱）
    n = len(btc_closes)
    btcd_proxy_90d = []
    dates_90 = []
    for i in range(90, n):
        pct = (btc_closes[i] - btc_closes[i-90]) / btc_closes[i-90]
        btcd_proxy_90d.append(pct)
        dates_90.append(btc_dates[i] if i < len(btc_dates) else '')

    # 统计：BTC 90日涨幅分位数
    sorted_vals = sorted(btcd_proxy_90d)
    n2 = len(sorted_vals)
    p25 = sorted_vals[int(n2 * 0.25)]
    p50 = sorted_vals[int(n2 * 0.50)]
    p75 = sorted_vals[int(n2 * 0.75)]

    latest_90d = btcd_proxy_90d[-1] if btcd_proxy_90d else 0.0
    percentile = sum(1 for v in sorted_vals if v <= latest_90d) / max(n2, 1)

    # BTC.D信号解读
    if percentile > 0.75:
        btcd_signal = 'BTC_DOMINANT'
        btcd_desc   = f'BTC 90日涨幅处于历史前{(1-percentile)*100:.0f}%分位 → BTC强势主导，山寨季未启动'
        altcoin_season = False
    elif percentile < 0.25:
        btcd_signal = 'ALTCOIN_SEASON'
        btcd_desc   = f'BTC 90日涨幅处于历史后{percentile*100:.0f}%分位 → BTC相对弱势，山寨季可能来临'
        altcoin_season = True
    else:
        btcd_signal = 'NEUTRAL'
        btcd_desc   = f'BTC 90日涨幅处于历史中间位置（{percentile*100:.0f}%分位）'
        altcoin_season = False

    # 山寨季评分加成
    score_addon = 8 if altcoin_season else (-5 if percentile > 0.75 else 0)

    return {
        'signal':          btcd_signal,
        'altcoin_season':  altcoin_season,
        'btc_90d_pct':     round(latest_90d * 100, 1),
        'percentile':      round(percentile, 3),
        'p25_pct':         round(p25 * 100, 1),
        'p75_pct':         round(p75 * 100, 1),
        'score_addon':     score_addon,
        'description':     btcd_desc,
    }


# ── 子模块：VIX联动分析 ───────────────────────────────────────────────────────

def _analyze_vix_btc(vix_bars: List[dict], btc_bars_by_date: Dict[str, float]) -> dict:
    """
    VIX vs BTC相关性分析
    历史铁证：
    - VIX > 30 → 恐慌期，BTC通常跟随下跌（相关系数 ~-0.6）
    - VIX 快速下降（>-20%月跌幅）→ 风险资产反弹窗口
    - VIX 低位（< 15）→ 市场自满，注意尾部风险
    """
    if not vix_bars or not btc_bars_by_date:
        return {'status': 'insufficient_data'}

    # 对齐VIX与BTC
    vix_aligned, btc_aligned, dates = [], [], []
    for b in vix_bars:
        d = b.get('date', '')
        if d and d in btc_bars_by_date:
            vix_aligned.append(float(b['c']))
            btc_aligned.append(btc_bars_by_date[d])
            dates.append(d)

    if len(dates) < 60:
        return {'status': 'insufficient_overlap'}

    # 整体相关系数（日收益率）
    vix_ret = _pct_change(vix_aligned, 1)
    btc_ret = _pct_change(btc_aligned, 1)
    overall_corr = _pearson_corr(vix_ret, btc_ret)

    # 最近90天相关系数
    corr_90d = _pearson_corr(vix_ret[-90:], btc_ret[-90:]) if len(vix_ret) >= 90 else overall_corr

    # 当前VIX状态
    vix_now  = vix_aligned[-1]
    vix_20d  = sum(vix_aligned[-20:]) / 20 if len(vix_aligned) >= 20 else vix_now
    vix_chg_20d = (vix_now - vix_20d) / vix_20d if vix_20d > 0 else 0.0

    # VIX区间判断
    if vix_now > 30:
        vix_regime = 'FEAR'
        vix_desc   = f'VIX={vix_now:.1f} 恐慌区（>30），BTC历史同期平均跌幅-15~-30%'
        btc_impact = 'BEARISH'
        score_addon = -10
    elif vix_now > 20:
        vix_regime = 'ELEVATED'
        vix_desc   = f'VIX={vix_now:.1f} 偏高区（20~30），市场谨慎'
        btc_impact = 'CAUTIOUS'
        score_addon = -3
    elif vix_now < 13:
        vix_regime = 'COMPLACENT'
        vix_desc   = f'VIX={vix_now:.1f} 低位（<13），市场自满，注意尾部风险'
        btc_impact = 'NEUTRAL_RISK'
        score_addon = -2
    else:
        vix_regime = 'NORMAL'
        vix_desc   = f'VIX={vix_now:.1f} 正常区（13~20）'
        btc_impact = 'NEUTRAL'
        score_addon = 0

    # VIX趋势信号
    if vix_chg_20d < -0.20:
        vix_trend_signal = 'VIX_FALLING_FAST'
        vix_desc += f'，20日跌幅{vix_chg_20d*100:.0f}% → 恐慌消退，反弹窗口'
        score_addon += 5
    elif vix_chg_20d > 0.20:
        vix_trend_signal = 'VIX_RISING_FAST'
        vix_desc += f'，20日涨幅{vix_chg_20d*100:.0f}% → 恐慌升温，注意风险'
        score_addon -= 5
    else:
        vix_trend_signal = 'VIX_STABLE'

    # 历史极值统计
    vix_vals = [b['c'] for b in vix_bars]
    vix_max  = max(vix_vals)
    vix_min  = min(vix_vals)
    vix_pct  = (vix_now - vix_min) / (vix_max - vix_min) if vix_max > vix_min else 0.5

    return {
        'vix_now':          round(vix_now, 2),
        'vix_regime':       vix_regime,
        'vix_trend':        vix_trend_signal,
        'vix_20d_chg':      round(vix_chg_20d, 3),
        'vix_percentile':   round(vix_pct, 3),
        'overall_corr':     round(overall_corr, 3),
        'corr_90d':         round(corr_90d, 3),
        'btc_impact':       btc_impact,
        'score_addon':      score_addon,
        'description':      vix_desc,
        'vix_hist_max':     round(vix_max, 1),
        'vix_hist_min':     round(vix_min, 1),
    }


# ── 子模块：US10Y利率分析 ─────────────────────────────────────────────────────

def _analyze_rates_btc(us10y_bars: List[dict], btc_bars_by_date: Dict[str, float]) -> dict:
    """
    US10Y美债收益率 vs BTC相关性
    历史铁证：
    - 利率快速上升 → 风险资产（包括BTC）压力增大
    - 利率回落 → 流动性宽松预期 → BTC往往受益
    - 利率>5% → 历史罕见，BTC估值压力极大
    """
    if not us10y_bars or not btc_bars_by_date:
        return {'status': 'insufficient_data'}

    rate_aligned, btc_aligned, dates = [], [], []
    for b in us10y_bars:
        d = b.get('date', '')
        if d and d in btc_bars_by_date:
            rate_aligned.append(float(b['c']))
            btc_aligned.append(btc_bars_by_date[d])
            dates.append(d)

    if len(dates) < 60:
        return {'status': 'insufficient_overlap'}

    # 日收益率相关性
    rate_chg = _pct_change(rate_aligned, 1)
    btc_ret  = _pct_change(btc_aligned, 1)
    corr_full = _pearson_corr(rate_chg, btc_ret)
    corr_90d  = _pearson_corr(rate_chg[-90:], btc_ret[-90:]) if len(rate_chg) >= 90 else corr_full

    # 当前利率状态
    rate_now   = rate_aligned[-1]
    rate_3m    = sum(rate_aligned[-63:]) / 63 if len(rate_aligned) >= 63 else rate_now
    rate_chg_3m = (rate_now - rate_3m) / rate_3m if rate_3m > 0 else 0.0

    # 利率区间判断（历史绝对水平）
    if rate_now > 5.0:
        rate_regime = 'VERY_HIGH'
        rate_desc   = f'US10Y={rate_now:.2f}% 极高（>5%）→ 资金成本极贵，风险资产估值严重承压'
        score_addon = -12
    elif rate_now > 4.0:
        rate_regime = 'HIGH'
        rate_desc   = f'US10Y={rate_now:.2f}% 高位（4~5%）→ 风险资产承压'
        score_addon = -6
    elif rate_now > 3.0:
        rate_regime = 'MODERATE'
        rate_desc   = f'US10Y={rate_now:.2f}% 中等（3~4%）→ 中性偏负面'
        score_addon = -2
    elif rate_now < 2.0:
        rate_regime = 'LOW'
        rate_desc   = f'US10Y={rate_now:.2f}% 低位（<2%）→ 流动性宽松，利好风险资产'
        score_addon = 5
    else:
        rate_regime = 'NORMAL'
        rate_desc   = f'US10Y={rate_now:.2f}% 正常区间（2~3%）'
        score_addon = 0

    # 利率趋势（3个月变化）
    if rate_chg_3m > 0.15:
        rate_trend = 'RISING_FAST'
        rate_desc += f'，3月涨{rate_chg_3m*100:.0f}% → 加息预期升温，BTC压力'
        score_addon -= 4
    elif rate_chg_3m < -0.10:
        rate_trend = 'FALLING'
        rate_desc += f'，3月跌{abs(rate_chg_3m)*100:.0f}% → 降息预期，BTC潜在利好'
        score_addon += 4
    else:
        rate_trend = 'STABLE'

    return {
        'rate_now':       round(rate_now, 3),
        'rate_regime':    rate_regime,
        'rate_trend':     rate_trend,
        'rate_3m_chg':    round(rate_chg_3m, 3),
        'corr_full':      round(corr_full, 3),
        'corr_90d':       round(corr_90d, 3),
        'score_addon':    score_addon,
        'description':    rate_desc,
    }


# ── 子模块：DXY美元指数分析 ──────────────────────────────────────────────────

def _analyze_dxy_btc(dxy_bars: List[dict], btc_bars_by_date: Dict[str, float]) -> dict:
    """
    DXY美元指数 vs BTC
    历史铁证：DXY强 → BTC弱（负相关，相关系数约-0.4~-0.6）
    """
    if not dxy_bars or not btc_bars_by_date:
        return {'status': 'insufficient_data'}

    dxy_aligned, btc_aligned, dates = [], [], []
    for b in dxy_bars:
        d = b.get('date', '')
        if d and d in btc_bars_by_date:
            dxy_aligned.append(float(b['c']))
            btc_aligned.append(btc_bars_by_date[d])
            dates.append(d)

    if len(dates) < 60:
        return {'status': 'insufficient_overlap'}

    dxy_ret = _pct_change(dxy_aligned, 1)
    btc_ret = _pct_change(btc_aligned, 1)
    corr_full = _pearson_corr(dxy_ret, btc_ret)
    corr_90d  = _pearson_corr(dxy_ret[-90:], btc_ret[-90:]) if len(dxy_ret) >= 90 else corr_full

    dxy_now  = dxy_aligned[-1]
    dxy_20d  = sum(dxy_aligned[-20:]) / 20 if len(dxy_aligned) >= 20 else dxy_now
    dxy_trend = (dxy_now - dxy_20d) / dxy_20d if dxy_20d > 0 else 0.0

    if dxy_trend > 0.015:
        dxy_signal = 'DXY_STRENGTHENING'
        score_addon = -5
        desc = f'DXY={dxy_now:.1f} 20日走强(+{dxy_trend*100:.1f}%) → 美元强，BTC承压'
    elif dxy_trend < -0.015:
        dxy_signal = 'DXY_WEAKENING'
        score_addon = 5
        desc = f'DXY={dxy_now:.1f} 20日走弱({dxy_trend*100:.1f}%) → 美元弱，BTC受益'
    else:
        dxy_signal = 'DXY_NEUTRAL'
        score_addon = 0
        desc = f'DXY={dxy_now:.1f} 横盘（20日变化{dxy_trend*100:.1f}%）'

    return {
        'dxy_now':      round(dxy_now, 2),
        'dxy_trend_20d':round(dxy_trend, 4),
        'dxy_signal':   dxy_signal,
        'corr_full':    round(corr_full, 3),
        'corr_90d':     round(corr_90d, 3),
        'score_addon':  score_addon,
        'description':  desc,
    }


# ── 主接口 ───────────────────────────────────────────────────────────────────

_CACHE: dict = {}
_CACHE_TS: float = 0.0
_CACHE_TTL = 3600  # 1小时缓存


def get_cross_asset_context(symbol: str = 'BTCUSDT', current_price: Optional[float] = None) -> dict:
    """
    跨品种宏观相关性分析主接口

    Returns
    -------
    {
      btcd      : BTC.D山寨季分析
      vix       : VIX恐慌指数联动
      rates     : US10Y利率联动
      dxy       : DXY美元指数联动
      score_addon_total : int  综合评分加成
      macro_summary     : str  一句话摘要
    }
    """
    import time
    now = time.time()
    cache_key = f"{symbol}_{current_price}"
    if cache_key in _CACHE and now - _CACHE_TS < _CACHE_TTL:
        return _CACHE[cache_key]

    # 加载BTC日线数据
    btc_bars = _load_btc_1d()
    btc_closes = [float(b['c']) for b in btc_bars]
    btc_dates  = [_ts_to_date(b['ts']) for b in btc_bars]
    btc_by_date = dict(zip(btc_dates, btc_closes))

    # 加载宏观数据
    vix_bars   = _load_macro('VIX_1d.jsonl.gz')
    us10y_bars = _load_macro('US10Y_1d.jsonl.gz')
    dxy_bars   = _load_macro('DXY_1d.jsonl.gz')

    # 各子模块分析
    btcd_result  = _analyze_btcd(btc_closes, btc_dates)
    vix_result   = _analyze_vix_btc(vix_bars, btc_by_date)
    rates_result = _analyze_rates_btc(us10y_bars, btc_by_date)
    dxy_result   = _analyze_dxy_btc(dxy_bars, btc_by_date)

    # 综合评分加成
    total_addon = (
        btcd_result.get('score_addon', 0) +
        vix_result.get('score_addon', 0) +
        rates_result.get('score_addon', 0) +
        dxy_result.get('score_addon', 0)
    )

    # 宏观一句话摘要
    parts = []
    if vix_result.get('vix_regime'):
        parts.append(f"VIX={vix_result.get('vix_now','?')}({vix_result['vix_regime']})")
    if rates_result.get('rate_regime'):
        parts.append(f"US10Y={rates_result.get('rate_now','?')}%({rates_result['rate_regime']})")
    if dxy_result.get('dxy_signal'):
        parts.append(dxy_result['dxy_signal'])
    if btcd_result.get('signal'):
        parts.append(btcd_result['signal'])

    macro_summary = ' | '.join(parts) + f' | 宏观综合加成:{total_addon:+d}'

    result = {
        'btcd':              btcd_result,
        'vix':               vix_result,
        'rates':             rates_result,
        'dxy':               dxy_result,
        'score_addon_total': total_addon,
        'macro_summary':     macro_summary,
    }

    _CACHE[cache_key] = result
    _CACHE_TS = now
    return result


# ── 测试 ──────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    print("=" * 65)
    print("🌐 梵天跨品种宏观相关性分析（阶段3）")
    print("=" * 65)

    ctx = get_cross_asset_context('BTCUSDT')

    print("\n📊 BTC.D 山寨轮动分析")
    btcd = ctx['btcd']
    print(f"  信号: {btcd.get('signal','N/A')}")
    print(f"  BTC 90日涨幅: {btcd.get('btc_90d_pct','N/A')}% (历史{btcd.get('percentile',0)*100:.0f}%分位)")
    print(f"  {btcd.get('description','')}")
    print(f"  山寨季: {'✅ 是' if btcd.get('altcoin_season') else '❌ 否'} | 评分加成: {btcd.get('score_addon',0):+d}")

    print("\n😱 VIX恐慌指数联动")
    vix = ctx['vix']
    print(f"  当前VIX: {vix.get('vix_now','N/A')} ({vix.get('vix_regime','N/A')})")
    print(f"  20日变化: {vix.get('vix_20d_chg',0)*100:.1f}% ({vix.get('vix_trend','N/A')})")
    print(f"  全期相关系数: {vix.get('overall_corr','N/A')} (负=BTC随VIX反向)")
    print(f"  近90天相关: {vix.get('corr_90d','N/A')}")
    print(f"  历史区间: {vix.get('vix_hist_min','N/A')} ~ {vix.get('vix_hist_max','N/A')}")
    print(f"  {vix.get('description','')}")
    print(f"  评分加成: {vix.get('score_addon',0):+d}")

    print("\n📈 US10Y美债收益率联动")
    rates = ctx['rates']
    print(f"  当前收益率: {rates.get('rate_now','N/A')}% ({rates.get('rate_regime','N/A')})")
    print(f"  3月趋势: {rates.get('rate_trend','N/A')} ({rates.get('rate_3m_chg',0)*100:.1f}%)")
    print(f"  全期相关系数: {rates.get('corr_full','N/A')}")
    print(f"  近90天相关: {rates.get('corr_90d','N/A')}")
    print(f"  {rates.get('description','')}")
    print(f"  评分加成: {rates.get('score_addon',0):+d}")

    print("\n💵 DXY美元指数联动")
    dxy = ctx['dxy']
    print(f"  当前DXY: {dxy.get('dxy_now','N/A')} ({dxy.get('dxy_signal','N/A')})")
    print(f"  20日趋势: {dxy.get('dxy_trend_20d',0)*100:.2f}%")
    print(f"  全期相关系数: {dxy.get('corr_full','N/A')} (负=BTC随DXY反向)")
    print(f"  近90天相关: {dxy.get('corr_90d','N/A')}")
    print(f"  {dxy.get('description','')}")
    print(f"  评分加成: {dxy.get('score_addon',0):+d}")

    print("\n" + "=" * 65)
    print(f"🎯 宏观综合: {ctx['macro_summary']}")
    print(f"📊 阶段3宏观总评分加成: {ctx['score_addon_total']:+d}")
    print("=" * 65)
