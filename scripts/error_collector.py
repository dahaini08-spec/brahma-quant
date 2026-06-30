#!/usr/bin/env python3
"""
error_collector.py — 梵天统一错误收集层 v1.0
设计院 2026-06-06

替代78个裸except，所有异常统一写入data/error_log.jsonl
用法：
  from error_collector import safe_call, log_error, ErrorCollector
  result = safe_call(my_func, args, context='brahma_brain')
"""
import json, traceback, time, os, sys
from pathlib import Path
from datetime import datetime, timezone
from functools import wraps

BASE     = Path(__file__).parent.parent
ERR_LOG  = BASE / 'data' / 'error_log.jsonl'
MAX_LINES = 2000  # 超出自动轮转

def log_error(module: str, error: Exception, context: str = '', extra: dict = None):
    """写入错误日志"""
    entry = {
        'ts':      datetime.now(timezone.utc).isoformat(),
        'module':  module,
        'error':   type(error).__name__,
        'msg':     str(error)[:300],
        'context': context[:200] if context else '',
        'tb':      traceback.format_exc()[-500:],
    }
    if extra:
        entry.update(extra)
    try:
        with open(ERR_LOG, 'a') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')
        # 轮转
        lines = ERR_LOG.read_text().splitlines()
        if len(lines) > MAX_LINES:
            ERR_LOG.write_text('\n'.join(lines[-MAX_LINES:]) + '\n')
    except:
        pass  # 错误收集器本身不能崩溃

def safe_call(func, *args, module='unknown', context='', default=None, **kwargs):
    """安全调用，异常自动收集"""
    try:
        return func(*args, **kwargs)
    except Exception as e:
        log_error(module, e, context)
        return default

def guarded(module_name: str, default=None):
    """装饰器：保护函数，异常静默+记录"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                log_error(module_name, e, context=func.__name__)
                return default
        return wrapper
    return decorator

def get_recent_errors(hours: float = 24, module: str = None) -> list:
    """读取最近N小时的错误"""
    if not ERR_LOG.exists():
        return []
    cutoff = time.time() - hours * 3600
    errors = []
    for line in open(ERR_LOG):
        if not line.strip(): continue
        try:
            e = json.loads(line)
            ts_str = e.get('ts', '')
            from datetime import datetime as dt
            ts = dt.fromisoformat(ts_str.replace('Z','+00:00')).timestamp()
            if ts < cutoff: continue
            if module and e.get('module') != module: continue
            errors.append(e)
        except:
            pass
    return errors

def error_summary(hours: float = 6) -> str:
    """6小时错误摘要，用于L2诊断"""
    errors = get_recent_errors(hours)
    if not errors:
        return f'✅ {hours}H内无错误'
    from collections import Counter
    mods = Counter(e.get('module','?') for e in errors)
    top = ', '.join(f'{m}×{c}' for m,c in mods.most_common(5))
    return f'⚠️ {hours}H内{len(errors)}个错误: {top}'

if __name__ == '__main__':
    print(error_summary(24))
