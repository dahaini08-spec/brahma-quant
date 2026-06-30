#!/usr/bin/env python3
"""
达摩院 · P0 数据拉取器 v2.0
================================
目标：BTC + ETH 期货永续数据 2019-01-01 ~ 2026-06-12
周期：1D / 4H / 1H / 15M
来源：Binance FAPI (期货K线 + 标记价格)

输出：data/backtest/{sym}_{tf}_2019_2026.parquet
      含 open/high/low/close/volume/mark_close/ATR/EMA/regime_label
"""
import sys, time, warnings
from pathlib import Path
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd
import requests

warnings.filterwarnings('ignore')
BASE    = Path('/root/.openclaw/workspace/trading-system')
OUT_DIR = BASE / 'data' / 'backtest'
OUT_DIR.mkdir(parents=True, exist_ok=True)

SYMS      = ['BTCUSDT', 'ETHUSDT']
INTERVALS = ['1d', '4h', '1h', '15m']
START_DT  = datetime(2019, 1, 1, tzinfo=timezone.utc)
END_DT    = datetime(2026, 6, 12, tzinfo=timezone.utc)

TF_LIMIT = {'1d': 1000, '4h': 1000, '1h': 1000, '15m': 1000}  # Binance每次最多1000条


def fetch_klines(sym: str, interval: str, start_ms: int, end_ms: int) -> list:
    """拉取期货K线，自动分页"""
    url  = 'https://fapi.binance.com/fapi/v1/klines'
    rows = []
    cur  = start_ms
    limit = TF_LIMIT.get(interval, 1000)
    while cur < end_ms:
        for attempt in range(5):
            try:
                r = requests.get(url, params={
                    'symbol': sym, 'interval': interval,
                    'startTime': cur, 'endTime': end_ms,
                    'limit': limit,
                }, timeout=20)
                r.raise_for_status()
                data = r.json()
                break
            except Exception as e:
                if attempt == 4:
                    print(f'  [WARN] {sym} {interval} fetch失败: {e}')
                    return rows
                time.sleep(2 ** attempt)
        if not data:
            break
        rows.extend(data)
        cur = data[-1][0] + 1
        if len(data) < limit:
            break
        time.sleep(0.12)
    return rows


def build_df(rows: list) -> pd.DataFrame:
    cols = ['open_time','open','high','low','close','volume',
            'close_time','quote_vol','trades','taker_base','taker_quote','ignore']
    df = pd.DataFrame(rows, columns=cols[:len(rows[0])] if rows else cols)
    df['open_time'] = pd.to_datetime(df['open_time'].astype(int), unit='ms', utc=True)
    df = df.set_index('open_time').sort_index()
    for c in ['open','high','low','close','volume']:
        df[c] = df[c].astype(float)
    df = df[~df.index.duplicated(keep='last')]
    return df[['open','high','low','close','volume']]


def add_indicators(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    c = df['close']
    # ATR
    h, l, pc = df['high'], df['low'], c.shift(1)
    tr = pd.concat([h-l, (h-pc).abs(), (l-pc).abs()], axis=1).max(axis=1)
    df['atr'] = tr.ewm(span=14, adjust=False).mean()
    # EMA
    for p in [21, 55, 200]:
        df[f'ema{p}'] = c.ewm(span=p, adjust=False).mean()
    # RSI-14
    delta = c.diff()
    gain  = delta.clip(lower=0).ewm(span=14, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(span=14, adjust=False).mean()
    df['rsi14'] = 100 - 100 / (1 + gain / loss.replace(0, 1e-9))
    # 体制标签（仅4H用于训练）
    if tf == '4h':
        df['regime'] = 'CHOP'
        df.loc[c > df['ema200'], 'regime'] = 'BULL'
        df.loc[c < df['ema200'], 'regime'] = 'BEAR'
    return df


def main():
    manifest = {}
    total_start = time.time()

    for sym in SYMS:
        manifest[sym] = {}
        for tf in INTERVALS:
            print(f'\n[{sym} {tf}] 拉取 {START_DT.date()} ~ {END_DT.date()}...', flush=True)
            t0 = time.time()
            rows = fetch_klines(
                sym, tf,
                int(START_DT.timestamp() * 1000),
                int(END_DT.timestamp() * 1000),
            )
            if not rows:
                print(f'  [SKIP] 无数据')
                manifest[sym][tf] = {'status': 'EMPTY', 'n': 0}
                continue

            df = build_df(rows)
            df = add_indicators(df, tf)
            df = df.dropna(subset=['atr'])

            # 截断到 2019-01-01
            df = df[df.index >= START_DT]

            fname = OUT_DIR / f'{sym.lower()}_{tf}_2019_2026.parquet'
            df.to_parquet(fname)

            elapsed = time.time() - t0
            n = len(df)
            print(f'  OK: {n:,}条  {str(df.index[0])[:10]}~{str(df.index[-1])[:10]}  ({elapsed:.1f}s)  → {fname.name}')
            manifest[sym][tf] = {'status': 'OK', 'n': n,
                                  'start': str(df.index[0])[:10],
                                  'end': str(df.index[-1])[:10],
                                  'file': str(fname)}

    import json
    manifest_path = OUT_DIR / 'fetch_manifest_2019_2026.json'
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    print(f'\n总耗时: {time.time()-total_start:.0f}s')
    print(f'Manifest: {manifest_path}')
    print('P0 完成 ✅')


if __name__ == '__main__':
    main()
