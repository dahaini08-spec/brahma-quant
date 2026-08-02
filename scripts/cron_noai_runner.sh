#!/bin/bash
# cron_noai_runner.sh — 零AI cron执行器 v2.0
# 设计院 2026-06-09 | v1.1: 新增 clean-stale 2026-06-17
# v2.0: 2026-08-02 — A类12个cron全部迁移，不走agentTurn，大幅降载
# 职责：执行交易系统任务，只在异常时通过CLI发Jarvis告警，正常时完全静默
# 用法：bash cron_noai_runner.sh <task_name>

TASK="$1"
BASE="/root/.openclaw/workspace/trading-system"
LOG="$BASE/logs/noai_runner.log"
JARVIS_TARGET="73295708:thread:019fb612-d570-7f0b-89c5-2065284157e0"

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
    OUT=$(cd "$BASE" && timeout 60 python3 scripts/market_screener.py --silent 2>&1 | tail -3)
    log "$OUT"
    if echo "$OUT" | grep -qiE 'traceback|exception|error.*failed'; then
        send_alert "🚨 [market-screener失败] $(echo "$OUT" | tail -2)"
    fi
    ;;

  venv-health-guard)
    # 检查venv依赖 + 补建libgomp软链，正常静默
    OUT=$(cd "$BASE" && timeout 20 python3 -c "
import sys, os, subprocess
sys.path.insert(0,'scripts')

# 补建libgomp软链（每次重启后可能丢失）
gomp_dst = '/usr/local/lib/libgomp.so.1'
gomp_src = 'venv/lib/python3.11/site-packages/torch/lib/libgomp.so.1'
if not os.path.exists(gomp_dst) and os.path.exists(gomp_src):
    try:
        os.symlink(os.path.abspath(gomp_src), gomp_dst)
        os.system('ldconfig 2>/dev/null')
        print('FIXED: libgomp软链已补建')
    except Exception as e:
        print(f'WARN: libgomp软链失败: {e}')

# 检查核心依赖
r = subprocess.run(['venv/bin/python3','-c','import numpy,pandas,lightgbm,requests,chromadb;print(\"OK\")'],
    capture_output=True, text=True, timeout=10)
if 'OK' in r.stdout:
    print('HEARTBEAT_OK')
else:
    print('FAIL: ' + r.stderr[:100])
" 2>&1 | tail -3)
    log "$OUT"
    if echo "$OUT" | grep -q 'FIXED:'; then
        send_alert "🔧 [venv-health] libgomp软链已自动补建（重启后恢复）"
    fi
    if echo "$OUT" | grep -q 'FAIL:'; then
        send_alert "🚨 [venv-health失败] $(echo "$OUT" | tail -2)"
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
    # 脚本自带push，正常静默
    PUMP_SCRIPT="$BASE/scripts/pump_signal_executor.py"
    SCAN_SCRIPT="$BASE/scripts/scan_and_alert.py"
    if [ -f "$PUMP_SCRIPT" ]; then
        OUT=$(cd "$BASE" && timeout 60 python3 scripts/pump_signal_executor.py 2>&1 | tail -5)
    elif [ -f "$SCAN_SCRIPT" ]; then
        OUT=$(cd "$BASE" && timeout 60 python3 scripts/scan_and_alert.py 2>&1 | tail -5)
    else
        log "pump-hunter: 脚本不存在，跳过"
        exit 0
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

  *)
    log "未知任务: $TASK"
    exit 1
    ;;
esac

exit 0