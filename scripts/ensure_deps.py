#!/usr/bin/env python3
"""
ensure_deps.py — 运行时依赖自动安装
在brahma系统启动时调用，确保关键依赖存在
"""
import subprocess, sys

REQUIRED = {
    'yaml':        'pyyaml',
    'sklearn':     'scikit-learn',
    'statsmodels': 'statsmodels',
    'psutil':      'psutil',
}

def ensure():
    missing = []
    for mod, pkg in REQUIRED.items():
        try:
            __import__(mod)
        except ImportError:
            missing.append(pkg)
    if missing:
        print(f'[ensure_deps] 安装缺失依赖: {missing}')
        subprocess.run(
            [sys.executable, '-m', 'pip', 'install', '--break-system-packages', '-q'] + missing,
            timeout=120
        )
        print('[ensure_deps] ✅ 安装完成')
    return len(missing)

if __name__ == '__main__':
    n = ensure()
    print(f'[ensure_deps] 已安装{n}个缺失包' if n else '[ensure_deps] 所有依赖已就绪')
