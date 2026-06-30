#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║  梵天大脑 · macro_engine.py  · P2d 宏观量化引擎                  ║
║  数据源：免费公开 API（无需KEY）                                  ║
║  覆盖：恐惧贪婪指数 / 比特币主导率 / 市场资金体量                  ║
║  评分贡献：宏观环境 0~10分                                        ║
╚══════════════════════════════════════════════════════════════════╝
"""
import urllib.request, json, time

_cache: dict = {}
_TTL = 300  # 5分钟

def _get(url: str, headers: dict = None):
    now = time.time()
    if url in _cache and now - _cache[url]['ts'] < _TTL:
        return _cache[url]['data']
    try:
        h = {'User-Agent': 'brahma/4.0'}
        if headers:
            h.update(headers)
        req = urllib.request.Request(url, headers=h)
        with urllib.request.urlopen(req, timeout=6) as r:
            data = json.loads(r.read())
            _cache[url] = {'ts': now, 'data': data}
            return data
    except Exception:
        return None


def get_fear_greed() -> dict:
    """Alternative.me 恐惧贪婪指数（免费）"""
    data = _get("https://api.alternative.me/fng/?limit=3&format=json")
    if not data or 'data' not in data:
        return {'value': 50, 'label': 'Neutral', 'trend': 'FLAT'}

    vals = [int(d['value']) for d in data['data'][:3]]
    current = vals[0]
    prev    = vals[-1]

    if current <= 20:
        label = 'Extreme Fear'
    elif current <= 40:
        label = 'Fear'
    elif current <= 60:
        label = 'Neutral'
    elif current <= 80:
        label = 'Greed'
    else:
        label = 'Extreme Greed'

    trend = 'RISING' if current > prev + 5 else ('FALLING' if current < prev - 5 else 'FLAT')

    return {'value': current, 'label': label, 'trend': trend, 'prev': prev}


def get_btc_dominance() -> dict:
    """
    BTC主导率（从 CoinGecko 公开 /global 端点）
    BTC主导率高→资金集中BTC，山寨币弱
    BTC主导率低→资金外溢山寨，高弹性交易机会
    """
    data = _get("https://api.coingecko.com/api/v3/global")
    if not data:
        return {'btc_dom': 50.0, 'signal': 'NEUTRAL'}

    btc_dom = data.get('data', {}).get('market_cap_percentage', {}).get('btc', 50.0)

    if btc_dom > 58:
        signal = 'BTC_DOMINANT'   # 避险模式，山寨弱
    elif btc_dom < 45:
        signal = 'ALTCOIN_SEASON'  # 山寨季，高β品种机会多
    else:
        signal = 'BALANCED'

    return {'btc_dom': round(btc_dom, 2), 'signal': signal}


def macro_score(symbol: str, signal_dir: str, fg_data: dict = None) -> dict:
    """
    宏观评分 0~10分
    恐惧贪婪指数配合信号方向 + BTC主导率环境

    哲学：极度恐惧 + 做多 = 最佳宏观环境（机构抄底时机）
          极度贪婪 + 做空 = 最佳宏观环境（顶部做空时机）
    """
    is_long = signal_dir in ('LONG', '做多')
    is_btc  = 'BTC' in symbol.upper()

    fg  = fg_data if fg_data else get_fear_greed()
    dom = get_btc_dominance()

    fv = fg['value']
    s_fg = 0
    fg_note = ''

    if is_long:
        if fv <= 20:   s_fg = 10; fg_note = f'极度恐惧({fv})→底部抄多✅'
        elif fv <= 35: s_fg = 7;  fg_note = f'恐惧({fv})→做多友好'
        elif fv <= 50: s_fg = 5;  fg_note = f'中性({fv})'
        elif fv <= 70: s_fg = 3;  fg_note = f'贪婪({fv})→上方有阻力'
        else:          s_fg = 1;  fg_note = f'极度贪婪({fv})→顶部风险高'
    else:
        if fv >= 80:   s_fg = 10; fg_note = f'极度贪婪({fv})→顶部做空✅'
        elif fv >= 65: s_fg = 7;  fg_note = f'贪婪({fv})→做空友好'
        elif fv >= 50: s_fg = 5;  fg_note = f'中性({fv})'
        elif fv >= 35: s_fg = 3;  fg_note = f'恐惧({fv})→下方有支撑'
        else:          s_fg = 1;  fg_note = f'极度恐惧({fv})→反弹风险高'

    # BTC主导率调整（影响山寨机会）
    dom_bonus = 0
    dom_note  = ''
    if not is_btc:
        ds = dom['signal']
        if is_long and ds == 'ALTCOIN_SEASON':
            dom_bonus = 2; dom_note = f'山寨季({dom["btc_dom"]:.0f}%BTC)→山寨强'
        elif not is_long and ds == 'BTC_DOMINANT':
            dom_bonus = 1; dom_note = f'BTC主导({dom["btc_dom"]:.0f}%)→山寨弱'

    total = min(s_fg + dom_bonus, 10)
    notes = [fg_note]
    if dom_note:
        notes.append(dom_note)

    return {
        'score':  total,
        'notes':  notes,
        'raw': {
            'fear_greed':    fg,
            'btc_dominance': dom,
        }
    }


if __name__ == '__main__':
    import sys
    sym = sys.argv[1] if len(sys.argv) > 1 else 'BTCUSDT'
    dr  = sys.argv[2] if len(sys.argv) > 2 else 'SHORT'
    r = macro_score(sym, dr)
    print(f"\n=== 宏观引擎 · {sym} {dr} ===")
    print(f"宏观总分: {r['score']}/10")
    for n in r['notes']:
        print(f"  {n}")
    fg = r['raw']['fear_greed']
    dom = r['raw']['btc_dominance']
    print(f"恐惧贪婪: {fg['value']} {fg['label']}  趋势:{fg['trend']}")
    print(f"BTC主导率: {dom['btc_dom']}%  {dom['signal']}")
