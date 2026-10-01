#!/usr/bin/env python3
"""
square_hot_poster_router.py — square_hot_poster 5合1路由器
[2.0封印 2026-10-01 苏摩111]

替代原5条独立cron：
  0 1  * * *  hot_tickers
  0 7  * * *  top_gainers
  0 9  * * *  pump_alert
  0 11 * * *  market_summary
  0 13 * * *  top_losers

改为单条：0 1,7,9,11,13 * * * python3 scripts/square_hot_poster_router.py

按当前UTC小时自动选type，节省4个cron行。
接入位置：brahma_crontab.txt
"""
import subprocess, sys, time
from pathlib import Path
from datetime import datetime, timezone

# UTC小时 → type映射（与原cron时间一致）
HOUR_TO_TYPE = {
    1:  ('hot_tickers',    ['--no-delay']),
    7:  ('top_gainers',    ['--no-delay']),
    9:  ('pump_alert',     []),
    11: ('market_summary', []),
    13: ('top_losers',     ['--no-delay']),
}

utc_hour = datetime.now(timezone.utc).hour
mapping = HOUR_TO_TYPE.get(utc_hour)

if mapping is None:
    print(f'[router] UTC {utc_hour}:00 无对应type，跳过')
    sys.exit(0)

post_type, extra_args = mapping
script = Path(__file__).parent / 'square' / 'square_hot_poster.py'

cmd = [sys.executable, str(script), '--type', post_type] + extra_args
print(f'[router] UTC {utc_hour}:00 → {post_type} {extra_args}')

result = subprocess.run(cmd, timeout=110)
sys.exit(result.returncode)
