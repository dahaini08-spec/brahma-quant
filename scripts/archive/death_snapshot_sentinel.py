#!/usr/bin/env python3
"""
death_snapshot_sentinel.py — 死亡快照哨兵 [P1封印 2026-09-28 苏摩111]

背景: 02:06-02:08进程群灭无OOM无错误记录，根因未明。本哨兵在死亡发生瞬间抓取系统快照留证。

机制:
1. 每分钟被independent_watchdog调用（独立于supercronic，supercronic死了它还活着）
2. 检测: 对比上次快照的supercronic PID——PID变化或进程消失=死亡事件
3. 死亡瞬间抓快照: /proc/meminfo关键值/load/uptime/ps全量截断/磁盘水位/打开fd计数/当前cron日志尾部
4. 双写: data/death_snapshots/*.json（详细留证） + events流circuit_breaker事件（不可变证据）
5. 状态文件 data/death_sentinel_state.json 记录last_seen_pid

接入位置: scripts/independent_watchdog.sh（每60s调用一次）
消费方: 设计院审计 / daily_review / 苏摩事后分析
"""
# [2026-10-05 P1 苏摩111] 统一路径管理，替代裸 sys.path.insert
try:
    import brahma_path_setup  # noqa
except ImportError:
    pass  # 兜底：原有 sys.path.insert 仍保留

import json
import os
import re
import subprocess
import time
from pathlib import Path

DATA = Path(__file__).parent.parent / 'data'
SNAP_DIR = DATA / 'death_snapshots'
STATE_F = DATA / 'death_sentinel_state.json'

# 关键进程名与判定：全部消失才叫"群灭"，单个消失叫"部分死亡"
MONITORED = ['supercronic', 'cvd_ws_collector']


def _pgrep_all(pattern: str) -> list:
    try:
        out = subprocess.run(['pgrep', '-f', pattern], capture_output=True, text=True, timeout=5)
        return [int(x) for x in out.stdout.split() if x.strip().isdigit()]
    except Exception:
        return []


def _meminfo() -> dict:
    out = {}
    try:
        for line in Path('/proc/meminfo').read_text().splitlines()[:8]:
            k, v = line.split(':', 1)
            out[k.strip()] = v.strip()
    except Exception:
        pass
    return out


def _loadavg() -> str:
    try:
        return Path('/proc/loadavg').read_text().strip()
    except Exception:
        return ''


def _uptime_s() -> float:
    try:
        return float(Path('/proc/uptime').read_text().split()[0])
    except Exception:
        return 0.0


def _disk() -> dict:
    try:
        st = os.statvfs(str(Path(__file__).parent))
        return {'free_gb': round(st.f_bavail * st.f_frsize / 1e9, 2), 'total_gb': round(st.f_blocks * st.f_frsize / 1e9, 1)}
    except Exception:
        return {}


def _ps_snapshot() -> list:
    """全进程快照（截断防爆炸）——死亡瞬间谁还在谁先死的证据。"""
    try:
        out = subprocess.run(['ps', '-eo', 'pid,ppid,etime,rss,comm,args'], capture_output=True, text=True, timeout=8)
        lines = out.stdout.splitlines()
        keep = []
        for ln in lines:
            if any(k in ln for k in ('python', 'supercronic', 'bash scripts', 'node', 'watchdog')):
                keep.append(ln[:180])
        return keep[:120]
    except Exception:
        return []


def _dmesg_tail() -> list:
    """尽力而为: 容器内可能无权限。"""
    for cmd in (['dmesg', '-T', '--level=emerg,alert,crit,err', '|', 'tail', '-15'], ):
        pass
    try:
        out = subprocess.run('dmesg -T --level=emerg,alert,crit,err 2>/dev/null | tail -15 || true',
                             shell=True, capture_output=True, text=True, timeout=8)
        return [ln[:160] for ln in out.stdout.splitlines() if ln.strip()]
    except Exception:
        return []


def _oom_signals() -> list:
    """间接OOM证据: 宿主meminfo不可见时看cgroup内存事件。"""
    sigs = []
    for cg in Path('/sys/fs/cgroup').glob('memory.events*'):
        try:
            sigs.append(f'{cg.name}: {cg.read_text().strip()[:120]}')
        except Exception:
            pass
    # cgroup v1
    for f in Path('/sys/fs/cgroup/memory').rglob('memory.oom_control'):
        try:
            sigs.append(f'v1 {f.parent.name}: {f.read_text().strip()[:80]}')
        except Exception:
            pass
    return sigs[:10]


def check_once() -> dict:
    """单次检测。返回{'status': ok|death_detected|first_run, ...}"""
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    now_pid = {}
    for name in MONITORED:
        pids = _pgrep_all(name)
        now_pid[name] = pids[0] if pids else None

    state = {}
    if STATE_F.exists():
        try:
            state = json.loads(STATE_F.read_text())
        except Exception:
            state = {}

    last_pid = state.get('last_pid', {})
    ts = int(time.time())

    # 首次运行: 只记录基线
    if not state:
        STATE_F.write_text(json.dumps({'last_pid': now_pid, 'ts': ts, 'runs': 1}, ensure_ascii=False))
        return {'status': 'first_run', 'pid': now_pid}

    state['runs'] = int(state.get('runs', 0)) + 1

    death = None
    partial = []
    for name in MONITORED:
        was = last_pid.get(name)
        now = now_pid.get(name)
        if was and not now:
            partial.append(f'{name}消失(前PID {was})')
        elif was and now and was != now:
            partial.append(f'{name}PID更换 {was}→{now} (进程重启证据)')
        elif not was and not now and name == 'supercronic':
            partial.append(f'{name}从未在位(基线异常)')

    if partial:
        death = partial  # 全部视为事件（PID更换同样是重启证据）

    result = {'status': 'ok', 'pid': now_pid, 'ts': ts}
    if death:
        snapshot = {
            'ts': ts,
            'ts_iso': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(ts)),
            'events': death,
            'last_pid': last_pid,
            'now_pid': now_pid,
            'meminfo': _meminfo(),
            'loadavg': _loadavg(),
            'uptime_s': _uptime_s(),
            'disk': _disk(),
            'ps_alive': _ps_snapshot(),
            'dmesg_tail': _dmesg_tail(),
            'oom_signals': _oom_signals(),
        }
        # 双写1: 快照文件
        snap_f = SNAP_DIR / f'death_{ts}.json'
        snap_f.write_text(json.dumps(snapshot, ensure_ascii=False, indent=1))
        # 双写2: 事件流不可变证据
        try:
            import sys
            sys.path.insert(0, str(Path(__file__).parent))
            import brahma_events
            brahma_events.append('circuit_breaker', {
                'kind': 'process_death_detected',
                'events': death,
                'snapshot_file': snap_f.name,
                'memfree': snapshot['meminfo'].get('MemAvailable', ''),
                'oom_signals': len(snapshot['oom_signals']),
            })
        except Exception:
            pass
        result = {'status': 'death_detected', 'events': death, 'snapshot': str(snap_f)}

    STATE_F.write_text(json.dumps({'last_pid': now_pid, 'ts': ts, 'runs': state['runs'],
                                   'deaths': int(state.get('deaths', 0)) + (1 if death else 0),
                                   'last_death_at': ts if death else state.get('last_death_at')}, ensure_ascii=False))
    return result


if __name__ == '__main__':
    print(json.dumps(check_once(), ensure_ascii=False))
