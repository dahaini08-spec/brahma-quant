#!/bin/bash
# venv_restore.sh — 梵天venv依赖自愈脚本
# 设计院永久修复 2026-07-26 苏摩111封印
#
# 问题根因：容器重启后venv/site-packages为空，核心脚本(brahma_1hao_analysis等)
#          依赖venv/bin/python，静默失败无告警。
# 修复方案：重启后自动检测venv并恢复依赖，失败时推送P0告警。

WORK_DIR="/root/.openclaw/workspace/trading-system"
VENV_PYTHON="${WORK_DIR}/venv/bin/python"
REQ_CORE="${WORK_DIR}/requirements-core.txt"
LOG="/tmp/venv_restore.log"
PUSH_TARGET="73295708:thread:019fb612-d570-7f0b-89c5-2065284157e0"

echo "[$(date -u '+%Y-%m-%d %H:%M UTC')] venv_restore 开始检查..." >> $LOG

# 检查venv是否健康
VENV_OK=false
if [ -f "$VENV_PYTHON" ]; then
    PKG_COUNT=$("$VENV_PYTHON" -c "import requests, numpy, pandas; print('ok')" 2>/dev/null)
    [ "$PKG_COUNT" = "ok" ] && VENV_OK=true
fi

if $VENV_OK; then
    echo "[$(date -u '+%Y-%m-%d %H:%M UTC')] ✅ venv依赖正常，跳过修复" >> $LOG
    exit 0
fi

echo "[$(date -u '+%Y-%m-%d %H:%M UTC')] ⚠️ venv依赖丢失，开始修复..." >> $LOG

# Step1: 确保pip存在
if ! "$VENV_PYTHON" -m pip --version > /dev/null 2>&1; then
    echo "[$(date -u)] pip缺失，bootstrap..." >> $LOG
    curl -sS https://bootstrap.pypa.io/get-pip.py | "$VENV_PYTHON" >> $LOG 2>&1
fi

# Step2: 安装core依赖
"$VENV_PYTHON" -m pip install -r "$REQ_CORE" -q >> $LOG 2>&1
EXIT_CODE=$?

# Step3: 验证
VERIFY=$("$VENV_PYTHON" -c "import requests, numpy, pandas; print('ok')" 2>/dev/null)

if [ "$VERIFY" = "ok" ] && [ $EXIT_CODE -eq 0 ]; then
    echo "[$(date -u '+%Y-%m-%d %H:%M UTC')] ✅ venv恢复成功" >> $LOG
    # 推送成功通知
    openclaw message send \
        --channel jarvis \
        --target "$PUSH_TARGET" \
        --message "✅ 梵天venv自愈完成 | 容器重启后依赖恢复成功 | requests/numpy/pandas就绪" \
        2>/dev/null
else
    echo "[$(date -u '+%Y-%m-%d %H:%M UTC')] ❌ venv恢复失败" >> $LOG
    # 推送P0告警
    openclaw message send \
        --channel jarvis \
        --target "$PUSH_TARGET" \
        --message "🚨 P0 梵天venv恢复失败！1号工程/brahma_engine不可用，需苏摩人工介入！查看日志: /tmp/venv_restore.log" \
        2>/dev/null
    exit 1
fi
