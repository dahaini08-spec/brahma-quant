#!/bin/bash
# cron_noai_runner.sh — 零AI cron执行器 v1.1
# 设计院 2026-06-09 | v1.1: 新增 clean-stale 2026-06-17
# 职责：执行交易系统任务，只在异常时通过CLI发Jarvis告警，正常时完全静默
# 用法：bash cron_noai_runner.sh <task_name>

TASK="$1"
BASE="/root/.openclaw/workspace/trading-system"
LOG="$BASE/logs/noai_runner.log"
JARVIS_TARGET="73295708:thread:019f1797-6c60-7541-ad72-ec34ed14dfc4"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] [$TASK] $1" >> "$LOG"; }

send_alert() {
    local msg="$1"
    openclaw message send \
        --channel jarvis \
        --to "$JARVIS_TARGET" \
        --message "$msg" 2>/dev/null || true
}

case "$TASK" in

  commander)
    OUT=$(cd "$BASE" && timeout 60 python3 scripts/brahma_commander.py --cycle 2>&1 | tail -8)
    log "$OUT"
    if echo "$OUT" | grep -qE 'HEARTBEAT_OK|信号0个|批准0个|no signals|DRY_RUN.*0'; then
        exit 0
    fi
    if echo "$OUT" | grep -qE 'DRY_RUN|PAPER|paper_open'; then
        send_alert "📡 [梵天Commander] Paper开仓触发
$(echo "$OUT" | grep -E 'DRY_RUN|PAPER|symbol|direction|score' | head -5)"
        exit 0
    fi
    if echo "$OUT" | grep -qiE 'error|failed|exception|traceback'; then
        send_alert "🚨 [Commander异常] $(echo "$OUT" | tail -3)"
    fi
    ;;

  state-refresh)
    OUT=$(cd "$BASE" && timeout 30 python3 scripts/brahma_state_refresh.py 2>&1 | tail -3)
    log "$OUT"
    if echo "$OUT" | grep -qiE 'error|failed|exception'; then
        send_alert "🚨 [State刷新失败] $(echo "$OUT" | tail -2)"
    fi
    ;;

  position-guardian)
    OUT=$(cd "$BASE" && timeout 30 python3 scripts/position_guardian.py 2>&1 | tail -3)
    log "$OUT"
    if echo "$OUT" | grep -qiE 'error|failed|持仓异常|SL_SETUP_FAILED|mismatch'; then
        send_alert "🚨 [持仓守护告警] $(echo "$OUT" | tail -3)"
    fi
    ;;

  supervisor)
    OUT=$(bash "$BASE/scripts/supervisor_check.sh" 2>&1 | tail -3)
    log "$OUT"
    if echo "$OUT" | grep -qiE 'error|failed|down|异常'; then
        send_alert "🚨 [Supervisor异常] $(echo "$OUT" | tail -2)"
    fi
    ;;

  self-heal)
    OUT=$(cd "$BASE" && timeout 60 python3 scripts/brahma360_guardian.py 2>&1 | tail -5)
    log "$OUT"
    if echo "$OUT" | grep -qiE 'error|failed|exception|heal_failed'; then
        send_alert "🚨 [Self-Heal异常] $(echo "$OUT" | tail -3)"
    fi
    ;;

  memory-guard)
    bash "$BASE/scripts/gateway_memory_guard.sh" 2>&1 | tee -a "$LOG" | grep -v '内存正常' | grep . && true
    ;;

  clean-stale)
    # 结构失效信号清理（dd1_pending + queue_state）
    # 按结构判断：穿越止损 / 大幅偏离入场区 / 体制反转
    OUT=$(cd "$BASE" && timeout 30 python3 scripts/clean_stale_signals.py 2>&1)
    log "$OUT"
    # 无清除 → 静默
    if echo "$OUT" | grep -q '总计清除: 0'; then
        exit 0
    fi
    # 有清除 → 发通知
    if echo "$OUT" | grep -qE '总计清除: [1-9]'; then
        send_alert "🧹 [结构失效清理]
$(echo "$OUT" | grep -E '清除|保留|总计|失效' | head -10)"
        exit 0
    fi
    # 错误
    if echo "$OUT" | grep -qiE 'error|exception|traceback'; then
        send_alert "🚨 [clean-stale异常] $(echo "$OUT" | tail -3)"
    fi
    ;;

  *)
    log "未知任务: $TASK"
    exit 1
    ;;
esac

exit 0
