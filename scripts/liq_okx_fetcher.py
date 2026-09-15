#!/usr/bin/env python3
"""
liq_okx_fetcher.py — OKX清算数据REST采集器
[2026-09-15 苏摩111] 替代liqmap_collector常驻WS

优势：
  - OKX liquidation-orders API返回真实强平记录（Binance WS推送=0）
  - REST轮询比常驻WS省资源（无需维持连接）
  - 每2h采集一次，足够用于s7清算评分

输出：
  data/liqmap_raw.jsonl — 追加写入真实强平记录
  data/liqmap_heatmap.json — 聚合热力图
"""

import json, time, sys
from pathlib import Path
from collections import defaultdict
import requests

BASE = Path(__file__).parent.parent
RAW_FILE  = BASE / "data" / "liqmap_raw.jsonl"
HEAT_FILE = BASE / "data" / "liqmap_heatmap.json"

# OKX标的映射
OKX_SYMBOLS = {
    'BTCUSDT': 'BTC-USDT',
    'ETHUSDT': 'ETH-USDT',
    'SOLUSDT': 'SOL-USDT',
    'XRPUSDT': 'XRP-USDT',
    'BNBUSDT': 'BNB-USDT',
}

BUCKET_SZ = 50  # 50美元一桶

def fetch_okx_liquidations(okx_uly: str) -> list:
    """从OKX获取真实清算订单"""
    url = 'https://www.okx.com/api/v5/public/liquidation-orders'
    params = {'instType': 'SWAP', 'state': 'filled', 'uly': okx_uly}
    try:
        r = requests.get(url, params=params, timeout=15)
        data = r.json()
        if data.get('code') != '0':
            return []
        orders = data.get('data', [])
        records = []
        for order in orders:
            for d in order.get('details', []):
                records.append({
                    'ts': int(d.get('time', d.get('ts', 0))) / 1000,
                    'symbol': okx_uly.replace('-', ''),
                    'side': d.get('side', ''),  # buy=空头被清, sell=多头被清
                    'posSide': d.get('posSide', ''),
                    'price': float(d.get('bkPx', 0)),
                    'qty': float(d.get('sz', 0)),
                    'source': 'okx',
                })
        return records
    except Exception as e:
        print(f"[liq_okx] {okx_uly} error: {e}", file=sys.stderr)
        return []

def save_raw(records: list):
    """追加写入raw文件"""
    if not records:
        return
    with open(RAW_FILE, 'a') as f:
        for r in records:
            if r['price'] > 0:
                f.write(json.dumps(r) + '\n')

def aggregate_heatmap():
    """从raw jsonl重新聚合热力图（7天窗口）"""
    if not RAW_FILE.exists():
        return {}
    cutoff = time.time() - 7 * 86400
    heat = defaultdict(lambda: {"long_liq": 0.0, "short_liq": 0.0, "count": 0})
    seen = set()  # 去重（OKX可能返回重复记录）
    with open(RAW_FILE) as f:
        for line in f:
            try:
                d = json.loads(line)
                if d.get("ts", 0) < cutoff:
                    continue
                # 去重key = ts+price+qty
                key = f"{d.get('ts',0):.0f}_{d.get('price',0)}_{d.get('qty',0)}"
                if key in seen:
                    continue
                seen.add(key)
                price = float(d.get("price", 0))
                qty = float(d.get("qty", 0))
                side = d.get("side", "")
                bucket = int(round(price / BUCKET_SZ) * BUCKET_SZ)
                if side == "sell":  # 多头被清
                    heat[bucket]["long_liq"] += qty * price / 1e6
                elif side == "buy":  # 空头被清
                    heat[bucket]["short_liq"] += qty * price / 1e6
                heat[bucket]["count"] += 1
            except Exception:
                pass
    return {str(k): v for k, v in heat.items()}

def save_heatmap():
    heat = aggregate_heatmap()
    total_long = sum(v["long_liq"] for v in heat.values())
    total_short = sum(v["short_liq"] for v in heat.values())
    out = {
        "ts": time.time(),
        "total_long_liq_M": round(total_long, 3),
        "total_short_liq_M": round(total_short, 3),
        "buckets": heat,
    }
    HEAT_FILE.write_text(json.dumps(out, ensure_ascii=False))
    print(f"[liq_okx] 热力图: long={total_long:.2f}M short={total_short:.2f}M buckets={len(heat)}")

def main():
    BASE.joinpath("data").mkdir(exist_ok=True)
    total = 0
    for binance_sym, okx_sym in OKX_SYMBOLS.items():
        records = fetch_okx_liquidations(okx_sym)
        save_raw(records)
        total += len(records)
        print(f"[liq_okx] {okx_sym}: {len(records)}条清算")
    save_heatmap()
    print(f"[liq_okx] 总计: {total}条 | raw追加完成")

if __name__ == '__main__':
    main()
