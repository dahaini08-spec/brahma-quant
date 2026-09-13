#!/usr/bin/env python3
"""
seal_v2.py — 封印2.0 自动化双轨审查
[果蝇架构Phase 3] 从mattpocock/skills借鉴 code-review + diagnosing-bugs

Standards轴（自动）：代码规范 + 冒烟测试 + 模块健康
Spec轴（自动）：接入位置 + 端口连通 + dim_trace写入
红测试先行：修复前先写复现测试，修复后验证变绿

用法：
    python3 scripts/seal_v2.py                    # 完整封印
    python3 scripts/seal_v2.py --file dag_executor.py  # 指定文件
    python3 scripts/seal_v2.py --quick            # 快速（跳过冒烟）
"""
import sys
import os
import time
import json
from pathlib import Path

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'scripts'))
sys.dont_write_bytecode = True

# ── Standards轴 ──────────────────────────────────────────────────

def check_imports():
    """检查关键import是否正常"""
    results = []
    try:
        from brahma_brain.brahma_core import confluence_score
        results.append(('✅', 'brahma_core.confluence_score import OK'))
    except Exception as e:
        results.append(('❌', f'brahma_core import FAIL: {e}'))

    try:
        from brahma_brain.dag_executor import apply_sparse_activation
        results.append(('✅', 'dag_executor import OK'))
    except Exception as e:
        results.append(('❌', f'dag_executor import FAIL: {e}'))

    try:
        from brahma_brain.dim_trace_writer import trace_dim, read_recent
        results.append(('✅', 'dim_trace_writer import OK'))
    except Exception as e:
        results.append(('❌', f'dim_trace_writer import FAIL: {e}'))

    try:
        from brahma_brain.brahma_mcp_server import _tool_health, _tool_config
        results.append(('✅', 'brahma_mcp_server import OK'))
    except Exception as e:
        results.append(('❌', f'brahma_mcp_server import FAIL: {e}'))

    return results

def check_smoke():
    """冒烟测试（轻量）"""
    results = []
    try:
        from scripts.module_check import check_modules
        alive, missing, port_issues, alerts = check_modules()
        if len(missing) == 0 and len(port_issues) == 0:
            results.append(('✅', f'module_check: {len(alive)}/27存活 0端口问题'))
        else:
            results.append(('⚠️', f'module_check: {len(alive)}存活 {len(missing)}缺失 {len(port_issues)}端口问题'))
    except Exception as e:
        results.append(('❌', f'module_check FAIL: {e}'))

    try:
        from brahma_brain.dag_executor import _load_config, dump_config
        cfg = _load_config()
        regimes = [k for k in cfg if not k.startswith('_')]
        results.append(('✅', f'scoring_config: {len(regimes)}体制×2方向 = {len(regimes)*2}组合'))
    except Exception as e:
        results.append(('❌', f'scoring_config FAIL: {e}'))

    try:
        from brahma_brain.dim_trace_writer import trace_dim, read_recent
        trace_dim('_seal_test', {'test': True}, {'result': 'pass'}, 'ok', 0)
        recent = read_recent(1)
        assert recent[0]['output']['result'] == 'pass'
        results.append(('✅', 'dim_trace: 写入+读取正常'))
    except Exception as e:
        results.append(('❌', f'dim_trace FAIL: {e}'))

    return results

def check_except_pass():
    """统计except pass数量"""
    import re
    total = 0
    by_file = {}
    for pyfile in sorted((BASE / 'brahma_brain').glob('*.py')):
        try:
            lines = open(pyfile, errors='ignore').readlines()
        except Exception:
            continue
        for line in lines:
            if re.match(r'^\s*except\s*(\w+\s*(\(\s*\w+\s*\))?\s*)?:\s*pass\s*$', line):
                total += 1
                f = str(pyfile.relative_to(BASE))
                by_file[f] = by_file.get(f, 0) + 1
    return total, by_file

# ── Spec轴 ──────────────────────────────────────────────────────

def check_dag_integration():
    """验证DAG接入位置"""
    results = []
    core = BASE / 'brahma_brain' / 'brahma_core.py'
    if not core.exists():
        return [('❌', 'brahma_core.py不存在')]

    content = core.read_text()
    if 'apply_sparse_activation' in content:
        results.append(('✅', 'DAG接入: brahma_core.py中找到apply_sparse_activation'))
    else:
        results.append(('❌', 'DAG接入: brahma_core.py中未找到apply_sparse_activation'))

    if '_dag_result' in content:
        results.append(('✅', 'DAG结果: _dag_result变量存在'))
    else:
        results.append(('❌', 'DAG结果: _dag_result变量不存在'))

    if 'dag_position_mult' in content:
        results.append(('✅', 'DAG透传: position_mult透传到extra_data'))
    else:
        results.append(('⚠️', 'DAG透传: position_mult未透传'))

    return results

def check_dim_trace_injection():
    """验证dim_trace注入"""
    results = []
    core = BASE / 'brahma_brain' / 'brahma_core.py'
    content = core.read_text()
    if 'trace_dim' in content or '_trace(' in content:
        results.append(('✅', 'dim_trace注入: confluence_score中有trace写入'))
    else:
        results.append(('❌', 'dim_trace注入: 未找到trace写入'))

    trace_file = BASE / 'data' / 'dim_trace.jsonl'
    if trace_file.exists():
        lines = trace_file.read_text().strip().split('\n')
        results.append(('✅', f'dim_trace.jsonl: {len(lines)}条记录'))
    else:
        results.append(('⚠️', 'dim_trace.jsonl: 不存在'))

    return results

def check_context():
    """验证CONTEXT.md存在"""
    results = []
    ctx = BASE / 'brahma_brain' / 'CONTEXT.md'
    if ctx.exists():
        content = ctx.read_text()
        if 's1' in content and 'FVG' in content and 'HCME' in content:
            results.append(('✅', f'CONTEXT.md: 存在且包含关键术语'))
        else:
            results.append(('⚠️', 'CONTEXT.md: 存在但术语不全'))
    else:
        results.append(('❌', 'CONTEXT.md: 不存在'))

    adr_dir = BASE / 'docs' / 'adr'
    if adr_dir.exists():
        adrs = list(adr_dir.glob('*.md'))
        results.append(('✅', f'docs/adr/: {len(adrs)}个ADR文件'))
    else:
        results.append(('❌', 'docs/adr/: 不存在'))

    return results

def check_invocation():
    """验证user/model分离"""
    results = []
    reg_path = BASE / 'data' / 'module_registry.json'
    if not reg_path.exists():
        return [('❌', 'module_registry.json: 不存在')]

    reg = json.loads(reg_path.read_text())
    has_invocation = False
    for cat in ['critical_modules', 'enhancement_modules']:
        for name, info in reg[cat].items():
            if 'invocation' in info:
                has_invocation = True
                break
        if has_invocation:
            break

    if has_invocation:
        user_count = sum(1 for cat in ['critical_modules', 'enhancement_modules']
                        for info in reg[cat].values()
                        if info.get('invocation') == 'user-invoked')
        model_count = sum(1 for cat in ['critical_modules', 'enhancement_modules']
                         for info in reg[cat].values()
                         if info.get('invocation') == 'model-invoked')
        results.append(('✅', f'invocation分离: {user_count}user + {model_count}model'))
    else:
        results.append(('❌', 'invocation分离: 未配置'))

    return results

# ── 主流程 ─────────────────────────────────────────────────────

def main():
    quick = '--quick' in sys.argv

    print('═' * 60)
    print('  封印2.0 自动化双轨审查')
    print('═' * 60)
    print()

    all_pass = True

    # Standards轴
    print('┌─ Standards轴 ──────────────────────────────────────')
    print('│')
    print('│  1. Import检查')
    for status, msg in check_imports():
        print(f'│    {status} {msg}')
        if '❌' in status:
            all_pass = False

    print('│')
    print('│  2. 冒烟测试')
    for status, msg in check_smoke():
        print(f'│    {status} {msg}')
        if '❌' in status:
            all_pass = False

    print('│')
    print('│  3. 沉默错误统计')
    total_ep, by_file = check_except_pass()
    if total_ep == 0:
        print(f'│    ✅ 0处except pass')
    else:
        print(f'│    ⚠️ {total_ep}处except pass（建议清理）')
        for f, c in sorted(by_file.items(), key=lambda x: -x[1])[:3]:
            print(f'│       {f}: {c}处')
    print('│')
    print('└──────────────────────────────────────────────────────')
    print()

    # Spec轴
    print('┌─ Spec轴 ────────────────────────────────────────────')
    print('│')
    print('│  4. DAG接入验证')
    for status, msg in check_dag_integration():
        print(f'│    {status} {msg}')
        if '❌' in status:
            all_pass = False

    print('│')
    print('│  5. dim_trace注入验证')
    for status, msg in check_dim_trace_injection():
        print(f'│    {status} {msg}')
        if '❌' in status:
            all_pass = False

    print('│')
    print('│  6. CONTEXT.md + ADR验证')
    for status, msg in check_context():
        print(f'│    {status} {msg}')
        if '❌' in status:
            all_pass = False

    print('│')
    print('│  7. user/model分离验证')
    for status, msg in check_invocation():
        print(f'│    {status} {msg}')
        if '❌' in status:
            all_pass = False

    print('│')
    print('└──────────────────────────────────────────────────────')
    print()

    # 结论
    print('═' * 60)
    if all_pass:
        print('  ✅ 封印2.0通过')
    else:
        print('  ⚠️ 封印2.0有失败项（见上方❌）')
    print(f'  ⚠️ {total_ep}处except pass建议清理（不阻塞封印）')
    print('═' * 60)

if __name__ == '__main__':
    main()
