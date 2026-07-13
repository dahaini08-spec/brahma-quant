#!/usr/bin/env python3
"""
P0: 实时清算热力图 — liq_heatmap.py
设计院 v5.6 | 2026-07-13

策略：
  1. Binance 订单簿深度 → 估算各价位杠杆持仓分布
  2. 近期强平记录 → 验证热点区域
  3. 多杠杆倍数清算价位矩阵 → 识别高密度清算区
输出: 上下方最危险清算区 + 梵天评分贡献
"""
import sys, os, requests, json, time, hmac, hashlib
from pathlib import Path
from collections import defaultdict

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))

KEY = os.environ.get('BINANCE_API_KEY', 'sDqoRAyeYHHzevKNxSj5JfkWpNUd6v8qPAhVy0Y8wbWGwC48eC7uhFOENAlVqV7b')
SEC = os.environ.get('BINANCE_SECRET',  'hXQnzQco9SNVgKgF2m3xvBGlJjOHBVtlzqRlxOTkp0kiJAwAOTeUiGLQSAopqIj7')
HDR = {'X-MBX-APIKEY': KEY}


def _signed(params: dict) -> str:
    ts = int(time.time() * 1000)
    params['timestamp'] = ts
    qs = '&'.join(f'{k}={v}' for k, v in params.items())
    sig = hmac.new(SEC.encode(), qs.encode(), hashlib.sha256).hexdigest()
    return qs + '&signature=' + sig


def get_liq_heatmap(sym: str = 'BTCUSDT') -> dict:
    """
    构建清算热力图
    核心逻辑：
      - 从订单簿深度估算大额挂单聚集区（止损密集区）
      - 基于当前价格 × 多杠杆倍数 → 计算各杠杆空头/多头的清算价格
      - 识别上方空头清算密集区（轧空目标）和下方多头清算密集区（洗盘目标）
    """
    try:
        # 1. 实时价格
        px = float(requests.get(
            'https://fapi.binance.com/fapi/v1/ticker/price',
            params={'symbol': sym}, timeout=5
        ).json()['price'])

        # 2. 订单簿深度（获取大额挂单聚集区）
        depth = requests.get(
            'https://fapi.binance.com/fapi/v1/depth',
            params={'symbol': sym, 'limit': 100}, timeout=8
        ).json()

        bids = [(float(p), float(q)) for p, q in depth.get('bids', [])]
        asks = [(float(p), float(q)) for p, q in depth.get('asks', [])]

        # 3. 多杠杆清算价位矩阵
        levers = [2, 3, 5, 10, 20, 50]
        short_liq = {}  # 空头被轧（价格上涨触发）
        long_liq  = {}  # 多头被轧（价格下跌触发）

        for lev in levers:
            # 空头清算价：做空价格 × (1 + 1/lev × 维持保证金修正≈1.1)
            sliq = round(px * (1 + 1.0 / lev), 1)
            # 多头清算价：做多价格 × (1 - 1/lev × 修正)
            lliq = round(px * (1 - 1.0 / lev), 1)
            short_liq[lev] = sliq
            long_liq[lev]  = lliq

        # 4. 订单簿大额挂单识别（止损代理）
        ask_clusters = defaultdict(float)
        bid_clusters = defaultdict(float)
        bucket_size  = px * 0.005  # 0.5%为一档

        for p, q in asks:
            b = round(p / bucket_size) * bucket_size
            ask_clusters[b] += q * p  # USD价值

        for p, q in bids:
            b = round(p / bucket_size) * bucket_size
            bid_clusters[b] += q * p

        # Top3高密度挂单区
        top_ask_zones = sorted(ask_clusters.items(), key=lambda x: -x[1])[:3]
        top_bid_zones = sorted(bid_clusters.items(), key=lambda x: -x[1])[:3]

        # 5. 梵天评分贡献
        # 上方最近清算密集区（3x空头）距离
        nearest_short_liq = short_liq[3]   # 3x空头最近
        nearest_long_liq  = long_liq[3]    # 3x多头最近
        dist_short = round((nearest_short_liq - px) / px * 100, 2)
        dist_long  = round((px - nearest_long_liq) / px * 100, 2)

        # 评分逻辑：
        # 上方近距空头清算池 → 做市商有动力拉价轧空 → 多头加分
        # 下方近距多头清算池 → 做市商有动力压价洗盘 → 多头风险
        liq_bull_score = 0
        liq_bear_score = 0
        if dist_short < 2.0:   liq_bull_score += 8   # 近距空头清算 → 拉升诱因强
        elif dist_short < 3.5: liq_bull_score += 4
        if dist_long < 2.0:    liq_bear_score += 8   # 近距多头清算 → 下压风险
        elif dist_long < 3.5:  liq_bear_score += 4

        result = {
            'symbol'           : sym,
            'price'            : px,
            'short_liq_map'    : short_liq,   # 各杠杆空头清算价
            'long_liq_map'     : long_liq,    # 各杠杆多头清算价
            'nearest_short_liq': nearest_short_liq,
            'nearest_long_liq' : nearest_long_liq,
            'dist_to_short_liq': dist_short,
            'dist_to_long_liq' : dist_long,
            'top_ask_clusters' : [(round(p, 1), round(v, 0)) for p, v in top_ask_zones],
            'top_bid_clusters' : [(round(p, 1), round(v, 0)) for p, v in top_bid_zones],
            'liq_bull_score'   : liq_bull_score,
            'liq_bear_score'   : liq_bear_score,
            'ts'               : time.time(),
        }

        # 缓存到data/
        cache = BASE / 'data' / f'liq_heatmap_{sym}.json'
        cache.write_text(json.dumps(result, indent=2))
        return result

    except Exception as e:
        return {'error': str(e), 'symbol': sym}


def format_report(r: dict) -> str:
    if 'error' in r:
        return f'⚠️ liq_heatmap error: {r["error"]}'
    px = r['price']
    lines = [
        f'🔥 清算热力图 — {r["symbol"]} ${px:,.1f}',
        '',
        '【上方空头清算区（轧空目标）】',
    ]
    for lev, liq in sorted(r['short_liq_map'].items()):
        dist = round((liq - px) / px * 100, 2)
        bar = '█' * max(1, int(20 / lev))
        lines.append(f'  {lev:>2}x空头清算: ${liq:>10,.1f} (+{dist:.2f}%) {bar}')

    lines += ['', '【下方多头清算区（洗盘风险）】']
    for lev, liq in sorted(r['long_liq_map'].items()):
        dist = round((px - liq) / px * 100, 2)
        bar = '█' * max(1, int(20 / lev))
        lines.append(f'  {lev:>2}x多头清算: ${liq:>10,.1f} (-{dist:.2f}%) {bar}')

    lines += [
        '',
        '【订单簿大额挂单密集区（止损代理）】',
        '  上方阻力(Ask):',
    ]
    for p, v in r['top_ask_clusters']:
        lines.append(f'    ${p:,.1f}  ${v:,.0f}')
    lines.append('  下方支撑(Bid):')
    for p, v in r['top_bid_clusters']:
        lines.append(f'    ${p:,.1f}  ${v:,.0f}')

    lines += [
        '',
        f'【梵天评分贡献】',
        f'  近距空头清算(3x={r["nearest_short_liq"]:,.1f}, +{r["dist_to_short_liq"]}%): '
        f'多头加分={r["liq_bull_score"]}',
        f'  近距多头清算(3x={r["nearest_long_liq"]:,.1f}, -{r["dist_to_long_liq"]}%): '
        f'空头风险={r["liq_bear_score"]}',
    ]
    return '\n'.join(lines)


if __name__ == '__main__':
    syms = sys.argv[1:] if len(sys.argv) > 1 else ['BTCUSDT', 'ETHUSDT']
    for sym in syms:
        r = get_liq_heatmap(sym)
        print(format_report(r))
        print()
