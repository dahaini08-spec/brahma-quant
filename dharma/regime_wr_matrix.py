#!/usr/bin/env python3
"""
dharma/regime_wr_matrix.py — 体制×方向 WR矩阵生成器 v1.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
设计院 × 达摩院 · 2026-06-17

职责：
  1. 从现有 offline_brahma_replay 结果提取/重算体制×方向 WR矩阵
  2. 生成 s23 在不同体制下的预测精度矩阵
  3. 输出用于达摩院 Ablation 的"体制分层"验证报告

这是达摩院的核心分析工具：
  体制×方向 才是唯一真正的 alpha 来源（设计院封印）
  s23 的价值必须在每个体制内独立测量，不能混合计算

用法：
  python3 dharma/regime_wr_matrix.py              # 从replay结果提取
  python3 dharma/regime_wr_matrix.py --fresh      # 重新扫描parquet
"""

import sys, json, argparse
from pathlib import Path
from datetime import datetime, timezone
import numpy as np
import pandas as pd

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'brahma_brain'))

RESULTS = BASE / 'dharma' / 'results'
TAG = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')

# ── 体制分组（设计院体制字典对齐）────────────────────────────────
REGIME_COLORS = {
    'BULL_TREND':      ('S级',  '🟢', '+'),
    'BULL_EARLY':      ('A级',  '🟢', '+'),
    'BULL_CORRECTION': ('S+级', '🟡', '?'),  # 待解锁
    'BEAR_TREND':      ('S级',  '🔴', '-'),
    'BEAR_EARLY':      ('A级',  '🔴', '-'),
    'BEAR_RECOVERY':   ('S+级', '🟡', '?'),  # 待解锁
    'CHOP_MID':        ('❌',   '⬛', '0'),
    'CHOP_HIGH':       ('❌',   '⬛', '0'),
    'CHOP_LOW':        ('❌',   '⬛', '0'),
}


def load_from_replay_report(report_path: Path) -> dict:
    """从最新replay报告提取体制WR矩阵"""
    with open(report_path) as f:
        data = json.load(f)

    matrix = {}
    reports = data.get('reports', {})

    for sym, rdata in reports.items():
        by_regime = rdata.get('by_regime', {})
        for regime_key, stats in by_regime.items():
            if '_' not in regime_key:
                continue
            # 尝试解析 REGIME_DIRECTION 格式
            parts = regime_key.rsplit('_', 1)
            if len(parts) == 2 and parts[1] in ('LONG', 'SHORT'):
                regime, direction = parts[0], parts[1]
            else:
                continue

            key = f'{sym}_{regime}_{direction}'
            matrix[key] = {
                'symbol': sym,
                'regime': regime,
                'direction': direction,
                'n': stats.get('n', 0),
                'wr': stats.get('wr', 0),
                'avg_pnl': stats.get('avg_pnl', 0),
                'source': 'replay',
            }

    return matrix


def compute_from_parquet(sym: str = 'BTCUSDT', quick: bool = False) -> dict:
    """从parquet数据直接计算体制×方向 WR矩阵（简化版）"""
    print(f'  计算 {sym} 体制×方向矩阵...')

    fixed = BASE / 'data' / 'backtest' / 'fixed'
    sym_l = sym.lower()

    df1h = pd.read_parquet(fixed / f'{sym_l}_1h_fixed.parquet')
    df15 = pd.read_parquet(fixed / f'{sym_l}_15m_fixed.parquet')

    if quick:
        df1h = df1h.tail(len(df1h) // 3)
        df15 = df15.tail(len(df15) // 3)

    # 获取列名
    def get_col(df, name):
        for c in df.columns:
            if name.lower() in c.lower():
                return c
        return df.columns[{'open':0,'high':1,'low':2,'close':3,'vol':4}.get(name,0)]

    close_col_1h = get_col(df1h, 'close')
    closes_1h = df1h[close_col_1h].values

    # 简化体制识别（离线版）
    def get_regime_simple(closes, i, window=50):
        if i < window:
            return 'CHOP_MID'
        recent = closes[i-window:i]
        ema_fast = recent[-10:].mean()
        ema_slow = recent.mean()
        rsi_raw = closes[i-14:i]
        if len(rsi_raw) < 14:
            return 'CHOP_MID'
        delta = np.diff(rsi_raw)
        gains = delta[delta > 0].mean() if any(delta > 0) else 0
        losses = abs(delta[delta < 0].mean()) if any(delta < 0) else 1e-10
        rsi = 100 - (100 / (1 + gains / losses))

        slope = (ema_fast - ema_slow) / ema_slow
        if slope > 0.02 and rsi > 55:
            return 'BULL_TREND' if slope > 0.04 else 'BULL_EARLY'
        elif slope < -0.02 and rsi < 45:
            return 'BEAR_TREND' if slope < -0.04 else 'BEAR_EARLY'
        elif slope > 0.01 and rsi < 45:
            return 'BULL_CORRECTION'
        elif slope < -0.01 and rsi > 55:
            return 'BEAR_RECOVERY'
        return 'CHOP_MID'

    matrix = {}
    stride = 4  # 每4根1H取一次（避免过度重叠）

    for i in range(100, len(closes_1h) - 16, stride):
        regime = get_regime_simple(closes_1h, i)
        entry = closes_1h[i]
        future = closes_1h[i+1:i+17]
        if len(future) < 8:
            continue

        max_ret_long  = (future.max() - entry) / entry
        max_ret_short = (entry - future.min()) / entry

        for direction in ('LONG', 'SHORT'):
            ret = max_ret_long if direction == 'LONG' else max_ret_short
            is_win = ret > 0.003  # >0.3% 算胜（含手续费）

            key = f'{regime}_{direction}'
            if key not in matrix:
                matrix[key] = {'wins': 0, 'total': 0, 'regime': regime, 'direction': direction}
            matrix[key]['total'] += 1
            if is_win:
                matrix[key]['wins'] += 1

    # 计算WR
    for k, v in matrix.items():
        n = v['total']
        v['n'] = n
        v['wr'] = v['wins'] / n if n > 0 else 0
        v['symbol'] = sym

    return matrix


def print_matrix(matrix: dict, min_n: int = 50):
    """打印体制×方向 WR矩阵"""
    print(f'\n{"体制":<25} {"方向":<8} {"n":>6} {"WR":>7} {"评级":<8} {"图示":<25}')
    print('─' * 80)

    # 按体制分组排序
    order = ['BULL_TREND', 'BULL_EARLY', 'BULL_CORRECTION',
             'BEAR_TREND', 'BEAR_EARLY', 'BEAR_RECOVERY',
             'CHOP_MID', 'CHOP_HIGH', 'CHOP_LOW']

    for regime in order:
        for direction in ('LONG', 'SHORT'):
            key = f'{regime}_{direction}'
            v = matrix.get(key, {})
            n = v.get('n', 0)
            wr = v.get('wr', 0)
            if n < min_n:
                continue

            wr_pct = wr * 100 if wr <= 1 else wr
            bar = '█' * min(int(wr_pct / 5), 20)
            empty = '░' * (20 - len(bar))

            grade, icon, _ = REGIME_COLORS.get(regime, ('?', '⬜', '?'))
            color = ''
            if wr_pct >= 68:    color = '\033[92m'  # 绿
            elif wr_pct >= 60:  color = '\033[93m'  # 黄
            elif wr_pct < 50:   color = '\033[91m'  # 红

            print(f'{icon} {regime:<22} {direction:<8} {n:>6} '
                  f'{color}{wr_pct:6.1f}%\033[0m  {grade:<6}  '
                  f'{color}{bar}{empty}\033[0m')

    print()


def analyze_s23_by_regime(matrix: dict, sym: str = 'BTCUSDT', quick: bool = False):
    """分析 Kronos-Lite s23 在每个体制下的预测精度"""
    print(f'\n=== s23 体制分层精度分析 ({sym}) ===\n')

    from brahma_brain.kronos_lite import get_s23_score, _CACHE

    fixed = BASE / 'data' / 'backtest' / 'fixed'
    sym_l = sym.lower()
    df15 = pd.read_parquet(fixed / f'{sym_l}_15m_fixed.parquet')
    if quick:
        df15 = df15.tail(40000)

    def get_col(df, name):
        for c in df.columns:
            if name.lower() in c.lower():
                return c
        return df.columns[{'open': 0, 'high': 1, 'low': 2, 'close': 3, 'vol': 4}.get(name, 0)]

    close_col = get_col(df15, 'close')
    open_col  = get_col(df15, 'open')
    high_col  = get_col(df15, 'high')
    low_col   = get_col(df15, 'low')
    vol_col   = get_col(df15, 'vol')

    closes = df15[close_col].values
    opens  = df15[open_col].values
    highs  = df15[high_col].values
    lows   = df15[low_col].values
    vols   = df15[vol_col].values if vol_col in df15.columns else np.ones(len(df15))

    # 简化体制识别
    def regime_at(i, window=200):
        if i < window:
            return 'CHOP_MID'
        c = closes[i-window:i]
        ema_f = c[-20:].mean()
        ema_s = c.mean()
        delta = np.diff(c[-14:])
        g = delta[delta > 0].mean() if any(delta > 0) else 0
        l = abs(delta[delta < 0].mean()) if any(delta < 0) else 1e-10
        rsi = 100 - (100 / (1 + g / l))
        slope = (ema_f - ema_s) / ema_s
        if slope > 0.015 and rsi > 55:
            return 'BULL_TREND' if slope > 0.03 else 'BULL_EARLY'
        elif slope < -0.015 and rsi < 45:
            return 'BEAR_TREND' if slope < -0.03 else 'BEAR_EARLY'
        elif slope > 0.008 and rsi < 45:
            return 'BULL_CORRECTION'
        elif slope < -0.008 and rsi > 55:
            return 'BEAR_RECOVERY'
        return 'CHOP_MID'

    regime_s23 = {}
    stride = 80

    print(f'  处理 {len(range(200, len(closes)-17, stride))} 个窗口...')
    for i in range(200, len(closes) - 17, stride):
        regime = regime_at(i)
        direction = 'LONG' if closes[i] > closes[i-20:i].mean() else 'SHORT'

        future = closes[i+1:i+17]
        entry = closes[i]
        ret = (future.max() - entry) / entry if direction == 'LONG' else (entry - future.min()) / entry
        is_win = ret > 0.003

        klines = [[opens[j], highs[j], lows[j], closes[j], vols[j]]
                  for j in range(i - 200, i)]

        _CACHE.clear()
        s23, meta = get_s23_score(sym, direction, klines, regime)

        rkey = f'{regime}_{direction}'
        if rkey not in regime_s23:
            regime_s23[rkey] = {
                'n_all': 0, 'wins_all': 0,
                'n_s23_pos': 0, 'wins_s23_pos': 0,
                'n_s23_neg': 0, 'wins_s23_neg': 0,
                'regime': regime, 'direction': direction,
            }

        r = regime_s23[rkey]
        r['n_all'] += 1
        if is_win:
            r['wins_all'] += 1

        if s23 > 0:
            r['n_s23_pos'] += 1
            if is_win:
                r['wins_s23_pos'] += 1
        elif s23 < 0:
            r['n_s23_neg'] += 1
            if is_win:
                r['wins_s23_neg'] += 1

    # 打印结果
    print(f'\n{"体制_方向":<30} {"全量WR":>8} {"s23>0 WR":>10} {"s23<0 WR":>10} {"边际":>8} {"n_pos":>7}')
    print('─' * 80)

    summary = []
    for regime in ['BULL_TREND', 'BULL_EARLY', 'BULL_CORRECTION',
                   'BEAR_TREND', 'BEAR_EARLY', 'BEAR_RECOVERY',
                   'CHOP_MID']:
        for direction in ('LONG', 'SHORT'):
            rkey = f'{regime}_{direction}'
            r = regime_s23.get(rkey, {})
            n = r.get('n_all', 0)
            if n < 10:
                continue

            wr_all  = r['wins_all'] / n * 100
            np_ = r['n_s23_pos']
            nn_ = r['n_s23_neg']
            wr_pos  = r['wins_s23_pos'] / np_ * 100 if np_ > 5 else float('nan')
            wr_neg  = r['wins_s23_neg'] / nn_ * 100 if nn_ > 5 else float('nan')
            marginal = wr_pos - wr_all if not np.isnan(wr_pos) else float('nan')

            col = '\033[92m' if marginal > 1 else ('\033[91m' if marginal < -1 else '\033[93m') \
                  if not np.isnan(marginal) else ''
            pos_str = f'{wr_pos:.1f}%' if not np.isnan(wr_pos) else '  N/A'
            neg_str = f'{wr_neg:.1f}%' if not np.isnan(wr_neg) else '  N/A'
            marg_str = f'{col}{marginal:+.1f}%\033[0m' if not np.isnan(marginal) else '   N/A'

            icon = REGIME_COLORS.get(regime, ('?', '⬜', '?'))[1]
            print(f'{icon} {rkey:<28} {wr_all:6.1f}%  {pos_str:>8}  {neg_str:>8}  '
                  f'{marg_str:>8}  {np_:>5}')

            if not np.isnan(marginal):
                summary.append({'regime': regime, 'direction': direction,
                                 'marginal': marginal, 'n_pos': np_})

    return regime_s23, summary


def main():
    parser = argparse.ArgumentParser(description='体制×方向 WR矩阵生成器')
    parser.add_argument('--fresh', action='store_true', help='从parquet重新计算（而非读replay）')
    parser.add_argument('--quick', action='store_true', help='快速模式（最近1年）')
    parser.add_argument('--s23', action='store_true', help='同时分析s23体制分层精度')
    parser.add_argument('--sym', default='BTCUSDT', help='标的符号')
    args = parser.parse_args()

    print('\n🏛️  达摩院 · 体制×方向 WR矩阵分析')
    print(f'   时间: {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}')
    print(f'   模式: {"parquet重算" if args.fresh else "replay报告提取"} | {"快速" if args.quick else "全量"}')
    print()

    # 加载/计算矩阵
    if args.fresh:
        matrix = compute_from_parquet(args.sym, quick=args.quick)
    else:
        # 优先读最新replay报告
        report_files = sorted(RESULTS.glob('replay_report_*.json'))
        if report_files:
            print(f'  使用replay报告: {report_files[-1].name}')
            matrix = load_from_replay_report(report_files[-1])
            # 转换格式
            matrix2 = {}
            for k, v in matrix.items():
                rk = f"{v['regime']}_{v['direction']}"
                if rk not in matrix2:
                    matrix2[rk] = v
                else:
                    # 合并多标的
                    matrix2[rk]['n'] += v['n']
            matrix = matrix2
        else:
            print('  无replay报告，切换到parquet重算模式')
            matrix = compute_from_parquet(args.sym, quick=args.quick)

    print_matrix(matrix, min_n=30)

    # s23体制分层分析
    if args.s23:
        regime_s23, summary = analyze_s23_by_regime(matrix, args.sym, quick=args.quick)

        # 找出s23最有效的体制
        print('\n  s23边际贡献排行（n_pos≥20）:')
        valid = [(s['marginal'], s['regime'], s['direction'], s['n_pos'])
                 for s in summary if s['n_pos'] >= 20]
        for m, r, d, n in sorted(valid, reverse=True)[:8]:
            icon = '✅' if m > 1 else ('⚠️' if m >= 0 else '❌')
            print(f'    {icon} {r}_{d}: s23边际={m:+.1f}%WR  n_pos={n}')

    # 保存报告
    out = RESULTS / f'regime_wr_matrix_{TAG}.json'
    with open(out, 'w') as f:
        json.dump({
            'timestamp': TAG,
            'matrix': {k: {kk: float(vv) if isinstance(vv, (np.floating, float)) else vv
                            for kk, vv in v.items()} for k, v in matrix.items()},
        }, f, indent=2, ensure_ascii=False)
    print(f'\n  报告保存: {out}')


if __name__ == '__main__':
    main()
