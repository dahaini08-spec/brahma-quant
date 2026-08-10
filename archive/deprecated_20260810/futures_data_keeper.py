#!/usr/bin/env python3
"""
梵天设计院 · 期货合约数据保持系统
覆盖 15m / 1h / 4h / 1d 全周期
封印: 2026-08-07
"""
import requests, json, time, sys, os
from datetime import datetime, timezone
from pathlib import Path

BASE = 'https://fapi.binance.com'
SYMBOLS = ['BTCUSDT', 'ETHUSDT']
DATA_DIR = Path(__file__).parent.parent / 'data'
DATA_DIR.mkdir(exist_ok=True)

# 各周期K线数量
INTERVALS = {
    '15m': 200,   # 最近50小时
    '1h':  200,   # 最近8.3天
    '4h':  200,   # 最近33天
    '1d':  365,   # 最近1年
}

def get(endpoint, params={}, retries=3):
    for i in range(retries):
        try:
            r = requests.get(BASE + endpoint, params=params, timeout=12)
            if r.status_code == 200:
                return r.json()
            time.sleep(0.5)
        except Exception as e:
            if i == retries - 1:
                print(f'  ⚠️ {endpoint} 失败: {e}')
    return None

def ema(data, n):
    if not data: return 0
    e = data[0]; k = 2 / (n + 1)
    for x in data[1:]: e = x * k + e * (1 - k)
    return e

def rsi(data, n=14):
    if len(data) < n + 1: return 50.0
    gains  = [max(data[i]-data[i-1], 0) for i in range(1, len(data))]
    losses = [max(data[i-1]-data[i], 0) for i in range(1, len(data))]
    ag = sum(gains[-n:]) / n
    al = sum(losses[-n:]) / n
    if al == 0: return 100.0
    return round(100 - 100 / (1 + ag / al), 2)

def atr(klines, n=14):
    trs = []
    for i in range(1, min(n+1, len(klines))):
        h = klines[i]['high']; l = klines[i]['low']; pc = klines[i-1]['close']
        trs.append(max(h-l, abs(h-pc), abs(l-pc)))
    return round(sum(trs)/len(trs), 2) if trs else 0

def fetch_klines(sym, interval, limit):
    raw = get('/fapi/v1/klines', {'symbol': sym, 'interval': interval, 'limit': limit})
    if not raw: return []
    return [{
        'ts':    int(k[0]),
        'time':  datetime.utcfromtimestamp(k[0]/1000).strftime('%m-%d %H:%M'),
        'open':  float(k[1]), 'high': float(k[2]),
        'low':   float(k[3]), 'close': float(k[4]),
        'volume': float(k[5]), 'quote_vol': float(k[7]),
        'trades': int(k[8]),
        'taker_buy_vol': float(k[9]),
    } for k in raw]

def fetch_spot(sym):
    ticker = get('/fapi/v1/ticker/24hr', {'symbol': sym})
    prem   = get('/fapi/v1/premiumIndex',  {'symbol': sym})
    oi_raw = get('/fapi/v1/openInterest',  {'symbol': sym})
    if not ticker: return {}
    d = {
        'price':             float(ticker['lastPrice']),
        'change_24h_pct':    float(ticker['priceChangePercent']),
        'high_24h':          float(ticker['highPrice']),
        'low_24h':           float(ticker['lowPrice']),
        'volume_24h':        float(ticker['volume']),
        'quote_vol_24h_b':   round(float(ticker['quoteVolume'])/1e9, 4),
    }
    if prem:
        d['mark_price']        = float(prem['markPrice'])
        d['index_price']       = float(prem['indexPrice'])
        d['funding_rate_pct']  = round(float(prem['lastFundingRate'])*100, 6)
        d['next_funding_time'] = datetime.utcfromtimestamp(prem['nextFundingTime']/1000).strftime('%H:%M UTC')
    if oi_raw:
        d['open_interest']     = float(oi_raw['openInterest'])
        d['open_interest_usd_b'] = round(float(oi_raw['openInterest']) * d['price'] / 1e9, 4)
    return d

def fetch_derivatives(sym):
    d = {}
    # 多空比
    lsr = get('/futures/data/globalLongShortAccountRatio', {'symbol': sym, 'period': '1h', 'limit': 5})
    if lsr:
        d['long_pct']   = round(float(lsr[-1]['longAccount'])*100, 2)
        d['short_pct']  = round(float(lsr[-1]['shortAccount'])*100, 2)
        d['lsr_history'] = [{'time': datetime.utcfromtimestamp(x['timestamp']/1000).strftime('%H:%M'), 'long': round(float(x['longAccount'])*100,1)} for x in lsr]
    # OI历史
    for period in ['15m', '1h', '4h']:
        oi_h = get('/futures/data/openInterestHist', {'symbol': sym, 'period': period, 'limit': 10})
        if oi_h:
            d[f'oi_hist_{period}'] = [{'time': datetime.utcfromtimestamp(x['timestamp']/1000).strftime('%H:%M'), 'oi_b': round(float(x['sumOpenInterestValue'])/1e9,4)} for x in oi_h]
            # OI变化率
            if len(oi_h) >= 2:
                oi_chg = (float(oi_h[-1]['sumOpenInterest']) - float(oi_h[0]['sumOpenInterest'])) / float(oi_h[0]['sumOpenInterest']) * 100
                d[f'oi_change_{period}_pct'] = round(oi_chg, 3)
    # Taker买卖比
    taker = get('/futures/data/takerlongshortRatio', {'symbol': sym, 'period': '15m', 'limit': 8})
    if taker:
        d['taker_ratio_15m'] = [{'time': datetime.utcfromtimestamp(x['timestamp']/1000).strftime('%H:%M'), 'ratio': round(float(x['buySellRatio']),3)} for x in taker]
    # 资金费率历史
    fr_hist = get('/fapi/v1/fundingRate', {'symbol': sym, 'limit': 8})
    if fr_hist:
        d['funding_rate_history'] = [{'time': datetime.utcfromtimestamp(x['fundingTime']/1000).strftime('%m-%d %H:%M'), 'rate_pct': round(float(x['fundingRate'])*100,5)} for x in fr_hist]
    return d

def run():
    now_utc = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    ts = int(time.time())
    print(f'[{now_utc}] 梵天期货数据保持系统 启动')

    master = {'generated_at': now_utc, 'ts': ts}
    klines_all = {'generated_at': now_utc, 'ts': ts}

    for sym in SYMBOLS:
        print(f'\n  [{sym}]')
        spot = fetch_spot(sym)
        deriv = fetch_derivatives(sym)

        # K线全周期
        klines_sym = {}
        for tf, limit in INTERVALS.items():
            kl = fetch_klines(sym, tf, limit)
            klines_sym[tf] = kl
            if kl:
                closes = [x['close'] for x in kl]
                r1 = rsi(closes)
                e20 = ema(closes, 20)
                atr_val = atr(kl)
                atr_pct = round(atr_val / closes[-1] * 100, 3) if closes[-1] else 0
                print(f'    {tf}: {len(kl)}根 RSI={r1} EMA20={e20:.2f} ATR={atr_pct:.3f}%')

        klines_all[sym] = klines_sym

        # 技术指标汇总
        def tech(kl):
            if not kl: return {}
            closes = [x['close'] for x in kl]
            highs  = [x['high']  for x in kl]
            lows   = [x['low']   for x in kl]
            return {
                'rsi':    rsi(closes),
                'ema20':  round(ema(closes, 20), 4),
                'ema50':  round(ema(closes, 50), 4),
                'ema200': round(ema(closes, 200), 4) if len(closes)>=200 else None,
                'atr_pct': round(atr(kl)/closes[-1]*100, 4) if closes[-1] else 0,
                'price_vs_ema20_pct': round((closes[-1]-ema(closes,20))/ema(closes,20)*100, 3),
                'ret_5bar': round((closes[-1]-closes[-6])/closes[-6]*100, 3) if len(closes)>=6 else None,
                'ret_20bar': round((closes[-1]-closes[-21])/closes[-21]*100, 3) if len(closes)>=21 else None,
            }

        master[sym] = {
            **spot,
            **deriv,
            'tech_15m': tech(klines_sym.get('15m',[])),
            'tech_1h':  tech(klines_sym.get('1h',[])),
            'tech_4h':  tech(klines_sym.get('4h',[])),
            'tech_1d':  tech(klines_sym.get('1d',[])),
        }

        px = spot.get('price', 0)
        fr = spot.get('funding_rate_pct', 0)
        oi = spot.get('open_interest_usd_b', 0)
        lp = deriv.get('long_pct', 0)
        r4h = master[sym]['tech_4h'].get('rsi', 0)
        print(f'    → \${px:,.2f} FR={fr:+.4f}% OI={oi:.2f}B 多={lp:.1f}% RSI_4H={r4h}')

    # 保存
    master_path = DATA_DIR / 'futures_master.json'
    klines_path = DATA_DIR / 'futures_klines.json'

    with open(master_path, 'w') as f:
        json.dump(master, f, ensure_ascii=False, indent=2)
    with open(klines_path, 'w') as f:
        json.dump(klines_all, f, ensure_ascii=False)

    print(f'\n  ✅ futures_master.json ({master_path.stat().st_size//1024}KB)')
    print(f'  ✅ futures_klines.json  ({klines_path.stat().st_size//1024}KB)')
    print(f'  完成时间: {datetime.now(timezone.utc).strftime("%H:%M:%S UTC")}')

if __name__ == '__main__':
    run()
