#!/bin/bin/env bash
# independent_watchdog.sh — 独立看门狗，不依赖supercronic
# [9.13封印 苏摩111] 解决循环依赖：supercronic死→CVD死→无人重启
# 运行方式：nohup bash scripts/independent_watchdog.sh >> logs/watchdog.log 2>&1 &
# 每60s检查一次，不依赖任何其他进程

cd /root/.openclaw/workspace/trading-system

while true; do
  TS=$(date -u '+%Y-%m-%d %H:%M:%S UTC')
  ALERT=""

  # 1. supercronic存活检查
  SP_PID=$(pgrep -f "supercronic.*brahma_crontab" | head -1)
  if [ -z "$SP_PID" ]; then
    ALERT="${ALERT}⚠️ supercronic未运行\n"
    bash start_supercronic.sh >> logs/syscron.log 2>&1
    sleep 3
    SP_NEW=$(pgrep -f "supercronic.*brahma_crontab" | head -1)
    if [ -n "$SP_NEW" ]; then
      ALERT="${ALERT}→ 已自动重启 PID=$SP_NEW\n"
    else
      ALERT="${ALERT}→ 🔴 重启失败！\n"
    fi
  fi

  # 2. CVD采集器存活检查
  CVD_PID=$(pgrep -f "cvd_ws_collector" | head -1)
  if [ -z "$CVD_PID" ]; then
    ALERT="${ALERT}⚠️ CVD采集器未运行\n"
    nohup python3 scripts/cvd_ws_collector.py >> logs/cvd_ws.log 2>&1 &
    sleep 2
    CVD_NEW=$(pgrep -f "cvd_ws_collector" | head -1)
    if [ -n "$CVD_NEW" ]; then
      ALERT="${ALERT}→ CVD已重启 PID=$CVD_NEW\n"
    fi
  fi

  # 3. liqmap采集器存活检查
  LIQ_PID=$(pgrep -f "liqmap_collector" | head -1)
  if [ -z "$LIQ_PID" ]; then
    ALERT="${ALERT}⚠️ liqmap采集器未运行\n"
    nohup python3 brahma_brain/liqmap_collector.py >> logs/liqmap.log 2>&1 &
    sleep 2
    LIQ_NEW=$(pgrep -f "liqmap_collector" | head -1)
    if [ -n "$LIQ_NEW" ]; then
      ALERT="${ALERT}→ liqmap已重启 PID=$LIQ_NEW\n"
    fi
  fi

  # 4. regime_state.json健康检查
  python3 -c "import json; json.load(open('data/regime_state.json'))" 2>/dev/null
  if [ $? -ne 0 ]; then
    ALERT="${ALERT}⚠️ regime_state.json损坏\n"
  fi

  # 5. 模块注册表检查（每10min一次）
  MIN=$(date -u '+%M')
  if [ "$((MIN % 10))" -eq "0" ]; then
    MOD_RESULT=$(python3 scripts/module_check.py 2>&1)
    echo "$MOD_RESULT" | grep -q "缺失" && ALERT="${ALERT}${MOD_RESULT}\n"
  fi

  # 有告警则推送
  if [ -n "$ALERT" ]; then
    echo -e "$TS\n$ALERT" >> logs/watchdog.log
    # 推送到Jarvis
    MSG=$(echo -e "$ALERT" | tr '\n' ' ' | sed 's/  */ /g')
    openclaw message send -t "73295708:thread:01a07628-0405-7e85-a34b-e68cd029dfc6" --channel jarvis --message "🐕 独立看门狗: $MSG" >/dev/null 2>&1 &
  else
    # 静默，每10min打一次心跳
    MIN=$(date -u '+%M')
    if [ "$((MIN % 10))" -eq "0" ]; then
      echo "$TS ✅ all alive (sp=$SP_PID cvd=$CVD_PID liq=$LIQ_PID)" >> logs/watchdog.log
    fi
  fi

  sleep 60
done
