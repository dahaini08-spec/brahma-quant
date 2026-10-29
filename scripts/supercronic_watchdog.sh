#!/bin/bash
# [封印 2026-10-29 苏摩111] supercronic独立watchdog脚本
SCRON="/root/.openclaw/workspace/trading-system/supercronic"
CRONTAB="/root/.openclaw/workspace/trading-system/brahma_crontab.txt"
LOG="/root/.openclaw/workspace/trading-system/logs/supercronic.log"

# 检查supercronic真实进程（通过readlink /proc/pid/exe验证二进制）
SC_ALIVE=false
for pid in $(pgrep -f "supercronic.*brahma_crontab" 2>/dev/null); do
    if readlink "/proc/$pid/exe" 2>/dev/null | grep -q "supercronic"; then
        SC_ALIVE=true
        break
    fi
done

if ! $SC_ALIVE; then
    echo "[watchdog $(date -u '+%H:%M UTC')] supercronic死亡 → 拉起"
    setsid "$SCRON" "$CRONTAB" >> "$LOG" 2>&1 < /dev/null &
    sleep 2
    for pid in $(pgrep -f "supercronic.*brahma_crontab" 2>/dev/null); do
        if readlink "/proc/$pid/exe" 2>/dev/null | grep -q "supercronic"; then
            echo "[watchdog] 拉起成功 PID=$pid"
            exit 0
        fi
    done
    echo "[watchdog] 拉起失败！"
    exit 1
else
    echo "HEARTBEAT_OK"
fi
