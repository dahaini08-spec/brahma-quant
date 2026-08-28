#!/bin/bash
# wrapper_1h.sh — 每小时任务串行漏斗
# 合并: oi-advanced-scanner + pump-hunter + news-scraper + auto-executor-run + kronos-mini-refresh
# 2026-08-28 苏摩111修复：去掉 set -e，每步独立容错

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

echo "[wrapper-1h] 启动 $(date '+%Y-%m-%d %H:%M:%S')"

run_step "1/5 oi_scanner" bash "$RUNNER" timeout 60 python3 scripts/oi_advanced_scanner.py 2>&1 | tail -2

run_step "1b paper_bridge" bash "$RUNNER" timeout 20 python3 scripts/paper_auto_bridge.py --source oi --no-notify 2>&1 | tail -2

run_step "2/5 pump_hunter" bash "$RUNNER" timeout 30 python3 scripts/pump_hunter_executor.py 2>&1 | tail -2

run_step "3/5 news_scraper" bash "$RUNNER" timeout 30 python3 scripts/news_scraper.py 2>&1 | tail -2

run_step "4/5 brahma_cpu" bash "$RUNNER" timeout 40 python3 brahma_brain/brahma_cpu.py --trigger 2>&1 | tail -2

run_step "5/5 kronos_refresh" bash "$RUNNER" timeout 30 python3 -c "
import sys
sys.path.insert(0,'/root/.openclaw/workspace/trading-system/brahma_brain')
sys.path.insert(0,'/root/.openclaw/workspace/trading-system')
import kronos_bridge as kb
kb._loaded=False; kb._predictor=None
for s in ['BTCUSDT','ETHUSDT']:
    kb._run_kronos(None, s)
print('Kronos刷新完成')
" 2>&1 | tail -2

echo "[wrapper-1h] 完成 $(date '+%Y-%m-%d %H:%M:%S') | 错误数=$ERRORS"
