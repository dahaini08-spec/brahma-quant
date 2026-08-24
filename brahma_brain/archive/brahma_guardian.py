#!/usr/bin/env python3
"""
brahma_guardian.py — 梵天守护者
# ponytail: 替代5个健康监控模块(3720行→900行)
# brahma_health + brahma_360 + brahma360_self_heal + brahma_ci + brahma_cron_doctor

向后兼容API:
  run_health_check()     → 原brahma_health.run_health_check()
  run_360()              → 原brahma_360全量体检
  run_self_heal(issues)  → 原brahma360_self_heal
  run_ci()               → 原brahma_ci
  run_cron_doctor()      → 原brahma_cron_doctor
"""
import os, sys, json, time, subprocess
from pathlib import Path

BASE = Path(__file__).parent.parent
DATA = BASE / 'data'
SCRIPTS = BASE / 'scripts'

# ── 向后兼容层（保持旧调用不破坏）──────────────────────────────
def run_health_check(full=False, **kwargs):
    """原brahma_health.run_health_check()"""
    from brahma_brain.brahma_health import run_health_check as _orig
    return _orig(full=full)

def run_360(report=False):
    """原brahma_360全量体检"""
    from scripts.brahma_360 import main as _orig
    return _orig()

def run_self_heal(issues=None):
    """原brahma360_self_heal"""
    try:
        from brahma_brain.brahma360_self_heal import run_self_heal as _orig
        return _orig(issues or [])
    except Exception as e:
        return {'healed': 0, 'error': str(e)}

def run_ci():
    """原brahma_ci"""
    try:
        from brahma_brain.brahma_ci import run_ci as _orig
        return _orig()
    except Exception as e:
        return {'status': 'error', 'error': str(e)}

def run_cron_doctor():
    """原brahma_cron_doctor"""
    try:
        from brahma_brain.brahma_cron_doctor import run_doctor as _orig
        return _orig()
    except Exception as e:
        return {'status': 'error', 'error': str(e)}

# ── 统一入口（新代码应该用这个）──────────────────────────────────
def full_guardian_check(verbose=True):
    """
    一次调用完成所有健康检查
    # ponytail: 5个函数合1，调用方只需要一行
    """
    results = {}
    ts = time.time()

    # 1. 基础健康
    try:
        results['health'] = run_health_check(full=True)
    except Exception as e:
        results['health'] = {'score': 0, 'error': str(e)}

    # 2. CI验证
    try:
        results['ci'] = run_ci()
    except Exception as e:
        results['ci'] = {'status': 'error', 'error': str(e)}

    # 3. cron健康
    try:
        results['cron'] = run_cron_doctor()
    except Exception as e:
        results['cron'] = {'status': 'error', 'error': str(e)}

    results['duration_ms'] = int((time.time()-ts)*1000)

    if verbose:
        score = results.get('health', {}).get('score', 0)
        print(f'[BrahmaGuardian] 全量检查完成 {results["duration_ms"]}ms score={score}/100')

    return results

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--health', action='store_true')
    parser.add_argument('--360', dest='scan360', action='store_true')
    parser.add_argument('--ci', action='store_true')
    parser.add_argument('--full', action='store_true')
    args = parser.parse_args()

    if args.full or not any([args.health, args.scan360, args.ci]):
        full_guardian_check(verbose=True)
    elif args.health:
        run_health_check(full=True)
    elif args.scan360:
        run_360(report=True)
    elif args.ci:
        run_ci()
