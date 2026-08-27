#!/bin/bash
# paper_engine 直接执行脚本（noai，带超时保护）
cd /root/.openclaw/workspace/trading-system

FREE_MB=$(free -m | awk '/^Mem:/{print $7}')
if [ "$FREE_MB" -lt 300 ]; then
    echo "HEARTBEAT_OK"
    exit 0
fi

timeout 80 /root/.openclaw/workspace/trading-system/venv/bin/python3 \
    scripts/paper_engine.py 2>&1 | tail -5

echo "HEARTBEAT_OK"
