#!/usr/bin/env python3
"""
达摩院测试系统 · 100轮严格交叉验证
=====================================
目标：用100轮随机分割验证梵天评分系统的真实稳健性

设计：
  - 数据切割：IS (训练期 2018-2024-12-31) / OOS (验证期 2025-01-01+)
  - 严格防穿越：cutoff=2024-12-31，2025+仅作OOS验证
  - 100轮 Bootstrap 重采样（OOS段）
  - 品种：BTC / ETH / SOL / DOGE / LTC / BNB
  - 方向分析：多空分别统计
  - 输出：WR/PF/CI95/稳健性评级

用法: python3 dharma/dharma_100_test.py [--fast] [--sym BTCUSDT]
"""
import sys, json, time, random, math, argparse, os
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict

import numpy as np
import pandas as pd

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))
from dharma.dharma_system_backtest import add_indicators, score_signal

RESULTS_DIR = Path(__file__).parent / 'results'
RESULTS_DIR.mkdir(exist_ok=True)

# ══════════════════════════════════════════════════════════════════
# 设计院最高原则：IS/OOS 严格切割，禁止穿越
# ══════════════════════════════════════════════════════════════════
OOS_CUTOFF  = pd.Timestamp('2025-01-01', tz='UTC')   # OOS起点
IS_END      = pd.Timestamp('2024-12-31', tz='UTC')   # IS终点（含）
N_ROUNDS    = 100                                      # 测试轮数
SEED        = 42
RNG         = random.Random(SEED)
NP_RNG      = np.random.default_rng(SEED)

# 测试品种（核心6个）
TEST_SYMS   = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'DOGEUSDT', 'LTCUSDT', 'BNBUSDT']

# 信号评分门槛
SCORE_THR   = 140
SL_ATR_MULT = 1.5
TP_RR       = 2.5
MAX_HOLD_H  = 20   # 最大持仓20小时（动态持仓窗口中位值）


def load_sym(sym: str) -> pd.DataFrame:
    """加载品种历史数据"""
    data_dir = Path(__file__).parent / 'data'
    candidates = sorted(data_dir.glob(f'{sym.lower()}_1h_*.parquet'))
    if not candidates:
        return None
    df = pd.read_parquet(candidates[0])
    if df.index.tz is None:
        df.index = df.index.tz_localize('UTC')
    elif str(df.index.tz) != 'UTC':
        df.index = df.index.tz_convert('UTC')
    df = add_indicators(df)
    return df


def generate_signals(df: pd.DataFrame, direction: str, score_thr: int = SCORE_THR) -> pd.DataFrame:
    """
    扫描历史数据，生成满足评分门槛的信号列表
    防穿越：仅使用信号时刻及之前的数据
    """
    records = []
    for i in range(200, len(df)):
        row = df.iloc[i]
        try:
            sc = score_signal(row, direction)
            total = sc.get('total', 0) or 0
            if total >= score_thr:
                records.append({
                    'ts':        df.index[i],
                    'price':     float(row['close']),
                    'atr':       float(row.get('atr', 0) or 0),
                    'score':     total,
                    'direction': direction,
                    'idx':       i,
                })
        except Exception:
            pass
    if not records:
        return pd.DataFrame()
    return pd.DataFrame(records).set_index('ts')


def simulate_trades(df: pd.DataFrame, signals: pd.DataFrame,
                    sl_mult: float = SL_ATR_MULT,
                    tp_rr: float = TP_RR,
                    max_hold: int = MAX_HOLD_H) -> list:
    """
    对每条信号模拟出入场
    - entry: 信号K线下一根开盘（防看未来）
    - SL = entry ± ATR × sl_mult
    - TP = entry ∓ (SL距离 × tp_rr)
    - TIMEOUT: 超过max_hold小时无结果
    """
    trades = []
    df_arr  = df['close'].values
    df_high = df['high'].values
    df_low  = df['low'].values

    for _, sig in signals.iterrows():
        i   = int(sig['idx'])
        if i + 1 >= len(df):
            continue
        atr = float(sig['atr'])
        if atr <= 0:
            continue

        entry  = float(df_arr[i + 1])   # 下一根开盘价（近似）
        sl_dist = atr * sl_mult
        tp_dist = sl_dist * tp_rr

        if sig['direction'] == 'SHORT':
            sl = entry + sl_dist
            tp = entry - tp_dist
        else:
            sl = entry - sl_dist
            tp = entry + tp_dist

        outcome = 'TIMEOUT'
        pnl_pct  = 0.0

        for j in range(i + 1, min(i + 1 + max_hold, len(df))):
            hi = df_high[j]
            lo = df_low[j]
            if sig['direction'] == 'SHORT':
                if lo <= tp:
                    outcome = 'TP1'
                    pnl_pct = tp_rr * (atr / entry) * 100
                    break
                if hi >= sl:
                    outcome = 'SL'
                    pnl_pct = -(atr / entry) * 100
                    break
            else:
                if hi >= tp:
                    outcome = 'TP1'
                    pnl_pct = tp_rr * (atr / entry) * 100
                    break
                if lo <= sl:
                    outcome = 'SL'
                    pnl_pct = -(atr / entry) * 100
                    break

        trades.append({
            'ts':        sig.name,
            'score':     sig['score'],
            'direction': sig['direction'],
            'entry':     entry,
            'sl':        sl,
            'tp':        tp,
            'outcome':   outcome,
            'pnl_pct':   round(pnl_pct, 4),
        })

    return trades


def calc_pf(trades: list) -> float:
    wins   = sum(t['pnl_pct'] for t in trades if t['pnl_pct'] > 0)
    losses = abs(sum(t['pnl_pct'] for t in trades if t['pnl_pct'] < 0))
    return round(wins / losses, 4) if losses > 0 else 0.0


def bootstrap_100(trades: list, n_rounds: int = N_ROUNDS) -> dict:
    """100轮Bootstrap重采样，计算WR/PF的分布和CI95"""
    settled = [t for t in trades if t['outcome'] in ('TP1', 'SL')]
    n = len(settled)
    if n < 5:
        return {'n': n, 'error': f'样本不足({n}条，需>=5)'}

    rng = random.Random(SEED)
    wrs = []
    pfs = []

    for _ in range(n_rounds):
        sample = [settled[rng.randint(0, n-1)] for _ in range(n)]
        wins   = sum(1 for t in sample if t['outcome'] == 'TP1')
        wr     = wins / n
        pf     = calc_pf(sample)
        wrs.append(wr)
        pfs.append(pf)

    wrs.sort(); pfs.sort()

    def pct(arr, p):
        idx = int(len(arr) * p / 100)
        return round(arr[min(idx, len(arr)-1)], 4)

    ci95_wr = (pct(wrs, 2.5), pct(wrs, 97.5))
    ci95_pf = (pct(pfs, 2.5), pct(pfs, 97.5))

    # 稳健性评级
    pf_lo = ci95_pf[0]
    if pf_lo >= 1.5:
        grade = 'GOLD 🥇'
    elif pf_lo >= 1.3:
        grade = 'SILVER 🥈'
    elif pf_lo >= 1.0:
        grade = 'BRONZE 🥉'
    else:
        grade = 'FAIL ❌'

    return {
        'n':        n,
        'wr_mean':  round(sum(wrs)/len(wrs), 4),
        'pf_mean':  round(sum(pfs)/len(pfs), 4),
        'ci95_wr':  ci95_wr,
        'ci95_pf':  ci95_pf,
        'pf_ci_lo': pf_lo,
        'grade':    grade,
        'rounds':   n_rounds,
    }


def run_sym(sym: str, fast: bool = False) -> dict:
    """对单个品种运行100轮测试"""
    t0 = time.time()
    print(f"\n  ▶ {sym}...", end=' ', flush=True)

    df = load_sym(sym)
    if df is None or len(df) < 500:
        print("❌ 数据不足")
        return {'sym': sym, 'error': '数据不足'}

    # 切割 IS / OOS
    is_df  = df[df.index <= IS_END]
    oos_df = df[df.index >= OOS_CUTOFF]

    print(f"IS={len(is_df)}根 OOS={len(oos_df)}根", end=' ', flush=True)

    if len(oos_df) < 200:
        print("⚠️ OOS太少")

    result = {'sym': sym, 'directions': {}}

    for direction in ['SHORT', 'LONG']:
        # IS段：生成信号参数（不用于评估，仅验证信号逻辑）
        # OOS段：评估信号质量（防穿越核心）
        thr = SCORE_THR if not fast else SCORE_THR + 10

        # 在 OOS 段生成并评估
        if len(oos_df) >= 200:
            oos_sigs   = generate_signals(oos_df, direction, thr)
            oos_trades = simulate_trades(oos_df, oos_sigs) if not oos_sigs.empty else []
        else:
            oos_sigs   = pd.DataFrame()
            oos_trades = []

        oos_settled = [t for t in oos_trades if t['outcome'] in ('TP1', 'SL')]
        oos_bs      = bootstrap_100(oos_trades)

        # IS段信号数量（参考，不用于评估）
        is_sigs = generate_signals(is_df, direction, thr) if not fast else pd.DataFrame()

        result['directions'][direction] = {
            'is_signals':  len(is_sigs),
            'oos_signals': len(oos_sigs) if not oos_sigs.empty else 0,
            'oos_trades':  len(oos_trades),
            'oos_settled': len(oos_settled),
            'oos_wr':      round(sum(1 for t in oos_settled if t['outcome']=='TP1')/max(len(oos_settled),1),3),
            'oos_pf':      round(calc_pf(oos_settled), 3),
            'bootstrap':   oos_bs,
        }

    elapsed = round(time.time() - t0, 1)
    print(f"耗时={elapsed}s ✅")
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--fast', action='store_true', help='快速模式（减少计算）')
    ap.add_argument('--sym',  type=str, default=None, help='单品种测试')
    args = ap.parse_args()

    syms = [args.sym.upper()] if args.sym else TEST_SYMS
    ts   = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')

    print("=" * 65)
    print(f"  梵天设计院 · 达摩院100轮严格OOS验证")
    print(f"  IS截止={IS_END.date()} | OOS起点={OOS_CUTOFF.date()}")
    print(f"  测试品种={len(syms)}个 | Bootstrap轮数={N_ROUNDS}")
    print(f"  评分门槛={SCORE_THR} | SL={SL_ATR_MULT}xATR | TP={TP_RR}xRR")
    print("=" * 65)

    all_results = {}
    t_start = time.time()

    for sym in syms:
        r = run_sym(sym, fast=args.fast)
        all_results[sym] = r

    # ── 汇总报告 ──────────────────────────────────────────────────
    print("\n" + "=" * 65)
    print(f"  达摩院100轮测试 · 汇总报告  {ts}")
    print("=" * 65)

    print(f"\n{'品种':<12} {'方向':<6} {'OOS信号':>8} {'OOS-WR':>8} {'OOS-PF':>8} {'CI95-PF下限':>12} {'评级':<12}")
    print("-" * 70)

    grand_settled = []

    for sym, r in all_results.items():
        if 'error' in r:
            print(f"{sym:<12} {'--':<6} {'--':>8} {'ERROR':>8} {'':>8} {'':>12} {r['error']:<12}")
            continue
        for direction, d in r['directions'].items():
            bs = d.get('bootstrap', {})
            wr_str  = f"{d['oos_wr']*100:.1f}%"
            pf_str  = f"{d['oos_pf']:.3f}"
            ci_lo   = bs.get('pf_ci_lo', 0)
            ci_str  = f"{ci_lo:.3f}"
            grade   = bs.get('grade', '--')
            n_sig   = d['oos_signals']
            marker  = '✅' if ci_lo >= 1.3 else ('⚠️' if ci_lo >= 1.0 else '❌')
            print(f"{sym:<12} {direction:<6} {n_sig:>8} {wr_str:>8} {pf_str:>8} {ci_str:>12} {grade:<12} {marker}")

    # 全局统计
    all_trades_flat = []
    for sym, r in all_results.items():
        if 'error' in r: continue
        for direction, d in r['directions'].items():
            # 这里用 oos_pf 和 oos_wr 无法还原 trades，但可以统计通过品种数
            bs = d.get('bootstrap', {})
            if bs.get('pf_ci_lo', 0) >= 1.3:
                all_trades_flat.append({'sym': sym, 'dir': direction, 'grade': bs.get('grade')})

    print()
    print(f"  通过认证（CI95-PF下限≥1.3）: {len(all_trades_flat)} 个品种×方向组合")
    for t in all_trades_flat:
        print(f"    ✅ {t['sym']} {t['dir']} — {t['grade']}")

    elapsed = round(time.time() - t_start, 1)
    print(f"\n  总耗时: {elapsed}s | Bootstrap轮数: {N_ROUNDS}")

    # 保存结果
    out_file = RESULTS_DIR / f'dharma_100_test_{ts}.json'
    with open(out_file, 'w', encoding='utf-8') as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2, default=str)
    print(f"  结果已保存: {out_file.name}")
    print("=" * 65)


if __name__ == '__main__':
    main()
