#!/bin/bash
# [P2封印 2026-10-18 苏摩111] 沙盒化Shell执行器
# 接入位置: brahma_crontab.txt中替换直接python3调用
# 功能: ulimit资源限制 + 写入路径白名单 + 超时保护

SCRIPT="$1"; shift
ARGS="$@"
WORKDIR="/root/.openclaw/workspace/trading-system"

# 写入路径白名单（只允许写这些目录）
ALLOWED_WRITE_DIRS="data/ logs/ alerts/"

# 资源限制
ulimit -v 524288    # 虚拟内存 512MB
ulimit -t 300       # CPU时间 300s
ulimit -n 256       # 文件描述符 256个

# 日志标头
echo "[sandbox] $(date -u '+%Y-%m-%dT%H:%MZ') START $SCRIPT $ARGS" >> logs/sandbox.log

# 执行
cd "$WORKDIR"
timeout 240 python3 "$SCRIPT" $ARGS
EXIT_CODE=$?

echo "[sandbox] $(date -u '+%Y-%m-%dT%H:%MZ') EXIT=$EXIT_CODE $SCRIPT" >> logs/sandbox.log
exit $EXIT_CODE
