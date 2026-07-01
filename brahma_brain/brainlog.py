"""
brainlog.py — 梵天统一日志格式封装
设计院·增效减负 2026-07-01

╔══════════════════════════════════════════════════════════════╗
║  目标：所有模块统一用 [MODULE:LEVEL] 前缀                   ║
║  当前：511处 print() 混用无法过滤                           ║
║  方案：轻量封装，零侵入，不改现有代码                       ║
╠══════════════════════════════════════════════════════════════╣
║  用法：                                                      ║
║    from brainlog import get_logger                           ║
║    log = get_logger('brahma_core')                           ║
║    log.info('score=166 valid=True')                          ║
║    log.warn('p_up=0.95 极值模式')                            ║
║    log.error('API连接失败', exc=e)                           ║
╠══════════════════════════════════════════════════════════════╣
║  过滤：                                                      ║
║    set_level('WARN')  # 只看警告+错误                        ║
║    set_level('ERROR') # 只看错误                             ║
║    set_module_filter('brahma_core','s7')  # 只看指定模块     ║
╚══════════════════════════════════════════════════════════════╝

# INTERFACE CONTRACT
# Interface : get_logger(name)->BrainLogger | set_level(level) | set_module_filter(*names)
# Output    : 格式化print到stdout，[MODULE:LEVEL timestamp] message
# Call Freq : 模块初始化时调用一次，后续复用实例
# Deps      : 无外部依赖，纯stdlib
"""

import time
import threading
import sys
import os
from typing import Optional

# ── 全局配置 ──────────────────────────────────────────────────
_LEVELS = {'DEBUG': 0, 'INFO': 1, 'WARN': 2, 'ERROR': 3, 'SILENT': 99}
_current_level = _LEVELS['INFO']
_module_filter: Optional[set] = None   # None = 全部允许
_lock = threading.Lock()

# 是否在生产环境（减少时间戳开销）
_PROD = os.environ.get('BRAHMA_ENV', 'prod') == 'prod'


def set_level(level: str):
    """全局日志级别设置"""
    global _current_level
    _current_level = _LEVELS.get(level.upper(), _LEVELS['INFO'])


def set_module_filter(*module_names: str):
    """只输出指定模块的日志，None=全部"""
    global _module_filter
    _module_filter = set(module_names) if module_names else None


def _should_log(module: str, level_int: int) -> bool:
    if level_int < _current_level:
        return False
    if _module_filter is not None:
        return any(f in module for f in _module_filter)
    return True


def _format(module: str, level: str, msg: str, tag: str = '') -> str:
    """
    格式：[MODULE:LEVEL] message
    生产模式省略时间戳（减少IO开销）
    """
    tag_str = f':{tag}' if tag else ''
    if _PROD:
        return f'[{module.upper()}{tag_str}:{level}] {msg}'
    else:
        ts = time.strftime('%H:%M:%S')
        return f'[{module.upper()}{tag_str}:{level} {ts}] {msg}'


class BrainLogger:
    """
    轻量日志器，每个模块一个实例

    特性：
    - 线程安全（锁保护）
    - 异常自动格式化
    - 支持tag子分类（如 log.info('...', tag='s7')）
    - SILENT级别完全静默（cron脚本用）
    """

    __slots__ = ('_name',)

    def __init__(self, name: str):
        self._name = name

    def debug(self, msg: str, tag: str = '', exc: Exception = None):
        self._emit('DEBUG', 0, msg, tag, exc)

    def info(self, msg: str, tag: str = '', exc: Exception = None):
        self._emit('INFO', 1, msg, tag, exc)

    def warn(self, msg: str, tag: str = '', exc: Exception = None):
        self._emit('WARN', 2, msg, tag, exc)

    def error(self, msg: str, tag: str = '', exc: Exception = None):
        self._emit('ERROR', 3, msg, tag, exc)

    def _emit(self, level: str, level_int: int, msg: str, tag: str, exc: Optional[Exception]):
        if not _should_log(self._name, level_int):
            return
        line = _format(self._name, level, msg, tag)
        if exc is not None:
            line += f' | {type(exc).__name__}: {str(exc)[:80]}'
        with _lock:
            print(line, flush=True)

    # ── 兼容旧式 print(f'[TAG] ...') 模式 ──
    def raw(self, msg: str):
        """直接输出，不加前缀（兼容现有 print(f'[s7-xxx]...') ）"""
        if _should_log(self._name, _LEVELS['INFO']):
            with _lock:
                print(msg, flush=True)


# ── 模块注册表（单例缓存）──────────────────────────────────────
_loggers: dict = {}


def get_logger(name: str) -> BrainLogger:
    """获取（或创建）模块日志器"""
    if name not in _loggers:
        _loggers[name] = BrainLogger(name)
    return _loggers[name]


# ── 快捷函数（零迁移成本）──────────────────────────────────────
def binfo(module: str, msg: str, tag: str = ''):
    """零迁移成本快捷函数，直接替换 print(f'[{tag}] {msg}')"""
    get_logger(module).info(msg, tag=tag)


def bwarn(module: str, msg: str, tag: str = ''):
    get_logger(module).warn(msg, tag=tag)


def berror(module: str, msg: str, tag: str = '', exc: Exception = None):
    get_logger(module).error(msg, tag=tag, exc=exc)


# ── 统计接口（供 brahma_health 调用）──────────────────────────
_error_counts: dict = {}   # {module: count}
_warn_counts: dict = {}

_original_emit = BrainLogger._emit

def _patched_emit(self, level, level_int, msg, tag, exc):
    if level == 'ERROR':
        _error_counts[self._name] = _error_counts.get(self._name, 0) + 1
    elif level == 'WARN':
        _warn_counts[self._name] = _warn_counts.get(self._name, 0) + 1
    _original_emit(self, level, level_int, msg, tag, exc)

BrainLogger._emit = _patched_emit


def get_stats() -> dict:
    """供 brahma_health 调用，返回错误/警告计数"""
    return {
        'error_counts': dict(_error_counts),
        'warn_counts':  dict(_warn_counts),
        'total_errors': sum(_error_counts.values()),
        'total_warns':  sum(_warn_counts.values()),
        'hot_modules':  sorted(_error_counts, key=_error_counts.get, reverse=True)[:5],
    }


def reset_stats():
    """重置计数（健康检查后调用）"""
    _error_counts.clear()
    _warn_counts.clear()


if __name__ == '__main__':
    # 快速验证
    log = get_logger('demo')
    log.info('系统启动', tag='init')
    log.warn('p_up=0.95 极值模式', tag='s23')
    log.error('API超时', tag='api', exc=TimeoutError('fapi timeout'))

    set_level('WARN')
    log.info('这条不会显示（级别过滤）')
    log.warn('这条会显示')

    print()
    print('stats:', get_stats())
