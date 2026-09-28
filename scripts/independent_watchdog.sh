#!/bin/bash
# independent_watchdog.sh — 独立看门狗 v4 [2026-09-27 苏摩111]
# 变更（9.27排查结论）：
#   1. supercronic被宿主定期清扫是平台常态（9.23起每日14-31次，看门狗自愈闭环一直正常）
#      → 单次自愈事件（检测到死+重启成功）降级P4：只写日志，不再推送
#   2. 连续失败计数：连续3轮(3分钟)重启仍失败才推送P1（真故障）
#   3. 修复硬编码线程ID（MEMORY封印：线程ID是会话级易变资产，禁止硬编码）
#      → 改读 alerts/.env JARVIS_TARGET，缺省只写日志不推送
#   4. CVD/liqmap重启同样降级P4（同属自愈事件）
# 运行方式：nohup bash scripts/independent_watchdog.sh >> logs/watchdog.log 2>&1 &
# 每60s检查一次
# [9.23修复 苏摩111] 单实例锁：防止多个watchdog并存；防自杀锁：确保重启时能接管
# 锁模式改为通配：同时匹配相对路径(bash scripts/...)与绝对路径启动的实例
cd /root/.openclaw/workspace/trading-system
STATE_FILE="data/watchdog_indep_state.json"
FAIL_COUNT_FILE="data/watchdog_fail_count.json"

# [9.27 v4] 推送目标从alerts/.env读取（SSOT），不再硬编码
# alerts/.env现有键：JARVIS_THREAD_ID（线程ID易变资产）；用户ID用系统常量73295708
load_target() {
  if [ -f alerts/.env ]; then
    JARVIS_THREAD_ID=""
    source alerts/.env 2>/dev/null
    if [ -n "$JARVIS_THREAD_ID" ]; then
      echo "73295708:thread:$JARVIS_THREAD_ID"
      return
    fi
  fi
  echo ""
}
JARVIS_TARGET="$(load_target)"

push_jarvis() {
  local msg="$1"
  if [ -n "$JARVIS_TARGET" ]; then
    openclaw message send -t "$JARVIS_TARGET" --channel jarvis --message "$msg" >/dev/null 2>&1 &
  fi
}

# === 单实例锁：如果已有watchdog实例运行，本实例退出 ===
MY_PID=$$
EXISTING=$(pgrep -f "bash.*independent_watchdog\.sh" | grep -v "^${MY_PID}$" | head -1)
if [ -n "$EXISTING" ]; then
  # 已有实例，检查它是否真的活着（用 /proc/PID/stat 验证）
  if [ -d "/proc/$EXISTING" ]; then
    exit 0  # 已有活实例，本实例不重复
  fi
fi

while true; do
  TS=$(date -u '+%Y-%m-%d %H:%M:%S UTC')
  ALERT=""        # 真故障（连续失败）才置位
  SELF_HEALED=""  # 单次自愈事件：只记日志不推送

  # [P1 2026-09-28 苏摩111] 死亡快照哨兵: PID变化或消失→瞬间抓系统快照留证
  python3 "$BASE/scripts/death_snapshot_sentinel.py" >> "$BASE/logs/death_sentinel.log" 2>&1 || true

  # 进程检查+自动重启
  # [9.20修复 苏摩111] start_supercronic.sh加timeout防止阻塞看门狗循环
  # [9.27 v4 苏摩111] supercronic清扫是平台常态（每日14-31次），单次自愈只记日志；
  #                   连续3轮失败才推送P1（fail_count持久化，看门狗重启不清零）
  SP_PID=$(pgrep -f "supercronic.*brahma_crontab" | head -1)
  if [ -z "$SP_PID" ]; then
    timeout 30 bash start_supercronic.sh >> logs/syscron.log 2>&1
    sleep 3
    SP_PID=$(pgrep -f "supercronic.*brahma_crontab" | head -1)
    if [ -n "$SP_PID" ]; then
      SELF_HEALED="${SELF_HEALED}supercronic自愈PID=$SP_PID "
      echo '{"sp_fail": 0}' > "$FAIL_COUNT_FILE"
    else
      SP_FAIL=$(python3 -c "import json,os; f='$FAIL_COUNT_FILE'; print(json.load(open(f)).get('sp_fail',0)+1) if os.path.exists(f) else print(1)" 2>/dev/null || echo 1)
      echo "{\"sp_fail\": $SP_FAIL}" > "$FAIL_COUNT_FILE"
      if [ "$SP_FAIL" -ge 3 ]; then
        ALERT="${ALERT}🔴 supercronic连续${SP_FAIL}轮重启失败 "
      fi
    fi
  else
    [ -f "$FAIL_COUNT_FILE" ] && echo '{"sp_fail": 0}' > "$FAIL_COUNT_FILE"
  fi

  CVD_PID=$(pgrep -f "cvd_ws_collector" | head -1)
  if [ -z "$CVD_PID" ]; then
    setsid python3 scripts/cvd_ws_collector.py >> logs/cvd_ws.log 2>&1 < /dev/null &
    sleep 2
    CVD_PID=$(pgrep -f "cvd_ws_collector" | head -1)
    SELF_HEALED="${SELF_HEALED}CVD自愈PID=$CVD_PID "
  fi

  LIQ_PID=$(pgrep -f "liqmap_collector" | head -1)
  if [ -z "$LIQ_PID" ]; then
    setsid python3 brahma_brain/liqmap_collector.py >> logs/liqmap.log 2>&1 < /dev/null &
    sleep 2
    LIQ_PID=$(pgrep -f "liqmap_collector" | head -1)
    SELF_HEALED="${SELF_HEALED}liqmap自愈PID=$LIQ_PID "
  fi

  # === [9.22苏摩111] 数据自愈检查 ===
  # 每5分钟跑一次self_heal_daemon --once，自动修复过期数据
  MIN5=$(date -u '+%-M')
  if [ $((MIN5 % 5)) -eq 0 ]; then
    timeout 60 python3 scripts/self_heal_daemon.py --once >> logs/self_heal.log 2>&1
  fi

  # === [9.27 v4] 告警分级 ===
  # ALERT非空=连续3轮重启失败的真故障 → 推送P1
  # SELF_HEALED非空=单次自愈事件 → 只写日志（平台清扫常态，每日14-31次，推送纯噪声）
  if [ -n "$ALERT" ]; then
    push_jarvis "🐕 独立看门狗: $ALERT"
    echo -e "$TS\n$ALERT" >> logs/watchdog.log
  elif [ -n "$SELF_HEALED" ]; then
    echo "$TS [SELF-HEAL] $SELF_HEALED" >> logs/watchdog.log
    # 每10分钟整点记一次all alive心跳
    MIN=$(date -u '+%-M')
    if [ $((MIN % 10)) -eq 0 ]; then
      echo "$TS ✅ all alive (sp=$SP_PID cvd=$CVD_PID liq=$LIQ_PID)" >> logs/watchdog.log
    fi
  else
    MIN=$(date -u '+%-M')
    if [ $((MIN % 10)) -eq 0 ]; then
      echo "$TS ✅ all alive (sp=$SP_PID cvd=$CVD_PID liq=$LIQ_PID)" >> logs/watchdog.log
    fi
  fi

  sleep 60
done
