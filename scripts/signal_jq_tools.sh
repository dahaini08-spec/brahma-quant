#!/bin/bash
# signal_jq_tools.sh — 梵天信号日志 jq 分析工具集
# 设计院封印 2026-08-28 苏摩111
# 用法: bash scripts/signal_jq_tools.sh [命令]

BASE="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$BASE/data/live_signal_log.jsonl"

if [ ! -f "$LOG" ]; then
    echo "❌ 日志文件不存在: $LOG"
    exit 1
fi

CMD="${1:-help}"

case "$CMD" in

  # 最近N条信号（默认10）
  recent)
    N="${2:-10}"
    echo "📊 最近 $N 条信号:"
    tail -200 "$LOG" | jq -s \
      'sort_by(.ts) | reverse | .[:'"$N"'] | .[] |
       "\(.ts|todate) | \(.symbol) | \(.regime) | score=\(.score) | \(.verdict)"' -r
    ;;

  # 按体制统计今日信号分布
  regime)
    echo "📊 体制分布:"
    cat "$LOG" | jq -r '.regime' | sort | uniq -c | sort -rn
    ;;

  # 高分信号（score > N，默认130）
  high)
    N="${2:-130}"
    echo "🔥 score > $N 的信号:"
    cat "$LOG" | jq -r "select(.score > $N) |
      \"\(.ts|todate) | \(.symbol) | \(.regime) | score=\(.score) | \(.verdict)\"" 2>/dev/null | tail -20
    ;;

  # 指定标的的信号历史
  sym)
    SYM="${2:-BTCUSDT}"
    echo "📈 $SYM 信号历史:"
    cat "$LOG" | jq -r "select(.symbol == \"$SYM\") |
      \"\(.ts|todate) | \(.regime) | score=\(.score) | \(.verdict)\"" 2>/dev/null | tail -20
    ;;

  # 今日WR统计（已结信号）
  wr)
    echo "📊 信号结果统计:"
    cat "$LOG" | jq -r 'select(.result != null) | .result' | sort | uniq -c | sort -rn
    echo "---"
    cat "$LOG" | jq -s '[.[] | select(.result != null)] |
      {
        total: length,
        win: ([.[] | select(.result == "WIN")] | length),
        loss: ([.[] | select(.result == "LOSS")] | length)
      } |
      "总计: \(.total) | 盈: \(.win) | 亏: \(.loss) | WR: \(if .total > 0 then (.win/.total*100|round) else 0 end)%"' -r 2>/dev/null
    ;;

  # SKIP信号原因统计
  skip)
    echo "⏭️ SKIP原因分布:"
    cat "$LOG" | jq -r 'select(.verdict == "SKIP" or .action == "SKIP") | .reason // .skip_reason // "unknown"' \
      2>/dev/null | sort | uniq -c | sort -rn | head -15
    ;;

  # 实时追踪（tail -f）
  watch)
    echo "👁️ 实时信号监控（Ctrl+C退出）:"
    tail -f "$LOG" | jq -r '
      "\(.ts|todate) | \(.symbol) | \(.regime) | score=\(.score) | \(.verdict)"
    ' 2>/dev/null
    ;;

  # 磁盘占用
  size)
    echo "💾 日志文件大小:"
    ls -lh "$BASE/data/"*signal* "$BASE/data/"*log* 2>/dev/null | awk '{print $5, $9}'
    du -sh "$BASE/data/"
    ;;

  # py-spy 火焰图（需要梵天分析进程在跑）
  flame)
    PID=$(pgrep -f "brahma_analysis_runner" | head -1)
    if [ -z "$PID" ]; then
      echo "❌ brahma_analysis_runner 未在运行，先触发一次分析"
      exit 1
    fi
    OUT="/tmp/brahma_flame_$(date +%s).svg"
    echo "🔥 py-spy 采样 PID=$PID → $OUT"
    py-spy record -o "$OUT" --pid "$PID" --duration 30 --nonblocking
    echo "✅ 火焰图: $OUT"
    ;;

  # 帮助
  help|*)
    echo "梵天信号分析工具集 (jq-powered)"
    echo ""
    echo "用法: bash scripts/signal_jq_tools.sh [命令] [参数]"
    echo ""
    echo "  recent [N]     最近N条信号（默认10）"
    echo "  regime         体制分布统计"
    echo "  high [score]   高分信号（默认>130）"
    echo "  sym [SYMBOL]   指定标的历史"
    echo "  wr             WR胜率统计"
    echo "  skip           SKIP原因分布"
    echo "  watch          实时监控"
    echo "  size           日志磁盘占用"
    echo "  flame          py-spy火焰图（需进程运行中）"
    ;;
esac
