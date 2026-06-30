#!/bin/bash
# 梵天 全系统高强度训练 — 系统cron调度，免疫Gateway重启
# 训练顺序：M02 → M03 → v4 → v5 → M08 → CI → 协调器
set -e
BASE=/root/.openclaw/workspace/trading-system
LOG=/tmp/grand_training.log
DONE_FLAG=/tmp/grand_training_done
RUNNING_FLAG=/tmp/grand_training_running

# 防重复运行
if [ -f "$RUNNING_FLAG" ]; then
    PID=$(cat $RUNNING_FLAG 2>/dev/null)
    if kill -0 $PID 2>/dev/null; then
        echo "[$(date -u +%H:%M:%S)] 已在运行中 PID=$PID，跳过" >> $LOG
        exit 0
    fi
fi
echo $$ > $RUNNING_FLAG

echo "" >> $LOG
echo "══════════════════════════════════════════════════" >> $LOG
echo "[$(date -u)] 梵天全系统高强度训练 开始" >> $LOG
echo "══════════════════════════════════════════════════" >> $LOG

T0=$(date +%s)

run_step() {
    local name=$1; shift
    local t0=$(date +%s)
    echo "[$(date -u +%H:%M:%S)] ▶ $name 开始..." >> $LOG
    if python3 "$@" >> $LOG 2>&1; then
        local t1=$(date +%s)
        echo "[$(date -u +%H:%M:%S)] ✅ $name 完成 ($((t1-t0))s)" >> $LOG
    else
        echo "[$(date -u +%H:%M:%S)] ❌ $name 失败 (rc=$?)" >> $LOG
    fi
}

cd $BASE

# Phase-2: 统计认证
run_step "M02-Bootstrap置信区间" dharma/train_m02_bootstrap.py
run_step "M03-体制矩阵"           dharma/train_m03_regime.py

# Phase-3: 深度优化
run_step "v4-N13~N18"             dharma/train_10k_v4.py
run_step "v5-N19/N22"             dharma/train_10k_v5.py

# Phase-4: 认证落地
run_step "M08-冠军认证"            dharma/train_100k.py --node M08
run_step "达摩院CI"               dharma/dharma_ci.py
run_step "协调器-全量"             brahma_coordinator.py --full

T1=$(date +%s)
echo "" >> $LOG
echo "══════════════════════════════════════════════════" >> $LOG
echo "[$(date -u)] 训练完成！总耗时: $((T1-T0))s" >> $LOG
echo "══════════════════════════════════════════════════" >> $LOG

# 写完成标记
echo "DONE:$(date -u +%Y%m%d_%H%M%S):$((T1-T0))s" > $DONE_FLAG
rm -f $RUNNING_FLAG
