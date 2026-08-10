#!/usr/bin/env python3
"""
log_rotation.py -- Brahma system log rotation + disk cleanup
Runs every 6h. Truncates fast-growing log files, cleans orphan tmp + brahma_cache.

2026-08-07 permanent fixes:
  P1: Clean /root/.openclaw/cron/*.tmp orphan temp files
  P2: Actively clean brahma_cache files older than 3 days
  P3: Disk pressure check, alert if >75%
"""
import sys, os, time, shutil
from pathlib import Path

BASE = Path(__file__).parent.parent
DATA = BASE / 'data'

ROTATION_RULES = {
    'capital_alloc.jsonl':         500,
    'kronos_bridge_shadow.jsonl':  1000,
    'nerve_alerts.jsonl':           500,
    'signal_bus.jsonl':             200,
    'oi_advanced_signals.jsonl':    500,
    'tradfi_signal_log.jsonl':      500,
    'ev_feedback_log.jsonl':        300,
    'llm_council_shadow_log.jsonl': 300,
}


def clean_core_dumps() -> float:
    """紧急：清理trading-system目录下的core dump文件（每次log_rotation运行时）"""
    import glob
    freed = 0.0
    count = 0
    # trading-system根目录
    for pattern in [
        str(BASE / 'core.*'),
        str(BASE.parent / 'core.*'),
    ]:
        for f in glob.glob(pattern):
            try:
                p = Path(f)
                if p.is_file() and p.stat().st_size > 1024*1024:  # >1MB才是真core
                    freed += p.stat().st_size
                    p.unlink()
                    count += 1
            except Exception:
                pass
    if count > 0:
        print(f"  [CORE] 清理{count}个core dump: 释放{freed/1024/1024/1024:.2f}GB")
    return freed / 1024 / 1024


def clean_cron_tmp() -> float:
    """P1: clean /root/.openclaw/cron/*.tmp orphan files (>1h old)"""
    cron_dir = Path('/root/.openclaw/cron')
    if not cron_dir.exists():
        return 0.0
    cutoff = time.time() - 3600
    freed = 0.0
    for f in cron_dir.glob('*.tmp'):
        try:
            if f.stat().st_mtime < cutoff:
                freed += f.stat().st_size
                f.unlink()
        except Exception:
            pass
    return freed / 1024 / 1024


def clean_brahma_cache() -> float:
    """P2: clean brahma_cache files older than 3 days"""
    cache_dir = DATA / 'brahma_cache'
    if not cache_dir.exists():
        return 0.0
    cutoff = time.time() - 3 * 86400
    freed = 0.0
    for f in cache_dir.iterdir():
        try:
            if f.is_file() and f.stat().st_mtime < cutoff:
                freed += f.stat().st_size
                f.unlink()
        except Exception:
            pass
    return freed / 1024 / 1024


def check_disk_pressure() -> tuple:
    """P3: check /root disk usage. Returns (pct, msg)"""
    usage = shutil.disk_usage('/root')
    pct = usage.used / usage.total * 100
    avail_gb = usage.free / 1024 ** 3
    if pct > 85:
        return pct, f"[DISK CRITICAL] {pct:.1f}% used / {avail_gb:.1f}GB free"
    elif pct > 75:
        return pct, f"[DISK WARNING] {pct:.1f}% used / {avail_gb:.1f}GB free"
    return pct, f"[DISK OK] {pct:.1f}% used / {avail_gb:.1f}GB free"


def rotate():
    report = []
    total_freed_mb = 0.0

    for fname, keep_lines in ROTATION_RULES.items():
        fpath = DATA / fname
        if not fpath.exists():
            continue
        old_size = fpath.stat().st_size
        lines = [l for l in fpath.read_text(errors='replace').split('\n') if l.strip()]
        if len(lines) <= keep_lines:
            continue
        fpath.write_text('\n'.join(lines[-keep_lines:]) + '\n')
        freed_mb = (old_size - fpath.stat().st_size) / 1024 / 1024
        total_freed_mb += freed_mb
        report.append(f"  {fname}: {len(lines)}->{keep_lines} lines (-{freed_mb:.1f}MB)")

    if report:
        msg = f"[log-rotation] freed {total_freed_mb:.1f}MB\n" + '\n'.join(report)
        print(msg)
        try:
            sys.path.insert(0, str(BASE / 'scripts'))
            from push_hub import _jarvis
            _jarvis(msg)
        except Exception:
            pass
    else:
        print("no_rotation_needed")

    # P1
    tmp_freed = clean_cron_tmp()
    if tmp_freed > 0:
        print(f"  [P1] cron.tmp cleaned: freed {tmp_freed:.1f}MB")

    # P2
    cache_freed = clean_brahma_cache()
    if cache_freed > 0:
        print(f"  [P2] brahma_cache cleaned: freed {cache_freed:.1f}MB")

    # P3
    pct, disk_msg = check_disk_pressure()
    print(f"  {disk_msg}")
    if pct > 75:
        try:
            sys.path.insert(0, str(BASE / 'scripts'))
            from push_hub import _jarvis
            _jarvis(f"[DISK ALERT]\n{disk_msg}\nPlease check disk usage.")
        except Exception:
            pass


if __name__ == '__main__':
    rotate()
