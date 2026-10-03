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

# 5. auto_analysis新鲜度兜底 [2026-09-30 苏摩111]
# 根因：gateway重启风暴吃掉14:15班（supercronic死窗口内cron全灭）→ 数据断供到下一偶数小时班
# 本兑底由openclaw bridge每5min调用，supercronic死了也能自愈（破鸡生蛋死锁）
# 单飞：pgrep在跑不重跑 + flock锁防并发；仅当latest超过150min才补跑（正常班2h+30min余量）
LATEST="data/auto_analysis_latest.json"
if [ -f "$LATEST" ]; then
    AGE_MIN=$(( ( $(date +%s) - $(stat -c %Y "$LATEST") ) / 60 ))
    if [ "$AGE_MIN" -gt 150 ] && ! pgrep -f "battlefield_auto_analysis" > /dev/null; then
        echo "[$(date -u '+%H:%M')] auto_analysis断供(${AGE_MIN}min) → 兑底补跑(setsid)"
        setsid bash -c 'flock -n /tmp/brahma_auto_analysis.lock timeout 900 python3 scripts/battlefield_auto_analysis.py >> logs/auto_analysis.log 2>&1' < /dev/null &
    fi
else
    echo "[$(date -u '+%H:%M')] auto_analysis_latest.json缺失 → 兑底补跑(setsid)"
    setsid bash -c 'flock -n /tmp/brahma_auto_analysis.lock timeout 900 python3 scripts/battlefield_auto_analysis.py >> logs/auto_analysis.log 2>&1' < /dev/null &
fi

# 6. supercronic重启后补发缺失高价值帖型 [Fix3 2026-10-03 苏摩111]
# 根因：supercronic重启时间晚于chart(06:00)/edu(05:00,13:00)/video(08:00)/live(11:00)
# 机制：每次resurrect运行时检查今日是否已发过，没发则补发（每项最多1次/天）
# 触发：每分钟process_resurrect.sh由watchdog调用
CATCHUP_LOG="logs/square_catchup.log"
TODAY_UTC=$(date -u '+%Y-%m-%d')

_square_catchup() {
    local script=$1 marker=$2
    local stamp_file="data/.catchup_${marker}_${TODAY_UTC}"
    if [ ! -f "$stamp_file" ]; then
        # 检查today是否在post_log里有记录
        if ! grep -q "\"${marker}\"" data/square_post_log.jsonl 2>/dev/null || \
           ! grep "\"${marker}\"" data/square_post_log.jsonl | grep -q "$(date -u '+%Y-%m-%d')"; then
            echo "[$(date -u '+%H:%M')] catchup: ${marker} 今日未发，补发..." >> "$CATCHUP_LOG"
            timeout 120 python3 $script >> "$CATCHUP_LOG" 2>&1
            touch "$stamp_file"
        fi
    fi
}

# 只在特定时间窗口补发（避免凌晨补发）
HOUR_UTC=$(date -u '+%H')
if [ "$HOUR_UTC" -ge 7 ] && [ "$HOUR_UTC" -le 23 ]; then
    _square_catchup "scripts/square_chart_poster.py" "chart_post"
fi
if [ "$HOUR_UTC" -ge 6 ] && [ "$HOUR_UTC" -le 23 ]; then
    _square_catchup "scripts/square_video_poster.py" "video_post"
fi
if [ "$HOUR_UTC" -ge 6 ] && [ "$HOUR_UTC" -le 23 ]; then
    _square_catchup "scripts/square/square_edu_poster.py" "education"
fi

# 6. supercronic重启后补发缺失高价值帖型 [Fix3 2026-10-03 苏摩111]
CATCHUP_LOG="logs/square_catchup.log"
TODAY_UTC=$(date -u '+%Y-%m-%d')

_square_catchup() {
    local script=$1 marker=$2
    local stamp_file="data/.catchup_${marker}_${TODAY_UTC}"
    if [ ! -f "$stamp_file" ]; then
        if ! python3 -c "
import json,sys
from pathlib import Path
lines = Path('data/square_post_log.jsonl').read_text().splitlines() if Path('data/square_post_log.jsonl').exists() else []
today = '$(date -u +%Y-%m-%d)'
found = any('${marker}' in l and today in l for l in lines[-100:])
sys.exit(0 if found else 1)
" 2>/dev/null; then
            echo "[catchup $(date -u '+%H:%M')] ${marker} 今日未发→补发" >> "\$CATCHUP_LOG"
            cd /root/.openclaw/workspace/trading-system && timeout 120 python3 \$script >> "\$CATCHUP_LOG" 2>&1
            touch "\$stamp_file"
        fi
    fi
}

HOUR_UTC=\$(date -u '+%H')
if [ "\$HOUR_UTC" -ge 7 ] && [ "\$HOUR_UTC" -le 22 ]; then
    _square_catchup "scripts/square_chart_poster.py" "chart_post"
fi
if [ "\$HOUR_UTC" -ge 9 ] && [ "\$HOUR_UTC" -le 22 ]; then
    _square_catchup "scripts/square_video_poster.py" "video_post"
fi
if [ "\$HOUR_UTC" -ge 6 ] && [ "\$HOUR_UTC" -le 22 ]; then
    _square_catchup "scripts/square/square_edu_poster.py" "education"
fi
