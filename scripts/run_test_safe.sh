#!/usr/bin/env bash
# run_test_safe.sh — 抗重启测试运行器
# 设计院 2026-09-16 苏摩111自主决策
# 用法: bash scripts/run_test_safe.sh "pytest tests/xxx.py -q"
# 原理: 后台运行+输出到文件+轮询文件，不受gateway compaction重启影响
#
# 接入位置: 所有长exec(>15s)统一调用此脚本

set -eu
CMD="$1"
RESULT_FILE="/tmp/openclaw_exec_result.txt"
PID_FILE="/tmp/openclaw_exec_pid.txt"

# 后台运行，输出重定向到文件
nohup bash -c "$CMD" > "$RESULT_FILE" 2>&1 &
PID=$!
echo "$PID" > "$PID_FILE"
echo "STARTED PID=$PID"

# 等待完成（最长180s）
for i in $(seq 1 180); do
    if ! kill -0 "$PID" 2>/dev/null; then
        echo "DONE"
        cat "$RESULT_FILE"
        exit 0
    fi
    sleep 1
done

# 超时
kill "$PID" 2>/dev/null || true
echo "TIMEOUT"
cat "$RESULT_FILE"
