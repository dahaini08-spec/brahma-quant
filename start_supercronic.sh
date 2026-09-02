#!/bin/bash
SCRON=/root/.openclaw/workspace/trading-system/supercronic
CRONTAB=/root/.openclaw/workspace/trading-system/brahma_crontab.txt
LOG=/root/.openclaw/workspace/trading-system/logs/supercronic.log
PIDFILE=/tmp/brahma_supercronic.pid

mkdir -p "$(dirname $LOG)"

if [ -f "$PIDFILE" ] && kill -0 "$(cat $PIDFILE)" 2>/dev/null; then
    echo "supercronic already running (pid=$(cat $PIDFILE))"
    exit 0
fi

nohup "$SCRON" "$CRONTAB" >> "$LOG" 2>&1 &
echo $! > "$PIDFILE"
echo "supercronic started pid=$(cat $PIDFILE)"
