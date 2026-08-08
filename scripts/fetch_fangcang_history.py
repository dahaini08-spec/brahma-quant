#!/usr/bin/env python3
"""
fetch_fangcang_history.py — 批量拉取历史K线写入方仓数据目录
设计院封印 2026-08-08

用法:
    python3 scripts/fetch_fangcang_history.py --symbols XRPUSDT DOGEUSDT ...
"""
import requests, json, time, sys, argparse
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).parent.parent
BACKTEST_DIR = BASE / 'data' / 'backtest'
BACKTEST_DIR.mkdir(parents=True, exist_ok=True)

FAPI = 'https://fapi.binance.com/fapi/v1/klines'

# 按交易所上线时间设定起始点
START_MAP = {
    'XRPUSDT':  '2020-01-01',
    'DOGEUSDT': '2020-07-01',
    'ADAUSDT':  '2020-01-01',
    'LINKUSDT': '2020-01-01',
    'CHZUSDT':  '2020-05-01',
    'OPUSDT':   '2022-06-01',
    'ARBUSDT':  '2023-04-01',
    'UNIUSDT':  '2020-09-01',
    'ZECUSDT':  '2020-01-01',
    'DEFAULT':  '2020-01-01',
}

def fetch_klines(symbol: str, interval: str, start_ms: int, end_ms: int) -> list:
    """分批拉取，返回 [open_time, o, h, l, c, v, ...] 列表"""
    all_bars = []
    cur = start_ms
    while cur < end_ms:
        params = {
            'symbol': symbol, 'interval': interval,
            'startTime': cur, 'endTime': end_ms,
            'limit': 1500
        }
        try:
            r = requests.get(FAPI, params=params, timeout=15)
            bars = r.json()
            if not bars or not isinstance(bars, list):
                break
            all_bars.extend(bars)
            last_t = bars[-1][0]
            if last_t <= cur or len(bars) < 10:
                break
            cur = last_t + 1
            time.sleep(0.08)  # 避免触发rate limit
        except Exception as e:
            print(f'  ⚠️ 拉取失败: {e}', flush=True)
            time.sleep(1)
            break
    return all_bars

def fetch_and_save(symbol: str, interval: str):
    out_path = BACKTEST_DIR / f'{symbol}_{interval}.json'
    
    # 如已有数据，增量更新
    existing = []
    last_ts = None
    if out_path.exists():
        try:
            existing = json.loads(out_path.read_text())
            if existing:
                last_ts = existing[-1][0]
        except:
            existing = []
    
    # 确定起始时间
    start_str = START_MAP.get(symbol, START_MAP['DEFAULT'])
    if last_ts:
        start_ms = last_ts + 1
        mode = '增量更新'
    else:
        dt = datetime.strptime(start_str, '%Y-%m-%d').replace(tzinfo=timezone.utc)
        start_ms = int(dt.timestamp() * 1000)
        mode = '全量拉取'
    
    end_ms = int(time.time() * 1000)
    
    print(f'  {symbol} {interval}: {mode} ({start_str if not last_ts else "续传"} ~ now)', flush=True)
    
    new_bars = fetch_klines(symbol, interval, start_ms, end_ms)
    
    if not new_bars:
        if existing:
            print(f'    → 已是最新 ({len(existing)}根)', flush=True)
            return len(existing)
        print(f'    → 无数据', flush=True)
        return 0
    
    all_bars = existing + new_bars
    # 去重排序
    seen = {}
    for b in all_bars:
        seen[b[0]] = b
    all_bars = sorted(seen.values(), key=lambda x: x[0])
    
    out_path.write_text(json.dumps(all_bars))
    print(f'    → 保存 {len(all_bars)}根 ({out_path.stat().st_size//1024}KB)', flush=True)
    return len(all_bars)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--symbols', nargs='+', required=True)
    parser.add_argument('--intervals', nargs='+', default=['15m', '4h'])
    args = parser.parse_args()
    
    print(f'方仓历史数据拉取 | {len(args.symbols)}币种 × {len(args.intervals)}时框')
    print('=' * 50)
    
    results = {}
    for sym in args.symbols:
        results[sym] = {}
        for interval in args.intervals:
            n = fetch_and_save(sym, interval)
            results[sym][interval] = n
    
    print()
    print('=== 完成汇总 ===')
    for sym, tfs in results.items():
        tf_str = ' | '.join([f'{tf}:{n}根' for tf, n in tfs.items()])
        print(f'  {sym}: {tf_str}')

if __name__ == '__main__':
    main()
