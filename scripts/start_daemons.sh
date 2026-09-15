#!/bin/bash
# start_daemons.sh — 一键启动全部守护进程 [2026-09-13 苏摩111封印]
# 启动顺序：supercronic → CVD → liqmap → 看门狗A → 看门狗B
# 使用setsid创建新session，彻底脱离parent shell

cd /root/.openclaw/workspace/trading-system
mkdir -p logs

echo "=== 启动梵天守护进程集群 ==="

# 0. 依赖恢复
bash start_supercronic.sh >> logs/syscron.log 2>&1
sleep 1

# 1. supercronic
SP_PID=$(pgrep -f "supercronic.*brahma_crontab" | head -1)
if [ -z "$SP_PID" ]; then
    bash start_supercronic.sh >> logs/syscron.log 2>&1
    sleep 2
    SP_PID=$(pgrep -f "supercronic.*brahma_crontab" | head -1)
fi
echo "supercronic: PID=$SP_PID"

# 2. CVD采集器
CVD_PID=$(pgrep -f "cvd_ws_collector" | head -1)
if [ -z "$CVD_PID" ]; then
    setsid python3 scripts/cvd_ws_collector.py >> logs/cvd_ws.log 2>&1 &
    sleep 2
    CVD_PID=$(pgrep -f "cvd_ws_collector" | head -1)
fi
echo "CVD采集器: PID=$CVD_PID"

# 3. liqmap采集器
LIQ_PID=$(pgrep -f "liqmap_collector" | head -1)
if [ -z "$LIQ_PID" ]; then
    setsid python3 brahma_brain/liqmap_collector.py >> logs/liqmap.log 2>&1 &
    sleep 2
    LIQ_PID=$(pgrep -f "liqmap_collector" | head -1)
fi
echo "liqmap采集器: PID=$LIQ_PID"

# 4. 看门狗A（setsid彻底脱离session）
WDA_PID=$(pgrep -f "daemon_watchdog.sh A" | head -1)
if [ -z "$WDA_PID" ]; then
    setsid bash scripts/daemon_watchdog.sh A >> logs/watchdog_v2.log 2>&1 &
    sleep 2
    WDA_PID=$(pgrep -f "daemon_watchdog.sh A" | head -1)
fi
echo "看门狗A: PID=$WDA_PID"

# 5. 看门狗B
WDB_PID=$(pgrep -f "daemon_watchdog.sh B" | head -1)
if [ -z "$WDB_PID" ]; then
    setsid bash scripts/daemon_watchdog.sh B >> logs/watchdog_v2.log 2>&1 &
    sleep 2
    WDB_PID=$(pgrep -f "daemon_watchdog.sh B" | head -1)
fi
echo "看门狗B: PID=$WDB_PID"

echo ""
echo "=== 守护进程集群状态 ==="
echo "supercronic:    $([ -n "$SP_PID" ] && echo "✅ PID=$SP_PID" || echo "❌")"
echo "CVD采集器:      $([ -n "$CVD_PID" ] && echo "✅ PID=$CVD_PID" || echo "❌")"
echo "liqmap采集器:   $([ -n "$LIQ_PID" ] && echo "✅ PID=$LIQ_PID" || echo "❌")"
echo "看门狗A:        $([ -n "$WDA_PID" ] && echo "✅ PID=$WDA_PID" || echo "❌")"
echo "看门狗B:        $([ -n "$WDB_PID" ] && echo "✅ PID=$WDB_PID" || echo "❌")"
echo ""
echo "看门狗功能：进程存活检查 + 缓存freshness告警 + 双看门狗互看 + Jarvis推送"
