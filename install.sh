#!/bin/bash
# ============================================================
# install.sh — 梵天量化系统一键部署脚本
# [设计院封印 2026-08-13 苏摩111]
# 用法: bash install.sh
# 前置: Python 3.11+ | OpenClaw 已安装
# ============================================================

set -e
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✅ $*${NC}"; }
warn() { echo -e "${YELLOW}⚠️  $*${NC}"; }
fail() { echo -e "${RED}❌ $*${NC}"; exit 1; }

echo "=============================================="
echo " 梵天量化系统 一键部署"
echo " $(date)"
echo "=============================================="

# ── Step 1: 配置密钥 ───────────────────────────────────────
echo ""
echo "=== Step 1: 配置密钥 ==="
if [ ! -f ".env" ]; then
    cp .env.example .env
    warn ".env 已创建，请填入 BINANCE_API_KEY 和 BINANCE_SECRET"
    echo "编辑命令: nano .env"
    read -p "填写完毕后按回车继续..."
else
    ok ".env 已存在，跳过"
fi

# ── Step 2: 检查 Python ────────────────────────────────────
echo ""
echo "=== Step 2: 检查 Python ==="
PYTHON=$(python3 --version 2>&1)
echo "  Python: $PYTHON"
python3 -c "import sys; assert sys.version_info >= (3,11), 'Python 3.11+ required'" || fail "Python 3.11+ 必须"
ok "Python 版本检查通过"

# ── Step 3: 安装 Python 依赖 ──────────────────────────────
echo ""
echo "=== Step 3: 安装 Python 依赖 ==="
if [ -f "install_deps.sh" ]; then
    bash install_deps.sh
    ok "依赖安装完成"
else
    pip3 install --break-system-packages -r requirements.txt
    ok "依赖安装完成（基础版）"
fi

# ── Step 4: 验证依赖 ───────────────────────────────────────
echo ""
echo "=== Step 4: 验证依赖 ==="
if [ -f "brahma_deps_test.sh" ]; then
    bash brahma_deps_test.sh | grep -E "✅|❌|PASS|FAIL" | head -20
    ok "依赖验证完成"
fi

# ── Step 5: 检查 OpenClaw ──────────────────────────────────
echo ""
echo "=== Step 5: 检查 OpenClaw ==="
if ! command -v openclaw &>/dev/null; then
    fail "OpenClaw 未安装。请先安装 OpenClaw 并配置 Jarvis 渠道"
fi
openclaw gateway status 2>/dev/null | grep -E "Listening|port" | head -2
ok "OpenClaw 运行正常"

# ── Step 6: 注册 cron 任务 ────────────────────────────────
echo ""
echo "=== Step 6: 注册 cron 任务 ==="
EXISTING=$(openclaw cron list 2>/dev/null | grep -c "every\|cron " || echo 0)
if [ "$EXISTING" -gt 20 ]; then
    warn "检测到 $EXISTING 个已注册cron任务，跳过重复注册"
    warn "如需重新注册: openclaw cron list | 逐个删除后再运行 bash scripts/cron_register_all.sh"
else
    echo "  当前已有 $EXISTING 个任务，开始注册..."
    bash scripts/cron_register_all.sh
    ok "cron 任务注册完成"
fi

# ── Step 7: 启动 ws_guardian ──────────────────────────────
echo ""
echo "=== Step 7: 启动 ws_guardian ==="
if pgrep -f "ws_guardian" > /dev/null; then
    ok "ws_guardian 已在运行"
else
    cd /root/.openclaw/workspace/trading-system
    nohup python3 scripts/ws_guardian.py > logs/ws_guardian.log 2>&1 &
    sleep 2
    pgrep -f "ws_guardian" > /dev/null && ok "ws_guardian 已启动" || warn "ws_guardian 启动失败，请手动检查"
fi

# ── 完成 ────────────────────────────────────────────────────
echo ""
echo "=============================================="
ok "梵天系统部署完成！"
echo ""
echo "验证命令:"
echo "  python3 brahma_brain/brahma_health.py       # 健康检查"
echo "  python3 brahma_brain/brahma_360.py --report # 360体检"
echo "  python3 scripts/brahma_wiring_check.py      # 接线检查"
echo "  openclaw cron list                          # cron任务列表"
echo "=============================================="
