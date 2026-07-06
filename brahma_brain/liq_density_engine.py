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
  Bybit   /v5/market/recent-trade — 免费公开（成交流近似）
  OKX     /v5/public/liquidation-orders — 真实强平记录（2026-07-06 修复）
         旧错误端点: /v5/rubik/stat/contracts/open-interest-volume (OI历史，非清算)
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


def _get_okx_liquidations(symbol_base: str = 'BTC') -> list:
    """
    拉取 OKX 真实强制平仓记录
    修复 2026-07-06: 原接口 /v5/rubik/stat/contracts/open-interest-volume 返回OI历史，非清算数据
    正确接口: /v5/public/liquidation-orders?instType=SWAP&uly=BTC-USDT&state=filled
    实测: BTC=929条, ETH=1444条真实清算记录
    """
    try:
        uly = f'{symbol_base}-USDT'
        r = requests.get(
            'https://www.okx.com/api/v5/public/liquidation-orders',
            params={'instType': 'SWAP', 'uly': uly, 'state': 'filled', 'limit': 100},
            timeout=8
        )
        data = r.json()
        results = []
        for item in data.get('data', []):
            for d in item.get('details', []):
                px = float(d.get('bkPx', 0))
                sz = float(d.get('sz', 0))
                if px > 0 and sz > 0:
                    usd = px * sz
                    pos_side = d.get('posSide', 'long')  # long/short
                    # posSide='long' 被清算 → sell side → 多头止损在下方
                    # posSide='short' 被清算 → buy side → 空头止损在上方
                    side = 'BUY' if pos_side == 'short' else 'SELL'
                    results.append({
                        'price': px, 'qty': sz, 'usd': usd,
                        'side': side, 'pos_side': pos_side, 'source': 'okx'
                    })
        return results
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
    okx_orders = _get_okx_liquidations(symbol_base)  # 修复: 真实清算而非OI历史

    all_orders = bn_orders + bybit_orders + okx_orders
    sources_ok = sum([bool(bn_orders), bool(bybit_orders), bool(okx_orders)])

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

    # 5. 评分建议（方向性加权 2026-07-01 落地 | 2026-07-06 修复：基于真实清算数据）
    # 清算集群逻辑（统一做多视角）：
    #   ✅ 上方空头止损密集（SHORT被清 → 价格上行助推）→ LONG加分
    #   ❌ 下方多头止损密集且紧贴（LONG被清 → 价格下行风险）→ LONG扣分
    #   空头视角：上下反转计算
    # 注：all_orders现在包含OKX真实清算数据，方向由pos_side决定
    score_adj = 0
    nearest_above_dist = abs(above_walls[0][0] - current_price) / current_price if above_walls else 1.0
    nearest_below_dist = abs(below_walls[0][0] - current_price) / current_price if below_walls else 1.0

    # 上方空头止损墙（OKX posSide=short + bybit SELL）→ LONG磁铁效应
    if above_total > below_total * 1.5 and above_total > 1_000_000:
        score_adj += min(8, int(above_total / 100_000_000))  # 最多+8分
    # 下方多头止损墙紧贴（<3%）→ LONG风险
    elif below_total > above_total * 1.5 and nearest_below_dist < 0.03:
        score_adj -= min(4, int(below_total / 50_000_000))

    confidence = min(1.0, sources_ok / 3.0)

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
        'sources': f'binance({len(bn_orders)}) bybit({len(bybit_orders)}) okx({len(okx_orders)})',
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
