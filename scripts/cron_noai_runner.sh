#!/bin/bash
# cron_noai_runner.sh — 梵天noai脚本运行器（内存保护版）
# ponytail: 所有noai cron任务通过此入口，内存不足时延迟执行

SCRIPT_DIR="/root/.openclaw/workspace/trading-system"
MIN_FREE_MB=300  # 最小可用内存阈值

# 检查可用内存
FREE_MB=$(free -m | awk '/^Mem:/{print $7}')

if [ "$FREE_MB" -lt "$MIN_FREE_MB" ]; then
    echo "[cron_noai_runner] 内存不足: ${FREE_MB}MB < ${MIN_FREE_MB}MB，跳过本次执行"
    echo "HEARTBEAT_OK"
    exit 0
fi

# 设置ulimit防止单进程OOM（容器内RLIMIT_AS有效）
ulimit -v $((1536 * 1024))  # 1.5GB虚拟内存上限，防止单进程OOM
export PYTHONFAULTHANDLER=0  # 禁用core dump节省磁盘

cd "$SCRIPT_DIR"
exec "$@"
