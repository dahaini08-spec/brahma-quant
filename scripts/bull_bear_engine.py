#!/usr/bin/env python3
"""
bull_bear_engine.py — P2 Bull/Bear双视角评分引擎（TradingAgents启发）
在brahma_brain评分基础上，同时计算做多/做空两个视角的独立分数
差值 = 方向置信度，消灭单方向偏见

输出：
  bull_score: 做多视角综合分（0~100）
  bear_score: 做空视角综合分（0~100）
  conviction:  差值，正=偏多，负=偏空
  bias_label:  'STRONG_BULL'/'LEAN_BULL'/'NEUTRAL'/'LEAN_BEAR'/'STRONG_BEAR'
  debate_summary: 辩论摘要文本
"""
import json
import urllib.request
from typing import Tuple

def _fetch(url: str) -> dict:
    try:
        return json.loads(urllib.request.urlopen(url, timeout=6).read())
    except Exception:
        return {}

def _klines(sym: str, interval: str, limit: int = 50):
    data = _fetch(f'https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval={interval}&limit={limit}')
    if not isinstance(data, list): return [], [], [], []
    c = [float(k[4]) for k in data]
    h = [float(k[2]) for k in data]
    l = [float(k[3]) for k in data]
    v = [float(k[5]) for k in data]
    return c, h, l, v

def _rsi(c, n=14):
    # [FIX-RSI-WILDER 2026-06-14] 统一Wilder EMA算法，与market_state.rsi对齐
    if len(c) < n + 1: return 50.0
    g = [max(c[i]-c[i-1], 0) for i in range(1, len(c))]
    lo = [max(c[i-1]-c[i], 0) for i in range(1, len(c))]
    ag = sum(g[:n])/n; al = sum(lo[:n])/n
    for i in range(n, len(g)):
        ag = (ag*(n-1)+g[i])/n; al = (al*(n-1)+lo[i])/n
    return round(100-100/(1+ag/max(al,1e-9)), 1) if al else 100.0

def _bb(c, n=20):
    if len(c) < n: return c[-1], c[-1], c[-1]
    m = sum(c[-n:])/n; s = (sum((x-m)**2 for x in c[-n:])/n)**0.5
    return m+2*s, m, m-2*s

def _ema(c, n):
    e = c[0]; k = 2/(n+1)
    for x in c[1:]: e = x*k + e*(1-k)
    return e

def _macd(c):
    if len(c) < 26: return 0, 0
    return _ema(c, 12) - _ema(c, 26), _ema(c, 9)

def debate(symbol: str, price: float = 0) -> dict:
    """
    对某个标的进行Bull/Bear双视角辩论
    返回评分、置信度、辩论摘要
    """
    sym = symbol.upper()
    if not sym.endswith('USDT'):
        sym += 'USDT'

    if not price:
        try:
            t = _fetch(f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={sym}')
            price = float(t.get('price', 0))
        except Exception:
            pass

    c1, h1, l1, v1 = _klines(sym, '1h', 48)
    c4, h4, l4, v4 = _klines(sym, '4h', 20)
    c1d, _, _, _ = _klines(sym, '1d', 10)
    pr = _fetch(f'https://fapi.binance.com/fapi/v1/premiumIndex?symbol={sym}')
    fr = float(pr.get('lastFundingRate', 0)) * 100

    if not c1 or not price:
        return {'error': '数据获取失败', 'bull_score': 50, 'bear_score': 50}

    rsi_1h = _rsi(c1); rsi_4h = _rsi(c4); rsi_1d = _rsi(c1d)
    bb1 = _bb(c1); bb4 = _bb(c4)
    macd_dif, _ = _macd(c1)
    macd4_dif, _ = _macd(c4)
    vol_avg = sum(v1[:-3])/max(len(v1[:-3]),1)
    vol_ratio = v1[-1]/vol_avg if vol_avg else 1

    # 近期高低点结构
    hi8 = max(h1[-8:]); lo8 = min(l1[-8:])
    hi_trend = h1[-1] > max(h1[-5:-1]) if len(h1) > 5 else False  # HH
    lo_trend = l1[-1] > min(l1[-5:-1]) if len(l1) > 5 else False  # HL (bullish)

    bull_args = []
    bear_args = []
    bull = 0.0
    bear = 0.0

    # ── 1. RSI ────────────────────────────────────────────────
    if rsi_4h < 25:
        bull += 20; bull_args.append(f'4H RSI={rsi_4h}极端超卖(+20)')
        bear += 2;  bear_args.append(f'4H RSI={rsi_4h}但未确认反转(+2)')
    elif rsi_4h < 35:
        bull += 12; bull_args.append(f'4H RSI={rsi_4h}超卖区(+12)')
        bear += 5
    elif rsi_4h > 75:
        bear += 20; bear_args.append(f'4H RSI={rsi_4h}过热(+20)')
        bull += 2;  bull_args.append(f'4H RSI={rsi_4h}但动能强(+2)')
    elif rsi_4h > 65:
        bear += 12; bear_args.append(f'4H RSI={rsi_4h}偏热(+12)')
        bull += 5

    if rsi_1h < 30:
        bull += 8; bull_args.append(f'1H RSI={rsi_1h}短线超卖(+8)')
    elif rsi_1h > 70:
        bear += 8; bear_args.append(f'1H RSI={rsi_1h}短线过热(+8)')

    # ── 2. 布林带位置 ─────────────────────────────────────────
    bb1_pos = (price - bb1[2]) / (bb1[0] - bb1[2]) if bb1[0] != bb1[2] else 0.5
    if bb1_pos < 0.2:
        bull += 10; bull_args.append(f'1H布林下轨附近bb_pos={bb1_pos:.2f}(+10)')
    elif bb1_pos > 0.8:
        bear += 10; bear_args.append(f'1H布林上轨附近bb_pos={bb1_pos:.2f}(+10)')

    # 日线布林
    bb1d = _bb(c1d)
    if bb1d[2] < price < bb1d[2] * 1.02:
        bull += 8; bull_args.append(f'日线布林下轨刚站稳(+8)')
    elif price > bb1d[0] * 0.98:
        bear += 8; bear_args.append(f'日线布林上轨附近(+8)')

    # ── 3. MACD ───────────────────────────────────────────────
    if macd_dif > 0:
        bull += 6; bull_args.append(f'1H MACD正值金叉(+6)')
    else:
        bear += 6; bear_args.append(f'1H MACD负值死叉(+6)')
    if macd4_dif > 0:
        bull += 8; bull_args.append(f'4H MACD正值(+8)')
    else:
        bear += 8; bear_args.append(f'4H MACD负值(+8)')

    # ── 4. 资金费率（极强信号）────────────────────────────────
    if fr < -0.008:
        bull += 15; bull_args.append(f'资金费{fr:.4f}%空头极度拥挤=挤空燃料(+15)')
    elif fr < -0.003:
        bull += 8; bull_args.append(f'资金费{fr:.4f}%空头付费(+8)')
    elif fr > 0.008:
        bear += 15; bear_args.append(f'资金费{fr:.4f}%多头极度拥挤=挤多风险(+15)')
    elif fr > 0.003:
        bear += 8; bear_args.append(f'资金费{fr:.4f}%多头付费(+8)')

    # ── 5. 结构 HH/HL ─────────────────────────────────────────
    if hi_trend and lo_trend:
        bull += 10; bull_args.append('HH+HL上升结构确认(+10)')
    elif not hi_trend and not lo_trend:
        bear += 10; bear_args.append('LH+LL下降结构确认(+10)')

    # ── 6. 成交量 ─────────────────────────────────────────────
    if vol_ratio > 1.5:
        # 放量需判断方向
        if c1[-1] > c1[-2]:
            bull += 8; bull_args.append(f'放量上涨vol_ratio={vol_ratio:.1f}(+8)')
        else:
            bear += 8; bear_args.append(f'放量下跌vol_ratio={vol_ratio:.1f}(+8)')
    elif vol_ratio < 0.3:
        # 缩量=方向不确定，双方各减2分
        bull -= 2; bear -= 2

    # ── 归一化到0~100 ─────────────────────────────────────────
    MAX = 85.0
    bull_norm = round(min(max(bull / MAX * 100, 0), 100), 1)
    bear_norm = round(min(max(bear / MAX * 100, 0), 100), 1)
    conviction = round(bull_norm - bear_norm, 1)

    if conviction >= 30:
        bias_label = 'STRONG_BULL'
    elif conviction >= 12:
        bias_label = 'LEAN_BULL'
    elif conviction <= -30:
        bias_label = 'STRONG_BEAR'
    elif conviction <= -12:
        bias_label = 'LEAN_BEAR'
    else:
        bias_label = 'NEUTRAL'

    # 辩论摘要
    debate_lines = [
        f'=== {sym} Bull/Bear辩论 ===',
        f'现价 ${price:.4g}  资金费{fr:.4f}%',
        f'',
        f'🟢 Bull({bull_norm:.0f}分):',
    ]
    for a in bull_args[:5]:
        debate_lines.append(f'  + {a}')
    debate_lines += [f'', f'🔴 Bear({bear_norm:.0f}分):']
    for a in bear_args[:5]:
        debate_lines.append(f'  - {a}')
    debate_lines += [
        f'',
        f'裁定: {bias_label}  置信差={conviction:+.1f}',
    ]

    return {
        'symbol': sym,
        'price': price,
        'bull_score': bull_norm,
        'bear_score': bear_norm,
        'conviction': conviction,
        'bias_label': bias_label,
        'bull_args': bull_args,
        'bear_args': bear_args,
        'debate_summary': '\n'.join(debate_lines),
        'rsi_1h': rsi_1h,
        'rsi_4h': rsi_4h,
        'fr': round(fr, 4),
    }


if __name__ == '__main__':
    import sys
    sym = sys.argv[1] if len(sys.argv) > 1 else 'ETHUSDT'
    result = debate(sym)
    print(result['debate_summary'])
    print(f'\nbull={result["bull_score"]}  bear={result["bear_score"]}  bias={result["bias_label"]}')
