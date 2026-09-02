#!/usr/bin/env python3
"""
self_healing_watchdog.py — 梵天watchdog（修正版）
设计院封印 2026-09-02 苏摩111

修正：梵天全部是cron周期脚本，无常驻进程。
监控逻辑：检查关键cron任务的最近运行时间，超时则告警。
"""
import sys, os, json, time
from pathlib import Path

BASE       = Path(__file__).parent.parent
STATE_FILE = BASE / 'data' / 'self_healing_state.json'
CRON_JOBS  = Path('/root/.openclaw/cron/jobs.json')
CRON_RUNS  = Path('/root/.openclaw/cron/runs')

# 关键cron任务监控（超过max_idle_min分钟未运行 → 告警）
WATCHES = [
    {'name': 'position-guardian',    'max_idle_min': 10},
    {'name': 'rsi-structure-watcher','max_idle_min': 30},
    {'name': 'brahma-state-refresh', 'max_idle_min': 130},
]

def load_state():
    try: return json.loads(STATE_FILE.read_text())
    except: return {}

def save_state(s):
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(s, indent=2))

def get_last_run_ts(jid):
    f = CRON_RUNS / ('%s.jsonl' % jid)
    if not f.exists(): return 0
    for line in reversed(f.read_text().strip().splitlines()):
        try:
            d = json.loads(line)
            if d.get('startedAt'): return d['startedAt'] / 1000
        except: pass
    return 0

def run():
    now   = time.time()
    state = load_state()
    alerts = []

    try:
        jobs = json.loads(CRON_JOBS.read_text())
        lst  = jobs if isinstance(jobs, list) else jobs.get('jobs', [])
        name_to_id = {j['name']: j['id'] for j in lst}
    except Exception as e:
        print('HEARTBEAT_OK')
        return

    for w in WATCHES:
        name     = w['name']
        max_idle = w['max_idle_min'] * 60
        jid      = name_to_id.get(name)
        if not jid: continue

        last_ts  = get_last_run_ts(jid)
        idle_sec = now - last_ts if last_ts else 99999
        ps       = state.get(name, {'last_alert': 0})

        if idle_sec > max_idle:
            if now - ps.get('last_alert', 0) > 3600:
                alerts.append('⚠️ %s 已%dmin未运行（阈值%dmin）' % (
                    name, int(idle_sec//60), w['max_idle_min']))
                ps['last_alert'] = now
        else:
            ps['last_alert'] = 0

        state[name] = ps

    save_state(state)

    if not alerts:
        print('HEARTBEAT_OK')
        return

    msg = '🔧 梵天watchdog | %s\n%s' % (
        time.strftime('%H:%M CST', time.localtime()),
        '\n'.join(alerts))
    print(msg)

    try:
        sys.path.insert(0, str(BASE / 'scripts'))
        from push_hub import _jarvis
        _jarvis(msg, dedup_key='self_healing_watchdog', dedup_ttl=1800)
    except Exception:
        pass

if __name__ == '__main__':
    run()
