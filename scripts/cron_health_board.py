#!/usr/bin/env python3
"""
cron_health_board.py — 梵天Cron健康看板（amux Kanban借鉴）
设计院自主决策 2026-09-02 苏摩111

功能：
  - 扫描所有cron任务状态（ok/error/idle）
  - 按分级输出：🔴ERROR / 🟡无超时保护 / 🟢正常
  - 统计error连续次数、运行时长异常
  - 推送到Jarvis（HEARTBEAT_OK静默，有问题才推）
"""
import sys, os, json, time, subprocess
from pathlib import Path

BASE = Path(__file__).parent.parent
CRON_JOBS = Path('/root/.openclaw/cron/jobs.json')
CRON_RUNS = Path('/root/.openclaw/cron/runs')

sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'scripts'))

def load_jobs():
    d = json.loads(CRON_JOBS.read_text())
    return d if isinstance(d, list) else d.get('jobs', [])

def get_last_run(job_id):
    f = CRON_RUNS / f'{job_id}.jsonl'
    if not f.exists():
        return None
    lines = f.read_text().strip().splitlines()
    if not lines:
        return None
    try:
        return json.loads(lines[-1])
    except Exception:
        return None

def get_recent_errors(job_id, n=3):
    f = CRON_RUNS / f'{job_id}.jsonl'
    if not f.exists():
        return 0
    lines = f.read_text().strip().splitlines()[-n:]
    count = 0
    for l in lines:
        try:
            d = json.loads(l)
            if d.get('status') == 'error':
                count += 1
        except Exception:
            pass
    return count

def format_ms(ms):
    if ms < 1000:
        return '%dms' % ms
    elif ms < 60000:
        return '%.1fs' % (ms/1000)
    else:
        return '%.1fmin' % (ms/60000)

def run():
    jobs = load_jobs()
    now = time.time()

    errors = []
    warnings = []
    ok_count = 0

    for j in jobs:
        name = j.get('name', '?')
        jid  = j.get('id', '')
        timeout = j.get('runTimeoutSeconds')
        last_run = get_last_run(jid)
        recent_err = get_recent_errors(jid, 3)

        status = last_run.get('status', 'unknown') if last_run else 'idle'
        duration = last_run.get('durationMs', 0) if last_run else 0
        error_msg = last_run.get('error', '') if last_run else ''

        # 连续error
        if recent_err >= 2:
            errors.append({
                'name': name,
                'consecutive_errors': recent_err,
                'duration': duration,
                'error': error_msg[:60],
                'timeout': timeout,
            })
        elif status == 'error' and recent_err >= 1:
            warnings.append({
                'name': name,
                'status': 'error×1',
                'duration': duration,
                'error': error_msg[:60],
                'timeout': timeout,
            })
        elif not timeout:
            # 无超时保护且运行时长>30s
            if duration > 30000:
                warnings.append({
                    'name': name,
                    'status': '无超时保护',
                    'duration': duration,
                    'error': '运行%.0fs无超时限制' % (duration/1000),
                    'timeout': None,
                })
        else:
            ok_count += 1

    # 决定是否推送
    if not errors and not warnings:
        print('HEARTBEAT_OK')
        return

    # 构建看板报告
    lines = ['📊 梵天Cron健康看板 | %s CST' % time.strftime('%m-%d %H:%M', time.localtime())]
    lines.append('━━━━━━━━━━━━━━━━━━━━')
    lines.append('✅正常: %d  ⚠️告警: %d  🔴异常: %d' % (ok_count, len(warnings), len(errors)))

    if errors:
        lines.append('\n🔴 连续ERROR（需关注）:')
        for e in errors[:5]:
            to_str = '超时%ds' % e['timeout'] if e['timeout'] else '无超时⚠️'
            lines.append('  %-28s 连续%d次 %s [%s]' % (
                e['name'][:28], e['consecutive_errors'],
                format_ms(e['duration']), to_str))
            if e['error'] and 'gateway restart' not in e['error']:
                lines.append('    └ %s' % e['error'])

    if warnings:
        lines.append('\n🟡 单次异常:')
        for w in warnings[:5]:
            lines.append('  %-28s %s %s' % (
                w['name'][:28], w['status'], format_ms(w['duration'])))

    lines.append('━━━━━━━━━━━━━━━━━━━━')

    msg = '\n'.join(lines)
    print(msg)

    # 推送
    try:
        from push_hub import _jarvis
        _jarvis(msg, dedup_key='cron_health_board', dedup_ttl=3600)
    except Exception as e:
        print('[push_hub] %s' % e)

if __name__ == '__main__':
    run()
