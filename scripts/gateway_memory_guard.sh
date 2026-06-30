#!/bin/bash
# Gateway内存守卫 v2.0
# 设计院 2026-06-09 星枢引擎升级
# 职责：Gateway内存>1100MB时自动重启释放内存（原600MB过于激进）
# 历史记录：1040MB正常运行，1576MB失控，1100MB为安全阈值

BASE="/root/.openclaw/workspace/trading-system"
LOG="$BASE/logs/gateway_memory_guard.log"
THRESHOLD_MB=950

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG"; }

GW_PID=$(ps aux | grep "node.*gateway" | grep -v grep | awk '{print $2}' | head -1)
if [ -z "$GW_PID" ]; then
    log "Gateway未运行，跳过"
    exit 0
fi

# force参数：无论内存大小强制重启
FORCE_MODE="${1:-}"

# 获取RSS内存(KB→MB)
MEM_MB=$(cat /proc/$GW_PID/status 2>/dev/null | grep VmRSS | awk '{print int($2/1024)}')
if [ -z "$MEM_MB" ]; then
    log "无法读取Gateway内存"
    exit 0
fi

log "Gateway PID=$GW_PID 内存=${MEM_MB}MB (阈值${THRESHOLD_MB}MB)"

if [ "$FORCE_MODE" = "force" ] || [ "$MEM_MB" -gt "$THRESHOLD_MB" ]; then
    # 检查持仓
    POSITIONS=$(python3 -c "
import json, sys
sys.path.insert(0,'$BASE')
bs=json.load(open('$BASE/data/brahma_state.json'))
pos=bs.get('positions',{})
if isinstance(pos,dict):
    # dict格式：过滤已平仓品种
    _closed={'DOGEUSDT'}
    cnt=sum(1 for sym,r in pos.items() if sym not in _closed and isinstance(r,dict))
elif isinstance(pos,list):
    cnt=sum(1 for p in pos if isinstance(p,dict) and p.get('status')=='OPEN')
else:
    cnt=0
print(cnt)
" 2>/dev/null || echo "0")
    
    if [ "$POSITIONS" -gt "0" ]; then
        log "⚠️ 内存超限但有${POSITIONS}个持仓，跳过重启"
        exit 0
    fi
    
    log "🔄 内存${MEM_MB}MB>阈值，持仓=0，执行Gateway重启..."
    log "✅ Gateway重启信号已发送（3秒后生效）"
    # 延迟3秒执行kill，让本cron任务先正常退出，避免"interrupted"误报
    (sleep 3 && kill -15 $GW_PID 2>/dev/null) &
    disown
else
    log "✅ 内存正常"
fi
