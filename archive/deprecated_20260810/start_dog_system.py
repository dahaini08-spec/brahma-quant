"""
犬系统 · 启动脚本
scripts/start_dog_system.py

启动 dog_commander 后台守护进程
[2026-07-23 设计院封印]
"""
import os
import sys
import subprocess
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

def is_running():
    r = subprocess.run(['pgrep', '-f', 'dog_commander'],
                       capture_output=True, text=True)
    return bool(r.stdout.strip())

def start():
    if is_running():
        print('[dog_system] ✅ 犬系统已在运行')
        return
    log_path = BASE_DIR / 'logs' / 'dog_commander.log'
    log_path.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        [sys.executable, '-m', 'brahma_brain.dog_commander'],
        cwd=str(BASE_DIR),
        stdout=open(log_path, 'a'),
        stderr=subprocess.STDOUT,
        start_new_session=True
    )
    time.sleep(2)
    if is_running():
        print(f'[dog_system] 🐕 犬系统启动成功 PID={proc.pid}')
    else:
        print('[dog_system] ❌ 启动失败，检查日志:', log_path)

def stop():
    subprocess.run(['pkill', '-f', 'dog_commander'], capture_output=True)
    print('[dog_system] 犬系统已停止')

def status():
    r = subprocess.run(['pgrep', '-f', 'dog_commander'],
                       capture_output=True, text=True)
    pids = r.stdout.strip()
    if pids:
        print(f'[dog_system] 🐕 运行中 PID={pids}')
    else:
        print('[dog_system] ⚠️ 未运行')

if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'start'
    {'start': start, 'stop': stop, 'status': status}.get(cmd, start)()
