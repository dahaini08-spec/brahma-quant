#!/usr/bin/env python3
"""
log_rotation.py — 梵天系统日志轮转
每6小时运行，截断高速增长的日志文件，防止磁盘/内存压力
"""
import sys, os
from pathlib import Path

BASE = Path(__file__).parent.parent
DATA = BASE / 'data'

# 日志文件 → 保留最近N行
ROTATION_RULES = {
    'capital_alloc.jsonl':        500,
    'kronos_bridge_shadow.jsonl': 1000,
    'nerve_alerts.jsonl':          500,
    'signal_bus.jsonl':            200,
    'oi_advanced_signals.jsonl':   500,
    'tradfi_signal_log.jsonl':     500,
    'ev_feedback_log.jsonl':       300,
    'llm_council_shadow_log.jsonl': 300,
}

def rotate():
    report = []
    total_freed_mb = 0.0
    
    for fname, keep_lines in ROTATION_RULES.items():
        fpath = DATA / fname
        if not fpath.exists():
            continue
        
        old_size = fpath.stat().st_size
        lines = fpath.read_text(errors='replace').split('\n')
        lines = [l for l in lines if l.strip()]
        
        if len(lines) <= keep_lines:
            continue
        
        new_content = '\n'.join(lines[-keep_lines:]) + '\n'
        fpath.write_text(new_content)
        
        new_size = fpath.stat().st_size
        freed_mb = (old_size - new_size) / 1024 / 1024
        total_freed_mb += freed_mb
        report.append(f"  {fname}: {len(lines)}→{keep_lines}行 (-{freed_mb:.1f}MB)")
    
    if report:
        msg = f"🔄 日志轮转完成，共释放 {total_freed_mb:.1f}MB\n" + '\n'.join(report)
        print(msg)
        try:
            sys.path.insert(0, str(BASE / 'scripts'))
            from push_hub import _jarvis
            _jarvis(msg)
        except Exception:
            pass
    else:
        print("no_rotation_needed")

if __name__ == '__main__':
    rotate()
