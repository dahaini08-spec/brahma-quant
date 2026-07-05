#!/bin/bash
# Gateway重启后自动恢复守护进程（零AI，系统cron执行）
BASE="/root/.openclaw/workspace/trading-system"
LOG="$BASE/logs/gateway_recover.log"
# SSOT: alerts/.env JARVIS_USER_ID + JARVIS_THREAD_ID
TARGET="73295708:t:019f309c-609b-7a75-a195-e221e5927c63"

ts() { date -u '+%Y-%m-%d %H:%M:%S UTC'; }
log() { echo "[$(ts)] $1" >> "$LOG"; }

log "=== 恢复链启动 ==="
pkill -f ws_guardian.py 2>/dev/null
pkill -f watchdog.py    2>/dev/null
sleep 2

nohup python3 "$BASE/ws_guardian.py" > "$BASE/logs/ws_guardian.log" 2>&1 & WS_PID=$!
nohup python3 "$BASE/watchdog.py"    > "$BASE/logs/watchdog.log"    2>&1 & WD_PID=$!
sleep 5

WS_OK=$(ps aux | grep ws_guardian | grep -v grep | wc -l)
WD_OK=$(ps aux | grep watchdog    | grep -v grep | wc -l)

if [ "$WS_OK" -gt 0 ] && [ "$WD_OK" -gt 0 ]; then
    MSG="✅ Gateway重启恢复完成 | ws_guardian PID=$WS_PID ✅ | watchdog PID=$WD_PID ✅ | $(ts)"
    log "恢复成功"
else
    MSG="⚠️ 恢复异常：ws=$WS_OK watchdog=$WD_OK | 请检查日志"
    log "恢复异常"
fi

openclaw message send --channel jarvis --target "$TARGET" --silent --message "$MSG" 2>/dev/null
