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
    'brahma_brain.math_utils',
    'brahma_brain.universal_asset_router',
    'brahma_brain.position_sizer',
    'brahma_brain.dynamic_sl',
]

# 信号链子引擎（try/except降级可接受）
SIGNAL_CHAIN_MODULES = [
    'brahma_brain.smc_engine',
    'brahma_brain.divergence_engine',
    'brahma_brain.volume_exhaustion_engine',
    'brahma_brain.multitf_engine',
    'brahma_brain.gex_engine',
    'brahma_brain.liq_scanner',
    'brahma_brain.orderbook_heatmap',
    'brahma_brain.orderbook_engine',
    'brahma_brain.order_flow_engine',
    'brahma_brain.whale_engine',
    'brahma_brain.onchain_engine',
    'brahma_brain.options_engine',
    'brahma_brain.kronos_engine',
    'brahma_brain.signal_selector',
    'brahma_brain.brahma_parallel_engine',
]

# runner层注入（try/except降级可接受）
RUNNER_MODULES = [
    'cross_market_engine',
    'macro_engine',
    'kronos_bridge',
    'upgrade_v2.v2_integrator',
]

# 分析辅助模块（不在信号链）
ANALYTICS_MODULES = [
    'brahma_brain.portfolio_optimizer',
    'brahma_brain.capital_allocator',
    'brahma_brain.causal_regime_verifier',
    'brahma_brain.brahma_health',
    'brahma_brain.ev_feedback',
    'brahma_brain.ic_tracker',
    'brahma_brain.auto_review',
    'brahma_brain.brainlog',
    'brahma_brain.brahma_360',
    'brahma_brain.brahma_orchestrator',
    'brahma_brain.llm_council_bridge',
]

# 数据基础设施
DATA_INFRA_MODULES = [
    'brahma_brain.brahma_event_bus',
    'brahma_brain.data_cache',
    'brahma_brain.live_price_feed',
    'brahma_brain.realtime_fetch',
    'brahma_brain.market_state',
    'brahma_brain.smart_money_engine',
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
    all_tiers = [
        ('CORE',         CORE_MODULES),
        ('SIGNAL_CHAIN', SIGNAL_CHAIN_MODULES),
        ('RUNNER',       RUNNER_MODULES),
        ('ANALYTICS',    ANALYTICS_MODULES),
        ('DATA_INFRA',   DATA_INFRA_MODULES),
        ('OPTIONAL',     OPTIONAL_MODULES),
    ]
    for tier, mods in all_tiers:
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
