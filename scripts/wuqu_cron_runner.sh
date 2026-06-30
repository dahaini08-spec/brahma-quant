#!/bin/bash
# wuqu_cron_runner.sh — 武曲仪表盘零积分推送脚本
# 设计院 · 2026-06-14
#
# 原理：直接运行 Python 脚本，用 openclaw message send 发到对话框
# 完全不启动 AI agent，不消耗积分
#
# 系统 crontab（每10分钟）：
# */10 * * * * /root/.openclaw/workspace/trading-system/scripts/wuqu_cron_runner.sh

cd /root/.openclaw/workspace/trading-system

OUTPUT=$(python3 scripts/wuqu_dashboard.py 2>&1)

# 无新信号 → 静默退出
if [ "$OUTPUT" = "HEARTBEAT_OK" ] || [ -z "$OUTPUT" ]; then
    exit 0
fi

# 有新信号 → openclaw message send 直发 Jarvis（零积分）
# -t: Telegram chat_id（含 thread）
openclaw message send \
    --channel jarvis \
    -t "73295708" \
    --thread-id '019ed32f-c46d-72ab-9d5e-92e47b4bdcc5' \
    --message "$OUTPUT"
