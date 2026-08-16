#!/usr/bin/env python3
"""
session_disk_cleanup.py — 梵天Session磁盘孤儿文件清理
设计院永久修复 2026-08-07

根因：openclaw每次cron isolated run都产生session文件，
      完成后session元数据被清理，但磁盘上的jsonl/trajectory/checkpoint文件残留。
      无自动清理机制 → 每日~200MB增速 → 最终ENOSPC崩溃。

策略：
  - 读取sessions.json，获取所有活跃session引用的文件UUID
  - 磁盘上不被引用的文件 = 孤儿文件，安全删除
  - 额外保护：最近N小时内创建的文件不删（防止删到正在运行的session）
  - dry_run模式：只统计不删除
"""

import json
import os
import pathlib
import re
import time
import sys
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
log = logging.getLogger("session_cleanup")

SESS_DIR     = pathlib.Path("/root/.openclaw/agents/main/sessions")
PROTECT_HOURS = 2   # 最近2小时内的文件不动


def get_referenced_uuids() -> set:
    """从sessions.json提取所有活跃session引用的UUID"""
    sfile = SESS_DIR / "sessions.json"
    if not sfile.exists():
        return set()
    try:
        data = json.loads(sfile.read_text())
    except Exception:
        return set()

    uuids = set()
    sessions = data if isinstance(data, dict) else {}
    for key, val in sessions.items():
        if not isinstance(val, dict):
            continue
        sf = val.get("sessionFile", "")
        if sf:
            uuids.add(pathlib.Path(sf).stem.split("-topic-")[0])
            uuids.add(pathlib.Path(sf).stem)
        sid = val.get("sessionId", "")
        if sid:
            uuids.add(sid)
        # 从key中提取uuid
        found = re.findall(
            r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',
            key
        )
        uuids.update(found)
    return uuids


def cleanup(dry_run: bool = False) -> dict:
    if not SESS_DIR.exists():
        return {"error": "sessions dir not found"}

    protected_ts = time.time() - PROTECT_HOURS * 3600
    referenced = get_referenced_uuids()

    removed_count = 0
    removed_bytes = 0
    kept_count    = 0
    protected_count = 0
    errors        = 0

    for f in SESS_DIR.iterdir():
        if f.name in ("sessions.json", "sessions.json.lock"):
            continue
        if not f.is_file():
            continue

        # 保护最近N小时内的文件
        try:
            mtime = f.stat().st_mtime
        except Exception:
            continue

        if mtime >= protected_ts:
            protected_count += 1
            continue

        # 判断是否被引用
        fname = f.name
        uuids_in_name = re.findall(
            r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',
            fname
        )
        is_referenced = any(u in referenced for u in uuids_in_name)

        if is_referenced:
            kept_count += 1
            continue

        # 孤儿文件 → 删除
        try:
            sz = f.stat().st_size
            if not dry_run:
                f.unlink()
            removed_count += 1
            removed_bytes += sz
        except Exception as e:
            errors += 1
            log.warning(f"删除失败: {f.name} — {e}")

    result = {
        "dry_run":        dry_run,
        "removed_files":  removed_count,
        "removed_mb":     round(removed_bytes / 1024 / 1024, 1),
        "kept_files":     kept_count,
        "protected_recent": protected_count,
        "errors":         errors,
        "disk_after_mb":  round(get_sess_disk_mb(), 1),
    }
    return result


def get_sess_disk_mb() -> float:
    total = 0
    for f in SESS_DIR.iterdir():
        try:
            total += f.stat().st_size
        except Exception:
            pass
    return total / 1024 / 1024


def main():
    dry_run = "--dry-run" in sys.argv
    mode = "DRY-RUN" if dry_run else "EXECUTE"
    log.info(f"[session_cleanup] 启动 {mode} 模式")

    before_mb = get_sess_disk_mb()
    result = cleanup(dry_run=dry_run)

    action = "将释放" if dry_run else "已释放"
    msg = (
        f"[session_cleanup] {mode} 完成\n"
        f"  {action}: {result['removed_files']}个文件 / {result['removed_mb']}MB\n"
        f"  保留: {result['kept_files']}个活跃文件\n"
        f"  保护(最近{PROTECT_HOURS}h): {result['protected_recent']}个\n"
        f"  错误: {result['errors']}个\n"
        f"  Sessions目录: {before_mb:.1f}MB → {result['disk_after_mb']:.1f}MB"
    )
    print(msg)

    # 推送到Jarvis（非dry_run时）
    if not dry_run and result["removed_mb"] > 10:
        try:
            sys.path.insert(0, str(pathlib.Path(__file__).parent))
            from push_hub import _jarvis
            freed = result["removed_mb"]
            _jarvis(
                f"🧹 Session磁盘清理完成\n"
                f"释放: {freed}MB / {result['removed_files']}个孤儿文件\n"
                f"当前Sessions目录: {result['disk_after_mb']}MB"
            )
        except Exception:
            pass

    return result


if __name__ == "__main__":
    main()
