#!/usr/bin/env python3
"""
BrahmaOptimizer — 设计院自主参数优化引擎
苏摩111授权 2026-08-07

目标：解决评分系统逆序异常（高分WR=0%，低分WR=100%）
方法：网格搜索 score_gate × rsi4h_weight × grade_gate
数据：401条实盘信号日志 + 64条simfactory trades + 方仓v8铁证
目标函数：WR × EV × sqrt(n)（三维收益函数）
"""

import json
import os
import sys
import math
from typing import Dict, List, Tuple
from datetime import datetime

# 路径
DATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'data')
SIGNAL_LOG = os.path.join(DATA_DIR, 'live_signal_log.jsonl')
SIMFACTORY_TRADES = os.path.join(DATA_DIR, 'simfactory_trades.jsonl')
WR_MATRIX_V8 = os.path.join(DATA_DIR, 'wr_matrix_v8_6y5.json')
OUTPUT_PATH = os.path.join(DATA_DIR, 'brahma_optimizer_results.json')


def load_signals() -> List[dict]:
    """加载实盘信号日志"""
    sigs = []
    if not os.path.exists(SIGNAL_LOG):
        return sigs
    with open(SIGNAL_LOG) as f:
        for line in f:
            try:
                s = json.loads(line.strip())
                if isinstance(s, dict):
                    sigs.append(s)
            except Exception:
                pass
    return sigs


def load_simfactory_trades() -> List[dict]:
    """加载simfactory模拟交易结果"""
    trades = []
    if not os.path.exists(SIMFACTORY_TRADES):
        return trades
    with open(SIMFACTORY_TRADES) as f:
        for line in f:
            try:
                t = json.loads(line.strip())
                if isinstance(t, dict):
                    trades.append(t)
            except Exception:
                pass
    return trades


def load_wr_matrix() -> dict:
    """加载方仓v8铁证矩阵"""
    if not os.path.exists(WR_MATRIX_V8):
        return {}
    with open(WR_MATRIX_V8) as f:
        return json.load(f)


def get_rsi4h_bucket(rsi4h: float) -> str:
    """RSI_4H分桶（对应方仓铁证）"""
    if rsi4h is None:
        return None
    if rsi4h < 50:
        return 'RSI_0_40' if rsi4h < 40 else 'RSI_40_50'
    elif rsi4h < 55:
        return 'RSI_50_55'
    elif rsi4h < 60:
        return 'RSI_55_60'
    elif rsi4h < 70:
        return 'RSI_60_70'
    else:
        return 'RSI_70_100'


def simulate_with_params(
    trades: List[dict],
    signals: List[dict],
    wr_matrix: dict,
    score_gate: float,
    rsi4h_bonus: float,    # RSI_4H<55时额外bonus（正数=奖励低RSI入场）
    grade_gate: float,
) -> Dict:
    """
    模拟给定参数下的系统表现。
    
    参数调整逻辑：
    - score_gate: 执行门槛（原138，搜索范围125~160）
    - rsi4h_bonus: RSI_4H<55时给score加bonus（鼓励低位入场）
    - grade_gate: grade门槛（原80，搜索范围70~90）
    
    返回：{'wr': float, 'ev': float, 'n': int, 'objective': float}
    """
    # 基于simfactory trades过滤
    eligible_trades = []
    for t in trades:
        if t.get('regime') != 'BULL_TREND' or t.get('direction') != 'LONG':
            continue
        
        raw_score = t.get('score', 0) or 0
        rsi4h = t.get('rsi_4h', None)
        grade = t.get('grade', 0) or 0
        
        # 调整score：RSI_4H<55时加bonus（奖励低位入场）
        adjusted_score = raw_score
        if rsi4h is not None and rsi4h < 55:
            adjusted_score += rsi4h_bonus
        elif rsi4h is not None and rsi4h >= 60:
            # RSI_4H>=60时扣分（惩罚追高）
            adjusted_score -= (rsi4h - 60) * 0.5
        
        # 门控检查
        if adjusted_score < score_gate:
            continue
        if grade < grade_gate:
            continue
        
        eligible_trades.append(t)
    
    if len(eligible_trades) < 3:
        return {'wr': 0, 'ev': 0, 'n': 0, 'objective': 0}
    
    wins = sum(1 for t in eligible_trades if (t.get('pnl_pct', 0) or 0) > 0)
    pnl_list = [t.get('pnl_pct', 0) or 0 for t in eligible_trades]
    n = len(eligible_trades)
    wr = wins / n
    ev = sum(pnl_list) / n
    
    # 目标函数：WR × EV × sqrt(n)
    # EV可能为负，用max(0, ev)防止负目标值遮蔽好WR
    objective = wr * max(0, ev) * math.sqrt(n)
    
    # 次级目标：如果EV为负，惩罚
    if ev < 0:
        objective = -abs(wr - 0.5) * abs(ev) * math.sqrt(n)
    
    return {
        'wr': round(wr * 100, 2),
        'ev': round(ev, 4),
        'n': n,
        'objective': round(objective, 6),
        'wins': wins,
    }


def grid_search(trades: List[dict], signals: List[dict], wr_matrix: dict) -> List[dict]:
    """
    网格搜索最优参数组合
    搜索空间：
    - score_gate: [120, 125, 130, 135, 138, 140, 145, 148, 150, 155]
    - rsi4h_bonus: [0, 3, 5, 8, 10, 12, 15]（RSI_4H<55时的score加成）
    - grade_gate: [70, 72, 75, 78, 80, 82, 85]
    """
    score_gates = [110, 115, 120, 125, 130, 135, 138, 140, 145, 148, 150, 155]
    rsi4h_bonuses = [0, 3, 5, 8, 10, 12, 15]
    grade_gates = [70, 72, 75, 78, 80, 82, 85]
    
    results = []
    total = len(score_gates) * len(rsi4h_bonuses) * len(grade_gates)
    
    print(f"🔍 搜索空间: {total}个组合")
    
    for sg in score_gates:
        for rb in rsi4h_bonuses:
            for gg in grade_gates:
                r = simulate_with_params(trades, signals, wr_matrix, sg, rb, gg)
                if r['n'] >= 3:
                    results.append({
                        'score_gate': sg,
                        'rsi4h_bonus': rb,
                        'grade_gate': gg,
                        **r
                    })
    
    # 按目标函数排序
    results.sort(key=lambda x: x['objective'], reverse=True)
    return results


def analyze_current_baseline(trades: List[dict]) -> dict:
    """当前基准（score_gate=138, rsi4h_bonus=0, grade_gate=80）"""
    bl_trades = [t for t in trades 
                 if t.get('regime')=='BULL_TREND' 
                 and t.get('direction')=='LONG'
                 and (t.get('score',0) or 0) >= 138
                 and (t.get('grade',0) or 0) >= 80]
    if not bl_trades:
        return {'wr': 0, 'ev': 0, 'n': 0}
    wins = sum(1 for t in bl_trades if (t.get('pnl_pct',0) or 0) > 0)
    n = len(bl_trades)
    pnl_list = [t.get('pnl_pct',0) or 0 for t in bl_trades]
    return {
        'wr': round(wins/n*100, 2),
        'ev': round(sum(pnl_list)/n, 4),
        'n': n,
    }


def analyze_score_bands(trades: List[dict]) -> dict:
    """分析各score段的WR分布（诊断逆序异常）"""
    bl = [t for t in trades if t.get('regime')=='BULL_TREND' and t.get('direction')=='LONG']
    bands = {}
    for t in bl:
        s = t.get('score', 0) or 0
        b = f"{int(s//10)*10}-{int(s//10)*10+9}"
        if b not in bands:
            bands[b] = {'wins': 0, 'total': 0, 'pnl': []}
        bands[b]['total'] += 1
        pnl = t.get('pnl_pct', 0) or 0
        if pnl > 0:
            bands[b]['wins'] += 1
        bands[b]['pnl'].append(pnl)
    
    result = {}
    for band in sorted(bands.keys()):
        d = bands[band]
        wr = d['wins']/d['total']*100 if d['total'] else 0
        avg_pnl = sum(d['pnl'])/len(d['pnl']) if d['pnl'] else 0
        result[band] = {'wr': round(wr,1), 'n': d['total'], 'avg_pnl': round(avg_pnl,3)}
    return result


def run_optimizer():
    print("=" * 70)
    print("🏛️ BrahmaOptimizer — 设计院参数优化引擎")
    print(f"时间: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 70)
    
    # 加载数据
    signals = load_signals()
    trades = load_simfactory_trades()
    wr_matrix = load_wr_matrix()
    
    print(f"\n📊 数据加载:")
    print(f"  实盘信号日志: {len(signals)}条")
    print(f"  simfactory trades: {len(trades)}条")
    print(f"  方仓v8铁证: {'已加载' if wr_matrix else '缺失'}")
    
    bull_long_trades = [t for t in trades 
                        if t.get('regime')=='BULL_TREND' 
                        and t.get('direction')=='LONG']
    print(f"  BULL_TREND LONG trades: {len(bull_long_trades)}条")
    
    # 基准分析
    print("\n📋 score段逆序分析（诊断追高偏差）:")
    bands = analyze_score_bands(trades)
    for band, d in bands.items():
        marker = " ❌死亡区" if d['wr'] == 0 and d['n'] >= 3 else (" ✅" if d['wr'] >= 80 else "")
        print(f"  score {band}: WR={d['wr']}% n={d['n']} avgPnL={d['avg_pnl']}%{marker}")
    
    # 当前基准
    baseline = analyze_current_baseline(trades)
    print(f"\n📌 当前基准 (score≥138, grade≥80):")
    print(f"  WR={baseline['wr']}% EV={baseline['ev']}% n={baseline['n']}")
    
    # 网格搜索
    print("\n🔍 开始网格搜索...")
    results = grid_search(trades, signals, wr_matrix)
    
    print(f"\n✅ 搜索完成，有效组合: {len(results)}个")
    
    if not results:
        print("❌ 无有效组合（数据量不足）")
        return
    
    # Top 10
    print("\n🏆 Top 10 参数组合 (按目标函数排序):")
    print(f"{'排名':<4} {'score_gate':<11} {'rsi4h_bonus':<12} {'grade_gate':<11} {'WR%':<8} {'EV%':<8} {'n':<5} {'目标值':<10}")
    print("-" * 75)
    for i, r in enumerate(results[:10]):
        print(f"#{i+1:<3} {r['score_gate']:<11} {r['rsi4h_bonus']:<12} {r['grade_gate']:<11} "
              f"{r['wr']:<8} {r['ev']:<8} {r['n']:<5} {r['objective']:<10}")
    
    best = results[0]
    print(f"\n🥇 最优参数组合:")
    print(f"  score_gate:   {best['score_gate']}  (原 138)")
    print(f"  rsi4h_bonus:  +{best['rsi4h_bonus']}  (RSI_4H<55时score加成，原 0)")
    print(f"  grade_gate:   {best['grade_gate']}  (原 80)")
    print(f"  预期WR:       {best['wr']}%  (原 ~37%)")
    print(f"  预期EV:       {best['ev']}%")
    print(f"  样本量:       {best['n']}条")
    
    # 与当前基准对比
    if baseline['n'] > 0:
        wr_delta = best['wr'] - baseline['wr']
        ev_delta = best['ev'] - baseline['ev']
        print(f"\n📈 vs 当前基准:")
        print(f"  WR改善: {baseline['wr']}% → {best['wr']}% ({'+' if wr_delta>0 else ''}{wr_delta:.1f}%)")
        print(f"  EV改善: {baseline['ev']}% → {best['ev']}% ({'+' if ev_delta>0 else ''}{ev_delta:.4f}%)")
    
    # 方仓铁证交叉验证
    print("\n📚 方仓铁证交叉验证 (6.5年历史数据):")
    for sym in ['BTC', 'ETH']:
        if sym in wr_matrix:
            bt_long = wr_matrix[sym].get('BULL_TREND', {}).get('LONG', {})
            print(f"  {sym} BULL_TREND LONG:")
            for bucket_key in ['RSI_50_55', 'RSI_55_60', 'RSI_60_70']:
                bdata = bt_long.get(bucket_key, {})
                if bdata.get('n', 0) > 0:
                    print(f"    {bucket_key}: WR={bdata['wr']}% EV={bdata['ev']} n={bdata['n']}")
    
    # 设计院封印建议
    print("\n" + "=" * 70)
    print("🏛️ 设计院封印建议")
    print("=" * 70)
    
    if best['wr'] >= 50 and best['n'] >= 5:
        print(f"\n✅ 建议封印新参数：")
        print(f"  brahma_engine.py 修改:")
        print(f"    SCORE_GATE_BULL_LONG = {best['score_gate']}  # 原 138")
        print(f"    RSI4H_BONUS_BELOW_55 = {best['rsi4h_bonus']}  # 原 0（新增）")
        print(f"    GRADE_GATE = {best['grade_gate']}  # 原 80")
        print(f"\n  预期效果: WR {baseline.get('wr','?')}% → {best['wr']}%")
        print(f"  样本量:   n={best['n']} 条（simfactory验证）")
        print(f"  等待苏摩111批准后写入 brahma_brain")
    elif best['wr'] >= 45:
        print(f"\n⚠️ 参数改善有限 (WR={best['wr']}%<50%)，建议:")
        print(f"  1. 等待更多simfactory数据积累（当前n={len(bull_long_trades)}条）")
        print(f"  2. 考虑引入 rsi4h_gate 作为强制门控而非bonus加减分")
        print(f"  3. 暂时使用RSI_4H<55作为硬门控（方仓铁证最优区间）")
    else:
        print(f"\n❌ 当前数据量不足以得出高置信度结论")
        print(f"  需要更多trades数据（当前n={len(bull_long_trades)}条）")
        print(f"  建议继续积累后重跑优化器")
    
    # 保存结果
    output = {
        'timestamp': datetime.utcnow().isoformat(),
        'baseline': baseline,
        'score_bands': bands,
        'top10': results[:10],
        'best': best,
        'total_valid_combinations': len(results),
        'data_stats': {
            'signals': len(signals),
            'trades': len(trades),
            'bull_long_trades': len(bull_long_trades),
        }
    }
    with open(OUTPUT_PATH, 'w') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\n💾 结果已保存: {OUTPUT_PATH}")
    
    return output


if __name__ == '__main__':
    result = run_optimizer()
