"""
brahma_logger.py — 梵天结构化日志系统 v1.0
设计院 2026-07-08 | P1-1 外部审计改善方案

替换分散的 print() 为统一 JSONL 格式日志
支持: 文件输出 + stdout + 未来Loki/ELK接入
"""
import json, time, logging, sys, os
from pathlib import Path
from datetime import datetime, timezone

# 日志文件路径
_LOG_DIR = Path(__file__).parent.parent / 'data'
_STRUCTURED_LOG = _LOG_DIR / 'brahma_structured.jsonl'
_MAX_FILE_MB = 50  # 超过50MB自动轮转

# 级别映射
LEVEL_DEBUG = 'DEBUG'
LEVEL_INFO  = 'INFO'
LEVEL_WARN  = 'WARN'
LEVEL_ERROR = 'ERROR'
LEVEL_SIGNAL = 'SIGNAL'
LEVEL_EXEC  = 'EXEC'
LEVEL_REGIME = 'REGIME'

# 模块颜色（终端输出用）
_LEVEL_EMOJI = {
    'DEBUG':  '🔍', 'INFO': 'ℹ️', 'WARN': '⚠️',
    'ERROR':  '🚨', 'SIGNAL': '📡', 'EXEC': '⚡',
    'REGIME': '🏛️',
}

def log(module: str, event: str, level: str = LEVEL_INFO,
        symbol: str = '', score: float = 0, metrics: dict = None, **kwargs):
    """
    统一结构化日志入口
    
    用法:
        from brahma_brain.brahma_logger import log, LEVEL_SIGNAL
        log('BrahmaBrain', 'signal_evaluated', LEVEL_SIGNAL, symbol='ETHUSDT', score=144.1)
    """
    now_ts = time.time()
    now_iso = datetime.fromtimestamp(now_ts, tz=timezone.utc).isoformat()
    
    entry = {
        'ts': now_ts,
        'iso': now_iso,
        'level': level,
        'module': module,
        'event': event,
        'symbol': symbol,
        'score': round(score, 2) if score else 0,
    }
    if metrics:
        entry['metrics'] = metrics
    if kwargs:
        entry.update(kwargs)
    
    # 写入文件
    try:
        # 轮转检查
        if _STRUCTURED_LOG.exists() and _STRUCTURED_LOG.stat().st_size > _MAX_FILE_MB * 1024 * 1024:
            bak = _STRUCTURED_LOG.with_suffix(f'.jsonl.bak_{int(now_ts)}')
            _STRUCTURED_LOG.rename(bak)
        
        with open(_STRUCTURED_LOG, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')
    except Exception:
        pass  # 日志写失败不影响主流程
    
    # stdout 输出（保留人类可读格式）
    emoji = _LEVEL_EMOJI.get(level, '•')
    sym_str = f' [{symbol}]' if symbol else ''
    score_str = f' score={score:.0f}' if score else ''
    extra = ' '.join(f'{k}={v}' for k,v in kwargs.items() if k not in ('regime','direction'))
    print(f'{emoji}[{module}]{sym_str}{score_str} {event} {extra}'.strip())

def log_signal(symbol: str, score: float, valid: bool, regime: str,
               direction: str, action: str, timing: str = '', **kwargs):
    """信号专用日志"""
    log('SignalEngine', 'signal_evaluated', LEVEL_SIGNAL,
        symbol=symbol, score=score,
        metrics={
            'valid': valid, 'regime': regime,
            'direction': direction, 'action': action,
            'timing': timing,
        }, **kwargs)

def log_exec(symbol: str, result: str, reason: str, notional: float = 0, **kwargs):
    """执行专用日志"""
    log('Executor', 'order_execution', LEVEL_EXEC,
        symbol=symbol,
        metrics={'result': result, 'reason': reason[:100], 'notional': notional},
        **kwargs)

def log_regime(symbol: str, from_regime: str, to_regime: str, **kwargs):
    """体制切换日志"""
    log('RegimeMachine', 'regime_switch', LEVEL_REGIME,
        symbol=symbol,
        metrics={'from': from_regime, 'to': to_regime},
        **kwargs)

# 兼容旧 print 调用 — 可选开启
class BrahmaPrint:
    """替换 print() 的兼容层，将输出同时写入结构化日志"""
    def __init__(self, module: str):
        self.module = module
        self._orig = print
    
    def __call__(self, *args, **kwargs):
        msg = ' '.join(str(a) for a in args)
        self._orig(*args, **kwargs)  # 保持原始输出
        try:
            log(self.module, msg[:200], LEVEL_INFO)
        except Exception:
            pass

if __name__ == '__main__':
    # 测试
    log('Test', 'startup', LEVEL_INFO, metrics={'version': 'v6.0'})
    log_signal('ETHUSDT', 165.0, True, 'BEAR_RECOVERY', 'LONG', 'ENTER', 'MONITOR')
    log_exec('ETHUSDT', 'FILLED', 'OK', notional=36.4)
    log_regime('ETHUSDT', 'BULL_TREND', 'BEAR_RECOVERY')
    print(f"✅ 结构化日志写入: {_STRUCTURED_LOG}")
