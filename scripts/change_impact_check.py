#!/usr/bin/env python3
"""
change_impact_check.py — Python版Ripple：改动影响链分析
[设计院封印 2026-10-14 苏摩111]

接入位置：
  1. pre-commit hook（git add *.py 后自动运行）
  2. 手动调用：python3 scripts/change_impact_check.py <file1.py> [file2.py ...]

蒸馏来源：github.com/nsodhi-cmu/claude-code-ripple
  Ripple = 每次diff后画出受影响函数/调用链/需审查连接
  梵天Python版 = ast.parse静态分析，无需外部依赖

输出格式：
  修改文件: smc_engine.py
  修改函数: find_fvg()
  影响链:
    → analyze_smc()        [smc_engine.py]
    → analyze_smc_multi_tf() [smc_engine.py]
    → run_smc_resonance()  [smc_engine.py]
  ⚠️ 请确认以上调用方兼容修改
"""
import ast
import sys
from pathlib import Path

BASE = Path(__file__).parent.parent
SCAN_DIRS = [
    BASE / 'brahma_brain',
    BASE / 'scripts',
]
# 扫描时跳过的目录（太大/无关）
SKIP_DIRS = {'venv', 'pip_cache', '__pycache__', '.git', 'node_modules', 'wheelhouse'}

# 预构建全库函数调用映射（{func_name: [(file, caller_func), ...]}）
def build_call_index(scan_dirs):
    """扫描所有Python文件，构建被调用函数→调用方的反向索引"""
    index = {}  # {callee_name: [(filepath, caller_name), ...]}
    for scan_dir in scan_dirs:
        if not scan_dir.exists():
            continue
        for pyfile in scan_dir.rglob('*.py'):
            # 跳过无关目录
            if any(skip in pyfile.parts for skip in SKIP_DIRS):
                continue
            try:
                source = pyfile.read_text(encoding='utf-8', errors='ignore')
                tree = ast.parse(source)
            except Exception:
                continue

            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                caller_name = node.name
                for child in ast.walk(node):
                    if not isinstance(child, ast.Call):
                        continue
                    callee = None
                    if isinstance(child.func, ast.Name):
                        callee = child.func.id
                    elif isinstance(child.func, ast.Attribute):
                        callee = child.func.attr
                    if callee:
                        if callee not in index:
                            index[callee] = []
                        entry = (str(pyfile.relative_to(BASE)), caller_name)
                        if entry not in index[callee]:
                            index[callee].append(entry)
    return index


def get_modified_functions(filepath):
    """提取文件中定义的所有函数名"""
    try:
        source = Path(filepath).read_text(encoding='utf-8', errors='ignore')
        tree = ast.parse(source)
    except Exception:
        return []
    return [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def analyze_impact(changed_files, max_depth=2):
    """分析改动文件的影响链，返回报告字符串"""
    print(f"[change_impact_check] 扫描全库调用索引...", file=sys.stderr)
    index = build_call_index(SCAN_DIRS)
    print(f"[change_impact_check] 索引完成，覆盖 {len(index)} 个函数名", file=sys.stderr)

    report_lines = []
    has_impact = False

    for cf in changed_files:
        cf_path = Path(cf)
        if not cf_path.exists():
            continue
        rel_path = cf_path.relative_to(BASE) if cf_path.is_absolute() else cf_path

        funcs = get_modified_functions(cf)
        if not funcs:
            continue

        report_lines.append(f"\n📝 修改文件: {rel_path}")
        file_has_impact = False

        for func in funcs:
            callers = index.get(func, [])
            if not callers:
                continue
            file_has_impact = True
            has_impact = True
            report_lines.append(f"  🔧 {func}() — {len(callers)}个调用方需审查:")
            for caller_file, caller_func in callers[:10]:  # 最多显示10个
                report_lines.append(f"     └─ {caller_func}()  [{caller_file}]")
            if len(callers) > 10:
                report_lines.append(f"     └─ ...还有{len(callers)-10}个调用方")

        if not file_has_impact:
            report_lines.append(f"  ✅ 无外部调用方（独立函数，无影响链）")

    if not report_lines:
        return "✅ 无Python文件变更需要分析"

    header = "\n" + "="*60
    header += "\n🔍 change_impact_check — 改动影响链分析"
    header += "\n" + "="*60
    footer = "\n" + "="*60
    if has_impact:
        footer += "\n⚠️  请确认以上调用方与修改兼容后再commit"
    else:
        footer += "\n✅  无影响链，可安全commit"
    footer += "\n" + "="*60

    return header + "\n".join(report_lines) + footer


def main():
    # 从命令行参数获取文件列表，或从stdin读取（git pre-commit模式）
    if len(sys.argv) > 1:
        changed_files = sys.argv[1:]
    else:
        # pre-commit模式：读取git staged的Python文件
        import subprocess
        result = subprocess.run(
            ['git', 'diff', '--cached', '--name-only', '--diff-filter=ACM'],
            capture_output=True, text=True,
            cwd=BASE
        )
        changed_files = [
            str(BASE / f) for f in result.stdout.strip().split('\n')
            if f.endswith('.py') and f.strip()
        ]

    if not changed_files:
        print("✅ 无Python文件变更")
        return 0

    report = analyze_impact(changed_files)
    print(report)
    return 0


if __name__ == '__main__':
    sys.exit(main())
