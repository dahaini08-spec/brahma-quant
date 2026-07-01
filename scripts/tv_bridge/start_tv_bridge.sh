#!/bin/bash
# TV Bridge自检启动脚本
PID_FILE=/tmp/tv_webhook.pid
LOG_FILE=/tmp/tv_webhook.log
SCRIPT=/root/.openclaw/workspace/trading-system/scripts/tv_bridge/tv_webhook_server.py

# 检查是否已在运行
if [ -f "$PID_FILE" ]; then
    PID=$(cat $PID_FILE)
    if kill -0 $PID 2>/dev/null; then
        echo "[TV-Bridge] 已在运行 PID=$PID"
        exit 0
    fi
fi

# 启动服务
nohup python3 $SCRIPT 5678 >> $LOG_FILE 2>&1 &
echo $! > $PID_FILE
echo "[TV-Bridge] 启动完成 PID=$(cat $PID_FILE)"
