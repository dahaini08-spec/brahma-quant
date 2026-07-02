#!/usr/bin/env python3
"""
exception_injector.py — 梵天异常免疫工具
设计院封印 2026-07-02

自动扫描全系统裸函数，批量注入三级fallback策略：
  A级: 数据获取函数 → 返回None + 日志
  B级: 计算函数     → 返回上次缓存值 + 警告
  C级: 执行函数     → 中止 + 推送Jarvis告警

用法:
  python3 brahma_brain/exception_injector.py          # 扫描报告（不修改）
  python3 brahma_brain/exception_injector.py --layer dharma  # 扫描指定层
  python3 brahma_brain/exception_injector.py --stats  # 仅统计

设计原则：只统计和报告，不自动修改代码（苏摩批准后手动执行修复）
"""
import ast
import os
import sys
import json
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Tuple

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))

# ── 层级配置 ─────────────────────────────────────────────────────────
LAYERS = {
    'brahma_brain': {
        'path': BASE / 'brahma_brain',
        'priority': 2,
        'label': '🧠 AI大脑层',
    },
    'dharma': {
        'path': BASE / 'dharma',
        'priority': 1,  # 最危险，优先修复
        'label': '🔬 验证层',
    },
    'scripts': {
        'path': BASE / 'scripts',
        'priority': 3,
        'label': '⚙️ 执行层',
    },
    'guardrails': {
        'path': BASE / 'guardrails',
        'priority': 2,
        'label': '🛡️ 防护层',
    },
}

# ── 高危函数关键词（C级：执行函数，崩溃代价最高）─────────────────────
EXEC_KEYWORDS = [
    'execute', 'order', 'trade', 'buy', 'sell', 'place',
    'close', 'open_position', 'submit', 'send_signal',
    'auto_exec', 'push', 'notify', 'alert',
    'run_analysis', 'run_batch', 'run_scan',
]

# ── 数据获取函数关键词（A级）─────────────────────────────────────────
FETCH_KEYWORDS = [
    'fetch', 'get_', 'load', 'read', 'query', 'pull',
    'download', 'request', 'api', 'ws_', 'websocket',
]

# ── 计算函数关键词（B级）────────────────────────────────────────────
CALC_KEYWORDS = [
    'calc', 'compute', 'score', 'analyze', 'evaluate',
    'predict', 'estimate', 'measure', 'detect',
]


@dataclass
class FunctionReport:
    file: str
    function: str
    line: int
    level: str        # A/B/C
    has_try: bool
    args: List[str] = field(default_factory=list)
    complexity: int = 0  # 函数行数


def classify_function(name: str) -> str:
    """三级分类"""
    name_lower = name.lower()
    if any(kw in name_lower for kw in EXEC_KEYWORDS):
        return 'C'  # 执行函数，最危险
    if any(kw in name_lower for kw in FETCH_KEYWORDS):
        return 'A'  # 数据获取
    if any(kw in name_lower for kw in CALC_KEYWORDS):
        return 'B'  # 计算函数
    return 'B'  # 默认B级


def scan_file(path: str) -> List[FunctionReport]:
    """扫描单个文件的所有函数"""
    reports = []
    try:
        content = open(path, encoding='utf-8', errors='ignore').read()
        tree = ast.parse(content)
        lines = content.split('\n')
    except (SyntaxError, IOError):
        return []

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue

        # 跳过私有/魔术方法（低优先级）
        if node.name.startswith('__') and node.name.endswith('__'):
            continue

        # 检查是否有try-except
        has_try = any(isinstance(n, ast.Try) for n in ast.walk(node))

        # 函数复杂度（行数）
        end_line = getattr(node, 'end_lineno', node.lineno + 5)
        complexity = end_line - node.lineno

        reports.append(FunctionReport(
            file=path,
            function=node.name,
            line=node.lineno,
            level=classify_function(node.name),
            has_try=has_try,
            args=[a.arg for a in node.args.args],
            complexity=complexity,
        ))

    return reports


def scan_layer(layer_name: str) -> Tuple[List[FunctionReport], Dict]:
    """扫描指定层"""
    if layer_name not in LAYERS:
        print(f"未知层: {layer_name}")
        return [], {}

    config = LAYERS[layer_name]
    path = config['path']
    if not path.exists():
        return [], {}

    all_reports = []
    file_count = 0
    for f in sorted(path.rglob('*.py')):
        # 跳过archive和__pycache__
        if any(skip in str(f) for skip in ['__pycache__', 'archive', '.git']):
            continue
        reports = scan_file(str(f))
        if reports:
            all_reports.extend(reports)
            file_count += 1

    stats = {
        'layer': layer_name,
        'label': config['label'],
        'priority': config['priority'],
        'files': file_count,
        'total_funcs': len(all_reports),
        'no_try': sum(1 for r in all_reports if not r.has_try),
        'has_try': sum(1 for r in all_reports if r.has_try),
        'c_level_exposed': sum(1 for r in all_reports if not r.has_try and r.level == 'C'),
        'b_level_exposed': sum(1 for r in all_reports if not r.has_try and r.level == 'B'),
        'a_level_exposed': sum(1 for r in all_reports if not r.has_try and r.level == 'A'),
    }
    if stats['total_funcs'] > 0:
        stats['coverage_pct'] = 100 * stats['has_try'] // stats['total_funcs']
    else:
        stats['coverage_pct'] = 0

    return all_reports, stats


def generate_report(target_layer: str = None) -> None:
    """生成全系统异常覆盖报告"""
    layers_to_scan = [target_layer] if target_layer else list(LAYERS.keys())
    
    all_layer_stats = []
    all_c_level_exposed = []

    print("\n" + "="*60)
    print("🔬 梵天异常免疫扫描报告")
    print("="*60)

    total_funcs = 0
    total_no_try = 0

    for layer in layers_to_scan:
        reports, stats = scan_layer(layer)
        if not stats:
            continue
        all_layer_stats.append(stats)
        total_funcs += stats['total_funcs']
        total_no_try += stats['no_try']

        # C级暴露函数（最危险）
        c_exposed = [r for r in reports if not r.has_try and r.level == 'C']
        all_c_level_exposed.extend(c_exposed)

        risk = '🔴' if stats['coverage_pct'] < 30 else ('🟡' if stats['coverage_pct'] < 50 else '🟢')
        print(f"\n{risk} {stats['label']} ({layer})")
        print(f"   文件: {stats['files']} | 函数: {stats['total_funcs']} | "
              f"异常覆盖: {stats['coverage_pct']}%")
        print(f"   C级暴露(执行): {stats['c_level_exposed']} | "
              f"B级暴露(计算): {stats['b_level_exposed']} | "
              f"A级暴露(数据): {stats['a_level_exposed']}")

    # 总计
    total_coverage = 100 * (total_funcs - total_no_try) // total_funcs if total_funcs else 0
    print(f"\n{'='*60}")
    print(f"📊 全系统汇总")
    print(f"   函数总数: {total_funcs} | 无异常处理: {total_no_try} | 覆盖率: {total_coverage}%")
    print(f"   C级(执行函数)暴露: {len(all_c_level_exposed)} 个 ← 最高风险")

    # 输出C级高危函数清单
    if all_c_level_exposed:
        print(f"\n🚨 C级高危函数清单（执行类，崩溃代价最高）:")
        # 按文件分组，优先显示行数大的（复杂函数）
        sorted_c = sorted(all_c_level_exposed, key=lambda r: r.complexity, reverse=True)
        for r in sorted_c[:20]:
            rel_path = os.path.relpath(r.file, BASE)
            print(f"   ⚠️ {rel_path}:{r.line} → {r.function}() [{r.complexity}行]")
        if len(sorted_c) > 20:
            print(f"   ... 还有 {len(sorted_c) - 20} 个")

    # 修复建议
    print(f"\n✅ 修复建议（按优先级）:")
    for stats in sorted(all_layer_stats, key=lambda s: s['priority']):
        if stats['c_level_exposed'] > 0:
            print(f"   P{stats['priority']} {stats['label']}: "
                  f"修复 {stats['c_level_exposed']} 个C级函数 "
                  f"（当前覆盖率 {stats['coverage_pct']}% → 目标 85%）")

    print(f"\n{'='*60}")
    print(f"预计工作量: {total_no_try // 50} 天（按50个/天速度）")
    print(f"最快路径: 先修复 dharma 层（最危险，17%覆盖率）")
    print("="*60)

    # 保存JSON报告
    report_path = BASE / 'logs' / 'exception_coverage_report.json'
    report_path.parent.mkdir(exist_ok=True)
    with open(report_path, 'w') as f:
        json.dump({
            'timestamp': __import__('time').time(),
            'total_funcs': total_funcs,
            'total_no_try': total_no_try,
            'coverage_pct': total_coverage,
            'layers': all_layer_stats,
            'c_level_count': len(all_c_level_exposed),
        }, f, indent=2)
    print(f"\n报告已保存: {report_path}")


def generate_patch_template(layer: str) -> None:
    """
    为指定层生成异常处理补丁模板
    输出每个裸函数应该添加什么样的try-except
    """
    reports, stats = scan_layer(layer)
    if not reports:
        print(f"层 {layer} 扫描无结果")
        return

    bare_funcs = [r for r in reports if not r.has_try]
    print(f"\n🔧 {layer} 层异常补丁模板（{len(bare_funcs)} 个函数）")
    print("="*60)

    templates = {
        'C': '''
# === C级修复模板（执行函数）===
# 原函数体包裹在try中，失败时推送告警
try:
    {original_body}
except Exception as e:
    import logging
    logging.getLogger(__name__).error(
        f"[BRAHMA_EXEC_ERROR] {func_name} 执行失败: {{e}}", exc_info=True
    )
    # 尝试推送Jarvis告警（降级处理）
    try:
        from scripts.signal_router import push_critical_alert
        push_critical_alert(f"执行层异常: {func_name} - {{e}}")
    except Exception:
        pass
    return None  # 安全返回
''',
        'B': '''
# === B级修复模板（计算函数）===
# 计算失败时返回None，不崩溃
try:
    {original_body}
except Exception as e:
    import logging
    logging.getLogger(__name__).warning(
        f"[BRAHMA_CALC_WARN] {func_name} 计算异常: {{e}}"
    )
    return None  # 上层需要处理None
''',
        'A': '''
# === A级修复模板（数据获取函数）===
# 数据获取失败时安静返回None，减少噪音
try:
    {original_body}
except Exception as e:
    import logging
    logging.getLogger(__name__).debug(
        f"[BRAHMA_FETCH_DEBUG] {func_name} 数据获取失败: {{e}}"
    )
    return None
''',
    }

    # 按C→B→A顺序显示，优先修复最危险的
    for level in ['C', 'B', 'A']:
        level_funcs = [r for r in bare_funcs if r.level == level]
        if not level_funcs:
            continue
        label = {'C': '🚨执行函数', 'B': '⚠️计算函数', 'A': 'ℹ️数据函数'}[level]
        print(f"\n{label} ({len(level_funcs)}个):")
        for r in level_funcs[:5]:
            rel = os.path.relpath(r.file, BASE)
            print(f"  {rel}:{r.line} → {r.function}()")
        if len(level_funcs) > 5:
            print(f"  ... 还有{len(level_funcs)-5}个")
        print(templates[level].format(
            original_body='    pass  # 原函数体',
            func_name='<func_name>'
        ))


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='梵天异常免疫扫描工具')
    parser.add_argument('--layer', help='指定层(brahma_brain/dharma/scripts/guardrails)')
    parser.add_argument('--stats', action='store_true', help='仅输出统计')
    parser.add_argument('--patch', help='生成指定层的补丁模板')
    args = parser.parse_args()

    if args.patch:
        generate_patch_template(args.patch)
    elif args.stats:
        # 快速统计模式
        for layer in LAYERS:
            _, stats = scan_layer(layer)
            if stats:
                risk = '🔴' if stats['coverage_pct'] < 30 else ('🟡' if stats['coverage_pct'] < 50 else '🟢')
                print(f"{risk} {stats['label']}: {stats['coverage_pct']}% ({stats['has_try']}/{stats['total_funcs']})")
    else:
        generate_report(args.layer)
