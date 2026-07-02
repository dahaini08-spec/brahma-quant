#!/bin/bash
# 梵天系统依赖安装脚本（设计院封印 2026-07-02）
# 用途：系统重启后一键恢复所有Python依赖
# 使用：bash scripts/install_deps.sh

set -e
echo "[install_deps] 安装梵天系统依赖..."

pip install --break-system-packages -q \
    torch --index-url https://download.pytorch.org/whl/cpu \
    2>&1 | tail -2

pip install --break-system-packages -q \
    huggingface_hub safetensors einops python-dotenv \
    2>&1 | tail -2

echo "[install_deps] ✅ 完成"
python3 -c "import torch, huggingface_hub, safetensors, einops; print('[install_deps] 验证通过: torch', torch.__version__)"
