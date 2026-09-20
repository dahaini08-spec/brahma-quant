#!/bin/bash
# brahma_autostart.sh — 梵天4进程自启脚本
# [9.19 设计院封印 苏摩111] 防止服务器重启后进程全死
#
# 环境约束：无systemd、无rc.local → 用独立看门狗+启动脚本双保险
# 架构：
#   1. 本脚本启动4个进程
#   2. independent_watchdog.sh 60s循环监控+自动重启
#   3. 本脚本可通过cron @reboot 或 OpenClaw heartbeat调用
#
# 用法:
#   bash scripts/brahma_autostart.sh          # 启动
#   bash scripts/brahma_autostart.sh --check  # 仅检查不启动

BASE="/root/.openclaw/workspace/trading-system"
LOG="$BASE/logs"
PY="$BASE/venv/bin/python3"

# 依赖恢复：仅检查libgomp（秒级），跳过jesse重装（start_supercronic.sh里会超时）
WORKSPACE_GOMP=/root/.openclaw/workspace/trading-system/libgomp.so.1
TORCH_GOMP=/root/.openclaw/workspace/trading-system/venv/lib/python3.11/site-packages/torch/lib/libgomp.so.1
if [ ! -f /usr/local/lib/libgomp.so.1 ]; then
    ln -sf "$TORCH_GOMP" /usr/local/lib/libgomp.so.1 2>/dev/null
    ldconfig 2>/dev/null
fi

# ── 检查模式 ──
if [ "$1" = "--check" ]; then
    echo "=== 进程状态检查 ==="
    pgrep -f "supercronic.*brahma_crontab" > /dev/null && echo "✅ supercronic" || echo "❌ supercronic"
    pgrep -f "cvd_ws_collector" > /dev/null && echo "✅ CVD" || echo "❌ CVD"
    pgrep -f "liqmap_collector" > /dev/null && echo "✅ liqmap" || echo "❌ liqmap"
    pgrep -f "independent_watchdog" > /dev/null && echo "✅ watchdog" || echo "❌ watchdog"
    exit 0
fi

# ── 启动模式 ──
echo "=== 梵天4进程自启 ==="

# 1. supercronic
if ! pgrep -f "supercronic.*brahma_crontab" > /dev/null; then
    cd "$BASE"
    nohup ./supercronic brahma_crontab.txt >> "$LOG/supercronic.log" 2>&1 &
    echo "✅ supercronic PID=$!"
else
    echo "✅ supercronic 已运行"
fi

# 2. CVD采集器
if ! pgrep -f "cvd_ws_collector" > /dev/null; then
    cd "$BASE"
    nohup $PY scripts/cvd_ws_collector.py >> "$LOG/cvd_collector.log" 2>&1 &
    echo "✅ CVD PID=$!"
else
    echo "✅ CVD 已运行"
fi

# 3. liqmap采集器
if ! pgrep -f "liqmap_collector" > /dev/null; then
    cd "$BASE"
    nohup python3 brahma_brain/liqmap_collector.py >> "$LOG/liqmap.log" 2>&1 &
    echo "✅ liqmap PID=$!"
else
    echo "✅ liqmap 已运行"
fi

# 4. 独立看门狗
if ! pgrep -f "independent_watchdog" > /dev/null; then
    cd "$BASE"
    nohup bash scripts/independent_watchdog.sh >> "$LOG/watchdog.log" 2>&1 &
    echo "✅ watchdog PID=$!"
else
    echo "✅ watchdog 已运行"
fi

sleep 2
echo "=== 启动完成 ==="
bash "$0" --check
