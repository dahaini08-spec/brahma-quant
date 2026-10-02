"""
state_store.py — brahma_state 统一读取封装
[2026-10-02 苏摩111封印]

写入端唯一：brahma_state_refresh.py → _atomic_write()
读取端分散：各模块裸 json.loads(Path(...).read_text())
本模块提供统一读取接口，带缓存+降级，消除重复代码

用法:
    from state_store import get_state, get_regime, get_hurst
    bs = get_state('BTC')           # 返回完整state dict
    regime = get_regime('ETH')      # 'CHOP_MID'
    hurst  = get_hurst('BTC')       # 0.570
"""
import json, time
from pathlib import Path
from typing import Optional

_DATA = Path(__file__).parent.parent / 'data'
_CACHE: dict = {}          # {sym: (mtime, data)}
_CACHE_TTL  = 30           # 30s内直接复用，不重复 stat()

def get_state(sym: str) -> dict:
    """读取 brahma_state_<sym>.json，带30s内存缓存"""
    sym_l = sym.lower().replace('usdt', '')
    key   = sym_l
    now   = time.time()

    if key in _CACHE:
        mtime_cached, data = _CACHE[key]
        if now - mtime_cached < _CACHE_TTL:
            return data

    p = _DATA / f'brahma_state_{sym_l}.json'
    if not p.exists():
        return {}
    try:
        mtime = p.stat().st_mtime
        data  = json.loads(p.read_text())
        _CACHE[key] = (mtime, data)
        return data
    except Exception:
        return {}

def get_regime(sym: str) -> str:
    """返回当前体制字符串，默认 CHOP_MID"""
    bs = get_state(sym)
    return str(bs.get('regime') or bs.get('market_state_raw', {}).get('regime', 'CHOP_MID'))

def get_hurst(sym: str) -> float:
    """返回 Hurst 指数，优先 market_state_raw.hurst_4h"""
    bs = get_state(sym)
    ms = bs.get('market_state_raw', {})
    en = bs.get('extra', {}).get('ensemble', {}).get('raw_vec', {})
    return float(ms.get('hurst_4h') or en.get('hurst') or 0.0)

def get_liq(sym: str) -> tuple[float, float]:
    """返回 (nearest_short_liq, nearest_long_liq)"""
    sym_l = sym.lower().replace('usdt', '')
    p = _DATA / f'liq_heatmap_{sym_l}usdt.json'
    if not p.exists():
        return 0.0, 0.0
    try:
        d = json.loads(p.read_text())
        return float(d.get('nearest_short_liq') or 0), float(d.get('nearest_long_liq') or 0)
    except Exception:
        return 0.0, 0.0

def get_price(sym: str) -> float:
    """从 brahma_state 读缓存价格"""
    bs = get_state(sym)
    return float(bs.get('price') or bs.get('market_state_raw', {}).get('price', 0.0))

def invalidate(sym: Optional[str] = None) -> None:
    """手动失效缓存（state_refresh写完后调用）"""
    if sym:
        _CACHE.pop(sym.lower().replace('usdt', ''), None)
    else:
        _CACHE.clear()
