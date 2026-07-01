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


# ═══════════════════════════════════════════════════════════════
# [s_macro_v2 2026-07-01] DXY实时 + BTC.D精准加权
# 设计院·四方共识落地：全要素宏观层升级
# ═══════════════════════════════════════════════════════════════

def get_dxy_realtime() -> dict:
    """
    DXY 美元指数实时（Yahoo Finance /v8，免费）
    返回：price, chg_1h_pct, chg_24h_pct, direction
    """
    default = {'price': 0.0, 'chg_1h_pct': 0.0, 'chg_24h_pct': 0.0, 'direction': 'NEUTRAL'}
    url = 'https://query1.finance.yahoo.com/v8/finance/chart/DX-Y.NYB?interval=1h&range=2d'
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())
        closes = data['chart']['result'][0]['indicators']['quote'][0]['close']
        closes = [x for x in closes if x is not None]
        if len(closes) < 2:
            return default
        now_p  = closes[-1]
        prev1h = closes[-2]
        prev24 = closes[-25] if len(closes) >= 25 else closes[0]
        chg1h  = round((now_p - prev1h) / prev1h * 100, 3)
        chg24  = round((now_p - prev24) / prev24 * 100, 3)
        direction = 'UP' if chg1h > 0.05 else ('DOWN' if chg1h < -0.05 else 'FLAT')
        return {'price': round(now_p, 2), 'chg_1h_pct': chg1h, 'chg_24h_pct': chg24, 'direction': direction}
    except Exception:
        return default


def get_nasdaq_realtime() -> dict:
    """
    纳指期货 NQ=F 实时（Yahoo Finance，免费）
    BTC与纳指相关系数≈0.7，宏观共振确认
    """
    default = {'price': 0.0, 'chg_1h_pct': 0.0, 'direction': 'NEUTRAL'}
    url = 'https://query1.finance.yahoo.com/v8/finance/chart/NQ=F?interval=1h&range=2d'
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())
        closes = data['chart']['result'][0]['indicators']['quote'][0]['close']
        closes = [x for x in closes if x is not None]
        if len(closes) < 2:
            return default
        chg = round((closes[-1] - closes[-2]) / closes[-2] * 100, 3)
        direction = 'UP' if chg > 0.1 else ('DOWN' if chg < -0.1 else 'FLAT')
        return {'price': round(closes[-1], 0), 'chg_1h_pct': chg, 'direction': direction}
    except Exception:
        return default


def macro_score_v2(symbol: str, signal_dir: str) -> dict:
    """
    全要素宏观评分 v2（在原 macro_score 基础上叠加）

    新增维度：
      DXY实时方向（Yahoo Finance）
      纳指期货方向（Yahoo Finance）
      BTC.D精准阈值加权（CoinGecko已有，精细化）

    返回：
      score_addon   : 新增宏观加分（叠加到原 macro_score 上）
      dxy           : DXY数据
      nasdaq        : 纳指数据
      notes         : 描述列表
    """
    is_short = (signal_dir == 'SHORT')
    is_btc   = ('BTC' in symbol.upper())
    score    = 0
    notes    = []

    # ── DXY 实时 ──
    dxy = get_dxy_realtime()
    if dxy['price'] > 0:
        if is_short and dxy['direction'] == 'UP':
            pts = 3 if abs(dxy['chg_1h_pct']) >= 0.15 else 2
            score += pts; notes.append(f'DXY={dxy["price"]:.2f}(+{dxy["chg_1h_pct"]:.2f}%) 美元走强→加密承压 +{pts}')
        elif is_short and dxy['direction'] == 'DOWN':
            score -= 1; notes.append(f'DXY={dxy["price"]:.2f}({dxy["chg_1h_pct"]:.2f}%) 美元走弱→做空逆风 -1')
        elif not is_short and dxy['direction'] == 'DOWN':
            pts = 2
            score += pts; notes.append(f'DXY={dxy["price"]:.2f}({dxy["chg_1h_pct"]:.2f}%) 美元走弱→加密利好 +{pts}')

    # ── 纳指期货 ──
    nq = get_nasdaq_realtime()
    if nq['price'] > 0:
        if is_short and nq['direction'] == 'DOWN':
            score += 2; notes.append(f'NQ={nq["price"]:.0f}({nq["chg_1h_pct"]:.2f}%) 纳指下跌→BTC共振 +2')
        elif is_short and nq['direction'] == 'UP':
            score -= 1; notes.append(f'NQ={nq["price"]:.0f}({nq["chg_1h_pct"]:.2f}%) 纳指上涨→做空逆风 -1')
        elif not is_short and nq['direction'] == 'UP':
            score += 2; notes.append(f'NQ={nq["price"]:.0f}({nq["chg_1h_pct"]:.2f}%) 纳指上涨→BTC利好 +2')

    # ── BTC.D 精准加权（山寨做空叠加）──
    if not is_btc and is_short:
        try:
            dom_data = get_btc_dominance()
            btc_d = dom_data.get('btc_dom', 0)
            if btc_d >= 56:
                score += 4; notes.append(f'BTC.D={btc_d:.1f}% 高位吸血→山寨更弱 +4')
            elif btc_d >= 53:
                score += 2; notes.append(f'BTC.D={btc_d:.1f}% 偏高→山寨承压 +2')
        except Exception:
            pass

    return {
        'score_addon': min(max(score, -3), 6),  # 限制范围 -3~+6
        'dxy':    dxy,
        'nasdaq': nq,
        'notes':  notes,
    }
