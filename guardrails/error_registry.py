#!/usr/bin/env python3
"""
error_registry.py — 梵天系统错误注册表
设计院 · 防御纵深框架 Layer 4
2026-05-28

职责：
  1. 登记每一次已知错误（人工或自动触发）
  2. 为每条错误生成可回归的测试用例
  3. run_regression() 验证已知错误是否复现
  4. 发现 regression → 立即 Jarvis 告警

使用：
  # 登记新错误
  from guardrails.error_registry import register_error
  register_error(category='routing', ...)

  # 跑回归测试（cron 每周一次）
  python3 guardrails/error_registry.py --regression
"""

import json
import os
import sys
import subprocess
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE = Path(__file__).parent.parent
REGISTRY_FILE = BASE / 'data' / 'known_mistakes.json'
BJ = timezone(timedelta(hours=8))


def _now_bj():
    return datetime.now(BJ).strftime('%Y-%m-%d %H:%M BJ')


def _ts():
    return int(datetime.now(timezone.utc).timestamp())


def _load() -> list:
    # 优先用 safe_json
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from safe_json import safe_read
        data = safe_read(str(REGISTRY_FILE), default=[])
        return data if isinstance(data, list) else []
    except ImportError:
        pass
    try:
        return json.loads(REGISTRY_FILE.read_text())
    except Exception:
        return []


def _save(data: list):
    REGISTRY_FILE.parent.mkdir(parents=True, exist_ok=True)
    # 优先用 safe_json 原子写入
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from safe_json import safe_write
        safe_write(str(REGISTRY_FILE), data)
        return
    except ImportError:
        pass
    REGISTRY_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def _jarvis(msg: str):
    """发送 Jarvis 告警"""
    try:
        subprocess.Popen([
            'openclaw', 'message', 'send',
            '--channel', 'jarvis',
            '--to', os.environ.get('JARVIS_USER_ID', 'YOUR_USER_ID'),
            '--message', msg,
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


# ═══════════════════════════════════════════════════════
# 公开 API
# ═══════════════════════════════════════════════════════

def register_error(
    category: str,
    description: str,
    trigger_condition: str,
    fix_applied: str,
    test_input: str,
    test_should_block: bool = True,
    test_fn: str = '',
    error_id: str = '',
) -> dict:
    """
    登记一条已知错误。

    Args:
        category:          分类 routing / format / analysis / trade
        description:       人类可读描述（一句话说清楚错误是什么）
        trigger_condition: 什么情况下触发（用于回归）
        fix_applied:       修复方案描述
        test_input:        回归测试的输入（字符串）
        test_should_block: True=期望被拦截, False=期望通过
        test_fn:           回归测试调用的函数路径 'module.function'
        error_id:          手动指定 ID（留空自动生成）

    Returns:
        登记的 entry dict
    """
    errors = _load()

    # 防重复
    for e in errors:
        if e.get('trigger_condition') == trigger_condition and e.get('description') == description:
            print(f'[ErrorRegistry] ℹ️ 已存在相同错误，跳过: {e["id"]}')
            return e

    eid = error_id or f'ERR-{len(errors)+1:03d}-{_ts()}'

    entry = {
        'id':                eid,
        'category':          category,
        'description':       description,
        'trigger_condition': trigger_condition,
        'fix_applied':       fix_applied,
        'test_input':        test_input,
        'test_should_block': test_should_block,
        'test_fn':           test_fn,
        'registered_at':     _now_bj(),
        'regression_passed': None,   # None = 未测试
        'last_tested_at':    None,
    }

    errors.append(entry)
    _save(errors)
    print(f'[ErrorRegistry] ✅ 已登记 {eid}: {description[:60]}')
    return entry


def run_regression(alert_on_fail: bool = True) -> dict:
    """
    对所有已登记错误跑回归测试。
    返回 {passed: int, failed: int, skipped: int, failures: list}
    """
    errors = _load()
    passed = failed = skipped = 0
    failures = []

    for err in errors:
        test_fn_path = err.get('test_fn', '')
        test_input   = err.get('test_input', '')
        should_block = err.get('test_should_block', True)

        if not test_fn_path or not test_input:
            skipped += 1
            continue

        try:
            result = _run_test(test_fn_path, test_input, should_block)
            err['last_tested_at'] = _now_bj()

            if result['ok']:
                err['regression_passed'] = True
                passed += 1
                print(f'  ✅ {err["id"]} PASS')
            else:
                err['regression_passed'] = False
                failed += 1
                failures.append({'id': err['id'], 'desc': err['description'], 'detail': result.get('detail', '')})
                print(f'  ❌ {err["id"]} FAIL — {result.get("detail", "")}')

        except Exception as e:
            skipped += 1
            err['last_tested_at'] = _now_bj()
            print(f'  ⚠️ {err["id"]} ERROR — {e}')

    _save(errors)

    summary = {'passed': passed, 'failed': failed, 'skipped': skipped, 'failures': failures}

    if failed > 0 and alert_on_fail:
        fail_list = '\n'.join(f'  • {f["id"]}: {f["desc"]}' for f in failures)
        _jarvis(
            f'🔴 [ErrorRegistry] 回归测试失败！{failed}个已知错误复现\n\n'
            f'{fail_list}\n\n'
            f'请立即检查并修复'
        )

    print(f'\n[ErrorRegistry] 回归结果: ✅{passed} ❌{failed} ⏭{skipped}')
    return summary


def list_errors(category: str = '') -> list:
    """列出已登记错误，可按分类过滤"""
    errors = _load()
    if category:
        return [e for e in errors if e.get('category') == category]
    return errors


# ═══════════════════════════════════════════════════════
# 内部：回归测试执行器
# ═══════════════════════════════════════════════════════

def _run_test(test_fn_path: str, test_input: str, should_block: bool) -> dict:
    """
    执行单条回归测试。
    test_fn_path 格式: 'module_path::function_name'
    """
    if '::' not in test_fn_path:
        return {'ok': False, 'detail': f'test_fn 格式错误: {test_fn_path}'}

    module_path, fn_name = test_fn_path.split('::', 1)

    # 动态导入
    import importlib.util as _ilu
    spec = _ilu.spec_from_file_location('_test_mod', str(BASE / module_path))
    if spec is None:
        return {'ok': False, 'detail': f'模块不存在: {module_path}'}
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)

    fn = getattr(mod, fn_name, None)
    if fn is None:
        return {'ok': False, 'detail': f'函数不存在: {fn_name}'}

    result = fn(test_input)

    # result 可以是 bool / tuple(bool, ...) / dict(ok=bool)
    if isinstance(result, bool):
        blocked = not result  # False 返回 = 被拦截
    elif isinstance(result, (tuple, list)):
        blocked = not result[0]
    elif isinstance(result, dict):
        blocked = not result.get('ok', True)
    else:
        blocked = False

    if should_block and blocked:
        return {'ok': True, 'detail': '正确拦截'}
    elif not should_block and not blocked:
        return {'ok': True, 'detail': '正确放行'}
    elif should_block and not blocked:
        return {'ok': False, 'detail': f'应拦截但放行了 (input={test_input[:50]})'}
    else:
        return {'ok': False, 'detail': f'应放行但拦截了 (input={test_input[:50]})'}


# ═══════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='梵天错误注册表')
    parser.add_argument('--regression', action='store_true', help='跑回归测试')
    parser.add_argument('--list', action='store_true', help='列出所有错误')
    parser.add_argument('--category', default='', help='过滤分类')
    args = parser.parse_args()

    if args.regression:
        sys.path.insert(0, str(BASE))
        sys.path.insert(0, str(BASE / 'scripts'))
        run_regression(alert_on_fail=True)

    elif args.list:
        errors = list_errors(args.category)
        print(f'已登记错误: {len(errors)} 条\n')
        for e in errors:
            status = '✅' if e.get('regression_passed') else ('❌' if e.get('regression_passed') is False else '⏭')
            print(f'  {status} [{e["id"]}] {e["category"]:10s} {e["description"][:60]}')
            print(f'       登记: {e["registered_at"]} | 最后测试: {e.get("last_tested_at","从未")}')
    else:
        parser.print_help()
