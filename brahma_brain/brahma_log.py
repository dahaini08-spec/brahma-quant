"""
brahma_log.py — 日志函数shim（兼容层）
设计院 2026-08-10 | 精简后恢复
提供 berr() 函数，委托给 brainlog.berror
"""
try:
    from brahma_brain.brainlog import berror as _berror
    def berr(module: str, msg: str, exc=None):
        """短名兼容 berror"""
        _berror(module, msg, exc=exc)
except ImportError:
    import logging
    _log = logging.getLogger('brahma_log')
    def berr(module: str, msg: str, exc=None):
        _log.warning(f'[{module}] {msg}')

__all__ = ['berr']
