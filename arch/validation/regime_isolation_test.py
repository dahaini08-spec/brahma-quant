#!/usr/bin/env python3
"""
Regime × Direction Information Isolation Test
================================================
Phase B · 2026-09-13 三方联合深度评估

审计假设: 体制检测用价格趋势 → 方向乘数双重计算 → OOS必衰减

检测方法:
1. 计算体制标签与方向信号的信息重叠度
2. 在回测数据上做 leave-one-out: 去掉体制乘数 vs 保留乘数
3. 对比OOS WR: 如果去掉乘数后WR不降，说明乘数是噪声而非信号
4. 互信息(Mutual Information)量化重叠度

Usage:
    python3 arch/validation/regime_isolation_test.py --signals data/live_signal_log.jsonl
    python3 arch/validation/regime_isolation_test.py --backtest data/wr_matrix_v8_6y5.json
"""
import json, math, argparse, sys
from pathlib import Path
from collections import Counter, defaultdict
from datetime import datetime, timezone

# ── 互信息计算 ────────────────────────────────────────────
def mutual_information(x: list, y: list) -> float:
    """计算两个离散变量的互信息 I(X;Y)"""
    n = len(x)
    if n == 0:
        return 0
    
    px = Counter(x)
    py = Counter(y)
    pxy = Counter(zip(x, y))
    
    mi = 0
    for (xi, yi), c in pxy.items():
        p_xy = c / n
        p_x = px[xi] / n
        p_y = py[yi] / n
        if p_xy > 0 and p_x > 0 and p_y > 0:
            mi += p_xy * math.log2(p_xy / (p_x * p_y))
    
    return mi

def normalized_mi(x: list, y: list) -> float:
    """归一化互信息 NMI = I(X;Y) / sqrt(H(X) * H(Y))"""
    n = len(x)
    if n == 0:
        return 0
    
    def entropy(labels):
        c = Counter(labels)
        h = 0
        for count in c.values():
            p = count / n
            if p > 0:
                h -= p * math.log2(p)
        return h
    
    mi = mutual_information(x, y)
    hx = entropy(x)
    hy = entropy(y)
    
    if hx == 0 or hy == 0:
        return 0
    
    return mi / math.sqrt(hx * hy)

# ── 体制×方向重叠检测 ─────────────────────────────────────
def test_regime_direction_overlap(signals: list) -> dict:
    """
    检测体制标签和方向信号的信息重叠
    
    高重叠 → 乘数是双重计算 → OOS会衰减
    低重叠 → 乘数是独立信号 → 有价值
    """
    regimes = [s.get('regime', 'UNKNOWN') for s in signals]
    directions = [s.get('direction', 'UNKNOWN') for s in signals]
    outcomes = ['WIN' if (s.get('pnl_pct', 0) or 0) > 0 else 'LOSS' for s in signals]
    
    # 1. 互信息: 体制 vs 方向
    mi_regime_dir = mutual_information(regimes, directions)
    nmi_regime_dir = normalized_mi(regimes, directions)
    
    # 2. 互信息: 体制 vs 结果
    mi_regime_outcome = mutual_information(regimes, outcomes)
    nmi_regime_outcome = normalized_mi(regimes, outcomes)
    
    # 3. 互信息: 方向 vs 结果
    mi_dir_outcome = mutual_information(directions, outcomes)
    nmi_dir_outcome = normalized_mi(directions, outcomes)
    
    # 4. 条件互信息: 体制给出方向后的增量信息
    # I(Outcome; Regime | Direction) = I(Outcome; Regime, Direction) - I(Outcome; Direction)
    regime_dir = [f"{r}|{d}" for r, d in zip(regimes, directions)]
    mi_regime_dir_outcome = mutual_information(regime_dir, outcomes)
    conditional_mi = mi_regime_dir_outcome - mi_dir_outcome
    
    return {
        'mi_regime_direction': round(mi_regime_dir, 4),
        'nmi_regime_direction': round(nmi_regime_dir, 4),
        'mi_regime_outcome': round(mi_regime_outcome, 4),
        'nmi_regime_outcome': round(nmi_regime_outcome, 4),
        'mi_direction_outcome': round(mi_dir_outcome, 4),
        'nmi_direction_outcome': round(nmi_dir_outcome, 4),
        'conditional_mi_regime_given_direction': round(conditional_mi, 4),
        'interpretation': {
            'regime_direction_overlap': '高重叠→双重计算' if nmi_regime_dir > 0.3 else '低重叠→独立信号',
            'regime_predictive_power': '有预测力' if nmi_regime_outcome > 0.05 else '弱预测力',
            'conditional_value': '体制有增量信息' if conditional_mi > 0.02 else '体制无增量信息（双重计算）',
        }
    }

# ── 乘数效果对比: 有乘数 vs 无乘数 ─────────────────────────
def test_multiplier_effect(signals: list) -> dict:
    """
    对比: 体制乘数vs等权重
    
    如果乘数是有效信号: 加权后EV应显著高于等权
    如果乘数是双重计算: 加权后EV不优于等权（甚至更差，因为过度减仓好信号）
    """
    # 体制乘数表 (从brahma_core.py L332-382)
    regime_mult = {
        'BEAR_TREND':    {'LONG': 0.50, 'SHORT': 1.60},
        'BEAR_EARLY':    {'LONG': 0.35, 'SHORT': 1.20},
        'BEAR_RECOVERY': {'LONG': 1.15, 'SHORT': 0.50},
        'BULL_TREND':    {'LONG': 1.20, 'SHORT': 0.50},
        'BULL_EARLY':    {'LONG': 1.20, 'SHORT': 0.35},
        'CHOP_MID':      {'LONG': 0.50, 'SHORT': 0.88},
    }
    
    # 按体制×方向分组
    groups = defaultdict(list)
    for s in signals:
        regime = s.get('regime', 'UNKNOWN')
        direction = s.get('direction', 'UNKNOWN')
        pnl = s.get('pnl_pct', 0) or 0
        groups[(regime, direction)].append(pnl)
    
    # 计算加权EV vs 等权EV
    weighted_ev = 0
    equal_ev = 0
    total_n = 0
    
    group_details = []
    for (regime, direction), pnls in groups.items():
        n = len(pnls)
        if n == 0:
            continue
        avg_pnl = sum(pnls) / n
        
        # 乘数
        mult = regime_mult.get(regime, {}).get(direction, 1.0)
        
        # 加权贡献: avg_pnl × mult × n / total
        weighted_ev += avg_pnl * mult * n
        equal_ev += avg_pnl * n
        total_n += n
        
        group_details.append({
            'regime': regime,
            'direction': direction,
            'n': n,
            'avg_pnl': round(avg_pnl, 4),
            'multiplier': mult,
            'weighted_contribution': round(avg_pnl * mult, 4),
            'equal_contribution': round(avg_pnl, 4),
        })
    
    if total_n > 0:
        weighted_ev /= total_n
        equal_ev /= total_n
    
    # 分时间窗口: 前半训练 vs 后半测试
    sorted_signals = sorted(signals, key=lambda s: s.get('ts', 0))
    mid = len(sorted_signals) // 2
    first_half = sorted_signals[:mid]
    second_half = sorted_signals[mid:]
    
    # 前半: 计算每组的EV
    train_groups = defaultdict(list)
    for s in first_half:
        regime = s.get('regime', 'UNKNOWN')
        direction = s.get('direction', 'UNKNOWN')
        pnl = s.get('pnl_pct', 0) or 0
        train_groups[(regime, direction)].append(pnl)
    
    # 后半: 用前半的参数 vs 等权
    test_weighted = 0
    test_equal = 0
    test_n = 0
    for s in second_half:
        regime = s.get('regime', 'UNKNOWN')
        direction = s.get('direction', 'UNKNOWN')
        pnl = s.get('pnl_pct', 0) or 0
        mult = regime_mult.get(regime, {}).get(direction, 1.0)
        test_weighted += pnl * mult
        test_equal += pnl
        test_n += 1
    
    if test_n > 0:
        test_weighted /= test_n
        test_equal /= test_n
    
    return {
        'full_sample': {
            'weighted_ev': round(weighted_ev, 4),
            'equal_ev': round(equal_ev, 4),
            'difference': round(weighted_ev - equal_ev, 4),
            'multiplier_better': weighted_ev > equal_ev,
        },
        'oos_split': {
            'train_n': len(first_half),
            'test_n': len(second_half),
            'test_weighted_ev': round(test_weighted, 4),
            'test_equal_ev': round(test_equal, 4),
            'test_difference': round(test_weighted - test_equal, 4),
            'multiplier_better_oos': test_weighted > test_equal,
        },
        'group_details': group_details,
    }

# ── 大样本回测验证 ─────────────────────────────────────────
def test_backtest_multiplier(backtest_path: str) -> dict:
    """
    用6.5年回测大样本验证: 体制×方向乘数是否有效
    """
    d = json.load(open(backtest_path))
    
    results = {}
    for sym in d:
        sym_data = {}
        for regime in d[sym]:
            for direction in d[sym][regime]:
                stats = d[sym][regime][direction].get('ALL', {})
                if isinstance(stats, dict) and 'wr' in stats:
                    wr = stats['wr']
                    ev = stats.get('ev', 0)
                    n = stats.get('n', 0)
                    sym_data[f"{regime}|{direction}"] = {
                        'wr': wr,
                        'ev': ev,
                        'n': n,
                        'is_positive_ev': ev > 0,
                    }
        results[sym] = sym_data
    
    # 分析: 乘数放大方向的EV是否确实更高？
    mult_amplified = 0
    mult_attenuated = 0
    for sym in results:
        for key, stats in results[sym].items():
            regime, direction = key.split('|')
            # 乘数>1的方向应该有正EV
            if direction == 'LONG' and regime in ('BULL_TREND', 'BULL_EARLY', 'BEAR_RECOVERY'):
                if stats['ev'] > 0:
                    mult_amplified += 1
                else:
                    mult_attenuated += 1
            elif direction == 'SHORT' and regime in ('BEAR_TREND', 'BEAR_EARLY'):
                if stats['ev'] > 0:
                    mult_amplified += 1
                else:
                    mult_attenuated += 1
    
    return {
        'backtest_results': results,
        'multiplier_amplified_correct': mult_amplified,
        'multiplier_amplified_wrong': mult_attenuated,
        'accuracy': round(mult_amplified / (mult_amplified + mult_attenuated), 3) if (mult_amplified + mult_attenuated) > 0 else 0,
    }

# ── 主入口 ────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description='Regime × Direction Information Isolation Test')
    parser.add_argument('--signals', default='data/live_signal_log.jsonl')
    parser.add_argument('--backtest', default='data/wr_matrix_v8_6y5.json')
    parser.add_argument('--report', action='store_true')
    args = parser.parse_args()
    
    # 加载实盘信号
    signals = []
    with open(args.signals) as f:
        for line in f:
            try:
                s = json.loads(line)
            except:
                continue
            if not s.get('settled') and s.get('status') not in ('TP1','TP2','SL','TIMEOUT','EXPIRED','EXPIRED_NO_TOUCH'):
                continue
            signals.append(s)
    
    print(f"加载 {len(signals)} 条实盘信号")
    
    # 1. 信息重叠检测
    print(f"\n{'='*60}")
    print("1. 体制×方向信息重叠检测")
    print(f"{'='*60}")
    overlap = test_regime_direction_overlap(signals)
    for k, v in overlap.items():
        if isinstance(v, dict):
            print(f"  {k}:")
            for k2, v2 in v.items():
                print(f"    {k2}: {v2}")
        else:
            print(f"  {k}: {v}")
    
    # 2. 乘数效果对比
    print(f"\n{'='*60}")
    print("2. 乘数效果对比 (加权 vs 等权)")
    print(f"{'='*60}")
    effect = test_multiplier_effect(signals)
    print(f"  全样本:")
    print(f"    加权EV: {effect['full_sample']['weighted_ev']}%")
    print(f"    等权EV: {effect['full_sample']['equal_ev']}%")
    print(f"    差值:   {effect['full_sample']['difference']}%")
    print(f"    乘数更优: {'是' if effect['full_sample']['multiplier_better'] else '否'}")
    print(f"  OOS拆分:")
    print(f"    训练n: {effect['oos_split']['train_n']} | 测试n: {effect['oos_split']['test_n']}")
    print(f"    测试加权EV: {effect['oos_split']['test_weighted_ev']}%")
    print(f"    测试等权EV: {effect['oos_split']['test_equal_ev']}%")
    print(f"    OOS乘数更优: {'是' if effect['oos_split']['multiplier_better_oos'] else '否'}")
    
    # 3. 大样本回测验证
    print(f"\n{'='*60}")
    print("3. 6.5年回测大样本验证")
    print(f"{'='*60}")
    if Path(args.backtest).exists():
        bt = test_backtest_multiplier(args.backtest)
        print(f"  乘数放大方向正确: {bt['multiplier_amplified_correct']}")
        print(f"  乘数放大方向错误: {bt['multiplier_amplified_wrong']}")
        print(f"  准确率: {bt['accuracy']:.1%}")
        
        for sym, data in bt['backtest_results'].items():
            print(f"\n  {sym}:")
            for key, stats in data.items():
                tag = '✅' if stats['is_positive_ev'] else '❌'
                print(f"    {tag} {key}: WR={stats['wr']}% EV={stats['ev']}% n={stats['n']}")
    else:
        bt = {'error': '回测文件不存在'}
        print(f"  ❌ {args.backtest} 不存在")
    
    # 保存
    results = {
        'overlap': overlap,
        'multiplier_effect': effect,
        'backtest': bt,
        'n_signals': len(signals),
    }
    out = Path('data/phase_b_regime_isolation.json')
    out.write_text(json.dumps(results, indent=2, ensure_ascii=False, default=str))
    print(f"\n保存: {out}")
    
    if args.report:
        report = f"""# Regime × Direction Information Isolation Report

生成时间: {datetime.now(timezone.utc).isoformat()}

## 1. 信息重叠

| 指标 | 值 | 解读 |
|------|-----|------|
| NMI(Regime, Direction) | {overlap['nmi_regime_direction']} | {overlap['interpretation']['regime_direction_overlap']} |
| NMI(Regime, Outcome) | {overlap['nmi_regime_outcome']} | {overlap['interpretation']['regime_predictive_power']} |
| 条件MI(Regime|Direction) | {overlap['conditional_mi_regime_given_direction']} | {overlap['interpretation']['conditional_value']} |

## 2. 乘数效果

| 场景 | 加权EV | 等权EV | 乘数更优 |
|------|--------|--------|---------|
| 全样本 | {effect['full_sample']['weighted_ev']}% | {effect['full_sample']['equal_ev']}% | {effect['full_sample']['multiplier_better']} |
| OOS | {effect['oos_split']['test_weighted_ev']}% | {effect['oos_split']['test_equal_ev']}% | {effect['oos_split']['multiplier_better_oos']} |

## 3. 回测验证

乘数放大方向准确率: {bt.get('accuracy', 'N/A')}
"""
        Path('docs/phase_b_regime_isolation_report.md').write_text(report)
        print("报告: docs/phase_b_regime_isolation_report.md")

if __name__ == '__main__':
    main()
