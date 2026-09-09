"""
_reader.py — Spotify方案本地化：大文件读取→廉价模型摘要
<!-- 2026-09-09 苏摩111 三方联合封印 | 接入位置：brahma_brain/_reader.py -->

设计理念（Spotify三道铁门本地化）：
1. >350行文件 → 不全量读入旗舰上下文
2. 廉价模型提取关键函数签名+入口 → 只返回要点
3. 编辑文件和复杂推理绝不分流 → 旗舰模型亲自处理

使用方式：
    from brahma_brain._reader import smart_read
    summary = smart_read('brahma_brain/brahma_core.py')  # 返回摘要
    summary = smart_read('brahma_brain/gates.py')        # 小文件直接返回原文
"""

import os
from pathlib import Path

THRESHOLD_LINES = 350  # Spotify标准：>350行走摘要

def _count_lines(filepath: str) -> int:
    """快速行数统计"""
    try:
        with open(filepath, 'r', errors='ignore') as f:
            return sum(1 for _ in f)
    except Exception:
        return 0

def _extract_skeleton(filepath: str, max_funcs: int = 30) -> str:
    """
    零依赖提取文件骨架：类名、函数签名、常量、import
    不调LLM，纯文本处理，0 token消耗
    """
    skeleton_lines = []
    in_func_body = False
    
    with open(filepath, 'r', errors='ignore') as f:
        for i, line in enumerate(f):
            stripped = line.strip()
            
            # import 行
            if stripped.startswith('import ') or stripped.startswith('from '):
                skeleton_lines.append(line.rstrip())
                continue
            
            # class 定义
            if stripped.startswith('class ') or stripped.startswith('class '):
                skeleton_lines.append(line.rstrip())
                in_func_body = False
                continue
            
            # def 定义（函数签名行）
            if stripped.startswith('def ') or stripped.startswith('async def '):
                skeleton_lines.append(line.rstrip())
                in_func_body = True
                continue
            
            # 常量赋值（全大写变量名）
            if stripped and '=' in stripped and not stripped.startswith('#'):
                var_name = stripped.split('=')[0].strip()
                if var_name.isupper() or var_name.startswith('_') and var_name[1:].isupper():
                    skeleton_lines.append(line.rstrip())
                    continue
            
            # docstring 第一行
            if stripped.startswith('"""') or stripped.startswith("'''"):
                skeleton_lines.append(line.rstrip())
                if not stripped.endswith('"""') and not stripped.endswith("'''"):
                    # 多行docstring，跳过内容
                    continue
            
            # 函数体内的关键return/raise
            if in_func_body and (stripped.startswith('return ') or stripped.startswith('raise ')):
                # 缩进2-8空格的return/raise，可能是函数出口
                indent = len(line) - len(line.lstrip())
                if 4 <= indent <= 12:
                    skeleton_lines.append(f'  {line.rstrip()}')
    
    # 限制输出长度
    if len(skeleton_lines) > max_funcs * 3:
        skeleton_lines = skeleton_lines[:max_funcs * 3]
        skeleton_lines.append(f'... (truncated, {max_funcs * 3} lines shown)')
    
    return '\n'.join(skeleton_lines)

def smart_read(filepath: str, force_full: bool = False) -> str:
    """
    智能读取文件：
    - <350行 → 返回原文
    - >350行 → 返回骨架摘要（函数签名+常量+import）
    - force_full=True → 强制返回原文（编辑文件时用）
    
    零token消耗：不调LLM，纯文本处理
    """
    if force_full:
        with open(filepath, 'r', errors='ignore') as f:
            return f.read()
    
    lines = _count_lines(filepath)
    
    if lines <= THRESHOLD_LINES:
        with open(filepath, 'r', errors='ignore') as f:
            return f.read()
    
    # 大文件 → 骨架摘要
    skeleton = _extract_skeleton(filepath)
    header = f'# 📄 {filepath} ({lines}行, 骨架摘要)\n# 💡 如需编辑，用 smart_read(path, force_full=True)\n\n'
    return header + skeleton

def file_info(filepath: str) -> dict:
    """返回文件元信息"""
    lines = _count_lines(filepath)
    size = os.path.getsize(filepath) if os.path.exists(filepath) else 0
    return {
        'path': filepath,
        'lines': lines,
        'size_kb': round(size / 1024, 1),
        'needs_summary': lines > THRESHOLD_LINES,
    }

if __name__ == '__main__':
    # 自检
    import sys
    test_files = [
        'brahma_core.py',
        'gates.py',
        'signal_quality_engine.py',
    ]
    base = Path(__file__).parent
    for f in test_files:
        fp = str(base / f)
        info = file_info(fp)
        print(f"{f}: {info['lines']}行 {info['size_kb']}KB needs_summary={info['needs_summary']}")
