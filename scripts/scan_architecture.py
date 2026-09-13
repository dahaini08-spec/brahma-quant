#!/usr/bin/env python3
"""
scan_architecture.py — 梵天架构扫描器
[果蝇架构Phase 3] 从mattpocock/skills借鉴 improve-codebase-architecture

扫描浅模块+沉默错误+端口缺失，输出候选改进列表。
建议每周跑一次，定期发现架构债。

用法：
    python3 scripts/scan_architecture.py
    python3 scripts/scan_architecture.py --json  # JSON输出
"""
import json
import os
import sys
import re
from pathlib import Path

BASE = Path(__file__).parent.parent
BRAHMA_BRAIN = BASE / 'brahma_brain'
REGISTRY = BASE / 'data' / 'module_registry.json'

# ── 浅模块检测 ──────────────────────────────────────────────────

def scan_shallow_modules():
    """检测浅模块：行数多但端口声明少"""
    if not REGISTRY.exists():
        return [], ['module_registry.json不存在']

    reg = json.loads(REGISTRY.read_text())
    shallow = []

    for category in ['critical_modules', 'enhancement_modules']:
        for name, info in reg[category].items():
            fpath = BASE / info['file']
            if not fpath.exists():
                continue

            lines = sum(1 for _ in open(fpath, errors='ignore'))
            funcs = 0
            in_class = False
            for line in open(fpath, errors='ignore'):
                if re.match(r'^\s*def \w+', line):
                    funcs += 1
                elif re.match(r'^class \w+', line):
                    funcs += 1

            inputs = len(info.get('inputs', []))
            outputs = len(info.get('outputs', []))
            ports = inputs + outputs
            has_ports = ports > 0

            # 浅模块判定：>500行且0端口
            is_shallow = lines > 500 and not has_ports
            # 中度浅模块：>200行且0端口
            is_medium = 200 < lines <= 500 and not has_ports

            entry = {
                'name': name,
                'file': info['file'],
                'lines': lines,
                'funcs': funcs,
                'ports': ports,
                'inputs': inputs,
                'outputs': outputs,
                'invocation': info.get('invocation', '?'),
                'optional': info.get('optional', False),
                'verdict': 'shallow' if is_shallow else ('medium' if is_medium else 'deep'),
            }
            shallow.append(entry)

    return shallow, []

# ── 沉默错误检测 ──────────────────────────────────────────────────

def scan_silent_errors():
    """扫描except pass——沉默错误"""
    results = []
    for pyfile in sorted(BRAHMA_BRAIN.glob('*.py')):
        try:
            lines = open(pyfile, errors='ignore').readlines()
        except Exception:
            continue
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            # 匹配 except: pass / except Exception: pass / except ...: pass
            if re.match(r'^except\s*(\w+\s*(\(\s*\w+\s*\))?\s*)?:\s*pass\s*$', stripped):
                results.append({
                    'file': str(pyfile.relative_to(BASE)),
                    'line': i,
                    'code': stripped,
                })
    return results

# ── 端口缺失检测 ──────────────────────────────────────────────────

def scan_missing_ports():
    """检测有实现但无端口声明的模块"""
    if not REGISTRY.exists():
        return [], ['module_registry.json不存在']

    reg = json.loads(REGISTRY.read_text())
    missing = []

    for category in ['critical_modules', 'enhancement_modules']:
        for name, info in reg[category].items():
            inputs = len(info.get('inputs', []))
            outputs = len(info.get('outputs', []))
            if inputs == 0 and outputs == 0:
                fpath = BASE / info['file']
                exists = fpath.exists()
                if exists:
                    lines = sum(1 for _ in open(fpath, errors='ignore'))
                    if lines > 100:  # 有实质实现但无端口
                        missing.append({
                            'name': name,
                            'file': info['file'],
                            'lines': lines,
                            'category': category,
                        })
    return missing, []

# ── 孤儿模块检测 ──────────────────────────────────────────────────

def scan_orphans():
    """检测孤儿模块（known_orphans中status=MISSING）"""
    if not REGISTRY.exists():
        return []
    reg = json.loads(REGISTRY.read_text())
    orphans = []
    for name, info in reg.get('known_orphans', {}).items():
        if info.get('status') == 'MISSING':
            orphans.append({
                'name': name,
                'imported_by': info.get('imported_by', '?'),
                'fallback': info.get('fallback', 'skip'),
            })
    return orphans

# ── 主流程 ─────────────────────────────────────────────────────

def main():
    shallow, s_errors = scan_shallow_modules()
    silent_errors = scan_silent_errors()
    missing_ports, mp_errors = scan_missing_ports()
    orphans = scan_orphans()

    # 分类
    deep = [m for m in shallow if m['verdict'] == 'deep']
    shallow_only = [m for m in shallow if m['verdict'] == 'shallow']
    medium = [m for m in shallow if m['verdict'] == 'medium']

    if '--json' in sys.argv:
        output = {
            'summary': {
                'total_modules': len(shallow),
                'deep': len(deep),
                'medium': len(medium),
                'shallow': len(shallow_only),
                'silent_errors': len(silent_errors),
                'missing_ports': len(missing_ports),
                'orphans': len(orphans),
            },
            'shallow_modules': shallow_only,
            'medium_modules': medium,
            'deep_modules': deep,
            'silent_errors': silent_errors,
            'missing_ports': missing_ports,
            'orphans': orphans,
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return

    # 文本报告
    print('═' * 60)
    print('  梵天架构扫描报告')
    print('═' * 60)
    print(f'  模块总数: {len(shallow)} | 深: {len(deep)} | 中: {len(medium)} | 浅: {len(shallow_only)}')
    print(f'  沉默错误: {len(silent_errors)}处 | 端口缺失: {len(missing_ports)} | 孤儿: {len(orphans)}')
    print()

    # 浅模块
    if shallow_only:
        print('─' * 60)
        print('🔴 浅模块（>500行无端口声明）：')
        print('─' * 60)
        for m in sorted(shallow_only, key=lambda x: -x['lines']):
            print(f"  {m['name']:30s} | {m['lines']:5d}行 {m['funcs']:2d}函数 0端口 → 建议加端口声明")
        print()

    # 中度浅模块
    if medium:
        print('─' * 60)
        print('⚠️ 中度浅模块（200-500行无端口声明）：')
        print('─' * 60)
        for m in sorted(medium, key=lambda x: -x['lines']):
            print(f"  {m['name']:30s} | {m['lines']:5d}行 {m['funcs']:2d}函数 0端口")
        print()

    # 深模块
    if deep:
        print('─' * 60)
        print(f'✅ 深模块（有端口+有实现）：{len(deep)}个')
        print('─' * 60)
        for m in sorted(deep, key=lambda x: -x['ports']):
            print(f"  {m['name']:30s} | {m['lines']:5d}行 in={m['inputs']} out={m['outputs']} 端口={m['ports']}")
        print()

    # 沉默错误
    if silent_errors:
        print('─' * 60)
        print(f'⚠️ 沉默错误（except pass）：{len(silent_errors)}处')
        print('─' * 60)
        by_file = {}
        for e in silent_errors:
            f = e['file']
            if f not in by_file:
                by_file[f] = []
            by_file[f].append(e)
        for f, errs in sorted(by_file.items(), key=lambda x: -len(x[1])):
            print(f"  {f}: {len(errs)}处")
            for e in errs[:3]:
                print(f"    L{e['line']}: {e['code']}")
            if len(errs) > 3:
                print(f"    ... 还有{len(errs)-3}处")
        print()

    # 端口缺失
    if missing_ports:
        print('─' * 60)
        print(f'⚠️ 端口缺失（>100行无端口）：{len(missing_ports)}个')
        print('─' * 60)
        for m in missing_ports:
            print(f"  {m['name']:30s} | {m['file']} ({m['lines']}行)")
        print()

    # 孤儿
    if orphans:
        print('─' * 60)
        print(f'⚠️ 孤儿模块：{len(orphans)}个')
        print('─' * 60)
        for o in orphans:
            print(f"  {o['name']:30s} | imported_by={o['imported_by']} fallback={o['fallback']}")
        print()

    # 建议
    print('═' * 60)
    print('  改进建议')
    print('═' * 60)
    if shallow_only:
        print(f'  1. 给{len(shallow_only)}个浅模块加端口声明')
    if silent_errors:
        print(f'  2. 清理{len(silent_errors)}处except pass → 改为stderr告警')
    if missing_ports:
        print(f'  3. 给{len(missing_ports)}个>100行无端口模块补充端口')
    if orphans:
        print(f'  4. 清理{len(orphans)}个孤儿模块的import引用')
    if not shallow_only and not silent_errors and not missing_ports and not orphans:
        print('  ✅ 架构健康，无需改进')
    print()

if __name__ == '__main__':
    main()
