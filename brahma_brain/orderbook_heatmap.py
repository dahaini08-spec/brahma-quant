"""
orderbook_heatmap.py — 订单簿大单挂单分析
设计院自主决策 2026-06-29

职责：
  1. 拉取 Binance fapi 500档订单簿
  2. 识别大单挂单聚集区（流动性墙）
  3. 计算买卖压力不对称
  4. 输出结构化数据供 brahma_core s7 维度使用

数据源：Binance fapi/v1/depth（免费，无需API Key）
替代能力：CoinAnk套餐4「挂单流动性热力图」≈85%
"""

import requests
import time
from typing import Optional

_CACHE: dict = {}
_CACHE_TTL = 60  # 秒


def get_orderbook_heatmap(symbol: str, levels: int = 500) -> dict:
    """
    拉取并分析订单簿大单挂单分布

    返回：
      big_bid_walls  : [(price, qty_btc), ...] 大买单墙（降序）
      big_ask_walls  : [(price, qty_btc), ...] 大卖单墙（升序）
      nearest_bid_wall : 最近大买单价位
      nearest_ask_wall : 最近大卖单价位
      bid_wall_usd   : 买单墙总USD
      ask_wall_usd   : 卖单墙总USD
      pressure_bias  : BUY_WALL / SELL_WALL / NEUTRAL
      imbalance_pct  : 买卖不对称度（正=买压强）
      mid_price      : 中间价
    """
    cache_key = f'ob_{symbol}'
    now = time.time()
    if cache_key in _CACHE and now - _CACHE[cache_key]['ts'] < _CACHE_TTL:
        return _CACHE[cache_key]['data']

    try:
        url = f'https://fapi.binance.com/fapi/v1/depth?symbol={symbol}&limit={levels}'
        r = requests.get(url, timeout=8)
        r.raise_for_status()
        raw = r.json()

        bids = [(float(p), float(q)) for p, q in raw.get('bids', [])]
        asks = [(float(p), float(q)) for p, q in raw.get('asks', [])]

        if not bids or not asks:
            return _empty_result(symbol)

        mid_price = (bids[0][0] + asks[0][0]) / 2

        # 动态大单门槛：取前500档数量的前10%均值
        all_qtys = [q for _, q in bids + asks]
        all_qtys.sort(reverse=True)
        top10_avg = sum(all_qtys[:max(1, len(all_qtys)//10)]) / max(1, len(all_qtys)//10)
        big_threshold = max(top10_avg * 2.0, 0.5)  # 至少0.5 BTC

        big_bids = [(p, q) for p, q in bids if q >= big_threshold]
        big_asks = [(p, q) for p, q in asks if q >= big_threshold]

        # 最近大单墙
        nearest_bid = big_bids[0] if big_bids else (bids[0][0] * 0.99, 0)
        nearest_ask = big_asks[0] if big_asks else (asks[0][0] * 1.01, 0)

        # 总USD
        bid_wall_usd = sum(p * q for p, q in big_bids)
        ask_wall_usd = sum(p * q for p, q in big_asks)

        # 不对称度
        total = bid_wall_usd + ask_wall_usd
        imbalance = (bid_wall_usd - ask_wall_usd) / total if total > 0 else 0

        if imbalance > 0.15:
            bias = 'BUY_WALL'
        elif imbalance < -0.15:
            bias = 'SELL_WALL'
        else:
            bias = 'NEUTRAL'

        # 价格区间分桶（清算热力图替代）
        price_range = mid_price * 0.05  # ±5% 范围
        bucket_size = mid_price * 0.005  # 每档0.5%
        n_buckets = int(price_range * 2 / bucket_size)
        price_lo = mid_price - price_range
        buckets = {}
        for p, q in bids + asks:
            if price_lo <= p <= mid_price + price_range:
                bucket_idx = int((p - price_lo) / bucket_size)
                buckets[bucket_idx] = buckets.get(bucket_idx, 0) + q * p

        # 找最大流动性区间
        max_bucket = max(buckets, key=lambda k: buckets[k]) if buckets else 0
        max_liq_price = price_lo + max_bucket * bucket_size + bucket_size / 2
        max_liq_usd = buckets.get(max_bucket, 0)

        result = {
            'symbol': symbol,
            'mid_price': round(mid_price, 4),
            'big_threshold_qty': round(big_threshold, 3),
            'big_bid_walls': [(round(p, 2), round(q, 3)) for p, q in big_bids[:5]],
            'big_ask_walls': [(round(p, 2), round(q, 3)) for p, q in big_asks[:5]],
            'nearest_bid_wall': round(nearest_bid[0], 2),
            'nearest_ask_wall': round(nearest_ask[0], 2),
            'bid_wall_usd': round(bid_wall_usd, 0),
            'ask_wall_usd': round(ask_wall_usd, 0),
            'pressure_bias': bias,
            'imbalance_pct': round(imbalance * 100, 1),
            'max_liq_zone_price': round(max_liq_price, 2),
            'max_liq_zone_usd': round(max_liq_usd, 0),
            'ts': now,
            'source': 'binance_depth',
        }

        _CACHE[cache_key] = {'ts': now, 'data': result}
        return result

    except Exception as e:
        return _empty_result(symbol, error=str(e))


def _empty_result(symbol: str, error: str = '') -> dict:
    return {
        'symbol': symbol,
        'mid_price': 0,
        'big_threshold_qty': 0,
        'big_bid_walls': [],
        'big_ask_walls': [],
        'nearest_bid_wall': 0,
        'nearest_ask_wall': 0,
        'bid_wall_usd': 0,
        'ask_wall_usd': 0,
        'pressure_bias': 'NEUTRAL',
        'imbalance_pct': 0,
        'max_liq_zone_price': 0,
        'max_liq_zone_usd': 0,
        'ts': time.time(),
        'source': 'error',
        'error': error,
    }


def get_ob_score(symbol: str, signal_dir: str) -> tuple[int, str]:
    """
    返回 (加分, 描述) 供 brahma_core 调用

    做空时：卖单墙强 → 正确方向加分，买单墙强 → 扣分
    做多时：买单墙强 → 正确方向加分，卖单墙强 → 扣分
    """
    data = get_orderbook_heatmap(symbol)
    if data.get('source') == 'error':
        return 0, 'ob_error'

    bias = data['pressure_bias']
    imb = data['imbalance_pct']

    if signal_dir == 'SHORT':
        if bias == 'SELL_WALL':
            pts = min(4, int(abs(imb) / 10) + 2)
            return pts, f'卖单墙强({imb:.0f}%) +{pts}'
        elif bias == 'BUY_WALL':
            pts = -min(3, int(abs(imb) / 15) + 1)
            return pts, f'买单墙阻力({imb:.0f}%) {pts}'
        return 0, f'订单簿中性({imb:.0f}%)'

    elif signal_dir == 'LONG':
        if bias == 'BUY_WALL':
            pts = min(4, int(abs(imb) / 10) + 2)
            return pts, f'买单墙强({imb:.0f}%) +{pts}'
        elif bias == 'SELL_WALL':
            pts = -min(3, int(abs(imb) / 15) + 1)
            return pts, f'卖单墙压制({imb:.0f}%) {pts}'
        return 0, f'订单簿中性({imb:.0f}%)'

    return 0, 'unknown_dir'


if __name__ == '__main__':
    import json
    result = get_orderbook_heatmap('BTCUSDT')
    print(json.dumps(result, indent=2, ensure_ascii=False))
    score, desc = get_ob_score('BTCUSDT', 'SHORT')
    print(f'\n做空评分: {score:+d}  {desc}')
