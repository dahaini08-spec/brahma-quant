"""
梵天插件总线 v1.0
[P2-A 2026-08-31 苏摩111封印]

自动扫描 brahma_brain/plugins/ 目录
每个插件文件实现 run(r: dict) -> str 函数
brahma_full_report.py 自动加载并调用
"""
import importlib, os
from pathlib import Path

PLUGIN_DIR = Path(__file__).parent

def load_all_plugins():
    """返回所有可用插件列表"""
    plugins = []
    for f in sorted(PLUGIN_DIR.glob('plugin_*.py')):
        name = f.stem
        try:
            mod = importlib.import_module(f'brahma_brain.plugins.{name}')
            if hasattr(mod, 'run'):
                plugins.append((name, mod))
        except Exception as e:
            pass
    return plugins

def run_all_plugins(r: dict) -> str:
    """运行所有插件，返回拼接输出"""
    results = []
    for name, mod in load_all_plugins():
        try:
            out = mod.run(r)
            if out and out.strip():
                results.append(out.strip())
        except Exception:
            pass
    return '\n'.join(results)
