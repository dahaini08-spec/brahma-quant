#!/bin/bash
# cron_noai_runner.sh — 零AI cron执行器 v2.1
# 设计院 2026-06-09 | v1.1: 新增 clean-stale 2026-06-17
# v2.0: 2026-08-02 — A类12个cron全部迁移，不走agentTurn，大幅降载
# v2.1: 2026-08-07 — core dump永久封印 + 防御层
# 职责：执行交易系统任务，只在异常时通过CLI发Jarvis告警，正常时完全静默
# 用法：bash cron_noai_runner.sh <task_name>

# ── 防御层：禁止产生core dump ─────────────────────────────────
ulimit -c 0
export PYTHONFAULTHANDLER=0          # 禁用Python fault handler（防止core dump）
export PYTHONDONTWRITEBYTECODE=1     # 不写.pyc（减少IO）

TASK="$1"
BASE="/root/.openclaw/workspace/trading-system"
LOG="$BASE/logs/noai_runner.log"
JARVIS_TARGET="73295708:thread:019fd9dd-4b0f-71db-87fb-1e192ccb2291"  # 2026-08-07 苏摩111更新 SSOT

mkdir -p "$BASE/logs"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] [$TASK] $1" >> "$LOG"; }

send_alert() {
    local msg="$1"
    openclaw message send \
        --channel jarvis \
        --to "$JARVIS_TARGET" \
        --message "$msg" 2>/dev/null || true
}

case "$TASK" in

  # ── 原有任务（保留）──────────────────────────────────────────
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
    OUT=$(cd "$BASE" && timeout 30 python3 scripts/clean_stale_signals.py 2>&1)
    log "$OUT"
    if echo "$OUT" | grep -q '总计清除: 0'; then
        exit 0
    fi
    if echo "$OUT" | grep -qE '总计清除: [1-9]'; then
        send_alert "🧹 [结构失效清理]
$(echo "$OUT" | grep -E '清除|保留|总计|失效' | head -10)"
        exit 0
    fi
    if echo "$OUT" | grep -qiE 'error|exception|traceback'; then
        send_alert "🚨 [clean-stale异常] $(echo "$OUT" | tail -3)"
    fi
    ;;

  # ── v2.0 新增：A类12个任务（脚本自带push，正常静默）────────────

  brahma-state-refresh)
    # 刷新BTC/ETH体制状态到data/brahma_state.json，正常完全静默
    OUT=$(cd "$BASE" && timeout 30 python3 scripts/brahma_state_refresh.py 2>&1 | tail -3)
    log "$OUT"
    if echo "$OUT" | grep -qiE 'traceback|exception|error.*failed'; then
        send_alert "🚨 [brahma-state-refresh失败] $(echo "$OUT" | tail -2)"
    fi
    ;;

  market-screener)
    # 更新scan_candidates.json，正常完全静默
    OUT=$(cd "$BASE" && timeout 60 python3 scripts/market_screener.py 2>&1 | tail -3)
    log "$OUT"
    if echo "$OUT" | grep -qiE 'traceback|exception|error.*failed'; then
        send_alert "🚨 [market-screener失败] $(echo "$OUT" | tail -2)"
    fi
    ;;

  venv-health-guard)
    # 检查 venv 依赖 + 补建 libgomp，正常静默
    # 2026-08-08 修复：libgomp 实际在 xgboost.libs，需设置 LD_LIBRARY_PATH
    OUT=$(cd "$BASE" && timeout 20 python3 scripts/venv_health_check.py 2>&1 | tail -3)
    log "$OUT"
    if echo "$OUT" | grep -q 'FIXED:'; then
        send_alert "🔧 [venv-health] libgomp 已自动补建（重启后恢复）"
    fi
    if echo "$OUT" | grep -q 'FAIL:'; then
        send_alert "🚨 [venv-health 失败] $(echo "$OUT" | tail -2)"
    fi
    ;;

  signal-settler)
    # 结算信号WR，脚本自带push_hub推送（有结算才推），正常静默
    OUT=$(cd "$BASE" && timeout 60 python3 scripts/signal_settler.py --push 2>&1 | tail -5)
    log "$OUT"
    if echo "$OUT" | grep -qiE 'traceback|exception|推送失败'; then
        send_alert "🚨 [signal-settler失败] $(echo "$OUT" | tail -2)"
    fi
    ;;

  bbw-squeeze-monitor)
    # BBW压缩预警，脚本自带push_hub（有压缩才推），正常静默
    OUT=$(cd "$BASE" && timeout 20 python3 scripts/bbw_squeeze_monitor.py 2>&1)
    log "$OUT"
    if echo "$OUT" | grep -qiE 'traceback|exception'; then
        send_alert "🚨 [bbw-monitor失败] $(echo "$OUT" | tail -2)"
    fi
    ;;

  brahma-online-calibrate)
    # 更新signal_weights.json，纯文件操作，正常静默
    OUT=$(cd "$BASE" && timeout 30 python3 -c "
import sys, json
sys.path.insert(0,'.')
from pathlib import Path
ic_path = Path('data/ic_tracker_state.json')
sw_path = Path('data/signal_weights.json')
if not ic_path.exists():
    print('HEARTBEAT_OK: ic_tracker_state.json不存在')
    sys.exit(0)
ic = json.loads(ic_path.read_text())
ev_buckets = ic.get('ev_by_bucket', {})
sw = json.loads(sw_path.read_text()) if sw_path.exists() else {}
updated = 0
for k, v in ev_buckets.items():
    if v.get('n', 0) >= 8 and abs(v.get('wr', 0.5) - 0.5) > 0.1:
        old = sw.get(k, {}).get('multiplier', 1.0)
        wr = v['wr']
        new_mul = round(max(0.3, min(2.0, 0.5 + (wr - 0.5) * 3)), 2)
        if abs(new_mul - old) > 0.05:
            sw.setdefault(k, {})['multiplier'] = new_mul
            updated += 1
if updated > 0:
    sw_path.write_text(json.dumps(sw, indent=2))
    print(f'CALIBRATED: {updated}个分桶权重已更新')
else:
    print('HEARTBEAT_OK')
" 2>&1 | tail -3)
    log "$OUT"
    if echo "$OUT" | grep -qiE 'traceback|exception'; then
        send_alert "🚨 [online-calibrate失败] $(echo "$OUT" | tail -2)"
    fi
    ;;

  macro-state-refresh)
    # 刷新宏观状态，正常静默
    MACRO_SCRIPT="$BASE/scripts/macro_factor_engine.py"
    if [ -f "$MACRO_SCRIPT" ]; then
        OUT=$(cd "$BASE" && timeout 60 python3 scripts/macro_factor_engine.py 2>&1 | tail -5)
    else
        # fallback: DXY直接拉取写入macro_state.json
        OUT=$(cd "$BASE" && timeout 20 python3 -c "
import urllib.request, json
from pathlib import Path
from datetime import datetime, timezone
try:
    url = 'https://fapi.binance.com/fapi/v1/ticker/price?symbol=BTCUSDT'
    r = json.loads(urllib.request.urlopen(url, timeout=5).read())
    state = {'ts': datetime.now(timezone.utc).isoformat(), 'btc': float(r['price']), 'source': 'fapi'}
    Path('data/macro_state.json').write_text(json.dumps(state))
    print('HEARTBEAT_OK')
except Exception as e:
    print(f'WARN: {e}')
" 2>&1)
    fi
    log "$OUT"
    if echo "$OUT" | grep -qiE 'traceback|exception|error.*failed'; then
        send_alert "🚨 [macro-state-refresh失败] $(echo "$OUT" | tail -2)"
    fi
    ;;

  brahma-auto-heal)
    # 自愈检查，异常才告警
    OUT=$(cd "$BASE" && timeout 60 python3 -W ignore scripts/brahma_auto_heal.py 2>&1 | tail -5)
    log "$OUT"
    if echo "$OUT" | grep -qiE 'traceback|exception|❌.*失败|heal.*fail'; then
        send_alert "🚨 [brahma-auto-heal异常] $(echo "$OUT" | tail -3)"
    elif echo "$OUT" | grep -q '🔧'; then
        # 有修复项，记录日志但不打扰苏摩
        log "auto-heal修复项: $(echo "$OUT" | grep '🔧' | head -3)"
    fi
    ;;

  main-signal-watcher)
    # 脚本自带push_hub推送P0/P1信号，正常静默
    OUT=$(cd "$BASE" && timeout 45 python3 scripts/signal_watcher.py 2>&1 | tail -3)
    log "$OUT"
    if echo "$OUT" | grep -qiE 'traceback|exception'; then
        send_alert "🚨 [signal-watcher异常] $(echo "$OUT" | tail -2)"
    fi
    ;;

  rsi-structure-watcher)
    # 脚本自带事件触发链，正常静默
    OUT=$(cd "$BASE" && timeout 45 python3 scripts/rsi_structure_watcher.py 2>&1 | tail -3)
    log "$OUT"
    if echo "$OUT" | grep -qiE 'traceback|exception'; then
        send_alert "🚨 [rsi-watcher异常] $(echo "$OUT" | tail -2)"
    fi
    ;;

  brahma-nerve-center)
    # 脚本自带push_hub P0/P1推送，正常静默
    OUT=$(cd "$BASE" && timeout 60 python3 scripts/brahma_nerve_center.py 2>&1 | tail -3)
    log "$OUT"
    if echo "$OUT" | grep -qiE 'traceback|exception'; then
        send_alert "🚨 [nerve-center异常] $(echo "$OUT" | tail -2)"
    fi
    ;;

  pump-hunter)
    # [设计院封印 2026-08-02] 修复路径：正确脚本在 dharma/pump_hunter/scan_and_alert.py
    # 旧路径 scripts/pump_signal_executor.py 和 scripts/scan_and_alert.py 均已移除
    PUMP_SCRIPT="$BASE/dharma/pump_hunter/scan_and_alert.py"
    if [ -f "$PUMP_SCRIPT" ]; then
        OUT=$(cd "$BASE" && timeout 150 python3 dharma/pump_hunter/scan_and_alert.py 2>&1 | tail -5)
    else
        log "pump-hunter: 脚本不存在 $PUMP_SCRIPT，跳过"
        send_alert "🚨 [pump-hunter] 脚本缺失: $PUMP_SCRIPT"
        exit 1
    fi
    log "$OUT"
    if echo "$OUT" | grep -qiE 'traceback|exception'; then
        send_alert "🚨 [pump-hunter异常] $(echo "$OUT" | tail -2)"
    fi
    ;;

  position-guardian-unified)
    # auto_position_manager自带_push止损告警，正常静默
    OUT=$(cd "$BASE" && timeout 45 python3 scripts/auto_position_manager.py --check 2>&1 | tail -5)
    log "$OUT"
    if echo "$OUT" | grep -qiE 'traceback|exception'; then
        send_alert "🚨 [position-guardian异常] $(echo "$OUT" | tail -2)"
    fi
    ;;

  oi-advanced-scanner)
    # 脚本自带push_hub OI异动推送，正常静默
    OUT=$(cd "$BASE" && timeout 90 python3 scripts/oi_advanced_scanner.py 2>&1 | tail -5)
    log "$OUT"
    if echo "$OUT" | grep -qiE 'traceback|exception'; then
        send_alert "🚨 [oi-scanner异常] $(echo "$OUT" | tail -2)"
    fi
    ;;

  live-performance-daily)
    # 每日战绩报告，有新交易时推送，无新交易静默
    OUT=$(cd "$BASE" && timeout 30 python3 scripts/performance_logger.py 2>/dev/null)
    log "$OUT"
    if echo "$OUT" | grep -qE 'sync完成: [1-9]|新记录|pnl='; then
        send_alert "📊 [每日战绩] $(echo "$OUT" | tail -5)"
    fi
    if echo "$OUT" | grep -qiE 'traceback|exception'; then
        send_alert "🚨 [performance-logger异常] $(echo "$OUT" | tail -2)"
    fi
    ;;

  pump-outcome-tracker)
    OUT=$(cd "$BASE" && timeout 30 python3 dharma/pump_hunter/outcome_tracker_cron.py 2>&1)
    log "$OUT"
    if echo "$OUT" | grep -qiE 'traceback|exception'; then
        send_alert "🚨 [outcome-tracker异常] $(echo "$OUT" | tail -2)"
    fi
    ;;

  liq-paper-update)
    # 清算集群TP纸面交易引擎：扫描新信号 + 结算到期仓位，脚本自带push，正常静默
    OUT=$(cd "$BASE" && timeout 60 python3 scripts/liq_paper_trader.py 2>&1 | tail -5)
    log "$OUT"
    if echo "$OUT" | grep -qiE 'traceback|exception'; then
        send_alert "🚨 [liq-paper-update异常] $(echo "$OUT" | tail -2)"
    fi
    ;;

  pump-gainer-monitor)
    # 合约涨幅榜监控 — 新入榜妖币推送 [设计院封印 2026-08-07]
    OUT=$(cd "$BASE" && timeout 60 python3 scripts/pump_gainer_monitor.py 2>&1 | tail -3)
    log "$OUT"
    if echo "$OUT" | grep -qiE 'traceback|exception'; then
        send_alert "🚨 [gainer-monitor异常] $(echo "$OUT" | tail -2)"
    fi
    ;;

  system-guardian)
    # [v2.1 合并] ws-guardian + liq-ws-guardian + brahma-mem-watchdog → 1个任务
    BASE_SG="/root/.openclaw/workspace/trading-system"
    # 内存检查
    MEM_PCT=$(python3 -c "import psutil; m=psutil.virtual_memory(); print(int(m.percent))" 2>/dev/null || echo "0")
    if [ "$MEM_PCT" -gt 85 ] 2>/dev/null; then
      send_alert "🚨 [system-guardian] 内存告警: ${MEM_PCT}% > 85%"
    fi
    # Binance API连通性
    API_OK=$(python3 -c "
import urllib.request
try:
    urllib.request.urlopen('https://fapi.binance.com/fapi/v1/ping',timeout=5)
    print('OK')
except Exception as e:
    print(f'FAIL:{e}')
" 2>/dev/null)
    if echo "$API_OK" | grep -q "FAIL"; then
      send_alert "🚨 [system-guardian] Binance API不可达: $API_OK"
    fi
    # core dump防御性清理
    CORE_CNT=$(find "$BASE_SG" -maxdepth 1 -name "core.*" 2>/dev/null | wc -l)
    if [ "$CORE_CNT" -gt 0 ]; then
      find "$BASE_SG" -maxdepth 1 -name "core.*" -delete
      send_alert "🧹 [system-guardian] 清理${CORE_CNT}个core dump文件"
    fi
    log "system-guardian: mem=${MEM_PCT}% api=${API_OK} core_cleaned=${CORE_CNT}"
    ;;

  futures-data-keep)
    OUT=$(python3 -c "
import urllib.request, json, time
from pathlib import Path
try:
    url = 'https://fapi.binance.com/fapi/v1/ticker/price'
    data = json.loads(urllib.request.urlopen(url, timeout=8).read())
    btc = next((x['price'] for x in data if x['symbol']=='BTCUSDT'), '?')
    eth = next((x['price'] for x in data if x['symbol']=='ETHUSDT'), '?')
    Path('/root/.openclaw/workspace/trading-system/data/futures_price_cache.json').write_text(
        json.dumps({'btc':btc,'eth':eth,'ts':time.time()}))
    print('HEARTBEAT_OK')
except Exception as e:
    print(f'FAIL:{e}')
" 2>&1)
    log "$OUT"
    if echo "$OUT" | grep -q "FAIL:"; then
      send_alert "🚨 [futures-data-keep失败] $OUT"
    fi
    ;;

  disk-buffer-check)
    FREE=$(python3 -c "import shutil; s=shutil.disk_usage('/root'); print(int(s.free/1024**3))" 2>/dev/null || echo "0")
    if [ "$FREE" -lt 5 ] 2>/dev/null; then
      send_alert "🚨 [disk-buffer] /root磁盘不足: ${FREE}GB"
    fi
    log "disk-buffer: free=${FREE}GB"
    ;;

  log-rotation)
    # ── 日志轮转清理（设计院 2026-08-09）──────────────────────────────
    BASE_DIR="/root/.openclaw/workspace/trading-system"
    LOG_DIR="$BASE_DIR/logs"
    DATA_DIR="$BASE_DIR/data"
    CLEANED=0

    # 1. logs/ 目录：.log 文件超过 7 天删除；保留最近 7 天
    while IFS= read -r f; do
      rm -f "$f" && log "[log-rotation] 删除旧log: $f" && CLEANED=$((CLEANED+1))
    done < <(find "$LOG_DIR" -name "*.log" -mtime +7 2>/dev/null)

    # 2. logs/ 目录：.jsonl 超过 14 天删除
    while IFS= read -r f; do
      rm -f "$f" && log "[log-rotation] 删除旧jsonl: $f" && CLEANED=$((CLEANED+1))
    done < <(find "$LOG_DIR" -name "*.jsonl" -mtime +14 2>/dev/null)

    # 3. 活跃 .log 文件超过 50MB → 截断保留最后 5000 行
    while IFS= read -r f; do
      SIZE=$(du -k "$f" 2>/dev/null | cut -f1)
      if [ "${SIZE:-0}" -gt 51200 ]; then
        tail -n 5000 "$f" > "${f}.tmp" && mv "${f}.tmp" "$f"
        log "[log-rotation] 截断大文件: $f (${SIZE}KB → 5000行)"
        CLEANED=$((CLEANED+1))
      fi
    done < <(find "$LOG_DIR" -name "*.log" 2>/dev/null)

    # 4. data/ 目录：低价值 .log 超过 3 天删除（auto_position_manager 等运维日志）
    while IFS= read -r f; do
      rm -f "$f" && log "[log-rotation] 删除data旧log: $f" && CLEANED=$((CLEANED+1))
    done < <(find "$DATA_DIR" -maxdepth 1 -name "*.log" -mtime +3 2>/dev/null)

    # 5. data/ 目录：noai_runner.log 超过 20MB → 截断
    RUNNER_LOG="$LOG_DIR/noai_runner.log"
    if [ -f "$RUNNER_LOG" ]; then
      SIZE=$(du -k "$RUNNER_LOG" 2>/dev/null | cut -f1)
      if [ "${SIZE:-0}" -gt 20480 ]; then
        tail -n 3000 "$RUNNER_LOG" > "${RUNNER_LOG}.tmp" && mv "${RUNNER_LOG}.tmp" "$RUNNER_LOG"
        log "[log-rotation] 截断noai_runner.log: ${SIZE}KB → 3000行"
        CLEANED=$((CLEANED+1))
      fi
    fi

    log "[log-rotation] 完成，清理/截断项目: ${CLEANED}个"
    ;;

  *)
    log "未知任务: $TASK"
    exit 1
    ;;
esac

exit 0
