#!/bin/bash
# supercronic_watchdog.sh — 检测supercronic存活，挂了自动拉起
# 2026-09-09 苏摩111封印

PID=$(pgrep -f "supercronic.*brahma_crontab")
if [ -z "$PID" ]; then
  echo "$(date -u '+%Y-%m-%d %H:%M UTC') [watchdog] supercronic未运行，正在重启..."
  bash /root/.openclaw/workspace/trading-system/start_supercronic.sh >> /root/.openclaw/workspace/trading-system/logs/syscron.log 2>&1
  sleep 2
  NEW_PID=$(pgrep -f "supercronic.*brahma_crontab")
  if [ -n "$NEW_PID" ]; then
    echo "$(date -u '+%Y-%m-%d %H:%M UTC') [watchdog] supercronic已重启 PID=$NEW_PID"
    # 推送通知
    openclaw message send -t "73295708:thread:01a07970-f8ce-706b-8bea-3c94dd055443" --channel jarvis --message "⚠️ Supercronic已重启 PID=$NEW_PID" >/dev/null 2>&1 &
  else
    echo "$(date -u '+%Y-%m-%d %H:%M UTC') [watchdog] supercronic重启失败！"
    openclaw message send -t "73295708:thread:01a07970-f8ce-706b-8bea-3c94dd055443" --channel jarvis --message "🔴 Supercronic重启失败！手动检查" >/dev/null 2>&1 &
  fi
fi
