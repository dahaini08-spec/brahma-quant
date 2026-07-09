#!/usr/bin/env python3
"""Import smoke test — all brahma_v6 modules must import cleanly."""
import sys, importlib
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

MODULES = [
    "brahma_v6.execution.order_state",
    "brahma_v6.execution.order_ticket",
    "brahma_v6.dharma2.models",
    "brahma_v6.dharma2.trade_ledger",
    "brahma_v6.dharma2.ev_bucket",
    "brahma_v6.reality.models",
    "brahma_v6.reality.fee_model",
    "brahma_v6.reality.slippage_model",
    "brahma_v6.reality.funding_model",
    "brahma_v6.reality.impact_model",
    "brahma_v6.reality.reality_engine",
    "brahma_v6.risk.models",
    "brahma_v6.risk.kill_switch",
    "brahma_v6.risk.daily_loss_guard",
    "brahma_v6.risk.exposure_guard",
    "brahma_v6.risk.leverage_guard",
    "brahma_v6.risk.risk_kernel",
    "brahma_v6.ops.dlq",
    "brahma_v6.ops.reconciler",
    "brahma_v6.ops.healthcheck",
    "brahma_v6.ops.ten_million.scenario",
    "brahma_v6.ops.ten_million.driver",
    "brahma_v6.ops.ten_million.invariant_checker",
    "brahma_v6.ops.ten_million.optimizer",
    "brahma_v6.ops.ten_million.report",
    "brahma_v6.apps.million_run_simulator",
]

failed = []
for mod in MODULES:
    try:
        importlib.import_module(mod)
        print(f"  ✅ {mod}")
    except Exception as e:
        print(f"  ❌ {mod}: {e}")
        failed.append(mod)

if failed:
    print(f"\nIMPORT SMOKE: FAIL ({len(failed)} modules failed)")
    sys.exit(1)
else:
    print(f"\nIMPORT SMOKE: OK ({len(MODULES)} modules)")
    sys.exit(0)
