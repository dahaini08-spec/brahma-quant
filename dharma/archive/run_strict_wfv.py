#!/usr/bin/env python3
"""
达摩院 · 严格无穿越 WFV 运行入口
用法：python3 dharma/run_strict_wfv.py ETH 1h
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dharma.dharma_strict_wfv import StrictWFV, NodePurityChecker, analyze_strengths_weaknesses, _stats, _grade

def bar(title, width=60):
    print()
    print('═' * width)
    print(f'  {title}')
    print('═' * width)

def main():
    symbol    = sys.argv[1].upper() if len(sys.argv) > 1 else 'ETH'
    timeframe = sys.argv[2].lower() if len(sys.argv) > 2 else '1h'

    print(f'\n🏯 达摩院 · 严格无穿越 Walk-Forward 验证')
    print(f'   标的: {symbol}USDT  周期: {timeframe}')
    print(f'   原则: T 时刻信号只看 T 之前的 K 线，结局用 T+1~T+48 复盘')

    t0 = time.time()
    wfv = StrictWFV(n_folds=5, min_train_years=1.5)

    try:
        fold_results = wfv.run(symbol, timeframe)
    except FileNotFoundError as e:
        print(f'\n❌ {e}')
        sys.exit(1)

    # ── Fold 明细 ──────────────────────────────────────────────
    bar(f'Walk-Forward 分 Fold 结果（{len(fold_results)} 折）')
    all_oos_outcomes = []

    for fr in fold_results:
        is_s  = fr.is_stats
        oos_s = fr.oos_stats
        decay = round(is_s['wr'] - oos_s['wr'], 4)
        grade = _grade(oos_s)
        print(f'\n  Fold {fr.fold}')
        print(f'    训练窗口: {fr.train_start} ~ {fr.train_end}')
        print(f'    测试窗口: {fr.test_start} ~ {fr.test_end}')
        print(f'    IS  → WR={is_s["wr"]*100:.1f}%  PF={is_s["pf"]:.3f}  n={is_s["n"]}')
        print(f'    OOS → WR={oos_s["wr"]*100:.1f}%  PF={oos_s["pf"]:.3f}  n={oos_s["n"]}  Grade={grade}')
        print(f'    胜率衰减: {decay*100:+.1f}ppt  {"⚠️ 过拟合风险" if decay > 0.08 else "✅ 泛化正常"}')
        all_oos_outcomes.extend(fr.outcomes)

    # ── 整体 OOS 汇总 ──────────────────────────────────────────
    bar('整体 OOS 汇总')
    overall = _stats(all_oos_outcomes)
    grade   = _grade(overall)
    print(f'  总 OOS 信号数: {overall["n"]}')
    print(f'  胜率 WR:       {overall["wr"]*100:.2f}%')
    print(f'  利润因子 PF:   {overall["pf"]:.3f}')
    print(f'  平均盈亏%:     {overall["avg_pnl"]:+.3f}%')
    print(f'  系统评级:      {grade}')

    # ── 优劣势分析 ─────────────────────────────────────────────
    if all_oos_outcomes:
        bar('优劣势分析')
        analysis = analyze_strengths_weaknesses(all_oos_outcomes)

        print('\n  📊 体制表现:')
        for regime, s in sorted(analysis['regime_stats'].items(),
                                  key=lambda x: x[1]['pf'], reverse=True):
            flag = '🏆' if regime == analysis['best_regime'] else ('❌' if regime == analysis['worst_regime'] else '  ')
            print(f'    {flag} {regime:<15} WR={s["wr"]*100:.1f}%  PF={s["pf"]:.3f}  n={s["n"]}')

        print('\n  📅 年份表现:')
        for year in sorted(analysis['year_stats'].keys()):
            s = analysis['year_stats'][year]
            flag = '🏆' if year == analysis['best_year'] else ('❌' if year == analysis['worst_year'] else '  ')
            print(f'    {flag} {year}  WR={s["wr"]*100:.1f}%  PF={s["pf"]:.3f}  n={s["n"]}')

        if analysis['best_hour'] != -1:
            print(f'\n  ⏰ 最佳入场时段: UTC {analysis["best_hour"]:02d}:00  '
                  f'WR={analysis["hour_stats"][analysis["best_hour"]]["wr"]*100:.1f}%')

        print('\n  💡 改进建议:')
        for i, s in enumerate(analysis['suggestions'], 1):
            print(f'    {i}. {s}')

    # ── 节点纯洁性检查 ─────────────────────────────────────────
    bar('节点纯洁性验证')
    sym_lower = symbol.lower()
    soul_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             'lana', 'soul_db', f'节点_{sym_lower}usdt.jsonl')
    checker = NodePurityChecker()
    report  = checker.check(soul_path)
    if 'error' not in report:
        print(f'  节点总数:       {report["total_nodes"]}')
        print(f'  2025+ 节点:     {report["post_2025_nodes"]}  （含完整结局: {report["post_2025_with_full_outcome"]}）')
        print(f'  末尾可疑节点:   {report["late_nodes_with_outcome"]}')
        print(f'  纯洁率:         {report["purity_rate"]}%')
        print(f'  判定: {report["verdict"]}')
        print(f'  说明: {report["note"]}')
    else:
        print(f'  {report["error"]}')

    elapsed = time.time() - t0
    print(f'\n✅ 完成，耗时 {elapsed:.1f}s\n')


if __name__ == '__main__':
    main()
