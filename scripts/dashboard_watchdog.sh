#!/bin/bash
PID_FILE=/root/.openclaw/workspace/trading-system/logs/dashboard.pid
LOG=/root/.openclaw/workspace/trading-system/logs/dashboard.log

if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if ! ps -p $PID > /dev/null 2>&1; then
        echo "$(date): 看板崩溃，自动重启..." >> "$LOG"
        cd /root/.openclaw/workspace/trading-system
        nohup python3 scripts/brahma_dashboard_server.py --port 80 >> "$LOG" 2>&1 &
        echo $! > "$PID_FILE"
        echo "$(date): 重启完成 PID=$(cat $PID_FILE)" >> "$LOG"
    fi
else
    cd /root/.openclaw/workspace/trading-system
    nohup python3 scripts/brahma_dashboard_server.py --port 80 >> "$LOG" 2>&1 &
    echo $! > "$PID_FILE"
fi
