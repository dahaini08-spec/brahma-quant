#!/usr/bin/env python3
"""
data_contract_validator.py — 数据契约验证器
设计院 2026-09-17 苏摩111封印

启动时自动检查所有硬编码字典 vs data文件，发现断裂则告警
接入位置：brahma_core.py 启动时调用 / module_check.py

用法:
    python3 scripts/data_contract_validator.py
    或 from brahma_brain.data_contract_validator import validate_all_contracts
"""
import json
import os
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).parent.parent if '__file__' in dir() else Path('/root/.openclaw/workspace/trading-system')
BRAIN = BASE / 'brahma_brain'
DATA = BASE / 'data'

# 契约注册表：硬编码变量 → 应读的data文件
CONTRACTS = [
    {
        'consumer': 'brahma_brain/trader_brain.py',
        'var': '_MACRO_EVENTS',
        'data_file': 'macro_real.json',
        'desc': 'FOMC/宏观事件',
        'check': 'rate_expectation.action == DONE → post_event',
    },
    {
        'consumer': 'brahma_brain/trader_brain.py',
        'var': '_check_macro_calendar',
        'data_file': 'macro_data.json',
        'desc': 'CPI/PPI/NFP数据',
        'check': '返回值包含macro_data字段',
    },
    {
        'consumer': 'brahma_brain/signal_selector.py',
        'var': 'DYNAMIC_MIN',
        'data_file': 'scoring_config.json',
        'desc': '体制门槛',
        'check': 'DYNAMIC_MIN值与scoring_config.min_score一致',
    },
    {
        'consumer': 'brahma_brain/timing_filter.py',
        'var': '_REGIME_THRESHOLDS',
        'data_file': 'scoring_config.json',
        'desc': '时机过滤体制阈值',
        'check': '从scoring_config读取timing_ready/timing_monitor',
    },
    {
        'consumer': 'brahma_brain/ensemble_engine.py',
        'var': 'REGIME_DIRECTION_WEIGHTS',
        'data_file': 'scoring_config.json',
        'desc': '体制方向权重',
        'check': '从scoring_config读取position_mult',
    },
    {
        'consumer': 'brahma_brain/volatility_context.py',
        'var': '_SYMBOL_ATR_SCALE',
        'data_file': 'scoring_config.json',
        'desc': 'ATR缩放因子',
        'check': '从scoring_config._meta.atr_scale读取',
    },
]


def validate_all_contracts() -> dict:
    """验证所有数据契约，返回断裂列表"""
    breaks = []
    for c in CONTRACTS:
        data_path = DATA / c['data_file']
        consumer_path = BASE / c['consumer']

        # 1. data文件存在？
        data_exists = data_path.exists()

        # 2. 消费者文件存在？
        consumer_exists = consumer_path.exists()

        # 3. 消费者是否引用data文件？
        reads_data = False
        if consumer_exists:
            content = consumer_path.read_text()
            if c['data_file'] in content:
                reads_data = True

        status = 'OK'
        issue = ''
        if not data_exists:
            status = 'WARN'
            issue = f"data文件不存在: {c['data_file']}"
        elif consumer_exists and not reads_data:
            status = 'BREAK'
            issue = f"消费者不读data文件: {c['consumer']} 不引用 {c['data_file']}"

        if status != 'OK':
            breaks.append({
                'consumer': c['consumer'],
                'var': c['var'],
                'data_file': c['data_file'],
                'desc': c['desc'],
                'status': status,
                'issue': issue,
                'check': c['check'],
            })

    return {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'total_contracts': len(CONTRACTS),
        'breaks': breaks,
        'break_count': len(breaks),
        'all_ok': len(breaks) == 0,
    }


def main():
    result = validate_all_contracts()
    print(f"=== 数据契约验证 {result['timestamp']} ===")
    print(f"契约总数: {result['total_contracts']} | 断裂: {result['break_count']} | 状态: {'✅全绿' if result['all_ok'] else '❌有断裂'}")
    print()
    if result['breaks']:
        print("=== 断裂详情 ===")
        for b in result['breaks']:
            print(f"  [{b['status']}] {b['desc']}")
            print(f"    消费者: {b['consumer']}:{b['var']}")
            print(f"    data文件: {b['data_file']}")
            print(f"    问题: {b['issue']}")
            print(f"    检查: {b['check']}")
            print()
    return result


if __name__ == '__main__':
    main()
