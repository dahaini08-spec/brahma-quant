#!/usr/bin/env python3
"""
引擎贡献率追踪 — 战略层学习闭环基础
每次信号结算(TP/SL)后，记录该信号各引擎得分贡献
为后续引擎权重自适应提供数据基础
"""
import json, time
from pathlib import Path
from datetime import datetime, timezone

ATTRIBUTION_LOG = Path('data/engine_attribution.jsonl')
ENGINE_PERF = Path('data/engine_performance.json')

def record_settlement(signal: dict, outcome: str):
    """
    signal: live_signal_log中的一条记录
    outcome: 'TP1' / 'TP2' / 'SL'
    """
    engines = signal.get('engine_scores') or signal.get('score_breakdown') or {}
    record = {
        'ts': time.time(),
        'date': datetime.now(timezone.utc).strftime('%Y-%m-%d'),
        'symbol': signal.get('symbol'),
        'direction': signal.get('signal_dir'),
        'regime': signal.get('regime'),
        'total_score': signal.get('score', 0),
        'outcome': outcome,
        'won': outcome in ('TP1', 'TP2'),
        'engines': engines,
    }
    with open(ATTRIBUTION_LOG, 'a') as f:
        f.write(json.dumps(record, ensure_ascii=False) + '\n')
    _update_engine_perf(engines, outcome in ('TP1', 'TP2'))

def _update_engine_perf(engines: dict, won: bool):
    """更新各引擎胜率统计"""
    perf = {}
    if ENGINE_PERF.exists():
        try: perf = json.loads(ENGINE_PERF.read_text())
        except: pass
    for eng, score in engines.items():
        if eng not in perf:
            perf[eng] = {'total': 0, 'wins': 0, 'total_score': 0}
        perf[eng]['total'] += 1
        perf[eng]['wins'] += 1 if won else 0
        perf[eng]['total_score'] += float(score or 0)
    ENGINE_PERF.write_text(json.dumps(perf, ensure_ascii=False, indent=2))

def print_engine_report():
    """打印引擎效能报告"""
    if not ENGINE_PERF.exists():
        print('无引擎数据（需要更多TP/SL结算记录）')
        return
    perf = json.loads(ENGINE_PERF.read_text())
    print(f'引擎效能报告（{len(perf)}个引擎）')
    ranked = sorted(perf.items(),
                    key=lambda x: x[1]['wins']/max(x[1]['total'],1), reverse=True)
    for eng, d in ranked[:15]:
        wr = d['wins']/d['total']*100 if d['total'] else 0
        avg = d['total_score']/d['total'] if d['total'] else 0
        print(f'  {eng}: WR={wr:.0f}% n={d["total"]} avg_score={avg:.1f}')

if __name__ == '__main__':
    print_engine_report()
