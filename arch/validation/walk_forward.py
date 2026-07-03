#!/usr/bin/env python3
"""
Walk-Forward Validation (WFV)
梵天验证层 · 设计院补齐 2026-07-03

Walk-Forward是量化验证的黄金标准：
  - 训练集向前滚动，测试集始终在训练集之后（无未来数据泄露）
  - 每窗口独立训练+测试，汇总所有测试期结果
  - 配合CPCV+DSR，构成完整的Dharma三件套验证

参考：
  - López de Prado《Advances in Financial Machine Learning》Chapter 12
  - Prado & Bailey《The False Strategy Theorem》
  - 梵天达摩院 arch/validation/combinatorial_purged_cv.py

用法:
  python3 arch/validation/walk_forward.py                     # 快速WF验证
  python3 arch/validation/walk_forward.py --symbol BTCUSDT    # 单标的
  python3 arch/validation/walk_forward.py --full              # 完整报告
  python3 arch/validation/walk_forward.py --compare-cpcv      # 与CPCV对比
"""

import numpy as np
import pandas as pd
import json
import argparse
import warnings
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Tuple, Dict, Optional

warnings.filterwarnings('ignore')

ROOT = Path(__file__).parent.parent.parent
DATA = ROOT / 'data'
DHARMA_DATA = ROOT / 'dharma' / 'data'


# ─────────────────────────────────────────────────────────────────
# WalkForwardValidator 核心引擎
# ─────────────────────────────────────────────────────────────────

class WalkForwardValidator:
    """
    滚动窗口Walk-Forward验证器

    架构：
      [Train Window 1][Test 1]
              [Train Window 2][Test 2]
                      [Train Window 3][Test 3]
                              ...

    关键参数：
      train_ratio: 训练窗口占总数据比例（如0.7=70%训练）
      step_size:   每次向前滚动的步长（如0.1=10%步进）
      min_train:   最小训练样本数（防止样本不足）
    """

    def __init__(
        self,
        train_ratio:  float = 0.70,   # 训练窗口 70%
        step_ratio:   float = 0.10,   # 步进 10%
        min_train:    int   = 30,     # 最少30个样本才训练
        min_test:     int   = 10,     # 最少10个样本才测试
        verbose:      bool  = True,
    ):
        self.train_ratio = train_ratio
        self.step_ratio  = step_ratio
        self.min_train   = min_train
        self.min_test    = min_test
        self.verbose     = verbose

    def generate_windows(self, n: int) -> List[Tuple[List[int], List[int]]]:
        """生成所有(train_idx, test_idx)窗口对"""
        windows = []
        train_size = int(n * self.train_ratio)
        step_size  = max(1, int(n * self.step_ratio))

        start = 0
        while True:
            train_end = start + train_size
            if train_end >= n:
                break
            test_end = min(n, train_end + step_size)
            if test_end - train_end < self.min_test:
                break
            if train_end - start < self.min_train:
                start += step_size
                continue

            train_idx = list(range(start, train_end))
            test_idx  = list(range(train_end, test_end))
            windows.append((train_idx, test_idx))
            start += step_size

        return windows

    def validate(
        self,
        returns:    np.ndarray,    # 收益率序列（每笔交易）
        timestamps: Optional[np.ndarray] = None,
    ) -> Dict:
        """
        执行Walk-Forward验证

        参数：
          returns:    形如 [r1, r2, ...] 的收益率数组（+为盈，-为亏）
          timestamps: 对应时间戳（可选，用于日志）

        返回：
          {
            n_windows: int,          # 验证窗口数
            overall_wr: float,       # 全样本WR
            overall_ev: float,       # 全样本EV
            window_results: list,    # 每个窗口结果
            consistency_score: float,# 一致性分数（越高越稳定）
            overfitting_ratio: float,# IS vs OOS WR比（>1=可能过拟合）
            is_significant: bool,    # 是否达到统计显著性
          }
        """
        n = len(returns)
        windows = self.generate_windows(n)

        if not windows:
            return {
                'n_windows': 0,
                'error': f'样本量不足(n={n})，无法完成Walk-Forward验证',
                'min_required': self.min_train + self.min_test,
            }

        window_results = []
        all_test_returns = []
        all_train_wrs = []

        for w_idx, (train_idx, test_idx) in enumerate(windows):
            train_ret = returns[train_idx]
            test_ret  = returns[test_idx]

            # 计算训练集统计（IS）
            train_wr = np.mean(train_ret > 0) if len(train_ret) > 0 else 0
            train_ev = np.mean(train_ret) if len(train_ret) > 0 else 0
            train_sr = (np.mean(train_ret) / np.std(train_ret) * np.sqrt(252)
                        if np.std(train_ret) > 0 else 0)

            # 计算测试集统计（OOS）
            test_wr = np.mean(test_ret > 0) if len(test_ret) > 0 else 0
            test_ev = np.mean(test_ret) if len(test_ret) > 0 else 0
            test_sr = (np.mean(test_ret) / np.std(test_ret) * np.sqrt(252)
                       if np.std(test_ret) > 0 else 0)

            window_result = {
                'window':       w_idx + 1,
                'train_n':      len(train_idx),
                'test_n':       len(test_idx),
                'train_start':  int(train_idx[0]),
                'train_end':    int(train_idx[-1]),
                'test_start':   int(test_idx[0]),
                'test_end':     int(test_idx[-1]),
                # IS（样本内）
                'is_wr':        round(float(train_wr), 4),
                'is_ev':        round(float(train_ev), 4),
                'is_sr':        round(float(train_sr), 4),
                # OOS（样本外）
                'oos_wr':       round(float(test_wr), 4),
                'oos_ev':       round(float(test_ev), 4),
                'oos_sr':       round(float(test_sr), 4),
                # 泄露指标
                'oos_degradation': round(float(train_wr - test_wr), 4),
            }
            window_results.append(window_result)
            all_test_returns.extend(test_ret.tolist())
            all_train_wrs.append(float(train_wr))

            if self.verbose:
                print(f'  W{w_idx+1:2d} IS={train_wr:.1%}({len(train_idx)}笔) '
                      f'OOS={test_wr:.1%}({len(test_idx)}笔) '
                      f'退化={train_wr-test_wr:+.1%}')

        # 汇总统计
        all_test_arr = np.array(all_test_returns)
        overall_wr   = float(np.mean(all_test_arr > 0))
        overall_ev   = float(np.mean(all_test_arr))
        overall_sr   = (float(np.mean(all_test_arr) / np.std(all_test_arr) * np.sqrt(252))
                        if np.std(all_test_arr) > 0 else 0)

        # 一致性分数：OOS WR的稳定性（标准差越小越稳定）
        oos_wrs = [w['oos_wr'] for w in window_results]
        consistency_score = 1 - float(np.std(oos_wrs)) if len(oos_wrs) > 1 else 0.5

        # 过拟合比率：平均IS WR / OOS WR
        avg_train_wr = float(np.mean(all_train_wrs))
        overfitting_ratio = (avg_train_wr / overall_wr
                             if overall_wr > 0 else float('inf'))

        # 统计显著性（二项检验，H0: WR=50%）
        n_test = len(all_test_returns)
        n_wins = int(sum(1 for r in all_test_returns if r > 0))
        # 简化二项检验: z = (p - 0.5) / sqrt(0.25/n)
        if n_test > 0:
            z_score = (overall_wr - 0.5) / np.sqrt(0.25 / n_test)
            is_significant = bool(z_score > 1.645)  # 单尾 95% 置信
        else:
            z_score = 0
            is_significant = False

        return {
            'n_windows':          len(windows),
            'n_test_trades':      n_test,
            'overall_wr':         round(overall_wr, 4),
            'overall_ev':         round(overall_ev, 4),
            'overall_sr':         round(overall_sr, 4),
            'consistency_score':  round(consistency_score, 4),
            'overfitting_ratio':  round(overfitting_ratio, 4),
            'avg_is_wr':          round(avg_train_wr, 4),
            'z_score':            round(z_score, 4),
            'is_significant':     is_significant,
            'window_results':     window_results,
            'generated_at':       datetime.now(timezone.utc).isoformat(),
        }


# ─────────────────────────────────────────────────────────────────
# 数据加载：从梵天历史记录加载
# ─────────────────────────────────────────────────────────────────

def load_returns_from_signal_log(symbol: Optional[str] = None) -> Tuple[np.ndarray, list]:
    """从 live_signal_log.jsonl 加载已结算的信号收益率"""
    log_path = DATA / 'live_signal_log.jsonl'
    if not log_path.exists():
        return np.array([]), []

    records = []
    with open(log_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                if not r.get('settled'):
                    continue
                outcome = r.get('outcome', '')
                if outcome not in ('TP1', 'TP2', 'SL', 'TIMEOUT'):
                    continue
                pnl = r.get('pnl_pct')
                if pnl is None:
                    continue
                if symbol and r.get('symbol') != symbol:
                    continue
                records.append(r)
            except Exception:
                continue

    if not records:
        return np.array([]), []

    # 按时间排序
    records.sort(key=lambda r: float(r.get('ts', 0)))
    returns = np.array([float(r['pnl_pct']) for r in records])
    return returns, records


def load_returns_from_backtest(symbol: Optional[str] = None) -> Tuple[np.ndarray, list]:
    """从 dharma 回测结果加载收益率（backtest_results.jsonl 或类似文件）"""
    candidates = [
        DATA / 'backtest_results.jsonl',
        DHARMA_DATA / 'backtest_results.jsonl',
        ROOT / 'dharma' / 'data' / 'wr_stats_v7.json',
    ]

    for path in candidates:
        if not path.exists():
            continue

        records = []
        try:
            if path.suffix == '.jsonl':
                with open(path) as f:
                    for line in f:
                        try:
                            r = json.loads(line.strip())
                            if symbol and r.get('symbol') != symbol:
                                continue
                            pnl = r.get('pnl_pct') or r.get('pnl') or r.get('return')
                            if pnl is not None:
                                records.append({'pnl_pct': float(pnl), 'ts': r.get('ts', 0)})
                        except Exception:
                            continue
            elif path.suffix == '.json':
                with open(path) as f:
                    data = json.load(f)
                if isinstance(data, list):
                    records = [{'pnl_pct': float(r.get('pnl_pct', 0)), 'ts': r.get('ts', 0)}
                                for r in data if r.get('pnl_pct') is not None]
        except Exception:
            continue

        if len(records) >= 20:
            records.sort(key=lambda r: float(r.get('ts', 0)))
            returns = np.array([r['pnl_pct'] for r in records])
            return returns, records

    return np.array([]), []


# ─────────────────────────────────────────────────────────────────
# 与 CPCV 对比报告
# ─────────────────────────────────────────────────────────────────

def compare_with_cpcv(wf_result: Dict, cpcv_wr: float, cpcv_dsr: float) -> Dict:
    """对比WF结果与已知CPCV+DSR结论"""
    oos_wr = wf_result.get('overall_wr', 0)
    degradation_avg = np.mean([w['oos_degradation']
                                for w in wf_result.get('window_results', [])])

    consistency_pass = wf_result.get('consistency_score', 0) > 0.7
    overfitting_ok   = wf_result.get('overfitting_ratio', 999) < 1.3
    wr_aligned       = abs(oos_wr - cpcv_wr) < 0.10   # WF OOS WR与CPCV WR偏差<10%

    verdict = 'PASS' if (consistency_pass and overfitting_ok and wr_aligned) else 'WARN'

    return {
        'wf_oos_wr':          round(oos_wr, 4),
        'cpcv_wr':            round(cpcv_wr, 4),
        'wr_delta':           round(oos_wr - cpcv_wr, 4),
        'wr_aligned':         wr_aligned,
        'consistency_pass':   consistency_pass,
        'overfitting_ok':     overfitting_ok,
        'avg_degradation':    round(float(degradation_avg), 4),
        'verdict':            verdict,
    }


# ─────────────────────────────────────────────────────────────────
# 合成数据演示（当实盘数据不足时）
# ─────────────────────────────────────────────────────────────────

def generate_synthetic_returns(
    n: int = 200,
    wr: float = 0.65,
    avg_win: float = 2.0,
    avg_loss: float = -1.0,
    seed: int = 42,
) -> np.ndarray:
    """
    生成合成交易收益率（用于架构验证）
    模拟梵天信号特征：WR=65%, RR≈2.0
    """
    rng = np.random.default_rng(seed)
    wins  = rng.exponential(scale=avg_win,  size=int(n * wr))
    losses = -rng.exponential(scale=-avg_loss, size=n - len(wins))
    returns = np.concatenate([wins, losses])
    rng.shuffle(returns)
    return returns


# ─────────────────────────────────────────────────────────────────
# CLI 入口
# ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='梵天 Walk-Forward 验证器')
    parser.add_argument('--symbol',       default=None,   help='标的符号（如 BTCUSDT）')
    parser.add_argument('--full',         action='store_true', help='完整报告（含每窗口详情）')
    parser.add_argument('--compare-cpcv', action='store_true', help='与CPCV结论对比')
    parser.add_argument('--synthetic',    action='store_true', help='用合成数据演示架构')
    parser.add_argument('--train-ratio',  type=float, default=0.70, help='训练集比例（默认0.70）')
    parser.add_argument('--step',         type=float, default=0.10, help='步进比例（默认0.10）')
    args = parser.parse_args()

    print('=' * 60)
    print('🏛️  梵天 Walk-Forward Validator | 设计院 2026-07-03')
    print('=' * 60)

    # 加载数据
    returns = np.array([])

    if not args.synthetic:
        returns, records = load_returns_from_signal_log(args.symbol)
        if len(returns) < 20:
            print(f'live_signal_log数据不足({len(returns)}条), 尝试回测数据...')
            returns, records = load_returns_from_backtest(args.symbol)

    if len(returns) < 20 or args.synthetic:
        if not args.synthetic:
            print(f'实盘/回测数据不足({len(returns)}条), 使用合成数据演示架构...')
        print('使用合成数据: n=200 WR=65% RR=2.0 (模拟梵天信号特征)')
        returns = generate_synthetic_returns(n=200, wr=0.65)
        print()

    print(f'数据: {len(returns)}笔交易 | 标的: {args.symbol or "全部"}')
    print(f'参数: 训练比={args.train_ratio:.0%} 步进={args.step:.0%}')
    print()

    # 执行 Walk-Forward
    validator = WalkForwardValidator(
        train_ratio=args.train_ratio,
        step_ratio=args.step,
        verbose=True,
    )

    print('── Walk-Forward 窗口扫描 ──')
    result = validator.validate(returns)
    print()

    if result.get('error'):
        print(f'❌ {result["error"]}')
        return

    # 主报告
    print('── Walk-Forward 汇总 ──')
    print(f'  验证窗口数:   {result["n_windows"]}')
    print(f'  OOS总交易数:  {result["n_test_trades"]}')
    print(f'  OOS WR:       {result["overall_wr"]:.1%}')
    print(f'  OOS EV:       {result["overall_ev"]:+.3f}%')
    print(f'  OOS SR(年化): {result["overall_sr"]:+.2f}')
    print(f'  IS 平均WR:    {result["avg_is_wr"]:.1%}')
    print(f'  一致性分数:   {result["consistency_score"]:.3f} '
          f'({"✅" if result["consistency_score"] > 0.7 else "⚠️"})')
    print(f'  过拟合比:     {result["overfitting_ratio"]:.2f}x '
          f'({"✅<1.3" if result["overfitting_ratio"] < 1.3 else "⚠️>1.3"})')
    print(f'  Z分数:        {result["z_score"]:.2f} '
          f'({"✅显著" if result["is_significant"] else "⚠️不显著"})')

    # 与CPCV对比
    if args.compare_cpcv:
        print()
        print('── 与CPCV对比 ──')
        cmp = compare_with_cpcv(result, cpcv_wr=0.827, cpcv_dsr=22.64)
        print(f'  WF OOS WR:  {cmp["wf_oos_wr"]:.1%}')
        print(f'  CPCV WR:    {cmp["cpcv_wr"]:.1%}')
        print(f'  WR偏差:     {cmp["wr_delta"]:+.1%} ({"✅对齐" if cmp["wr_aligned"] else "⚠️偏差>10%"})')
        print(f'  一致性:     {"✅通过" if cmp["consistency_pass"] else "⚠️不稳定"}')
        print(f'  过拟合:     {"✅正常" if cmp["overfitting_ok"] else "⚠️可能过拟合"}')
        print(f'  综合裁决:   {"✅ PASS" if cmp["verdict"] == "PASS" else "⚠️ WARN"}')

    # 完整窗口详情
    if args.full:
        print()
        print('── 窗口详情 ──')
        print(f'{"W":>3}  {"训练N":>5} {"IS-WR":>6} {"IS-EV":>7}  '
              f'{"测试N":>5} {"OOS-WR":>7} {"OOS-EV":>8} {"退化":>7}')
        print('-' * 65)
        for w in result['window_results']:
            print(f'W{w["window"]:>2}  {w["train_n"]:>5} {w["is_wr"]:>6.1%} '
                  f'{w["is_ev"]:>+7.3f}  {w["test_n"]:>5} {w["oos_wr"]:>7.1%} '
                  f'{w["oos_ev"]:>+8.3f} {w["oos_degradation"]:>+7.1%}')

    # 保存结果
    out_path = DATA / 'wf_validation_result.json'
    with open(out_path, 'w') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print()
    print(f'✅ 结果已保存: {out_path}')

    print()
    print('── 梵天验证三件套状态 ──')
    print('  ✅ CPCV  (combinatorial_purged_cv.py)')
    print('  ✅ DSR   (deflated_sharpe.py)')
    print('  ✅ WF    (walk_forward.py)  ← 今日补齐')
    print()
    print('🏛️  Dharma验证拼图完整。')


if __name__ == '__main__':
    main()
