#!/usr/bin/env python3
"""
cron_drawdown_touch.py — 每小时更新drawdown/circuit_breaker时间戳
[2.0封印 2026-10-01 苏摩111]

替代原crontab inline python3 -c代码块，使用safe_io原子写防并发损坏。
接入位置：brahma_crontab.txt 03 * * * *
"""
import sys, time
from pathlib import Path

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE / 'brahma_brain'))
sys.path.insert(0, str(BASE))

try:
    from safe_io import locked_json_update
except ImportError:
    import json, os, tempfile
    def locked_json_update(path, fn, default=None):
        p = Path(path)
        d = json.loads(p.read_text()) if p.exists() else default
        data = fn(d)
        fd, tmp = tempfile.mkstemp(dir=p.parent)
        os.close(fd)
        with open(tmp, 'w') as f:
            import json as _j; _j.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)

# Step1: 更新drawdown跟踪器
try:
    from brahma_brain.drawdown_tracker import update_drawdown as _udd
except ImportError:
    try:
        from drawdown_tracker import update_drawdown as _udd
    except ImportError:
        _udd = None

if _udd:
    try:
        _udd()
        print(f'[cron_drawdown_touch] drawdown updated')
    except Exception as e:
        print(f'[cron_drawdown_touch] drawdown_tracker WARN: {e}')
else:
    print(f'[cron_drawdown_touch] drawdown_tracker not found, skip')

# Step2: 原子更新 circuit_breaker.json 时间戳
try:
    cb_path = BASE / 'data' / 'circuit_breaker.json'
    if cb_path.exists():
        locked_json_update(cb_path, lambda d: {**d, 'ts': time.time(), 'last_updated': time.time()})
        print(f'[cron_drawdown_touch] circuit_breaker.json ts updated')
except Exception as e:
    print(f'[cron_drawdown_touch] circuit_breaker WARN: {e}')

# Step3: 原子更新 drawdown_state.json 时间戳
try:
    dd_path = BASE / 'data' / 'drawdown_state.json'
    if dd_path.exists():
        locked_json_update(dd_path, lambda d: {**d, 'ts': time.time(), 'last_updated': time.time()})
        print(f'[cron_drawdown_touch] drawdown_state.json ts updated')
except Exception as e:
    print(f'[cron_drawdown_touch] drawdown_state WARN: {e}')

print(f'[cron_drawdown_touch] done at {time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}')
