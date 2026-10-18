#!/usr/bin/env python3
"""
trace_query.py — 梵天决策轨迹查询工具
[封印 2026-10-18 苏摩111] claude-quickstarts Trajectory Logging配套

用法:
  python3 scripts/trace_query.py --last 10
  python3 scripts/trace_query.py --sym ETH --last 20
  python3 scripts/trace_query.py --verdict ENTER
  python3 scripts/trace_query.py --date 2026-10-18
  python3 scripts/trace_query.py --explain        # 对比相邻决策差异
  python3 scripts/trace_query.py --gate           # 哪个Gate最常阻断
"""
import json, argparse, sys
from pathlib import Path
from collections import Counter
from datetime import datetime

TRACE_FILE = Path(__file__).parent.parent / 'data' / 'decision_trace.jsonl'

def load_traces():
    if not TRACE_FILE.exists():
        return []
    lines = TRACE_FILE.read_text(encoding='utf-8').splitlines()
    traces = []
    for l in lines:
        try: traces.append(json.loads(l))
        except: pass
    return traces

def fmt_trace(t, idx=None):
    pfx = f"[{idx}] " if idx is not None else ""
    verdict_icon = {'ENTER':'🟢','WATCH':'⚡','WAIT':'⚪'}.get(t.get('verdict','?'),'❓')
    blocked = t.get('blocked_by') or '通过'
    reasons = ' | '.join((t.get('reason') or [])[:2])
    return (
        f"{pfx}{verdict_icon} {t.get('ts','?')}  {t.get('sym','?')} @${t.get('price',0):,.0f}\n"
        f"   regime={t.get('regime','?')}  score={t.get('score',0):.0f}  RSI1H={t.get('rsi_1h',0):.1f}\n"
        f"   verdict={t.get('verdict','?')}  阻断={blocked}\n"
        f"   理由: {reasons or '无'}\n"
        f"   align={t.get('align_count',0)}/7  OI={t.get('oi_signal','?')}  CVD={t.get('cvd_1h',0):.1f}"
    )

def cmd_last(traces, n=10, sym=None):
    if sym:
        traces = [t for t in traces if t.get('sym','').upper() == sym.upper()]
    recent = traces[-n:]
    print(f"最近{len(recent)}条决策轨迹{f' [{sym}]' if sym else ''}:\n")
    for i, t in enumerate(reversed(recent), 1):
        print(fmt_trace(t, i))
        print()

def cmd_verdict(traces, verdict):
    matched = [t for t in traces if t.get('verdict','').upper() == verdict.upper()]
    print(f"verdict={verdict} 共{len(matched)}条:\n")
    for t in matched[-10:]:
        print(fmt_trace(t))
        print()

def cmd_date(traces, date_str):
    matched = [t for t in traces if t.get('ts','').startswith(date_str)]
    print(f"{date_str} 共{len(matched)}条决策:\n")
    for t in matched:
        print(fmt_trace(t))
        print()

def cmd_explain(traces):
    """对比相邻决策变化"""
    if len(traces) < 2:
        print("轨迹不足2条，无法对比")
        return
    print("决策变化分析（最近10对）:\n")
    recent = traces[-20:]
    for i in range(1, min(len(recent), 11)):
        prev = recent[i-1]; curr = recent[i]
        if prev.get('sym') != curr.get('sym'): continue
        v_prev = prev.get('verdict','?'); v_curr = curr.get('verdict','?')
        if v_prev == v_curr: continue
        # 方向变化，输出对比
        icon = '🔄' if v_prev != v_curr else '  '
        print(f"{icon} {prev.get('ts','?')} → {curr.get('ts','?')}  [{prev.get('sym')}]")
        print(f"   {v_prev} → {v_curr}")
        print(f"   RSI: {prev.get('rsi_1h',0):.1f} → {curr.get('rsi_1h',0):.1f}")
        print(f"   CVD: {prev.get('cvd_1h',0):.1f} → {curr.get('cvd_1h',0):.1f}")
        print(f"   阻断: {prev.get('blocked_by','无')} → {curr.get('blocked_by','无')}")
        print()

def cmd_gate(traces):
    """哪个Gate最常阻断"""
    blocked = [t.get('blocked_by') for t in traces if t.get('blocked_by')]
    counter = Counter(blocked)
    total = len(traces)
    wait_count = sum(1 for t in traces if t.get('verdict')=='WAIT')
    enter_count = sum(1 for t in traces if t.get('verdict')=='ENTER')
    watch_count = sum(1 for t in traces if t.get('verdict')=='WATCH')
    print(f"Gate阻断统计（共{total}条决策）:\n")
    print(f"  ENTER={enter_count} ({enter_count/max(total,1)*100:.0f}%)")
    print(f"  WATCH={watch_count} ({watch_count/max(total,1)*100:.0f}%)")
    print(f"  WAIT ={wait_count}  ({wait_count/max(total,1)*100:.0f}%)\n")
    print("最常阻断的Gate:")
    for gate, cnt in counter.most_common(8):
        pct = cnt / max(total, 1) * 100
        print(f"  {gate:<35} {cnt:>4}次 ({pct:.0f}%)")

def main():
    parser = argparse.ArgumentParser(description='梵天决策轨迹查询')
    parser.add_argument('--last',    type=int, default=10, help='最近N条')
    parser.add_argument('--sym',     type=str, default=None, help='标的 BTC/ETH')
    parser.add_argument('--verdict', type=str, default=None, help='ENTER/WATCH/WAIT')
    parser.add_argument('--date',    type=str, default=None, help='日期 2026-10-18')
    parser.add_argument('--explain', action='store_true', help='对比相邻决策差异')
    parser.add_argument('--gate',    action='store_true', help='Gate阻断统计')
    args = parser.parse_args()

    traces = load_traces()
    if not traces:
        print(f"轨迹文件不存在或为空: {TRACE_FILE}")
        sys.exit(0)

    print(f"=== 梵天决策轨迹  总计{len(traces)}条 ===\n")

    if args.gate:        cmd_gate(traces)
    elif args.explain:   cmd_explain(traces)
    elif args.verdict:   cmd_verdict(traces, args.verdict)
    elif args.date:      cmd_date(traces, args.date)
    else:                cmd_last(traces, args.last, args.sym)

if __name__ == '__main__':
    main()
