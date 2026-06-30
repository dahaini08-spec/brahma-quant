#!/usr/bin/env python3
"""
precise_entry_backtest.py — 精准入场对照实验 v1.0
══════════════════════════════════════════════════
目标：验证 CHoCH精准触发 能否将R:R从0.78提升至1.5+

对照组A（当前）：梵天信号触发即入场，止损=OB上沿
实验组B（新版）：等待15M CHoCH确认后入场，止损=CHoCH前低点

苏摩批准：2026-06-28 11:42 UTC
"""
import sys, json, warnings, os
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict

warnings.filterwarnings('ignore')
os.environ['OFFLINE_REPLAY'] = '1'

BASE    = Path('/root/.openclaw/workspace/trading-system')
FIXED   = BASE / 'data' / 'backtest' / 'fixed'
RESULTS = BASE / 'dharma' / 'results'
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'brahma_brain'))

import numpy as np
import pandas as pd

TAG = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')


def _ema(arr, period):
    out = np.empty_like(arr, dtype=float)
    out[:] = np.nan
    k = 2 / (period + 1)
    start = 0
    while start < len(arr) and np.isnan(arr[start]):
        start += 1
    if start >= len(arr): return out
    out[start] = arr[start]
    for i in range(start + 1, len(arr)):
        out[i] = arr[i] * k + out[i-1] * (1 - k)
    return out


def _rsi(close, period=14):
    delta = np.diff(close, prepend=close[0])
    gain  = np.where(delta > 0, delta, 0.0)
    loss  = np.where(delta < 0, -delta, 0.0)
    avg_gain = np.full_like(close, np.nan)
    avg_loss = np.full_like(close, np.nan)
    if len(close) < period + 1: return avg_gain
    avg_gain[period] = gain[1:period+1].mean()
    avg_loss[period] = loss[1:period+1].mean()
    for i in range(period + 1, len(close)):
        avg_gain[i] = (avg_gain[i-1] * (period-1) + gain[i]) / period
        avg_loss[i] = (avg_loss[i-1] * (period-1) + loss[i]) / period
    rs = np.where(avg_loss == 0, 100.0, avg_gain / avg_loss)
    rsi_val = np.where(np.isnan(avg_loss), np.nan, 100 - 100 / (1 + rs))
    return rsi_val


def detect_choch_15m(df15m, signal_idx, direction, lookback=8):
    """
    在信号触发后的15M K线里寻找CHoCH（结构转换）
    direction='SHORT': 找bearish CHoCH（新低后反弹失败创新低）
    返回：(choch_idx, choch_close, stop_ref) 或 None
    """
    closes = df15m['close'].values
    lows   = df15m['low'].values
    highs  = df15m['high'].values
    n = len(closes)

    # 在信号后最多lookback根K线内找CHoCH
    for i in range(signal_idx + 1, min(signal_idx + lookback + 1, n - 1)):
        if direction == 'SHORT':
            # bearish CHoCH: 价格反弹后创新低 → 入场在新低确认K线收盘
            recent_high = highs[max(0, i-3):i].max() if i > 0 else highs[i]
            if closes[i] < lows[max(0, i-3):i].min() and i > signal_idx + 1:
                stop_ref = recent_high * 1.003  # 止损在近期高点上方0.3%
                return i, closes[i], stop_ref
        else:  # LONG
            recent_low = lows[max(0, i-3):i].min() if i > 0 else lows[i]
            if closes[i] > highs[max(0, i-3):i].max() and i > signal_idx + 1:
                stop_ref = recent_low * 0.997
                return i, closes[i], stop_ref
    return None


def run_comparison(sym, filter_regime=None, filter_tf='15M', n_min=300):
    """
    对同一批信号跑 A/B 对比实验
    """
    print(f"\n{'='*60}")
    print(f"[精准入场实验] {sym}  tf={filter_tf}  regime={filter_regime or '全部'}")

    # 读取最新回放数据
    jsonl_files = sorted(RESULTS.glob(f'replay_{sym.lower()}*.jsonl'),
                         key=lambda f: f.stat().st_mtime, reverse=True)
    if not jsonl_files:
        print(f"  ❌ 无回放数据"); return None

    signals = []
    with open(jsonl_files[0]) as f:
        for line in f:
            try: signals.append(json.loads(line))
            except: pass

    # 过滤：体制 + 周期 + 通过门控 + 有效结果
    filtered = [s for s in signals
                if s.get('pass') == True
                and s.get('result') in ('TP', 'SL')
                and s.get('tf', '') == filter_tf
                and (filter_regime is None or s.get('regime') == filter_regime)]

    print(f"  过滤后信号: {len(filtered)}个")
    if len(filtered) < n_min:
        print(f"  ⚠️ 样本不足（需≥{n_min}）"); return None

    # 读取15M原始数据
    parquet = FIXED / f'{sym.lower()}_15m_fixed.parquet'
    if not parquet.exists():
        print(f"  ❌ 缺少parquet数据"); return None

    df15m = pd.read_parquet(parquet)
    df15m.sort_index(inplace=True)

    closes = df15m['close'].values
    lows   = df15m['low'].values
    highs  = df15m['high'].values
    ts_arr = df15m.index

    # ── 组A：当前系统（原始信号结果）──
    A_results = []
    for s in filtered:
        result  = s.get('result')
        pnl     = float(s.get('pnl_pct', 0))
        A_results.append({'result': result, 'pnl': pnl})

    # ── 组B：CHoCH精准触发 ──
    B_results = []
    choch_found = 0
    target_rr   = 2.0  # 固定目标：止损距离×2.0

    for s in filtered:
        # 找信号对应的15M K线索引
        ts_raw = s.get('ts')
        ts = pd.Timestamp(ts_raw)
        # 时区对齐
        if ts.tzinfo is not None and df15m.index.tzinfo is None:
            ts = ts.tz_localize(None)
        elif ts.tzinfo is None and df15m.index.tzinfo is not None:
            ts = ts.tz_localize('UTC')
        idx_arr = df15m.index.searchsorted(ts)
        if idx_arr >= len(df15m) - 10:
            continue

        direction = s.get('direction', 'SHORT')
        entry_price = float(s.get('price', closes[idx_arr]))

        # 寻找CHoCH
        choch = detect_choch_15m(df15m, idx_arr, direction, lookback=6)

        if choch is None:
            # 未找到CHoCH → 该信号跳过（B组不交易）
            B_results.append({'result': 'SKIP', 'pnl': 0})
            continue

        choch_idx, choch_entry, stop_ref = choch
        choch_found += 1

        # 计算止损距离
        if direction == 'SHORT':
            sl_dist = (stop_ref - choch_entry) / choch_entry
            tp_target = choch_entry * (1 - sl_dist * target_rr)
        else:
            sl_dist = (choch_entry - stop_ref) / choch_entry
            tp_target = choch_entry * (1 + sl_dist * target_rr)

        # 模拟持仓（最多20根15M K线）
        result = 'TIMEOUT'
        pnl    = 0.0
        for j in range(choch_idx + 1, min(choch_idx + 21, len(df15m))):
            if direction == 'SHORT':
                if lows[j] <= tp_target:
                    result = 'TP'
                    pnl = -sl_dist * target_rr * 100
                    break
                if highs[j] >= stop_ref:
                    result = 'SL'
                    pnl = -sl_dist * 100
                    break
            else:
                if highs[j] >= tp_target:
                    result = 'TP'
                    pnl = sl_dist * target_rr * 100
                    break
                if lows[j] <= stop_ref:
                    result = 'SL'
                    pnl = -sl_dist * 100
                    break

        B_results.append({'result': result, 'pnl': pnl})

    # ── 统计 ──
    def stats(results, name):
        tp  = [r for r in results if r['result'] == 'TP']
        sl  = [r for r in results if r['result'] == 'SL']
        tot = len(tp) + len(sl)
        wr  = len(tp) / tot if tot > 0 else 0
        avg_win  = sum(r['pnl'] for r in tp)  / len(tp)  if tp  else 0
        avg_loss = sum(abs(r['pnl']) for r in sl) / len(sl) if sl else 0
        rr   = avg_win / avg_loss if avg_loss > 0 else 0
        ev   = wr * avg_win - (1-wr) * avg_loss
        skip = sum(1 for r in results if r['result'] == 'SKIP')
        to   = sum(1 for r in results if r['result'] == 'TIMEOUT')
        print(f"\n  [{name}]")
        print(f"    总信号={tot+skip+to}  有效={tot}  跳过={skip}  超时={to}")
        print(f"    WR={wr:.1%}  TP={len(tp)}  SL={len(sl)}")
        print(f"    平均盈利={avg_win:+.3f}%  平均亏损={avg_loss:.3f}%")
        print(f"    真实R:R={rr:.2f}:1  期望值EV={ev:+.4f}%/笔")
        return {'wr': wr, 'rr': rr, 'ev': ev, 'n': tot}

    a = stats(A_results, '组A 当前系统')
    b = stats(B_results, '组B CHoCH精准触发')

    print(f"\n  ──── 对比结论 ────")
    print(f"    WR变化:  {a['wr']:.1%} → {b['wr']:.1%}  ({b['wr']-a['wr']:+.1%})")
    print(f"    R:R变化: {a['rr']:.2f} → {b['rr']:.2f}  ({b['rr']-a['rr']:+.2f})")
    print(f"    EV变化:  {a['ev']:+.4f}% → {b['ev']:+.4f}%  ({b['ev']-a['ev']:+.4f}%)")
    print(f"    CHoCH命中率: {choch_found}/{len(filtered)} = {choch_found/max(len(filtered),1):.1%}")

    return {'sym': sym, 'A': a, 'B': b, 'choch_rate': choch_found/max(len(filtered),1)}


if __name__ == '__main__':
    print("🔬 精准入场对照实验 v1.0")
    print("   目标：验证CHoCH触发是否提升R:R")
    print("   苏摩批准 · 2026-06-28")

    all_results = {}

    # 优先验证最强信号组合
    for sym in ['BTCUSDT', 'ETHUSDT']:
        for regime in ['BEAR_TREND', 'BULL_TREND']:
            direction = 'SHORT' if regime == 'BEAR_TREND' else 'LONG'
            r = run_comparison(sym, filter_regime=regime, filter_tf='15M', n_min=200)
            if r:
                all_results[f'{sym}_{regime}'] = r

    # 汇总报告
    print(f"\n{'='*60}")
    print("📊 实验汇总报告")
    print(f"{'='*60}")
    for key, r in all_results.items():
        a, b = r['A'], r['B']
        ev_lift = (b['ev'] - a['ev']) / abs(a['ev']) * 100 if a['ev'] != 0 else 0
        verdict = '✅ 有效提升' if b['rr'] > 1.2 and b['ev'] > a['ev'] else '⚠️ 效果有限'
        print(f"\n  {key}")
        print(f"    R:R: {a['rr']:.2f}→{b['rr']:.2f}  EV提升: {ev_lift:+.0f}%  {verdict}")

    # 保存结果
    out = RESULTS / f'precise_entry_experiment_{TAG}.json'
    with open(out, 'w') as f:
        json.dump({
            'tag': TAG,
            'description': 'CHoCH精准触发对照实验',
            'approved_by': '苏摩 2026-06-28 11:42 UTC',
            'results': all_results
        }, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n  报告已保存: {out.name}")
