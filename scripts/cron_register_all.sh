#!/bin/bash
# cron_register_all.sh — 梵天全量cron任务注册脚本 v2
# [设计院封印 2026-08-13 苏摩111]
# 用法: bash scripts/cron_register_all.sh
# 注意: USER_ID/THREAD_MAIN 新机器需替换

set -e
echo "[梵天cron注册] 开始 $(date)"

USER_ID="73295708"
THREAD_MAIN="019fd70a-0942-72b1-aeb9-1bd4fc11b30d"
THREAD_SQUARE="019fe171-b435-7360-a246-b9f04b40bdde"
COUNT=0

echo "  [1/36] rsi-structure-watcher"
openclaw cron add \
  --name 'rsi-structure-watcher' \
  --every 5m \
  --message 'cd /root/.openclaw/workspace/trading-system && python3 scripts/rsi_structure_watcher.py 2>&1 | tail -3 【输出规则】必须用中文回复。仅当有RSI突破或结构破坏预警时推送内容。无信号则只回复：HEARTBEAT_OK' \
  --channel jarvis \
  --to "$USER_ID:thread:$THREAD_MAIN" \
  --light-context \
  --announce
COUNT=$((COUNT+1))

echo "  [2/36] brahma-360-health"
openclaw cron add \
  --name 'brahma-360-health' \
  --every 2h \
  --message 'cd /root/.openclaw/workspace/trading-system && python3 scripts/brahma_health.py 2>&1 | tail -3 【输出规则】必须用中文回复。仅当健康分<90或有CRITICAL/ERROR时推送报告。健康则只回复：HEARTBEAT_OK' \
  --channel jarvis \
  --to "$USER_ID:thread:$THREAD_MAIN" \
  --light-context \
  --announce
COUNT=$((COUNT+1))

echo "  [3/36] brahma-state-refresh"
openclaw cron add \
  --name 'brahma-state-refresh' \
  --every 1h \
  --message 'cd /root/.openclaw/workspace/trading-system && python3 scripts/brahma_state_refresh.py 2>&1 | tail -2 【输出规则】必须用中文回复。仅当体制发生切换时推送新体制信息。无切换则只回复：HEARTBEAT_OK' \
  --channel jarvis \
  --to "$USER_ID:thread:$THREAD_MAIN" \
  --light-context \
  --announce
COUNT=$((COUNT+1))

echo "  [4/36] auto-executor-run"
openclaw cron add \
  --name 'auto-executor-run' \
  --every 1h \
  --message 'cd /root/.openclaw/workspace/trading-system && python3 scripts/auto_executor.py 2>&1 | tail -5 【输出规则】必须用中文回复。仅当有交易执行、信号触发或错误时推送内容。无动作则只回复：HEARTBEAT_OK' \
  --channel jarvis \
  --to "$USER_ID:thread:$THREAD_MAIN" \
  --light-context \
  --announce
COUNT=$((COUNT+1))

echo "  [5/36] position-guardian-unified"
openclaw cron add \
  --name 'position-guardian-unified' \
  --every 1h \
  --message 'bash /root/.openclaw/workspace/trading-system/scripts/cron_noai_runner.sh position-guardian-unified 【输出规则】必须用中文回复。仅当持仓有风险预警（止损触发/逆体制/重大亏损）时推送。无异常则只回复：HEARTBEAT_OK' \
  --channel jarvis \
  --to "$USER_ID:thread:$THREAD_MAIN" \
  --light-context \
  --announce
COUNT=$((COUNT+1))

echo "  [6/36] brahma-gex-refresh"
openclaw cron add \
  --name 'brahma-gex-refresh' \
  --every 4h \
  --message 'Run GEX refresh for BTC and ETH options data. Execute this Python code: import sys; sys.path.insert(0,"/root/.openclaw/workspace/trading-system"); from brahma_brain.gex_scanner import scan_gex; r1=scan_gex("BTC",force=True); r2=scan_gex("ETH",force=T...' \
  --channel jarvis \
  --to "$USER_ID:thread:$THREAD_MAIN" \
  --light-context \
  --announce
COUNT=$((COUNT+1))

echo "  [7/36] oi-advanced-scanner"
openclaw cron add \
  --name 'oi-advanced-scanner' \
  --every 2h \
  --message 'bash /root/.openclaw/workspace/trading-system/scripts/cron_noai_runner.sh oi-advanced-scanner If the script exits with code 0 and produces no meaningful signal, alert, or anomaly, reply ONLY: HEARTBEAT_OK' \
  --channel jarvis \
  --to "$USER_ID:thread:$THREAD_MAIN" \
  --light-context \
  --announce
COUNT=$((COUNT+1))

echo "  [8/36] 🧠square-市场热度总结"
openclaw cron add \
  --name '🧠square-市场热度总结' \
  --cron '0 * * * *' \
  --message 'Run: bash -c '\''cd /root/.openclaw/workspace/trading-system && python3 scripts/square/square_hot_poster.py --type market_summary --no-delay'\''; reply HEARTBEAT_OK always' \
  --channel jarvis \
  --to "$USER_ID:thread:$THREAD_SQUARE" \
  --announce
COUNT=$((COUNT+1))

echo "  [9/36] auto-1hao-trigger"
openclaw cron add \
  --name 'auto-1hao-trigger' \
  --every 3h \
  --message 'Run brahma signal scan. Steps:  1. Read screener candidates: cd /root/.openclaw/workspace/trading-system && python3 -c " import json; from pathlib import Path d = json.loads(Path(\"data/scan_candidates.json\").read_text()) syms = [x[\"symbol\"] for x...' \
  --channel jarvis \
  --to "$USER_ID:thread:$THREAD_MAIN" \
  --model 'litellm/Qwen3.5-397B-A17B-SGLang' \
  --light-context \
  --announce
COUNT=$((COUNT+1))

echo "  [10/36] ops-disk-clean"
openclaw cron add \
  --name 'ops-disk-clean' \
  --every 12h \
  --message 'bash -c "cd /root/.openclaw/workspace/trading-system && python3 scripts/brahma_ops_center.py --disk-clean 2>&1"' \
  --channel jarvis \
  --to "$USER_ID:thread:$THREAD_MAIN" \
  --model 'litellm/Qwen3.5-397B-A17B-SGLang' \
  --light-context \
  --announce
COUNT=$((COUNT+1))

echo "  [11/36] signal-settler"
openclaw cron add \
  --name 'signal-settler' \
  --every 2h \
  --message 'bash /root/.openclaw/workspace/trading-system/scripts/cron_noai_runner.sh signal-settler If the script exits successfully with no meaningful signal, alert, or error, reply ONLY: HEARTBEAT_OK' \
  --channel jarvis \
  --to "$USER_ID:thread:$THREAD_MAIN" \
  --light-context \
  --announce
COUNT=$((COUNT+1))

echo "  [12/36] pump-hunter"
openclaw cron add \
  --name 'pump-hunter' \
  --every 3h \
  --message 'bash /root/.openclaw/workspace/trading-system/scripts/cron_noai_runner.sh pump-hunter 【输出规则】必须用中文回复。仅当有score≥108的暴涨预警信号时推送内容。无信号或脚本超时则只回复：HEARTBEAT_OK' \
  --channel jarvis \
  --to "$USER_ID:thread:$THREAD_MAIN" \
  --light-context \
  --announce
COUNT=$((COUNT+1))

echo "  [13/36] macro-ai-bridge"
openclaw cron add \
  --name 'macro-ai-bridge' \
  --every 4h \
  --message 'Run: cd /root/.openclaw/workspace/trading-system && python3 scripts/macro_ai_bridge.py 2>&1. If state is RISK_OFF, reply with a warning. Otherwise reply HEARTBEAT_OK.' \
  --channel jarvis \
  --to "$USER_ID:thread:$THREAD_MAIN" \
  --model 'litellm/Qwen3.5-397B-A17B-SGLang' \
  --light-context \
  --announce
COUNT=$((COUNT+1))

echo "  [14/36] ai-pro-screener"
openclaw cron add \
  --name 'ai-pro-screener' \
  --every 6h \
  --message 'Run: cd /root/.openclaw/workspace/trading-system && python3 scripts/ai_pro_screener.py 2>&1 | tail -5. Reply HEARTBEAT_OK when done. Only push to user if error or exception found.' \
  --channel jarvis \
  --to "$USER_ID:thread:$THREAD_MAIN" \
  --model 'litellm/Qwen3.5-397B-A17B-SGLang' \
  --light-context \
  --announce
COUNT=$((COUNT+1))

echo "  [15/36] brahma-session-compressor"
openclaw cron add \
  --name 'brahma-session-compressor' \
  --every 6h \
  --message 'Run the brahma session compressor to clean up old checkpoint files and free memory. Execute: cd /root/.openclaw/workspace/trading-system && python3 scripts/brahma_session_compressor.py. Report how many MB were freed. If nothing was freed reply HEARTB...' \
  --channel jarvis \
  --to "$USER_ID:thread:$THREAD_MAIN" \
  --light-context \
  --announce
COUNT=$((COUNT+1))

echo "  [16/36] log-rotation"
openclaw cron add \
  --name 'log-rotation' \
  --every 6h \
  --message 'bash /root/.openclaw/workspace/trading-system/scripts/cron_noai_runner.sh log-rotation If the script exits with code 0 and produces no meaningful signal, alert, or anomaly, reply ONLY: HEARTBEAT_OK' \
  --channel jarvis \
  --to "$USER_ID:thread:$THREAD_MAIN" \
  --light-context \
  --announce
COUNT=$((COUNT+1))

echo "  [17/36] ☀️早间梵天日报-Square"
openclaw cron add \
  --name '☀️早间梵天日报-Square' \
  --cron '0 * * * *' \
  --message '⚠️ 铁律：第一步用 binance-cli 获取 BTCUSDT/ETHUSDT 实时价格+24H涨跌幅。失败 → 回复 HEARTBEAT_OK 终止。  获取价格后，运行体制状态： python3 -c " import sys,json from pathlib import Path s=Path(\"/root/.openclaw/workspace/trading-system/data/brahma_state.json\") d=json.loads(s.read_text()...' \
  --channel jarvis \
  --to "$USER_ID:thread:$THREAD_MAIN" \
  --light-context \
  --announce
COUNT=$((COUNT+1))

echo "  [18/36] tradfi-watcher"
openclaw cron add \
  --name 'tradfi-watcher' \
  --every 4h \
  --message 'Run: cd /root/.openclaw/workspace/trading-system && python3 scripts/tradfi_watcher.py. Report any macro alerts (DXY/NQ/VIX异动). If no alerts, reply HEARTBEAT_OK.' \
  --channel jarvis \
  --to "$USER_ID:thread:$THREAD_MAIN" \
  --light-context \
  --announce
COUNT=$((COUNT+1))

echo "  [19/36] 🔢square-夜盘跌幅榜"
openclaw cron add \
  --name '🔢square-夜盘跌幅榜' \
  --cron '0 * * * *' \
  --message 'Run: bash -c '\''cd /root/.openclaw/workspace/trading-system && python3 scripts/square/square_hot_poster.py --type top_losers'\''; reply HEARTBEAT_OK always' \
  --channel jarvis \
  --to "$USER_ID:thread:$THREAD_SQUARE" \
  --announce
COUNT=$((COUNT+1))

echo "  [20/36] brahma-cron-doctor"
openclaw cron add \
  --name 'brahma-cron-doctor' \
  --every 8h \
  --message 'Run: cd /root/.openclaw/workspace/trading-system && python3 scripts/brahma_cron_doctor.py 2>&1. If output is HEARTBEAT_OK, reply HEARTBEAT_OK. Otherwise report the full diagnosis.' \
  --channel jarvis \
  --to "$USER_ID:thread:$THREAD_MAIN" \
  --light-context \
  --announce
COUNT=$((COUNT+1))

echo "  [21/36] 🔭square-亚盘热度"
openclaw cron add \
  --name '🔭square-亚盘热度' \
  --cron '0 * * * *' \
  --message 'Run: bash -c '\''cd /root/.openclaw/workspace/trading-system && python3 scripts/square/square_hot_poster.py --type hot_tickers'\''; reply HEARTBEAT_OK always' \
  --channel jarvis \
  --to "$USER_ID:thread:$THREAD_SQUARE" \
  --announce
COUNT=$((COUNT+1))

echo "  [22/36] 💡午后洞察帖-Square"
openclaw cron add \
  --name '💡午后洞察帖-Square' \
  --cron '0 * * * *' \
  --message '⚠️ 铁律：第一步用 binance-cli 获取 BTCUSDT/ETHUSDT 实时价格。失败 → 回复 HEARTBEAT_OK 终止。  获取价格后，获取当前OB/FVG状态： python3 -c " import sys,json; sys.path.insert(0,\".\") from pathlib import Path s=Path(\"/root/.openclaw/workspace/trading-system/data/brahma_state.json\") d...' \
  --channel jarvis \
  --to "$USER_ID:thread:$THREAD_MAIN" \
  --light-context \
  --announce
COUNT=$((COUNT+1))

echo "  [23/36] 📡square-资金费率"
openclaw cron add \
  --name '📡square-资金费率' \
  --cron '0 * * * *' \
  --message 'Run: bash -c '\''cd /root/.openclaw/workspace/trading-system && python3 scripts/square/square_hot_poster.py --type funding_rate'\''; reply HEARTBEAT_OK always' \
  --channel jarvis \
  --to "$USER_ID:thread:$THREAD_SQUARE" \
  --announce
COUNT=$((COUNT+1))

echo "  [24/36] core-dump-cleaner"
openclaw cron add \
  --name 'core-dump-cleaner' \
  --every 1d \
  --message 'Run this shell command and reply with only its output: python3 -c " import os, glob removed = 0 for f in glob.glob(\"/tmp/core.*\") + glob.glob(\"/root/core.*\"):     try: os.remove(f); removed += 1     except: pass print(f\"HEARTBEAT_OK core_dumps={...' \
  --channel jarvis \
  --to "$USER_ID:thread:$THREAD_MAIN" \
  --model 'litellm/Qwen3.5-397B-A17B-SGLang' \
  --light-context \
  --announce
COUNT=$((COUNT+1))

echo "  [25/36] brahma-360-daily"
openclaw cron add \
  --name 'brahma-360-daily' \
  --every 1d \
  --message '执行梵天360日报（中文）： 1) cd /root/.openclaw/workspace/trading-system && python3 scripts/update_live_performance.py 2>&1 | tail -3 2) cd /root/.openclaw/workspace/trading-system && python3 scripts/smart_digest.py --push 2>&1 | tail -5 3) cd /root/.openclaw/w...' \
  --channel jarvis \
  --to "$USER_ID:thread:$THREAD_MAIN" \
  --model 'litellm/Qwen3.5-397B-A17B-SGLang' \
  --light-context \
  --announce
COUNT=$((COUNT+1))

echo "  [26/36] 🔥square-热度币早盘"
openclaw cron add \
  --name '🔥square-热度币早盘' \
  --cron '0 * * * *' \
  --message 'Run: bash -c '\''cd /root/.openclaw/workspace/trading-system && python3 scripts/square/square_hot_poster.py --type hot_tickers --no-delay'\''; reply HEARTBEAT_OK always' \
  --channel jarvis \
  --to "$USER_ID:thread:$THREAD_SQUARE" \
  --announce
COUNT=$((COUNT+1))

echo "  [27/36] session-disk-cleanup"
openclaw cron add \
  --name 'session-disk-cleanup' \
  --cron '0 * * * *' \
  --message 'Run the session disk cleanup script to remove orphaned session files. Execute: cd /root/.openclaw/workspace/trading-system && python3 scripts/session_disk_cleanup.py. Report freed MB. If freed > 100MB say: 🧹 Session清理完成 释放XMB. Otherwise HEARTBEAT_OK.' \
  --channel jarvis \
  --to "$USER_ID:thread:$THREAD_MAIN" \
  --model 'litellm/Qwen3.5-397B-A17B-SGLang' \
  --light-context \
  --announce
COUNT=$((COUNT+1))

echo "  [28/36] brahma-data-janitor"
openclaw cron add \
  --name 'brahma-data-janitor' \
  --every 1d \
  --message '运行梵天数据自动清理守卫：cd /root/.openclaw/workspace/trading-system && python3 scripts/brahma_data_janitor.py。输出清理报告，无清理则回复HEARTBEAT_OK静默。' \
  --channel jarvis \
  --to "$USER_ID:thread:$THREAD_MAIN" \
  --announce
COUNT=$((COUNT+1))

echo "  [29/36] brahma-morning-report"
openclaw cron add \
  --name 'brahma-morning-report' \
  --every 1d \
  --message 'Generate the daily Brahma morning report. Steps: 1) cd /root/.openclaw/workspace/trading-system 2) read data/brahma_state.json for regime 3) count signals from data/live_signal_log.jsonl last 24h 4) check cron errors via: openclaw cron list. Then sen...' \
  --channel jarvis \
  --to "$USER_ID:thread:$THREAD_MAIN" \
  --light-context \
  --announce
COUNT=$((COUNT+1))

echo "  [30/36] 💡square-热帖跟评"
openclaw cron add \
  --name '💡square-热帖跟评' \
  --cron '0 * * * *' \
  --message 'Run: bash -c '\''cd /root/.openclaw/workspace/trading-system && python3 scripts/square/square_hot_poster.py --type hot_news'\''; reply HEARTBEAT_OK always' \
  --channel jarvis \
  --to "$USER_ID:thread:$THREAD_SQUARE" \
  --announce
COUNT=$((COUNT+1))

echo "  [31/36] 🎯square-涨跌幅榜"
openclaw cron add \
  --name '🎯square-涨跌幅榜' \
  --cron '0 * * * *' \
  --message 'Run: bash -c '\''cd /root/.openclaw/workspace/trading-system && python3 scripts/square/square_hot_poster.py --type top_gainers --no-delay'\''; reply HEARTBEAT_OK always' \
  --channel jarvis \
  --to "$USER_ID:thread:$THREAD_SQUARE" \
  --announce
COUNT=$((COUNT+1))

echo "  [32/36] 🌙夜盘守望帖-Square"
openclaw cron add \
  --name '🌙夜盘守望帖-Square' \
  --cron '0 * * * *' \
  --message '⚠️ 铁律：第一步用 binance-cli 获取 BTCUSDT/ETHUSDT 实时价格。失败 → 回复 HEARTBEAT_OK 终止。  获取价格后，运行OB/清算集群实时检查： cd /root/.openclaw/workspace/trading-system && python3 -c " import sys,os,json; sys.path.insert(0,'\''.'\'') os.environ['\''BINANCE_API_KEY'\'']='\''sDqoRAy...' \
  --channel jarvis \
  --to "$USER_ID:thread:$THREAD_MAIN" \
  --model 'litellm/Qwen3.5-397B-A17B-SGLang' \
  --light-context \
  --announce
COUNT=$((COUNT+1))

echo "  [33/36] 🔥square-暴涨预警"
openclaw cron add \
  --name '🔥square-暴涨预警' \
  --cron '0 * * * *' \
  --message 'Run: bash -c '\''cd /root/.openclaw/workspace/trading-system && python3 scripts/square/square_hot_poster.py --type pump_alert'\''; reply HEARTBEAT_OK always' \
  --channel jarvis \
  --to "$USER_ID:thread:$THREAD_SQUARE" \
  --announce
COUNT=$((COUNT+1))

echo "  [34/36] brahma-scan-guard"
openclaw cron add \
  --name 'brahma-scan-guard' \
  --every 12h  # was one-shot \
  --message 'Run brahma full scan guard for BTC ETH SOL. Execute: cd /root/.openclaw/workspace/trading-system && python3 brahma_brain/brahma_analysis_runner.py BTCUSDT ETHUSDT SOLUSDT 2>&1 | tail -10 【输出规则】必须用中文回复。仅当有score≥138的高分信号时推送分析卡片。无高分信号则只回复：HEARTBEAT_OK' \
  --channel jarvis \
  --to "$USER_ID:thread:$THREAD_MAIN" \
  --light-context \
  --announce
COUNT=$((COUNT+1))

echo "  [35/36] 📈square-合约热度榜"
openclaw cron add \
  --name '📈square-合约热度榜' \
  --cron '0 * * * *' \
  --message 'Run: bash -c '\''cd /root/.openclaw/workspace/trading-system && timeout 20 python3 scripts/square/square_hot_poster.py --type top_gainers 2>&1'\''; if output has content reply it in Chinese, else reply HEARTBEAT_OK' \
  --channel jarvis \
  --to "$USER_ID:thread:$THREAD_SQUARE" \
  --announce
COUNT=$((COUNT+1))

echo "  [36/36] 🌐square-热门话题"
openclaw cron add \
  --name '🌐square-热门话题' \
  --cron '0 * * * *' \
  --message 'Run: bash -c '\''cd /root/.openclaw/workspace/trading-system && python3 scripts/square/square_hot_poster.py --type hot_news --no-delay'\''; reply HEARTBEAT_OK always' \
  --channel jarvis \
  --to "$USER_ID:thread:$THREAD_SQUARE" \
  --announce
COUNT=$((COUNT+1))

echo "[梵天cron注册] 完成: $COUNT 个任务注册成功"
