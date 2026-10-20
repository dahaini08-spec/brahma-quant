#!/usr/bin/env python3
"""
三所清算数据聚合采集器
[V2.0 2026-09-20 苏摩111]

根因：当前清算数据只用Binance单一交易所，清算地图不够准确
方案：聚合Binance+OKX+Bybit三所清算数据 + 每日历史存档用于回测

数据源：
- Binance: /futures/data/topLongShortPositionRatio (已有)
- OKX: /api/v5/public/liquidation-orders (新增)
- Bybit: /v5/liquidation (新增)

输出：
- data/liq_aggregated_btcusdt.json — 三所合并清算地图
- data/history/liq_YYYYMMDD.jsonl — 每日快照存档
"""

import json, time, os, sys
from pathlib import Path
from datetime import datetime, timezone, date
import logging

BASE = Path(__file__).parent.parent
DATA = BASE / 'data'
HISTORY = DATA / 'history'

# 延迟import，避免启动时网络请求
def _fetch_binance_liq(symbol: str) -> dict:
    """Binance清算数据（已有liqmap_collector的轻量版）"""
    try:
        import requests
        url = f'https://fapi.binance.com/futures/data/topLongShortPositionRatio'
        params = {'symbol': symbol, 'period': '1h', 'limit': 1}
        r = requests.get(url, params=params, timeout=10)
        if r.status_code == 200 and r.json():
            d = r.json()[0]
            return {
                'exchange': 'binance',
                'long_short_ratio': float(d.get('longShortRatio', 1.0)),
                'long_acct': float(d.get('longAccount', 0.5)),
                'short_acct': float(d.get('shortAccount', 0.5)),
                'ts': int(d.get('timestamp', time.time()*1000)),
            }
    except Exception as e:
        logging.debug(f"binance liq error: {e}")
    return {'exchange': 'binance', 'long_short_ratio': 1.0, 'long_acct': 0.5, 'short_acct': 0.5}


def _fetch_okx_liq(symbol: str) -> dict:
    """OKX清算订单"""
    try:
        import requests
        # OKX清算订单API
        okx_symbol = symbol.replace('USDT', '-USDT-SWAP')
        url = 'https://www.okx.com/api/v5/public/liquidation-orders'
        params = {'instId': okx_symbol, 'uly': symbol.replace('USDT', '-USDT')}
        r = requests.get(url, params=params, timeout=10)
        if r.status_code == 200:
            data = r.json().get('data', [])
            long_liq = sum(1 for d in data if d.get('side') == 'long')
            short_liq = sum(1 for d in data if d.get('side') == 'short')
            return {
                'exchange': 'okx',
                'long_liq_count': long_liq,
                'short_liq_count': short_liq,
                'total_liq': len(data),
                'ts': int(time.time() * 1000),
            }
    except Exception as e:
        logging.debug(f"okx liq error: {e}")
    return {'exchange': 'okx', 'long_liq_count': 0, 'short_liq_count': 0, 'total_liq': 0}


def _fetch_bybit_liq(symbol: str) -> dict:
    """Bybit清算订单"""
    try:
        import requests
        url = 'https://api.bybit.com/v5/market/liquidation'
        params = {'category': 'linear', 'symbol': symbol}
        r = requests.get(url, params=params, timeout=10)
        if r.status_code == 200:
            data = r.json().get('result', {}).get('list', [])
            long_liq = sum(1 for d in data if d.get('side') == 'Buy')
            short_liq = sum(1 for d in data if d.get('side') == 'Sell')
            return {
                'exchange': 'bybit',
                'long_liq_count': long_liq,
                'short_liq_count': short_liq,
                'total_liq': len(data),
                'ts': int(time.time() * 1000),
            }
    except Exception as e:
        logging.debug(f"bybit liq error: {e}")
    return {'exchange': 'bybit', 'long_liq_count': 0, 'short_liq_count': 0, 'total_liq': 0}


def aggregate_liq(symbol: str) -> dict:
    """聚合三所清算数据"""
    binance = _fetch_binance_liq(symbol)
    okx = _fetch_okx_liq(symbol)
    bybit = _fetch_bybit_liq(symbol)
    
    # 合并
    total_long_liq = okx.get('long_liq_count', 0) + bybit.get('long_liq_count', 0)
    total_short_liq = okx.get('short_liq_count', 0) + bybit.get('short_liq_count', 0)
    
    # Binance LSR作为基准
    lsr = binance.get('long_short_ratio', 1.0)
    
    # 三所综合清算压力
    # 多头清算 > 空头清算 → 上方止损墙更强
    # 空头清算 > 多头清算 → 下方支撑池更强
    if total_long_liq + total_short_liq > 0:
        long_pressure = total_long_liq / (total_long_liq + total_short_liq)
    else:
        long_pressure = binance.get('long_acct', 0.5)
    
    result = {
        'symbol': symbol,
        'ts': time.time() * 1000,
        'updated': datetime.now(timezone.utc).isoformat(),
        'exchanges': {
            'binance': binance,
            'okx': okx,
            'bybit': bybit,
        },
        'aggregated': {
            'lsr': lsr,
            'long_pressure': round(long_pressure, 3),
            'short_pressure': round(1 - long_pressure, 3),
            'total_liq_events': total_long_liq + total_short_liq,
            'long_liq': total_long_liq,
            'short_liq': total_short_liq,
        },
    }
    
    return result


def save_aggregated(symbol: str, data: dict):
    """保存聚合数据"""
    sym_lower = symbol.lower()
    out = DATA / f'liq_aggregated_{sym_lower}.json'
    out.write_text(json.dumps(data, indent=2))
    
    # 历史存档
    HISTORY.mkdir(parents=True, exist_ok=True)
    today = date.today().strftime('%Y%m%d')
    hist_file = HISTORY / f'liq_{today}.jsonl'
    with open(hist_file, 'a') as f:
        f.write(json.dumps(data, ensure_ascii=False) + '\n')


def main_loop(symbols=None, interval=300):
    """主循环"""
    if symbols is None:
        symbols = ['BTCUSDT', 'ETHUSDT']
    
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
    logging.info(f'三所清算聚合采集器启动 symbols={symbols} interval={interval}s')
    
    while True:
        for sym in symbols:
            try:
                data = aggregate_liq(sym)
                save_aggregated(sym, data)
                agg = data['aggregated']
                logging.info(f'{sym}: LSR={agg["lsr"]:.3f} long_liq={agg["long_liq"]} short_liq={agg["short_liq"]}')
            except Exception as e:
                logging.error(f'{sym} error: {e}')
        
        time.sleep(interval)


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--symbols', nargs='+', default=['BTCUSDT', 'ETHUSDT'])
    p.add_argument('--once', action='store_true')
    p.add_argument('--interval', type=int, default=300)
    args = p.parse_args()
    
    if args.once:
        for sym in args.symbols:
            data = aggregate_liq(sym)
            save_aggregated(sym, data)
            print(f'{sym}: LSR={data["aggregated"]["lsr"]:.3f} long={data["aggregated"]["long_liq"]} short={data["aggregated"]["short_liq"]}')
    else:
        main_loop(args.symbols, args.interval)
