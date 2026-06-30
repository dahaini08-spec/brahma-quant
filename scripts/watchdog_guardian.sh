#!/bin/bash
# 梵天守护双进程 watchdog_guardian.sh v2.0
# 修复：空仓时不重启ws_guardian（防进程泄漏）
# 逻辑：有持仓 → 守护ws_guardian；空仓 → 允许ws_guardian正常退出
WD=/root/.openclaw/workspace/trading-system
LOG=$WD/logs/watchdog.log
mkdir -p $WD/logs

has_positions() {
    python3 -c "
import sys, json, os
sys.path.insert(0, '$WD')
try:
    state = json.load(open('$WD/data/brahma_state.json'))
    positions = [p for p in state.get('positions', []) if p.get('status') == 'OPEN']
    print('yes' if positions else 'no')
except:
    print('no')
" 2>/dev/null
}

while true; do
    pos=$(has_positions)
    
    if [ "$pos" = "yes" ]; then
        # 有持仓：确保ws_guardian在运行
        if ! pgrep -f "python3.*ws_guardian.py" > /dev/null 2>&1; then
            echo "$(date '+%Y-%m-%d %H:%M:%S') [watchdog] 有持仓+ws_guardian宕机，重启..." >> $LOG
            setsid nohup python3 $WD/ws_guardian.py >> $WD/logs/ws_guardian.log 2>&1 &
            echo "$(date '+%Y-%m-%d %H:%M:%S') [watchdog] 重启完成 pid=$!" >> $LOG
        fi
    else
        # 空仓：不重启（允许ws_guardian正常待机/退出）
        # 只记录状态
        :
    fi
    
    sleep 60
done
