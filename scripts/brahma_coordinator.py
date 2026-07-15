#!/usr/bin/env python3
"""
brahma_coordinator.py — 梵天系统协调器
设计院 · 2026-07-15

health_check: 快速检查各子系统状态
"""
import sys, json, time
from pathlib import Path

BASE = Path(__file__).parent.parent

def health_check():
    now = time.time()
    results = {}

    checks = {
        'regime_state'    : ('data/regime_state.json',      3600*6),
        'signal_log'      : ('data/live_signal_log.jsonl',  3600*13),
        'oi_candidates'   : ('data/oi_candidates.json',     3600*3),
        'wuqu_positions'  : ('data/wuqu_positions.json',    86400),
        'sl_state'        : ('data/position_sl_state.json', 86400),
        'push_dedup'      : ('data/apm_push_dedup.json',    86400*2),
    }

    for name, (rel_path, max_age) in checks.items():
        fp = BASE / rel_path
        if not fp.exists():
            results[name] = {'status': 'MISSING', 'age_h': -1}
        else:
            age = now - fp.stat().st_mtime
            status = 'OK' if age < max_age else 'STALE'
            results[name] = {'status': status, 'age_h': round(age/3600, 1)}

    # cron jobs.json线程验证
    try:
        with open('/root/.openclaw/cron/jobs.json') as f:
            data = json.load(f)
        jobs = data if isinstance(data, list) else data.get('jobs', [])
        new_t = '019f5e0f-7d13-7392-a4e1-262e1cfc2dc2'
        wrong = [j['name'] for j in jobs
                 if 'thread:' in j.get('delivery',{}).get('to','')
                 and new_t not in j.get('delivery',{}).get('to','')
                 and '019f04e3' not in j.get('delivery',{}).get('to','')]
        results['cron_routes'] = {'status': 'OK' if not wrong else 'ERROR', 'wrong': wrong}
    except Exception as e:
        results['cron_routes'] = {'status': 'ERROR', 'err': str(e)}

    ok    = sum(1 for v in results.values() if v.get('status')=='OK')
    total = len(results)
    score = int(ok/total*100)

    print(f'[brahma_coordinator] health_check {time.strftime("%H:%M UTC")}')
    print(f'  综合健康分: {score}/100 ({ok}/{total}项正常)')
    for name, v in results.items():
        icon = '✅' if v['status']=='OK' else ('⚠️' if v['status']=='STALE' else '❌')
        print(f'  {icon} {name}: {v["status"]}')

    if score >= 90:
        print('HEARTBEAT_OK')
    else:
        issues = [n for n,v in results.items() if v['status']!='OK']
        print(f'⚠️ 需关注: {issues}')

    return score

if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'health_check':
        health_check()
    else:
        health_check()
