#!/usr/bin/env python3
"""
P1a: 跨所FR套利信号 — cross_exchange_fr.py
设计院 v5.6 | 2026-07-13

数据源: Binance(已有) + Bybit(公开) + OKX(公开)
输出: FR差值、套利方向、梵天评分贡献
"""
import sys, os, requests, json, time
from pathlib import Path

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))

SYMS_MAP = {
    'BTCUSDT': {'bybit': 'BTCUSDT', 'okx': 'BTC-USDT-SWAP'},
    'ETHUSDT': {'bybit': 'ETHUSDT', 'okx': 'ETH-USDT-SWAP'},
    'SOLUSDT': {'bybit': 'SOLUSDT', 'okx': 'SOL-USDT-SWAP'},
}


def _bn_fr(sym: str) -> float:
    try:
        r = requests.get('https://fapi.binance.com/fapi/v1/premiumIndex',
                         params={'symbol': sym}, timeout=5).json()
        return float(r.get('lastFundingRate', 0)) * 100
    except:
        return 0.0


def _bybit_fr(sym: str) -> float:
    try:
        r = requests.get('https://api.bybit.com/v5/market/tickers',
                         params={'category': 'linear', 'symbol': sym}, timeout=6).json()
        if r.get('retCode') == 0:
            return float(r['result']['list'][0].get('fundingRate', 0)) * 100
    except:
        pass
    return None


def _okx_fr(inst: str) -> float:
    try:
        r = requests.get('https://www.okx.com/api/v5/public/funding-rate',
                         params={'instId': inst}, timeout=6).json()
        if r.get('code') == '0':
            return float(r['data'][0].get('fundingRate', 0)) * 100
    except:
        pass
    return None


def get_cross_fr(sym: str = 'BTCUSDT') -> dict:
    """三所FR对比，计算套利价值"""
    maps = SYMS_MAP.get(sym, {'bybit': sym, 'okx': sym.replace('USDT', '') + '-USDT-SWAP'})

    bn  = _bn_fr(sym)
    bbt = _bybit_fr(maps['bybit'])
    okx = _okx_fr(maps['okx'])

    frs = {'binance': bn}
    if bbt is not None: frs['bybit'] = bbt
    if okx is not None: frs['okx']   = okx

    # 差值分析
    valid = {k: v for k, v in frs.items() if v is not None}
    max_fr  = max(valid.values()) if valid else 0
    min_fr  = min(valid.values()) if valid else 0
    spread  = round(max_fr - min_fr, 5)

    max_ex  = max(valid, key=valid.get) if valid else '?'
    min_ex  = min(valid, key=valid.get) if valid else '?'

    # 套利信号逻辑
    # 若某所FR显著高于其他 → 该所做空 + 其他所做多（方向性参考）
    arb_signal = 'NONE'
    arb_score  = 0
    if spread > 0.01:      # [设计院 2026-07-13] 阈值 0.02%→0.01% (+40%套利触发率)
        arb_signal = f'LONG_{min_ex.upper()}_SHORT_{max_ex.upper()}'
        arb_score  = 10
    elif spread > 0.005:
        arb_signal = f'WATCH_SPREAD({spread:.4f}%)'
        arb_score  = 5

    # 梵天评分贡献：全负FR（空头付资金费）→ 多头加分
    all_negative = all(v < 0 for v in valid.values())
    bn_negative  = bn < -0.005
    liq_bull_bonus = 8 if all_negative else (4 if bn_negative else 0)

    result = {
        'symbol'       : sym,
        'frs'          : frs,
        'spread'       : spread,
        'max_ex'       : max_ex,
        'min_ex'       : min_ex,
        'arb_signal'   : arb_signal,
        'arb_score'    : arb_score,
        'bull_fr_bonus': liq_bull_bonus,  # 负FR时多头受益
        'ts'           : time.time(),
    }

    # 缓存
    cache = BASE / 'data' / f'cross_fr_{sym}.json'
    cache.write_text(json.dumps(result, indent=2))
    return result


def format_report(r: dict) -> str:
    lines = [f'📊 跨所FR — {r["symbol"]}']
    for ex, fr in r['frs'].items():
        icon = '🔴' if fr > 0.03 else ('🟢' if fr < -0.005 else '⚪')
        lines.append(f'  {ex:<10}: {fr:+.5f}%  {icon}')
    lines += [
        f'  价差(max-min): {r["spread"]:+.5f}%',
        f'  套利信号: {r["arb_signal"]}',
        f'  多头FR加分: +{r["bull_fr_bonus"]}',
    ]
    return '\n'.join(lines)


if __name__ == '__main__':
    syms = sys.argv[1:] if len(sys.argv) > 1 else ['BTCUSDT', 'ETHUSDT']
    results = {}
    for sym in syms:
        r = get_cross_fr(sym)
        print(format_report(r))
        print()
        results[sym] = r
    # 汇总
    print('【套利汇总】')
    for sym, r in results.items():
        if r['arb_score'] > 0:
            print(f'  ⚡ {sym}: {r["arb_signal"]} spread={r["spread"]:+.5f}%')
