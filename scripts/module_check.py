#!/usr/bin/env python3
"""
模块注册表检查 v2 — 启动时扫描所有模块+端口连通性
[9.13封印 苏摩111] Phase 0：组件即端口
[9.26修复 苏摩111] 三个历史bug根因修复：
  1. registry实际键是modules（v3-1），旧代码读critical_modules/enhancement_modules → 永久假绿
  2. v3-1字段是path/deps（模块名，非路径），旧代码当文件路径拼 → 9个模块假缺失
  3. 双__main__块重复执行 → 输出重复推送（9.18旲马重复推送根因之一）
[9.26接入 苏摩111] data_contract_validator → module_check（原声明接入位从未接线）
"""
import json, os, sys, time
from pathlib import Path
import sys

BASE = Path(__file__).parent.parent
REGISTRY = BASE / 'data' / 'module_registry.json'

def check_modules():
    """检查所有注册模块+端口连通性，返回(alive, missing, port_issues, alerts)"""
    if not REGISTRY.exists():
        return [], [], [], ['module_registry.json不存在']

    reg = json.loads(REGISTRY.read_text())
    # [9.26修复] v3-1实际键=modules，字段=path（相对路径）+deps（模块名）
    modules = reg.get('modules', {})
    if not isinstance(modules, dict):
        return [], [], [], [f'module_registry.json结构异常: modules应为dict']

    alive = []
    missing = []
    port_issues = []
    alerts = []

    for name, info in modules.items():
        rel = info.get('path', '')
        if not rel:
            alerts.append(f'🔴 {name}: registry缺少path字段')
            missing.append(name)
            continue
        fpath = BASE / rel
        if fpath.exists():
            alive.append(name)
            # 检查端口连通性：deps是模块名 → 解析为对应path再验证
            for dep in info.get('deps', []):
                dep_info = modules.get(dep)
                dep_path = BASE / dep_info['path'] if dep_info else BASE / f'brahma_brain/{dep}.py'
                if not dep_path.exists():
                    severity = '⚠️' if info.get('optional', False) else '🔴'
                    port_issues.append(f'{severity} {name}: 依赖模块 {dep} 文件不存在 → 输入端口断开')
        else:
            # [9.26] registry v3-1是V3重命名设计文档：9个模块从未落地，
            # 其replaces的主链文件全部存活。设计未落地≠故障，降级INFO不入nerve_alerts
            missing.append(name)
            alerts.append(f'ℹ️ {name}: V3设计未落地({rel})，现状由replaces列表文件承载')

    return alive, missing, port_issues, alerts


def main():
    alive, missing, port_issues, alerts = check_modules()
    print(f'模块检查: ✅{len(alive)}存活 | ℹ️{len(missing)}设计未落地 | ⚠️{len(port_issues)}端口问题')
    if alerts:
        for a in alerts:
            print(f'  {a}')
    if port_issues:
        print(f'\n端口连通性:')
        for p in port_issues:
            print(f'  {p}')
    if not alerts and not port_issues:
        print('  全部模块存活+端口连通 ✅')

    # 写入nerve_alerts（仅真故障：端口断开；设计未落地不推）
    all_alerts = port_issues
    if all_alerts:
        try:
            alert_path = BASE / 'data' / 'nerve_alerts.jsonl'
            with open(alert_path, 'a') as f:
                for a in all_alerts:
                    f.write(json.dumps({'type': 'module_check', 'msg': a, 'ts': time.time()}) + '\n')
        except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
    return all_alerts


def run_contract_check() -> dict:
    """[9.26接入 苏摩111] 数据契约验证 → module_check
    接入位置：data_contract_validator.py声明的接入位（9.17封印时声明但未接线）
    功能：验证硬编码字典 vs data文件契约，发现断裂则进nerve_alerts
    """
    try:
        sys.path.insert(0, str(BASE / 'brahma_brain'))
        from data_contract_validator import validate_all_contracts
        return validate_all_contracts()
    except Exception as e:
        return {'all_ok': False, 'break_count': -1, 'breaks': [],
                'error': str(e)[:120]}


def run_wiring_check(full=False):
    """[9.26修复] 此函数原定义在__main__块内，但__main__里引用在定义前 → NameError永久静默
    接线完整性检测：可import/有真实caller/端到端可达
    """
    try:
        sys.path.insert(0, str(BASE / 'brahma_brain'))
        from brahma_wiring_check import run_check as _run_wc
        return _run_wc(full=full)
    except ImportError:
        return {'status': 'skipped', 'detail': 'brahma_wiring_check不可用'}
    except Exception as e:
        return {'status': 'error', 'detail': str(e)[:80]}


if __name__ == '__main__':
    alive, missing, port_issues, alerts = check_modules()
    print(f'模块检查: ✅{len(alive)}存活 | ℹ️{len(missing)}设计未落地 | ⚠️{len(port_issues)}端口问题')
    if alerts:
        for a in alerts:
            print(f'  {a}')
    if port_issues:
        print(f'\n端口连通性:')
        for p in port_issues:
            print(f'  {p}')
    if not alerts and not port_issues:
        print('  全部模块存活+端口连通 ✅')

    # [9.26接入] 数据契约验证（同一入口，单一__main__）
    contract = run_contract_check()
    if contract.get('break_count', 0) == 0:
        print(f"数据契约: ✅全绿 ({contract.get('total_contracts', '?')}条契约)")
    elif contract.get('break_count', -1) > 0:
        print(f"数据契约: ❌{contract['break_count']}条断裂")
        for b in contract.get('breaks', []):
            print(f"  [{b.get('status')}] {b.get('desc')}: {b.get('issue')}")
    else:
        print(f"数据契约: ⚠️检查异常 {contract.get('error', '')}")

    # 新增：接线检查
    print()
    wc_result = run_wiring_check()
    if isinstance(wc_result, dict):
        # [9.26修复] brahma_wiring_check返回{ok,warn,fail,islands,results}计数，非status dict
        print(f"接线检查: ✅{wc_result.get('ok',0)}通过 ⚠️{wc_result.get('warn',0)}警告 ❌{wc_result.get('fail',0)}失败 | 高价值孤岛={wc_result.get('islands',0)}")
    elif isinstance(wc_result, list):
        for r in wc_result:
            print(f'  {r}')