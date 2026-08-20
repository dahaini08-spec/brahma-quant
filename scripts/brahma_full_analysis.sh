#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════
# brahma_full_analysis.sh — 梵天全能力分析标准入口
# 苏摩封印 2026-08-20 | 三步流水线：全能力→专家解读→VIP策略
# ══════════════════════════════════════════════════════════
# 用法：
#   bash brahma_full_analysis.sh BTCUSDT ETHUSDT
#   bash brahma_full_analysis.sh BTCUSDT LONG
#
# 核心规则：
#   1. BRAHMA_FORCE_FULL=1 → 绕过内存门控，强制全能力
#   2. 先释放page cache（权限允许时）
#   3. 单品种顺序执行（避免并发OOM）
#   4. 输出写入 /tmp/brahma_output_<sym>.txt 供后续处理

set -e
cd "$(dirname "$0")/.."

SYMBOLS="${@:-BTCUSDT ETHUSDT}"
DIRECTION="${DIRECTION:-LONG}"
OUT_DIR="/tmp/brahma_outputs"
mkdir -p "$OUT_DIR"

echo "════════════════════════════════════════════════"
echo "🏛️ 梵天全能力分析启动 | $(date '+%Y-%m-%d %H:%M CST')"
echo "品种: $SYMBOLS | 方向: $DIRECTION"
echo "内存: $(grep MemAvailable /proc/meminfo | awk '{print int($2/1024)}')MB 可用"
echo "════════════════════════════════════════════════"

# 释放page cache（容器环境可能无权限，忽略失败）
sync 2>/dev/null || true

for SYM in $SYMBOLS; do
    echo ""
    echo "▶ 分析 $SYM ..."
    OUT_FILE="$OUT_DIR/${SYM}_latest.txt"
    
    # BRAHMA_FORCE_FULL=1 强制全能力，单品种独立进程
    BRAHMA_FORCE_FULL=1 \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    python3 scripts/brahma_1hao_analysis.py \
        --symbols "$SYM" \
        --direction "$DIRECTION" \
        2>&1 | tee "$OUT_FILE"
    
    echo "✅ $SYM 完成 → $OUT_FILE"
done

echo ""
echo "════════════════════════════════════════════════"
echo "✅ 全能力分析完成 | 输出目录: $OUT_DIR"
echo "════════════════════════════════════════════════"
