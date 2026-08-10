#!/usr/bin/env python3
"""
fetch_historical_data.py — 历史K线数据补全脚本 v1.0
设计院封印 2026-08-08

功能:
  - 补全 ETH/SOL/BNB 6.5年历史K线（2019-11-01至今）
  - 补全 BTC 2026-07至今缺口
  - 存储为 parquet 格式到 data/historical/
  - 断点续传，自动跳过已有数据

用法:
  python3 scripts/fetch_historical_data.py --symbols ETH SOL BNB --fix-btc
"""

import urllib.request, json, time, os, sys, argparse
from pathlib import Path
from datetime import datetime, timezone

try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False

BASE = Path(__file__).parent.parent
DATA_DIR = BASE / 'data' / 'historical'
DATA_DIR.mkdir(parents=True, exist_ok=True)

FAPI = 'https://fapi.binance.com'
SPOT = 'https://api.binance.com'

# 目标资产配置
ASSETS = {
    'BTCUSDT': {'start': '2019-11-01', 'fix_from': '2026-07-01'},
    'ETHUSDT': {'start': '2019-11-01'},
    'SOLUSDT': {'start': '2021-09-01'},  # SOL上线时间
    'BNBUSDT': {'start': '2019-11-01'},
}

TIMEFRAMES = ['15m', '1h', '4h', '1d']


def fetch_klines(symbol: str, interval: str, start_ts: int, end_ts: int) -> list:
    """分批拉取K线，自动处理限流"""
    all_klines = []
    cur = start_ts
    limit = 1000

    while cur < end_ts:
        url = (f'{FAPI}/fapi/v1/klines'
               f'?symbol={symbol}&interval={interval}'
               f'&startTime={cur}&endTime={end_ts}&limit={limit}')
        try:
            resp = urllib.request.urlopen(url, timeout=15)
            batch = json.loads(resp.read())
            if not batch:
                break
            all_klines.extend(batch)
            cur = batch[-1][0] + 1  # 下一批从最后一根+1ms开始
            if len(batch) < limit:
                break  # 已到末尾
            time.sleep(0.12)  # 限流保护
        except Exception as e:
            print(f"  ⚠️ 拉取失败 {symbol} {interval} ts={cur}: {e}")
            time.sleep(2)
            break

    return all_klines


def klines_to_df(klines: list) -> 'pd.DataFrame':
    """K线列表转DataFrame"""
    df = pd.DataFrame(klines, columns=[
        'ts','open','high','low','close','volume',
        'close_time','quote_vol','trades','taker_buy_base',
        'taker_buy_quote','ignore'
    ])
    df['ts'] = pd.to_datetime(df['ts'], unit='ms', utc=True)
    df = df.set_index('ts')
    for col in ['open','high','low','close','volume','quote_vol']:
        df[col] = df[col].astype(float)
    df = df[['open','high','low','close','volume','quote_vol','trades']]
    df.index.name = 'ts'
    return df


def save_parquet(df: 'pd.DataFrame', symbol: str, interval: str):
    """保存/追加到parquet"""
    sym_lower = symbol.lower()
    out_dir = DATA_DIR / sym_lower
    out_dir.mkdir(exist_ok=True)
    fpath = out_dir / f'{sym_lower}_{interval}.parquet'

    if fpath.exists():
        existing = pd.read_parquet(fpath)
        # 合并去重
        combined = pd.concat([existing, df])
        combined = combined[~combined.index.duplicated(keep='last')]
        combined = combined.sort_index()
        combined.to_parquet(fpath)
        return len(combined) - len(existing), len(combined)
    else:
        df.to_parquet(fpath)
        return len(df), len(df)


def fetch_symbol(symbol: str, cfg: dict, fix_only: bool = False):
    """拉取单个资产的所有时间框架"""
    print(f"\n{'='*55}")
    print(f"📥 {symbol}")
    print(f"{'='*55}")

    now_ts = int(time.time() * 1000)

    for interval in TIMEFRAMES:
        sym_lower = symbol.lower()
        fpath = DATA_DIR / sym_lower / f'{sym_lower}_{interval}.parquet'

        if fix_only and fpath.exists() and 'fix_from' in cfg:
            # 只补缺口
            fix_start = int(datetime.fromisoformat(
                cfg['fix_from']).replace(tzinfo=timezone.utc).timestamp() * 1000)
            print(f"  {interval}: 补缺口 {cfg['fix_from']}→now ...", end=' ', flush=True)
            klines = fetch_klines(symbol, interval, fix_start, now_ts)
        elif fpath.exists() and not fix_only:
            # 已有数据，只补最新
            existing = pd.read_parquet(fpath)
            last_ts = int(existing.index[-1].timestamp() * 1000) + 1
            print(f"  {interval}: 已有{len(existing)}根，补全至今 ...", end=' ', flush=True)
            klines = fetch_klines(symbol, interval, last_ts, now_ts)
        else:
            # 全量拉取
            start_ts = int(datetime.fromisoformat(
                cfg['start']).replace(tzinfo=timezone.utc).timestamp() * 1000)
            print(f"  {interval}: 全量拉取 {cfg['start']}→now ...", end=' ', flush=True)
            klines = fetch_klines(symbol, interval, start_ts, now_ts)

        if not klines:
            print("无新数据")
            continue

        if not HAS_PANDAS:
            print(f"⚠️ pandas未安装，跳过parquet保存，原始数据{len(klines)}根")
            continue

        df = klines_to_df(klines)
        new_cnt, total = save_parquet(df, symbol, interval)
        print(f"✅ +{new_cnt}根 → 总{total}根")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--symbols', nargs='+', default=['ETHUSDT','SOLUSDT','BNBUSDT'])
    parser.add_argument('--fix-btc', action='store_true', help='补全BTC 2026-07至今缺口')
    parser.add_argument('--all', action='store_true', help='全部资产')
    args = parser.parse_args()

    targets = {}
    if args.all or args.fix_btc:
        targets['BTCUSDT'] = ASSETS['BTCUSDT']

    for sym in (args.symbols or []):
        sym_upper = sym.upper()
        if not sym_upper.endswith('USDT'):
            sym_upper += 'USDT'
        if sym_upper in ASSETS:
            targets[sym_upper] = ASSETS[sym_upper]
        else:
            targets[sym_upper] = {'start': '2020-01-01'}

    if not targets:
        targets = {s: cfg for s, cfg in ASSETS.items() if s != 'BTCUSDT'}

    print(f"🏛️ 历史数据补全任务")
    print(f"目标: {list(targets.keys())}")
    print(f"时框: {TIMEFRAMES}")
    print(f"存储: {DATA_DIR}")

    for symbol, cfg in targets.items():
        fix_only = symbol == 'BTCUSDT' and args.fix_btc and not args.all
        fetch_symbol(symbol, cfg, fix_only=fix_only)

    print(f"\n✅ 全部完成")
    # 汇总
    print(f"\n=== 数据汇总 ===")
    if HAS_PANDAS:
        for sym_dir in sorted(DATA_DIR.iterdir()):
            if sym_dir.is_dir():
                for pf in sorted(sym_dir.glob('*.parquet')):
                    df = pd.read_parquet(pf)
                    t0 = df.index[0].strftime('%Y-%m-%d')
                    t1 = df.index[-1].strftime('%Y-%m-%d')
                    size_mb = pf.stat().st_size / 1024 / 1024
                    print(f"  {pf.name:35s} {len(df):>8,}根  {t0}→{t1}  {size_mb:.1f}MB")


if __name__ == '__main__':
    main()
