#!/bin/bash
# guardian_wrapper.sh — position+RSI守护，无异常静默，有异常直接推送
# 不经过AI session，避免event loop阻塞
cd /root/.openclaw/workspace/trading-system

SL_OUT=$(timeout 20 python3 scripts/position_sl_monitor.py 2>&1 | tail -5)
RSI_OUT=$(timeout 20 python3 scripts/rsi_structure_watcher.py 2>&1 | tail -3)

# 检查是否有异常
HAS_ALERT=0
echo "$SL_OUT" | grep -qiE "止损|SL触发|预警|ALERT|warning" && HAS_ALERT=1
echo "$RSI_OUT" | grep -qiE "破坏|预警|ALERT|warning|RSI" && HAS_ALERT=1

if [ "$HAS_ALERT" -eq 1 ]; then
    MSG="⚠️ Guardian预警\n$(date '+%H:%M UTC')\n$SL_OUT\n$RSI_OUT"
    python3 -c "
import sys
sys.path.insert(0,'brahma_brain')
sys.path.insert(0,'scripts')
try:
    from push_hub import _jarvis
    from system_config import JARVIS_USER_ID, JARVIS_THREAD_ID
    _jarvis(f'{JARVIS_USER_ID}:thread:{JARVIS_THREAD_ID}', '''$MSG''')
except Exception as e:
    print(f'push失败: {e}')
" 2>/dev/null
fi
