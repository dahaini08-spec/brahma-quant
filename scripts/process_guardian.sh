#!/bin/bash
# process_guardian.sh — 进程守护脚本
# 由OpenClaw cron每10分钟调用
# OpenClaw cron是gateway管理的，gateway restart后自动恢复

cd /root/.openclaw/workspace/trading-system
LOG=logs/process_guardian.log
mkdir -p logs

ts() { date -u '+%Y-%m-%dT%H:%M:%SZ'; }

RESTARTED=""

# 1. supercronic — 清理旧PID文件后重启
if ! pgrep -f "supercronic.*brahma_crontab" > /dev/null 2>&1; then
    rm -f /tmp/brahma_supercronic.pid
    nohup /root/.openclaw/workspace/trading-system/supercronic \
        /root/.openclaw/workspace/trading-system/brahma_crontab.txt \
        >> /root/.openclaw/workspace/trading-system/logs/supercronic.log 2>&1 &
    echo $! > /tmp/brahma_supercronic.pid
    RESTARTED="${RESTARTED}supercronic "
    echo "[$(ts)] RESTART supercronic pid=$!" >> "$LOG"
fi

# 2. CVD collector
if ! pgrep -f "cvd_ws_collector" > /dev/null 2>&1; then
    nohup python3 scripts/cvd_ws_collector.py >> logs/cvd_ws.log 2>&1 &
    RESTARTED="${RESTARTED}cvd "
    echo "[$(ts)] RESTART cvd_ws_collector" >> "$LOG"
fi

# 3. liqmap collector
if ! pgrep -f "liqmap_collector" > /dev/null 2>&1; then
    nohup python3 brahma_brain/liqmap_collector.py >> logs/liqmap.log 2>&1 &
    RESTARTED="${RESTARTED}liqmap "
    echo "[$(ts)] RESTART liqmap_collector" >> "$LOG"
fi

# 4. independent_watchdog (保留作为二级保护，60s循环)
if ! pgrep -f "independent_watchdog" > /dev/null 2>&1; then
    nohup bash scripts/independent_watchdog.sh >> logs/watchdog.log 2>&1 &
    RESTARTED="${RESTARTED}watchdog "
    echo "[$(ts)] RESTART independent_watchdog" >> "$LOG"
fi

# 输出结果
if [ -z "$RESTARTED" ]; then
    echo "HEARTBEAT_OK"
else
    echo "重启: ${RESTARTED}"
fi
