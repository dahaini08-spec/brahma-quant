#!/usr/bin/env python3
"""
self_healing_watchdog.py — 梵天自愈watchdog（amux Self-Healing借鉴）
设计院自主决策 2026-09-02 苏摩111

功能：
  - 监控关键进程（ws_guardian、scan_fast）
  - 进程挂掉自动重启，不需要人工干预
  - 重启超过3次/小时触发告警推送
  - 本身作为cron每5min运行一次
"""
import sys, os, subprocess, json, time
from pathlib import Path

BASE = Path(__file__).parent.parent
STATE_FILE = BASE / 'data' / 'self_healing_state.json'

sys.path.insert(0, str(BASE / 'scripts'))

# 关键进程配置
PROCESSES = [
    {
        'name': 'ws_guardian',
        'match': 'scripts/ws_guardian.py',
        'start_cmd': ['python3', str(BASE / 'scripts' / 'ws_guardian.py')],
        'start_bg': True,
        'max_restarts_per_hour': 3,
    },
    {
        'name': 'scan_fast',
        'match': 'scripts/scan_fast.py',
        'start_cmd': ['python3', str(BASE / 'scripts' / 'scan_fast.py')],
        'start_bg': True,
        'max_restarts_per_hour': 3,
    },
]

def load_state():
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}

def save_state(state):
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))

def is_running(match):
    r = subprocess.run(['ps', 'aux'], capture_output=True, text=True)
    return match in r.stdout

def restart_process(proc):
    cmd = proc['start_cmd']
    if proc.get('start_bg'):
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    else:
        subprocess.run(cmd, timeout=10)

def run():
    state = load_state()
    now = time.time()
    alerts = []
    healed = []

    for proc in PROCESSES:
        name = proc['name']
        ps = state.get(name, {'restarts': [], 'last_alert': 0})

        if is_running(proc['match']):
            # 正常运行，清理1小时前的重启记录
            ps['restarts'] = [t for t in ps.get('restarts', []) if now - t < 3600]
            state[name] = ps
            continue

        # 进程不在运行
        restarts_1h = [t for t in ps.get('restarts', []) if now - t < 3600]

        if len(restarts_1h) >= proc['max_restarts_per_hour']:
            # 超过重启上限，告警不再重启
            if now - ps.get('last_alert', 0) > 3600:
                alerts.append('🔴 %s 1小时内重启%d次，超过上限，需人工介入' % (name, len(restarts_1h)))
                ps['last_alert'] = now
        else:
            # 自动重启
            try:
                restart_process(proc)
                restarts_1h.append(now)
                ps['restarts'] = restarts_1h
                healed.append('%s（第%d次）' % (name, len(restarts_1h)))
            except Exception as e:
                alerts.append('⚠️ %s 重启失败: %s' % (name, str(e)[:60]))

        state[name] = ps

    save_state(state)

    if not healed and not alerts:
        print('HEARTBEAT_OK')
        return

    lines = ['🔧 梵天自愈watchdog | %s' % time.strftime('%H:%M CST', time.localtime())]
    if healed:
        lines.append('✅ 已自愈: ' + ' / '.join(healed))
    if alerts:
        lines.append('\n'.join(alerts))

    msg = '\n'.join(lines)
    print(msg)

    try:
        from push_hub import _jarvis
        _jarvis(msg, dedup_key='self_healing_watchdog', dedup_ttl=1800)
    except Exception as e:
        print('[push_hub] %s' % e)

if __name__ == '__main__':
    run()
