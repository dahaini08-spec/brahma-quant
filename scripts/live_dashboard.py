#!/usr/bin/env python3
"""
live_dashboard.py — 姓赵不宣直播数据面板聚合器 v1.0
[设计院封印 2026-09-15 苏摩111]

功能：每10s刷新OI/CVD/FR/K线/清算/体制 → 输出到终端 → 苏摩投屏读取
直播时运行：python3 scripts/live_dashboard.py
"""

import json, os, sys, time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

BASE = Path(__file__).parent.parent
DATA = BASE / 'data'
CST = timezone(timedelta(hours=8))
FAPI = 'https://fapi.binance.com/fapi/v1'

SYMBOLS = ['BTCUSDT', 'ETHUSDT']
REFRESH_SEC = 10


def fetch_ticker(sym):
    try:
        r = requests.get(f'{FAPI}/ticker/24hr?symbol={sym}', timeout=5).json()
        return {
            'price': float(r['lastPrice']),
            'chg_pct': float(r['priceChangePercent']),
            'high': float(r['highPrice']),
            'low': float(r['lowPrice']),
            'vol': float(r['quoteVolume']),
        }
    except:
        return {}


def fetch_fr(sym):
    try:
        r = requests.get(f'{FAPI}/premiumIndex?symbol={sym}', timeout=5).json()
        return float(r.get('lastFundingRate', 0)) * 100
    except:
        return 0


def fetch_oi(sym):
    try:
        r = requests.get(f'{FAPI}/data/openInterestHist',
                         params={'symbol': sym, 'period': '4h', 'limit': 6}, timeout=5).json()
        if r and len(r) >= 2:
            vals = [float(x['sumOpenInterestValue']) for x in r]
            trend = 'BUILD' if vals[-1] > vals[0] else 'UNWIND'
            chg = (vals[-1] - vals[0]) / vals[0] * 100
            return {'trend': trend, 'chg_pct': chg, 'current': vals[-1]}
    except:
        pass
    return {}


def load_cvd(sym):
    path = DATA / f'cvd_realtime_{sym.lower()}.json'
    if path.exists():
        try:
            return json.loads(path.read_text())
        except:
            pass
    return {}


def load_liq(sym):
    path = DATA / f'liq_heatmap_{sym}.json'
    if path.exists():
        try:
            return json.loads(path.read_text())
        except:
            pass
    return {}


def load_regime(sym):
    path = DATA / 'regime_state.json'
    if path.exists():
        try:
            data = json.loads(path.read_text())
            # key is uppercase SYM+USDT
            entry = data.get(sym, {})
            if not entry:
                # try without USDT
                entry = data.get(sym.replace('USDT',''), {})
            return entry
        except:
            pass
    return {}


def load_gex(sym):
    # GEX cache per-symbol (修复E 2026-09-16)
    currency = 'BTC' if 'BTC' in sym else 'ETH' if 'ETH' in sym else sym.replace('USDT','')
    path = DATA / f'gex_cache_{currency.lower()}.json'
    if not path.exists():
        path = DATA / 'gex_cache.json'  # fallback to old cache
    if path.exists():
        try:
            return json.loads(path.read_text())
        except:
            pass
    return {}


def format_panel():
    lines = []
    now = datetime.now(CST).strftime('%H:%M:%S')
    lines.append(f'┌─────────────────────────────────────────────┐')
    lines.append(f'│  🎙️ 姓赵不宣直播数据面板  {now} CST        │')
    lines.append(f'├─────────────────────────────────────────────┤')

    for sym in SYMBOLS:
        ticker = fetch_ticker(sym)
        fr = fetch_fr(sym)
        oi = fetch_oi(sym)
        cvd = load_cvd(sym)
        liq = load_liq(sym)
        regime = load_regime(sym)
        gex = load_gex(sym)

        if not ticker:
            lines.append(f'│  {sym}: 数据拉取失败                        │')
            continue

        price = ticker['price']
        chg = ticker['chg_pct']
        high = ticker['high']
        low = ticker['low']
        vol = ticker['vol']

        # 价格行
        price_str = f'{price:,.2f}' if sym == 'ETHUSDT' else f'{price:,.0f}'
        lines.append(f'│  {sym.replace("USDT","")}  ${price_str}  {chg:+.2f}%{"":<8}│')

        # FR
        fr_str = f'FR {fr:+.4f}%'
        if fr > 0.03:
            fr_str += ' ⚠️多头拥挤'
        elif fr < -0.03:
            fr_str += ' ⚠️空头拥挤'
        lines.append(f'│  {fr_str:<43}│')

        # OI
        if oi:
            oi_str = f'OI {oi["trend"]} {oi["chg_pct"]:+.1f}%'
            lines.append(f'│  {oi_str:<43}│')

        # CVD
        if cvd:
            cvd_1h = cvd.get('cvd_1h', 0)
            cvd_str = f'CVD 1H {cvd_1h:+,.0f}'
            lines.append(f'│  {cvd_str:<43}│')

        # 体制
        if regime:
            reg_name = regime.get('confirmed', '?')
            reg_cn = regime.get('confirmed_cn', '')
            reg_str = f'体制 {reg_name} {reg_cn}'
            lines.append(f'│  {reg_str:<43}│')

        # GEX (global, show once)
        if gex and sym == SYMBOLS[0]:
            gex_val = gex.get('total_gex', 0)
            gex_regime = gex.get('regime', '?')
            gex_magnet = gex.get('gamma_magnet', 0)
            gex_str = f'GEX {gex_val:+,.0f} {gex_regime} magnet={gex_magnet:.0f}'
            lines.append(f'│  {gex_str:<43}│')

        # 清算
        if liq:
            above = liq.get('nearest_short_liq', 0)
            below = liq.get('nearest_long_liq', 0)
            if above and below:
                above_pct = (above - price) / price * 100
                below_pct = (below - price) / price * 100
                above_str = f'{above:,.0f}' if sym == 'BTCUSDT' else f'{above:.2f}'
                below_str = f'{below:,.0f}' if sym == 'BTCUSDT' else f'{below:.2f}'
                liq_str = f'止损墙 {above_str}({above_pct:+.1f}%) 池 {below_str}({below_pct:+.1f}%)'
                lines.append(f'│  {liq_str:<43}│')

        lines.append(f'│  {"":<43}│')

    lines.append(f'└─────────────────────────────────────────────┘')
    return '\n'.join(lines)


def main():
    print('🎙️ 姓赵不宣直播数据面板启动')
    print(f'刷新频率: 每{REFRESH_SEC}秒')
    print('按 Ctrl+C 退出\n')

    try:
        while True:
            os.system('clear' if os.name != 'nt' else 'cls')
            print(format_panel())
            print()
            print('提示: 此面板供直播投屏使用，数据来自梵天系统+币安API')
            time.sleep(REFRESH_SEC)
    except KeyboardInterrupt:
        print('\n面板已退出')


if __name__ == '__main__':
    main()
