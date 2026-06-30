#!/bin/bash
# Gateway重启 + 守护进程自动恢复
# 设计院 2026-05-24

BASE="/root/.openclaw/workspace/trading-system"
CONF="$BASE/supervisor/brahma.conf"
SUPERVISORD="/usr/local/bin/supervisord"

# 1. 重启 Gateway
GW_PID=$(ps aux | grep 'node dist/index.js' | grep -v grep | awk '{print $2}' | head -1)
if [ -n "$GW_PID" ]; then
    MEM=$(ps -o rss= -p $GW_PID 2>/dev/null | awk '{printf "%.0fMB", $1/1024}')
    kill -15 $GW_PID 2>/dev/null
    echo "Gateway重启: PID=$GW_PID 释放内存=$MEM"
else
    echo "Gateway未运行"
fi

# 2. 等待Gateway重启完成
sleep 8

# 3. 确保supervisord在运行
if ! pgrep -f "supervisord.*brahma" > /dev/null 2>&1; then
    $SUPERVISORD -c $CONF
    echo "supervisord重启 ✅"
    sleep 5
fi

# 4. 验证4个进程心跳
python3 -c "
import json,time
checks=[
    ('ws_guardian','data/ws_guardian_state.json',120),
    ('watchdog','data/watchdog_state.json',180),
    ('brahma_daemon','data/brahma_daemon_state.json',700),
    ('system_daemon','data/system_daemon_state.json',120),
]
all_ok=True
for name,path,thresh in checks:
    try:
        d=json.load(open('$BASE/'+path))
        age=time.time()-d['ts']
        ok=age<thresh
        if not ok: all_ok=False
        print('{} {} {:.0f}s'.format(name,'✅' if ok else '🔴',age))
    except: print(name,'🔴'); all_ok=False
if all_ok: print('守护进程全部正常 ✅')
else: print('部分守护进程异常，需检查')
"
