#!/usr/bin/env python3
# ponytail: onchain_engine 469行，独立计算引擎，功能内聚，拆分条件: 单引擎>3000行且有完整测试
"""

# STATUS: ACTIVE
# 链上数据引擎，间接调用
# LAST_REVIEW: 2026-07-01 | 属于辅助计算层，修改前确认调用链
# ─────────────────────────────────────────────────────────────
╔══════════════════════════════════════════════════════════════════╗
║  梵天大脑 · onchain_engine.py  · P1b 链上引擎                    ║
║  数据源：Binance Futures 公开端点（零API消耗）                    ║
║  维度：OI变化 / 多空比 / 主动买卖比 / 资金费率趋势 / 综合信号      ║
║  评分贡献：机构视角 0~30分（接入brahma_brain.py）                 ║
╚══════════════════════════════════════════════════════════════════╝
"""
import urllib.request, json, time

FAPI = "https://fapi.binance.com"

# ── TTL缓存 ──────────────────────────────────────────────────────
_cache: dict = {}
_TTL = 180  # 3分钟

def _get(url: str) -> dict | list | None:
    now = time.time()
    if url in _cache and now - _cache[url]['ts'] < _TTL:
        return _cache[url]['data']
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())
            _cache[url] = {'ts': now, 'data': data}
            return data
    except Exception:
        return None


try:
    import sys as _oe_sys, os as _oe_os
    _oe_sys.path.insert(0, _oe_os.path.dirname(_oe_os.path.dirname(_oe_os.path.abspath(__file__))))
    from config import coinglass_key as _cg_fn
    CG_KEY = _cg_fn()
except Exception:
    CG_KEY = "a56a2491bca5491ca3f7c7f53b6a6963"  # fallback
CG_BASE = "https://open-api-v4.coinglass.com"

def _cg(path: str, qs: str = ''):
    """CoinGlass v4 付费 API，带TTL缓存"""
    url = f"{CG_BASE}{path}" + (f"?{qs}" if qs else '')
    now = time.time()
    if url in _cache and now - _cache[url]['ts'] < _TTL:
        return _cache[url]['data']
    try:
        req = urllib.request.Request(url, headers={
            'CG-API-KEY': CG_KEY, 'User-Agent': 'brahma/4.0'
        })
        with urllib.request.urlopen(req, timeout=6) as r:
            d = json.loads(r.read())
            if str(d.get('code','0')) in ('0','200','None'):
                data = d.get('data', d)
                _cache[url] = {'ts': now, 'data': data}
                return data
    except Exception:
        pass
    return None


# ═══════════════════════════════════════════════════════════════
# 一、OI变化（机构建仓/出货识别）
# ═══════════════════════════════════════════════════════════════

def get_oi_change(symbol: str) -> dict:
    """
    返回：
      oi_now       当前OI（张）
      oi_value_now 当前OI美元价值
      oi_chg_5     过去5期OI变化%
      oi_chg_1     最近1期变化%
      oi_trend     'RISING'/'FALLING'/'FLAT'
    """
    url = f"{FAPI}/futures/data/openInterestHist?symbol={symbol}&period=1h&limit=6"
    data = _get(url)
    if not data or len(data) < 2:
        return {'oi_trend': 'UNKNOWN', 'oi_chg_5': 0, 'oi_chg_1': 0,
                'oi_now': 0, 'oi_value_now': 0}

    oi_vals = [float(d['sumOpenInterest']) for d in data]
    usd_vals = [float(d['sumOpenInterestValue']) for d in data]
    oi_now  = oi_vals[-1]
    oi_prev = oi_vals[0]
    oi_chg_5 = (oi_now - oi_prev) / oi_prev * 100 if oi_prev > 0 else 0
    oi_chg_1 = (oi_vals[-1] - oi_vals[-2]) / oi_vals[-2] * 100 if oi_vals[-2] > 0 else 0

    if oi_chg_5 > 1.5:
        trend = 'RISING'
    elif oi_chg_5 < -1.5:
        trend = 'FALLING'
    else:
        trend = 'FLAT'

    return {
        'oi_now':       oi_now,
        'oi_value_now': usd_vals[-1],
        'oi_chg_5':     round(oi_chg_5, 3),
        'oi_chg_1':     round(oi_chg_1, 3),
        'oi_trend':     trend,
    }


# ═══════════════════════════════════════════════════════════════
# 二、多空比（情绪极端识别）
# ═══════════════════════════════════════════════════════════════

def get_long_short(symbol: str) -> dict:
    """
    返回：
      ls_ratio    当前多空比（多/空）
      long_pct    多头账户占比%
      ls_extreme  'EXTREME_LONG'/'EXTREME_SHORT'/'NEUTRAL'
      ls_trend    最近5期方向
    """
    url = f"{FAPI}/futures/data/globalLongShortAccountRatio?symbol={symbol}&period=1h&limit=6"
    data = _get(url)
    if not data or len(data) < 2:
        return {'ls_ratio': 1.0, 'long_pct': 50.0, 'ls_extreme': 'NEUTRAL', 'ls_trend': 'FLAT'}

    ratios = [float(d['longShortRatio']) for d in data]
    long_pcts = [float(d['longAccount']) * 100 for d in data]

    ls_now   = ratios[-1]
    long_now = long_pcts[-1]

    # 极端判断（散户共识=反向）
    if long_now > 68:
        extreme = 'EXTREME_LONG'    # 多头拥挤→空头信号
    elif long_now < 32:
        extreme = 'EXTREME_SHORT'   # 空头拥挤→多头信号
    else:
        extreme = 'NEUTRAL'

    # 趋势：最近5期多空比变化方向
    ls_chg = ratios[-1] - ratios[0]
    ls_trend = 'RISING' if ls_chg > 0.05 else ('FALLING' if ls_chg < -0.05 else 'FLAT')

    return {
        'ls_ratio':   round(ls_now, 3),
        'long_pct':   round(long_now, 2),
        'ls_extreme': extreme,
        'ls_trend':   ls_trend,
    }


# ═══════════════════════════════════════════════════════════════
# 三、主动买卖比（大单方向）
# ═══════════════════════════════════════════════════════════════

def get_taker_ratio(symbol: str) -> dict:
    """
    返回：
      buy_ratio   主动买成交占比%
      sell_ratio  主动卖成交占比%
      taker_bias  'BUY_DOMINANT'/'SELL_DOMINANT'/'BALANCED'
    """
    url = f"{FAPI}/futures/data/takerlongshortRatio?symbol={symbol}&period=1h&limit=5"
    data = _get(url)
    if not data:
        return {'buy_ratio': 50.0, 'sell_ratio': 50.0, 'taker_bias': 'BALANCED'}

    buy_vols  = [float(d['buyVol'])  for d in data]
    sell_vols = [float(d['sellVol']) for d in data]
    total = sum(buy_vols) + sum(sell_vols)
    if total == 0:
        return {'buy_ratio': 50.0, 'sell_ratio': 50.0, 'taker_bias': 'BALANCED'}

    buy_r  = sum(buy_vols)  / total * 100
    sell_r = sum(sell_vols) / total * 100

    if buy_r > 55:
        bias = 'BUY_DOMINANT'
    elif sell_r > 55:
        bias = 'SELL_DOMINANT'
    else:
        bias = 'BALANCED'

    return {
        'buy_ratio':  round(buy_r, 2),
        'sell_ratio': round(sell_r, 2),
        'taker_bias': bias,
    }


# ═══════════════════════════════════════════════════════════════
# 四、资金费率趋势（来自 options_engine 复用，避免重复请求）
# ═══════════════════════════════════════════════════════════════

def get_funding_trend(symbol: str) -> dict:
    """
    返回：
      funding_now    当前资金费率
      funding_trend  'BULL_HEAT'/'BEAR_HEAT'/'NEUTRAL'
      funding_signal 对应方向提示
    """
    url = f"{FAPI}/fapi/v1/premiumIndex?symbol={symbol}"
    data = _get(url)
    if not data:
        return {'funding_now': 0.0, 'funding_trend': 'NEUTRAL', 'funding_signal': '-'}

    fr = float(data.get('lastFundingRate', 0))

    if fr > 0.0008:
        trend = 'BULL_HEAT'
        signal = '多头过热，注意回调风险'
    elif fr > 0.0003:
        trend = 'BULL_MILD'
        signal = '多头温和，趋势延续'
    elif fr < -0.0008:
        trend = 'BEAR_HEAT'
        signal = '空头过热，注意反弹风险'
    elif fr < -0.0003:
        trend = 'BEAR_MILD'
        signal = '空头温和，下行延续'
    else:
        trend = 'NEUTRAL'
        signal = '资金中性'

    return {
        'funding_now':    round(fr * 100, 5),
        'funding_trend':  trend,
        'funding_signal': signal,
    }



# ═══════════════════════════════════════════════════════════════
# 五（新）、CoinGlass 清算数据（机构级）
# ═══════════════════════════════════════════════════════════════

def get_liquidation_data(symbol: str) -> dict:
    """
    CoinGlass 清算列表：多空清算比例 + 总清算量
    做空信号时：多头大量清算=下跌加速
    做多信号时：空头大量清算=上涨加速
    """
    data = _cg('/api/futures/liquidation/coin-list')
    if not data:
        return {'liq_1h': 0, 'long_liq_1h': 0, 'short_liq_1h': 0,
                'liq_bias': 'NEUTRAL', 'liq_note': '清算数据不可用'}

    sym = symbol.replace('USDT', '').replace('PERP', '')
    item = next((d for d in data if d.get('symbol', '').upper() == sym), None)
    if not item:
        return {'liq_1h': 0, 'long_liq_1h': 0, 'short_liq_1h': 0,
                'liq_bias': 'NEUTRAL', 'liq_note': f'{sym}无清算数据'}

    liq_1h  = item.get('liquidation_usd_1h', 0)
    long_1h = item.get('long_liquidation_usd_1h', 0)
    short_1h= item.get('short_liquidation_usd_1h', 0)

    # 清算偏向（谁被清算更多→反向受益）
    if liq_1h > 0:
        long_pct = long_1h / liq_1h
        if long_pct > 0.7:
            bias = 'LONG_SQUEEZE'    # 多头被大量清算→空头有利
        elif long_pct < 0.3:
            bias = 'SHORT_SQUEEZE'   # 空头被大量清算→多头有利
        else:
            bias = 'NEUTRAL'
    else:
        bias = 'NEUTRAL'

    return {
        'liq_1h':    round(liq_1h, 0),
        'long_liq_1h':  round(long_1h, 0),
        'short_liq_1h': round(short_1h, 0),
        'liq_bias':  bias,
        'liq_note':  f'1H清算${liq_1h/1e6:.2f}M 多:{long_pct*100:.0f}%' if liq_1h > 0 else '暂无清算',
    }


def get_oi_exchange_info(symbol: str) -> dict:
    """
    CoinGlass OI跨所数据：全市场OI总量 + 24H变化
    OI_chg_24h > +3%：机构大幅建仓（趋势信号）
    OI_chg_24h < -3%：机构去杠杆（风险信号）
    """
    data = _cg('/api/option/info', f'symbol={symbol.replace("USDT","")}&exchange=All')
    if not data:
        return {'oi_total': 0, 'oi_chg_24h': 0, 'oi_signal': 'NEUTRAL'}

    # data[0] = All exchanges aggregated
    item = data[0] if isinstance(data, list) and data else {}
    oi_usd   = item.get('open_interest_usd', 0)
    oi_chg   = item.get('open_interest_change_24h', 0)

    if oi_chg > 3:
        signal = 'BUILDING'      # 机构建仓
    elif oi_chg < -3:
        signal = 'DELEVERAGING'  # 去杠杆
    else:
        signal = 'STABLE'

    return {
        'oi_total': round(oi_usd / 1e9, 3),  # 十亿USD
        'oi_chg_24h': round(oi_chg, 2),
        'oi_signal': signal,
    }


# ═══════════════════════════════════════════════════════════════
# 五、综合链上评分（0~30分）
# ═══════════════════════════════════════════════════════════════

def onchain_score(symbol: str, signal_dir: str) -> dict:
    """
    主接口：返回综合链上评分 + 各维度详情

    评分逻辑（总分30）：
      OI配合度     0~10  OI与价格同向建仓=高分
      多空比反转   0~10  极端拥挤反向=高分
      主动成交方向 0~5   大单与信号同向=高分
      资金费率适中 0~5   费率极端=减分，适中=加分
    """
    is_long = signal_dir in ('LONG', '做多')

    # 4个数据源并发拉取（原串行→并发，节省~600ms）
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=4) as ex:
        f_oi   = ex.submit(get_oi_change, symbol)
        f_ls   = ex.submit(get_long_short, symbol)
        f_tkr  = ex.submit(get_taker_ratio, symbol)
        f_fund = ex.submit(get_funding_trend, symbol)
        oi   = f_oi.result()
        ls   = f_ls.result()
        tkr  = f_tkr.result()
        fund = f_fund.result()

    score = 0
    notes = []

    # ── OI配合度（0~10）───────────────────────────────────────
    # 做多：OI上升+价格上涨=机构建多仓=加分
    # 做空：OI上升+价格下跌=机构建空仓=加分
    oi_chg = oi['oi_chg_5']
    if oi['oi_trend'] == 'RISING' and abs(oi_chg) > 2:
        s_oi = 10
        notes.append(f'OI急涨{oi_chg:+.1f}%→机构建仓')
    elif oi['oi_trend'] == 'RISING':
        s_oi = 7
        notes.append(f'OI温和上升{oi_chg:+.1f}%')
    elif oi['oi_trend'] == 'FALLING':
        s_oi = 3
        notes.append(f'OI下降{oi_chg:+.1f}%→减仓')
    else:
        s_oi = 5
        notes.append('OI稳定')
    score += s_oi

    # ── 多空比反转（0~10）────────────────────────────────────
    extreme = ls['ls_extreme']
    if is_long and extreme == 'EXTREME_SHORT':
        s_ls = 10
        notes.append(f'空头拥挤{ls["long_pct"]:.0f}%多→反转做多')
    elif not is_long and extreme == 'EXTREME_LONG':
        s_ls = 10
        notes.append(f'多头拥挤{ls["long_pct"]:.0f}%多→反转做空')
    elif extreme == 'NEUTRAL':
        s_ls = 5
        notes.append(f'多空中性{ls["long_pct"]:.0f}%多')
    else:
        s_ls = 2
        notes.append(f'多空拥挤方向不利{ls["long_pct"]:.0f}%多')
    score += s_ls

    # ── 主动成交方向（0~5）───────────────────────────────────
    bias = tkr['taker_bias']
    if (is_long and bias == 'BUY_DOMINANT') or (not is_long and bias == 'SELL_DOMINANT'):
        s_tkr = 5
        notes.append(f'大单{bias}与信号同向')
    elif bias == 'BALANCED':
        s_tkr = 3
        notes.append('大单均衡')
    else:
        s_tkr = 1
        notes.append(f'大单{bias}逆向')
    score += s_tkr

    # ── 资金费率适中（0~5）───────────────────────────────────
    ft = fund['funding_trend']
    if ft == 'NEUTRAL':
        s_fund = 5
        notes.append('费率中性✅')
    elif (is_long and ft == 'BEAR_MILD') or (not is_long and ft == 'BULL_MILD'):
        s_fund = 4
        notes.append(f'费率{ft}有利')
    elif (is_long and ft == 'BULL_MILD') or (not is_long and ft == 'BEAR_MILD'):
        s_fund = 3
        notes.append(f'费率{ft}温和')
    else:
        # 极端费率（BULL_HEAT/BEAR_HEAT）
        s_fund = 1
        notes.append(f'费率极端{ft}⚠️')
    score += s_fund

    # ── CoinGlass 机构清算信号（额外加成 0~8）──────────────────
    liq_data = get_liquidation_data(symbol)
    oi_ex    = get_oi_exchange_info(symbol)
    s_cg = 0
    cg_notes = []

    # 清算挤压方向与信号一致
    lb = liq_data['liq_bias']
    if (is_long  and lb == 'SHORT_SQUEEZE') or        (not is_long and lb == 'LONG_SQUEEZE'):
        s_cg += 5
        cg_notes.append(liq_data['liq_note'])
    elif lb != 'NEUTRAL':
        cg_notes.append(liq_data['liq_note'])

    # OI机构建仓方向
    oi_sig = oi_ex['oi_signal']
    if oi_sig == 'BUILDING':
        s_cg += 3
        cg_notes.append(f"OI全市场+{oi_ex['oi_chg_24h']}% 机构建仓")
    elif oi_sig == 'DELEVERAGING':
        cg_notes.append(f"OI全市场{oi_ex['oi_chg_24h']}% 去杠杆⚠️")

    if cg_notes:
        notes.extend(cg_notes)

    score += s_cg

    return {
        'score':         min(score, 30),
        'max':           30,
        'breakdown': {
            'oi_score':   s_oi,
            'ls_score':   s_ls,
            'taker_score': s_tkr,
            'fund_score': s_fund,
            'cg_score':   s_cg,
        },
        'notes':        notes,
        'raw': {
            'oi':          oi,
            'ls':          ls,
            'taker':       tkr,
            'funding':     fund,
            'liquidation': liq_data,
            'oi_exchange': oi_ex,
        }
    }


# ── 独立运行测试 ──────────────────────────────────────────────
if __name__ == '__main__':
    import sys
    sym = sys.argv[1] if len(sys.argv) > 1 else 'BTCUSDT'
    dr  = sys.argv[2] if len(sys.argv) > 2 else 'SHORT'
    print(f"\n=== 链上引擎 · {sym} {dr} ===")
    r = onchain_score(sym, dr)
    print(f"链上总分: {r['score']}/30")
    for k, v in r['breakdown'].items():
        bar = '█' * v
        print(f"  {k:15} {v:2d}  {bar}")
    print(f"分析: {' | '.join(r['notes'])}")
    raw = r['raw']
    print(f"\nOI变化5H: {raw['oi']['oi_chg_5']:+.2f}%  趋势:{raw['oi']['oi_trend']}")
    print(f"多空比: {raw['ls']['long_pct']:.1f}%多  {raw['ls']['ls_extreme']}")
    print(f"主动买卖: 买{raw['taker']['buy_ratio']:.1f}% 卖{raw['taker']['sell_ratio']:.1f}%  {raw['taker']['taker_bias']}")
    print(f"资金费率: {raw['funding']['funding_now']:+.5f}%  {raw['funding']['funding_trend']}")


# ══ 合并自 brahma_brain/smart_money_engine.py ══
"""
smart_money_engine.py — 聪明钱流向分析引擎
设计院 2026-07-01 · Glassnode盲区替代方案

替代能力：
  Glassnode链上巨鲸流向 ≈ 80%（用Binance大户持仓比+持仓趋势代理）
  CryptoQuant矿工流向 ≈ 60%（用OI结构+大户背离间接推断）

核心逻辑：
  1. 大户账户多空比(topLongShortAccountRatio) = 大户方向偏好
  2. 大户持仓多空比(topLongShortPositionRatio) = 大户实际仓位
  3. 散户账户多空比(globalLongShortAccountRatio) = 散户行为
  4. 大户-散户背离 = 聪明钱方向（背离大→大户与散户反向）
  5. 持仓趋势 = 大户是在加仓还是减仓

数据源：Binance fapi futures/data（免费公开，无需API Key）
"""


# ╔══ INTERFACE CONTRACT ═══════════════════════════════════════════╗
# ║ Interface : get_smart_money_signal(symbol)->dict
# ║ Output    : {signal, score_adj, big_pos_long, whale_retail_gap, confidence}
# ║ Call Freq : 随brahma_core评分，2分钟缓存
# ║ Deps      : requests(fapi futures/data)
# ╚════════════════════════════════════════════════════════════════╝
try:
    from brahma_brain.brahma_bus import _SESS as _HTTP, get_price as _bus_price  # [HTTP Session共享 2026-08-02 设计院自主]
    from brahma_brain.data_cache import get_long_short_ratio as _dc_lsr
except ImportError:
    try:
        from brahma_bus import _SESS as _HTTP, get_price as _bus_price
        from data_cache import get_long_short_ratio as _dc_lsr
    except ImportError:
        import requests
        _HTTP = requests
        _bus_price = None
        _dc_lsr = None
import time
from typing import Optional

_CACHE: dict = {}
_CACHE_TTL = 120  # 2分钟


def _get(url: str, timeout: int = 8) -> Optional[list]:
    now = time.time()
    if url in _CACHE and now - _CACHE[url]['ts'] < _CACHE_TTL:
        return _CACHE[url]['data']
    try:
        r = _HTTP.get(url, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        _CACHE[url] = {'data': data, 'ts': now}
        return data
    except Exception:
        return None


def get_smart_money_signal(symbol: str = 'BTCUSDT') -> dict:
    """
    聪明钱流向综合分析

    返回：
      big_acct_long     : 大户账户多仓比（0~1）
      big_pos_long      : 大户持仓多仓比（0~1）
      retail_long       : 散户多仓比（0~1）
      whale_retail_gap  : 大户-散户多仓背离（正=大户比散户更多）
      pos_trend_5h      : 大户持仓5小时趋势（正=在加多仓）
      signal            : SMART_MONEY_SHORT / SMART_MONEY_LONG / DIVERGE_SHORT / NEUTRAL
      score_adj         : 评分调整（做空视角，负=大户看多=不利做空）
      note              : 描述
      confidence        : 数据置信度 0~1
    """
    default = {
        'big_acct_long': 0.5, 'big_pos_long': 0.5, 'retail_long': 0.5,
        'whale_retail_gap': 0.0, 'pos_trend_5h': 0.0,
        'signal': 'NEUTRAL', 'score_adj': 0, 'note': 'N/A', 'confidence': 0.0
    }

    cache_key = f'smart_{symbol}'
    now = time.time()
    if cache_key in _CACHE and now - _CACHE[cache_key]['ts'] < _CACHE_TTL:
        return _CACHE[cache_key]['data']

    try:
        acc_url = f'https://fapi.binance.com/futures/data/topLongShortAccountRatio?symbol={symbol}&period=1h&limit=6'
        pos_url = f'https://fapi.binance.com/futures/data/topLongShortPositionRatio?symbol={symbol}&period=1h&limit=6'
        ret_url = f'https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol={symbol}&period=1h&limit=6'

        acc_data = _get(acc_url)
        pos_data = _get(pos_url)
        ret_data = _get(ret_url)

        if not acc_data or not pos_data or not ret_data:
            default['note'] = '数据拉取失败'
            return default

        # 最新值
        ba = float(acc_data[0]['longAccount'])   # 大户账户多仓比
        bp = float(pos_data[0]['longAccount'])   # 大户持仓多仓比
        rr = float(ret_data[0]['longAccount'])   # 散户多仓比

        # 背离指标
        whale_gap = round(ba - rr, 4)   # 正=大户比散户更多
        pos_gap   = round(ba - bp, 4)   # 大户账户多 但持仓少 = 大户实际轻仓（口是心非）

        # 持仓趋势（5H变化）
        n = min(len(acc_data), len(pos_data), 6)
        pos_trend = float(pos_data[0]['longAccount']) - float(pos_data[n-1]['longAccount'])

        # 信号逻辑
        score_adj = 0
        notes = []

        # 核心判断1：大户持仓偏空（<0.45）→ 聪明钱做空
        if bp < 0.45:
            score_adj += 4
            notes.append(f'大户持仓偏空({bp:.2%}) 聪明钱看空 +4')
        elif bp < 0.50:
            score_adj += 2
            notes.append(f'大户持仓中性偏空({bp:.2%}) +2')
        elif bp > 0.60:
            score_adj -= 3
            notes.append(f'大户持仓偏多({bp:.2%}) 聪明钱看多 -3')

        # 核心判断2：大户-散户背离
        if whale_gap < -0.05:
            # 大户比散户更空 = 强烈看空信号
            score_adj += 3
            notes.append(f'大户比散户更空(差{whale_gap:.3f}) 背离做空 +3')
        elif whale_gap > 0.08:
            # 大户比散户更多 = 聪明钱反向做多，做空风险
            score_adj -= 2
            notes.append(f'大户比散户更多(差{whale_gap:.3f}) 注意反转 -2')

        # 核心判断3：持仓趋势（过去5H大户在加什么仓）
        if pos_trend < -0.02:
            # 大户在减多仓/加空仓
            score_adj += 2
            notes.append(f'大户5H减多仓({pos_trend:+.3f}) 趋势做空 +2')
        elif pos_trend > 0.03:
            score_adj -= 1
            notes.append(f'大户5H加多仓({pos_trend:+.3f}) 逆势预警 -1')

        # 综合信号
        if score_adj >= 5:
            signal = 'SMART_MONEY_SHORT'
        elif score_adj <= -3:
            signal = 'SMART_MONEY_LONG'
        elif whale_gap < -0.03 and bp < 0.50:
            signal = 'DIVERGE_SHORT'
        else:
            signal = 'NEUTRAL'

        result = {
            'big_acct_long':    round(ba, 4),
            'big_pos_long':     round(bp, 4),
            'retail_long':      round(rr, 4),
            'whale_retail_gap': round(whale_gap, 4),
            'pos_trend_5h':     round(pos_trend, 4),
            'signal':           signal,
            'score_adj':        min(max(score_adj, -5), 8),
            'note':             ' | '.join(notes) if notes else f'大户持仓={bp:.2%} 散户={rr:.2%}',
            'confidence':       0.75 if (acc_data and pos_data and ret_data) else 0.3,
        }

        _CACHE[cache_key] = {'data': result, 'ts': now}
        return result

    except Exception as e:
        default['note'] = f'error: {str(e)[:40]}'
        return default


if __name__ == '__main__':
    import json
    alerts = []
    for sym in ['BTCUSDT', 'ETHUSDT']:
        r = get_smart_money_signal(sym)
        sig = r.get('signal', 'NEUTRAL')
        score_adj = r.get('score_adj', 0)
        # 只有明确背离才算ALERT（非中性且分值有意义）
        if sig != 'NEUTRAL' and abs(score_adj) >= 10:
            alerts.append(f'[ALERT] {sym} 大户背离={sig} score_adj={score_adj:+d} {r.get("note","")}')
    if alerts:
        for a in alerts:
            print(a)
    else:
        pass  # [静默]

# ══ 合并自 brahma_brain/whale_engine.py ══
# ponytail: whale_engine 450行，独立计算引擎，功能内聚，拆分条件: 单引擎>3000行且有完整测试
"""

# STATUS: ACTIVE
# 大户引擎，smart_money辅助
# LAST_REVIEW: 2026-07-01 | 属于辅助计算层，修改前确认调用链
# ─────────────────────────────────────────────────────────────
whale_engine.py · 链上巨鲸 & 交易所资金流引擎
brahma_brain · P2

数据源（免费公开）:
  1. CoinGlass  - 交易所净流入/净流出（BTC/ETH）
  2. Binance    - 大额成交聚合（aggTrades 筛选）
  3. CoinGecko  - 链上持仓变化代理
  4. Whale Alert 公开 RSS（无需API Key）
  5. [P2 2026-05-22] Binance FAPI - 衍生品聪明钱（资金费率+多空比+OI）

输出:
  exchange_flow: 净流入(负=流出=看多) / 净流出(正=流入=看空)
  whale_buys / whale_sells: 近1H大单方向
  smart_money_signal: 综合判断
  score: 0~20分（P2升级: +5分衍生品维度）
"""

import json, urllib.request, time

# ── brahma_bus 总线接入（设计院 2026-06-29）──
try:
    from brahma_brain.brahma_bus import bus as _brahma_bus
except Exception:
    _brahma_bus = None

def _bus_price(symbol: str) -> float:
    """[2026-08-28 精简] 委托brahma_bus.get_price() — SSOT，支持OFFLINE_MODE"""
    from brahma_bus import get_price as _gp
    return _gp(symbol)



CG_BASE = 'https://open-api.coinglass.com/public/v2'
FAPI    = 'https://fapi.binance.com'
CGECKO  = 'https://api.coingecko.com/api/v3'

_CACHE = {}
_CACHE_TTL = 300  # 5分钟缓存

def _get(url: str, timeout: int = 8) -> dict | list | None:
    now = time.time()
    if url in _CACHE and now - _CACHE[url]['ts'] < _CACHE_TTL:
        return _CACHE[url]['data']
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0',
            'Accept': 'application/json',
        })
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
            _CACHE[url] = {'data': data, 'ts': now}
            return data
    except Exception:
        return None

# ═══════════════════════════════════════════════════════════════
# 1. 交易所净流入/流出（CoinGlass）
# ═══════════════════════════════════════════════════════════════

def get_exchange_flow(symbol: str) -> dict:
    """
    获取主要交易所 ETH/BTC 净流入流出
    净流出(负值) = 提币离场 = 看多信号（减少抛压）
    净流入(正值) = 入金备售 = 看空信号（增加抛压）
    """
    sym = symbol.replace('USDT', '').upper()
    url = f'{CG_BASE}/exchange/net-inflow?symbol={sym}&intervalType=h1'
    data = _get(url)

    if not data or not isinstance(data, dict):
        # 降级：用 Binance 大额转账代理
        return _fallback_exchange_flow(symbol)

    try:
        d = data.get('data', {})
        # CoinGlass 格式: allExchange.inflow / outflow
        all_ex = d.get('allExchange', {})
        inflow  = float(all_ex.get('inflow', 0) or 0)
        outflow = float(all_ex.get('outflow', 0) or 0)
        net = inflow - outflow  # 正=净流入(看空), 负=净流出(看多)
        notes = []
        if net < -1e6:
            notes.append(f'链上净流出 ${abs(net)/1e6:.1f}M → 减少抛压 看多')
        elif net > 1e6:
            notes.append(f'链上净流入 ${net/1e6:.1f}M → 增加抛压 看空')
        return {
            'inflow':  round(inflow / 1e6, 2),
            'outflow': round(outflow / 1e6, 2),
            'net':     round(net / 1e6, 2),
            'notes':   notes,
        }
    except Exception:
        return _fallback_exchange_flow(symbol)


def _fallback_exchange_flow(symbol: str) -> dict:
    """降级：用 Binance 大额 aggTrades 作为代理"""
    url = f'{FAPI}/fapi/v1/aggTrades?symbol={symbol}&limit=1000'
    trades = _get(url)
    if not trades:
        return {'inflow': 0, 'outflow': 0, 'net': 0, 'notes': ['数据不可用']}

    # 大额成交（>10万美元）
    price_url = f'{FAPI}/fapi/v1/ticker/price?symbol={symbol}'
    p_data = _get(price_url)
    price = float(p_data['price']) if p_data else 2000

    big_buy = big_sell = 0
    for t in trades:
        qty = float(t['q']) * price
        if qty >= 100000:  # 10万美元以上
            if not t['m']:  # 买方主动
                big_buy += qty
            else:
                big_sell += qty

    net = big_buy - big_sell
    notes = []
    if big_buy > big_sell * 1.5:
        notes.append(f'大单净买入 ${(big_buy-big_sell)/1e6:.2f}M → 机构积累')
    elif big_sell > big_buy * 1.5:
        notes.append(f'大单净卖出 ${(big_sell-big_buy)/1e6:.2f}M → 机构减仓')

    return {
        'inflow':  round(big_sell / 1e6, 3),
        'outflow': round(big_buy / 1e6, 3),
        'net':     round(-net / 1e6, 3),  # 负=净买入(看多)
        'notes':   notes,
        'source':  'aggTrades_proxy',
    }

# ═══════════════════════════════════════════════════════════════
# 2. 巨鲸地址动向（用 Binance 大额成交 + Taker 综合判断）
# ═══════════════════════════════════════════════════════════════

def get_whale_activity(symbol: str) -> dict:
    """
    巨鲸活动分析
    - 近5分钟大额聚合成交（>50万美元）
    - Taker 方向 vs 小单方向分化
    """
    # 最近500笔聚合成交
    url = f'{FAPI}/fapi/v1/aggTrades?symbol={symbol}&limit=500'
    trades = _get(url)

    price_url = f'{FAPI}/fapi/v1/ticker/price?symbol={symbol}'
    p_data = _get(price_url)
    price = float(p_data['price']) if p_data else 2000

    if not trades:
        return {'whale_dir': 'NEUTRAL', 'big_buy': 0, 'big_sell': 0, 'notes': []}

    SMALL_THRESHOLD = 10000   # <1万美元 = 散户
    BIG_THRESHOLD   = 500000  # >50万美元 = 巨鲸

    small_buy = small_sell = big_buy = big_sell = 0

    for t in trades:
        qty_usd = float(t['q']) * price
        is_buy  = not t['m']
        if qty_usd < SMALL_THRESHOLD:
            if is_buy: small_buy += qty_usd
            else:      small_sell += qty_usd
        elif qty_usd >= BIG_THRESHOLD:
            if is_buy: big_buy += qty_usd
            else:      big_sell += qty_usd

    whale_net = big_buy - big_sell
    retail_net = small_buy - small_sell

    # 巨鲸 vs 散户方向相反 = 经典信号
    diverge = (whale_net > 0 and retail_net < 0) or (whale_net < 0 and retail_net > 0)

    if big_buy + big_sell < 1000:
        whale_dir = 'NEUTRAL'
    elif whale_net > 0:
        whale_dir = 'BUY'
    else:
        whale_dir = 'SELL'

    notes = []
    if diverge and abs(whale_net) > 100000:
        if whale_dir == 'BUY':
            notes.append(f'巨鲸净买 ${whale_net/1e6:.2f}M vs 散户卖 → 机构吸筹')
        else:
            notes.append(f'巨鲸净卖 ${abs(whale_net)/1e6:.2f}M vs 散户买 → 机构出货')
    elif whale_dir == 'BUY' and big_buy > 500000:
        notes.append(f'巨鲸积极买入 ${big_buy/1e6:.2f}M')
    elif whale_dir == 'SELL' and big_sell > 500000:
        notes.append(f'巨鲸积极卖出 ${big_sell/1e6:.2f}M')

    return {
        'whale_dir':   whale_dir,
        'big_buy':     round(big_buy / 1e6, 3),
        'big_sell':    round(big_sell / 1e6, 3),
        'whale_net':   round(whale_net / 1e6, 3),
        'retail_net':  round(retail_net / 1e6, 3),
        'diverge':     diverge,
        'notes':       notes,
    }

# ═══════════════════════════════════════════════════════════════
# 3. 综合鲸鱼评分
# ═══════════════════════════════════════════════════════════════

def whale_score(symbol: str, signal_dir: str) -> dict:
    """综合链上鲸鱼信号评分 → 0~15分"""
    score = 0
    notes = []
    breakdown = {}

    # 交易所流向
    try:
        flow = get_exchange_flow(symbol)
        net  = flow.get('net', 0)
        flow_s = 0
        if signal_dir == 'SHORT' and net > 0.5:    # 净流入=看空
            flow_s = 5; notes += flow['notes']
        elif signal_dir == 'LONG' and net < -0.5:   # 净流出=看多
            flow_s = 5; notes += flow['notes']
        elif signal_dir == 'SHORT' and net > 0.1:
            flow_s = 2
        elif signal_dir == 'LONG' and net < -0.1:
            flow_s = 2
        breakdown['exchange_flow'] = flow_s
        score += flow_s
    except Exception:
        breakdown['exchange_flow'] = 0
        flow = {}

    # 巨鲸方向
    try:
        whale = get_whale_activity(symbol)
        whale_dir = whale.get('whale_dir', 'NEUTRAL')
        whale_s = 0
        if signal_dir == 'SHORT' and whale_dir == 'SELL':
            whale_s = 6
            if whale.get('diverge'):
                whale_s = 8  # 巨鲸出货+散户接盘 = 最强信号
            notes += whale['notes']
        elif signal_dir == 'LONG' and whale_dir == 'BUY':
            whale_s = 6
            if whale.get('diverge'):
                whale_s = 8
            notes += whale['notes']
        breakdown['whale_activity'] = whale_s
        score += whale_s
    except Exception:
        breakdown['whale_activity'] = 0
        whale = {}

    return {
        'score':     min(score, 15),
        'max':       15,
        'breakdown': breakdown,
        'notes':     notes,
        'flow':      flow,
        'whale':     whale,
    }


# ═══════════════════════════════════════════════════════════════
# 4. [P2 2026-05-22] 衍生品聪明钱信号（Binance FAPI 公开接口）
#    资金费率方向 + 多空比分化 + OI变化
# ═══════════════════════════════════════════════════════════════

def get_derivatives_smart_money(symbol: str, signal_dir: str) -> dict:
    """
    [P2] 衍生品聪明钱（真实数据源： Binance FAPI 公开接口）

    三个维度：
      1. 资金费率方向：负费率=多头付空头=市场偏多；正费率=空头付多头=市场偏空
      2. 多空比分化：多空比远面>2.5=多头凥热（小心働空）；<0.4=空头凥热（小心做多）
      3. OI 5分钟变化：裁减且方向匹配=平仓压力方向一致

    输出 score: 0~5分（叠加到 whale_score 的 0~15 上，总上限 0~20）
    """
    score = 0
    notes = []
    breakdown = {}

    # ── 维度1: 资金费率 ───────────────────────────────
    try:
        url_fr = f'{FAPI}/fapi/v1/fundingRate?symbol={symbol}&limit=3'
        fr_data = _get(url_fr)
        fr_s = 0
        fr_note = ''
        if fr_data and isinstance(fr_data, list) and len(fr_data) > 0:
            latest_fr = float(fr_data[-1].get('fundingRate', 0))
            # 负费率感=多头付空头=市场偏多，加空分；正费率=空头付多头=市场偏空，加多分
            if signal_dir == 'SHORT' and latest_fr > 0.0003:
                fr_s = 2
                fr_note = f'资金费率+{latest_fr*100:.4f}%（多头付费，凥热利空）'
            elif signal_dir == 'SHORT' and latest_fr > 0.0001:
                fr_s = 1
                fr_note = f'资金费率+{latest_fr*100:.4f}%（偶数偏多）'
            elif signal_dir == 'LONG' and latest_fr < -0.0003:
                fr_s = 2
                fr_note = f'资金费率{latest_fr*100:.4f}%（空头付费，凥热利多）'
            elif signal_dir == 'LONG' and latest_fr < -0.0001:
                fr_s = 1
                fr_note = f'资金费率{latest_fr*100:.4f}%（偶数偏空）'
            if fr_note:
                notes.append(fr_note)
        breakdown['funding_rate'] = fr_s
        score += fr_s
    except Exception:
        breakdown['funding_rate'] = 0

    # ── 维度2: 多空比 ────────────────────────────────
    try:
        url_lsr = f'{FAPI}/futures/data/globalLongShortAccountRatio?symbol={symbol}&period=5m&limit=3'
        lsr_data = _get(url_lsr)
        lsr_s = 0
        if lsr_data and isinstance(lsr_data, list) and len(lsr_data) > 0:
            lsr = float(lsr_data[-1].get('longShortRatio', 1.0))
            # 多空比远面时小心反向操作
            if signal_dir == 'SHORT' and lsr > 2.5:
                lsr_s = 2
                notes.append(f'多空比={lsr:.2f}（多头凥热，裁多香を小心）')
            elif signal_dir == 'SHORT' and lsr > 1.8:
                lsr_s = 1
            elif signal_dir == 'LONG' and lsr < 0.4:
                lsr_s = 2
                notes.append(f'多空比={lsr:.2f}（空头凥热，裁空香を小心）')
            elif signal_dir == 'LONG' and lsr < 0.7:
                lsr_s = 1
        breakdown['long_short_ratio'] = lsr_s
        score += lsr_s
    except Exception:
        breakdown['long_short_ratio'] = 0

    # ── 维度3: OI 5分钟变化 ─────────────────────────
    try:
        url_oi = f'{FAPI}/futures/data/openInterestHist?symbol={symbol}&period=5m&limit=6'
        oi_data = _get(url_oi)
        oi_s = 0
        if oi_data and isinstance(oi_data, list) and len(oi_data) >= 4:
            oi_new = float(oi_data[-1].get('sumOpenInterest', 0))
            oi_old = float(oi_data[-4].get('sumOpenInterest', 0))
            oi_chg = (oi_new - oi_old) / max(oi_old, 1e-9)
            # OI下降（裁减）时：方向匹配=平仓压力一致
            # OI上升（新开）：方向匹配=势头活跃
            if signal_dir == 'SHORT' and oi_chg < -0.005:
                oi_s = 1
                notes.append(f'OI轻微下降{oi_chg*100:.2f}%（多头平仓压力）')
            elif signal_dir == 'SHORT' and oi_chg > 0.005:
                # 空头新开，对空信号有利
                oi_s = 1
                notes.append(f'OI上升{oi_chg*100:.2f}%（空头新开，势头活跃）')
            elif signal_dir == 'LONG' and oi_chg < -0.005:
                oi_s = 1
                notes.append(f'OI下降{oi_chg*100:.2f}%（空头平仓压力）')
            elif signal_dir == 'LONG' and oi_chg > 0.005:
                oi_s = 1
                notes.append(f'OI上升{oi_chg*100:.2f}%（多头新开，势头活跃）')
        breakdown['oi_change'] = oi_s
        score += oi_s
    except Exception:
        breakdown['oi_change'] = 0

    return {
        'score':     min(score, 5),
        'max':       5,
        'breakdown': breakdown,
        'notes':     notes,
        'source':    'binance_fapi_realtime',
    }


def whale_score(symbol: str, signal_dir: str) -> dict:
    """综合鲸鱼+衍生品聪明钱信号评分 → 0~20分
    [P2 2026-05-22] 旧 0~15 升级至 0~20：+5分 binance_fapi 衍生品维度
    """
    score = 0
    notes = []
    breakdown = {}

    # 交易所流向
    try:
        flow = get_exchange_flow(symbol)
        net  = flow.get('net', 0)
        flow_s = 0
        if signal_dir == 'SHORT' and net > 0.5:    # 净流入=看空
            flow_s = 5; notes += flow['notes']
        elif signal_dir == 'LONG' and net < -0.5:   # 净流出=看多
            flow_s = 5; notes += flow['notes']
        elif signal_dir == 'SHORT' and net > 0.1:
            flow_s = 2
        elif signal_dir == 'LONG' and net < -0.1:
            flow_s = 2
        breakdown['exchange_flow'] = flow_s
        score += flow_s
    except Exception:
        breakdown['exchange_flow'] = 0
        flow = {}

    # 巨鲸方向
    try:
        whale = get_whale_activity(symbol)
        whale_dir = whale.get('whale_dir', 'NEUTRAL')
        whale_s = 0
        if signal_dir == 'SHORT' and whale_dir == 'SELL':
            whale_s = 6
            if whale.get('diverge'):
                whale_s = 8  # 巨鲸出货+散户接盘 = 最强信号
            notes += whale['notes']
        elif signal_dir == 'LONG' and whale_dir == 'BUY':
            whale_s = 6
            if whale.get('diverge'):
                whale_s = 8
            notes += whale['notes']
        breakdown['whale_activity'] = whale_s
        score += whale_s
    except Exception:
        breakdown['whale_activity'] = 0
        whale = {}

    # [P2] 衍生品聪明钱（资金费率+多空比+OI）— Binance FAPI 公开接口
    try:
        deriv = get_derivatives_smart_money(symbol, signal_dir)
        d_s = deriv.get('score', 0)
        notes += deriv.get('notes', [])
        breakdown['derivatives_sm'] = d_s
        score += d_s
    except Exception:
        breakdown['derivatives_sm'] = 0
        deriv = {}

    return {
        'score':     min(score, 20),   # P2升级: 上限 15 → 20
        'max':       20,
        'breakdown': breakdown,
        'notes':     notes,
        'flow':      flow,
        'whale':     whale,
        'derivatives': deriv,
        'source':    'whale_engine_v2_p2',
    }