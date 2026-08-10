#!/usr/bin/env python3
"""
cleanup_core_dumps.py — 自动清理 core dump 文件
每2小时运行，防止磁盘被core文件填满导致重启
"""
import sys, os
from pathlib import Path

BASE = Path(__file__).parent.parent

def run():
    core_files = list(BASE.glob('core.*'))
    if not core_files:
        return "no_core_dumps"
    
    total_mb = sum(f.stat().st_size for f in core_files) / 1024 / 1024
    count = len(core_files)
    
    for f in core_files:
        try:
            f.unlink()
        except Exception as e:
            print(f"删除失败: {f} — {e}")
    
    msg = f"🧹 清理 {count} 个 core dump，释放 {total_mb:.0f}MB"
    print(msg)
    
    # 推送到Jarvis
    try:
        sys.path.insert(0, str(BASE / 'scripts'))
        from push_hub import _jarvis
        _jarvis(msg)
    except Exception:
        pass
    
    return msg

if __name__ == '__main__':
    result = run()
    print(result)
