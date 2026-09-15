#!/bin/bash
# daemon_watchdog.sh — 永生看门狗 v2.0 [2026-09-13 苏摩111封印]
# 替代independent_watchdog.sh，使用setsid彻底脱离session
# 三个改进：
# 1. setsid创建新session → 不受parent shell退出影响
# 2. 双看门狗互看 → watchdog_a和watchdog_b互相监控
# 3. 缓存freshness检查 → 检测过期缓存并推送Jarvis告警
#
# 启动方式：setsid bash scripts/daemon_watchdog.sh >> logs/watchdog_v2.log 2>&1 &
# 或者：bash scripts/start_daemons.sh（一键启动全部）

cd /root/.openclaw/workspace/trading-system
WATCHDOG_ROLE="${1:-A}"  # A或B
WATCHDOG_PEER_PIDFILE="/tmp/brahma_watchdog_${WATCHDOG_ROLE}.pid"
PEER_ROLE="B"
if [ "$WATCHDOG_ROLE" = "B" ]; then PEER_ROLE="A"; fi
PEER_PIDFILE="/tmp/brahma_watchdog_${PEER_ROLE}.pid"

echo "$$ > $WATCHDOG_PEER_PIDFILE"

# 缓存文件TTL定义（秒）
TTL_BRAHMA_STATE=14400   # 4h
TTL_GEX=86400            # 24h
TTL_VOL_BETA=43200       # 12h
TTL_MACRO=86400          # 24h
TTL_CVD=3600             # 1h
TTL_LIQ=14400            # 4h
TTL_CB=3600              # 1h
TTL_DD=3600              # 1h
TTL_AF=86400             # 24h
TTL_REGIME=3600          # 1h

# Jarvis推送函数
_push_alert() {
    local msg="$1"
    openclaw message send \
        -t "73295708:thread:01a07628-0405-7e85-a34b-e68cd029dfc6" \
        --channel jarvis \
        --message "🐕看门狗${WATCHDOG_ROLE}: $msg" \
        >/dev/null 2>&1 || true
}

# 缓存freshness检查
_check_cache_freshness() {
    local ALERT=""
    local NOW=$(date +%s)

    check_file() {
        local f="data/$1"
        local ttl="$2"
        local name="$3"
        if [ -f "$f" ]; then
            local mtime=$(stat -c %Y "$f" 2>/dev/null || echo 0)
            local age=$((NOW - mtime))
            if [ $age -gt $ttl ]; then
                local age_h=$((age / 3600))
                ALERT="${ALERT}⚠️ ${name}过期${age_h}h\n"
            fi
        else
            ALERT="${ALERT}⚠️ ${name}不存在\n"
        fi
    }

    check_file "brahma_state_btc.json" $TTL_BRAHMA_STATE "brahma_state_btc"
    check_file "brahma_state_eth.json" $TTL_BRAHMA_STATE "brahma_state_eth"
    check_file "gex_state.json" $TTL_GEX "GEX"
    check_file "vol_beta_state.json" $TTL_VOL_BETA "vol_beta"
    check_file "macro_state.json" $TTL_MACRO "宏观"
    check_file "cvd_realtime_btcusdt.json" $TTL_CVD "CVD_BTC"
    check_file "cvd_realtime_ethusdt.json" $TTL_CVD "CVD_ETH"
    check_file "liq_heatmap_btcusdt.json" $TTL_LIQ "清算热图BTC"
    check_file "liq_heatmap_ethusdt.json" $TTL_LIQ "清算热图ETH"
    check_file "circuit_breaker.json" $TTL_CB "风控熔断"
    check_file "drawdown_state.json" $TTL_DD "回撤"
    check_file "antifragile_state.json" $TTL_AF "反脆弱"
    check_file "regime_state.json" $TTL_REGIME "实时体制"

    echo "$ALERT"
}

# 进程重启函数
_restart_supercronic() {
    bash start_supercronic.sh >> logs/syscron.log 2>&1
    sleep 3
    pgrep -f "supercronic.*brahma_crontab" | head -1
}

_restart_cvd() {
    setsid python3 scripts/cvd_ws_collector.py >> logs/cvd_ws.log 2>&1 &
    sleep 2
    pgrep -f "cvd_ws_collector" | head -1
}

_restart_liqmap() {
    setsid python3 brahma_brain/liqmap_collector.py >> logs/liqmap.log 2>&1 &
    sleep 2
    pgrep -f "liqmap_collector" | head -1
}

# 主循环
while true; do
    TS=$(date -u '+%Y-%m-%d %H:%M:%S UTC')
    ALERT=""

    # 1. supercronic存活检查
    SP_PID=$(pgrep -f "supercronic.*brahma_crontab" | head -1)
    if [ -z "$SP_PID" ]; then
        ALERT="${ALERT}⚠️ supercronic未运行\n"
        SP_NEW=$(_restart_supercronic)
        if [ -n "$SP_NEW" ]; then
            ALERT="${ALERT}→ 已重启 PID=$SP_NEW\n"
        else
            ALERT="${ALERT}→ 🔴 重启失败！\n"
        fi
    fi

    # 2. CVD采集器存活检查
    CVD_PID=$(pgrep -f "cvd_ws_collector" | head -1)
    if [ -z "$CVD_PID" ]; then
        ALERT="${ALERT}⚠️ CVD采集器未运行\n"
        CVD_NEW=$(_restart_cvd)
        if [ -n "$CVD_NEW" ]; then
            ALERT="${ALERT}→ CVD已重启 PID=$CVD_NEW\n"
        fi
    fi

    # 3. liqmap采集器存活检查
    LIQ_PID=$(pgrep -f "liqmap_collector" | head -1)
    if [ -z "$LIQ_PID" ]; then
        ALERT="${ALERT}⚠️ liqmap采集器未运行\n"
        LIQ_NEW=$(_restart_liqmap)
        if [ -n "$LIQ_NEW" ]; then
            ALERT="${ALERT}→ liqmap已重启 PID=$LIQ_NEW\n"
        fi
    fi

    # 4. 对端看门狗存活检查（互看机制）
    PEER_PID=""
    if [ -f "$PEER_PIDFILE" ]; then
        PEER_PID=$(cat "$PEER_PIDFILE" 2>/dev/null)
        if [ -n "$PEER_PID" ] && ! kill -0 "$PEER_PID" 2>/dev/null; then
            PEER_PID=""
        fi
    fi
    if [ -z "$PEER_PID" ]; then
        ALERT="${ALERT}⚠️ 看门狗${PEER_ROLE}未运行，重启中\n"
        setsid bash scripts/daemon_watchdog.sh "$PEER_ROLE" >> logs/watchdog_v2.log 2>&1 &
        sleep 2
        PEER_NEW=$(cat "$PEER_PIDFILE" 2>/dev/null)
        if [ -n "$PEER_NEW" ]; then
            ALERT="${ALERT}→ 看门狗${PEER_ROLE}已重启 PID=$PEER_NEW\n"
        fi
    fi

    # 5. 缓存freshness检查（每5分钟一次）
    MIN=$(date -u '+%M')
    SEC=$(date -u '+%S')
    if [ "$((MIN % 5))" -eq 0 ] && [ "$SEC" -lt 60 ]; then
        CACHE_ALERT=$(_check_cache_freshness)
        if [ -n "$CACHE_ALERT" ]; then
            ALERT="${ALERT}${CACHE_ALERT}"
        fi
    fi

    # 6. regime_state.json健康检查
    python3 -c "import json; json.load(open('data/regime_state.json'))" 2>/dev/null
    if [ $? -ne 0 ]; then
        ALERT="${ALERT}⚠️ regime_state.json损坏\n"
    fi

    # 输出+告警
    if [ -n "$ALERT" ]; then
        echo -e "$TS [watchdog-$WATCHDOG_ROLE]" >> logs/watchdog_v2.log
        echo -e "$ALERT" >> logs/watchdog_v2.log
        # 推送Jarvis（合并一条消息）
        MSG=$(echo -e "$ALERT" | grep -v "^$" | tr '\n' ' ' | sed 's/  */ /g')
        _push_alert "$MSG"
    else
        # 静默心跳：每10min打一次
        if [ "$((MIN % 10))" -eq 0 ] && [ "$SEC" -lt 60 ]; then
            echo "$TS [watchdog-$WATCHDOG_ROLE] ✅ all alive (sp=$SP_PID cvd=$CVD_PID liq=$LIQ_PID peer=$PEER_PID)" >> logs/watchdog_v2.log
        fi
    fi

    sleep 60
done
