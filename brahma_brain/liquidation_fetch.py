"""
liquidation_fetch.py — 爆仓量按需拉取（非常驻进程）
[2026-09-22 苏摩111封印] 分析时拉取一次，写入缓存，VETERAN VR08消费

Binance API: /fapi/v1/allForceOrders (免费)
人工分析不需要秒级爆仓监控，按需拉取24h汇总即可
"""
import json, time, requests
from pathlib import Path
from datetime import datetime, timezone, timedelta

DATA_DIR = Path(__file__).parent.parent / 'data'
BINANCE_API = 'https://fapi.binance.com'


def fetch_liquidation_24h(symbol: str = 'BTCUSDT') -> dict:
    """
    分析时按需拉取24h爆仓量，写入缓存文件。
    返回:
        {
            'symbol': str,
            'total_24h_usd': float,      # 24h总爆仓量(USD)
            'long_24h_usd': float,        # 24h多单爆仓
            'short_24h_usd': float,       # 24h空单爆仓
            'long_short_ratio': float,    # 多空爆仓比
            'largest_single': float,      # 最大单笔爆仓
            'ts': float,
        }
    """
    try:
        # Binance所有强平订单（最近24h）
        now = datetime.now(timezone.utc)
        start_time = int((now - timedelta(hours=24)).timestamp() * 1000)
        
        resp = requests.get(
            f'{BINANCE_API}/fapi/v1/allForceOrders',
            params={'symbol': symbol, 'startTime': start_time, 'limit': 1000},
            timeout=15
        )
        
        if resp.status_code != 200:
            # Fallback: use public liquidation endpoint
            return _fetch_via_public(symbol)
        
        orders = resp.json()
        long_liq = 0
        short_liq = 0
        largest_single = 0
        
        for o in orders:
            side = o.get('side', '')
            price = float(o.get('price', 0))
            qty = float(o.get('origQty', 0))
            usd = price * qty
            if side == 'SELL':
                # SELL = 多头被强平
                long_liq += usd
            else:
                # BUY = 空头被强平
                short_liq += usd
            if usd > largest_single:
                largest_single = usd
        
        total = long_liq + short_liq
        ratio = long_liq / short_liq if short_liq > 0 else 999
        
        result = {
            'symbol': symbol,
            'total_24h_usd': total,
            'long_24h_usd': long_liq,
            'short_24h_usd': short_liq,
            'long_short_ratio': ratio,
            'largest_single': largest_single,
            'ts': time.time(),
        }
        
        cache_file = DATA_DIR / f'liquidation_{symbol.lower()}.json'
        cache_file.parent.mkdir(exist_ok=True)
        json.dump(result, open(cache_file, 'w'), indent=2)
        
        return result
        
    except Exception as e:
        import sys
        sys.stderr.write(f'[liquidation_fetch] ERROR: {e}\n')
        return {'symbol': symbol, 'total_24h_usd': 0, 'long_24h_usd': 0, 'short_24h_usd': 0, 'ts': 0}


def _fetch_via_public(symbol: str) -> dict:
    """备用：通过公开API获取爆仓量"""
    try:
        # Binance public liquidation (no auth needed)
        resp = requests.get(
            'https://fapi.binance.com/fapi/v1/allForceOrders',
            params={'symbol': symbol, 'limit': 100},
            timeout=10
        )
        orders = resp.json() if resp.status_code == 200 else []
        long_liq = sum(float(o.get('price',0)) * float(o.get('origQty',0)) 
                       for o in orders if o.get('side') == 'SELL')
        short_liq = sum(float(o.get('price',0)) * float(o.get('origQty',0)) 
                        for o in orders if o.get('side') == 'BUY')
        total = long_liq + short_liq
        result = {
            'symbol': symbol,
            'total_24h_usd': total,
            'long_24h_usd': long_liq,
            'short_24h_usd': short_liq,
            'long_short_ratio': long_liq / short_liq if short_liq > 0 else 999,
            'largest_single': 0,
            'ts': time.time(),
        }
        cache_file = DATA_DIR / f'liquidation_{symbol.lower()}.json'
        json.dump(result, open(cache_file, 'w'), indent=2)
        return result
    except Exception as e:
        import sys
        sys.stderr.write(f'[liquidation_fetch] PUBLIC ERROR: {e}\n')
        return {'symbol': symbol, 'total_24h_usd': 0, 'long_24h_usd': 0, 'short_24h_usd': 0, 'ts': 0}


if __name__ == '__main__':
    for sym in ['BTCUSDT', 'ETHUSDT']:
        r = fetch_liquidation_24h(sym)
        print(f'{sym}: total=${r["total_24h_usd"]:,.0f} long=${r["long_24h_usd"]:,.0f} short=${r["short_24h_usd"]:,.0f} ratio={r["long_short_ratio"]:.2f}')
