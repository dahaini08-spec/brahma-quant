#!/usr/bin/env python3
"""
cron_signal_queue_trim.py — signal_queue.jsonl 每日裁剪（保留最新500条）
[2.0封印 2026-10-01 苏摩111]

替代原crontab inline python3 -c代码块，使用safe_io flock防并发损坏。
接入位置：brahma_crontab.txt 6 3 * * *

功能：
  - 读取 data/signal_queue.jsonl
  - 保留最新 MAX_LINES 条（默认500）
  - 用 flock 原子写回，防止与 paper_engine/auto_executor 并发损坏
"""
import sys, json, time, fcntl
from pathlib import Path

BASE = Path(__file__).parent.parent
QUEUE_PATH = BASE / 'data' / 'signal_queue.jsonl'
MAX_LINES = 500

if not QUEUE_PATH.exists():
    print(f'[cron_signal_queue_trim] {QUEUE_PATH} not found, skip')
    sys.exit(0)

# 读取所有行（跳过损坏行）
lines = []
with open(QUEUE_PATH, 'r', encoding='utf-8') as f:
    fcntl.flock(f.fileno(), fcntl.LOCK_SH)
    try:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                json.loads(line)   # 校验JSON有效
                lines.append(line)
            except json.JSONDecodeError:
                pass  # 损坏行丢弃
    finally:
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)

before = len(lines)
if before <= MAX_LINES:
    print(f'[cron_signal_queue_trim] {before} lines ≤ {MAX_LINES}, no trim needed')
    sys.exit(0)

kept = lines[-MAX_LINES:]

# 原子写回（排他锁）
with open(QUEUE_PATH, 'w', encoding='utf-8') as f:
    fcntl.flock(f.fileno(), fcntl.LOCK_EX)
    try:
        f.write('\n'.join(kept) + '\n')
    finally:
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)

print(f'[cron_signal_queue_trim] trimmed {before} → {len(kept)} lines at {time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}')
