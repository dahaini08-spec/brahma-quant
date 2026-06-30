#!/bin/bash
LOG=/root/.openclaw/workspace/trading-system/logs/dashboard.log
PID_FILE=/root/.openclaw/workspace/trading-system/logs/dashboard.pid
PORT=80

[ -f "$PID_FILE" ] && kill $(cat "$PID_FILE") 2>/dev/null
fuser -k ${PORT}/tcp 2>/dev/null
sleep 1

cd /root/.openclaw/workspace/trading-system
nohup python3 scripts/brahma_dashboard_server.py --port $PORT >> "$LOG" 2>&1 &
echo $! > "$PID_FILE"
sleep 3

PID=$(cat "$PID_FILE")
if ps -p $PID > /dev/null 2>&1; then
    echo "✅ 看板已启动 PID=$PID PORT=$PORT"
else
    echo "❌ 启动失败"
    tail -5 "$LOG"
fi
