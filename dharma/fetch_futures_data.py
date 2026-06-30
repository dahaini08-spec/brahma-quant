#!/usr/bin/env python3
"""
达摩院 · 期货永续数据下载器 v1.0
设计院 · 2026-06-12

═══════════════════════════════════════════════════════════════
【数据层说明】

Layer-1: 期货K线（2019-09至今，无限制）
  来源: fapi.binance.com/fapi/v1/klines
  字段: open/high/low/close/volume/taker_buy_vol/trades
  周期: 1H + 4H + 1D
  用途: 主要价格结构，替代现货K线

Layer-2: 标记价格K线（2019-09至今）
  来源: fapi.binance.com/fapi/v1/markPriceKlines
  字段: mark_open/mark_high/mark_low/mark_close
  用途: 合约实际结算价，防止现货/合约价差引起的虚假信号

Layer-3: 资金费率（2019-09至今，每8H一次）
  来源: fapi.binance.com/fapi/v1/fundingRate
  字段: fundingRate（范围-0.75%~+0.75%）
  用途:
    - 正费率高(>0.1%/8H) = 多头拥挤 → 做空优势
    - 负费率深(<-0.05%/8H) = 空头拥挤 → 做多机会
    - 极端费率(>0.3%/8H) = 爆仓预警 → 强信号

Layer-4: 未平仓量OI（仅最近30天，API限制）
  来源: fapi.binance.com/futures/data/openInterestHist
  字段: sumOpenInterest / sumOpenInterestValue
  用途: OI急剧增加+价格上涨 = 趋势确认
        OI急剧减少+价格下跌 = 平仓抛压

Layer-5: 多空比（仅最近30天）
  来源: fapi.binance.com/futures/data/globalLongShortAccountRatio
  字段: longShortRatio / longAccount / shortAccount
  用途: 极端多空比 → 反转信号

【训练数据策略】
  Layer-1+2+3: 2019-09至今，约6年，用于全周期训练
  Layer-4+5:   仅最近30天，作为实时信号增强维度（不用于回测）
═══════════════════════════════════════════════════════════════
"""

import json
import time
import gzip
import subprocess
import warnings
from datetime import datetime, timezone, timedelta
from pathlib import Path
import pandas as pd
import numpy as np

warnings.filterwarnings('ignore')

BASE_URL_FAPI = 'https://fapi.binance.com'
OUT_DIR = Path(__file__).parent / 'data' / 'futures'
OUT_DIR.mkdir(parents=True, exist_ok=True)

SYMS_CORE = ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT',
             'DOGEUSDT', 'ADAUSDT', 'LINKUSDT', 'LTCUSDT']

TF_MAP = {'1h': '1h', '4h': '4h', '1d': '1d'}


# ── API工具 ───────────────────────────────────────────────────

def curl_get(url: str, params: dict = None, retries=3) -> dict | list:
    """curl子进程调用（绕过urllib限流）"""
    qs = '&'.join(f'{k}={v}' for k, v in (params or {}).items())
    full_url = f'{url}?{qs}' if qs else url
    for attempt in range(retries):
        try:
            r = subprocess.run(
                ['curl', '-s', '--max-time', '15', full_url],
                capture_output=True, text=True, timeout=20
            )
            return json.loads(r.stdout)
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    return []


def ts_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def ms_to_dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


# ── Layer-1: 期货K线 ──────────────────────────────────────────

def fetch_futures_klines(sym: str, interval: str,
                         start_dt: datetime, end_dt: datetime) -> pd.DataFrame:
    """分批拉取期货K线，返回完整DataFrame"""
    all_rows = []
    cur = ts_ms(start_dt)
    end = ts_ms(end_dt)
    limit = 1500

    while cur < end:
        data = curl_get(f'{BASE_URL_FAPI}/fapi/v1/klines', {
            'symbol': sym, 'interval': interval,
            'startTime': cur, 'endTime': end, 'limit': limit
        })
        if not isinstance(data, list) or not data:
            break
        all_rows.extend(data)
        last_ts = int(data[-1][0])
        if last_ts <= cur:
            break
        cur = last_ts + 1
        if len(data) < limit:
            break
        time.sleep(0.08)

    if not all_rows:
        return pd.DataFrame()

    df = pd.DataFrame(all_rows, columns=[
        'open_time', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'quote_vol', 'trades',
        'taker_buy_base', 'taker_buy_quote', 'ignore'
    ])
    df['open_time'] = pd.to_datetime(df['open_time'], unit='ms', utc=True)
    df = df.set_index('open_time').sort_index()
    for col in ['open', 'high', 'low', 'close', 'volume', 'quote_vol',
                'taker_buy_base', 'taker_buy_quote']:
        df[col] = df[col].astype(float)
    df['trades'] = df['trades'].astype(int)
    df['taker_buy_ratio'] = df['taker_buy_base'] / (df['volume'] + 1e-9)
    df = df.drop(columns=['close_time', 'ignore', 'taker_buy_quote'])
    df = df[~df.index.duplicated(keep='last')]
    return df


# ── Layer-2: 标记价格K线 ──────────────────────────────────────

def fetch_mark_klines(sym: str, interval: str,
                      start_dt: datetime, end_dt: datetime) -> pd.DataFrame:
    all_rows = []
    cur = ts_ms(start_dt)
    end = ts_ms(end_dt)
    limit = 1500

    while cur < end:
        data = curl_get(f'{BASE_URL_FAPI}/fapi/v1/markPriceKlines', {
            'symbol': sym, 'interval': interval,
            'startTime': cur, 'endTime': end, 'limit': limit
        })
        if not isinstance(data, list) or not data:
            break
        all_rows.extend(data)
        last_ts = int(data[-1][0])
        if last_ts <= cur:
            break
        cur = last_ts + 1
        if len(data) < limit:
            break
        time.sleep(0.08)

    if not all_rows:
        return pd.DataFrame()

    df = pd.DataFrame(all_rows, columns=[
        'open_time', 'mark_open', 'mark_high', 'mark_low', 'mark_close',
        '_v', '_ct', '_qv', '_t', '_tbb', '_tbq', '_i'
    ])
    df['open_time'] = pd.to_datetime(df['open_time'], unit='ms', utc=True)
    df = df.set_index('open_time').sort_index()
    for col in ['mark_open', 'mark_high', 'mark_low', 'mark_close']:
        df[col] = df[col].astype(float)
    df = df[['mark_open', 'mark_high', 'mark_low', 'mark_close']]
    df = df[~df.index.duplicated(keep='last')]
    return df


# ── Layer-3: 资金费率 ─────────────────────────────────────────

def fetch_funding_rate(sym: str,
                       start_dt: datetime, end_dt: datetime) -> pd.DataFrame:
    """资金费率（每8H），前向填充到1H对齐"""
    all_rows = []
    cur = ts_ms(start_dt)
    end = ts_ms(end_dt)
    limit = 1000

    while cur < end:
        data = curl_get(f'{BASE_URL_FAPI}/fapi/v1/fundingRate', {
            'symbol': sym, 'startTime': cur, 'endTime': end, 'limit': limit
        })
        if not isinstance(data, list) or not data:
            break
        all_rows.extend(data)
        last_ts = int(data[-1]['fundingTime'])
        if last_ts <= cur:
            break
        cur = last_ts + 1
        if len(data) < limit:
            break
        time.sleep(0.08)

    if not all_rows:
        return pd.DataFrame()

    df = pd.DataFrame(all_rows)
    df['ts'] = pd.to_datetime(df['fundingTime'], unit='ms', utc=True)
    df = df.set_index('ts').sort_index()
    df['funding_rate'] = df['fundingRate'].astype(float)
    df = df[['funding_rate']]
    df = df[~df.index.duplicated(keep='last')]
    return df


def align_funding_to_1h(df_1h: pd.DataFrame,
                         df_funding: pd.DataFrame) -> pd.Series:
    """
    资金费率对齐到1H K线：
    - 在结算时点记录当期费率
    - 其余时段前向填充（持有成本持续累积）
    - 额外计算：年化资金费率 = funding_rate × 3 × 365
    """
    if df_funding.empty:
        return pd.Series(0.0, index=df_1h.index, name='funding_rate')
    aligned = df_funding['funding_rate'].reindex(df_1h.index, method='ffill').fillna(0.0)
    return aligned


# ── Layer-3 衍生维度 ──────────────────────────────────────────

def build_funding_features(df_1h: pd.DataFrame,
                            funding_series: pd.Series) -> pd.DataFrame:
    """
    从资金费率构建多个特征维度：
    fr_raw:         当前资金费率（前向填充）
    fr_8h_sum:      过去3次（24H）资金费率累计
    fr_z:           资金费率z-score（90H滚动窗口）
    fr_extreme_long:  费率>+0.10% → 多头拥挤 → 做空信号+1
    fr_extreme_short: 费率<-0.05% → 空头拥挤 → 做多信号+1
    fr_carry_8h:    每8H持有成本（做多时支付/做空时收取）
    """
    fr = funding_series.rename('fr_raw')
    fr_sum24h = fr.rolling(24).sum()
    fr_mean = fr.rolling(90).mean()
    fr_std  = fr.rolling(90).std().replace(0, 1e-9)
    fr_z    = (fr - fr_mean) / fr_std

    features = pd.DataFrame({
        'fr_raw':          fr,
        'fr_24h_sum':      fr_sum24h,
        'fr_z':            fr_z.clip(-4, 4),
        'fr_crowd_short':  (fr >  0.001).astype(int),  # 多头拥挤→空单优势
        'fr_crowd_long':   (fr < -0.0005).astype(int), # 空头拥挤→多单优势
        'fr_extreme':      (fr.abs() > 0.003).astype(int),  # 极端费率预警
    }, index=df_1h.index)
    return features


# ── 合并成完整期货数据集 ──────────────────────────────────────

def build_futures_dataset(sym: str, interval: str,
                           start_dt: datetime, end_dt: datetime,
                           verbose: bool = True) -> pd.DataFrame:
    """
    合并所有层，输出完整期货训练数据集
    """
    if verbose:
        print(f'  [{sym} {interval}] K线...', end=' ', flush=True)
    df_kl = fetch_futures_klines(sym, interval, start_dt, end_dt)
    if df_kl.empty:
        print('❌ 无K线数据')
        return pd.DataFrame()
    if verbose:
        print(f'{len(df_kl):,}根', end=' ', flush=True)

    # 标记价格（仅1h/4h，1d精度足够）
    if verbose:
        print('标记价格...', end=' ', flush=True)
    df_mk = fetch_mark_klines(sym, interval, start_dt, end_dt)
    if not df_mk.empty:
        df_kl = df_kl.join(df_mk, how='left')
        # 基差 = (close - mark_close) / mark_close
        df_kl['basis_pct'] = ((df_kl['close'] - df_kl['mark_close'])
                               / df_kl['mark_close'] * 100).fillna(0)
    else:
        df_kl['mark_close'] = df_kl['close']
        df_kl['basis_pct']  = 0.0
    if verbose:
        print('✓', end=' ', flush=True)

    # 资金费率（仅1h K线对齐）
    if interval == '1h':
        if verbose:
            print('资金费率...', end=' ', flush=True)
        df_fr = fetch_funding_rate(sym, start_dt, end_dt)
        funding_aligned = align_funding_to_1h(df_kl, df_fr)
        fr_features = build_funding_features(df_kl, funding_aligned)
        df_kl = df_kl.join(fr_features, how='left')
        if verbose:
            print(f'✓({len(df_fr)}条)', end=' ', flush=True)
    else:
        # 4h/1d：仅加聚合资金费率均值
        df_fr = fetch_funding_rate(sym, start_dt, end_dt)
        if not df_fr.empty:
            # 按4h/1d周期聚合（mean）
            freq = '4h' if interval == '4h' else '1d'
            fr_agg = df_fr['funding_rate'].resample(freq).mean()
            fr_agg = fr_agg.reindex(df_kl.index, method='ffill').fillna(0.0)
            df_kl['fr_raw'] = fr_agg.values
        else:
            df_kl['fr_raw'] = 0.0

    if verbose:
        print('完成')

    return df_kl


# ══════════════════════════════════════════════════════════════
# 主下载入口
# ══════════════════════════════════════════════════════════════

def download_all(syms=None, intervals=None, years_back=6):
    """
    下载所有标的的期货永续数据
    """
    if syms is None:
        syms = SYMS_CORE
    if intervals is None:
        intervals = ['1h', '4h']

    end_dt   = datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(days=365 * years_back)
    # Binance期货从2019-09开始，限制最早时间
    futures_start = datetime(2019, 9, 10, tzinfo=timezone.utc)
    if start_dt < futures_start:
        start_dt = futures_start

    ts_str = end_dt.strftime('%Y%m%d_%H%M')

    print('═' * 65)
    print('  达摩院 · 期货永续数据下载器 v1.0')
    print(f'  标的: {len(syms)}  周期: {intervals}')
    print(f'  范围: {start_dt.date()} ~ {end_dt.date()}')
    print('═' * 65)

    manifest = {}
    t0 = time.time()

    for sym in syms:
        manifest[sym] = {}
        for interval in intervals:
            t_sym = time.time()
            print(f'\n[{sym} {interval}]', flush=True)
            try:
                df = build_futures_dataset(sym, interval, start_dt, end_dt)
                if df.empty:
                    manifest[sym][interval] = {'status': 'FAILED', 'n': 0}
                    continue

                # 保存parquet
                fname = OUT_DIR / f'{sym.lower()}_{interval}_futures.parquet'
                df.to_parquet(fname, compression='snappy')

                n = len(df)
                cols = list(df.columns)
                elapsed = time.time() - t_sym
                manifest[sym][interval] = {
                    'status': 'OK', 'n': n,
                    'start': str(df.index[0]),
                    'end': str(df.index[-1]),
                    'cols': cols,
                    'file': fname.name,
                    'elapsed_s': round(elapsed, 1),
                }
                print(f'  ✅ 保存: {fname.name}  ({n:,}根  {len(cols)}列  {elapsed:.1f}s)')
                print(f'     列: {cols}')

            except Exception as e:
                import traceback
                print(f'  ❌ 错误: {e}')
                traceback.print_exc()
                manifest[sym][interval] = {'status': 'ERROR', 'error': str(e)}

    # 保存清单
    manifest_path = OUT_DIR / f'manifest_{ts_str}.json'
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))

    elapsed_all = time.time() - t0
    print()
    print('═' * 65)
    ok = sum(1 for s in manifest.values() for v in s.values() if v.get('status') == 'OK')
    total = sum(len(v) for v in manifest.values())
    print(f'  完成: {ok}/{total}  总耗时: {elapsed_all:.0f}s ({elapsed_all/60:.1f}min)')
    print(f'  清单: {manifest_path.name}')
    print('═' * 65)

    return manifest


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--syms',  nargs='+', default=None)
    ap.add_argument('--quick', action='store_true', help='只下BTC+ETH 1H')
    ap.add_argument('--years', type=int, default=6)
    args = ap.parse_args()

    if args.quick:
        download_all(syms=['BTCUSDT','ETHUSDT'], intervals=['1h'], years_back=args.years)
    else:
        download_all(syms=args.syms, years_back=args.years)
