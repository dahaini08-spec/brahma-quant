#!/usr/bin/env python3
"""
模块注册表检查 v2 — 启动时扫描所有模块+端口连通性
[9.13封印 苏摩111] Phase 0：组件即端口
"""
import json, os, sys, time
from pathlib import Path

BASE = Path(__file__).parent.parent
REGISTRY = BASE / 'data' / 'module_registry.json'

def check_modules():
    """检查所有注册模块+端口连通性，返回(alive, missing, port_issues, alerts)"""
    if not REGISTRY.exists():
        return [], [], [], ['module_registry.json不存在']

    reg = json.loads(REGISTRY.read_text())
    alive = []
    missing = []
    port_issues = []
    alerts = []

    for category in ['critical_modules', 'enhancement_modules']:
        for name, info in reg.get(category, {}).items():
            fpath = BASE / info['file']
            if fpath.exists():
                alive.append(name)
                # 检查端口连通性：deps中的文件是否存在
                deps = info.get('deps', [])
                for dep in deps:
                    dep_path = BASE / dep
                    if not dep_path.exists():
                        optional = info.get('optional', False)
                        severity = '⚠️' if optional else '🔴'
                        port_issues.append(f'{severity} {name}: 依赖 {dep} 不存在 → 输入端口断开')
            else:
                missing.append(name)
                optional = info.get('optional', False)
                severity = '⚠️' if optional else '🔴'
                alerts.append(f'{severity} {name}: {info["file"]} 不存在 (被{info.get("imported_by","?")}引用)')

    return alive, missing, port_issues, alerts


def main():
    alive, missing, port_issues, alerts = check_modules()
    print(f'模块检查: ✅{len(alive)}存活 | ❌{len(missing)}缺失 | ⚠️{len(port_issues)}端口问题')
    if alerts:
        for a in alerts:
            print(f'  {a}')
    if port_issues:
        print(f'\n端口连通性:')
        for p in port_issues:
            print(f'  {p}')
    if not alerts and not port_issues:
        print('  全部模块存活+端口连通 ✅')

    # 写入nerve_alerts
    all_alerts = alerts + port_issues
    if all_alerts:
        try:
            alert_path = BASE / 'data' / 'nerve_alerts.jsonl'
            with open(alert_path, 'a') as f:
                for a in all_alerts:
                    f.write(json.dumps({'type': 'module_check', 'msg': a, 'ts': time.time()}) + '\n')
        except Exception:
            pass


if __name__ == '__main__':
    main()
