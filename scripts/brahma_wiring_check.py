#!/usr/bin/env python3
"""
brahma_wiring_check.py — 梵天代码接入验证（最小代价版）
[2026-08-31 苏摩111封印]

用Python内置AST库实现graphify核心功能：
  1. 哪些文件import了某个模块
  2. 新模块是否已接入主链路
  3. 死代码检测

用法:
  python3 scripts/brahma_wiring_check.py --check war_field_report
  python3 scripts/brahma_wiring_check.py --check regime_early_warning
  python3 scripts/brahma_wiring_check.py --dead-code
  python3 scripts/brahma_wiring_check.py --full
"""

import ast, os, sys, argparse
from pathlib import Path

ROOT = Path(__file__).parent.parent
BRAIN = ROOT / 'brahma_brain'
SCRIPTS = ROOT / 'scripts'

MAIN_CHAIN = [
    'brahma_analysis_runner.py',
    'brahma_full_report.py',
    'auto_executor.py',
    'brahma_core.py',
    'signal_selector.py',
]


def get_py_files(dirs):
    files = []
    for d in dirs:
        for f in Path(d).rglob('*.py'):
            if 'venv' not in str(f) and '__pycache__' not in str(f):
                files.append(f)
    return files


def get_imports(filepath):
    """提取文件中所有import的模块名"""
    try:
        with open(filepath) as f:
            src = f.read()
        tree = ast.parse(src)
    except:
        return []
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name.split('.')[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module.split('.')[0])
    return imports


def get_defined_functions(filepath):
    """提取文件中定义的所有函数名"""
    try:
        with open(filepath) as f:
            src = f.read()
        tree = ast.parse(src)
    except:
        return []
    return [node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]


def check_wiring(module_name):
    """检查模块是否已接入主链路"""
    print(f'\n=== 接入验证: {module_name} ===')
    all_files = get_py_files([BRAIN, SCRIPTS])
    found_in = []
    for f in all_files:
        imports = get_imports(f)
        # 也检查文件内容中的字符串引用
        try:
            content = f.read_text()
        except:
            content = ''
        if module_name in imports or module_name in content:
            found_in.append(str(f.relative_to(ROOT)))
    
    main_chain_hits = [f for f in found_in if any(m in f for m in MAIN_CHAIN)]
    
    print(f'  引用位置 ({len(found_in)}处):')
    for f in found_in[:10]:
        tag = ' ← 主链路✅' if any(m in f for m in MAIN_CHAIN) else ''
        print(f'    {f}{tag}')
    
    if main_chain_hits:
        print(f'  ✅ 已接入主链路: {main_chain_hits}')
    else:
        print(f'  ⚠️  未在主链路中找到引用 — 需要手动接入')
    
    return len(found_in) > 0


def dead_code_check():
    """简单死代码检测：定义了但从未被其他文件引用的函数"""
    print('\n=== 死代码检测 ===')
    all_files = get_py_files([BRAIN, SCRIPTS])
    
    # 收集所有定义
    all_defs = {}
    for f in all_files:
        funcs = get_defined_functions(f)
        for fn in funcs:
            all_defs[fn] = str(f.relative_to(ROOT))
    
    # 收集所有调用（简单文本搜索）
    all_calls = set()
    for f in all_files:
        try:
            content = f.read_text()
            for fn in all_defs:
                if fn + '(' in content:
                    all_calls.add(fn)
        except:
            pass
    
    uncalled = {fn: path for fn, path in all_defs.items()
                if fn not in all_calls
                and not fn.startswith('_')
                and fn not in ('main', 'test', 'setup')}
    
    print(f'  定义函数总数: {len(all_defs)}')
    print(f'  被调用函数: {len(all_calls)}')
    print(f'  疑似未使用: {len(uncalled)}')
    if uncalled:
        for fn, path in list(uncalled.items())[:10]:
            print(f'    {fn}() — {path}')


def full_check():
    """全量检查今日新增模块"""
    new_modules = ['war_field_report', 'regime_early_warning', 'calc_war_field_alignment', 'get_war_field_position']
    results = {}
    for m in new_modules:
        results[m] = check_wiring(m)
    
    print('\n=== 汇总 ===')
    for m, found in results.items():
        status = '✅ 已引用' if found else '❌ 未引用'
        print(f'  {m}: {status}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', help='检查指定模块接入情况')
    parser.add_argument('--dead-code', action='store_true')
    parser.add_argument('--full', action='store_true')
    args = parser.parse_args()
    
    if args.check:
        check_wiring(args.check)
    elif args.dead_code:
        dead_code_check()
    elif args.full:
        full_check()
    else:
        full_check()
