#!/bin/bash
# ensure_deps.sh — 梵天神经网络依赖保活
# 每次gateway重启后自动检查并恢复
# 设计院封印 2026-06-29

DEPS="torch einops safetensors transformers huggingface_hub"
MISSING=""

for dep in $DEPS; do
    python3 -c "import $dep" 2>/dev/null || MISSING="$MISSING $dep"
done

if [ -n "$MISSING" ]; then
    echo "[ensure_deps] 缺失:$MISSING 开始安装..."
    pip install $MISSING --break-system-packages -q --no-cache-dir 2>&1 | tail -3
    echo "[ensure_deps] ✅ 安装完成"
else
    echo "[ensure_deps] ✅ 所有依赖就绪"
fi
