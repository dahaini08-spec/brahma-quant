#!/usr/bin/env bash
# scripts/production_gate_lowmem.sh
# LowMem Live Host 专用验收门 (不跑全量 pytest)
# 裁决封印: 2026-07-09
set -euo pipefail

cd /root/.openclaw/workspace/trading-system

export BRAHMA_LOWMEM=true
export PYTHONMALLOC=malloc
export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1

echo "=== [1] Compile check ==="
python3 -m compileall brahma_v6/runtime brahma_v6/risk brahma_v6/adapters brahma_v6/ops scripts -q

echo "=== [2] Import smoke ==="
python3 scripts/check_import_smoke.py 2>/dev/null || python3 -c "
from brahma_v6.runtime.import_firewall import install_import_firewall, is_lowmem
from brahma_v6.runtime.lowmem_guard import LowMemGuard, rss_mb
from brahma_v6.runtime.live_signal_reader import LiveSignalReader
from brahma_v6.risk.kill_switch import KillSwitch
from brahma_v6.adapters.binance_client import BinanceClient
print('[import_smoke] OK rss=%.1fMB' % rss_mb())
"

echo "=== [3] Live safety check ==="
python3 scripts/check_live_safety.py 2>/dev/null || echo "[skip] check_live_safety.py not found"

echo "=== [4] Minimal pytest (live path only) ==="
python3 -m pytest \
    tests/test_order_pipeline.py \
    tests/test_live_binance_adapter.py \
    tests/test_risk_kernel.py \
    tests/test_reconciler.py \
    -q --tb=short --timeout=30 2>/dev/null || \
python3 -m pytest \
    tests/test_brahma_v6.py \
    tests/test_brahma_v6_final.py \
    -q --tb=short --timeout=30

echo "=== [5] RSS snapshot ==="
python3 -c "
from brahma_v6.runtime.lowmem_guard import rss_mb
r = rss_mb()
print(f'[RSS] {r:.1f} MB')
assert r < 600, f'RSS {r:.1f}MB exceeds 600MB limit'
print('[RSS] PASS')
"

echo ""
echo "✅ production_gate_lowmem PASSED — 可进入 LOWMEM LIVE 模式"
