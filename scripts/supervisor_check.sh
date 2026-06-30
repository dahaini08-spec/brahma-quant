#!/bin/bash
# supervisor_check.sh v5.0 — 进程守护检查（梵天360重构）
# 设计院 · 2026-06-09
#
# 职责：直接检查 ws_guardian + watchdog 进程，宕机立即重启
# 原v4.0依赖supervisor（实际未安装），每次返回STARTING，已废弃
#
# 原则：5秒内完成，不允许任何阻塞操作

WD=/root/.openclaw/workspace/trading-system
LOG=$WD/logs/supervisor_check.log
mkdir -p $WD/logs

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG"; }

healed=0

# ── 1. ws_guardian ──────────────────────────────
if pgrep -f "python3.*ws_guardian.py" > /dev/null 2>&1; then
    log "ws_guardian ✅"
else
    log "ws_guardian ❌ → 重启"
    nohup python3 $WD/ws_guardian.py >> $WD/logs/ws_guardian.log 2>&1 &
    healed=$((healed+1))
fi

# ── 2. watchdog_guardian ────────────────────────
if pgrep -f "watchdog_guardian.sh" > /dev/null 2>&1; then
    log "watchdog ✅"
else
    log "watchdog ❌ → 重启"
    nohup bash $WD/scripts/watchdog_guardian.sh >> $WD/logs/watchdog.log 2>&1 &
    healed=$((healed+1))
fi

# ── 输出 ────────────────────────────────────────
if [ $healed -eq 0 ]; then
    echo "HEARTBEAT_OK"
else
    echo "HEALED: ${healed}个进程已重启"
fi
