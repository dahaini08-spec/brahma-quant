#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# 梵天达摩院 · 训练启动器 v1.0
# 三重保护：nice+19 / 内存门槛800MB / UTC01:00凌晨执行
# ═══════════════════════════════════════════════════════════════
# 用法：
#   bash scripts/run_training.sh             # 全流程（WFV + 1000U）
#   bash scripts/run_training.sh --wfv-only  # 只跑WFV
#   bash scripts/run_training.sh --bt-only   # 只跑1000U回测
#   bash scripts/run_training.sh --fast      # 快速模式
# ═══════════════════════════════════════════════════════════════

set -euo pipefail

WD=/root/.openclaw/workspace/trading-system
LOG_DIR=$WD/logs
LOG=$LOG_DIR/training_$(date -u +%Y%m%d_%H%M%S).log
LOCK=/tmp/brahma_training.lock

mkdir -p "$LOG_DIR"

# ── 参数解析 ──
MODE="all"       # all / wfv_only / bt_only
FAST_FLAG=""
for arg in "$@"; do
    case "$arg" in
        --wfv-only) MODE="wfv_only" ;;
        --bt-only)  MODE="bt_only"  ;;
        --fast)     FAST_FLAG="--fast" ;;
    esac
done

log() { echo "[run_training $(date -u '+%H:%M:%S')] $*" | tee -a "$LOG"; }

# ════════════════════════════════════════════════════════════════
# 保护层 1：单实例锁（防重复运行）
# ════════════════════════════════════════════════════════════════
if [ -f "$LOCK" ]; then
    OLD_PID=$(cat "$LOCK" 2>/dev/null)
    if kill -0 "$OLD_PID" 2>/dev/null; then
        log "❌ 训练已在运行 (PID=$OLD_PID)，退出"
        exit 1
    else
        log "⚠️ 旧锁文件残留，清除后继续"
        rm -f "$LOCK"
    fi
fi
echo $$ > "$LOCK"
trap 'rm -f "$LOCK"; log "🏁 训练进程退出"' EXIT

# ════════════════════════════════════════════════════════════════
# 保护层 2：内存门槛 800MB
# ════════════════════════════════════════════════════════════════
check_memory() {
    local free_mb
    free_mb=$(free -m | awk '/^Mem:/{print $7}')
    if [ "$free_mb" -lt 800 ]; then
        log "❌ 内存不足：可用 ${free_mb}MB < 800MB，中止训练"
        log "   建议：重启 Gateway 释放内存后重试"
        exit 2
    fi
    log "✅ 内存检查通过：可用 ${free_mb}MB"
}

# ════════════════════════════════════════════════════════════════
# 保护层 3：持仓检查（有持仓时不跑高CPU任务）
# ════════════════════════════════════════════════════════════════
check_positions() {
    local pos
    pos=$(python3 -c "
import json
try:
    state = json.load(open('$WD/data/brahma_state.json'))
    positions = [p for p in state.get('positions', []) if p.get('status') == 'OPEN']
    print(len(positions))
except:
    print(0)
" 2>/dev/null)
    if [ "${pos:-0}" -gt 0 ]; then
        log "⚠️ 有 $pos 个活跃持仓，跳过训练（训练会占用CPU影响ws_guardian）"
        exit 3
    fi
    log "✅ 持仓检查通过：无活跃持仓"
}

# ════════════════════════════════════════════════════════════════
# 运行单个训练任务（nice+19，带超时和内存监控）
# ════════════════════════════════════════════════════════════════
run_task() {
    local label="$1"
    local script="$2"
    shift 2
    local args="$*"

    log "──────────────────────────────────────"
    log "▶ 开始：$label"
    log "  命令：python3 $script $args"

    local t0
    t0=$(date +%s)

    # nice+19 降优先级，避免影响主进程
    if nice -n 19 python3 "$WD/$script" $args >> "$LOG" 2>&1; then
        local elapsed=$(( $(date +%s) - t0 ))
        log "✅ 完成：$label 耗时=${elapsed}s"
        return 0
    else
        local exit_code=$?
        log "❌ 失败：$label exit=$exit_code"
        log "   查看日志：$LOG"
        return $exit_code
    fi
}

# ════════════════════════════════════════════════════════════════
# 主流程
# ════════════════════════════════════════════════════════════════
log "═══════════════════════════════════════════"
log "梵天达摩院 训练启动器 v1.0"
log "模式=$MODE FAST=${FAST_FLAG:-否}"
log "主机=$(hostname) PID=$$"
log "═══════════════════════════════════════════"

cd "$WD"

# 三重保护检查
check_memory
check_positions

# 记录训练开始
log "开始时间: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"

SUCCESS=0
FAILED=0

# ── WFV Walk-Forward 验证 ──
if [ "$MODE" = "all" ] || [ "$MODE" = "wfv_only" ]; then
    log ""
    log "【阶段 1/2】Walk-Forward 验证（6窗口）"

    # 再次检查内存（WFV会加载大量数据）
    FREE_NOW=$(free -m | awk '/^Mem:/{print $7}')
    log "  当前可用内存: ${FREE_NOW}MB"
    if [ "$FREE_NOW" -lt 600 ]; then
        log "  ⚠️ 内存偏低，尝试触发GC..."
        python3 -c "import gc; gc.collect()" 2>/dev/null || true
        sleep 2
    fi

    # [R3-note audit-2026-06-17] 训练版本: train_wfv_v1.py (v7 WFV用dharma/anchored_wfv_v7.py)
if run_task "WFV-BTC+ETH" "dharma/train_wfv_v1.py" $FAST_FLAG; then
        SUCCESS=$((SUCCESS + 1))
    else
        FAILED=$((FAILED + 1))
        log "  WFV失败，继续执行1000U回测..."
    fi

    # 内存回收
    python3 -c "import gc; gc.collect()" 2>/dev/null || true
    sleep 3
fi

# ── 1000U 盈利回测 ──
if [ "$MODE" = "all" ] || [ "$MODE" = "bt_only" ]; then
    log ""
    log "【阶段 2/2】1000U × 5x 8年盈利回测"

    FREE_NOW=$(free -m | awk '/^Mem:/{print $7}')
    log "  当前可用内存: ${FREE_NOW}MB"

    # SHORT + LONG 双向
    if run_task "BT-1000U-BTC-SHORT" "dharma/backtest_1000u.py" --sym BTCUSDT --dir SHORT; then
        SUCCESS=$((SUCCESS + 1))
    else
        FAILED=$((FAILED + 1))
    fi

    sleep 2

    if run_task "BT-1000U-ETH-SHORT" "dharma/backtest_1000u.py" --sym ETHUSDT --dir SHORT; then
        SUCCESS=$((SUCCESS + 1))
    else
        FAILED=$((FAILED + 1))
    fi

    sleep 2

    # LONG方向（体制切换到BULL后激活）
    if run_task "BT-1000U-BTC-LONG" "dharma/backtest_1000u.py" --sym BTCUSDT --dir LONG; then
        SUCCESS=$((SUCCESS + 1))
    else
        FAILED=$((FAILED + 1))
    fi

    sleep 2

    if run_task "BT-1000U-ETH-LONG" "dharma/backtest_1000u.py" --sym ETHUSDT --dir LONG; then
        SUCCESS=$((SUCCESS + 1))
    else
        FAILED=$((FAILED + 1))
    fi
fi

# ════════════════════════════════════════════════════════════════
# 汇总报告
# ════════════════════════════════════════════════════════════════
log ""
log "═══════════════════════════════════════════"
log "训练完成汇总"
log "  成功: $SUCCESS  失败: $FAILED"
log "  日志: $LOG"
log ""

# 列出最新结果文件
log "最新结果文件："
ls -lt "$WD/dharma/results/"train_wfv_v1_*.json 2>/dev/null | head -3 | \
    while read -r line; do log "  $line"; done
ls -lt "$WD/dharma/results/"backtest_1000u_*.json 2>/dev/null | head -3 | \
    while read -r line; do log "  $line"; done

log "═══════════════════════════════════════════"

# 退出码：全部成功=0，部分失败=1
if [ "$FAILED" -gt 0 ]; then
    exit 1
fi
exit 0
