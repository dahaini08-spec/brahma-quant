#!/usr/bin/env python3
"""
core_dump_cleaner.py — 触发式磁盘清理（触发式 > 定时式）
设计院封印 2026-09-02 苏摩111

改造点：
  - 从"每天清理一次"→"每小时检查，有垃圾立即清理"
  - 磁盘>70% → 深度清理（core/log/__pycache__/tmp）
  - 磁盘>85% → 紧急告警推送
  - 有清理动作才推送，否则HEARTBEAT_OK静默
"""
import os, sys, glob, shutil, time, json
from pathlib import Path

BASE  = Path('/root/.openclaw/workspace/trading-system')
ROOT  = Path('/root')

DISK_WARN     = 70   # 磁盘>70% 触发深度清理
DISK_CRITICAL = 85   # 磁盘>85% 告警推送

def disk_usage_pct(path='/root'):
    st = os.statvfs(path)
    total = st.f_blocks * st.f_frsize
    free  = st.f_bavail * st.f_frsize
    used  = total - free
    return round(used / total * 100, 1), round(free / 1024**3, 1)

def human_size(n):
    for unit in ['B','KB','MB','GB']:
        if n < 1024: return '%.1f%s' % (n, unit)
        n /= 1024
    return '%.1fGB' % n

def clean_core_dumps():
    """清理 core.* 文件"""
    cores = list(BASE.glob('core.*')) + list(glob.glob('/root/core.*'))
    total_size = sum(f.stat().st_size for f in cores if Path(f).exists())
    for f in cores:
        try: Path(f).unlink()
        except: pass
    return len(cores), total_size

def clean_pycache():
    """清理 __pycache__ 目录"""
    total = 0
    count = 0
    for d in BASE.rglob('__pycache__'):
        try:
            size = sum(f.stat().st_size for f in d.rglob('*') if f.is_file())
            shutil.rmtree(d)
            total += size
            count += 1
        except: pass
    return count, total

def clean_old_logs():
    """清理7天前的日志文件"""
    cutoff = time.time() - 7 * 86400
    total = 0
    count = 0
    for pattern in ['logs/*.log.*', 'logs/*.gz', 'data/*.log']:
        for f in BASE.glob(pattern):
            try:
                if f.stat().st_mtime < cutoff:
                    size = f.stat().st_size
                    f.unlink()
                    total += size
                    count += 1
            except: pass
    return count, total

def clean_tmp():
    """清理/tmp下的openclaw临时文件"""
    total = 0
    count = 0
    for f in Path('/tmp').glob('openclaw-*.log'):
        try:
            if f.stat().st_mtime < time.time() - 86400:
                size = f.stat().st_size
                f.unlink()
                total += size
                count += 1
        except: pass
    return count, total

def run():
    disk_pct, disk_free_gb = disk_usage_pct()
    actions = []
    freed_total = 0

    # 1. 始终清理 core dump（无条件）
    n, sz = clean_core_dumps()
    if n > 0:
        actions.append('core dump ×%d → -%s' % (n, human_size(sz)))
        freed_total += sz

    # 2. 磁盘>70% 触发深度清理
    if disk_pct > DISK_WARN:
        n2, sz2 = clean_pycache()
        if sz2 > 0:
            actions.append('__pycache__ ×%d → -%s' % (n2, human_size(sz2)))
            freed_total += sz2

        n3, sz3 = clean_old_logs()
        if sz3 > 0:
            actions.append('旧日志 ×%d → -%s' % (n3, human_size(sz3)))
            freed_total += sz3

        n4, sz4 = clean_tmp()
        if sz4 > 0:
            actions.append('tmp ×%d → -%s' % (n4, human_size(sz4)))
            freed_total += sz4

    # 重新检查磁盘
    disk_pct_after, disk_free_after = disk_usage_pct()

    # 决定输出
    if not actions and disk_pct < DISK_WARN:
        print('HEARTBEAT_OK')
        return

    lines = ['🧹 梵天磁盘清理 | %s' % time.strftime('%m-%d %H:%M CST', time.localtime())]

    if actions:
        lines.append('清理内容:')
        for a in actions:
            lines.append('  ✅ ' + a)
        lines.append('合计释放: %s' % human_size(freed_total))

    # 磁盘状态
    icon = '🔴' if disk_pct_after > DISK_CRITICAL else ('🟡' if disk_pct_after > DISK_WARN else '🟢')
    lines.append('\n磁盘状态: %s %.1f%% (剩余%.1fGB)' % (icon, disk_pct_after, disk_free_after))

    if disk_pct_after > DISK_CRITICAL:
        lines.append('⚠️ 磁盘仍>85%%，请苏摩检查大文件！')
        lines.append('  df -h && du -sh /root/.openclaw/workspace/trading-system/* | sort -rh | head -10')

    msg = '\n'.join(lines)
    print(msg)

    # 有清理或磁盘告警才推送
    try:
        sys.path.insert(0, str(BASE / 'scripts'))
        from push_hub import _jarvis
        _jarvis(msg, dedup_key='core_dump_cleaner', dedup_ttl=1800)
    except Exception as e:
        pass  # 推送失败不影响清理

if __name__ == '__main__':
    run()
