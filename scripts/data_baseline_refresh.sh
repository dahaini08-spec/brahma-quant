#!/bin/bash
# data_baseline_refresh.sh — 梵天系统基线数据刷新
# 每天2次 UTC 00:00 + 12:00，替代7条散装刷新任务
# 分析时已有按需刷新逻辑，此脚本只做基线保鲜
# 2026-09-13 苏摩111三方深度优化封印

cd /root/.openclaw/workspace/trading-system
echo "[baseline_refresh] $(date -u +%Y-%m-%dT%H:%M:%SZ) start"

# 1. GEX（原gex_engine 15m，TTL=30min，但分析时按需检测）
echo "[1/5] GEX..."
python3 brahma_brain/gex_engine.py --currency ALL >> logs/baseline_refresh.log 2>&1 || echo "  ⚠️ gex failed"

# 2. Vol Beta BTC+ETH（原4h×2，TTL=1h）
echo "[2/5] Vol Beta BTC..."
python3 brahma_brain/vol_beta_engine.py --currency BTC >> logs/baseline_refresh.log 2>&1 || echo "  ⚠️ vol_beta BTC failed"
echo "[2/5] Vol Beta ETH..."
python3 brahma_brain/vol_beta_engine.py --currency ETH >> logs/baseline_refresh.log 2>&1 || echo "  ⚠️ vol_beta ETH failed"

# 3. Liq Heatmap BTC+ETH（原4h，analysis_runner内get_liq_heatmap实时拉）
echo "[3/5] Liq Heatmap..."
python3 -c "import sys; sys.path.insert(0,'scripts'); from liq_heatmap import get_liq_heatmap; get_liq_heatmap('BTCUSDT'); get_liq_heatmap('ETHUSDT')" >> logs/baseline_refresh.log 2>&1 || echo "  ⚠️ liq_heatmap failed"

# 4. Macro Real（原30m，manual_analysis 24h过期自动刷新）
echo "[4/5] Macro Real..."
python3 scripts/macro_real_fetcher.py >> logs/baseline_refresh.log 2>&1 || echo "  ⚠️ macro_real failed"

# 5. OI Advanced Scanner（原2h，analysis_runner内已调用）
echo "[5/5] OI Scanner..."
python3 scripts/oi_advanced_scanner.py >> logs/baseline_refresh.log 2>&1 || echo "  ⚠️ oi_scanner failed"

echo "[baseline_refresh] $(date -u +%Y-%m-%dT%H:%M:%SZ) done"
