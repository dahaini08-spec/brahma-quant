#!/bin/bash
# 清理2H前的completed isolated sessions（含trajectory文件）
SESSIONS="/root/.openclaw/agents/main/sessions"
KEEP_TOPIC="019f309c"
KEEP_TOPIC2="019ed32f"
COUNT=0

# 清理2H前的所有session文件（.jsonl / .trajectory-path.json / .trajectory.jsonl）
find "$SESSIONS" -maxdepth 1 -type f \
  ! -name "sessions.json*" \
  ! -name "*${KEEP_TOPIC}*" \
  ! -name "*${KEEP_TOPIC2}*" \
  -mmin +120 \
  -delete 2>/dev/null
COUNT=$(find "$SESSIONS" -maxdepth 1 -type f ! -name "sessions.json*" | wc -l)
echo "session_cleanup: remaining=$COUNT"
