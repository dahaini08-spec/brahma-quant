#!/bin/bash
# 清理2H前的completed isolated sessions
SESSIONS="/root/.openclaw/agents/main/sessions"
KEEP_TOPIC="019f1797"
CUTOFF=$(python3 -c "import time; print(int(time.time()-2*3600))")
COUNT=0
for f in $SESSIONS/*.jsonl $SESSIONS/*.json; do
    [ -f "$f" ] || continue
    fname=$(basename "$f")
    [[ "$fname" == *"$KEEP_TOPIC"* ]] && continue
    [[ "$fname" == *"trajectory-path"* ]] && continue
    mtime=$(stat -c %Y "$f" 2>/dev/null || echo 9999999999)
    if [ "$mtime" -lt "$CUTOFF" ]; then
        rm -f "$f"
        COUNT=$((COUNT+1))
    fi
done
echo "cleaned $COUNT sessions"
