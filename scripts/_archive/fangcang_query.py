#!/usr/bin/env python3
"""
fangcang_query.py — 方仓统一查询接口
用法：python3 fangcang_query.py [--symbol BTC] [--tf 4h] [--dir LONG] [--top 10]
"""
import json, argparse
from pathlib import Path

INDEX = Path(__file__).parent.parent / 'data' / 'FANGCANG_MASTER_INDEX.json'

def load_index():
    if not INDEX.exists():
        print('⚠️  索引不存在，请先运行 build_fangcang_index.py')
        return None
    return json.load(open(INDEX))

def query(symbol=None, timeframe=None, direction=None, top=10):
    idx = load_index()
    if not idx: return []

    cases = idx['cases']
    if symbol:    cases = [c for c in cases if c['symbol'] == symbol.upper().replace('USDT','')]
    if timeframe: cases = [c for c in cases if c['timeframe'] == timeframe]
    if direction: cases = [c for c in cases if c['direction'] == direction.upper()]

    # 按 bbw_min 升序（压缩越深越有价值）
    cases = sorted(cases, key=lambda x: x['bbw_min'])[:top]
    return cases

def summary():
    idx = load_index()
    if not idx: return
    print(f"\n📊 方仓主索引摘要 v{idx['version']}")
    print(f"   构建时间: {idx['built_at']}")
    print(f"   总案例:   {idx['total_cases']} 条（去重跳过 {idx['dedup_skipped']} 条）")
    print(f"\n   币种 Top10:")
    for sym, n in list(idx['by_symbol'].items())[:10]:
        print(f"     {sym:10s}: {n}条")
    print(f"\n   方向: {idx['by_direction']}")
    print(f"   周期: {idx['by_timeframe']}")

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--symbol', '-s', default=None)
    parser.add_argument('--tf',     '-t', default=None)
    parser.add_argument('--dir',    '-d', default=None)
    parser.add_argument('--top',    '-n', type=int, default=10)
    parser.add_argument('--summary',action='store_true')
    args = parser.parse_args()

    if args.summary or (not args.symbol and not args.tf and not args.dir):
        summary()
    else:
        results = query(args.symbol, args.tf, args.dir, args.top)
        print(f"\n查询结果: {len(results)}条")
        for c in results:
            from datetime import datetime, timezone
            dt = datetime.fromtimestamp(c['ts_start'], tz=timezone.utc).strftime('%Y-%m-%d')
            print(f"  {c['symbol']:6s} {c['timeframe']:4s} {c['direction']:5s} "
                  f"{dt} BBW={c['bbw_min']:.2f} RSI={c['rsi']:.0f} "
                  f"return={c['future_return']:+.1f}% src={c['source']}")
