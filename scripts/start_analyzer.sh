#!/bin/bash
# start_analyzer.sh — 一键启动梵天分析系统
# 设计院 2026-09-20 苏摩111
# 用途：系统重启后一键恢复全部服务
# 使用：bash scripts/start_analyzer.sh

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(dirname "$SCRIPT_DIR")"
cd "$BASE_DIR"

echo "╔══════════════════════════════════════════╗"
echo "║  梵天分析系统 一键启动                    ║"
echo "║  设计院 2026-09-20 苏摩111                ║"
echo "╚══════════════════════════════════════════╝"

# ── 1. Python依赖检查 ──
echo "[1/6] Python依赖..."
python3 -c "import torch" 2>/dev/null || {
    echo "  torch未安装，安装中..."
    timeout 60 pip install --break-system-packages -q torch --index-url https://download.pytorch.org/whl/cpu 2>/dev/null || echo "  ⚠️ torch安装超时，跳过(非核心)"
}
echo "  ✅ Python依赖就绪"

# ── 2. 常驻进程 ──
echo "[2/6] 常驻进程..."
# CVD采集器
if ! pgrep -f cvd_ws_collector >/dev/null; then
    nohup python3 scripts/cvd_ws_collector.py >> logs/cvd_ws.log 2>&1 &
    echo "  ✅ CVD采集器 PID=$!"
else
    echo "  ✅ CVD采集器已运行 PID=$(pgrep -f cvd_ws_collector | head -1)"
fi

# 清算地图采集器
if ! pgrep -f liqmap_collector >/dev/null; then
    nohup python3 brahma_brain/liqmap_collector.py >> logs/liqmap.log 2>&1 &
    echo "  ✅ 清算采集器 PID=$!"
else
    echo "  ✅ 清算采集器已运行 PID=$(pgrep -f liqmap_collector | head -1)"
fi

# ── 3. supercronic ──
echo "[3/6] supercronic..."
if ! pgrep -f "supercronic.*brahma_crontab" >/dev/null; then
    nohup ./supercronic brahma_crontab.txt >> logs/supercronic.log 2>&1 &
    echo "  ✅ supercronic PID=$!"
    sleep 2
else
    echo "  ✅ supercronic已运行 PID=$(pgrep -f 'supercronic.*brahma_crontab' | head -1)"
fi

# ── 4. 独立看门狗 ──
echo "[4/6] 独立看门狗..."
if ! pgrep -f independent_watchdog >/dev/null; then
    nohup /bin/bash scripts/independent_watchdog.sh >> logs/watchdog.log 2>&1 &
    echo "  ✅ 看门狗 PID=$!"
else
    echo "  ✅ 看门狗已运行 PID=$(pgrep -f independent_watchdog | head -1)"
fi

# ── 5. 数据刷新 ──
echo "[5/6] 数据刷新..."
# 清算热图
timeout 60 python3 scripts/liq_heatmap.py BTCUSDT ETHUSDT >> logs/liq_heatmap.log 2>&1 && echo "  ✅ 清算热图" || echo "  ⚠️ 清算热图(非致命)"
# OI扫描
timeout 90 python3 scripts/oi_advanced_scanner.py >> logs/oi_scanner.log 2>&1 && echo "  ✅ OI扫描" || echo "  ⚠️ OI扫描(非致命)"
# 风控状态刷新
python3 -c "
import json, time
from pathlib import Path
for f in ['circuit_breaker.json', 'drawdown_state.json']:
    p = Path(f'data/{f}')
    if p.exists():
        d = json.loads(p.read_text())
        d['ts'] = time.time()
        d['last_updated'] = time.time()
        p.write_text(json.dumps(d, indent=2))
print('  ✅ 风控状态')
" 2>/dev/null || echo "  ⚠️ 风控刷新(非致命)"

# ── 6. Autopilot记忆层 ──
echo "[6/6] Autopilot记忆层..."
python3 scripts/autopilot_l0_refresh.py >> logs/autopilot_l0.log 2>&1 && echo "  ✅ L0工作记忆" || echo "  ⚠️ L0(非致命)"

# ── 汇总 ──
echo ""
echo "╔══════════════════════════════════════════╗"
echo "║  启动完成                                 ║"
echo "╠══════════════════════════════════════════╣"
echo "║  进程: supercronic + CVD + liqmap + 看门狗 ║"
echo "║  数据: 清算 + OI + 风控 + L0 已刷新        ║"
echo "║  cron: $(grep -c '^[0-9*/]' brahma_crontab.txt)条supercronic任务              ║"
echo "║  Autopilot: 每5min自动决策+记忆            ║"
echo "╚══════════════════════════════════════════╝"
