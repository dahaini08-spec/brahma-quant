#!/bin/bash
SCRON=/root/.openclaw/workspace/trading-system/supercronic
CRONTAB=/root/.openclaw/workspace/trading-system/brahma_crontab.txt
LOG=/root/.openclaw/workspace/trading-system/logs/supercronic.log
PIDFILE=/tmp/brahma_supercronic.pid

mkdir -p "$(dirname $LOG)"

# ===== 依赖恢复（/usr/local/lib 重启后被清空）=====
# libgomp: lightgbm 运行时依赖，从 torch 借用
TORCH_GOMP=/root/.openclaw/workspace/trading-system/venv/lib/python3.11/site-packages/torch/lib/libgomp.so.1
if [ ! -f /usr/local/lib/libgomp.so.1 ] && [ -f "$TORCH_GOMP" ]; then
    ln -sf "$TORCH_GOMP" /usr/local/lib/libgomp.so.1
    ldconfig 2>/dev/null
    echo "[startup] libgomp.so.1 restored"
fi

# lightgbm: 从 venv 复制到系统 dist-packages
if ! python3 -c 'import lightgbm' 2>/dev/null; then
    VENV_LGBM=/root/.openclaw/workspace/trading-system/venv/lib/python3.11/site-packages/lightgbm
    SYS_PKGS=/usr/local/lib/python3.11/dist-packages
    cp -r "$VENV_LGBM" "$SYS_PKGS/" 2>/dev/null
    pip install narwhals --break-system-packages -q 2>/dev/null
    echo "[startup] lightgbm restored"
fi
# =====================================================

if [ -f "$PIDFILE" ] && kill -0 "$(cat $PIDFILE)" 2>/dev/null; then
    echo "supercronic already running (pid=$(cat $PIDFILE))"
    exit 0
fi

nohup "$SCRON" "$CRONTAB" >> "$LOG" 2>&1 &
echo $! > "$PIDFILE"
echo "supercronic started pid=$(cat $PIDFILE)"
