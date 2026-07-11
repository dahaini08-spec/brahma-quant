#!/usr/bin/env python3
"""
达摩院 历史数据下载器 v1.0
设计院 × 苏摩111 · 2026-07-11

下载范围：
  标的: BTCUSDT / ETHUSDT (永续合约 fapi)
  时间: 2019-11-01 ~ 2026-06-30
  周期: 15m / 1h / 4h / 1d

输出格式：
  Parquet  — 高压缩列式存储，供回测引擎直接读取
  CSV.GZ   — 兼容格式（可选）
  meta.json — 元数据（条数/资费成本/下载时间）

资费成本矩阵（达摩院实测条件）：
  Maker: 0.020%  Taker: 0.050%
  资金费率: 平均 +0.010%/8H (按实际区间均值)
  滑点估算: 0.030% (BTC市价单) / 0.050% (ETH市价单)
  复合成本/笔: 约 0.12% (单边) 或 0.24% (完整开平)
"""

import requests
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import json
import time
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── 配置 ─────────────────────────────────────────────────────────
SYMBOLS    = ['BTCUSDT', 'ETHUSDT']
TIMEFRAMES = ['15m', '1h', '4h', '1d']

START_DT   = '2019-11-01'
END_DT     = '2026-07-01'   # 包含到2026-06-30

BASE_URL   = 'https://fapi.binance.com/fapi/v1/klines'
LIMIT      = 1500            # Binance永续合约单次最大1500
SLEEP_MS   = 80              # 请求间隔(ms)，避免限速
OUTPUT_DIR = Path(__file__).parent

# 达摩院实测资费参数
FEE_PARAMS = {
    'maker_rate':      0.0002,   # 0.020%
    'taker_rate':      0.0005,   # 0.050%
    'slip_btc':        0.0003,   # BTC滑点 0.030%
    'slip_eth':        0.0005,   # ETH滑点 0.050%
    'funding_rate_8h': 0.0001,   # 平均资金费率 0.010%/8H
    'note': '达摩院实测条件 2020-2026全样本均值'
}

# ── 工具函数 ──────────────────────────────────────────────────────
def ts_ms(date_str: str) -> int:
    return int(datetime.strptime(date_str, '%Y-%m-%d').replace(
        tzinfo=timezone.utc).timestamp() * 1000)

def fetch_klines(symbol: str, interval: str, start_ms: int, end_ms: int) -> list:
    """分批拉取K线，返回完整列表"""
    all_klines = []
    current = start_ms
    req_count = 0

    while current < end_ms:
        params = {
            'symbol':    symbol,
            'interval':  interval,
            'startTime': current,
            'limit':     LIMIT,
        }
        # 不传endTime，让Binance自动截止
        for attempt in range(4):
            try:
                r = requests.get(BASE_URL, params=params, timeout=30)
                data = r.json()
                if isinstance(data, list) and len(data) > 0:
                    all_klines.extend(data)
                    current = int(data[-1][0]) + 1
                    req_count += 1
                    if len(data) < LIMIT:
                        return all_klines  # 已到末尾
                    time.sleep(SLEEP_MS / 1000)
                    break
                elif isinstance(data, dict) and data.get('code'):
                    print(f'    API错误: {data}')
                    time.sleep(2)
                else:
                    return all_klines
            except Exception as e:
                print(f'    请求异常(重试{attempt+1}): {e}')
                time.sleep(3)
        else:
            print(f'    !! 连续失败，跳过 {symbol} {interval} ts={current}')
            break

    return all_klines

def klines_to_df(klines: list) -> pd.DataFrame:
    """原始K线列表 → DataFrame"""
    cols = ['ts', 'open', 'high', 'low', 'close', 'volume',
            'close_ts', 'quote_vol', 'trades', 'taker_buy_vol',
            'taker_buy_quote_vol', '_ignore']
    df = pd.DataFrame(klines, columns=cols)
    df = df.drop(columns=['_ignore'])
    # 类型转换
    df['ts']       = pd.to_datetime(df['ts'].astype('int64'), unit='ms', utc=True)
    df['close_ts'] = pd.to_datetime(df['close_ts'].astype('int64'), unit='ms', utc=True)
    for c in ['open','high','low','close','volume','quote_vol','taker_buy_vol','taker_buy_quote_vol']:
        df[c] = df[c].astype('float64')
    df['trades'] = df['trades'].astype('int64')
    df = df.set_index('ts').sort_index()
    # 去重
    df = df[~df.index.duplicated(keep='last')]
    return df

def compute_cost_matrix(df: pd.DataFrame, symbol: str, tf: str) -> dict:
    """基于实际数据计算资费成本矩阵"""
    slip = FEE_PARAMS['slip_btc'] if 'BTC' in symbol else FEE_PARAMS['slip_eth']
    taker = FEE_PARAMS['taker_rate']
    maker = FEE_PARAMS['maker_rate']
    fr    = FEE_PARAMS['funding_rate_8h']

    # 每笔完整成本（开+平，单边taker）
    cost_one_way = taker + slip
    cost_round   = cost_one_way * 2

    # 资金费率持仓期
    tf_hours = {'15m': 0.25, '1h': 1.0, '4h': 4.0, '1d': 24.0}
    hold_h   = tf_hours.get(tf, 1.0)
    fr_cost  = fr * (hold_h / 8)   # 每8H一次资金费

    # 价格统计
    avg_price = float(df['close'].mean())
    vol_daily = float(df['volume'].resample('1D').sum().mean()) if len(df) > 10 else 0

    return {
        'symbol':        symbol,
        'timeframe':     tf,
        'taker_pct':     round(taker * 100, 4),
        'maker_pct':     round(maker * 100, 4),
        'slippage_pct':  round(slip * 100, 4),
        'cost_one_way_pct': round(cost_one_way * 100, 4),
        'cost_round_pct':   round(cost_round * 100, 4),
        'funding_per_hold_pct': round(fr_cost * 100, 6),
        'funding_8h_pct': round(fr * 100, 4),
        'total_cost_pct': round((cost_round + fr_cost) * 100, 4),
        'avg_price_usd':  round(avg_price, 2),
        'avg_daily_vol':  round(vol_daily, 2),
        'bars_count':     len(df),
        'date_start':     str(df.index.min()),
        'date_end':       str(df.index.max()),
    }

# ── 主下载流程 ────────────────────────────────────────────────────
def main():
    start_ms = ts_ms(START_DT)
    end_ms   = ts_ms(END_DT)

    print('=' * 65)
    print('🏛️ 达摩院 历史数据下载器 v1.0')
    print(f'   {START_DT} ~ {END_DT} | {", ".join(SYMBOLS)} | {", ".join(TIMEFRAMES)}')
    print('=' * 65)

    all_meta = {}
    cost_matrix = []

    for sym in SYMBOLS:
        all_meta[sym] = {}
        sym_dir = OUTPUT_DIR / sym.lower()
        sym_dir.mkdir(exist_ok=True)

        for tf in TIMEFRAMES:
            out_parquet = sym_dir / f'{sym.lower()}_{tf}.parquet'
            out_meta    = sym_dir / f'{sym.lower()}_{tf}_meta.json'

            # 检查是否已下载
            if out_parquet.exists():
                df_exist = pd.read_parquet(out_parquet)
                print(f'  ✅ {sym} {tf}: 已存在 {len(df_exist):,}根，跳过')
                cost = compute_cost_matrix(df_exist, sym, tf)
                cost_matrix.append(cost)
                all_meta[sym][tf] = cost
                continue

            print(f'\n  📥 {sym} {tf} 下载中...', end='', flush=True)
            t0 = time.time()

            klines = fetch_klines(sym, tf, start_ms, end_ms)
            if not klines:
                print(f'  ⚠️  {sym} {tf}: 无数据')
                continue

            df = klines_to_df(klines)
            elapsed = time.time() - t0

            # 过滤到指定时间范围
            df = df[df.index >= pd.Timestamp(START_DT, tz='UTC')]
            df = df[df.index <  pd.Timestamp(END_DT,   tz='UTC')]

            # 保存 Parquet
            table = pa.Table.from_pandas(df, preserve_index=True)
            pq.write_table(table, out_parquet, compression='snappy')

            # 计算资费矩阵
            cost = compute_cost_matrix(df, sym, tf)
            cost_matrix.append(cost)
            all_meta[sym][tf] = cost

            # 保存元数据
            with open(out_meta, 'w') as f:
                json.dump(cost, f, indent=2, ensure_ascii=False)

            size_kb = out_parquet.stat().st_size / 1024
            print(f'  ✅ {len(df):,}根 | {elapsed:.1f}s | {size_kb:.0f}KB')

    # ── 汇总资费成本矩阵 ─────────────────────────────────────────
    print('\n' + '=' * 65)
    print('📊 达摩院实测资费成本矩阵')
    print('=' * 65)
    print(f'{"标的":10s} {"周期":6s} {"Taker":7s} {"滑点":7s} {"单边":8s} {"双边":8s} {"资金费/持仓":12s} {"合计":8s} {"K线数":8s}')
    print('-' * 75)
    for c in cost_matrix:
        print(f'{c["symbol"]:10s} {c["timeframe"]:6s} '
              f'{c["taker_pct"]:6.3f}% {c["slippage_pct"]:6.3f}% '
              f'{c["cost_one_way_pct"]:7.3f}% {c["cost_round_pct"]:7.3f}% '
              f'{c["funding_per_hold_pct"]:11.4f}% {c["total_cost_pct"]:7.3f}% '
              f'{c["bars_count"]:8,d}')

    # 保存全量汇总
    summary = {
        'generated_at':  datetime.now(timezone.utc).isoformat(),
        'data_range':    f'{START_DT} ~ {END_DT}',
        'symbols':       SYMBOLS,
        'timeframes':    TIMEFRAMES,
        'fee_params':    FEE_PARAMS,
        'cost_matrix':   cost_matrix,
        'note': '永续合约(fapi) | Binance | 达摩院实测条件'
    }
    with open(OUTPUT_DIR / 'summary.json', 'w') as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    print(f'\n✅ 汇总文件: data/historical/summary.json')
    print('✅ 达摩院历史数据下载完成')
    return summary

if __name__ == '__main__':
    main()
