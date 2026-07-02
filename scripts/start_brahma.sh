#!/bin/bash
# 梵天系统启动脚本（设计院封印 2026-07-02）
# 用途：系统重启后一键恢复所有依赖并启动服务
# 使用：bash scripts/start_brahma.sh

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(dirname "$SCRIPT_DIR")"

echo "[Brahma] ===== 梵天系统启动 ====="

# 1. Python依赖
echo "[Brahma] Step 1: 检查Python依赖..."
python3 -c "import torch" 2>/dev/null || {
    echo "[Brahma]   torch未安装，安装中..."
    pip install --break-system-packages -q torch --index-url https://download.pytorch.org/whl/cpu
}
python3 -c "import huggingface_hub, safetensors, einops" 2>/dev/null || {
    echo "[Brahma]   安装Kronos辅助依赖..."
    pip install --break-system-packages -q huggingface_hub safetensors einops python-dotenv
}
echo "[Brahma]   ✅ Python依赖就绪"

# 2. OmniRoute（可选，按需启动）
if command -v omniroute &>/dev/null; then
    echo "[Brahma] Step 2: 启动OmniRoute..."
    PORT=8765 nohup node "$(npm root -g)/omniroute/dist/server-ws.mjs" \
        > /tmp/omniroute.log 2>&1 &
    echo "[Brahma]   OmniRoute PID=$! 端口:8765"
else
    echo "[Brahma] Step 2: OmniRoute未安装，使用OpenRouter直连（.env已配置）"
fi

# 3. 验证Kronos
echo "[Brahma] Step 3: 验证Kronos模型..."
cd "$BASE_DIR"
python3 -c "
import sys
sys.path.insert(0, 'external/Kronos')
sys.path.insert(0, 'brahma_brain')
import kronos_engine
kronos_engine._model_load_attempted = False
kronos_engine._model_loaded = False
ok = kronos_engine._load_model()
print('[Brahma]   Kronos:', '✅ loaded' if ok else '❌ failed')
" 2>&1 | grep "\[Brahma\]"

echo "[Brahma] ===== 启动完成 ====="
