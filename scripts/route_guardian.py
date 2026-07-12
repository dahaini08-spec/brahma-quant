#!/usr/bin/env python3
"""
路由守卫 route_guardian.py
设计院封印 2026-07-12 苏摩111批准

职责：检测并修复 cron 任务路由回退到 019f309c 旧线程的问题。
每次 gateway 重启后路由可能回退，此脚本作为自愈机制定期运行。

用法：
  python3 scripts/route_guardian.py          # 检测+自动修复
  python3 scripts/route_guardian.py --check  # 仅检测，不修复
"""
import subprocess, json, sys, os
from datetime import datetime, timezone

CORRECT_THREAD = "73295708:thread:019f4448-76b9-7a64-b879-0ce3207fa18d"
OLD_THREAD_PREFIX = "019f309c"

# P1主线应路由到019f4448的任务名单
P1_MAIN_JOBS = {
    "rsi-structure-watcher",
    "auto-position-manager-30m",
    "pump-hunter",
    "regime-switch-monitor",
    "brahma-self-heal",
    "auto-executor-run",
    "oi-advanced-scanner",
    "sub-executor-run",
    "signal-dashboard",
    "brahma-paper-auto",
    "brahma-nerve-center",
    "stale-order-cleaner",
    "trc20-order-monitor",
    "main-signal-watcher",
    "signal-aggregator",
    "timesfm-bridge-4h",
    "brahma-scan-guard",
    "live-performance-daily",
    "gex-refresh",
    "brahma-strategy-doctor",
    "brahma-360-daily",
    "brahma-online-calibrate",
    "auto-position-manager",
    "route-guardian",
    "signal-fast-exec",
}

def get_jobs():
    cr = subprocess.run(['openclaw','cron','list','--json'], capture_output=True, text=True)
    if cr.returncode != 0:
        return []
    try:
        data = json.loads(cr.stdout)
        return data.get('jobs', data) if isinstance(data, dict) else data
    except:
        return []

def fix_route(job_id, job_name):
    result = subprocess.run(
        ['openclaw', 'cron', 'edit', job_id, '--to', CORRECT_THREAD],
        capture_output=True, text=True
    )
    return result.returncode == 0

def main():
    check_only = '--check' in sys.argv
    now = datetime.now(timezone.utc).strftime('%m-%d %H:%M UTC')

    jobs = get_jobs()
    if not jobs:
        print(f"[route_guardian] {now} ERROR: 无法获取cron任务列表")
        sys.exit(1)

    # 检测路由异常任务
    broken = []
    for j in jobs:
        name = j.get('name','')
        to   = j.get('delivery',{}).get('to','')
        jid  = j.get('id','')
        if OLD_THREAD_PREFIX in to and name in P1_MAIN_JOBS:
            broken.append((jid, name, to))

    if not broken:
        print(f"[route_guardian] {now} ✅ 路由正常，019f309c残留=0")
        return

    print(f"[route_guardian] {now} ⚠️ 发现{len(broken)}个路由异常:")
    for jid, name, to in broken:
        print(f"  {name} ({jid[:8]}) → {to[-20:]}")

    if check_only:
        print("[route_guardian] --check模式，不执行修复")
        sys.exit(1)

    # 自动修复
    fixed, failed = 0, 0
    for jid, name, to in broken:
        if fix_route(jid, name):
            print(f"  ✅ 修复: {name} → 019f4448")
            fixed += 1
        else:
            print(f"  ❌ 修复失败: {name}")
            failed += 1

    if fixed > 0:
        print(f"[route_guardian] {now} 修复完成: {fixed}成功 {failed}失败")
        if failed == 0:
            print(f"ROUTE_FIXED:{fixed}")
    else:
        print(f"[route_guardian] {now} 全部修复失败")
        sys.exit(1)

if __name__ == '__main__':
    main()
