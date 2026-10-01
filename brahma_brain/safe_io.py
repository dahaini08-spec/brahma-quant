#!/usr/bin/env python3
# ponytail: safe_io 多进程安全文件IO封装
# [2.0封印 2026-10-01 苏摩111] 接入位置：auto_executor/hunter_executor/brahma_health/brahma_cpu

"""
safe_io.py — 梵天多进程安全文件IO
===================================
解决三个P0并发问题：
  1. signal_queue/live_signal_log 多进程同时append → 截断/丢失
  2. position_sl_state.json 4个写者无锁 → SL价格被覆盖
  3. brahma_state_{symbol}.json 无锁读写 → 状态撕裂

API:
  locked_jsonl_append(path, entry)        — 原子追加JSONL行
  locked_jsonl_read(path)                 — 安全读取JSONL列表
  locked_json_read(path, default=None)    — 安全读取JSON
  locked_json_write(path, data)           — 原子写入JSON（先写tmp再rename）
  locked_json_update(path, fn, default)   — 原子读改写（fn(data)->data）
"""

from __future__ import annotations
import fcntl, json, os, tempfile
from pathlib import Path
from typing import Any, Callable

# ─── 内部辅助 ────────────────────────────────────────────────────────────────

def _flock_ex(f):
    fcntl.flock(f.fileno(), fcntl.LOCK_EX)

def _flock_un(f):
    fcntl.flock(f.fileno(), fcntl.LOCK_UN)


# ─── 公开API ─────────────────────────────────────────────────────────────────

def locked_jsonl_append(path: str | Path, entry: dict) -> None:
    """原子追加一行JSONL，多进程安全。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(entry, ensure_ascii=False) + '\n'
    with open(path, 'a', encoding='utf-8') as f:
        _flock_ex(f)
        try:
            f.write(line)
        finally:
            _flock_un(f)


def locked_jsonl_read(path: str | Path) -> list:
    """安全读取JSONL，跳过损坏行，返回list。"""
    path = Path(path)
    if not path.exists():
        return []
    result = []
    with open(path, 'r', encoding='utf-8') as f:
        _flock_ex(f)
        try:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    result.append(json.loads(line))
                except json.JSONDecodeError:
                    pass  # 损坏行跳过，不炸
        finally:
            _flock_un(f)
    return result


def locked_json_read(path: str | Path, default: Any = None) -> Any:
    """安全读取JSON文件，不存在或损坏时返回default。"""
    path = Path(path)
    if not path.exists():
        return default
    try:
        with open(path, 'r', encoding='utf-8') as f:
            _flock_ex(f)
            try:
                return json.load(f)
            finally:
                _flock_un(f)
    except (json.JSONDecodeError, OSError):
        return default


def locked_json_write(path: str | Path, data: Any) -> None:
    """原子写入JSON：先写临时文件再rename，防止写到一半崩溃导致文件损坏。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix='.tmp_')
    try:
        with os.fdopen(tmp_fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)  # 原子rename
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def locked_json_update(path: str | Path,
                        fn: Callable[[Any], Any],
                        default: Any = None) -> Any:
    """
    原子读改写JSON：
      data = read(path) or default
      new_data = fn(data)
      write(path, new_data)
      return new_data
    全程持有排他锁，多进程安全。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + '.lock')
    with open(lock_path, 'a') as lock_f:
        _flock_ex(lock_f)
        try:
            if path.exists():
                try:
                    with open(path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                except (json.JSONDecodeError, OSError):
                    data = default
            else:
                data = default
            new_data = fn(data)
            locked_json_write(path, new_data)
            return new_data
        finally:
            _flock_un(lock_f)
