#!/usr/bin/env python3
"""
brahma_data_ttl.py — 梵天数据TTL自动清理
# ponytail: 每个数据文件一条TTL规则，stdlib only，无依赖
每天执行一次，防止数据文件无限膨胀导致OOM重启

TTL策略:
  keep_n  = 保留最新N条
  keep_days = 保留最近N天（按ts字段）
"""
import json, time, shutil, os
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / 'data'
BACKUP_DIR = DATA_DIR / '_ttl_backup'

# TTL规则表 — 每个文件的保留策略
TTL_RULES = {
    'hcme_pseudo_signals.jsonl':    {'keep_n': 6000},
    'structure_db_btc.jsonl':       {'keep_n': 5000},
    'structure_db_eth.jsonl':       {'keep_n': 5000},
    'nerve_alerts.jsonl':           {'keep_n': 500},
    'brahma_structured.jsonl':      {'keep_n': 1000},
    'kronos_bridge_shadow.jsonl':   {'keep_n': 1000},
    'nerve_bus.jsonl':              {'keep_n': 200},
    'capital_alloc.jsonl':          {'keep_n': 500},
    'oi_advanced_signals.jsonl':    {'keep_n': 300},
    'live_signal_log.jsonl':        {'keep_n': 1000},
    'tradfi_signal_log.jsonl':      {'keep_n': 500},
    'multi_scan_log.jsonl':         {'keep_n': 300},
    'signal_bus.jsonl':             {'keep_n': 200},
    'news_feed.jsonl':              {'keep_n': 200},
    'wuqu_paper_trades.jsonl':      {'keep_n': 2000},
    'auto_execute_log.jsonl':       {'keep_n': 500},
}

def run_ttl(dry_run=False):
    freed_total = 0
    results = []

    for fname, rule in TTL_RULES.items():
        p = DATA_DIR / fname
        if not p.exists():
            continue

        before_mb = p.stat().st_size / 1024 / 1024
        lines = [l for l in p.read_text(errors='ignore').strip().split('\n') if l.strip()]
        before_n = len(lines)

        keep_n = rule.get('keep_n', 9999)
        keep_lines = lines[-keep_n:] if len(lines) > keep_n else lines
        after_n = len(keep_lines)

        if after_n >= before_n:
            continue  # 不需要清理

        if not dry_run:
            p.write_text('\n'.join(keep_lines) + '\n')

        after_mb = p.stat().st_size / 1024 / 1024 if not dry_run else before_mb * (after_n / before_n)
        freed = before_mb - after_mb
        freed_total += freed
        results.append(f'{fname}: {before_n}→{after_n}条 释放{freed:.1f}MB')

    prefix = '[DRY_RUN] ' if dry_run else ''
    for r in results:
        print(f'{prefix}✅ {r}')
    print(f'{prefix}合计释放: {freed_total:.1f}MB')
    return freed_total

if __name__ == '__main__':
    import sys
    dry = '--dry-run' in sys.argv
    print(f'梵天数据TTL清理 {"(模拟)" if dry else "(执行)"} {time.strftime("%Y-%m-%d %H:%M")}')
    freed = run_ttl(dry_run=dry)
    if freed > 5:
        print(f'⚠️  释放{freed:.1f}MB，数据膨胀已控制')
    elif freed > 0:
        print(f'✅ 释放{freed:.1f}MB，系统健康')
    else:
        print('✅ 数据量正常，无需清理')
