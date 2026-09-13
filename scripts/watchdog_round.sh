#!/bin/bash
# watchdog_round.sh — 单轮看门狗（由supercronic每2分钟调度）[2026-09-13 苏摩111封印]
# 替代长驻daemon_watchdog.sh，避免容器reaper杀长驻进程
# 每次运行：检查进程→重启→检查缓存freshness→告警→退出

cd /root/.openclaw/workspace/trading-system
TS=$(date -u '+%Y-%m-%d %H:%M:%S UTC')
ALERT=""

# 1. supercronic存活检查（不该需要——但防止万一）
SP_PID=$(pgrep -f "supercronic.*brahma_crontab" | head -1)
if [ -z "$SP_PID" ]; then
    ALERT="${ALERT}⚠️ supercronic未运行 "
    bash start_supercronic.sh >> logs/syscron.log 2>&1
    sleep 3
    SP_NEW=$(pgrep -f "supercronic.*brahma_crontab" | head -1)
    ALERT="${ALERT}→ 重启PID=$SP_NEW "
fi

# 2. CVD采集器存活检查
CVD_PID=$(pgrep -f "cvd_ws_collector" | head -1)
if [ -z "$CVD_PID" ]; then
    ALERT="${ALERT}⚠️ CVD未运行 "
    nohup python3 scripts/cvd_ws_collector.py >> logs/cvd_ws.log 2>&1 &
    sleep 2
    CVD_NEW=$(pgrep -f "cvd_ws_collector" | head -1)
    ALERT="${ALERT}→ CVD重启PID=$CVD_NEW "
fi

# 3. liqmap采集器存活检查
LIQ_PID=$(pgrep -f "liqmap_collector" | head -1)
if [ -z "$LIQ_PID" ]; then
    ALERT="${ALERT}⚠️ liqmap未运行 "
    nohup python3 brahma_brain/liqmap_collector.py >> logs/liqmap.log 2>&1 &
    sleep 2
    LIQ_NEW=$(pgrep -f "liqmap_collector" | head -1)
    ALERT="${ALERT}→ liqmap重启PID=$LIQ_NEW "
fi

# 4. 缓存freshness检查
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

# 5. regime_state.json健康检查
python3 -c "import json; json.load(open('data/regime_state.json'))" 2>/dev/null
if [ $? -ne 0 ]; then
    ALERT="${ALERT}⚠️ regime_state.json损坏 "
fi

# 输出+告警
if [ -n "$ALERT" ]; then
    echo "$TS $ALERT" >> logs/watchdog_v2.log
    # 推送Jarvis
    openclaw message send \
        -t "73295708:thread:01a07628-0405-7e85-a34b-e68cd029dfc6" \
        --channel jarvis \
        --message "🐕看门狗: $ALERT" \
        >/dev/null 2>&1 || true
else
    # 静默心跳：每10min打一次
    MIN=$(date -u '+%M')
    if [ "$((MIN % 10))" -eq 0 ]; then
        echo "$TS ✅ all alive (sp=$SP_PID cvd=$CVD_PID liq=$LIQ_PID)" >> logs/watchdog_v2.log
    fi
fi
