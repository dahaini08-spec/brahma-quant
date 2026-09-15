#!/usr/bin/env python3
"""
ast_comment_cleaner.py — AST级别安全注释清理工具
设计院 2026-09-01 苏摩111

原理：
  1. 先用ast.parse()验证原文件语法正确
  2. 逐行扫描，识别"纯装饰性注释"（不含实质信息）
  3. 只删除安全的注释行，保留所有代码行和有意义注释
  4. 清理后再次ast.parse()验证语法无误
  5. 输出统计报告

安全规则（以下注释行会被删除）：
  - 纯分隔线: # ═══════ / # ─────── (8个以上重复字符)
  - 纯结束标记: # ══ [END xxx] ══ / # ── [xxx END] ──
  - 空注释: # (仅一个#后跟空格或结束)
  - [CLEANED xxxx] 已删除引用记录

保留（不删除）：
  - 所有代码行（任何非注释内容）
  - 有实质说明的注释（包含中文/英文描述）
  - 封印说明、WR铁证、设计决策等有价值注释
  - 函数/类文档注释
  - TODO/FIXME等工作注释
"""

import ast
import re
import sys
from pathlib import Path


# 安全删除的注释模式（必须整行匹配）
SAFE_DELETE_PATTERNS = [
    # 纯分隔线（8个以上重复的═─=─符号）
    re.compile(r'^\s*#\s*[═─=\-]{8,}\s*$'),
    # END标记行: # ══ [END xxx] ══ 或 # ── [xxx END] ──
    re.compile(r'^\s*#\s*[══─]+\s*\[END\s+[^\]]+\]\s*[══─]*\s*$'),
    re.compile(r'^\s*#\s*[══─]+\s*\[[^\]]+\s+END\]\s*[══─]*\s*$'),
    # 纯空注释: 只有 # 或 # (空格)
    re.compile(r'^\s*#\s*$'),
    # [CLEANED YYYY-MM-DD] 已清理引用
    re.compile(r'^\s*#\s*\[CLEANED\s+\d{4}-\d{2}-\d{2}\].*$'),
]


def is_safe_to_delete(line: str) -> bool:
    """判断一行注释是否可以安全删除"""
    stripped = line.rstrip('\n')
    for pattern in SAFE_DELETE_PATTERNS:
        if pattern.match(stripped):
            return True
    return False


def clean_file(filepath: str, dry_run: bool = False) -> dict:
    """
    清理单个文件的装饰性注释
    返回统计信息
    """
    path = Path(filepath)
    content = path.read_text(encoding='utf-8')
    lines = content.splitlines(keepends=True)

    # Step1: 验证原始语法
    try:
        ast.parse(content)
    except SyntaxError as e:
        return {'error': f'原文件语法错误: {e}', 'file': filepath}

    # Step2: 逐行过滤
    deleted_lines = []
    kept_lines = []
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        # 只处理注释行
        if stripped.startswith('#') and is_safe_to_delete(line):
            deleted_lines.append((i, line.rstrip()))
        else:
            kept_lines.append(line)

    new_content = ''.join(kept_lines)

    # Step3: 验证清理后语法
    try:
        ast.parse(new_content)
    except SyntaxError as e:
        return {'error': f'清理后语法错误(已跳过): {e}', 'file': filepath, 'deleted': 0}

    # Step4: 写入（非dry_run时）
    if not dry_run and deleted_lines:
        path.write_text(new_content, encoding='utf-8')

    return {
        'file': filepath,
        'original_lines': len(lines),
        'new_lines': len(kept_lines),
        'deleted': len(deleted_lines),
        'deleted_samples': [f"  行{l[0]:4d}: {l[1][:60]}" for l in deleted_lines[:5]],
        'dry_run': dry_run,
    }


def main():
    targets = [
        'brahma_brain/brahma_core.py',
        'brahma_brain/brahma_analysis_runner.py',
        'brahma_brain/brahma_core_block_a.py',
        'brahma_brain/brahma_core_block_b.py',
        'brahma_brain/brahma_core_block_c.py',
        'scripts/auto_executor.py',
        'scripts/rsi_structure_watcher.py',
        'brahma_brain/llm_council_bridge.py',
        'brahma_brain/smc_engine.py',
        'brahma_brain/fangcang_engine.py',
    ]

    dry_run = '--dry-run' in sys.argv

    if dry_run:
        print("🔍 DRY RUN模式 — 不写入文件\n")
    else:
        print("✂️  执行模式 — 将写入文件\n")

    total_deleted = 0
    total_original = 0

    for target in targets:
        if not Path(target).exists():
            print(f"  SKIP (不存在): {target}")
            continue
        result = clean_file(target, dry_run=dry_run)
        if 'error' in result:
            print(f"  ❌ {target}: {result['error']}")
            continue
        deleted = result['deleted']
        original = result['original_lines']
        new = result['new_lines']
        total_deleted += deleted
        total_original += original
        if deleted > 0:
            print(f"  ✅ {Path(target).name}: {original}→{new}行 (-{deleted}行)")
            for s in result['deleted_samples']:
                print(f"      {s}")
        else:
            print(f"  ➖ {Path(target).name}: 无可删除注释")

    print(f"\n{'DRY RUN ' if dry_run else ''}总计: -{total_deleted}行")
    print(f"原总行数: {total_original} → 新总行数: {total_original - total_deleted}")


if __name__ == '__main__':
    main()
