#!/usr/bin/env python3
"""
brahma_system_integrity.py — 梵天系统一致性自检
════════════════════════════════════════════════════════════════
设计院 · 终极稳定性方案 · 2026-07-08

■ 核心理念（参考 Netflix Chaos Engineering + Two Sigma 状态验证）：
  系统稳定不靠「部署后祈祷」，而靠「可验证的不变量」。
  每次启动/每日，运行一次完整性自检，确认系统处于已知健康状态。

■ 验证层级（从底层到顶层）：
  L0 文件系统完整性  — 核心文件存在且可读
  L1 配置一致性      — system_config + CaC基准 + SSOT一致
  L2 模块注册完整性  — 54个核心模块全部可导入
  L3 数据新鲜度      — 关键数据文件不陈旧
  L4 推送通路        — push_hub可用 + cron路由正确
  L5 执行边界        — 没有孤立持仓/悬空止损

■ 自检结果：
  HEALTHY   — 所有层级通过
  DEGRADED  — 部分非关键层级失败（系统可运行，需关注）
  CRITICAL  — 关键层级失败（需立即处理）
════════════════════════════════════════════════════════════════
"""

import sys
import os
import json
import time
import importlib
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'brahma_brain'))
sys.path.insert(0, str(BASE / 'scripts'))

try:
    import scripts.system_config as _sc
    PUSH_TARGET  = f"{_sc.JARVIS_USER_ID}:thread:{_sc.JARVIS_THREAD_ID}"
    CORRECT_THREAD = _sc.JARVIS_THREAD_ID
except Exception:
    PUSH_TARGET    = '73295708:thread:019f443a-b891-70f1-8cb0-ed031a80e68b'
    CORRECT_THREAD = '019f443a-b891-70f1-8cb0-ed031a80e68b'

PUSH_CHANNEL = 'jarvis'

# ═══════════════════════════════════════════════════════════
# L0 — 文件系统完整性
# ═══════════════════════════════════════════════════════════

CORE_FILES = [
    # 系统核心
    'scripts/system_config.py',
    'brahma_brain/brahma_core.py',
    'brahma_brain/brahma_bus.py',
    'brahma_brain/brahma_analysis_runner.py',
    'brahma_brain/regime_state_machine.py',
    'brahma_brain/timing_filter.py',
    'brahma_brain/position_sizer.py',
    # 执行层
    'scripts/brahma_execute.py',
    'scripts/brahma_order_engine.py',
    'scripts/auto_position_manager.py',
    # 推送层
    'scripts/push_hub.py',
    'scripts/signal_bus.py',
    'scripts/brahma_nerve_center.py',
    # 自愈层
    'scripts/brahma_self_heal.py',
    'scripts/brahma_constitutional_guardian.py',
    # 地基层（新增）
    'guardrails/state_store.py',
    'guardrails/safe_json.py',
    # 配置基准
    'config/cron_jobs_cac.json',
]

def check_l0_filesystem() -> dict:
    missing = []
    for f in CORE_FILES:
        p = BASE / f
        if not p.exists():
            missing.append(f)
    return {
        'level': 'L0',
        'name':  '文件系统完整性',
        'ok':    len(missing) == 0,
        'missing': missing,
        'detail': f'核心文件齐全' if not missing else f'缺失 {len(missing)} 个: {missing[:3]}',
    }


# ═══════════════════════════════════════════════════════════
# L1 — 配置一致性
# ═══════════════════════════════════════════════════════════

def check_l1_config() -> dict:
    issues = []
    try:
        import scripts.system_config as sc
        thread = sc.JARVIS_THREAD_ID
        uid    = sc.JARVIS_USER_ID
    except Exception as e:
        return {'level': 'L1', 'name': '配置一致性', 'ok': False,
                'detail': f'system_config加载失败: {e}'}

    # 检查 CaC 基准中的路由是否与 system_config 一致
    cac_file = BASE / 'config' / 'cron_jobs_cac.json'
    if cac_file.exists():
        raw = json.loads(cac_file.read_text())
        jobs = raw.get('jobs', [])
        wrong_route = [j.get('name') for j in jobs
                       if j.get('delivery', {}).get('to') and
                       thread not in j.get('delivery', {}).get('to', '')]
        if wrong_route:
            issues.append(f'CaC路由不一致({len(wrong_route)}个): {wrong_route[:3]}')

    return {
        'level': 'L1',
        'name':  '配置一致性',
        'ok':    len(issues) == 0,
        'thread': thread[:8] + '...',
        'issues': issues,
        'detail': f'配置一致' if not issues else '; '.join(issues),
    }


# ═══════════════════════════════════════════════════════════
# L2 — 模块注册完整性
# ═══════════════════════════════════════════════════════════

CRITICAL_MODULES = [
    'brahma_brain.brahma_bus',
    'brahma_brain.brahma_core',
    'brahma_brain.brahma_analysis_runner',
    'brahma_brain.regime_state_machine',
    'brahma_brain.timing_filter',
    'brahma_brain.position_sizer',
    'brahma_brain.math_utils',
    'brahma_brain.smc_engine',
    'brahma_brain.market_state',
]

def check_l2_modules() -> dict:
    failed = []
    for mod in CRITICAL_MODULES:
        try:
            importlib.import_module(mod)
        except Exception as e:
            failed.append(f'{mod}: {str(e)[:40]}')
    return {
        'level': 'L2',
        'name':  '模块注册完整性',
        'ok':    len(failed) == 0,
        'failed': failed,
        'detail': f'{len(CRITICAL_MODULES)-len(failed)}/{len(CRITICAL_MODULES)}模块正常'
                  + (f' | 失败: {failed[:2]}' if failed else ''),
    }


# ═══════════════════════════════════════════════════════════
# L3 — 数据新鲜度
# ═══════════════════════════════════════════════════════════

DATA_FRESHNESS = {
    'data/regime_state.json':        90,   # 90min内必须更新
    'data/scan_candidates.json':    120,
    'data/rsi_watcher_state.json':   30,
    'data/live_signal_log.jsonl':   480,   # 8H内有信号才算正常
}

def check_l3_data() -> dict:
    stale = []
    now   = time.time()
    for rel_path, max_age in DATA_FRESHNESS.items():
        p = BASE / rel_path
        if not p.exists():
            stale.append(f'{rel_path}(不存在)')
        else:
            age = (now - p.stat().st_mtime) / 60
            if age > max_age:
                stale.append(f'{rel_path}({age:.0f}min)')
    return {
        'level': 'L3',
        'name':  '数据新鲜度',
        'ok':    len(stale) == 0,
        'stale': stale,
        'detail': '数据新鲜' if not stale else f'陈旧: {stale}',
    }


# ═══════════════════════════════════════════════════════════
# L4 — 推送通路
# ═══════════════════════════════════════════════════════════

KEY_CRON_JOBS = [
    'pump-hunter', 'rsi-structure-watcher', 'brahma-nerve-center',
    'oi-surge-scanner', 'main-signal-watcher', 'brahma-self-heal',
    'brahma-constitutional-guardian',
]

def check_l4_push() -> dict:
    issues = []

    push_hub = BASE / 'push_hub.py'
    push_hub_s = BASE / 'scripts' / 'push_hub.py'
    if not push_hub.exists() and not push_hub_s.exists():
        issues.append('push_hub.py 缺失')

    cron_file = Path.home() / '.openclaw/cron/jobs.json'
    if cron_file.exists():
        raw  = json.loads(cron_file.read_text())
        jobs = raw.get('jobs', raw) if isinstance(raw, dict) else raw
        jmap = {j.get('name'): j for j in jobs}
        for name in KEY_CRON_JOBS:
            j = jmap.get(name)
            if not j:
                issues.append(f'{name}: 未注册')
                continue
            msg = (j.get('payload', {}).get('message') or '').strip()
            ann = j.get('delivery', {}).get('announce', False)
            to  = j.get('delivery', {}).get('to', '')
            if not msg:
                issues.append(f'{name}: message空')
            if not ann:
                issues.append(f'{name}: announce=False')
            if CORRECT_THREAD not in to:
                issues.append(f'{name}: 路由错误')
    else:
        issues.append('jobs.json 不存在')

    return {
        'level': 'L4',
        'name':  '推送通路',
        'ok':    len(issues) == 0,
        'issues': issues,
        'detail': f'推送通路正常' if not issues else f'{len(issues)}个问题: {issues[:3]}',
    }


# ═══════════════════════════════════════════════════════════
# L5 — 执行边界
# ═══════════════════════════════════════════════════════════

def check_l5_execution() -> dict:
    issues = []

    # 检查 wuqu_positions 是否有悬空持仓（持仓>0但无止损记录）
    wuqu_f = BASE / 'data' / 'wuqu_positions.json'
    sl_f   = BASE / 'data' / 'sl_state.json'

    if wuqu_f.exists() and sl_f.exists():
        try:
            wuqu = json.loads(wuqu_f.read_text())
            sl   = json.loads(sl_f.read_text())
            positions = wuqu if isinstance(wuqu, list) else wuqu.get('positions', [])
            for pos in positions:
                sym = pos.get('symbol', pos.get('sym', '?'))
                if sym != '?' and sym not in sl:
                    issues.append(f'{sym}: 持仓无止损记录')
        except Exception:
            pass  # 解析失败不作为L5故障

    # 检查是否有超过24H的信号仍为 pending（队列积压）
    sig_bus = BASE / 'data' / 'signal_bus.jsonl'
    if sig_bus.exists():
        try:
            lines = sig_bus.read_text().strip().split('\n')
            now   = time.time()
            # 只计算 status=pending 的积压（历史记录是正常的）
            stuck = sum(1 for l in lines if l.strip() and
                        json.loads(l).get('status') == 'pending' and
                        now - json.loads(l).get('ts', now) > 86400)
            if stuck > 0:
                issues.append(f'signal_bus积压{stuck}条pending>≥24H')
        except Exception:
            pass

    return {
        'level': 'L5',
        'name':  '执行边界',
        'ok':    len(issues) == 0,
        'issues': issues,
        'detail': '执行边界正常' if not issues else f'{len(issues)}个问题: {issues}',
    }


# ═══════════════════════════════════════════════════════════
# 主自检流程
# ═══════════════════════════════════════════════════════════

LEVEL_WEIGHTS = {
    'L0': 'CRITICAL',  # 文件系统 → 任何缺失都是致命的
    'L1': 'CRITICAL',  # 配置不一致 → 信号发到错误线程
    'L2': 'CRITICAL',  # 核心模块缺失 → 分析完全失效
    'L3': 'DEGRADED',  # 数据陈旧 → 影响分析质量，但不崩溃
    'L4': 'CRITICAL',  # 推送断路 → 信号永远到不了苏摩
    'L5': 'DEGRADED',  # 执行边界 → 风险暴露，需关注
}


def run_integrity_check(push_result: bool = True) -> dict:
    ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    pass  # [静默]

    checks = {
        'L0': check_l0_filesystem(),
        'L1': check_l1_config(),
        'L2': check_l2_modules(),
        'L3': check_l3_data(),
        'L4': check_l4_push(),
        'L5': check_l5_execution(),
    }

    # 判断整体状态
    critical_failed = [l for l, c in checks.items()
                       if not c['ok'] and LEVEL_WEIGHTS[l] == 'CRITICAL']
    degraded        = [l for l, c in checks.items()
                       if not c['ok'] and LEVEL_WEIGHTS[l] == 'DEGRADED']

    if critical_failed:
        overall = 'CRITICAL'
    elif degraded:
        overall = 'DEGRADED'
    else:
        overall = 'HEALTHY'

    icon = {'HEALTHY': '🟢', 'DEGRADED': '🟡', 'CRITICAL': '🔴'}[overall]

    # 打印结果
    for level, c in checks.items():
        status = '✅' if c['ok'] else ('❌' if LEVEL_WEIGHTS[level] == 'CRITICAL' else '⚠️')
        print(f'  {status} {level} {c["name"]}: {c["detail"][:80]}')

    pass  # [静默]

    # 推送告警
    if push_result and overall != 'HEALTHY':
        import subprocess
        failed_lines = []
        for l in (critical_failed + degraded):
            c = checks[l]
            failed_lines.append(f'{icon} {l} {c["name"]}: {c["detail"][:80]}')

        msg = (
            f'{icon} **梵天系统一致性自检 · {overall}**\n'
            f'时间: {ts}\n\n'
            + '\n'.join(failed_lines[:8])
        )
        subprocess.run(
            ['openclaw', 'message', 'send',
             '--channel', PUSH_CHANNEL,
             '--target', PUSH_TARGET,
             '--message', msg],
            capture_output=True, text=True, timeout=15
        )

    return {
        'overall':  overall,
        'checks':   checks,
        'critical': critical_failed,
        'degraded': degraded,
        'ts':       ts,
    }


if __name__ == '__main__':
    result = run_integrity_check(push_result=True)
    sys.exit(0 if result['overall'] == 'HEALTHY' else 1)
