#!/usr/bin/env python3
"""
brahma_auto_mode.py — 梵天Auto自主学习层
[封印 2026-10-18 苏摩111] claude-quickstarts Auto Mode落地

功能:
  1. 读取 decision_trace.jsonl（轨迹记录）
  2. 分析哪些信号组合历史成功率最高
  3. 自动建议Gate5豁免阈值调整（±5分）
  4. 苏摩111批准后才写入生效（半自动）

设计原则:
  - 不自动修改核心参数，只"建议+等苏摩111"
  - 需≥50条有效轨迹才开始学习
  - 每条建议附带置信区间和样本量
"""
import json, time
from pathlib import Path
from collections import defaultdict

BASE   = Path(__file__).parent.parent
TRACE  = BASE / 'data' / 'decision_trace.jsonl'
LEARN  = BASE / 'data' / 'auto_learn_state.json'

# ── 学习引擎 ──────────────────────────────────────────────────────────────

def load_traces() -> list:
    if not TRACE.exists():
        return []
    lines = TRACE.read_text(encoding='utf-8').splitlines()
    return [json.loads(l) for l in lines if l.strip()]

def analyze_gate_performance(traces: list) -> dict:
    """分析每个Gate的阻断率和历史效果"""
    gate_stats = defaultdict(lambda: {'blocked': 0, 'passed': 0})
    for t in traces:
        blocked = t.get('blocked_by')
        verdict = t.get('verdict', 'WAIT')
        if blocked:
            gate_stats[blocked]['blocked'] += 1
        else:
            gate_stats['ALL_PASS']['passed'] += 1
    return dict(gate_stats)

def analyze_rsi_performance(traces: list) -> dict:
    """RSI区间与WATCH/ENTER成功率分析"""
    buckets = defaultdict(lambda: {'watch': 0, 'enter': 0, 'wait': 0})
    for t in traces:
        rsi = t.get('rsi_1h', 0)
        v   = t.get('verdict', 'WAIT')
        bucket = f'{int(rsi//10)*10}~{int(rsi//10)*10+10}'
        buckets[bucket][v.lower()] += 1
    return dict(buckets)

def suggest_threshold_adjustments(traces: list) -> list:
    """
    基于历史轨迹，建议Gate5豁免阈值调整
    只有置信度>70%且样本n≥10才输出建议
    """
    suggestions = []
    n = len(traces)

    if n < 50:
        return [{'type': 'insufficient_data', 'msg': f'当前{n}条轨迹，需≥50条才开始学习', 'n': n}]

    # 分析RSI豁免区[55~75]的实际表现
    rsi_exempt = [t for t in traces if 55 <= t.get('rsi_1h', 0) <= 75 and t.get('regime') == 'CHOP_MID']
    if rsi_exempt:
        watch_rate = sum(1 for t in rsi_exempt if t.get('verdict') == 'WATCH') / len(rsi_exempt)
        if watch_rate > 0.8 and len(rsi_exempt) >= 10:
            suggestions.append({
                'type': 'gate5_rsi_confirm',
                'msg': f'RSI[55~75]豁免区WATCH率={watch_rate:.0%} n={len(rsi_exempt)}，当前阈值有效，维持',
                'confidence': watch_rate,
                'n': len(rsi_exempt),
                'action': 'maintain'
            })
        elif watch_rate < 0.5 and len(rsi_exempt) >= 10:
            suggestions.append({
                'type': 'gate5_rsi_tighten',
                'msg': f'RSI[55~75]豁免区WATCH率仅{watch_rate:.0%} n={len(rsi_exempt)}，建议收窄至[58~72]',
                'confidence': 1 - watch_rate,
                'n': len(rsi_exempt),
                'action': 'tighten',
                'new_range': [58, 72],
                'requires': 'soma111'
            })

    # 分析最常阻断Gate
    all_blocked = [t.get('blocked_by') for t in traces if t.get('blocked_by')]
    if all_blocked:
        from collections import Counter
        top = Counter(all_blocked).most_common(3)
        for gate, cnt in top:
            rate = cnt / n
            if rate > 0.4:
                suggestions.append({
                    'type': 'gate_frequency',
                    'msg': f'{gate} 阻断率={rate:.0%} ({cnt}/{n}次)，是最大拦截门',
                    'gate': gate,
                    'rate': rate,
                    'n': cnt,
                    'action': 'review'
                })

    return suggestions

def run_auto_learn(verbose: bool = True) -> dict:
    """主入口：运行自主学习分析"""
    traces = load_traces()
    n = len(traces)
    now_str = time.strftime('%Y-%m-%dT%H:%MZ', time.gmtime())

    result = {
        'ts': now_str,
        'n_traces': n,
        'gate_stats': analyze_gate_performance(traces),
        'rsi_buckets': analyze_rsi_performance(traces),
        'suggestions': suggest_threshold_adjustments(traces),
    }

    # 保存学习状态
    json.dump(result, open(LEARN, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)

    if verbose:
        print(f"🧠 梵天Auto学习层  {now_str}")
        print(f"   轨迹样本: {n}条")
        if n < 50:
            print(f"   ⏳ 积累中（需≥50条开始学习，当前{n}条）")
        else:
            print(f"   Gate阻断统计: {result['gate_stats']}")
        print(f"\n   建议列表:")
        for s in result['suggestions']:
            icon = '✅' if s.get('action') == 'maintain' else ('⚠️' if s.get('requires') == 'soma111' else 'ℹ️')
            print(f"   {icon} {s.get('msg','')}")
            if s.get('requires') == 'soma111':
                print(f"      → 需要苏摩111批准后才生效")

    return result

if __name__ == '__main__':
    run_auto_learn(verbose=True)
