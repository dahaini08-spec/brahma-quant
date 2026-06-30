#!/usr/bin/env python3
"""
达摩院 · hold_max 持仓时限敏感性测试
设计院×达摩院 · 2026-06-27

目标：找到 BEAR_TREND_SHORT 最优持仓窗口（8/12/16/20根15M）
铁证依据：
  BEAR_TREND_SHORT WR=71.3%（16根当前）
  1H持仓窗口 WR=65.1%（差7.2pp）
  = 持仓时限直接影响胜率

测试矩阵：
  15M hold_max: [8, 12, 16, 20]（当前=16）
  1H  hold_max: [12, 18, 24]   （当前=24）
  关注体制: BEAR_TREND_SHORT / BULL_TREND_LONG / BEAR_EARLY_SHORT
"""

import sys, json, os, time
from pathlib import Path
from datetime import datetime, timezone
import pandas as pd

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'brahma_brain'))

from dharma.offline_brahma_replay import scan_signals_multi_tf, generate_report

RESULTS = BASE / 'dharma' / 'results'
TAG = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
FIXED = BASE / 'data' / 'backtest' / 'fixed'

# ── 测试参数矩阵 ────────────────────────────────────────────────
M15_HOLD_CONFIGS = [8, 12, 16, 20]   # 当前=16
H1_HOLD_CONFIGS  = [12, 18, 24]      # 当前=24

TARGET_REGIMES = {
    'BEAR_TREND_SHORT', 'BULL_TREND_LONG',
    'BEAR_EARLY_SHORT', 'BULL_EARLY_LONG',
}

def extract_regime_wr(report: dict) -> dict:
    """提取关键体制 WR 指标"""
    out = {}
    br = report.get('by_regime', {})
    for k in TARGET_REGIMES:
        v = br.get(k, {})
        if v.get('n', 0) >= 100:
            out[k] = {
                'wr':     round(v['wr'], 4),
                'n':      v['n'],
                'pnl':    round(v['avg_pnl_pct'], 4),
                'tp':     v.get('tp', 0),
                'sl':     v.get('sl', 0),
                'to':     v.get('to', 0),
            }
    ps = report.get('passed_stats', {})
    out['_overall'] = {
        'wr':     round(ps.get('wr', 0), 4),
        'n':      ps.get('n', 0),
        'pnl':    round(ps.get('avg_pnl_pct', 0), 4),
    }
    return out

def run_one(sym, df15m, df1h, df4h, df1d, m15_hold, h1_hold):
    """单组 hold_max 参数跑一次回放"""
    records = scan_signals_multi_tf(
        sym=sym, df15m=df15m, df1h=df1h, df4h=df4h, df1d=df1d,
        verbose=False, out_path=None,
        m15_hold_max=m15_hold,
        h1_hold_max=h1_hold,
    )
    report = generate_report(records, sym)
    return extract_regime_wr(report)

def main():
    os.environ['OFFLINE_REPLAY'] = '1'
    results = {}
    t0 = time.time()

    for sym in ['BTCUSDT', 'ETHUSDT']:
        print(f'\n{"="*60}')
        print(f'  {sym} hold_max 敏感性测试')
        print(f'{"="*60}')

        sym_l = sym.lower()
        df15m = pd.read_parquet(FIXED / f'{sym_l}_15m_fixed.parquet')
        df1h  = pd.read_parquet(FIXED / f'{sym_l}_1h_fixed.parquet')
        df4h  = pd.read_parquet(FIXED / f'{sym_l}_4h_fixed.parquet')
        df1d  = pd.read_parquet(FIXED / f'{sym_l}_1d_fixed.parquet')
        print(f'  数据加载: 15M={len(df15m):,}根')

        results[sym] = {}

        # 基准（当前值 16/24）
        print(f'\n  基准(16/24)...')
        t1 = time.time()
        base_r = run_one(sym, df15m, df1h, df4h, df1d, 16, 24)
        results[sym]['m15_16_h1_24'] = base_r
        print(f'  基准 WR={base_r["_overall"]["wr"]:.1%} n={base_r["_overall"]["n"]:,} ({time.time()-t1:.0f}s)')

        # 15M 测试（固定 h1=24）
        print(f'\n  15M hold_max 测试（h1固定=24）:')
        for m in M15_HOLD_CONFIGS:
            if m == 16:
                continue  # 已跑基准
            t1 = time.time()
            r = run_one(sym, df15m, df1h, df4h, df1d, m, 24)
            key = f'm15_{m}_h1_24'
            results[sym][key] = r
            bts = r.get('BEAR_TREND_SHORT', {})
            print(f'    m15={m}: WR={r["_overall"]["wr"]:.1%} | BEAR_TREND_SHORT WR={bts.get("wr",0):.1%} n={bts.get("n",0)} ({time.time()-t1:.0f}s)')

        # 1H 测试（固定 m15=16）
        print(f'\n  1H hold_max 测试（m15固定=16）:')
        for h in H1_HOLD_CONFIGS:
            if h == 24:
                continue  # 已跑基准
            t1 = time.time()
            r = run_one(sym, df15m, df1h, df4h, df1d, 16, h)
            key = f'm15_16_h1_{h}'
            results[sym][key] = r
            bts = r.get('BEAR_TREND_SHORT', {})
            print(f'    h1={h}: WR={r["_overall"]["wr"]:.1%} | BEAR_TREND_SHORT WR={bts.get("wr",0):.1%} n={bts.get("n",0)} ({time.time()-t1:.0f}s)')

    # ── 汇总输出 ──────────────────────────────────────────────
    out_path = RESULTS / f'hold_max_sensitivity_{TAG}.json'
    out_path.write_text(json.dumps({
        'generated': datetime.now(timezone.utc).isoformat(),
        'elapsed_min': round((time.time()-t0)/60, 2),
        'target_regimes': list(TARGET_REGIMES),
        'results': results,
    }, indent=2, ensure_ascii=False, default=str))

    print(f'\n{"="*60}')
    print(f'✅ 结果写入: {out_path.name}')
    print(f'总耗时: {(time.time()-t0)/60:.1f}分钟')
    print(f'{"="*60}')

    # ── 控制台摘要表 ──────────────────────────────────────────
    print(f'\n📊 BEAR_TREND_SHORT WR × hold_max 对比矩阵:')
    print(f'  {"配置":<18} {"BTC WR":>8} {"BTC n":>6} {"ETH WR":>8} {"ETH n":>6} {"整体WR_BTC":>10}')
    print('  ' + '-'*62)
    all_keys = sorted(set(k for sym_r in results.values() for k in sym_r.keys()))
    for key in all_keys:
        btc = results.get('BTCUSDT', {}).get(key, {})
        eth = results.get('ETHUSDT', {}).get(key, {})
        bts_b = btc.get('BEAR_TREND_SHORT', {})
        bts_e = eth.get('BEAR_TREND_SHORT', {})
        ov_b  = btc.get('_overall', {})
        marker = ' ← 当前' if key == 'm15_16_h1_24' else ''
        print(f'  {key:<18} {bts_b.get("wr",0):>8.1%} {bts_b.get("n",0):>6} '
              f'{bts_e.get("wr",0):>8.1%} {bts_e.get("n",0):>6} '
              f'{ov_b.get("wr",0):>10.1%}{marker}')

if __name__ == '__main__':
    main()
