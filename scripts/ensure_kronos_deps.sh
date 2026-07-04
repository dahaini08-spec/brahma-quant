#!/bin/bash
# Kronos依赖自动恢复脚本 - gateway重启后调用
echo "[ensure_kronos_deps] 检查Kronos依赖..."

python3 -c "import torch" 2>/dev/null || {
    echo "[ensure_kronos_deps] 安装torch..."
    pip install torch --index-url https://download.pytorch.org/whl/cpu --break-system-packages -q
}

python3 -c "import einops" 2>/dev/null || {
    echo "[ensure_kronos_deps] 安装einops..."
    pip install einops --break-system-packages -q
}

python3 -c "import huggingface_hub" 2>/dev/null || {
    echo "[ensure_kronos_deps] 安装huggingface_hub..."
    pip install huggingface_hub --break-system-packages -q
}

python3 -c "import safetensors" 2>/dev/null || {
    echo "[ensure_kronos_deps] 安装safetensors..."
    pip install safetensors --break-system-packages -q
}

python3 -c "import torch, einops, huggingface_hub, safetensors; print('[ensure_kronos_deps] ✅ 全部依赖就绪')"
