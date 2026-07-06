"""
brahma_brain/module_registry.py
梵天系统模块注册表 — 设计院封印 2026-07-06
防止孤儿模块通过try/except链接导致不稳定性
"""

# 主链路核心模块（必须存在）
CORE_MODULES = [
    'brahma_brain.brahma_core',
    'brahma_brain.brahma_analysis_runner',
    'brahma_brain.timing_filter',
    'brahma_brain.bull_regime_injector',
    'brahma_brain.regime_state_machine',
    'brahma_brain.dharma_data_bridge',
    'brahma_brain.brahma_bus',
]

# runner层注入（try/except降级可接受）
RUNNER_MODULES = [
    'cross_market_engine',
    'macro_engine',
    'kronos_bridge',
    'upgrade_v2.v2_integrator',
]

# 辅助监控模块（不在信号链，不影响有效性）
OPTIONAL_MODULES = [
    'brahma_brain.brahma_ci',
    'brahma_brain.brahma_ci_v2',
    'brahma_brain.circuit_breaker',
    'brahma_brain.exception_injector',
    'brahma_brain.memory_watchdog',
    'brahma_brain.headroom',
]


def check_core_modules():
    """验证核心模块全部可导入，返回缺失列表"""
    import importlib
    missing = []
    for mod in CORE_MODULES:
        try:
            importlib.import_module(mod)
        except ImportError as e:
            missing.append(f"{mod}: {e}")
    return missing


def get_module_status():
    """返回所有模块的导入状态"""
    import importlib
    status = {}
    all_mods = [('CORE', CORE_MODULES), ('RUNNER', RUNNER_MODULES), ('OPTIONAL', OPTIONAL_MODULES)]
    for tier, mods in all_mods:
        for mod in mods:
            try:
                importlib.import_module(mod)
                status[mod] = {'tier': tier, 'ok': True}
            except ImportError as e:
                status[mod] = {'tier': tier, 'ok': False, 'error': str(e)}
    return status


def _setup_path():
    """确保sys.path包含正确的根目录"""
    import sys, os
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # trading-system/
    brain = os.path.dirname(os.path.abspath(__file__))  # trading-system/brahma_brain/
    for p in [base, brain]:
        if p not in sys.path:
            sys.path.insert(0, p)


_setup_path()


if __name__ == '__main__':
    s = get_module_status()
    for mod, info in s.items():
        icon = '✅' if info['ok'] else '❌'
        err = f" — {info.get('error','?')}" if not info['ok'] else ''
        print(f"{icon} [{info['tier']}] {mod}{err}")
    missing = check_core_modules()
    if missing:
        print(f"\n🚨 CORE模块缺失: {missing}")
    else:
        print("\n✅ 所有CORE模块正常")
