#!/usr/bin/env python3
"""
brahma_wiring_check.py — 梵天接线验证检测器
[设计院 2026-08-08 自主决策封印]

功能：
1. 扫描 brahma_brain/ 所有模块
2. 检测是否被主链路 import / 调用
3. 高价值孤岛 → P1预警
4. 结果写入 data/wiring_status.json

触发时机：每次封印新模块后运行，brahma-cron-doctor 每日扫描
"""
import os, json, datetime, sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRAIN_DIR = os.path.join(BASE, 'brahma_brain')
DATA_DIR = os.path.join(BASE, 'data')

# 主链路文件
MAIN_CHAIN = [
    'brahma_brain/brahma_engine.py',
    'brahma_brain/brahma_core.py',           # [fix 2026-08-13] engine是core的shim，core才是真相
    'brahma_brain/brahma_analysis_runner.py',
    'scripts/brahma_1hao_analysis.py',
    'scripts/auto_executor.py',
    'scripts/signal_settler.py',
]

# 已知高价值模块 → 若孤立则 P1 预警
HIGH_VALUE = {
    'signal_15m_engine',
    'hcme_matcher',
    'brahma_optimizer',
    'ic_tracker',
    'vectorbt_simfactory',
    'brahma_360',
    'fangcang_engine',
    'brahma_decision_engine',
    'kronos_bridge',
    'smc_engine',
}

# 已知低价值/废弃 → 忽略
SKIP_MODULES = {
    'dharma_online_learner', 'pump_hunter_brain',
    'brahma_trade', 'brahma_orchestrator',
    'exception_injector', 'brahma_constitutional_test',
    '__init__',
    # 脚本调用型（cron命令行直接执行，不在Python import链中）
    'brahma_360',
    # 条件触发型（Step B: live_signal>=500条后触发）
    'brahma_optimizer',
    # 专项工具（dharma回测专用，非主链路）
    'vectorbt_simfactory',
}

def check():
    # 读取主链路内容
    main_content = ''
    for rel in MAIN_CHAIN:
        fp = os.path.join(BASE, rel)
        if os.path.exists(fp):
            with open(fp) as f:
                main_content += f.read()

    brain_files = [
        f.replace('.py', '') for f in os.listdir(BRAIN_DIR)
        if f.endswith('.py') and not f.startswith('_')
    ]

    orphans_high, orphans_low, connected = [], [], []
    for mod in sorted(brain_files):
        if mod in SKIP_MODULES:
            continue
        if mod in main_content or f'{mod}.py' in main_content:
            connected.append(mod)
        elif mod in HIGH_VALUE:
            orphans_high.append(mod)
        else:
            orphans_low.append(mod)

    result = {
        'ts': datetime.datetime.utcnow().isoformat(),
        'connected': len(connected),
        'orphans_high': orphans_high,
        'orphans_low': orphans_low,
        'total': len(connected) + len(orphans_high) + len(orphans_low),
        'status': 'WARN' if orphans_high else 'OK',
    }

    out_path = os.path.join(DATA_DIR, 'wiring_status.json')
    with open(out_path, 'w') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"[接线检测] 已接入: {len(connected)}  高价值孤岛: {len(orphans_high)}  低价值孤岛: {len(orphans_low)}")
    if orphans_high:
        print("[P1预警] 以下高价值模块未接入主链路:")
        for m in orphans_high:
            print(f"  ❌ {m}")
        sys.exit(1)
    else:
        print("[接线检测] ✅ 全部高价值模块已接入，无孤岛")
        sys.exit(0)

if __name__ == '__main__':
    check()
