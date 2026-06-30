#!/usr/bin/env python3
"""
Combinatorial Purged Cross-Validation (CPCV)
梵天验证层 · 基于 López de Prado《Advances in Financial Machine Learning》

用法:
  python3 arch/validation/combinatorial_purged_cv.py          # 武曲Paper验证
  python3 arch/validation/combinatorial_purged_cv.py --dharma # 达摩院OOS验证
  python3 arch/validation/combinatorial_purged_cv.py --full   # 完整报告
"""
import numpy as np
import pandas as pd
from itertools import combinations
from typing import Generator, Tuple, Optional
import warnings, json, argparse
from pathlib import Path
from datetime import datetime, timezone
warnings.filterwarnings('ignore')

ROOT = Path(__file__).parent.parent.parent


# ─────────────────────────────────────────────────────────────────
# CPCV 核心引擎
# ─────────────────────────────────────────────────────────────────
class CombinatorialPurgedCV:
    """
    Combinatorial Purged Cross-Validation
    专为梵天/星枢引擎时间序列数据设计
    """

    def __init__(self,
                 n_splits:      int   = 6,
                 n_test_folds:  int   = 2,
                 purge_days:    float = 2.0,
                 embargo_days:  float = 0.5,
                 verbose:       bool  = True):
        if n_test_folds >= n_splits:
            raise ValueError("n_test_folds 必须 < n_splits")
        self.n_splits     = n_splits
        self.n_test_folds = n_test_folds
        self.purge_days   = purge_days
        self.embargo_days = embargo_days
        self.verbose      = verbose

    def split(self,
              X:     pd.DataFrame,
              y:     Optional[pd.Series] = None,
              times: Optional[pd.Series] = None
              ) -> Generator[Tuple[np.ndarray, np.ndarray], None, None]:
        """
        生成 (train_idx, test_idx) 对
        X:     特征 DataFrame（index 为时间或整数）
        times: 若 X.index 非 datetime，则显式提供时间列
        """
        n = len(X)
        if times is None:
            times = X.index
        if not isinstance(times, pd.DatetimeIndex):
            times = pd.to_datetime(times)

        # 时间排序
        order   = np.argsort(times)
        times   = times[order]
        indices = np.arange(n)[order]

        # 标签结束时间（Triple-Barrier: 当前时间 + purge 窗口）
        purge_delta   = pd.Timedelta(days=self.purge_days)
        embargo_delta = pd.Timedelta(days=self.embargo_days)

        test_combos = list(combinations(range(self.n_splits), self.n_test_folds))
        if self.verbose:
            print(f"[CPCV] {len(test_combos)} 条独立路径 | "
                  f"K={self.n_splits} n_test={self.n_test_folds} "
                  f"purge={self.purge_days}d embargo={self.embargo_days}d")

        fold_size = n // self.n_splits

        for combo in test_combos:
            test_mask  = np.zeros(n, dtype=bool)
            train_mask = np.ones(n, dtype=bool)

            # ── 测试集 ──────────────────────────────────────────
            for fold in combo:
                s = fold * fold_size
                e = (fold + 1) * fold_size if fold < self.n_splits - 1 else n
                test_mask[s:e] = True

            # ── Purging：移除与测试集标签时间重叠的训练样本 ──────
            test_indices = np.where(test_mask)[0]
            for i in test_indices:
                # 测试样本的标签结束时间
                label_end = times[i] + purge_delta
                # 训练集中时间落在 (times[i], label_end) 内的样本 → 移除
                overlap = (times > times[i]) & (times < label_end) & (~test_mask)
                train_mask[overlap] = False

            # ── Embargo：测试集结束后缓冲期 ─────────────────────
            if self.embargo_days > 0 and test_mask.any():
                test_end_time  = times[test_mask].max()
                embargo_end    = test_end_time + embargo_delta
                embargo_region = (times > test_end_time) & (times <= embargo_end)
                train_mask[embargo_region] = False

            # 测试集不能进训练集
            train_mask[test_mask] = False

            train_idx = indices[train_mask]
            test_idx  = indices[test_mask]

            if len(train_idx) > 0 and len(test_idx) > 0:
                yield train_idx, test_idx


# ─────────────────────────────────────────────────────────────────
# 梵天数据加载
# ─────────────────────────────────────────────────────────────────
def load_wuqu_as_df() -> pd.DataFrame:
    records = []
    with open(ROOT / 'data/wuqu_paper_settled.jsonl') as f:
        for line in f:
            try: records.append(json.loads(line))
            except: pass

    rows = []
    for r in records:
        outcome = r.get('outcome', '')
        if outcome in ('TP1', 'TP2'):
            win = 1
        elif outcome == 'SL':
            win = 0
        else:
            continue  # TIMEOUT 跳过

        pnl = float(r.get('pnl_pct', 0))
        # 武曲Paper pnl_pct 单位是基点，需除以10000
        if abs(pnl) > 10:
            pnl = pnl / 10000

        rows.append({
            'ts':       pd.to_datetime(r.get('open_ts', '2026-01-01')),
            'symbol':   r.get('symbol', ''),
            'score':    float(r.get('score', 0)),
            'grade':    float(str(r.get('grade', 0)).split('(')[0].strip()) if str(r.get('grade','0'))[0:1].lstrip('-').isdigit() else 0,
            'gap_pct':  float(r.get('gap_pct', 0)),
            'regime':   r.get('regime', ''),
            'win':      win,
            'pnl':      pnl,
        })

    df = pd.DataFrame(rows).sort_values('ts').reset_index(drop=True)
    df.index = df['ts']
    return df


def load_dharma_oos_as_df(max_rows=2000) -> pd.DataFrame:
    import os
    rows = []
    dharma_dir = ROOT / 'data/dharma_backtest'
    for fn in os.listdir(dharma_dir):
        if 'oos_trades' not in fn or not fn.endswith('.jsonl'):
            continue
        with open(dharma_dir / fn) as f:
            for line in f:
                try:
                    t = json.loads(line)
                    result = t.get('result', '')
                    win = 1 if result in ('TP', 'WIN', 'TP1', 'TP2') else (0 if result == 'SL' else None)
                    if win is None:
                        continue
                    ts = pd.to_datetime(t.get('ms', 0), unit='ms') if t.get('ms') else pd.Timestamp('2026-01-01')
                    rows.append({
                        'ts':    ts,
                        'sym':   t.get('sym', ''),
                        'win':   win,
                        'pnl':   float(t.get('pnl_pct', 0)) / 100,
                        'side':  t.get('side', ''),
                    })
                except: pass
        if len(rows) >= max_rows:
            break

    df = pd.DataFrame(rows).sort_values('ts').reset_index(drop=True)
    df.index = df['ts']
    return df


# ─────────────────────────────────────────────────────────────────
# 评估函数
# ─────────────────────────────────────────────────────────────────
def evaluate_path(df: pd.DataFrame, train_idx, test_idx) -> dict:
    train = df.iloc[train_idx]
    test  = df.iloc[test_idx]

    def stats(sub):
        n    = len(sub)
        wins = sub['win'].sum()
        wr   = wins / n if n > 0 else 0
        pnls = sub['pnl'].values
        gain = pnls[pnls > 0].sum()
        loss = abs(pnls[pnls < 0].sum())
        pf   = gain / loss if loss > 0 else (999 if gain > 0 else 0)
        mean = pnls.mean()
        std  = pnls.std() if len(pnls) > 1 else 1e-9
        sr   = mean / std * np.sqrt(252) if std > 0 else 0
        return {'n': n, 'wr': round(wr, 4), 'pf': round(pf, 3), 'sharpe': round(sr, 3)}

    tr = stats(train)
    te = stats(test)
    return {
        'train': tr,
        'test':  te,
        'wr_deg': round(tr['wr'] - te['wr'], 4),
        'pf_deg': round(tr['pf'] - te['pf'], 3),
    }


# ─────────────────────────────────────────────────────────────────
# 主程序
# ─────────────────────────────────────────────────────────────────
def run_cpcv(df: pd.DataFrame, label: str,
             n_splits=6, n_test_folds=2,
             purge_days=2.0, embargo_days=0.5,
             max_paths=30):
    if len(df) < 20:
        print(f'[CPCV] {label}: 样本不足 ({len(df)}条)')
        return

    cv = CombinatorialPurgedCV(
        n_splits=n_splits,
        n_test_folds=n_test_folds,
        purge_days=purge_days,
        embargo_days=embargo_days,
        verbose=True,
    )

    path_results = []
    for i, (tr_idx, te_idx) in enumerate(cv.split(df)):
        if i >= max_paths:
            break
        res = evaluate_path(df, tr_idx, te_idx)
        path_results.append(res)

    if not path_results:
        print(f'[CPCV] {label}: 无有效路径')
        return

    # 汇总
    oos_wrs   = [r['test']['wr']   for r in path_results]
    oos_pfs   = [r['test']['pf']   for r in path_results]
    oos_srs   = [r['test']['sharpe'] for r in path_results]
    wr_degs   = [r['wr_deg']       for r in path_results]

    avg_oos_wr  = np.mean(oos_wrs)
    std_oos_wr  = np.std(oos_wrs)
    avg_oos_pf  = np.mean(oos_pfs)
    avg_oos_sr  = np.mean(oos_srs)
    avg_wr_deg  = np.mean(wr_degs)

    # Deflated Sharpe Ratio（简化版）
    n_paths = len(path_results)
    dsr_penalty = np.sqrt(np.log(n_paths))
    dsr = avg_oos_sr / dsr_penalty if dsr_penalty > 0 else avg_oos_sr

    # 过拟合判断
    overfit_paths = sum(1 for d in wr_degs if d > 0.08)
    overfit_rate  = overfit_paths / n_paths

    print(f'\n{"="*58}')
    print(f'  CPCV 报告 · {label}')
    print(f'  {len(path_results)}条独立回测路径 | N样本={len(df)}')
    print(f'{"="*58}')
    print(f'\n  OOS WR:    {avg_oos_wr:.1%} ± {std_oos_wr:.1%}')
    print(f'  OOS PF:    {avg_oos_pf:.3f}')
    print(f'  OOS Sharpe:{avg_oos_sr:.3f}')
    print(f'  DSR:       {dsr:.3f}  (Deflated Sharpe, 多路径惩罚后)')
    print(f'\n  WR退化:    {avg_wr_deg:+.1%}  (训练→OOS)')
    print(f'  过拟合路径:{overfit_paths}/{n_paths} ({overfit_rate:.0%})')

    # WR分布
    wr_p5  = np.percentile(oos_wrs, 5)
    wr_p50 = np.percentile(oos_wrs, 50)
    wr_p95 = np.percentile(oos_wrs, 95)
    print(f'\n  OOS WR分布: P5={wr_p5:.1%} | P50={wr_p50:.1%} | P95={wr_p95:.1%}')

    # 健康判断
    healthy = (
        avg_oos_wr > 0.55 and
        avg_oos_pf > 1.5 and
        overfit_rate < 0.3 and
        dsr > 0.5
    )
    print(f'\n  系统判定: {"✅ 通过 CPCV 验证" if healthy else "⚠️ 需要审查"}')
    if overfit_rate > 0.3:
        print(f'  ⚠️  过拟合路径比例 {overfit_rate:.0%} > 30%，建议减少特征或增加样本')
    if dsr < 0.5:
        print(f'  ⚠️  Deflated Sharpe={dsr:.3f} 偏低，多路径测试后信号可信度下降')

    return {
        'label': label,
        'n_paths': n_paths,
        'oos_wr':  round(avg_oos_wr, 4),
        'oos_wr_std': round(std_oos_wr, 4),
        'oos_pf':  round(avg_oos_pf, 3),
        'dsr':     round(dsr, 3),
        'overfit_rate': round(overfit_rate, 3),
        'healthy': healthy,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dharma', action='store_true')
    parser.add_argument('--full',   action='store_true')
    args = parser.parse_args()

    print(f'\n🏯 梵天 CPCV 验证引擎')
    print(f'   {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}')
    print(f'   López de Prado《AFML》方法论')

    results = []

    # ── 武曲Paper ──────────────────────────────────────────
    wuqu = load_wuqu_as_df()
    print(f'\n武曲Paper: {len(wuqu)}条有效交易 (已排除TIMEOUT)')
    if len(wuqu) >= 15:
        r = run_cpcv(wuqu, '武曲Paper',
                     n_splits=6, n_test_folds=2,
                     purge_days=1.0, embargo_days=0.5,
                     max_paths=15)
        if r: results.append(r)

    # ── 达摩院OOS ──────────────────────────────────────────
    if args.dharma or args.full:
        dharma = load_dharma_oos_as_df(max_rows=1500)
        print(f'\n达摩院OOS: {len(dharma)}条')
        if len(dharma) >= 50:
            r = run_cpcv(dharma, '达摩院OOS',
                         n_splits=8, n_test_folds=2,
                         purge_days=2.0, embargo_days=1.0,
                         max_paths=28)
            if r: results.append(r)

    # ── 保存报告 ───────────────────────────────────────────
    out = ROOT / 'data/cpcv_report.json'
    out.write_text(json.dumps({
        'ts': datetime.now(timezone.utc).isoformat(),
        'results': [{k: bool(v) if isinstance(v, (bool, __builtins__.__class__)) else v
                     for k, v in r.items()} for r in results],
    }, ensure_ascii=False, indent=2, default=str))
    print(f'\n✅ CPCV报告已保存: {out}')


if __name__ == '__main__':
    main()
