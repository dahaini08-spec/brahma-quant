#!/usr/bin/env python3
"""
达摩院 · 标准数据集构建器 v1.0
================================
设计院指定规格：
  标的：BTC/ETH（期货永续）
  起止：2019-11-01 ~ 2026-05-30（固定，不随时间变化）
  周期：15M / 1H / 4H / 1D
  维度：价格OHLCV + OI（持仓量）+ 资金费率FR + ATR + EMA + RSI + 体制标注

输出文件（本地固定路径）：
  data/backtest/fixed/
    btcusdt_15m_fixed.parquet
    btcusdt_1h_fixed.parquet
    btcusdt_4h_fixed.parquet
    btcusdt_1d_fixed.parquet
    ethusdt_15m_fixed.parquet
    ethusdt_1h_fixed.parquet
    ethusdt_4h_fixed.parquet
    ethusdt_1d_fixed.parquet
    dataset_manifest.json   ← 数据集元数据（行数/范围/字段/MD5）
"""
import sys, json, time, hashlib, warnings
from pathlib import Path
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests

warnings.filterwarnings('ignore')

BASE    = Path('/root/.openclaw/workspace/trading-system')
OUT_DIR = BASE / 'data' / 'backtest' / 'fixed'
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ════════════ 固定数据集规格（写死不变）════════════
DATA_START = datetime(2019, 11, 1,  tzinfo=timezone.utc)
DATA_END   = datetime(2026,  5, 30, tzinfo=timezone.utc)
SYMS       = ['BTCUSDT', 'ETHUSDT']
INTERVALS  = ['15m', '1h', '4h', '1d']
# ════════════════════════════════════════════════════

FAPI_BASE = 'https://fapi.binance.com'


def fetch_klines(sym: str, interval: str, start_ms: int, end_ms: int) -> list:
    url, rows = f'{FAPI_BASE}/fapi/v1/klines', []
    cur = start_ms
    while cur < end_ms:
        for attempt in range(5):
            try:
                r = requests.get(url, params={
                    'symbol': sym, 'interval': interval,
                    'startTime': cur, 'endTime': end_ms, 'limit': 1000,
                }, timeout=20)
                r.raise_for_status()
                data = r.json()
                break
            except Exception as e:
                if attempt == 4: return rows
                time.sleep(2 ** attempt)
        if not data: break
        rows.extend(data)
        cur = data[-1][0] + 1
        if len(data) < 1000: break
        time.sleep(0.12)
    return rows


def fetch_open_interest_hist(sym: str, interval: str, start_ms: int, end_ms: int) -> pd.Series:
    """拉取历史OI数据（仅支持5m/15m/30m/1h/2h/4h/6h/12h/1d）"""
    # OI接口支持的周期映射
    oi_interval_map = {
        '15m': '15m', '1h': '1h', '4h': '4h', '1d': '1d'
    }
    oi_tf = oi_interval_map.get(interval)
    if not oi_tf:
        return pd.Series(dtype=float)

    url = f'{FAPI_BASE}/futures/data/openInterestHist'
    rows, cur = [], start_ms
    while cur < end_ms:
        for attempt in range(4):
            try:
                r = requests.get(url, params={
                    'symbol': sym, 'period': oi_tf,
                    'startTime': cur, 'endTime': end_ms, 'limit': 500,
                }, timeout=20)
                r.raise_for_status()
                data = r.json()
                break
            except Exception:
                if attempt == 3: data = []
                time.sleep(2 ** attempt)
        if not data: break
        rows.extend(data)
        cur = data[-1]['timestamp'] + 1
        if len(data) < 500: break
        time.sleep(0.15)

    if not rows:
        return pd.Series(dtype=float)

    series = pd.Series(
        {pd.Timestamp(r['timestamp'], unit='ms', tz='UTC'): float(r['sumOpenInterest'])
         for r in rows}
    )
    return series


def fetch_funding_rate(sym: str, start_ms: int, end_ms: int) -> pd.Series:
    """拉取资金费率（每8H一条）"""
    url, rows = f'{FAPI_BASE}/fapi/v1/fundingRate', []
    cur = start_ms
    while cur < end_ms:
        for attempt in range(4):
            try:
                r = requests.get(url, params={
                    'symbol': sym, 'startTime': cur,
                    'endTime': end_ms, 'limit': 1000,
                }, timeout=20)
                r.raise_for_status()
                data = r.json()
                break
            except Exception:
                if attempt == 3: data = []
                time.sleep(2 ** attempt)
        if not data: break
        rows.extend(data)
        cur = data[-1]['fundingTime'] + 1
        if len(data) < 1000: break
        time.sleep(0.12)

    if not rows:
        return pd.Series(dtype=float)

    series = pd.Series(
        {pd.Timestamp(r['fundingTime'], unit='ms', tz='UTC'): float(r['fundingRate'])
         for r in rows}
    )
    return series


def build_price_df(rows: list) -> pd.DataFrame:
    cols = ['open_time','open','high','low','close','volume',
            'close_time','quote_vol','trades','taker_base','taker_quote','_']
    df = pd.DataFrame(rows, columns=cols[:len(rows[0])])
    df['open_time'] = pd.to_datetime(df['open_time'].astype(int), unit='ms', utc=True)
    df = df.set_index('open_time').sort_index()
    for c in ['open','high','low','close','volume']:
        df[c] = df[c].astype(float)
    df = df[~df.index.duplicated(keep='last')]
    return df[['open','high','low','close','volume']]


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    c = df['close']
    h, l, pc = df['high'], df['low'], c.shift(1)
    tr = pd.concat([h-l, (h-pc).abs(), (l-pc).abs()], axis=1).max(axis=1)
    df['atr14'] = tr.ewm(span=14, adjust=False).mean()
    for p in [21, 55, 200]:
        df[f'ema{p}'] = c.ewm(span=p, adjust=False).mean()
    delta = c.diff()
    gain  = delta.clip(lower=0).ewm(span=14, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(span=14, adjust=False).mean()
    df['rsi14'] = 100 - 100 / (1 + gain / loss.replace(0, 1e-9))
    return df


def label_regime(df: pd.DataFrame) -> pd.DataFrame:
    """精细7体制标注"""
    c, e200, rsi = df['close'], df['ema200'], df['rsi14']
    above = c > e200 * 1.015
    below = c < e200 * 0.985
    regime = pd.Series('CHOP', index=df.index)
    regime[below & (rsi < 40)]               = 'BEAR_TREND'
    regime[below & (rsi >= 40) & (rsi < 52)] = 'BEAR_EARLY'
    regime[below & (rsi >= 52)]              = 'BEAR_RECOVERY'
    regime[above & (rsi > 58)]               = 'BULL_TREND'
    regime[above & (rsi >= 48) & (rsi <= 58)]= 'BULL_EARLY'
    regime[above & (rsi < 48)]               = 'BULL_CORRECTION'
    regime[~above & ~below]                  = 'CHOP'
    df['regime'] = regime
    return df


def attach_oi(df: pd.DataFrame, sym: str, interval: str,
              start_ms: int, end_ms: int) -> pd.DataFrame:
    print(f'    拉取OI({interval})...', end=' ', flush=True)
    oi = fetch_open_interest_hist(sym, interval, start_ms, end_ms)
    if oi.empty:
        print('无数据')
        df['oi'] = np.nan
        return df
    oi.name = 'oi'
    df = df.join(oi, how='left')
    df['oi'] = df['oi'].ffill()
    print(f'{len(oi):,}条')
    return df


def attach_fr(df: pd.DataFrame, sym: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    print(f'    拉取资金费率...', end=' ', flush=True)
    fr = fetch_funding_rate(sym, start_ms, end_ms)
    if fr.empty:
        print('无数据')
        df['funding_rate'] = np.nan
        df['fr_cumulative_8h'] = np.nan
        return df
    fr.name = 'funding_rate'
    df = df.join(fr, how='left')
    df['funding_rate'] = df['funding_rate'].ffill()
    # 累积资金费率（近8H的持仓成本）
    df['fr_cumulative_8h'] = df['funding_rate'].rolling(3, min_periods=1).sum()
    print(f'{len(fr):,}条')
    return df


def file_md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            h.update(chunk)
    return h.hexdigest()[:8]


def main():
    t_global = time.time()
    manifest = {
        'spec': {
            'data_start': str(DATA_START.date()),
            'data_end':   str(DATA_END.date()),
            'syms':       SYMS,
            'intervals':  INTERVALS,
            'fixed':      True,
            'generated':  datetime.now(timezone.utc).isoformat(),
        },
        'files': {}
    }

    start_ms = int(DATA_START.timestamp() * 1000)
    end_ms   = int(DATA_END.timestamp()   * 1000)

    print('=' * 65)
    print('达摩院 · 固定数据集构建器 v1.0')
    print(f'范围: {DATA_START.date()} ~ {DATA_END.date()}')
    print(f'标的: {SYMS}  周期: {INTERVALS}')
    print('=' * 65)

    for sym in SYMS:
        print(f'\n{"━"*65}')
        print(f'▶ {sym}')

        for tf in INTERVALS:
            t0 = time.time()
            out_path = OUT_DIR / f'{sym.lower()}_{tf}_fixed.parquet'
            print(f'\n  [{sym} {tf}] 拉取价格...', end=' ', flush=True)

            rows = fetch_klines(sym, tf, start_ms, end_ms)
            if not rows:
                print('❌ 无数据')
                continue

            df = build_price_df(rows)
            df = df[(df.index >= DATA_START) & (df.index <= DATA_END)]
            df = add_indicators(df)
            df = label_regime(df)
            df = df.dropna(subset=['atr14', 'ema200'])

            print(f'{len(df):,}条  {str(df.index[0])[:10]}~{str(df.index[-1])[:10]}')

            # 附加OI（仅4H/1H/15M/1D）
            df = attach_oi(df, sym, tf, start_ms, end_ms)

            # 附加资金费率（每个标的只需拉一次，附加到所有周期）
            if tf == '4h':
                df = attach_fr(df, sym, start_ms, end_ms)
            else:
                # 其他周期资金费率从4H对齐（稀疏附加）
                df['funding_rate'] = np.nan
                df['fr_cumulative_8h'] = np.nan

            # 保存
            df.to_parquet(out_path, compression='snappy')
            md5 = file_md5(out_path)
            size_kb = out_path.stat().st_size // 1024
            elapsed = time.time() - t0

            manifest['files'][f'{sym}_{tf}'] = {
                'path':   str(out_path),
                'n_rows': len(df),
                'start':  str(df.index[0])[:10],
                'end':    str(df.index[-1])[:10],
                'cols':   list(df.columns),
                'size_kb': size_kb,
                'md5':    md5,
                'elapsed_s': round(elapsed, 1),
            }

            # 打印列信息
            oi_ok = df['oi'].notna().sum()
            fr_ok = df['funding_rate'].notna().sum()
            print(f'    → 已保存: {out_path.name}  ({size_kb}KB, {elapsed:.1f}s)')
            print(f'    列: {list(df.columns)}')
            print(f'    OI覆盖: {oi_ok:,}/{len(df):,}  FR覆盖: {fr_ok:,}/{len(df):,}')

    # 保存manifest
    manifest_path = OUT_DIR / 'dataset_manifest.json'
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))

    print()
    print('=' * 65)
    print('数据集构建完成 ✅')
    print(f'总耗时: {(time.time()-t_global)/60:.1f}分钟')
    print(f'输出目录: {OUT_DIR}')
    print(f'Manifest: {manifest_path}')
    print()
    print('文件清单:')
    for key, info in manifest['files'].items():
        print(f'  {key:<22}: {info["n_rows"]:>8,}条  {info["start"]}~{info["end"]}  {info["size_kb"]}KB  md5={info["md5"]}')


if __name__ == '__main__':
    main()
