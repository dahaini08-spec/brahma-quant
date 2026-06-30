#!/bin/bash
# 梵天关键数据备份 — 每小时执行
WD=/root/.openclaw/workspace/trading-system
BACKUP=$WD/data/backup
mkdir -p $BACKUP

TS=$(date '+%Y%m%d_%H%M')
CRITICAL_FILES=(
    "data/brahma_state.json"
    "data/adaptive_threshold_state.json"
    "data/dharma_runtime.json"
    "data/live_signal_log.jsonl"
    "data/wuqu_paper_settled.jsonl"
    "system_constants.json"
)

for f in "${CRITICAL_FILES[@]}"; do
    src="$WD/$f"
    base=$(basename $f)
    if [ -f "$src" ]; then
        cp "$src" "$BACKUP/${base}.bak"
    fi
done

# 只保留最近3个小时备份(防磁盘爆)
find $BACKUP -name "*.bak.*" -mmin +180 -delete 2>/dev/null

echo "OK backup=$TS"
