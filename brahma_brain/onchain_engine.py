#!/usr/bin/env python3
"""

# ── STATUS: AUXILIARY ──────────────────────────────────────────
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
