#!/usr/bin/env python3
"""
brahma360_cleaner.py — 梵天360清洁引擎
设计院 · 2026-06-09

职责（P1）：
  ① logs/ 自动轮转：>7天的log压缩归档，>30天的删除
  ② data/jsonl 自动归档：单文件>5000条 → 归档旧数据，保留最新2000条
  ③ 孤立脚本扫描：发现未被引用的脚本 → 生成报告推Jarvis（不自动移动）

用法：
  python3 scripts/brahma360_cleaner.py [--dry-run]
"""
import os, sys, gzip, shutil, json, re, subprocess
from pathlib import Path
from datetime import datetime, timezone, timedelta

BASE    = Path(__file__).parent.parent
LOGS    = BASE / 'logs'
DATA    = BASE / 'data'
SCRIPTS = BASE / 'scripts'
ARCHIVE = BASE / 'archive'

BJ   = timezone(timedelta(hours=8))
NOW  = datetime.now(timezone.utc)
DRY  = '--dry-run' in sys.argv

def log(msg): print(f'[Cleaner] {msg}')

# ══════════════════════════════════════════════
# ① logs/ 轮转
# ══════════════════════════════════════════════
def rotate_logs():
    if not LOGS.exists(): return []
    actions = []
    now_ts = NOW.timestamp()
    for f in LOGS.glob('*.log'):
        age_days = (now_ts - f.stat().st_mtime) / 86400
        size_mb  = f.stat().st_size / 1e6

        # >30天 → 删除
        if age_days > 30:
            if not DRY: f.unlink()
            actions.append(f'DELETE {f.name} ({age_days:.0f}天, {size_mb:.1f}MB)')

        # >7天 且 >1MB → 压缩归档
        elif age_days > 7 and size_mb > 1:
            gz_path = LOGS / f'{f.stem}.{f.stat().st_mtime:.0f}.log.gz'
            if not DRY:
                with open(f, 'rb') as fi, gzip.open(gz_path, 'wb') as fo:
                    shutil.copyfileobj(fi, fo)
                f.unlink()
            actions.append(f'COMPRESS {f.name} → {gz_path.name}')

        # >50MB 当前活跃日志 → 截断（保留最后5000行）
        elif size_mb > 50:
            if not DRY:
                lines = f.read_text(errors='ignore').splitlines()
                f.write_text('\n'.join(lines[-5000:]) + '\n')
            actions.append(f'TRUNCATE {f.name} ({size_mb:.1f}MB → 保留5000行)')

    # 同时清理>30天的.gz
    for f in LOGS.glob('*.gz'):
        age_days = (now_ts - f.stat().st_mtime) / 86400
        if age_days > 30:
            if not DRY: f.unlink()
            actions.append(f'DELETE_GZ {f.name}')

    return actions

# ══════════════════════════════════════════════
# ② data/jsonl 归档
# ══════════════════════════════════════════════
def archive_jsonl():
    actions = []
    data_archive = DATA / 'archive'
    if not DRY: data_archive.mkdir(exist_ok=True)

    CRITICAL = {'live_signal_log.jsonl', 'brahma_state.json', 'signal_queue.jsonl', 'wuqu_paper_state.json'}
    KEEP_RECENT = 2000   # 保留最新条数
    MAX_LINES   = 5000   # 超过此数触发归档

    for f in DATA.glob('*.jsonl'):
        if f.name in CRITICAL and 'log' not in f.name:
            continue
        lines = f.read_text(errors='ignore').splitlines()
        if len(lines) <= MAX_LINES:
            continue

        # 归档旧数据
        ts = NOW.strftime('%Y%m%d_%H%M')
        arch_path = data_archive / f'{f.stem}_{ts}.jsonl.gz'
        old_lines = lines[:-KEEP_RECENT]
        new_lines = lines[-KEEP_RECENT:]

        if not DRY:
            with gzip.open(arch_path, 'wt', encoding='utf-8') as gz:
                gz.write('\n'.join(old_lines) + '\n')
            f.write_text('\n'.join(new_lines) + '\n')

        actions.append(f'ARCHIVE {f.name}: {len(lines)}条 → 保留{KEEP_RECENT}条, 归档{len(old_lines)}条 → {arch_path.name}')

    # 清理data/archive/中>90天的gz
    if data_archive.exists():
        now_ts = NOW.timestamp()
        for f in data_archive.glob('*.gz'):
            if (now_ts - f.stat().st_mtime) / 86400 > 90:
                if not DRY: f.unlink()
                actions.append(f'DELETE_OLD_ARCHIVE {f.name}')

    return actions

# ══════════════════════════════════════════════
# ③ 孤立脚本扫描（只报告，不移动）
# ══════════════════════════════════════════════
def scan_orphans():
    # 读取cron白名单
    cron_used = set()
    try:
        data = json.load(open(BASE / '..' / '..' / 'cron' / 'jobs.json'))
        for j in data.get('jobs', []):
            e = j['payload'].get('event','') + j['payload'].get('message','')
            for m in re.findall(r'scripts/(\S+\.py)', e):
                cron_used.add(m)
    except: pass

    # 核心白名单
    ALWAYS_KEEP = {
        'brahma_commander.py','position_guardian.py','brahma_state_refresh.py',
        'brahma360_guardian.py','brahma360_l2_diag.py','brahma360_health_gate.py',
        'brahma360_cleaner.py','brahma360_auditor.py','system_regression_test.py',
        'signal_utils.py','oi_anomaly_broadcast.py','truth_baseline_weekly.py',
        'phase0_monitor.py','cron_noai_runner.sh','watchdog_guardian.sh',
        'gateway_memory_guard.sh','supervisor_check.sh',
    }

    orphans = []
    for f in sorted(SCRIPTS.glob('*.py')):
        if f.name in ALWAYS_KEEP or f.name in cron_used:
            continue
        # 检查import引用
        result = subprocess.run(
            ['grep', '-rl', f'import {f.stem}\b', '--include=*.py',
             'scripts/', 'brahma_brain/', 'dharma/'],
            capture_output=True, text=True, cwd=str(BASE)
        )
        hits = [l for l in result.stdout.splitlines() if 'archive' not in l]
        if not hits:
            orphans.append(f.name)

    return orphans

def main():
    bj_time = NOW.astimezone(BJ).strftime('%Y-%m-%d %H:%M CST')
    mode = ' [DRY-RUN]' if DRY else ''
    print(f'[梵天360清洁] {bj_time}{mode}')

    actions_log = rotate_logs()
    actions_jsonl = archive_jsonl()

    total = len(actions_log) + len(actions_jsonl)

    if actions_log:
        print(f'\n📁 logs/轮转 ({len(actions_log)}项):')
        for a in actions_log: print(f'  {a}')

    if actions_jsonl:
        print(f'\n📊 data/归档 ({len(actions_jsonl)}项):')
        for a in actions_jsonl: print(f'  {a}')

    # 孤立脚本扫描（只在每周一运行，避免每次都慢）
    orphans = []
    if NOW.weekday() == 0:
        print('\n🔍 孤立脚本扫描...')
        orphans = scan_orphans()
        if orphans:
            print(f'⚠️ 发现{len(orphans)}个孤立脚本（需人工确认后归档）:')
            for o in orphans: print(f'  {o}')
        else:
            print('✅ 无孤立脚本')

    if total == 0 and not orphans:
        print('HEARTBEAT_OK')
    else:
        print(f'\n✅ 清洁完成: 日志{len(actions_log)}项 + 数据{len(actions_jsonl)}项')

if __name__ == '__main__':
    main()
