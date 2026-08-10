#!/usr/bin/env python3
"""
梵天设计院 · 6.5年历史K线全量下载系统
覆盖: 2019-11-01 → 2026-07-30
周期: 15m / 1h / 4h / 1d
目标: BTC/ETH 牛熊全周期体制认知基础

封印: 2026-08-07 设计院自主
"""
import requests, json, time, os, gzip
from datetime import datetime, timezone
from pathlib import Path

# ── 配置 ──────────────────────────────────────────
SYMBOLS    = ['BTCUSDT', 'ETHUSDT']
INTERVALS  = ['15m', '1h', '4h', '1d']
START_TIME = datetime(2019, 11, 1,  tzinfo=timezone.utc)
END_TIME   = datetime(2026, 7, 30, 23, 59, tzinfo=timezone.utc)
BASE_URL   = 'https://fapi.binance.com'
BATCH_SIZE = 1000          # Binance 单次最大
SLEEP_MS   = 120           # 毫秒，限速保护
OUT_DIR    = Path(__file__).parent.parent / 'data' / 'historical'
OUT_DIR.mkdir(parents=True, exist_ok=True)

def ts_ms(dt): return int(dt.timestamp() * 1000)

def get_klines_batch(symbol, interval, start_ms, end_ms):
    """单批次获取，最多1000根"""
    for attempt in range(4):
        try:
            r = requests.get(
                f'{BASE_URL}/fapi/v1/klines',
                params={
                    'symbol':    symbol,
                    'interval':  interval,
                    'startTime': start_ms,
                    'endTime':   end_ms,
                    'limit':     BATCH_SIZE,
                },
                timeout=15
            )
            if r.status_code == 200:
                return r.json()
            elif r.status_code == 429:
                print(f'    限速 429，等待5s...')
                time.sleep(5)
            else:
                print(f'    HTTP {r.status_code}: {r.text[:100]}')
                time.sleep(1)
        except Exception as e:
            print(f'    请求异常(尝试{attempt+1}): {e}')
            time.sleep(2)
    return []

def download_symbol_interval(symbol, interval):
    """下载单个 symbol × interval 的完整历史"""
    out_file = OUT_DIR / f'{symbol}_{interval}.jsonl.gz'
    
    # 断点续传：检查已有数据的最后时间戳
    last_ts = ts_ms(START_TIME)
    if out_file.exists():
        try:
            with gzip.open(out_file, 'rt') as f:
                lines = f.readlines()
            if lines:
                last_line = json.loads(lines[-1])
                last_ts = last_line['ts'] + 1
                print(f'  断点续传: 已有{len(lines)}根，从 {datetime.utcfromtimestamp(last_ts/1000).strftime("%Y-%m-%d %H:%M")} 继续')
        except:
            pass

    end_ts = ts_ms(END_TIME)
    if last_ts >= end_ts:
        print(f'  {symbol} {interval}: 已完整，跳过')
        return True

    total_new = 0
    with gzip.open(out_file, 'at') as f:  # 追加模式
        current_ts = last_ts
        while current_ts < end_ts:
            batch = get_klines_batch(symbol, interval, current_ts, end_ts)
            if not batch:
                break
            for k in batch:
                row = {
                    'ts':     int(k[0]),
                    'o':  float(k[1]),
                    'h':  float(k[2]),
                    'l':  float(k[3]),
                    'c':  float(k[4]),
                    'v':  float(k[5]),
                    'qv': float(k[7]),
                    'tb': float(k[9]),   # taker buy vol
                    'n':  int(k[8]),     # trades
                }
                f.write(json.dumps(row) + '\n')
            total_new += len(batch)
            current_ts = int(batch[-1][0]) + 1
            time.sleep(SLEEP_MS / 1000)
            # 进度
            pct = (current_ts - ts_ms(START_TIME)) / (end_ts - ts_ms(START_TIME)) * 100
            dt_str = datetime.utcfromtimestamp(current_ts/1000).strftime('%Y-%m-%d')
            print(f'  {symbol} {interval}: {pct:.1f}% | {dt_str} | +{total_new}根', end='\r')

    print(f'  {symbol} {interval}: ✅ 完成 +{total_new}根新数据')
    return True

def get_stats():
    """统计已下载数据"""
    print('\n=== 数据统计 ===')
    total_rows = 0
    total_size = 0
    for sym in SYMBOLS:
        for tf in INTERVALS:
            f = OUT_DIR / f'{sym}_{tf}.jsonl.gz'
            if f.exists():
                size_kb = f.stat().st_size // 1024
                total_size += f.stat().st_size
                try:
                    with gzip.open(f, 'rt') as fp:
                        n = sum(1 for _ in fp)
                    total_rows += n
                    print(f'  {sym} {tf}: {n:,}根 ({size_kb}KB)')
                except:
                    print(f'  {sym} {tf}: {size_kb}KB (读取统计中...)')
    print(f'  合计: {total_rows:,}根 | {total_size//1024//1024}MB')

def main():
    print('=' * 60)
    print('梵天设计院 · 6.5年历史K线全量下载')
    print(f'范围: {START_TIME.strftime("%Y-%m-%d")} → {END_TIME.strftime("%Y-%m-%d")}')
    print(f'品种: {SYMBOLS}')
    print(f'周期: {INTERVALS}')
    print(f'输出: {OUT_DIR}')
    print('=' * 60)

    for symbol in SYMBOLS:
        print(f'\n[{symbol}]')
        for interval in INTERVALS:
            download_symbol_interval(symbol, interval)

    get_stats()
    print('\n✅ 全量历史数据下载完成！')

if __name__ == '__main__':
    main()
