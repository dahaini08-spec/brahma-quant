#!/usr/bin/env python3
"""
dag_visualizer.py — DAG稀疏激活可视化
[9.19 设计院封印 苏摩111] 让维度激活/sleep状态可审计

从dim_trace.jsonl读取最近的记录，生成ASCII表格展示：
  - 每个体制×方向的active/sleep维度
  - position_mult
  - score变化趋势

用法:
  python3 scripts/dag_visualizer.py              # 最近10条
  python3 scripts/dag_visualizer.py --all          # 全部
  python3 scripts/dag_visualizer.py --regime CHOP_MID  # 按体制过滤
"""
import json, sys, os
from pathlib import Path
from collections import defaultdict

BASE = Path(__file__).parent.parent
TRACE = BASE / 'data' / 'dim_trace.jsonl'
CONFIG = BASE / 'data' / 'scoring_config.json'

def load_config():
    if not CONFIG.exists():
        return {}
    return json.loads(CONFIG.read_text())

def load_traces(limit=10, regime_filter=None):
    if not TRACE.exists():
        return []
    lines = TRACE.read_text().strip().split('\n')
    traces = []
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            d = json.loads(line)
            if regime_filter:
                inp = d.get('input', {})
                if inp.get('regime') != regime_filter:
                    continue
            traces.append(d)
            if len(traces) >= limit:
                break
        except:
            continue
    return traces

def print_config_table(cfg):
    """打印6体制×2方向的配置表"""
    regimes = ['BEAR_TREND', 'BEAR_EARLY', 'CHOP_MID', 'BULL_TREND', 'BEAR_RECOVERY', 'BULL_EARLY']
    dirs = ['LONG', 'SHORT']

    print('\n┌─────────────────┬────────┬────────┬────────────┬────────────┐')
    print('│ 体制:方向       │ active │ sleep  │ pos_mult   │ 状态       │')
    print('├─────────────────┼────────┼────────┼────────────┼────────────┤')

    for regime in regimes:
        for direction in dirs:
            entry = cfg.get(regime, {}).get(direction, {})
            active = entry.get('active_dims', [])
            sleep = entry.get('sleep_dims', [])
            pos_mult = entry.get('position_mult', 1.0)

            if active == 'all':
                active_str = 'ALL(23)'
                sleep_str = '0'
                status = '✅ 全激活'
            elif isinstance(active, list) and len(active) == 3 and ''.join(active) == 'all':
                active_str = 'BUG!'
                sleep_str = '?'
                status = '❌ 字符串拆分'
            else:
                active_str = str(len(active)) if isinstance(active, list) else str(active)
                sleep_str = str(len(sleep)) if isinstance(sleep, list) else str(sleep)
                status = '⚠️ 稀疏'

            key = f'{regime}:{direction}'
            print(f'│ {key:15s} │ {active_str:>6} │ {sleep_str:>6} │ {pos_mult:>10.2f} │ {status:10s} │')

    print('└─────────────────┴────────┴────────┴────────────┴────────────┘')

def print_trace_table(traces):
    """打印最近trace记录"""
    if not traces:
        print('\n（无trace记录）')
        return

    print('\n=== 最近DAG执行记录 ===')
    print(f'{"时间":>12} │ {"标的":>6} │ {"体制":>12} │ {"方向":>5} │ {"score":>6} │ {"active":>6} │ {"sleep":>5} │')
    print('─' * 80)

    for t in reversed(traces):
        inp = t.get('input', {})
        out = t.get('output', {})
        ts = t.get('ts', 0)

        from datetime import datetime
        dt = datetime.utcfromtimestamp(ts).strftime('%m-%d %H:%M') if ts else '?'

        sym = inp.get('symbol', '?')[:6]
        regime = inp.get('regime', '?')[:12]
        direction = inp.get('dir', '?')[:5]
        score = out.get('score', inp.get('raw_score', '?'))
        dims = t.get('dims', {})

        nactive = sum(1 for v in dims.values() if v.get('status') == 'active')
        nsleep = sum(1 for v in dims.values() if v.get('status') == 'sleep')

        if isinstance(score, (int, float)):
            score_str = f'{score:.1f}'
        else:
            score_str = str(score)

        print(f'{dt:>12} │ {sym:>6} │ {regime:>12} │ {direction:>5} │ {score_str:>6} │ {nactive:>6} │ {nsleep:>5} │')

def main():
    regime_filter = None
    limit = 10
    show_all = '--all' in sys.argv
    for arg in sys.argv[1:]:
        if arg == '--all':
            limit = 9999
        elif arg.startswith('--regime'):
            regime_filter = arg.split('=')[1] if '=' in arg else None

    cfg = load_config()
    print_config_table(cfg)

    traces = load_traces(limit=limit, regime_filter=regime_filter)
    print_trace_table(traces)

if __name__ == '__main__':
    main()
