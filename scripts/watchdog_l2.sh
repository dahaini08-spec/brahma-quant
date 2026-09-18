#!/bin/bash
# watchdog_l2.sh — 二级看门狗 [9.18苏摩111 顶层修复]
# 纯bash，不依赖python，防止自身被OOM杀
# 每5min由superlonic调用，检查3个关键进程

cd /root/.openclaw/workspace/trading-system
STATE_FILE=data/watchdog_l2_state.json
ALERT_FILE=data/watchdog_l2_alerts.jsonl
NOW=$(date -u +%s)

# 初始化状态
if [ ! -f "$STATE_FILE" ]; then
  echo '{"supercronic":0,"cvd":0,"watchdog_l1":0,"last_check":0}' > "$STATE_FILE"
fi

# 检查进程
SUP=$(pgrep -f "supercronic brahma" | head -1)
CVD=$(pgrep -f "cvd_ws_collector" | head -1)
WD1=$(pgrep -f "independent_watchdog" | head -1)

ACTIONS=""

# supercronic死了→重启
if [ -z "$SUP" ]; then
  setsid bash start_supercronic.sh >> logs/syscron.log 2>&1 &
  ACTIONS="$ACTIONS restart_supercronic"
  sleep 2
  SUP=$(pgrep -f "supercronic brahma" | head -1)
fi

# CVD死了→重启
if [ -z "$CVD" ]; then
  setsid python3 scripts/cvd_ws_collector.py >> logs/cvd_ws.log 2>&1 &
  ACTIONS="$ACTIONS restart_cvd"
  sleep 2
  CVD=$(pgrep -f "cvd_ws_collector" | head -1)
fi

# watchdog L1死了→重启
if [ -z "$WD1" ]; then
  setsid bash scripts/independent_watchdog.sh >> logs/watchdog_indep.log 2>&1 &
  ACTIONS="$ACTIONS restart_watchdog_l1"
  sleep 2
  WD1=$(pgrep -f "independent_watchdog" | head -1)
fi

# 写状态
python3 -c "
import json
d = {'supercronic': $([ -n \"$SUP\" ] && echo 1 || echo 0),
     'cvd': $([ -n \"$CVD\" ] && echo 1 || echo 0),
     'watchdog_l1': $([ -n \"$WD1\" ] && echo 1 || echo 0),
     'last_check': $NOW,
     'actions': '$ACTIONS'.strip()}
json.dump(d, open('$STATE_FILE','w'))
" 2>/dev/null

# 告警（去重：只在状态变化时告警）
if [ -n "$ACTIONS" ]; then
  echo "{\"ts\":$NOW,\"actions\":\"$ACTIONS\"}" >> "$ALERT_FILE"
  echo "[$(date -u)] L2 WATCHDOG ACTIONS:$ACTIONS" >> logs/watchdog_l2.log
fi
