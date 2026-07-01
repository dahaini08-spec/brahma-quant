#!/usr/bin/env python3
"""
dharma/ta_m0_validator.py — TradingAgents M0 Paper质量验证器 v1.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
设计院 × 达摩院 · 2026-06-17

职责：
  M0 Paper阶段质量评估工具
  对比 TradingAgents Lite 输出 与 实际价格走向的准确率
  用于决策 M0→M1 升级时机

达摩院M0升级条件（全部满足）：
  ✅ n ≥ 30（样本充足）
  ✅ BULL_EARLY体制方向准确率 > 60%
  ✅ Lite模式置信度 > 0.55 时的信号WR > 62%
  ✅ 无幻觉风险（Lite模式不涉及LLM）

用法：
  python3 dharma/ta_m0_validator.py          # 用现有parquet数据验证
  python3 dharma/ta_m0_validator.py --live   # 读取paper_log实时记录
"""

import sys, json, time
from pathlib import Path
from datetime import datetime, timezone

import numpy as np
import pandas as pd

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'brahma_brain'))

RESULTS = BASE / 'dharma' / 'results'
TAG = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')


def validate_lite_on_parquet(sym: str = 'BTCUSDT', quick: bool = True) -> dict:
    """用历史parquet数据验证 Lite 模式在 BULL_EARLY 体制下的准确率"""
    print(f'\n=== TradingAgents Lite · M0验证 · {sym} ===\n')

    from brahma_brain.trading_agents_bridge import _lite_analysis, _meta_to_score, TA_REGIME_COEFF

    fixed = BASE / 'data' / 'backtest' / 'fixed'
    df = pd.read_parquet(fixed / f'{sym.lower()}_15m_fixed.parquet')
    if quick:
        df = df.tail(40000)

    close_col = next(c for c in df.columns if 'close' in c.lower())
    open_col  = next(c for c in df.columns if 'open'  in c.lower())
    high_col  = next(c for c in df.columns if 'high'  in c.lower())
    low_col   = next(c for c in df.columns if 'low'   in c.lower())
    vol_col   = df.columns[4] if len(df.columns) > 4 else df.columns[0]

    closes = df[close_col].values
    opens  = df[open_col].values
    highs  = df[high_col].values
    lows   = df[low_col].values
    vols   = df[vol_col].values

    def regime_at(i):
        c = closes[max(0, i-200):i]
        if len(c) < 50: return 'CHOP_MID'
        ef = c[-20:].mean(); es = c.mean()
        slope = (ef - es) / es
        d = np.diff(c[-14:])
        g = d[d>0].mean() if any(d>0) else 0
        l = abs(d[d<0].mean()) if any(d<0) else 1e-10
        rsi = 100 - 100/(1+g/l)
        if slope > 0.015 and rsi > 55: return 'BULL_TREND' if slope>0.03 else 'BULL_EARLY'
        elif slope < -0.015 and rsi < 45: return 'BEAR_TREND' if slope<-0.03 else 'BEAR_EARLY'
        elif slope > 0.008 and rsi < 45: return 'BULL_CORRECTION'
        elif slope < -0.008 and rsi > 55: return 'BEAR_RECOVERY'
        return 'CHOP_MID'

    results_by_regime = {}
    stride = 80
    print(f'处理 {len(range(200, len(closes)-17, stride))} 个窗口...')

    for i in range(200, len(closes)-17, stride):
        regime = regime_at(i)
        coeff = TA_REGIME_COEFF.get(regime, 0.0)
        if coeff == 0.0:
            continue  # 非BULL体制跳过

        klines = [[opens[j],highs[j],lows[j],closes[j],vols[j]] for j in range(i-200,i)]
        meta = _lite_analysis(sym, klines, regime)
        bias = meta.get('direction_bias', 'NEUTRAL')
        conf = meta.get('confidence', 0.5)

        # 未来16根收益
        entry = closes[i]
        future = closes[i+1:i+17]
        win_long  = (future.max()-entry)/entry > 0.003
        win_short = (entry-future.min())/entry > 0.003

        key = f'{regime}'
        if key not in results_by_regime:
            results_by_regime[key] = {
                'total': 0,
                'bias_long_correct': 0,   # 预测LONG且价格真涨
                'bias_short_correct': 0,
                'high_conf': [],           # conf>0.6时的WR
                'all_conf': [],
            }

        r = results_by_regime[key]
        r['total'] += 1

        actual_up = (future[-1] - entry) / entry > 0
        if bias == 'LONG'  and actual_up:  r['bias_long_correct']  += 1
        if bias == 'SHORT' and not actual_up: r['bias_short_correct'] += 1

        if bias == 'LONG':
            r['all_conf'].append((conf, int(win_long)))
            if conf > 0.6:
                r['high_conf'].append(int(win_long))
        elif bias == 'SHORT':
            r['all_conf'].append((conf, int(win_short)))
            if conf > 0.6:
                r['high_conf'].append(int(win_short))

    print()
    print(f'{"体制":<22} {"n":>5} {"方向准确率":>10} {"高置信WR":>10} {"评级"}')
    print('─' * 65)

    summary = {}
    for regime, r in sorted(results_by_regime.items()):
        n = r['total']
        if n < 5: continue
        dir_acc = (r['bias_long_correct'] + r['bias_short_correct']) / n * 100
        hc = r['high_conf']
        hc_wr = sum(hc)/len(hc)*100 if hc else float('nan')
        hc_n  = len(hc)

        grade = '✅A级' if dir_acc > 60 else ('⚠️B级' if dir_acc > 55 else '❌C级')
        hc_str = f'{hc_wr:.1f}%(n={hc_n})' if not np.isnan(hc_wr) else 'N/A'

        print(f'  {regime:<20} {n:>5} {dir_acc:>9.1f}%  {hc_str:>14}  {grade}')
        summary[regime] = {'n': n, 'dir_acc': dir_acc, 'hc_wr': hc_wr if not np.isnan(hc_wr) else 0}

    # M0升级决策
    print()
    bull_early = summary.get('BULL_EARLY', {})
    n = bull_early.get('n', 0)
    acc = bull_early.get('dir_acc', 0)
    hc_wr = bull_early.get('hc_wr', 0)

    print('=== 达摩院 M0 升级评估 ===')
    checks = [
        (n >= 30,   f'样本n={n}≥30'),
        (acc > 60,  f'BULL_EARLY方向准确率{acc:.1f}%>60%'),
        (hc_wr > 62, f'高置信WR={hc_wr:.1f}%>62%'),
    ]
    all_pass = all(ok for ok, _ in checks)
    for ok, desc in checks:
        print(f'  {"✅" if ok else "❌"} {desc}')

    print()
    if all_pass:
        print('  🎉 M0条件满足！可升级到 M1（离线回放验证）')
        print('  → 下一步: python3 dharma/dharma_runner.py --gate 2 --ablation ta_s24')
    else:
        print('  ⚠️  M0条件未满足，继续Paper观察')
        print(f'  → 运行: python3 brahma_brain/trading_agents_bridge.py 积累更多样本')

    # 保存报告
    report = {
        'timestamp': TAG,
        'symbol': sym,
        'summary': summary,
        'm0_pass': all_pass,
        'bull_early_stats': bull_early,
    }
    out = RESULTS / f'ta_m0_validation_{TAG}.json'
    with open(out, 'w') as f:
        json.dump(report, f, indent=2, ensure_ascii=False, default=str)
    print(f'\n  报告保存: {out}')
    return report


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--live', action='store_true')
    p.add_argument('--sym', default='BTCUSDT')
    args = p.parse_args()

    if args.live:
        from brahma_brain.trading_agents_bridge import generate_m0_report
        r = generate_m0_report()
        print(json.dumps(r, indent=2, ensure_ascii=False))
    else:
        validate_lite_on_parquet(args.sym, quick=True)
