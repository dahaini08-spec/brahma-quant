#!/usr/bin/env python3
"""
True Walk-Forward Validator with Parameter Freeze
===============================================
Phase B · 2026-09-13 三方联合深度评估

核心修复（vs旧dharma_ultimate_validator.py）:
1. 训练段估阈值/参数 → 测试段参数完全冻结 → 无前视
2. 每折记录: 交易数, WR, 成本后期望, MDD, 夏普
3. 组合剔除(CPCV)的PBO计算
4. 严禁用全样本数据在折内做任何归一化

Usage:
    python3 arch/validation/walk_forward.py --signals data/live_signal_log.jsonl
    python3 arch/validation/walk_forward.py --signals data/live_signal_log.jsonl --report
"""
import json, sys, os, time, math, argparse
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict

# ── 成本模型 ──────────────────────────────────────────────
TAKER_FEE = 0.0005   # 0.05% taker
SLIPPAGE_BP = 2      # 2bp 滑点
COST_PER_TRADE = TAKER_FEE + SLIPPAGE_BP / 10000  # ~0.0007

# ── 数据加载 ──────────────────────────────────────────────
def load_signals(path: str) -> list[dict]:
    """从live_signal_log.jsonl加载已结算信号"""
    signals = []
    with open(path) as f:
        for line in f:
            try:
                s = json.loads(line)
            except json.JSONDecodeError:
                continue
            # 只取已结算且有pnl的
            if not s.get('settled') and s.get('status') not in ('TP1','TP2','SL','TIMEOUT','EXPIRED','EXPIRED_NO_TOUCH'):
                continue
            pnl = s.get('pnl_pct')
            if pnl is None:
                # 从outcome推断
                outcome = s.get('outcome', '')
                sl_pct = s.get('sl_pct', 2.0)
                rr1 = s.get('rr1', 1.0)
                if outcome == 'TP1':
                    pnl = rr1 * sl_pct
                elif outcome == 'TP2':
                    pnl = rr1 * sl_pct * 2
                elif outcome == 'SL':
                    pnl = -sl_pct
                else:
                    continue  # 无法推断的跳过
            signals.append({
                'ts': s.get('ts', 0),
                'ts_iso': s.get('ts_iso', ''),
                'symbol': s.get('symbol', ''),
                'direction': s.get('direction', s.get('signal_dir', '')),
                'regime': s.get('regime', ''),
                'score': s.get('score', 0),
                'pnl_pct': pnl,
                'sl_pct': s.get('sl_pct', 2.0),
                'rr1': s.get('rr1', 1.0),
                'leverage': s.get('leverage_used', 5.0),
                'outcome': s.get('outcome', ''),
                'structure_grade': s.get('structure_grade', 0),
                'rsi_1h': s.get('rsi_1h', 50),
                'rsi_4h': s.get('rsi_4h', 50),
            })
    signals.sort(key=lambda x: x['ts'])
    return signals

# ── 真Walk-Forward ─────────────────────────────────────────
def true_walk_forward(
    signals: list[dict],
    train_ratio: float = 0.6,
    n_folds: int = 10,
    min_train: int = 30,
    min_test: int = 10,
) -> dict:
    """
    真Walk-Forward验证:
    1. 每折: 训练段估计最优score阈值和RR，测试段完全冻结参数
    2. 训练段: 扫描score阈值[80-160]，找最大化EV的阈值
    3. 测试段: 用冻结的阈值计算WR/EV/MDD/Sharpe
    4. 禁止用测试段数据做任何归一化或调整
    """
    n = len(signals)
    if n < min_train + min_test:
        return {'error': f'样本不足: {n} < {min_train}+{min_test}'}
    
    ts_min = signals[0]['ts']
    ts_max = signals[-1]['ts']
    total_span = ts_max - ts_min
    
    # 滚动窗口切分
    train_span = total_span * train_ratio / n_folds if n_folds > 1 else total_span * train_ratio
    test_span = total_span * (1 - train_ratio) / n_folds if n_folds > 1 else total_span * (1 - train_ratio)
    
    folds = []
    for i in range(n_folds):
        train_start = ts_min + (total_span - train_span - test_span) * i / max(n_folds - 1, 1)
        train_end = train_start + train_span
        test_start = train_end
        test_end = test_start + test_span
        
        train_sigs = [s for s in signals if train_start <= s['ts'] < train_end]
        test_sigs = [s for s in signals if test_start <= s['ts'] < test_end]
        
        if len(train_sigs) < min_train or len(test_sigs) < min_test:
            continue
        
        # ── 训练段: 扫描score阈值找最优 ──
        best_threshold = 100  # 默认
        best_ev = -999
        for threshold in range(80, 161, 5):
            filtered = [s for s in train_sigs if s['score'] >= threshold]
            if len(filtered) < 5:
                continue
            # 成本后EV: 每笔扣2×COST_PER_TRADE × leverage
            ev = sum(s['pnl_pct'] * s['leverage'] - 2 * COST_PER_TRADE * 100 * s['leverage'] for s in filtered) / len(filtered)
            if ev > best_ev:
                best_ev = ev
                best_threshold = threshold
        
        # ── 测试段: 参数完全冻结 ──
        test_filtered = [s for s in test_sigs if s['score'] >= best_threshold]
        test_all = test_sigs  # 也记录全量对比
        
        if len(test_filtered) < 1:
            folds.append({
                'fold': i + 1,
                'train_n': len(train_sigs),
                'test_n': len(test_sigs),
                'test_filtered_n': 0,
                'threshold': best_threshold,
                'train_ev': round(best_ev, 4),
                'test_wr': 0,
                'test_ev': 0,
                'test_mdd': 0,
                'test_sharpe': 0,
                'skipped': True,
            })
            continue
        
        # 测试段统计
        pnls = [s['pnl_pct'] * s['leverage'] - 2 * COST_PER_TRADE * 100 * s['leverage'] for s in test_filtered]
        wins = sum(1 for p in pnls if p > 0)
        losses = sum(1 for p in pnls if p <= 0)
        wr = wins / len(pnls) * 100
        ev = sum(pnls) / len(pnls)
        
        # MDD
        nav = 0
        peak = 0
        max_dd = 0
        for p in pnls:
            nav += p
            if nav > peak:
                peak = nav
            dd = (peak - nav) / peak * 100 if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd
        
        # Sharpe (年化)
        if len(pnls) > 1:
            mean_pnl = sum(pnls) / len(pnls)
            std_pnl = (sum((p - mean_pnl) ** 2 for p in pnls) / (len(pnls) - 1)) ** 0.5
            # 信号频率 ~ 每周3笔 → 年化 ~156笔
            sharpe = mean_pnl / std_pnl * math.sqrt(156) if std_pnl > 0 else 0
        else:
            sharpe = 0
        
        folds.append({
            'fold': i + 1,
            'train_n': len(train_sigs),
            'test_n': len(test_sigs),
            'test_filtered_n': len(test_filtered),
            'threshold': best_threshold,
            'train_ev': round(best_ev, 4),
            'test_wr': round(wr, 1),
            'test_ev': round(ev, 4),
            'test_mdd': round(max_dd, 2),
            'test_sharpe': round(sharpe, 2),
            'wins': wins,
            'losses': losses,
        })
    
    if not folds:
        return {'error': '无法生成有效折', 'n_signals': n}
    
    # 汇总
    valid_folds = [f for f in folds if not f.get('skipped')]
    if not valid_folds:
        return {'error': '所有折均被跳过', 'folds': folds}
    
    passed = sum(1 for f in valid_folds if f['test_wr'] >= 52)
    failed = sum(1 for f in valid_folds if f['test_wr'] < 50)
    avg_wr = sum(f['test_wr'] for f in valid_folds) / len(valid_folds)
    avg_ev = sum(f['test_ev'] for f in valid_folds) / len(valid_folds)
    avg_sharpe = sum(f['test_sharpe'] for f in valid_folds) / len(valid_folds)
    
    return {
        'n_folds': len(folds),
        'n_valid': len(valid_folds),
        'passed_folds': passed,
        'failed_folds': failed,
        'stability': round(passed / len(valid_folds), 3),
        'avg_wr': round(avg_wr, 1),
        'avg_ev': round(avg_ev, 4),
        'avg_sharpe': round(avg_sharpe, 2),
        'thresholds': [f['threshold'] for f in valid_folds],
        'folds': folds,
        'passed': passed / len(valid_folds) >= 0.6 and failed <= len(valid_folds) * 0.3,
    }

# ── CPCV: 组合剔除交叉验证 ─────────────────────────────────
def combinatorial_purged_cv(
    signals: list[dict],
    n_groups: int = 6,
    n_test_groups: int = 2,
    purge_gap_bars: int = 3,
) -> dict:
    """
    Combinatorial Purged Cross-Validation (CPCV):
    - 将数据分成N组
    - 选K组作为测试，其余训练
    - 在训练-测试边界purge若干bar消除自相关
    - 计算PBO (Probability of Backtest Overfitting)
    """
    n = len(signals)
    if n < n_groups * 5:
        return {'error': f'样本不足: {n} < {n_groups * 5}'}
    
    group_size = n // n_groups
    groups = [signals[i * group_size:(i + 1) * group_size] for i in range(n_groups)]
    # 余数放最后一组
    if n % n_groups:
        groups[-1].extend(signals[n_groups * group_size:])
    
    # 生成所有C(N,K)组合
    from itertools import combinations
    test_combos = list(combinations(range(n_groups), n_test_groups))
    
    results = []
    for combo in test_combos:
        test_idx = set(combo)
        train_idx = set(range(n_groups)) - test_idx
        
        # Purge: 移除训练段中靠近测试段边界的bar
        train_sigs = []
        test_sigs = []
        for gi, g in enumerate(groups):
            if gi in test_idx:
                test_sigs.extend(g)
            else:
                train_sigs.extend(g)
        
        # Purge边界
        test_ts = [s['ts'] for s in test_sigs]
        if test_ts:
            min_ts = min(test_ts)
            max_ts = max(test_ts)
            purge_window = 3600 * 24 * 7  # 7天purge
            train_sigs = [s for s in train_sigs 
                         if not (min_ts - purge_window <= s['ts'] <= max_ts + purge_window)]
        
        if len(train_sigs) < 20 or len(test_sigs) < 10:
            continue
        
        # 训练段: 找最优阈值
        best_thr = 100
        best_ev = -999
        for thr in range(80, 161, 5):
            filtered = [s for s in train_sigs if s['score'] >= thr]
            if len(filtered) < 5:
                continue
            ev = sum(s['pnl_pct'] * s['leverage'] - 2 * COST_PER_TRADE * 100 * s['leverage'] for s in filtered) / len(filtered)
            if ev > best_ev:
                best_ev = ev
                best_thr = thr
        
        # 测试段: 冻结阈值
        test_f = [s for s in test_sigs if s['score'] >= best_thr]
        if len(test_f) < 1:
            continue
        
        pnls = [s['pnl_pct'] * s['leverage'] - 2 * COST_PER_TRADE * 100 * s['leverage'] for s in test_f]
        ev = sum(pnls) / len(pnls)
        wr = sum(1 for p in pnls if p > 0) / len(pnls) * 100
        
        # 对比: 全样本最优 vs 测试段
        train_f = [s for s in train_sigs if s['score'] >= best_thr]
        train_pnls = [s['pnl_pct'] * s['leverage'] - 2 * COST_PER_TRADE * 100 * s['leverage'] for s in train_f]
        train_ev = sum(train_pnls) / len(train_pnls) if train_pnls else 0
        
        results.append({
            'test_groups': list(combo),
            'train_n': len(train_sigs),
            'test_n': len(test_sigs),
            'test_filtered_n': len(test_f),
            'threshold': best_thr,
            'train_ev': round(train_ev, 4),
            'test_ev': round(ev, 4),
            'test_wr': round(wr, 1),
            'is_better': ev > 0,
        })
    
    if not results:
        return {'error': '无有效组合'}
    
    # PBO: 测试段EV<=0的比例
    pbo = sum(1 for r in results if r['test_ev'] <= 0) / len(results)
    
    # 铁证判定: PBO < 50% → 非过拟合
    return {
        'n_combos': len(results),
        'pbo': round(pbo, 3),
        'pbo_threshold': 0.50,
        'is_overfit': pbo >= 0.50,
        'avg_test_ev': round(sum(r['test_ev'] for r in results) / len(results), 4),
        'positive_combos': sum(1 for r in results if r['test_ev'] > 0),
        'negative_combos': sum(1 for r in results if r['test_ev'] <= 0),
        'combos': results,
    }

# ── 报告生成 ──────────────────────────────────────────────
def generate_report(wf_result: dict, cpcv_result: dict, signals: list) -> str:
    lines = []
    lines.append("=" * 70)
    lines.append("Phase B Walk-Forward + CPCV 验证报告")
    lines.append(f"生成时间: {datetime.now(timezone.utc).isoformat()}")
    lines.append(f"样本: {len(signals)} 条已结算信号")
    lines.append("=" * 70)
    
    lines.append("\n── Walk-Forward 结果 ──")
    if 'error' in wf_result:
        lines.append(f"  ❌ {wf_result['error']}")
    else:
        lines.append(f"  折数: {wf_result['n_folds']} (有效: {wf_result['n_valid']})")
        lines.append(f"  通过折: {wf_result['passed_folds']} | 失败折: {wf_result['failed_folds']}")
        lines.append(f"  稳定性: {wf_result['stability']:.1%}")
        lines.append(f"  平均WR: {wf_result['avg_wr']}% | 平均EV: {wf_result['avg_ev']}%")
        lines.append(f"  平均Sharpe: {wf_result['avg_sharpe']}")
        lines.append(f"  判定: {'✅ 通过' if wf_result['passed'] else '❌ 不通过'}")
        lines.append("\n  逐折明细:")
        lines.append(f"  {'折':>3} {'训练n':>6} {'测试n':>6} {'过滤n':>6} {'阈值':>5} {'训练EV':>8} {'测试WR':>7} {'测试EV':>8} {'MDD':>6} {'Sharpe':>7}")
        for f in wf_result['folds']:
            if f.get('skipped'):
                lines.append(f"  {f['fold']:>3} {f['train_n']:>6} {f['test_n']:>6} {'SKIP':>6} {f['threshold']:>5} {f['train_ev']:>8}")
            else:
                lines.append(f"  {f['fold']:>3} {f['train_n']:>6} {f['test_n']:>6} {f['test_filtered_n']:>6} {f['threshold']:>5} {f['train_ev']:>8} {f['test_wr']:>6.1f}% {f['test_ev']:>8} {f['test_mdd']:>5.1f}% {f['test_sharpe']:>7.2f}")
    
    lines.append("\n── CPCV 结果 ──")
    if 'error' in cpcv_result:
        lines.append(f"  ❌ {cpcv_result['error']}")
    else:
        lines.append(f"  组合数: {cpcv_result['n_combos']}")
        lines.append(f"  PBO: {cpcv_result['pbo']:.1%} (阈值: {cpcv_result['pbo_threshold']:.0%})")
        lines.append(f"  正EV组合: {cpcv_result['positive_combos']} | 负EV组合: {cpcv_result['negative_combos']}")
        lines.append(f"  平均测试EV: {cpcv_result['avg_test_ev']}%")
        lines.append(f"  判定: {'✅ 非过拟合' if not cpcv_result['is_overfit'] else '❌ 过拟合'}")
    
    return '\n'.join(lines)

# ── 主入口 ────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description='True Walk-Forward + CPCV Validator')
    parser.add_argument('--signals', default='data/live_signal_log.jsonl', help='信号文件路径')
    parser.add_argument('--report', action='store_true', help='生成报告文件')
    args = parser.parse_args()
    
    signals = load_signals(args.signals)
    print(f"加载 {len(signals)} 条已结算信号")
    
    if len(signals) < 50:
        print("⚠️ 样本不足50，结果仅供参考")
    
    wf = true_walk_forward(signals, n_folds=8, train_ratio=0.6)
    cpcv = combinatorial_purged_cv(signals, n_groups=6, n_test_groups=2)
    
    report = generate_report(wf, cpcv, signals)
    print(report)
    
    if args.report:
        out_path = Path('docs/phase_b_wf_cpcv_report.md')
        out_path.write_text(report)
        print(f"\n报告已保存: {out_path}")
    
    # 保存原始结果
    results = {'walk_forward': wf, 'cpcv': cpcv, 'n_signals': len(signals)}
    out_json = Path('data/phase_b_validation.json')
    out_json.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"原始数据: {out_json}")

if __name__ == '__main__':
    main()
