#!/bin/bash
SCRON=/root/.openclaw/workspace/trading-system/supercronic
CRONTAB=/root/.openclaw/workspace/trading-system/brahma_crontab.txt
LOG=/root/.openclaw/workspace/trading-system/logs/supercronic.log
PIDFILE=/tmp/brahma_supercronic.pid

mkdir -p "$(dirname $LOG)"

# ===== 依赖恢复（/usr/local/lib 重启后被清空）=====
# libgomp: lightgbm 运行时依赖，从 torch 借用
# 持久化软链接在 workspace 目录（重启后不丢失）
WORKSPACE_GOMP=/root/.openclaw/workspace/trading-system/libgomp.so.1
TORCH_GOMP=/root/.openclaw/workspace/trading-system/venv/lib/python3.11/site-packages/torch/lib/libgomp.so.1
if [ ! -f /usr/local/lib/libgomp.so.1 ]; then
    if [ -L "$WORKSPACE_GOMP" ]; then
        cp -P "$WORKSPACE_GOMP" /usr/local/lib/libgomp.so.1 2>/dev/null || ln -sf "$TORCH_GOMP" /usr/local/lib/libgomp.so.1
    elif [ -f "$TORCH_GOMP" ]; then
        ln -sf "$TORCH_GOMP" /usr/local/lib/libgomp.so.1
    fi
    ldconfig 2>/dev/null
    echo "[startup] libgomp.so.1 restored"
fi

# lightgbm: 从 venv 复制到系统 dist-packages
if ! python3 -c 'import lightgbm' 2>/dev/null; then
    VENV_LGBM=/root/.openclaw/workspace/trading-system/venv/lib/python3.11/site-packages/lightgbm
    SYS_PKGS=/usr/local/lib/python3.11/dist-packages
    cp -r "$VENV_LGBM" "$SYS_PKGS/" 2>/dev/null
    timeout 20 pip install narwhals --break-system-packages -q 2>/dev/null
    echo "[startup] lightgbm restored"
fi
# jesse + jesse_rust: 已预装（系统python dist-packages）
# [9.25 苏摩111封印] 禁止启动时pip install：08:22事故根因——兜底pip install jesse全依赖
# (ray/optuna/sklearn/matplotlib/eth全家桶)吃满CPU+IO 11min，cron全阻塞、进程族全灭
# 新策略：缺失时只告警推送，安装由人工低峰执行
if ! venv/bin/python3 -c 'from jesse.indicators import rsi' 2>/dev/null; then
    echo "[startup][ALERT] jesse MISSING - auto-install DISABLED (0925 incident), alerting"
    python3 - <<'PYALERT' 2>/dev/null || true
import sys
sys.path.insert(0, 'brahma_brain')
sys.path.insert(0, 'scripts')
try:
    from push_hub import _jarvis
    from system_config import JARVIS_USER_ID, JARVIS_THREAD_ID
    _jarvis(f'{JARVIS_USER_ID}:thread:{JARVIS_THREAD_ID}',
            '⚠️ 启动检查：jesse缺失，自动安装已禁用(9.25事故根因)。94维引擎路径将降级，请低峰期人工安装 jesse jesse_rust')
except Exception:
    pass
PYALERT
else
    echo "[startup] jesse already available in venv"
fi
# =====================================================

if [ -f "$PIDFILE" ] && kill -0 "$(cat $PIDFILE)" 2>/dev/null; then
    echo "supercronic already running (pid=$(cat $PIDFILE))"
    exit 0
fi

nohup "$SCRON" "$CRONTAB" >> "$LOG" 2>&1 &
echo $! > "$PIDFILE"
echo "supercronic started pid=$(cat $PIDFILE)"
export PYTHONDONTWRITEBYTECODE=1
