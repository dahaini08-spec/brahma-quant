"""
brahma_brain/disk_cache.py — 统一磁盘缓存层
设计院封印 2026-09-03 苏摩111

职责：
  跨进程持久化缓存，解决supercronic每次spawn新进程=冷启动问题
  所有模块通过 disk_get/disk_set 共享缓存，TTL按数据类型配置

默认TTL：
  klines (K线)     → 300s  (5分钟，行情变化快)
  fangcang         → 3600s (60分钟，历史数据稳定)
  gex              → 300s  (5分钟，期权数据)
  vol_beta         → 3600s (60分钟，IV每4h刷新)
  har_rv           → 600s  (10分钟)

接入位置：
  brahma_brain/fangcang_engine.py  _load_klines()
  brahma_brain/gex_unified.py      get_gex_state()
  brahma_brain/vol_beta_engine.py  calc_vol_beta()
  brahma_brain/har_rv_engine.py    get_har_rv_score()
"""

import os
import time
import pickle
import hashlib
import logging
from pathlib import Path

_logger = logging.getLogger('disk_cache')

# 缓存目录
_CACHE_DIR = Path(__file__).parent.parent / 'data' / 'cache'
_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# 各类型默认TTL（秒）
TTL_KLINES   = 300    # K线：5分钟
TTL_FANGCANG = 3600   # 方仓历史：60分钟
TTL_GEX      = 300    # GEX期权：5分钟
TTL_VOL_BETA = 3600   # Vol-Beta：60分钟（每4h由supercronic刷新）
TTL_HAR_RV   = 600    # HAR-RV：10分钟
TTL_DEFAULT  = 300    # 默认：5分钟


def _cache_path(key: str) -> Path:
    """把key映射到缓存文件路径（8位md5避免文件名冲突）"""
    h = hashlib.md5(key.encode()).hexdigest()[:12]
    # 保留可读前缀方便调试
    safe_prefix = key.replace('/', '_').replace(':', '_')[:20]
    return _CACHE_DIR / f"{safe_prefix}_{h}.pkl"


def disk_get(key: str, ttl: int = TTL_DEFAULT):
    """
    读取缓存。未命中或过期返回None。
    key格式建议：'module:symbol:tf'，如 'fangcang:BTCUSDT:4h'
    """
    f = _cache_path(key)
    try:
        if not f.exists():
            return None
        age = time.time() - f.stat().st_mtime
        if age > ttl:
            return None
        return pickle.loads(f.read_bytes())
    except Exception as _e:
        _logger.debug(f'disk_get failed key={key}: {_e}')
        return None


def disk_set(key: str, value) -> bool:
    """
    写入缓存。失败静默返回False（不影响主流程）。
    """
    f = _cache_path(key)
    try:
        f.write_bytes(pickle.dumps(value, protocol=4))
        return True
    except Exception as _e:
        _logger.debug(f'disk_set failed key={key}: {_e}')
        return False


def disk_clear(prefix: str = ''):
    """清理缓存文件（可按前缀过滤）"""
    cleared = 0
    for f in _CACHE_DIR.glob('*.pkl'):
        if not prefix or prefix in f.name:
            try:
                f.unlink()
                cleared += 1
            except Exception:
                pass
    return cleared


def disk_stats() -> dict:
    """返回缓存目录统计"""
    files = list(_CACHE_DIR.glob('*.pkl'))
    total_bytes = sum(f.stat().st_size for f in files if f.exists())
    now = time.time()
    expired = sum(1 for f in files if f.exists() and now - f.stat().st_mtime > TTL_DEFAULT)
    return {
        'files':       len(files),
        'total_mb':    round(total_bytes / 1024 / 1024, 2),
        'expired':     expired,
        'cache_dir':   str(_CACHE_DIR),
    }
