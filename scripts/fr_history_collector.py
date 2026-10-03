#!/usr/bin/env python3
"""
fr_history_collector.py — 资金费率历史滚动采集
[设计院封印 2026-10-03 苏摩111]

接入位置：brahma_crontab.txt → 每8小时采集一次（对齐资金费率结算周期）
用途：为线路B FR Z-Score套利子系统积累30天历史数据

采集逻辑：
  - 每次运行在 data/fr_history_BTC.jsonl / fr_history_ETH.jsonl 追加一条
  - 每条格式: {"ts": epoch, "symbol": "BTCUSDT", "rate": 0.0001, "datetime": "..."}
  - 自动清理90天前数据（控制文件大小）
  - 无API KEY需要（公开端点）
"""
import json, time, urllib.request, sys
from pathlib import Path
from datetime import datetime, timezone

DATA_DIR = Path(__file__).parent.parent / 'data'
SYMBOLS  = ['BTCUSDT', 'ETHUSDT']
MAX_DAYS = 90   # 保留90天历史
BINANCE  = 'https://fapi.binance.com'


def fetch_current_fr(symbol: str) -> dict | None:
    try:
        url = f'{BINANCE}/fapi/v1/premiumIndex?symbol={symbol}'
        raw = urllib.request.urlopen(url, timeout=8).read()
        data = json.loads(raw)
        rate = float(data.get('lastFundingRate', 0))
        return {
            'ts':       int(time.time()),
            'symbol':   symbol,
            'rate':     rate,
            'datetime': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'),
        }
    except Exception as e:
        print(f'[WARN] fr_collector {symbol}: {e}', file=sys.stderr)
        return None


def collect():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    cutoff = int(time.time()) - MAX_DAYS * 86400

    for sym in SYMBOLS:
        entry = fetch_current_fr(sym)
        if not entry:
            continue

        path = DATA_DIR / f'fr_history_{sym[:3]}.jsonl'

        # 读取现有记录（过滤过期）
        existing = []
        if path.exists():
            for line in path.read_text().splitlines():
                try:
                    rec = json.loads(line)
                    if rec.get('ts', 0) > cutoff:
                        existing.append(rec)
                except Exception:
                    pass

        existing.append(entry)

        # 原子写入
        tmp = path.with_suffix('.tmp')
        tmp.write_text('\n'.join(json.dumps(r) for r in existing) + '\n')
        tmp.replace(path)

        print(f'[FR_COLLECT] {sym} rate={entry["rate"]:.6f} | 历史{len(existing)}条')


if __name__ == '__main__':
    collect()
