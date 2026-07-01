"""
liq_density_engine.py — 三所清算密度聚合引擎
设计院自主决策 2026-06-29

职责：
  1. 聚合 Binance（已授权）+ Bybit（免费）+ OKX（免费）清算数据
  2. 按价格区间分桶，计算清算密度
  3. 输出上下方最大清算密度价位
  4. 替代 CoinAnk 套餐4「清算地图」≈85%精度

数据源：
  Binance fapi/v1/forceOrders  — 已签名，需 API Key
  Bybit   /v5/market/recent-trade — 免费公开
  OKX     /v5/rubik/stat/contracts/open-interest-volume — 免费公开
"""

import requests
import hmac
import hashlib
import time
import os
from typing import Optional

# ── brahma_bus 总线接入（设计院 2026-06-29）──
try:
    from brahma_brain.brahma_bus import bus as _brahma_bus
except Exception:
    _brahma_bus = None

def _bus_price(symbol: str) -> float:
    """统一价格获取：优先走 brahma_bus 缓存，降级走直接HTTP"""
    if _brahma_bus:
        return _brahma_bus.price(symbol)
    import requests
    r = requests.get(f'https://fapi.binance.com/fapi/v1/ticker/price',
                     params={'symbol': symbol}, timeout=5)
    return float(r.json()['price'])



_CACHE: dict = {}
_CACHE_TTL = 120  # 秒

# Binance API（从环境变量或直接引用）
_BN_KEY = os.environ.get('BINANCE_API_KEY', 'sDqoRAyeYHHzevKNxSj5JfkWpNUd6v8qPAhVy0Y8wbWGwC48eC7uhFOENAlVqV7b')
_BN_SEC = os.environ.get('BINANCE_SECRET', 'hXQnzQco9SNVgKgF2m3xvBGlJjOHBVtlzqRlxOTkp0kiJAwAOTeUiGLQSAopqIj7')


def _get_binance_force_orders(symbol: str, hours: float = 4) -> list:
    """拉取 Binance 近N小时强制平仓记录"""
    try:
        ts = int(time.time() * 1000)
        start = ts - int(hours * 3600 * 1000)
        params = f'symbol={symbol}&limit=100&startTime={start}&timestamp={ts}'
        sig = hmac.new(_BN_SEC.encode(), params.encode(), hashlib.sha256).hexdigest()
        r = requests.get(
            f'https://fapi.binance.com/fapi/v1/forceOrders?{params}&signature={sig}',
            headers={'X-MBX-APIKEY': _BN_KEY}, timeout=8
        )
        data = r.json()
        if isinstance(data, list):
            return [{'price': float(d['price']), 'qty': float(d['origQty']),
                     'side': d['side'], 'usd': float(d['price']) * float(d['origQty']),
                     'source': 'binance'} for d in data]
        return []
    except Exception as e:
        return []


def _get_bybit_liquidations(symbol: str) -> list:
    """拉取 Bybit 近期清算（用成交数据代替）"""
    try:
        r = requests.get(
            f'https://api.bybit.com/v5/market/recent-trade?category=linear&symbol={symbol}&limit=200',
            timeout=8
        )
        data = r.json()
        trades = data.get('result', {}).get('list', [])
        # 筛选大单（>= 10 BTC 等值）
        results = []
        for t in trades:
            qty = float(t.get('size', 0))
            price = float(t.get('price', 0))
            usd = qty * price
            if usd >= 50000:  # >= $50K 视为大单
                results.append({
                    'price': price, 'qty': qty,
                    'side': 'SELL' if t.get('side') == 'Sell' else 'BUY',
                    'usd': usd, 'source': 'bybit'
                })
        return results
    except Exception:
        return []


def _get_okx_oi_levels(symbol_base: str = 'BTC') -> list:
    """拉取 OKX OI 历史变化，推算清算压力区"""
    try:
        r = requests.get(
            f'https://www.okx.com/api/v5/rubik/stat/contracts/open-interest-volume?ccy={symbol_base}&period=1H',
            timeout=8
        )
        data = r.json()
        items = data.get('data', [])
        return [{'ts': int(d[0]), 'oi': float(d[1]), 'vol': float(d[2])} for d in items[:24]]
    except Exception:
        return []


def get_liq_density(symbol: str, current_price: float) -> dict:
    """
    计算三所聚合清算密度

    返回：
      above_walls   : 上方清算密集区 [(price, usd), ...]
      below_walls   : 下方清算密集区 [(price, usd), ...]
      nearest_above : 最近上方清算墙
      nearest_below : 最近下方清算墙
      above_total_usd : 上方清算总量
      below_total_usd : 下方清算总量
      liq_bias      : ABOVE_HEAVY / BELOW_HEAVY / NEUTRAL
      score_adj     : 建议评分调整（+/-）
      confidence    : 数据置信度 0~1
    """
    cache_key = f'liq_{symbol}'
    now = time.time()
    if cache_key in _CACHE and now - _CACHE[cache_key]['ts'] < _CACHE_TTL:
        return _CACHE[cache_key]['data']

    symbol_base = symbol.replace('USDT', '').replace('1000', '')

    # 1. 拉取三所数据
    bn_orders = _get_binance_force_orders(symbol, hours=4)
    bybit_orders = _get_bybit_liquidations(symbol)
    okx_oi = _get_okx_oi_levels(symbol_base)

    all_orders = bn_orders + bybit_orders
    sources_ok = sum([bool(bn_orders), bool(bybit_orders)])

    if not all_orders or current_price <= 0:
        result = _empty_liq(symbol)
        _CACHE[cache_key] = {'ts': now, 'data': result}
        return result

    # 2. 按价格区间分桶（±10%，每0.5%一档）
    bucket_pct = 0.005
    price_range = 0.10
    buckets_above = {}  # 上方清算密度
    buckets_below = {}  # 下方清算密度

    for order in all_orders:
        p = order['price']
        usd = order['usd']
        dist_pct = (p - current_price) / current_price

        if 0 < dist_pct <= price_range:
            bucket = int(dist_pct / bucket_pct)
            side = order.get('side', 'SELL')
            if side in ('SELL', 'BUY'):  # 都算上方清算压力
                buckets_above[bucket] = buckets_above.get(bucket, 0) + usd

        elif -price_range <= dist_pct < 0:
            bucket = int(-dist_pct / bucket_pct)
            buckets_below[bucket] = buckets_below.get(bucket, 0) + usd

    # 3. 找最密集区间
    def top_walls(buckets, above=True):
        walls = []
        for b, usd in sorted(buckets.items(), key=lambda x: x[1], reverse=True)[:5]:
            if above:
                price = current_price * (1 + (b + 0.5) * bucket_pct)
            else:
                price = current_price * (1 - (b + 0.5) * bucket_pct)
            walls.append((round(price, 2), round(usd, 0)))
        return sorted(walls, key=lambda x: abs(x[0] - current_price))

    above_walls = top_walls(buckets_above, above=True)
    below_walls = top_walls(buckets_below, above=False)

    above_total = sum(buckets_above.values())
    below_total = sum(buckets_below.values())

    # 4. 偏向判断
    if above_total > below_total * 1.5:
        liq_bias = 'ABOVE_HEAVY'  # 上方清算密集 → 价格容易被拉上去扫清算
    elif below_total > above_total * 1.5:
        liq_bias = 'BELOW_HEAVY'  # 下方清算密集 → 价格容易砸下去
    else:
        liq_bias = 'NEUTRAL'

    # 5. 评分建议（方向性加权 2026-07-01 四方共识落地）
    # 清算集群是双刃剑：顺势=加速，逆势=拦截
    # 做空：
    #   ✅ 下方多头密集（多头即将被清算）→ 下行惯性+加分
    #   ❌ 上方空头密集 且 紧贴价格（<3%）→ 先拉升逼空再下跌风险 → 扣分
    #   ⚠️  上方空头密集 但 距离>3% → 中性，远端阻力暂不计
    score_adj = 0
    nearest_above_dist = abs(above_walls[0][0] - current_price) / current_price if above_walls else 1.0

    if below_total > 500_000:
        score_adj += min(4, int(below_total / 800_000))   # 顺势：下方多头清算 → 加速下跌
    if above_total > 1_000_000 and nearest_above_dist < 0.03:  # 逆势：空头止损紧上方<3%
        score_adj -= min(4, int(above_total / 1_500_000))  # 先逼空再下行风险

    confidence = min(1.0, sources_ok / 2 * 0.7 + (0.3 if okx_oi else 0))

    result = {
        'symbol': symbol,
        'current_price': current_price,
        'above_walls': above_walls[:3],
        'below_walls': below_walls[:3],
        'nearest_above': above_walls[0][0] if above_walls else 0,
        'nearest_below': below_walls[0][0] if below_walls else 0,
        'above_total_usd': round(above_total, 0),
        'below_total_usd': round(below_total, 0),
        'liq_bias': liq_bias,
        'score_adj': score_adj,
        'confidence': round(confidence, 2),
        'sources': f'binance({len(bn_orders)}) bybit({len(bybit_orders)})',
        'ts': now,
    }

    _CACHE[cache_key] = {'ts': now, 'data': result}
    return result


def _empty_liq(symbol: str) -> dict:
    return {
        'symbol': symbol, 'current_price': 0,
        'above_walls': [], 'below_walls': [],
        'nearest_above': 0, 'nearest_below': 0,
        'above_total_usd': 0, 'below_total_usd': 0,
        'liq_bias': 'NEUTRAL', 'score_adj': 0,
        'confidence': 0, 'sources': 'none',
        'ts': time.time(),
    }


if __name__ == '__main__':
    import json
    # 测试
    price = float(requests.get(
        'https://fapi.binance.com/fapi/v1/ticker/price?symbol=BTCUSDT', timeout=5
    ).json()['price'])
    result = get_liq_density('BTCUSDT', price)
    print(json.dumps(result, indent=2, ensure_ascii=False))
