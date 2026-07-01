#!/usr/bin/env python3
"""
达摩院 · 中小币数据集构建器 v1.0
================================
[P1-2 设计院 2026-06-24]

目标：为实盘核心中小币建立历史数据集 + 运行离线回放
标的：SOLUSDT / NEARUSDT / MANAUSDT / AXSUSDT / GALAUSDT
原因：铁证矩阵目前只有BTC+ETH，中小币实盘WR崩塌根因是训练域错配

输出：
  data/backtest/fixed/{sym}_1h_fixed.parquet
  data/backtest/fixed/{sym}_4h_fixed.parquet
  data/altcoin_iron_evidence.json   ← 各标的WR矩阵
"""
import sys, json, time, warnings
from pathlib import Path
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests

warnings.filterwarnings('ignore')

BASE    = Path('/root/.openclaw/workspace/trading-system')
OUT_DIR = BASE / 'data' / 'backtest' / 'fixed'
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ── 目标标的 ──────────────────────────────────────────
ALTCOINS = ['SOLUSDT', 'NEARUSDT', 'MANAUSDT', 'AXSUSDT', 'GALAUSDT']
INTERVALS = ['1h', '4h']
DATA_START = datetime(2020, 10, 1, tzinfo=timezone.utc)   # 大部分标的2020年上线
DATA_END   = datetime(2026, 6, 24, tzinfo=timezone.utc)

FAPI = 'https://fapi.binance.com'

# ── 工具函数 ──────────────────────────────────────────
def fetch_klines(sym, interval, start_ms, end_ms):
    rows, cur = [], start_ms
    while cur < end_ms:
        for attempt in range(5):
            try:
                r = requests.get(f'{FAPI}/fapi/v1/klines', params={
                    'symbol': sym, 'interval': interval,
                    'startTime': cur, 'endTime': end_ms, 'limit': 1000,
                }, timeout=20)
                r.raise_for_status()
                data = r.json()
                break
            except Exception as e:
                if attempt == 4:
                    print(f'  [WARN] fetch失败 {e}')
                    data = []
                time.sleep(2 ** attempt)
        if not data:
            break
        rows.extend(data)
        cur = data[-1][0] + 1
        if len(data) < 1000:
            break
        time.sleep(0.1)
    return rows

def build_df(rows):
    df = pd.DataFrame(rows, columns=[
        'open_time','open','high','low','close','volume',
        'close_time','quote_volume','trades','taker_buy_base',
        'taker_buy_quote','ignore'
    ])
    df['open_time'] = pd.to_datetime(df['open_time'], unit='ms', utc=True)
    df.set_index('open_time', inplace=True)
    for col in ['open','high','low','close','volume']:
        df[col] = df[col].astype(float)
    df = df[['open','high','low','close','volume']]
    df = df[~df.index.duplicated(keep='last')]
    df.sort_index(inplace=True)
    return df

def add_indicators(df):
    c = df['close'].values
    h = df['high'].values
    l = df['low'].values

    # EMA
    def ema(arr, p):
        out = np.full(len(arr), np.nan)
        k = 2/(p+1)
        start = next((i for i,v in enumerate(arr) if not np.isnan(v)), None)
        if start is None: return out
        out[start] = arr[start]
        for i in range(start+1, len(arr)):
            out[i] = arr[i]*k + out[i-1]*(1-k)
        return out

    df['ema21']  = ema(c, 21)
    df['ema55']  = ema(c, 55)
    df['ema200'] = ema(c, 200)

    # RSI Wilder
    def rsi_wilder(arr, p=14):
        out = np.full(len(arr), np.nan)
        d = np.diff(arr.astype(float))
        if len(d) < p: return out
        gains = np.where(d > 0, d, 0.0)
        losses = np.where(d < 0, -d, 0.0)
        ag = gains[:p].mean()
        al = losses[:p].mean()
        if al == 0: out[p] = 100.0
        else: out[p] = 100 - 100/(1+ag/al)
        for i in range(p, len(d)):
            ag = (ag*(p-1)+gains[i])/p
            al = (al*(p-1)+losses[i])/p
            out[i+1] = 100-100/(1+ag/al) if al > 0 else 100.0
        return out

    df['rsi'] = rsi_wilder(c)

    # ATR
    tr = np.maximum(h[1:]-l[1:],
         np.maximum(np.abs(h[1:]-c[:-1]),
                    np.abs(l[1:]-c[:-1])))
    atr = np.full(len(c), np.nan)
    p = 14
    if len(tr) >= p:
        atr[p] = tr[:p].mean()
        for i in range(p, len(tr)):
            atr[i+1] = (atr[i]*(p-1)+tr[i])/p
    df['atr14'] = atr

    return df

def detect_regime_simple(df_1h, df_4h):
    """简化版体制标注（基于EMA排列 + RSI）"""
    regimes = []
    for i in range(len(df_1h)):
        row = df_1h.iloc[i]
        e21, e55, e200 = row.get('ema21',0), row.get('ema55',0), row.get('ema200',0)
        rsi = row.get('rsi', 50)
        c   = row['close']

        if all(x > 0 for x in [e21, e55, e200]):
            if e21 > e55 > e200 and c > e21:
                r = 'BULL_TREND' if rsi > 60 else 'BULL_EARLY'
            elif e21 < e55 and e55 < e200 and c < e21:
                if rsi < 40:
                    r = 'BEAR_TREND'
                elif rsi < 55:
                    r = 'BEAR_EARLY'
                else:
                    r = 'BEAR_RECOVERY'
            elif e200 > e21 and c > e21:
                r = 'BEAR_RECOVERY'
            else:
                r = 'CHOP_MID'
        else:
            r = 'CHOP_MID'
        regimes.append(r)
    df_1h['regime'] = regimes
    return df_1h

# ── 简化回放引擎 ────────────────────────────────────────
def run_replay(sym, df_1h):
    """
    简化体制×方向回放：
    信号条件：EMA21穿越 + RSI过滤
    止损：2×ATR  止盈：4×ATR  超时：72根
    """
    ATR_SL = 2.0
    ATR_TP = 4.0
    MAX_BARS = 72

    trades = []
    in_trade = False
    trade = {}
    bars = 0

    rows = list(df_1h.itertuples())
    i = 1
    while i < len(rows):
        row = rows[i]
        prev = rows[i-1]
        c, h, l = row.close, row.high, row.low
        e21, e55, rsi, atr = row.ema21, row.ema55, row.rsi, row.atr14
        regime = getattr(row, 'regime', 'CHOP_MID')

        if any(v != v for v in [e21, e55, rsi, atr]):  # NaN check
            i += 1; continue

        if not in_trade:
            # LONG：多头体制 + EMA21金叉
            if regime in ('BULL_EARLY','BULL_TREND') and \
               prev.close < prev.ema21 and c > e21 and rsi < 65 and e21 > e55:
                sl  = c - ATR_SL * atr
                tp  = c + ATR_TP * atr
                trade = dict(ts=str(row.Index), entry=c, sl=sl, tp=tp,
                             dir='LONG', regime=regime, tp1_hit=False)
                in_trade = True; bars = 0

            # SHORT：空头体制 + EMA21死叉
            elif regime in ('BEAR_EARLY','BEAR_TREND') and \
                 prev.close > prev.ema21 and c < e21 and rsi > 35 and e21 < e55:
                sl  = c + ATR_SL * atr
                tp  = c - ATR_TP * atr
                trade = dict(ts=str(row.Index), entry=c, sl=sl, tp=tp,
                             dir='SHORT', regime=regime, tp1_hit=False)
                in_trade = True; bars = 0

        else:
            bars += 1
            entry, sl, tp, dir_ = trade['entry'], trade['sl'], trade['tp'], trade['dir']
            exit_p, reason = None, None

            if dir_ == 'LONG':
                if l <= sl:      exit_p, reason = sl, 'SL'
                elif h >= tp:    exit_p, reason = tp, 'TP'
                elif bars >= MAX_BARS: exit_p, reason = c, 'TIMEOUT'
            else:
                if h >= sl:      exit_p, reason = sl, 'SL'
                elif l <= tp:    exit_p, reason = tp, 'TP'
                elif bars >= MAX_BARS: exit_p, reason = c, 'TIMEOUT'

            if exit_p:
                pnl = (exit_p-entry)/entry*100 if dir_=='LONG' else (entry-exit_p)/entry*100
                trades.append({
                    'sym': sym, 'dir': dir_, 'regime': trade['regime'],
                    'entry': round(entry,6), 'exit': round(exit_p,6),
                    'pnl_pct': round(pnl,4), 'reason': reason, 'bars': bars
                })
                in_trade = False; trade = {}; bars = 0
        i += 1

    return trades

def calc_wr_matrix(trades):
    """计算体制×方向WR矩阵"""
    from collections import defaultdict, Counter
    groups = defaultdict(list)
    for t in trades:
        key = f"{t['regime']}_{t['dir']}"
        groups[key].append(t['pnl_pct'])

    matrix = {}
    for key, pnls in groups.items():
        if len(pnls) < 10:
            continue
        wins = [p for p in pnls if p > 0]
        matrix[key] = {
            'n': len(pnls),
            'wr': round(len(wins)/len(pnls)*100, 1),
            'avg_pnl': round(sum(pnls)/len(pnls), 4),
            'total_pnl': round(sum(pnls), 2),
        }
    return dict(sorted(matrix.items(), key=lambda x: -x[1]['wr']))

# ── 主流程 ────────────────────────────────────────────
def main():
    results = {}
    start_ms = int(DATA_START.timestamp() * 1000)
    end_ms   = int(DATA_END.timestamp() * 1000)

    for sym in ALTCOINS:
        print(f'\n{"━"*55}')
        print(f'▶ {sym} 开始处理...')

        # 抓取 1H 数据
        print(f'  [1/4] 拉取1H K线...')
        rows_1h = fetch_klines(sym, '1h', start_ms, end_ms)
        if not rows_1h:
            print(f'  ❌ {sym} 无数据，跳过')
            continue

        df_1h = build_df(rows_1h)
        df_1h = add_indicators(df_1h)
        print(f'  1H: {len(df_1h):,}根  {df_1h.index[0].date()} ~ {df_1h.index[-1].date()}')

        # 抓取 4H 数据
        print(f'  [2/4] 拉取4H K线...')
        rows_4h = fetch_klines(sym, '4h', start_ms, end_ms)
        df_4h = build_df(rows_4h)
        df_4h = add_indicators(df_4h)
        print(f'  4H: {len(df_4h):,}根')

        # 体制标注
        print(f'  [3/4] 体制标注...')
        df_1h = detect_regime_simple(df_1h, df_4h)

        # 保存数据集
        out_1h = OUT_DIR / f'{sym.lower()}_1h_fixed.parquet'
        out_4h = OUT_DIR / f'{sym.lower()}_4h_fixed.parquet'
        df_1h.to_parquet(out_1h)
        df_4h.to_parquet(out_4h)
        print(f'  ✅ 数据保存: {out_1h.name}  {out_4h.name}')

        # 回放
        print(f'  [4/4] 离线回放...')
        trades = run_replay(sym, df_1h)
        matrix = calc_wr_matrix(trades)

        print(f'  回放完成: {len(trades)}条信号')
        print(f'  体制×方向WR矩阵:')
        for key, m in matrix.items():
            flag = '✅' if m['wr'] >= 55 else ('🟡' if m['wr'] >= 45 else '🔴')
            print(f'    {flag} {key:<28} n={m["n"]:4d}  WR={m["wr"]:5.1f}%  avg={m["avg_pnl"]:+.4f}%')

        results[sym] = {
            'n_bars_1h': len(df_1h),
            'period': f'{df_1h.index[0].date()} ~ {df_1h.index[-1].date()}',
            'n_trades': len(trades),
            'wr_matrix': matrix,
        }

    # 保存结果
    out_path = BASE / 'data' / 'altcoin_iron_evidence.json'
    with open(out_path, 'w') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f'\n✅ 中小币铁证矩阵已写入: {out_path}')

    # 与BTC/ETH铁证对比
    try:
        btc_eth = json.load(open(BASE/'data'/'dharma_iron_evidence.json'))
        rdm = btc_eth.get('regime_direction_matrix',{})
        print('\n=== 铁证对比：BTC/ETH离线 vs 中小币离线 ===')
        keys_of_interest = ['BEAR_TREND_SHORT','BEAR_EARLY_SHORT','BULL_EARLY_LONG','BEAR_RECOVERY_LONG']
        for sym, data in results.items():
            print(f'\n  {sym}:')
            for key in keys_of_interest:
                alt = data['wr_matrix'].get(key)
                ref = rdm.get(key,{})
                if alt:
                    ref_wr = ref.get('wr',0)*100 if ref else 0
                    print(f'    {key:<28} 中小币WR={alt["wr"]:5.1f}%(n={alt["n"]:3d})  BTC/ETH铁证={ref_wr:.1f}%  差={alt["wr"]-ref_wr:+.1f}pp')
    except Exception as e:
        print(f'对比失败: {e}')

if __name__ == '__main__':
    main()
