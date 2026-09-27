#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
paper_lock.py — B线纸面盘单实例锁 [2026-09-27 苏摩111 顶层修复]
根因：9.26 B线账本闭环断裂——多写者（executor/tp_monitor/engine/reset）无锁竞态，
07:19归档与07:21-22 executor写盘竞态导致两笔BTC单记录被清空、SL穿仓静默。

设计决策：
- 拿不到锁 → SKIP本轮（纸面盘不排队，宁可丢一轮也不竞态写坏）
- flock(LOCK_EX|LOCK_NB) 非阻塞
- 锁文件 .paper_lock 在 data/ 下
- 用法：
    with paper_lock('paper_executor') as ok:
        if not ok: log SKIP; return
        ... 正常读写 paper_* 文件
"""
import fcntl
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / 'data'
LOCK_FILE = DATA / '.paper_lock'

# 锁持有时长上限（秒）——防死锁兜底
LOCK_STALE_SEC = 120

_last_holder = {'name': '', 'ts': 0.0}


def _read_holder() -> str:
    try:
        return LOCK_FILE.read_text()
    except Exception:
        return ''


def acquire(holder: str):
    """非阻塞获取锁。返回file handle（持锁中）或None（拿不到）。"""
    try:
        LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
        fh = open(LOCK_FILE, 'w')
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fh.write(f'{holder}|{time.time():.0f}')
        fh.flush()
        return fh
    except (IOError, OSError):
        # 已被占用
        return None


def release(fh):
    if fh is None:
        return
    try:
        fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()
    except Exception:
        pass


def is_stale() -> tuple:
    """检查锁持有者是否已过期（进程死亡残留）。返回(holder, stale_bool)"""
    raw = _read_holder()
    if not raw or '|' not in raw:
        return '', False
    name, ts = raw.split('|', 1)
    try:
        ts = float(ts)
    except ValueError:
        return name, True
    return name, (time.time() - ts) > LOCK_STALE_SEC


class PaperLock:
    """上下文管理器封装。yield True=拿到锁，False=跳过本轮。"""
    def __init__(self, holder: str):
        self.holder = holder
        self.fh = None

    def __enter__(self):
        self.fh = acquire(self.holder)
        if self.fh is None:
            holder, stale = is_stale()
            return False
        return True

    def __exit__(self, *a):
        release(self.fh)
        return False


def paper_lock(holder: str):
    return PaperLock(holder)
