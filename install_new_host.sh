#!/bin/bash
# ═══════════════════════════════════════════════════════════
# 梵天量化交易系统 - 新宿主一键安装脚本
# 生成: 2026-09-18 苏摩111
# 用法: bash install_new_host.sh
# ═══════════════════════════════════════════════════════════

set -e

echo "🏛️ 梵天系统新宿主安装"
echo "========================"

# 1. 克隆仓库
echo ""
echo "1️⃣ 克隆仓库..."
git clone https://github.com/dahaini08-spec/brahma-quant.git
cd brahma-quant

# 2. 创建虚拟环境
echo ""
echo "2️⃣ 创建虚拟环境..."
python3 -m venv venv
source venv/bin/activate

# 3. 安装基础依赖
echo ""
echo "3️⃣ 安装基础依赖..."
pip install --upgrade pip
pip install -e .

# 4. 安装research依赖
echo ""
echo "4️⃣ 安装research依赖..."
pip install -e ".[research]"

# 5. 安装live依赖
echo ""
echo "5️⃣ 安装live依赖..."
pip install -e ".[live]"

# 6. 配置 .env
echo ""
echo "6️⃣ 配置 .env..."
cat > .env << 'ENVEOF'
# ===== Binance API =====
BINANCE_API_KEY=sDqoRAyeYHHzevKNxSj5JfkWpNUd6v8qPAhVy0Y8wbWGwC48eC7uhFOENAlVqV7b
BINANCE_SECRET=hXQnzQco9SNVgKgF2m3xvBGlJjOHBVtlzqRlxOTkp0kiJAwAOTeUiGLQSAopqIj7
BINANCE_API_SECRET=hXQnzQco9SNVgKgF2m3xvBGlJjOHBVtlzqRlxOTkp0kiJAwAOTeUiGLQSAopqIj7
BINANCE_TESTNET=false

# ===== Binance Square =====
SQUARE_KEY_0=d9f19e3f6ba3480584db27b09bec0f27
SQUARE_KEY_1=c43ebad6a8434d1b91a039dbf43fda29
SQUARE_KEY_2=278f3e81efda4274a1d8e15dbc32ec88

# ===== OpenRouter =====
OPENROUTER_API_KEY=YOUR_OPENROUTER_KEY_HERE

# ===== AI-Trader (ai4trade.ai) =====
AITRADER_AGENT_ID=23903
AITRADER_AGENT_TOKEN=RkSIVV9xvC3VN_Hn0jqyNioc8CUqJ9-sqrumH5ZYR1w
AITRADER_USER_ID=23904
AITRADER_USER_TOKEN=Kt7hRUKwygRXvH7cM1HsLfXPAhN33k7mJ1V1s580lb0

# ===== 系统配置 =====
# LD_PRELOAD路径需根据新宿主实际路径调整
# LD_PRELOAD=/path/to/brahma-quant/libgomp.so.1
ENVEOF

echo "✅ .env 已生成"

# 7. 创建目录
echo ""
echo "7️⃣ 创建目录..."
mkdir -p logs data/brahma_cache data/reports

# 8. 权限
echo ""
echo "8️⃣ 设置权限..."
chmod +x supercronic 2>/dev/null || echo "⚠️ supercronic不存在（需单独下载）"

# 9. 验证安装
echo ""
echo "9️⃣ 验证安装..."
python3 -c "
from brahma_brain.brahma_core import confluence_score, analyze
from brahma_brain.trader_brain import decide
from brahma_brain.regime_scorer import score
from brahma_brain.signal_selector import DYNAMIC_MIN
print('import链路: ✅')
print(f'score_gate: CHOP={DYNAMIC_MIN[\"CHOP_MID\"]} BULL={DYNAMIC_MIN[\"BULL_TREND\"]}')
r = score('BTC', force=True)
print(f'体制检测: BTC={r[\"regime\"]} ✅')
"

# 10. 启动进程
echo ""
echo "🔟 启动进程..."
echo "  启动 supercronic..."
nohup ./supercronic brahma_crontab.txt >> logs/supercronic.log 2>&1 &
echo "  supercronic PID=$!"
echo "  启动 CVD采集器..."
nohup python3 scripts/cvd_ws_collector.py >> logs/cvd_collector.log 2>&1 &
echo "  CVD PID=$!"
echo "  启动 看门狗..."
nohup bash scripts/independent_watchdog.sh >> logs/watchdog.log 2>&1 &
echo "  watchdog PID=$!"

echo ""
echo "========================"
echo "✅ 安装完成！"
echo ""
echo "验证: python3 scripts/brahma_manual_analysis.py --symbols BTC ETH"
echo "日志: tail -f logs/supercronic.log"
echo ""
echo "⚠️ 注意:"
echo "  - LD_PRELOAD路径需根据实际路径取消注释"
echo "  - supercronic需从原宿主复制或下载"
echo "  - Python要求 >=3.11"
echo "========================"
