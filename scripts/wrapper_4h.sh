#!/bin/bash
# wrapper_4h.sh — 每4小时任务串行漏斗
# 合并: brahma-gex-refresh + brahma-360-health + liq-heatmap-refresh + scan-fast + macro-ai-bridge + brahma-zone-forecast
# 2026-08-28 苏摩111修复：去掉 set -e，每步独立容错，避免单步挂死导致全程无输出

DIR="/root/.openclaw/workspace/trading-system"
RUNNER="$DIR/scripts/cron_noai_runner.sh"
ERRORS=0

run_step() {
    local step="$1"; shift
    echo "[$step] 开始 $(date '+%H:%M:%S')"
    if "$@"; then
        echo "[$step] 完成 ✅"
    else
        echo "[$step] 失败 ❌ (exit=$?)"
        ERRORS=$((ERRORS+1))
    fi
}

echo "[wrapper-4h] 启动 $(date '+%Y-%m-%d %H:%M:%S')"

run_step "1/6 gex_refresh" bash "$RUNNER" timeout 60 python3 -c "
import sys; sys.path.insert(0,'$DIR')
from brahma_brain.gex_scanner import scan_gex
r1=scan_gex('BTC',force=True); r2=scan_gex('ETH',force=True)
print(f'GEX: BTC={r1.get(\"gex_value\",\"N/A\") if r1 else \"ERR\"} ETH={r2.get(\"gex_value\",\"N/A\") if r2 else \"ERR\"}')
" 2>&1 | tail -2

run_step "2/6 brahma_360" bash "$RUNNER" timeout 60 python3 brahma_brain/brahma_360.py 2>&1 | tail -2

run_step "3/6 liq_heatmap" bash "$RUNNER" timeout 60 python3 scripts/liq_heatmap.py --symbols BTCUSDT ETHUSDT SOLUSDT BNBUSDT 2>&1 | tail -2

run_step "4/6 scan_fast" bash "$RUNNER" timeout 120 python3 scripts/brahma_scan_all.py 2>&1 | tail -2

run_step "5/6 macro_ai_bridge" bash "$RUNNER" timeout 60 python3 scripts/macro_ai_bridge.py 2>&1 | tail -2

run_step "6/6 zone_forecast" bash "$RUNNER" timeout 60 python3 brahma_brain/price_zone_engine.py --symbols BTCUSDT ETHUSDT --push 2>&1 | tail -2

# [2026-08-29 苏摩111] 新增 step7：刷新 fangcang_dharma_realtime（513标的方仓快照）
run_step "7/7 fangcang_realtime" bash "$RUNNER" timeout 90 python3 scripts/fangcang_realtime_refresh.py 2>&1 | tail -3

echo "[wrapper-4h] 完成 $(date '+%Y-%m-%d %H:%M:%S') | 错误数=$ERRORS"
