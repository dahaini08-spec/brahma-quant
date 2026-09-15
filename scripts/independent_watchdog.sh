#!/bin/bash
# independent_watchdog.sh — 独立看门狗 v2 [2026-09-14 苏摩111]
# 修复刷屏：告警去重 + 状态文件 + 恢复通知
# 运行方式：nohup bash scripts/independent_watchdog.sh >> logs/watchdog.log 2>&1 &
# 每60s检查一次

cd /root/.openclaw/workspace/trading-system
STATE_FILE="data/watchdog_indep_state.json"

while true; do
  TS=$(date -u '+%Y-%m-%d %H:%M:%S UTC')
  ALERT=""
  RECOVERED=""

  # 进程检查+自动重启
  SP_PID=$(pgrep -f "supercronic.*brahma_crontab" | head -1)
  if [ -z "$SP_PID" ]; then
    ALERT="${ALERT}⚠️ supercronic未运行 "
    bash start_supercronic.sh >> logs/syscron.log 2>&1
    sleep 3
    SP_PID=$(pgrep -f "supercronic.*brahma_crontab" | head -1)
    ALERT="${ALERT}→ 重启PID=$SP_PID "
  fi

  CVD_PID=$(pgrep -f "cvd_ws_collector" | head -1)
  if [ -z "$CVD_PID" ]; then
    ALERT="${ALERT}⚠️ CVD未运行 "
    nohup python3 scripts/cvd_ws_collector.py >> logs/cvd_ws.log 2>&1 &
    sleep 2
    CVD_PID=$(pgrep -f "cvd_ws_collector" | head -1)
    ALERT="${ALERT}→ CVD重启PID=$CVD_PID "
  fi

  LIQ_PID=$(pgrep -f "liqmap_collector" | head -1)
  if [ -z "$LIQ_PID" ]; then
    ALERT="${ALERT}⚠️ liqmap未运行 "
    nohup python3 brahma_brain/liqmap_collector.py >> logs/liqmap.log 2>&1 &
    sleep 2
    LIQ_PID=$(pgrep -f "liqmap_collector" | head -1)
    ALERT="${ALERT}→ liqmap重启PID=$LIQ_PID "
  fi

  # === 去重逻辑 ===
  CUR_HASH=$(echo "$ALERT" | md5sum | cut -d' ' -f1)
  PREV_HASH=""
  if [ -f "$STATE_FILE" ]; then
    PREV_HASH=$(python3 -c "import json; print(json.load(open('$STATE_FILE')).get('hash',''))" 2>/dev/null)
  fi

  if [ -z "$ALERT" ] && [ -n "$PREV_HASH" ] && [ "$PREV_HASH" != "" ]; then
    # 之前有告警，现在恢复了
    RECOVERED="✅ 进程全部恢复"
    echo '{"hash": ""}' > "$STATE_FILE"
  elif [ -n "$ALERT" ]; then
    if [ "$CUR_HASH" == "$PREV_HASH" ]; then
      # 同样告警，不推送（去重）
      echo "$TS [DEDUP] $ALERT" >> logs/watchdog.log
      sleep 60
      continue
    else
      # 新告警或变化，更新state
      echo "{\"hash\": \"$CUR_HASH\"}" > "$STATE_FILE"
    fi
  fi

  # 日志
  if [ -n "$ALERT" ]; then
    echo -e "$TS\n$ALERT" >> logs/watchdog.log
  elif [ -n "$RECOVERED" ]; then
    echo "$TS $RECOVERED" >> logs/watchdog.log
  else
    MIN=$(date -u '+%-M')
    if [ $((MIN % 10)) -eq 0 ]; then
      echo "$TS ✅ all alive (sp=$SP_PID cvd=$CVD_PID liq=$LIQ_PID)" >> logs/watchdog.log
    fi
  fi

  # 推送（仅新告警或恢复）
  if [ -n "$ALERT" ] || [ -n "$RECOVERED" ]; then
    MSG="${ALERT}${RECOVERED}"
    openclaw message send -t "73295708:thread:01a07628-0405-7e85-a34b-e68cd029dfc6" --channel jarvis --message "🐕 独立看门狗: $MSG" >/dev/null 2>&1 &
  fi

  sleep 60
done
