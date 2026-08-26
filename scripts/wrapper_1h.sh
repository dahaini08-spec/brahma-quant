#!/bin/bash
# wrapper_1h.sh — 每小时任务串行漏斗
# 合并: oi-advanced-scanner + pump-hunter + news-scraper + auto-executor-run + kronos-mini-refresh
# 2026-08-26 梵天设计院 苏摩P1P2封印

set -e
CD="cd /root/.openclaw/workspace/trading-system"
RUNNER="/root/.openclaw/workspace/trading-system/scripts/cron_noai_runner.sh"

echo "[wrapper-1h] 启动 $(date '+%Y-%m-%d %H:%M:%S')"

echo "[1/5] oi_advanced_scanner + paper_auto_bridge"
bash "$RUNNER" timeout 60 python3 scripts/oi_advanced_scanner.py 2>&1 | tail -3
bash "$RUNNER" timeout 20 python3 scripts/paper_auto_bridge.py --source oi --no-notify 2>&1 | tail -2

echo "[2/5] pump_hunter"
bash "$RUNNER" timeout 30 python3 scripts/pump_hunter_executor.py 2>&1 | tail -3

echo "[3/5] news_scraper"
bash "$RUNNER" timeout 30 python3 scripts/news_scraper.py 2>&1 | tail -3

echo "[4/5] brahma_cpu --trigger"
bash "$RUNNER" timeout 40 python3 brahma_brain/brahma_cpu.py --trigger 2>&1 | tail -3

echo "[5/5] kronos_mini_refresh"
bash "$RUNNER" timeout 30 python3 -c "
import sys
sys.path.insert(0,'/root/.openclaw/workspace/trading-system/brahma_brain')
sys.path.insert(0,'/root/.openclaw/workspace/trading-system')
import kronos_bridge as kb
kb._loaded=False; kb._predictor=None
for s in ['BTCUSDT','ETHUSDT']:
    kb._run_kronos(None, s)
print('Kronos刷新完成')
" 2>&1 | tail -3

echo "[wrapper-1h] 完成 $(date '+%Y-%m-%d %H:%M:%S')"
