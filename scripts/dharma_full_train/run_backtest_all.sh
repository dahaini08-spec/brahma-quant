#!/bin/bash
# 达摩院 · 全周期回测调度器
# 资源安全策略：串行执行，每个任务完成后再下一个，避免内存撑爆
# 日志：data/dharma_backtest/scheduler.log

BASE="/root/.openclaw/workspace/trading-system"
OUT="$BASE/data/dharma_backtest"
LOG="$OUT/scheduler.log"
mkdir -p "$OUT"

log() { echo "[$(date -u +%H:%M:%S)] $1" | tee -a "$LOG"; }

log "====== 达摩院全周期回测启动 ======"
log "资源: 2核CPU / ~1.4GB可用RAM / 串行执行"
log "计划: BTC+ETH × 1h+4h+15m × train+OOS = 12个任务"

TOTAL=0; DONE=0
TASKS=(
  "BTCUSDT 4h both"
  "ETHUSDT 4h both"
  "BTCUSDT 1h both"
  "ETHUSDT 1h both"
  "BTCUSDT 15m both"
  "ETHUSDT 15m both"
)

for task in "${TASKS[@]}"; do
  read -r sym itv mode <<< "$task"
  DONE=$((DONE+1))
  log "[$DONE/6] 开始: $sym $itv"

  # 检查是否已完成（断点续传）
  DONE_FLAG="$OUT/${sym}_${itv}_both_done.flag"
  if [ -f "$DONE_FLAG" ]; then
    log "  [跳过] 已完成标记存在"
    continue
  fi

  START_T=$(date +%s)
  python3 "$BASE/scripts/dharma_full_train/backtest_engine.py" \
    --symbol "$sym" --interval "$itv" --mode both \
    >> "$LOG" 2>&1

  if [ $? -eq 0 ]; then
    touch "$DONE_FLAG"
    END_T=$(date +%s)
    log "  ✅ 完成 ${sym} ${itv}  耗时=$((END_T-START_T))秒"
  else
    log "  ❌ 失败 ${sym} ${itv}"
  fi

  # 内存释放间隔（串行保护）
  sleep 2
done

log "====== 全部完成 ======"

# 汇总所有结果
python3 -c "
import json, os, glob
OUT='$OUT'
all_results = []
for fp in sorted(glob.glob(f'{OUT}/*_summary.json')):
    try:
        data = json.load(open(fp))
        all_results.extend(data if isinstance(data, list) else [data])
    except: pass

# 按ROI排序
all_results.sort(key=lambda x: x.get('roi_pct', 0), reverse=True)

print()
print('='*70)
print('达摩院 全周期回测汇总报告')
print('='*70)
print(f'{\"标的\":<12}{\"周期\":<6}{\"模式\":<8}{\"交易\":<7}{\"胜率\":<8}{\"PF\":<7}{\"ROI\":<10}{\"最大回撤\":<10}')
print('-'*70)
for r in all_results:
    mode_cn = \"训练\" if r.get(\"mode\")==\"train\" else \"OOS\"
    print(f'{r.get(\"symbol\",\"?\"):<12}{r.get(\"interval\",\"?\"):<6}{mode_cn:<8}{r.get(\"trades\",0):<7}{str(r.get(\"wr_pct\",0))+\"%\":<8}{r.get(\"pf\",0):<7}{str(r.get(\"roi_pct\",0))+\"%\":<10}{str(r.get(\"max_dd_pct\",0))+\"%\":<10}')

with open(f'{OUT}/full_report.json','w') as f:
    json.dump(all_results, f, indent=2, ensure_ascii=False)
print(f'报告写入: {OUT}/full_report.json')
" 2>/dev/null | tee -a "$LOG"
