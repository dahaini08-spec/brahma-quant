"""
orderbook_fetch.py — 盘口深度按需快照（非常驻进程）
[2026-09-22 苏摩111封印] 分析时拉取一次，写入缓存，VETERAN VR09消费

Binance API: /fapi/v1/depth (免费)
人工分析看的是快照不是流，不需要WebSocket常驻
"""
import json, time, requests
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / 'data'
BINANCE_API = 'https://fapi.binance.com'


def fetch_orderbook_snapshot(symbol: str = 'BTCUSDT', depth: int = 20) -> dict:
    """
    分析时按需拉取盘口快照，写入缓存文件。
    返回:
        {
            'symbol': str,
            'bid_volume': float,         # 买盘总挂单量(USD)
            'ask_volume': float,         # 卖盘总挂单量(USD)
            'imbalance': float,          # 不平衡度 (>0.5=卖盘多, <0.5=买盘多)
            'bid_ask_ratio': float,      # 买/卖比
            'spread_bps': float,         # 买卖价差(基点)
            'wall_bid': float,           # 最大买盘墙
            'wall_ask': float,           # 最大卖盘墙
            'ts': float,
        }
    """
    try:
        resp = requests.get(
            f'{BINANCE_API}/fapi/v1/depth',
            params={'symbol': symbol, 'limit': depth},
            timeout=10
        )
        data = resp.json()
        
        bids = data.get('bids', [])  # [[price, qty], ...]
        asks = data.get('asks', [])
        
        if not bids or not asks:
            return {'symbol': symbol, 'imbalance': 0.5, 'bid_ask_ratio': 1.0, 'ts': 0}
        
        # 计算买卖盘总挂单量(USD)
        bid_volume = sum(float(b[0]) * float(b[1]) for b in bids)
        ask_volume = sum(float(a[0]) * float(a[1]) for a in asks)
        
        total = bid_volume + ask_volume
        imbalance = ask_volume / total if total > 0 else 0.5  # >0.5=卖盘多
        
        bid_ask_ratio = bid_volume / ask_volume if ask_volume > 0 else 999
        
        # 买卖价差
        best_bid = float(bids[0][0]) if bids else 0
        best_ask = float(asks[0][0]) if asks else 0
        spread_bps = (best_ask - best_bid) / best_ask * 10000 if best_ask > 0 else 0
        
        # 最大盘口墙
        wall_bid = max((float(b[0]) * float(b[1]) for b in bids), default=0)
        wall_ask = max((float(a[0]) * float(a[1]) for a in asks), default=0)
        
        result = {
            'symbol': symbol,
            'bid_volume': bid_volume,
            'ask_volume': ask_volume,
            'imbalance': imbalance,
            'bid_ask_ratio': bid_ask_ratio,
            'spread_bps': spread_bps,
            'wall_bid': wall_bid,
            'wall_ask': wall_ask,
            'ts': time.time(),
        }
        
        cache_file = DATA_DIR / f'orderbook_{symbol.lower()}.json'
        cache_file.parent.mkdir(exist_ok=True)
        json.dump(result, open(cache_file, 'w'), indent=2)
        
        return result
        
    except Exception as e:
        import sys
        sys.stderr.write(f'[orderbook_fetch] ERROR: {e}\n')
        return {'symbol': symbol, 'imbalance': 0.5, 'bid_ask_ratio': 1.0, 'ts': 0}


if __name__ == '__main__':
    for sym in ['BTCUSDT', 'ETHUSDT']:
        r = fetch_orderbook_snapshot(sym)
        print(f'{sym}: bid_vol=${r["bid_volume"]:,.0f} ask_vol=${r["ask_volume"]:,.0f} imbalance={r["imbalance"]:.2f} ratio={r["bid_ask_ratio"]:.2f}')
