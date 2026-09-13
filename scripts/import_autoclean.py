#!/usr/bin/env python3
"""
import_autoclean.py — 自动扫描无效import并注释清理
[果蝇架构Phase 3] 从"检测"升级为"自动修复"

功能：
  1. 扫描brahma_brain/*.py和scripts/*.py中所有import语句
  2. 对每个from X import Y，检查目标模块文件是否存在
  3. 不存在的import → 注释掉（保留代码逻辑，只禁用import行）
  4. 输出清理报告

用法：
    python3 scripts/import_autoclean.py          # 只扫描，不修改（dry-run）
    python3 scripts/import_autoclean.py --fix    # 实际注释掉无效import
"""
import re
import os
import sys
import importlib.util
from pathlib import Path

BASE = Path(__file__).parent.parent
SCAN_DIRS = [BASE / 'brahma_brain', BASE / 'scripts']
SKIP_DIRS = {'__pycache__', '_archived_20260826', 'archive', '_archive', '.git', 'venv'}

# 标准库/第三方包白名单——这些import不检查文件是否存在
_STDLIB_PREFIXES = {
    'os', 'sys', 're', 'json', 'time', 'math', 'asyncio', 'pathlib',
    'datetime', 'typing', 'collections', 'functools', 'itertools',
    'dataclasses', 'enum', 'abc', 'copy', 'io', 'logging',
    'subprocess', 'threading', 'queue', 'signal', 'traceback',
    'warnings', 'contextlib', 'inspect', 'importlib', 'hashlib',
    'base64', 'uuid', 'random', 'struct', 'array', 'bisect',
    'decimal', 'fractions', 'statistics', 'csv', 'pickle',
    'shutil', 'tempfile', 'glob', 'gzip', 'zipfile', 'tarfile',
    'socket', 'ssl', 'http', 'urllib', 'email', 'html', 'xml',
    'json', 'sqlite3', 'hashlib', 'hmac', 'secrets',
    '__future__', 'annotations',
    # 第三方包
    'numpy', 'pandas', 'requests', 'aiohttp', 'httpx',
    'mcp', 'dotenv', 'qdrant_client', 'sklearn', 'scipy',
    'yaml', 'toml', 'psutil', 'ccxt', 'websockets',
}

def is_stdlib(module_path):
    """判断是否标准库或第三方包"""
    top = module_path.split('.')[0]
    return top in _STDLIB_PREFIXES

def find_python_files():
    """找到所有要扫描的Python文件"""
    files = []
    for d in SCAN_DIRS:
        if not d.exists():
            continue
        for f in sorted(d.glob('*.py')):
            if any(skip in str(f) for skip in SKIP_DIRS):
                continue
            files.append(f)
    return files

def extract_imports(filepath):
    """提取文件中所有from X import Y语句"""
    imports = []
    lines = filepath.read_text(errors='ignore').split('\n')
    for i, line in enumerate(lines):
        stripped = line.strip()
        # 匹配 from X import Y 或 from brahma_brain.X import Y
        m = re.match(r'^(\s*)(from\s+([\w.]+)\s+import\s+(.+))$', stripped)
        if m:
            indent = m.group(1)
            full_stmt = m.group(2)
            module_path = m.group(3)
            import_names = m.group(4)
            imports.append({
                'line_no': i + 1,
                'indent': indent,
                'full': full_stmt,
                'module': module_path,
                'names': import_names,
                'raw_line': line,
            })
    return imports

def module_to_filepath(module_path, base_dir):
    """把模块路径转换为文件路径"""
    # from brahma_brain.xxx import yyy → brahma_brain/xxx.py
    # from xxx import yyy → xxx.py (relative to base_dir)
    parts = module_path.replace('.', '/')
    
    # 尝试直接路径
    candidates = [
        BASE / (parts + '.py'),
        BASE / 'brahma_brain' / (parts.split('brahma_brain/')[-1] + '.py'),
        base_dir / (parts.split('/')[-1] + '.py'),
    ]
    
    for c in candidates:
        if c.exists():
            return c
    return None

def is_import_valid(imp, base_dir):
    """检查import目标文件是否存在"""
    module_path = imp['module']
    
    # 标准库/第三方包直接跳过
    if is_stdlib(module_path):
        return True
    
    # brahma_brain.xxx → brahma_brain/xxx.py
    if module_path.startswith('brahma_brain.'):
        local = module_path[len('brahma_brain.'):]
        fp = BASE / 'brahma_brain' / (local.replace('.', '/') + '.py')
        return fp.exists()
    
    # scripts.xxx → scripts/xxx.py
    if module_path.startswith('scripts.'):
        local = module_path[len('scripts.'):]
        fp = BASE / 'scripts' / (local.replace('.', '/') + '.py')
        return fp.exists()
    
    # 无包前缀的本地模块——同时在brahma_brain/和scripts/中查找
    parts = module_path.replace('.', '/')
    candidates = [
        BASE / (parts + '.py'),
        BASE / 'brahma_brain' / (parts.split('/')[-1] + '.py'),
        BASE / 'scripts' / (parts.split('/')[-1] + '.py'),
        base_dir / (parts.split('/')[-1] + '.py'),
    ]
    for c in candidates:
        if c.exists():
            return True
    
    # 尝试importlib检查（处理已安装的包）
    try:
        spec = importlib.util.find_spec(module_path)
        if spec is not None:
            return True
    except (ImportError, ModuleNotFoundError, ValueError):
        pass
    
    return False

def is_in_try_block(lines, line_no):
    """检查某行是否在try块内"""
    for i in range(line_no - 2, max(0, line_no - 10), -1):
        if i >= len(lines):
            continue
        if 'try:' in lines[i] or 'try :' in lines[i]:
            return True
        # 如果遇到非try的缩进块头，说明不在try内
        if re.match(r'^\s*(if|for|while|with|def|class)\s', lines[i]):
            return False
    return False

def scan_imports():
    """扫描所有文件的import，返回无效import列表"""
    files = find_python_files()
    invalid_imports = []
    
    for filepath in files:
        rel_path = filepath.relative_to(BASE)
        lines = filepath.read_text(errors='ignore').split('\n')
        imports = extract_imports(filepath)
        
        for imp in imports:
            if not is_import_valid(imp, filepath.parent):
                in_try = is_in_try_block(lines, imp['line_no'])
                invalid_imports.append({
                    'file': str(rel_path),
                    'line_no': imp['line_no'],
                    'module': imp['module'],
                    'names': imp['names'],
                    'in_try': in_try,
                    'raw_line': imp['raw_line'],
                    'indent': imp['indent'],
                })
    
    return invalid_imports

def fix_imports(invalid_imports):
    """实际注释掉无效import"""
    # 按文件分组
    by_file = {}
    for imp in invalid_imports:
        f = imp['file']
        if f not in by_file:
            by_file[f] = []
        by_file[f].append(imp)
    
    fixed_count = 0
    for filepath_str, imps in by_file.items():
        filepath = BASE / filepath_str
        lines = filepath.read_text(errors='ignore').split('\n')
        
        # 从后往前注释（避免行号偏移）
        for imp in sorted(imps, key=lambda x: -x['line_no']):
            line_no = imp['line_no'] - 1  # 0-indexed
            old_line = lines[line_no]
            # 注释掉，保留原始内容
            lines[line_no] = f"{imp['indent']}# [import_autoclean] 模块不存在，已注释\n{imp['indent']}# {old_line.strip()}"
            fixed_count += 1
        
        filepath.write_text('\n'.join(lines))
        print(f"  ✅ {filepath_str}: 注释{len(imps)}处无效import")
    
    return fixed_count

def main():
    fix_mode = '--fix' in sys.argv
    
    print('═' * 60)
    print('  Import Autoclean — 无效import扫描清理')
    print('═' * 60)
    print(f"  模式: {'修复(--fix)' if fix_mode else '扫描(dry-run)'}")
    print()
    
    invalid = scan_imports()
    
    if not invalid:
        print('  ✅ 所有import有效，无需清理')
        return
    
    print(f'  发现 {len(invalid)} 处无效import:\n')
    
    # 按文件分组输出
    by_file = {}
    for imp in invalid:
        f = imp['file']
        if f not in by_file:
            by_file[f] = []
        by_file[f].append(imp)
    
    for filepath, imps in sorted(by_file.items()):
        print(f'  {filepath}:')
        for imp in imps:
            try_mark = 'try内' if imp['in_try'] else '❌ try外'
            print(f"    L{imp['line_no']:4d} {try_mark:6s} from {imp['module']} import {imp['names'][:50]}")
        print()
    
    if fix_mode:
        print('─' * 60)
        print('  开始修复...')
        fixed = fix_imports(invalid)
        print(f'\n  ✅ 共注释 {fixed} 处无效import')
    else:
        print(f'  dry-run模式，加 --fix 实际清理')
    
    print()
    print('═' * 60)

if __name__ == '__main__':
    main()
