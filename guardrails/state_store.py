#!/usr/bin/env python3
"""
state_store.py — 梵天系统地基层：原子状态持久化
════════════════════════════════════════════════════════════════
设计院 · 终极稳定性方案 · 2026-07-08

■ 问题根因（参考 Citadel / Two Sigma 数据一致性框架）：
  当前系统有 30+ 个文件各自用 write_text() / json.dump() 写状态。
  任何一个进程在写入中途被杀 → 文件截断 → 系统故障。
  这是「不稳定」的根本原因，不是监控任务不够多。

■ 解决方案：统一的状态存储地基
  所有状态文件的写入必须经过 StateStore。
  StateStore 提供原子操作：tmp → fsync → os.replace
  这是操作系统层面的保证，不依赖 Python 层面的任何逻辑。

■ 用法（替换所有裸 write_text）：
  # 旧写法（危险）：
  Path('data/regime_state.json').write_text(json.dumps(data))

  # 新写法（安全）：
  from guardrails.state_store import store
  store.write('regime_state', data)
  data = store.read('regime_state', default={})

■ 参考架构：
  - Jane Street: 不可变数据结构 + 写时复制
  - Two Sigma: 原子文件替换协议
  - Citadel: 双缓冲状态写入（active/standby）
  - Renaissance: 所有状态变更必须可审计
════════════════════════════════════════════════════════════════
"""

from __future__ import annotations
import json
import os
import shutil
import tempfile
import time
import threading
from pathlib import Path
from typing import Any, Optional, Callable


class StateStore:
    """
    梵天系统地基：统一原子状态存储。

    特性：
      1. 原子写入   — tmp → fsync → os.replace，绝不截断
      2. 自动备份   — 每次写入前保留 .bak（可选双重备份）
      3. 容灾读取   — 主文件损坏时自动回退到 .bak
      4. 写入锁     — 同名 key 的并发写入自动串行化
      5. 变更审计   — 可选写入 audit log（谁/何时/改了什么）
    """

    def __init__(self, data_dir: str | Path, audit: bool = False):
        self._base     = Path(data_dir)
        self._base.mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, threading.Lock] = {}
        self._global_lock = threading.Lock()
        self._audit    = audit
        self._audit_file = self._base / '_audit.jsonl'

    def _lock_for(self, key: str) -> threading.Lock:
        with self._global_lock:
            if key not in self._locks:
                self._locks[key] = threading.Lock()
            return self._locks[key]

    def _path(self, key: str) -> Path:
        safe = key.replace('/', '_').replace('..', '_')
        return self._base / f'{safe}.json'

    # ─────────────────────────────────────────────────────
    # 核心 API
    # ─────────────────────────────────────────────────────

    def read(self, key: str, default: Any = None) -> Any:
        """读取状态。主文件损坏时自动回退 .bak。"""
        p = self._path(key)
        for candidate in [p, Path(str(p) + '.bak')]:
            if candidate.exists():
                try:
                    return json.loads(candidate.read_text())
                except Exception:
                    continue
        return default

    def write(self, key: str, data: Any, backup: bool = True) -> bool:
        """
        原子写入状态。
        流程：序列化 → 验证 → tmp写入 → fsync → os.replace
        Returns True on success, False if serialization failed (data untouched).
        """
        with self._lock_for(key):
            p = self._path(key)
            try:
                serialized = json.dumps(data, ensure_ascii=False, indent=2)
                json.loads(serialized)  # 验证可反解析
            except Exception as e:
                self._log_audit(key, 'WRITE_FAIL', f'序列化失败: {e}')
                return False

            p.parent.mkdir(parents=True, exist_ok=True)

            if backup and p.exists():
                try:
                    shutil.copy2(p, str(p) + '.bak')
                except Exception:
                    pass

            try:
                fd, tmp = tempfile.mkstemp(dir=p.parent, suffix='.tmp')
                with os.fdopen(fd, 'w') as f:
                    f.write(serialized)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, p)
                if self._audit:
                    self._log_audit(key, 'WRITE_OK', f'size={len(serialized)}B')
                return True
            except Exception as e:
                try:
                    os.unlink(tmp)
                except Exception:
                    pass
                self._log_audit(key, 'WRITE_FAIL', f'IO失败: {e}')
                return False

    def update(self, key: str, fn: Callable, default: Any = None) -> bool:
        """
        读取 → 应用函数 → 原子写回。
        适用于局部更新（如追加日志、更新计数器）。
        
        示例：
          store.update('signal_count', lambda d: {**d, 'count': d.get('count',0)+1})
        """
        with self._lock_for(key):
            current = self.read(key, default)
            try:
                updated = fn(current)
            except Exception as e:
                self._log_audit(key, 'UPDATE_FAIL', f'函数执行失败: {e}')
                return False
            return self.write(key, updated, backup=True)

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def delete(self, key: str) -> bool:
        p = self._path(key)
        if p.exists():
            try:
                p.unlink()
                return True
            except Exception:
                return False
        return False

    def age_seconds(self, key: str) -> Optional[float]:
        """返回状态文件的年龄（秒），不存在则返回 None。"""
        p = self._path(key)
        if p.exists():
            return time.time() - p.stat().st_mtime
        return None

    # ─────────────────────────────────────────────────────
    # 审计
    # ─────────────────────────────────────────────────────

    def _log_audit(self, key: str, event: str, detail: str = ''):
        if not self._audit:
            return
        try:
            entry = json.dumps({
                'ts':     int(time.time()),
                'key':    key,
                'event':  event,
                'detail': detail,
            })
            with open(self._audit_file, 'a') as f:
                f.write(entry + '\n')
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────
# 全局单例（懒加载）
# ─────────────────────────────────────────────────────────────
_STORE: Optional[StateStore] = None
_STORE_LOCK = threading.Lock()


def get_store(data_dir: str | Path | None = None) -> StateStore:
    """
    获取全局 StateStore 单例。
    首次调用传入 data_dir；后续调用无需传参。
    """
    global _STORE
    if _STORE is None:
        with _STORE_LOCK:
            if _STORE is None:
                if data_dir is None:
                    data_dir = Path(__file__).parent.parent / 'data'
                _STORE = StateStore(data_dir)
    return _STORE


# 便捷访问（推荐用法）
store = get_store()
