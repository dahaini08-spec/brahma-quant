#!/usr/bin/env python3
"""
cac_update.py — 梵天 CaC 基准更新工具
════════════════════════════════════════════════════════
设计院 · 2026-07-08

用途：
  当新增/修改/删除任务后，运行此脚本同步CaC基准。
  必须在 git commit 之前运行，确保CaC与实际一致。

用法：
  python3 scripts/cac_update.py           # 同步CaC基准
  python3 scripts/cac_update.py --check   # 仅检查漂移，不修复

原则：
  - CaC是SSOT，Git是历史
  - 每次 openclaw cron add/rm 后必须运行此脚本
  - CI/CD 检查时运行 --check 模式
"""
import json, sys, hashlib
from pathlib import Path
from datetime import datetime, timezone

JOBS_FILE = Path.home() / '.openclaw/cron/jobs.json'
CAC_FILE  = Path(__file__).parent.parent / 'config' / 'cron_jobs_cac.json'

def main():
    check_only = '--check' in sys.argv

    # 读取当前状态
    raw = json.loads(JOBS_FILE.read_text())
    all_jobs = raw.get('jobs', raw) if isinstance(raw, dict) else raw
    current = {j.get('name'): j for j in all_jobs}

    # 读取CaC基准
    cac_raw = json.loads(CAC_FILE.read_text()) if CAC_FILE.exists() else {}
    cac_jobs_list = cac_raw.get('jobs', []) if isinstance(cac_raw, dict) else []
    cac = {j.get('name'): j for j in cac_jobs_list}

    # 比较差异
    added   = set(current) - set(cac)
    removed = set(cac) - set(current)
    changed = set()

    for name in set(current) & set(cac):
        cur_msg = (current[name].get('payload',{}).get('message') or '').strip()
        cac_msg = (cac[name].get('payload',{}).get('message') or '').strip()
        if cur_msg != cac_msg:
            changed.add(name)

    if not (added or removed or changed):
        print(f'✅ CaC基准已是最新 ({len(current)}个任务)')
        return 0

    print(f'发现差异: 新增={len(added)} 删除={len(removed)} 修改={len(changed)}')
    if added:   print(f'  新增: {sorted(added)}')
    if removed: print(f'  删除: {sorted(removed)}')
    if changed: print(f'  修改: {sorted(changed)}')

    if check_only:
        print('⚠️ --check 模式，不自动更新')
        return 1

    # 更新CaC基准
    new_cac = {
        'version':   'cac-v1.1',
        'updated':   datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
        'job_count': len(all_jobs),
        'sha256':    hashlib.sha256(json.dumps(all_jobs, sort_keys=True).encode()).hexdigest()[:16],
        'jobs':      all_jobs,
    }
    CAC_FILE.parent.mkdir(exist_ok=True)
    CAC_FILE.write_text(json.dumps(new_cac, indent=2, ensure_ascii=False))
    print(f'✅ CaC基准已更新: {len(all_jobs)}个任务 → {CAC_FILE}')
    print('⚠️  请记得 git add config/cron_jobs_cac.json && git commit')
    return 0

if __name__ == '__main__':
    sys.exit(main())
