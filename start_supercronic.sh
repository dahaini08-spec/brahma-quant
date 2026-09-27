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
# [9.27接线5 苏摩111] mcp包存在性哨兵（曾被系统清理清掉一次，缺了提醒不自动装）
# [9.27三方审核升级 苏摩111] 哨兵从纯日志升级为P1推送（mcp第三次丢失才暴露=日志无人看）
if ! python3 -c 'import mcp' 2>/dev/null; then
    echo "[startup][ALERT] mcp MISSING - brahma_mcp_server不可用，自动wheelhouse恢复中"
    # [9.27哨兵去重修复 苏摩111] dedup_key+TTL 4h：同一事故只推1次，不再5分钟刷屏20条
    python3 -c "import sys; sys.path.insert(0,'scripts'); from push_hub import push_jarvis; push_jarvis('🚨mcp包丢失(容器层重置) | 已自动从wheelhouse恢复 | 若1h内复发需人工查', priority='P1', dedup_key='mcp_missing', dedup_ttl=14400)" 2>/dev/null || true
    # [9.27wheelhouse 苏摩111] 自动恢复：workspace持久层不受overlaybd重置影响
    python3 scripts/ensure_deps.py 2>>logs/syscron.log || pip install --break-system-packages -q mcp 2>/dev/null || true
else
    echo "[startup] mcp already available"
fi
# =====================================================

if [ -f "$PIDFILE" ] && kill -0 "$(cat $PIDFILE)" 2>/dev/null; then
    echo "supercronic already running (pid=$(cat $PIDFILE))"
    exit 0
fi

# [梵天2.0 T2 A/B对照盘 2026-09-27 苏摩111] 影子环境固化：BRAHMA_SHADOW=1传给全部cron子进程
# 接入位置: scripts/t2_ab_launch.py / reports/brahma_2.0_design.md §6 T2 / 2周影子期结束由苏摩111移除
# 仅影响: auto_executor/paper_executor的risk_gate分支（只记录不拦截），1.0行为零变化
export BRAHMA_SHADOW=1

nohup "$SCRON" "$CRONTAB" >> "$LOG" 2>&1 &
echo $! > "$PIDFILE"
echo "supercronic started pid=$(cat $PIDFILE)"
export PYTHONDONTWRITEBYTECODE=1
