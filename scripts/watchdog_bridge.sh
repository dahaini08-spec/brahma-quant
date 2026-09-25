#!/bin/bash
# watchdog_bridge.sh — gateway重启后的进程复活兜底 [9.23 三方联合修复]
# 问题：gateway被平台强杀重启时，nohup进程族+supercronic全灭，
#       supercronic死了resurrect没人跑，形成"看门狗也死"的单点
# 方案：OpenClaw cron(独立于gateway进程树存活)每5min调用本脚本
#       → 必跑 process_resurrect.sh（幂等，进程都活着则秒退）
cd /root/.openclaw/workspace/trading-system
bash scripts/process_resurrect.sh >> logs/resurrect.log 2>&1
exit 0
