import os
#!/usr/bin/env python3
"""
notify_jarvis.py — 统一Jarvis推送入口
设计院 2026-06-04
[推送确认记录机制 2026-08-09 设计院封印]
  每次推送后自动写入 data/push_log.jsonl
  失败时写入 data/push_failures.jsonl
  晨报从 push_failures.jsonl 自动汇报昨日失败数
"""
import subprocess, sys, os, json, time
from pathlib import Path

try:
    import sys as _s; _s.path.insert(0, str(__import__('pathlib').Path(__file__).parent))
    from system_config import JARVIS_TARGET
except Exception:
    try:
        from scripts.system_config import JARVIS_TARGET as _ssot
        JARVIS_TARGET = os.environ.get('JARVIS_TARGET', _ssot)
    except Exception:
        JARVIS_TARGET = os.environ.get('JARVIS_TARGET', '73295708:thread:019fd9dd-4b0f-71db-87fb-1e192ccb2291')

_BASE = Path(__file__).parent.parent
_PUSH_LOG      = _BASE / 'data' / 'push_log.jsonl'
_PUSH_FAILURES = _BASE / 'data' / 'push_failures.jsonl'


def _record_push(msg: str, success: bool, caller: str = ''):
    """记录每次推送结果"""
    record = {
        'ts':      time.time(),
        'caller':  caller or 'unknown',
        'success': success,
        'msg_len': len(msg),
        'preview': msg[:60],
    }
    try:
        _PUSH_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(_PUSH_LOG, 'a') as f:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')
        if not success:
            with open(_PUSH_FAILURES, 'a') as f:
                f.write(json.dumps(record, ensure_ascii=False) + '\n')
    except Exception:
        pass


def get_push_stats(hours: int = 24) -> dict:
    """获取最近N小时推送统计（供晨报使用）"""
    cutoff = time.time() - hours * 3600
    total = success = failures = 0
    try:
        if _PUSH_LOG.exists():
            for line in _PUSH_LOG.read_text(errors='ignore').strip().split('\n'):
                if not line: continue
                try:
                    r = json.loads(line)
                    if float(r.get('ts', 0)) >= cutoff:
                        total += 1
                        if r.get('success'): success += 1
                        else: failures += 1
                except: pass
    except: pass
    return {'total': total, 'success': success, 'failures': failures,
            'rate': round(success/total*100, 1) if total > 0 else 100.0}


def send(msg: str, caller: str = '') -> bool:
    """通过openclaw message发送到Jarvis，并记录推送结果"""
    try:
        r = subprocess.run(
            ['openclaw', 'message', 'send',
             '--channel', 'jarvis',
             '--to', JARVIS_TARGET,
             '--message', msg],
            capture_output=True, text=True, timeout=15
        )
        success = r.returncode == 0
        _record_push(msg, success, caller)
        return success
    except Exception as e:
        _record_push(msg, False, caller)
        # fallback: print to stdout
        print(f'[notify_jarvis] {msg}')
        return False


if __name__ == '__main__':
    if len(sys.argv) > 1:
        msg = ' '.join(sys.argv[1:])
        send(msg, caller='cli')
        print('sent')
    elif '--stats' in sys.argv:
        stats = get_push_stats(24)
        print(f'推送统计(24H): 总{stats["total"]}条 成功{stats["success"]} 失败{stats["failures"]} 到达率{stats["rate"]}%')
