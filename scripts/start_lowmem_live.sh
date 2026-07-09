#!/usr/bin/env bash
# scripts/start_lowmem_live.sh
# No-Swap LowMem Live Lite 启动脚本
# 裁决封印: 2026-07-09
set -euo pipefail

cd /root/.openclaw/workspace/trading-system

# ── 加载 .env ──
if [ -f .env ]; then
    set -a; source .env; set +a
fi

# ── LowMem 模式环境变量 ──
export BRAHMA_MODE=FULL_AUTO_LIVE_LITE_LOWMEM
export BRAHMA_LOWMEM=true
export BRAHMA_ALLOW_LIVE_ORDER=${BRAHMA_ALLOW_LIVE_ORDER:-true}

export BRAHMA_SYMBOLS=ETHUSDT,BTCUSDT
export BRAHMA_MAX_POSITIONS=1
export BRAHMA_MAX_OPEN_ORDERS=1
export BRAHMA_ALLOW_MARKET_ORDER=false

export BRAHMA_RSS_WARN_MB=300
export BRAHMA_RSS_NO_NEW_POSITION_MB=450
export BRAHMA_RSS_SHUTDOWN_MB=600

# ── Python 内存优化 ──
export PYTHONUNBUFFERED=1
export PYTHONMALLOC=malloc
export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1

# ── 单进程虚拟内存上限 768MB ──
ulimit -v 786432 2>/dev/null || echo "[warn] ulimit -v not supported"

echo "[start_lowmem_live] mode=LOWMEM allow_live=${BRAHMA_ALLOW_LIVE_ORDER} $(date -u +%H:%M UTC)"

# flock 防多进程
exec flock -n /tmp/brahma_auto_executor.lock \
    python3 scripts/auto_executor.py
