"""
brahma_alert.py — 梵天关键路径异常告警
[矛盾2修复 2026-09-07 苏摩111] 消除静默失败

使用方法:
    from brahma_alert import alert_error, alert_warning

    try:
        result = critical_compute()
    except Exception as e:
        alert_error("wr_feedback", "compute_new_override失败", e)
        raise  # 或 return None，取决于是否可降级
"""
import logging, time, traceback, os, sys
from pathlib import Path

BASE = Path(__file__).parent.parent
LOG  = BASE / 'logs' / 'brahma_alert.log'

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('brahma_alert')

# 去重：同一错误1小时内只推送一次
_dedup: dict = {}

def alert_error(module: str, context: str, exc: Exception = None, push: bool = True):
    """关键计算错误 → logging.error + Jarvis P1推送（1h去重）"""
    msg = f'[{module}] ❌ {context}'
    if exc:
        msg += f'\n  {type(exc).__name__}: {str(exc)[:100]}'

    logger.error(msg)
    try:
        with open(LOG, 'a') as f:
            f.write(f'{time.strftime("%Y-%m-%dT%H:%M:%S")} ERROR {msg}\n')
    except Exception:
        pass

    if not push:
        return

    # 去重检查
    dedup_key = f'{module}:{context[:30]}'
    now = time.time()
    if now - _dedup.get(dedup_key, 0) < 3600:
        return
    _dedup[dedup_key] = now

    try:
        sys.path.insert(0, str(BASE / 'scripts'))
        from push_hub import _jarvis
        push_msg = f'⚠️ 梵天告警\n模块: {module}\n错误: {context}\n{f\"异常: {type(exc).__name__}: {str(exc)[:80]}\" if exc else \"\"}'
        _jarvis(push_msg, dedup_key=dedup_key, dedup_ttl=3600)
    except Exception:
        pass  # 告警本身失败不崩溃


def alert_warning(module: str, context: str, push: bool = False):
    """可选功能失败 → logging.warning（默认不推送）"""
    msg = f'[{module}] ⚠️ {context}'
    logger.warning(msg)
    try:
        with open(LOG, 'a') as f:
            f.write(f'{time.strftime("%Y-%m-%dT%H:%M:%S")} WARN  {msg}\n')
    except Exception:
        pass
