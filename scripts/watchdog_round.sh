#!/bin/bash
# watchdog_round.sh — 单轮看门狗（由supercronic每10分钟调度）[2026-09-14 v2 苏摩111]
# 修复刷屏问题：告警去重 + 状态文件 + 恢复通知
# 原理：同一告警只推一次，恢复时推一次"已恢复"，正常时不推

cd /root/.openclaw/workspace/trading-system
TS=$(date -u '+%Y-%m-%d %H:%M:%S UTC')
ALERT=""
RECOVERED=""
STATE_FILE="data/watchdog_alert_state.json"

# 加载上次告警状态
python3 -c "
import json, os
f = '$STATE_FILE'
if os.path.exists(f):
    print(open(f).read())
else:
    print('{}')
" > /tmp/wd_state_$$ 2>/dev/null
PREV_ALERTS=$(cat /tmp/wd_state_$$ 2>/dev/null || echo '{}')
rm -f /tmp/wd_state_$$

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

# 缓存freshness检查
NOW=$(date +%s)
check_cache() {
    local f="data/$1"
    local ttl="$2"
    local name="$3"
    if [ -f "$f" ]; then
        local mtime=$(stat -c %Y "$f" 2>/dev/null || echo 0)
        local age=$((NOW - mtime))
        if [ $age -gt $ttl ]; then
            local age_h=$((age / 3600))
            ALERT="${ALERT}⚠️ ${name}过期${age_h}h "
        fi
    else
        ALERT="${ALERT}⚠️ ${name}不存在 "
    fi
}

check_cache "brahma_state_btc.json" 14400 "brahma_state_btc"
check_cache "brahma_state_eth.json" 14400 "brahma_state_eth"
check_cache "gex_state.json" 86400 "GEX"
check_cache "vol_beta_state.json" 43200 "vol_beta"
check_cache "macro_state.json" 86400 "宏观"
check_cache "cvd_realtime_btcusdt.json" 3600 "CVD_BTC"
check_cache "cvd_realtime_ethusdt.json" 3600 "CVD_ETH"
check_cache "liq_heatmap_BTCUSDT.json" 14400 "清算热图BTC"
check_cache "liq_heatmap_ETHUSDT.json" 14400 "清算热图ETH"
check_cache "circuit_breaker.json" 3600 "风控熔断"
check_cache "drawdown_state.json" 3600 "回撤"
check_cache "antifragile_state.json" 86400 "反脆弱"
check_cache "regime_state.json" 3600 "实时体制"

# regime_state.json健康检查
python3 -c "import json; json.load(open('data/regime_state.json'))" 2>/dev/null
if [ $? -ne 0 ]; then
    ALERT="${ALERT}⚠️ regime_state.json损坏 "
fi

# === 去重逻辑 ===
# 当前告警items
CUR_ALERTS=$(echo "$ALERT" | tr ' ' '\n' | grep '⚠️' | sort | uniq)
CUR_SET=$(echo "$CUR_ALERTS" | md5sum | cut -d' ' -f1)

# 上次告警items
PREV_SET=$(echo "$PREV_ALERTS" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d.get('alert_hash', ''))
except:
    print('')
" 2>/dev/null)

# 恢复检测
if [ -z "$ALERT" ] && [ -n "$PREV_SET" ] && [ "$PREV_SET" != "" ]; then
    # 之前有告警，现在全恢复了
    RECOVERED="✅ 所有告警已恢复"
    echo '{"alert_hash": "", "alerts": []}' > "$STATE_FILE"
elif [ -n "$ALERT" ]; then
    # 有告警，检查是否和上次一样
    if [ "$CUR_SET" == "$PREV_SET" ]; then
        # 同样的告警，不推送（去重）
        echo "$TS [DEDUP] $ALERT" >> logs/watchdog_v2.log
        # 仍然更新state文件的时间戳
        python3 -c "
import json
d = {'alert_hash': '$CUR_SET', 'alerts': '''$CUR_ALERTS'''.strip().split('\n')}
json.dump(d, open('$STATE_FILE', 'w'))
" 2>/dev/null
        exit 0
    else
        # 新告警或告警变化，推送+更新state
        python3 -c "
import json
d = {'alert_hash': '$CUR_SET', 'alerts': '''$CUR_ALERTS'''.strip().split('\n')}
json.dump(d, open('$STATE_FILE', 'w'))
" 2>/dev/null
    fi
fi

# 输出日志
if [ -n "$ALERT" ]; then
    echo "$TS $ALERT" >> logs/watchdog_v2.log
elif [ -n "$RECOVERED" ]; then
    echo "$TS $RECOVERED" >> logs/watchdog_v2.log
else
    echo "$TS ✅ all alive (sp=$SP_PID cvd=$CVD_PID liq=$LIQ_PID)" >> logs/watchdog_v2.log
fi

# 推送（仅新告警或恢复）
if [ -n "$ALERT" ] || [ -n "$RECOVERED" ]; then
    MSG="${ALERT}${RECOVERED}"
    openclaw message send \
        -t "73295708:thread:01a07628-0405-7e85-a34b-e68cd029dfc6" \
        --channel jarvis \
        --message "🐕看门狗: $MSG" \
        >/dev/null 2>&1 || true
fi
