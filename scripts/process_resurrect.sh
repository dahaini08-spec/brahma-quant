#!/bin/bash
# process_resurrect.sh — 进程复活器 v2.1 [9.23 苏摩111 三方联合修复]
# 根因：nohup进程随exec session死 + gateway重启(SIGKILL)带走全家族
# v2.1：setsid完全脱离控制终端，父进程=init(1)，gateway重启不再带走
# 注意：gateway重启后crontab也会停，所以本脚本由两条路触发：
#   a) supercronic活着：每分钟跑
#   b) supercronic死了：independent_watchdog(若也死)则依赖下次任何人跑exec时手动兜底
# 兜底三：scripts/watchdog_bridge.sh 由OpenClaw cron每5min调用（cron任务独立于gateway）

cd /root/.openclaw/workspace/trading-system

# 1. supercronic
if ! pgrep -f "supercronic.*brahma_crontab" > /dev/null; then
    echo "[$(date -u '+%H:%M')] supercronic死 → 拉起(setsid)"
    setsid ./start_supercronic.sh >> logs/supercronic.log 2>&1 < /dev/null &
    sleep 2
fi

# 2. CVD采集器
if ! pgrep -f "cvd_ws_collector" > /dev/null; then
    echo "[$(date -u '+%H:%M')] CVD死 → 拉起(setsid)"
    setsid python3 scripts/cvd_ws_collector.py >> logs/cvd_ws.log 2>&1 < /dev/null &
    sleep 1
fi

# 3. liqmap采集器
if ! pgrep -f "liqmap_collector" > /dev/null; then
    echo "[$(date -u '+%H:%M')] liqmap死 → 拉起(setsid)"
    setsid python3 brahma_brain/liqmap_collector.py >> logs/liqmap.log 2>&1 < /dev/null &
    sleep 1
fi

# 4. watchdog（单实例锁版本，通配模式）
if ! pgrep -f "bash.*independent_watchdog\.sh" > /dev/null; then
    echo "[$(date -u '+%H:%M')] watchdog死 → 拉起(setsid)"
    setsid bash scripts/independent_watchdog.sh >> logs/watchdog.log 2>&1 < /dev/null &
    sleep 1
fi
