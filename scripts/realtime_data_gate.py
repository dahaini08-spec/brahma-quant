#!/usr/bin/env python3
"""
realtime_data_gate.py — AI分析数据强制验证门控
设计院 2026-06-20

核心原则：
  AI在对话中做任何标的分析前，必须先运行此脚本。
  脚本输出的JSON是唯一合法数据源。
  禁止AI在未运行此脚本的情况下引用任何LS/FR/RSI/OI数字。

三端口LS完整输出（防止端点混淆）：
  ls_position  = topLongShortPositionRatio  → 大户「仓位」比
  ls_account   = topLongShortAccountRatio   → 大户「账户数」比
  ls_global    = globalLongShortAccountRatio → 全市「账户数」比
"""

import sys, json, time, urllib.request
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

FAPI = 'https://fapi.binance.com'
FETCH_TIME = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')

def _get(url: str):
    req = urllib.request.Request(url, headers={'User-Agent': 'BrahmaGate/1.0'})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())

def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i-1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    for i in range(period, len(gains)):
        ag = (ag * (period-1) + gains[i]) / period
        al = (al * (period-1) + losses[i]) / period
    return round(100 - 100 / (1 + ag / al), 2) if al > 0 else 100.0

def calc_ema(closes, period):
    if len(closes) < period:
        return None
    k = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    for c in closes[period:]:
        ema = c * k + ema * (1 - k)
    return round(ema, 6)

def fetch_all(symbol: str) -> dict:
    sym = symbol.upper()
    tasks = {
        'ticker':      f'{FAPI}/fapi/v1/ticker/24hr?symbol={sym}',
        'premium':     f'{FAPI}/fapi/v1/premiumIndex?symbol={sym}',
        'oi':          f'{FAPI}/fapi/v1/openInterest?symbol={sym}',
        'fr_hist':     f'{FAPI}/fapi/v1/fundingRate?symbol={sym}&limit=8',
        'klines_15m':  f'{FAPI}/fapi/v1/klines?symbol={sym}&interval=15m&limit=20',
        'klines_1h':   f'{FAPI}/fapi/v1/klines?symbol={sym}&interval=1h&limit=20',
        'klines_4h':   f'{FAPI}/fapi/v1/klines?symbol={sym}&interval=4h&limit=20',
        # ── 三端口LS（核心修复：全部并发拉取，禁止混淆）
        'ls_position': f'{FAPI}/futures/data/topLongShortPositionRatio?symbol={sym}&period=1h&limit=8',
        'ls_account':  f'{FAPI}/futures/data/topLongShortAccountRatio?symbol={sym}&period=1h&limit=8',
        'ls_global':   f'{FAPI}/futures/data/globalLongShortAccountRatio?symbol={sym}&period=1h&limit=8',
        'oi_hist':     f'{FAPI}/futures/data/openInterestHist?symbol={sym}&period=1h&limit=8',
    }

    raw = {}
    errors = {}
    with ThreadPoolExecutor(max_workers=11) as ex:
        futs = {ex.submit(_get, url): key for key, url in tasks.items()}
        for f in as_completed(futs):
            key = futs[f]
            try:
                raw[key] = f.result()
            except Exception as e:
                errors[key] = str(e)

    out = {
        'symbol': sym,
        'fetch_time_utc': FETCH_TIME,
        'fetch_errors': errors,
        # 数据来源端点清单（强制透明）
        'data_sources': {
            'price':       'fapi/v1/ticker/24hr',
            'fr':          'fapi/v1/premiumIndex + fapi/v1/fundingRate',
            'ls_position': 'futures/data/topLongShortPositionRatio  ← 大户仓位比',
            'ls_account':  'futures/data/topLongShortAccountRatio   ← 大户账户比',
            'ls_global':   'futures/data/globalLongShortAccountRatio ← 全市账户比',
            'oi':          'fapi/v1/openInterest + futures/data/openInterestHist',
            'klines':      'fapi/v1/klines 15m/1h/4h',
        }
    }

    # ── 价格 ────────────────────────────────────────────
    if 'ticker' in raw:
        t = raw['ticker']
        out['price'] = float(t['lastPrice'])
        out['chg_24h_pct'] = float(t['priceChangePercent'])
        out['high_24h'] = float(t['highPrice'])
        out['low_24h'] = float(t['lowPrice'])
        out['vol_24h_usd_M'] = round(float(t['quoteVolume']) / 1e6, 2)

    # ── FR ──────────────────────────────────────────────
    if 'premium' in raw:
        p = raw['premium']
        out['fr_current_pct'] = round(float(p['lastFundingRate']) * 100, 4)
        out['mark_price'] = float(p['markPrice'])
        out['index_price'] = float(p['indexPrice'])
        out['premium_pct'] = round((float(p['markPrice']) - float(p['indexPrice'])) / float(p['indexPrice']) * 100, 4)
    if 'fr_hist' in raw and isinstance(raw['fr_hist'], list):
        fr_vals = [round(float(x['fundingRate']) * 100, 4) for x in raw['fr_hist']]
        out['fr_history_8'] = fr_vals
        out['fr_avg_8_pct'] = round(sum(fr_vals) / len(fr_vals), 4)
        out['fr_daily_cost_pct'] = round(out.get('fr_avg_8_pct', 0) * 3, 4)

    # ── OI ──────────────────────────────────────────────
    if 'oi' in raw:
        out['oi_current'] = float(raw['oi']['openInterest'])
    if 'oi_hist' in raw and isinstance(raw['oi_hist'], list) and len(raw['oi_hist']) >= 2:
        oi_vals = [float(x['sumOpenInterest']) for x in raw['oi_hist']]
        out['oi_history_8'] = [round(v, 0) for v in oi_vals]
        out['oi_chg_1h_pct'] = round((oi_vals[-1] - oi_vals[-2]) / oi_vals[-2] * 100, 2) if oi_vals[-2] else 0
        out['oi_chg_8h_pct'] = round((oi_vals[-1] - oi_vals[0]) / oi_vals[0] * 100, 2) if oi_vals[0] else 0

    # ── 三端口LS（核心：分开标注，禁止混淆）─────────────
    def _parse_ls(raw_list, name):
        if not isinstance(raw_list, list) or not raw_list:
            return None
        latest = float(raw_list[-1].get('longShortRatio', 1.0))
        history = [round(float(x.get('longShortRatio', 1.0)), 4) for x in raw_list]
        # 计算多头百分比（从ratio计算）
        long_pct = round(latest / (1 + latest) * 100, 2)
        return {
            'ratio_latest': round(latest, 4),
            'long_pct': long_pct,
            'short_pct': round(100 - long_pct, 2),
            'history_8': history,
            'trend': 'DOWN' if len(history) >= 2 and history[-1] < history[-2] else 'UP',
            'meaning': name,
        }

    if 'ls_position' in raw:
        out['ls_position'] = _parse_ls(raw['ls_position'],
            '大户仓位比(topLongShortPositionRatio) ← 聪明钱方向，最重要')
    if 'ls_account' in raw:
        out['ls_account'] = _parse_ls(raw['ls_account'],
            '大户账户比(topLongShortAccountRatio) ← 大户账户数')
    if 'ls_global' in raw:
        out['ls_global'] = _parse_ls(raw['ls_global'],
            '全市账户比(globalLongShortAccountRatio) ← 散户情绪')

    # ── RSI ─────────────────────────────────────────────
    for tf, key in [('15m', 'klines_15m'), ('1h', 'klines_1h'), ('4h', 'klines_4h')]:
        if key in raw and isinstance(raw[key], list):
            closes = [float(k[4]) for k in raw[key]]
            rsi = calc_rsi(closes)
            ema20 = calc_ema(closes, 20)
            ema50 = calc_ema(closes, 50)
            price = closes[-1] if closes else None
            out[f'rsi_{tf}'] = rsi
            out[f'ema20_{tf}'] = ema20
            out[f'ema50_{tf}'] = ema50

    # ── 最近4根1H K线结构 ────────────────────────────────
    if 'klines_1h' in raw and isinstance(raw['klines_1h'], list):
        recent = raw['klines_1h'][-4:]
        candles = []
        for k in recent:
            o, h, l, c = float(k[1]), float(k[2]), float(k[3]), float(k[4])
            total = h - l
            body = abs(c - o)
            upper = h - max(o, c)
            chg = round((c - o) / o * 100, 2)
            candles.append({
                'chg_pct': chg,
                'upper_wick_pct': round(upper / total * 100, 1) if total > 0 else 0,
                'body_pct': round(body / total * 100, 1) if total > 0 else 0,
                'close': round(c, 6),
                'direction': 'BULL' if c > o else 'BEAR',
            })
        out['candles_1h_recent4'] = candles

    return out


if __name__ == '__main__':
    symbol = sys.argv[1] if len(sys.argv) > 1 else 'BTCUSDT'
    data = fetch_all(symbol)
    print(json.dumps(data, indent=2, ensure_ascii=False))

    # ── 人类可读摘要（供AI直接引用）────────────────────
    print('\n' + '='*60)
    print(f'📡 {symbol} 实时数据摘要 [{data["fetch_time_utc"]}]')
    print('='*60)
    print(f'价格:      ${data.get("price", "N/A")}  ({data.get("chg_24h_pct", 0):+.2f}% 24H)')
    print(f'RSI:       15m={data.get("rsi_15m","N/A")}  1H={data.get("rsi_1h","N/A")}  4H={data.get("rsi_4h","N/A")}')
    print(f'FR:        当前={data.get("fr_current_pct","N/A")}%/8H  均值={data.get("fr_avg_8_pct","N/A")}%  日耗={data.get("fr_daily_cost_pct","N/A")}%')
    print(f'OI变化:    1H={data.get("oi_chg_1h_pct","N/A")}%  8H={data.get("oi_chg_8h_pct","N/A")}%')
    print()
    print('【LS三端口完整数据 — 禁止混淆使用】')
    for key in ['ls_position', 'ls_account', 'ls_global']:
        d = data.get(key)
        if d:
            print(f'  {key}:')
            print(f'    含义: {d["meaning"]}')
            print(f'    最新ratio: {d["ratio_latest"]}  多头:{d["long_pct"]}%  空头:{d["short_pct"]}%')
            print(f'    历史8H: {d["history_8"]}  趋势:{d["trend"]}')
    if data.get('fetch_errors'):
        print(f'\n⚠️  拉取错误: {data["fetch_errors"]}')
