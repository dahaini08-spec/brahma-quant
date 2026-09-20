#!/bin/bash
# auto_start.sh — 梵天系统冷启动自动化 [2026-09-20 苏摩111]
# 解决：每次容器重启后需手动启动4进程+依赖
# 方案：由OpenClaw cron在容器启动后自动调用此脚本

cd /root/.openclaw/workspace/trading-system

echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] 梵天冷启动自动化开始"

# 1. libgomp恢复
WORKSPACE_GOMP=/root/.openclaw/workspace/trading-system/libgomp.so.1
TORCH_GOMP=/root/.openclaw/workspace/trading-system/venv/lib/python3.11/site-packages/torch/lib/libgomp.so.1
if [ ! -f /usr/local/lib/libgomp.so.1 ]; then
    if [ -L "$WORKSPACE_GOMP" ]; then
        cp -P "$WORKSPACE_GOMP" /usr/local/lib/libgomp.so.1 2>/dev/null
    elif [ -f "$TORCH_GOMP" ]; then
        ln -sf "$TORCH_GOMP" /usr/local/lib/libgomp.so.1
    fi
    ldconfig 2>/dev/null
    echo "[startup] libgomp.so.1 restored"
fi

# 2. lightgbm恢复
if ! python3 -c 'import lightgbm' 2>/dev/null; then
    VENV_LGBM=/root/.openclaw/workspace/trading-system/venv/lib/python3.11/site-packages/lightgbm
    SYS_PKGS=/usr/local/lib/python3.11/dist-packages
    cp -r "$VENV_LGBM" "$SYS_PKGS/" 2>/dev/null
    pip install narwhals --break-system-packages -q 2>/dev/null
    echo "[startup] lightgbm restored"
fi

# 3. pytest恢复（冒烟测试依赖）
pip install pytest --break-system-packages -q 2>/dev/null

# 4. 启动4进程（带重复防护）
pgrep -f 'supercronic.*brahma_crontab' > /dev/null 2>&1 || { bash start_supercronic.sh >> logs/syscron.log 2>&1; sleep 2; }
pgrep -f 'cvd_ws_collector' > /dev/null 2>&1 || nohup python3 scripts/cvd_ws_collector.py >> logs/cvd.log 2>&1 &
pgrep -f 'liqmap_collector' > /dev/null 2>&1 || nohup python3 brahma_brain/liqmap_collector.py >> logs/liqmap.log 2>&1 &
pgrep -f 'independent_watchdog' > /dev/null 2>&1 || nohup bash scripts/independent_watchdog.sh >> logs/watchdog.log 2>&1 &

# 5. 验证
sleep 3
SP=$(pgrep -f "supercronic.*brahma_crontab" | head -1)
CVD=$(pgrep -f "cvd_ws_collector" | head -1)
LIQ=$(pgrep -f "liqmap_collector" | head -1)
WD=$(pgrep -f "independent_watchdog" | head -1)

echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] 启动完成: supercronic=$SP cvd=$CVD liqmap=$LIQ watchdog=$WD"

# 6. 冒烟测试
python3 -c "
import sys, os, importlib.util
sys.path.insert(0, '.'); sys.path.insert(0, 'brahma_brain')
spec = importlib.util.spec_from_file_location('smoke', 'tests/test_brahma_v4_smoke.py')
mod = importlib.util.module_from_spec(spec)
import pytest
def safe_skip(msg=None): raise SystemExit('SKIP')
pytest.skip = safe_skip
spec.loader.exec_module(mod)
p=f=s=0
for name in sorted(dir(mod)):
    if name.startswith('test_'):
        try: getattr(mod,name)(); p+=1
        except SystemExit: s+=1
        except: f+=1
print(f'冒烟测试: {p} passed / {f} failed / {s} skipped')
" 2>&1

echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] 冷启动自动化完成"
