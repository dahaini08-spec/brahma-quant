#!/bin/bash
# wrapper_4h.sh — 每4小时任务串行漏斗
# 合并: brahma-gex-refresh + brahma-360-health + liq-heatmap-refresh + scan-fast + macro-ai-bridge + brahma-zone-forecast
# 2026-08-26 梵天设计院 苏摩P1P2封印

set -e
DIR="/root/.openclaw/workspace/trading-system"
RUNNER="$DIR/scripts/cron_noai_runner.sh"

echo "[wrapper-4h] 启动 $(date '+%Y-%m-%d %H:%M:%S')"

echo "[1/6] gex_refresh (BTC+ETH)"
bash "$RUNNER" timeout 60 python3 -c "
import sys; sys.path.insert(0,'$DIR')
from brahma_brain.gex_scanner import scan_gex
r1=scan_gex('BTC',force=True); r2=scan_gex('ETH',force=True)
print(f'GEX: BTC={r1.get(\"gex_value\",\"N/A\") if r1 else \"ERR\"} ETH={r2.get(\"gex_value\",\"N/A\") if r2 else \"ERR\"}')
" 2>&1 | tail -3

echo "[2/6] brahma_360 health check"
bash "$RUNNER" timeout 60 python3 brahma_brain/brahma_360.py 2>&1 | tail -3

echo "[3/6] liq_heatmap refresh"
bash "$RUNNER" timeout 60 python3 scripts/liq_heatmap.py --symbols BTCUSDT ETHUSDT SOLUSDT BNBUSDT 2>&1 | tail -3

echo "[4/6] scan-fast (brahma_scan_all 30主力币)"
bash "$RUNNER" timeout 120 python3 scripts/brahma_scan_all.py 2>&1 | tail -3

echo "[5/6] macro_ai_bridge"
bash "$RUNNER" timeout 60 python3 scripts/macro_ai_bridge.py 2>&1 | tail -3

echo "[6/6] brahma_zone_forecast"
bash "$RUNNER" timeout 60 python3 brahma_brain/price_zone_engine.py --symbols BTCUSDT ETHUSDT --push 2>&1 | tail -3

echo "[wrapper-4h] 完成 $(date '+%Y-%m-%d %H:%M:%S')"
