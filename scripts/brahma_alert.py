"""
brahma_alert.py - 梵天关键路径异常告警
[矛盾2修复 2026-09-07 苏摩111] 消除静默失败

使用方法:
    from brahma_alert import alert_error, alert_warning
    try:
        result = critical_compute()
    except Exception as e:
        alert_error("wr_feedback", "compute_new_override失败", e)
"""
import logging, time, os, sys
from pathlib import Path

BASE = Path(__file__).parent.parent
LOG  = BASE / 'logs' / 'brahma_alert.log'

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('brahma_alert')

_dedup: dict = {}


def alert_error(module: str, context: str, exc: Exception = None, push: bool = True):
    """关键计算错误 → logging.error + Jarvis P1推送（1h去重）"""
    exc_part = ''
    if exc:
        exc_part = '\n  ' + type(exc).__name__ + ': ' + str(exc)[:100]
    msg = '[' + module + '] ERROR ' + context + exc_part
    logger.error(msg)
    try:
        os.makedirs(str(LOG.parent), exist_ok=True)
        with open(str(LOG), 'a') as f:
            f.write(time.strftime('%Y-%m-%dT%H:%M:%S') + ' ERROR ' + msg + '\n')
    except Exception:
        pass
    if not push:
        return
    dedup_key = module + ':' + context[:30]
    now = time.time()
    if now - _dedup.get(dedup_key, 0) < 3600:
        return
    _dedup[dedup_key] = now
    try:
        sys.path.insert(0, str(BASE))
        from push_hub import _jarvis
        exc_str = (type(exc).__name__ + ': ' + str(exc)[:80]) if exc else ''
        push_msg = 'ALERT ' + module + '\n' + context + '\n' + exc_str
        _jarvis(push_msg, dedup_key=dedup_key, dedup_ttl=3600)
    except Exception:
        pass


def alert_warning(module: str, context: str, push: bool = False):
    """可选功能失败 → logging.warning（默认不推送）"""
    msg = '[' + module + '] WARN ' + context
    logger.warning(msg)
    try:
        with open(str(LOG), 'a') as f:
            f.write(time.strftime('%Y-%m-%dT%H:%M:%S') + ' WARN  ' + msg + '\n')
    except Exception:
        pass
