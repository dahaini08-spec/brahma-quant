#!/usr/bin/env python3
"""
brahma_dashboard_push.py — 梵天仪表盘线程推送
直接调用 build_push_message() 并推送到 Jarvis 线程
"""
import sys, os, subprocess
from pathlib import Path

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))

from scripts.brahma_dashboard import build_push_message

try:
    from scripts.system_config import JARVIS_USER_ID, JARVIS_THREAD_ID, JARVIS_CHANNEL
    target  = f"{JARVIS_USER_ID}:thread:{JARVIS_THREAD_ID}"
    channel = JARVIS_CHANNEL
except Exception:
    target  = '73295708:thread:019f8768-6731-777d-8924-2426a5abd10f'
    channel = 'jarvis'

msg = build_push_message()

result = subprocess.run(
    ['openclaw', 'message', 'send', '--channel', channel, '--target', target, '--message', msg],
    capture_output=True, text=True, timeout=20
)
if result.returncode == 0:
    print('[dashboard-push] ✅ 推送成功')
else:
    print(f'[dashboard-push] ❌ 推送失败: {result.stderr[:200]}')
    sys.exit(1)
