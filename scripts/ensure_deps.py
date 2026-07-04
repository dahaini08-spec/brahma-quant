#!/usr/bin/env python3
"""
依赖自愈脚本 v1.0 2026-07-03
在brahma_self_heal中调用，检测缺失依赖并自动重装
"""
import subprocess, sys, importlib

REQUIRED = {
    'statsmodels': 'statsmodels',
    'sklearn':     'scikit-learn',
    'ta':          'ta',
    'scipy':       'scipy',
    'pandas':      'pandas',
    'numpy':       'numpy',
    'requests':    'requests',
}

def check_and_fix():
    missing = []
    for mod, pkg in REQUIRED.items():
        try:
            importlib.import_module(mod)
        except ImportError:
            missing.append(pkg)

    if not missing:
        return {'ok': True, 'missing': [], 'fixed': []}

    print(f'[ensure_deps] 缺失: {missing}，开始安装...')
    result = subprocess.run(
        [sys.executable, '-m', 'pip', 'install'] + missing +
        ['--break-system-packages', '-q'],
        capture_output=True, text=True, timeout=120
    )

    if result.returncode == 0:
        print(f'[ensure_deps] ✅ 已修复: {missing}')
        return {'ok': True, 'missing': missing, 'fixed': missing}
    else:
        print(f'[ensure_deps] ❌ 安装失败: {result.stderr[:200]}')
        return {'ok': False, 'missing': missing, 'fixed': [], 'error': result.stderr[:200]}

if __name__ == '__main__':
    r = check_and_fix()
    print(r)
