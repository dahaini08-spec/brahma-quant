#!/usr/bin/env python3
"""
梵天OI持仓成本热力图 [封印 2026-10-26 苏摩111]
接入位置：brahma_state_refresh.py → 写入data/liq_heatmap_{sym}.json
功能：从fapi拉取OI历史+价格历史，推算每个价位区间的净持仓成本
= 当前654bytes热力图→完整持仓成本地图（买墙/卖压识别）
= 40年老手"成本地图直觉"的数学化
"""
import json, time, urllib.request
from pathlib import Path
from collections import defaultdict

BASE = Path(__file__).parent.parent

def _fetch(url: str):
    try:
        with urllib.request.urlopen(url, timeout=6) as r:
            return json.loads(r.read())
    except Exception:
        return None

def build_heatmap(symbol: str, bucket_pct: float = 0.3) -> dict:
    """
    构建持仓成本热力图。
    算法：
      1. 拉取96根1H K线（4天）+ 每小时OI变化
      2. 每小时OI净变化 × 该小时均价 = 该价位区间新增持仓成本
      3. 按bucket_pct（0.3%）分桶，累加净多/空持仓量
      4. 输出：每个价位区间的持仓分布
    """
    sym = symbol.upper()
    if not sym.endswith('USDT'):
        sym += 'USDT'

    # 拉取1H K线（96根 = 4天）
    klines = _fetch(f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval=1h&limit=96")
    # 拉取1H OI历史（96根）
    oi_hist = _fetch(f"https://fapi.binance.com/futures/data/openInterestHist?symbol={sym}&period=1h&limit=96")
    # 当前价
    px_data = _fetch(f"https://fapi.binance.com/fapi/v1/ticker/price?symbol={sym}")
    current_px = float(px_data['price']) if px_data else 0

    if not klines or not oi_hist or not current_px:
        return {}

    # 对齐时间戳
    kline_map = {}
    for k in klines:
        ts = int(k[0]) // 1000  # 秒级
        kline_map[ts] = {
            'open':  float(k[1]),
            'high':  float(k[2]),
            'low':   float(k[3]),
            'close': float(k[4]),
            'mid':   (float(k[2]) + float(k[3])) / 2,
        }

    oi_map = {}
    for o in oi_hist:
        ts = int(o['timestamp']) // 1000
        oi_map[ts] = float(o['sumOpenInterest'])

    # 计算每小时OI变化量及对应价位
    oi_ts = sorted(oi_map.keys())
    buckets = defaultdict(lambda: {'long_oi': 0.0, 'short_oi': 0.0, 'count': 0})

    for i in range(1, len(oi_ts)):
        ts_cur = oi_ts[i]
        ts_prv = oi_ts[i-1]
        d_oi = oi_map[ts_cur] - oi_map[ts_prv]
        if abs(d_oi) < 0.001:
            continue

        # 找对应K线价格
        kl = kline_map.get(ts_cur) or kline_map.get(ts_prv)
        if not kl:
            continue

        # 按bucket_pct分桶（基于当前价）
        mid_price = kl['mid']
        bucket_size = current_px * bucket_pct / 100
        bucket_idx = round(mid_price / bucket_size) * bucket_size

        # OI增加 = 新建仓；减少 = 平仓（不确定方向，用价格推断）
        # 价格上涨期间OI增 = 多头建仓；下跌期间OI增 = 空头建仓
        is_up = kl['close'] >= kl['open']
        if d_oi > 0:
            if is_up:
                buckets[bucket_idx]['long_oi']  += d_oi
            else:
                buckets[bucket_idx]['short_oi'] += d_oi
        else:
            # 平仓：减少相应方向
            if is_up:
                buckets[bucket_idx]['short_oi'] += abs(d_oi) * 0.5
            else:
                buckets[bucket_idx]['long_oi']  += abs(d_oi) * 0.5
        buckets[bucket_idx]['count'] += 1

    # 构建热力图列表（按价格排序）
    heatmap = []
    for price_lvl in sorted(buckets.keys()):
        b = buckets[price_lvl]
        net = b['long_oi'] - b['short_oi']
        total = b['long_oi'] + b['short_oi']
        dist_pct = (price_lvl - current_px) / current_px * 100 if current_px > 0 else 0
        heatmap.append({
            'price':     round(price_lvl, 1),
            'dist_pct':  round(dist_pct, 2),
            'long_oi':   round(b['long_oi'], 2),
            'short_oi':  round(b['short_oi'], 2),
            'net_oi':    round(net, 2),
            'total_oi':  round(total, 2),
            'bias':      'LONG' if net > 0 else 'SHORT',
            'count':     b['count'],
        })

    # 找关键支撑/阻力（多头净持仓最密集区=买墙，空头最密集=卖压）
    long_zones  = sorted([h for h in heatmap if h['net_oi'] > 0],  key=lambda x: x['long_oi'],  reverse=True)[:3]
    short_zones = sorted([h for h in heatmap if h['net_oi'] < 0], key=lambda x: x['short_oi'], reverse=True)[:3]

    result = {
        'symbol':      symbol.upper(),
        'ts':          time.time(),
        'dt':          time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'current_px':  current_px,
        'bucket_pct':  bucket_pct,
        'heatmap':     heatmap,
        'buy_walls':   [{'price': z['price'], 'long_oi': z['long_oi'], 'dist_pct': z['dist_pct']} for z in long_zones],
        'sell_walls':  [{'price': z['price'], 'short_oi': z['short_oi'], 'dist_pct': z['dist_pct']} for z in short_zones],
        'total_buckets': len(heatmap),
        'data_source': 'fapi_oi_hist_klines_reconstruction',
    }

    # 写入热力图文件
    out_path = BASE / 'data' / f"liq_heatmap_{symbol.lower()}usdt.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False))
    return result

if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--symbols', nargs='+', default=['BTC', 'ETH'])
    args = p.parse_args()
    for sym in args.symbols:
        print(f"\n构建 {sym} 热力图...")
        r = build_heatmap(sym)
        if r:
            print(f"  完成: {r['total_buckets']}个价位桶 当前${r['current_px']:,.1f}")
            buy_fmt = [f"{z['price']:,.0f}({z['dist_pct']:+.1f}%)" for z in r['buy_walls']]
            sell_fmt= [f"{z['price']:,.0f}({z['dist_pct']:+.1f}%)" for z in r['sell_walls']]
            print(f"  买墙TOP3: {buy_fmt}")
            print(f"  卖压TOP3: {sell_fmt}")
            size = (BASE/'data'/f"liq_heatmap_{sym.lower()}usdt.json").stat().st_size
            print(f"  文件大小: {size:,}bytes（原654bytes→完整热力图）")
        else:
            print(f"  ❌ 构建失败")
