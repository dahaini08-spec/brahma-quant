#!/usr/bin/env python3
"""
brahma_macro.py — 统一宏观引擎 v1.0
设计院 2026-08-24 重建 | 替换2个旧模块:
  macro_engine.py     (418行) → macro_score / macro_score_v2 / write_macro_state
  fib_macro_engine.py (352行) → fib_macro_score

共享: HTTP层 / BrahmaBus价格 / 数据获取
向后兼容: 所有函数签名不变
"""
from __future__ import annotations
import json, time, urllib.request, logging
from pathlib import Path
from typing import Optional
from data_cache import _SSL_CTX as _DC_SSL_CTX

logger = logging.getLogger('brahma_macro')
BASE = Path(__file__).parent.parent
DATA = BASE / 'data'

# ── 共享HTTP层 ────────────────────────────────────────────────────────────────
def _get(url: str, timeout: int = 8, headers: dict | None = None) -> dict | list | None:
    try:
        req = urllib.request.Request(url, headers=headers or {'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=timeout, context=_DC_SSL_CTX) as r:
            return json.loads(r.read())
    except Exception:
        return None

def _bus_price(symbol: str) -> float:
    """从BrahmaBus获取当前价格"""
    try:
        import sys, os
        sys.path.insert(0, str(BASE / 'brahma_brain'))
        from brahma_bus import get_price
        return get_price(symbol) or 0.0
    except Exception:
        return 0.0

# ── 宏观数据获取（共享，原两个引擎各自定义现在合并）────────────────────────────
def get_fear_greed() -> dict:
    data = _get('https://api.alternative.me/fng/?limit=1', timeout=6)
    if data and isinstance(data.get('data'), list) and data['data']:
        d = data['data'][0]
        return {'value': int(d.get('value', 50)),
                'classification': d.get('value_classification', 'Neutral')}
    return {'value': 50, 'classification': 'Neutral'}

def get_btc_dominance() -> dict:
    data = _get('https://api.coingecko.com/api/v3/global', timeout=8)
    if data:
        btc_d = data.get('data', {}).get('market_cap_percentage', {}).get('btc', 0)
        return {'btc_dominance': round(btc_d, 2)}
    return {'btc_dominance': 0}

def get_dxy_realtime() -> dict:
    """委托macro_engine原始实现"""
    try:
        from brahma_brain.narrative_engine import get_dxy_realtime as _f
        return _f()
    except Exception:
        return {'value': 0, 'source': 'fallback'}

def get_nasdaq_realtime() -> dict:
    try:
        from brahma_brain.narrative_engine import get_nasdaq_realtime as _f
        return _f()
    except Exception:
        return {'value': 0, 'source': 'fallback'}

# ── macro_score (原macro_engine.py主函数) ─────────────────────────────────────
def macro_score(symbol: str, signal_dir: str, fg_data: dict | None = None) -> dict:
    """宏观评分 — 委托macro_engine保持完整逻辑 [向后兼容]"""
    try:
        from brahma_brain.narrative_engine import macro_score as _f
        return _f(symbol, signal_dir, fg_data)
    except Exception as e:
        logger.warning(f'macro_score降级: {e}')
        return {'score': 0, 'breakdown': {}, 'source': 'fallback'}

def macro_score_v2(symbol: str, signal_dir: str) -> dict:
    """宏观评分v2 — 含DXY+NQ实时数据"""
    try:
        from brahma_brain.narrative_engine import macro_score_v2 as _f
        return _f(symbol, signal_dir)
    except Exception as e:
        logger.warning(f'macro_score_v2降级: {e}')
        return {'score': 0, 'breakdown': {}, 'source': 'fallback'}

def write_macro_state() -> dict:
    """写入宏观状态到data/macro_state.json"""
    try:
        from brahma_brain.narrative_engine import write_macro_state as _f
        return _f()
    except Exception as e:
        logger.warning(f'write_macro_state降级: {e}')
        return {}

# ── fib_macro_score (原fib_macro_engine.py主函数) ─────────────────────────────
def _rsi14(closes: list) -> float:
    if len(closes) < 15:
        return 50.0
    g = [max(closes[i]-closes[i-1], 0) for i in range(1, len(closes))]
    lo = [max(closes[i-1]-closes[i], 0) for i in range(1, len(closes))]
    ag = sum(g[:14])/14; al = sum(lo[:14])/14
    for i in range(14, len(g)):
        ag = (ag*13+g[i])/14; al = (al*13+lo[i])/14
    return 100 - 100/(1 + ag/max(al,1e-9))

def _compute_fib_levels(high: float, low: float) -> dict:
    span = high - low
    return {
        '0.236': low + span*0.236,
        '0.382': low + span*0.382,
        '0.500': low + span*0.500,
        '0.618': low + span*0.618,
        '0.786': low + span*0.786,
    }

def fib_macro_score(symbol: str, price: float, signal_dir: str,
                    regime: str = '', klines_4h: list | None = None) -> dict:
    """
    Fibonacci宏观评分 — 含Fib回调位+RSI+体制评估
    委托fib_macro_engine原始实现（保持复杂逻辑不变）
    """
    try:
        from fib_macro_engine import fib_macro_score as _f
        return _f(symbol, price, signal_dir, regime, klines_4h)
    except Exception as e:
        logger.debug(f'fib_macro_score降级: {e}')
        return {'score': 0, 'fib_level': None, 'breakdown': {}, 'source': 'fallback'}

# ── 统一宏观批量查询（新接口，减少重复调用）──────────────────────────────────
def get_macro_bundle(symbol: str, signal_dir: str, price: float = 0) -> dict:
    """
    一次调用获取所有宏观数据（FG+BTC.D+DXY+macro_score）
    减少调用方4次独立调用 → 1次并行调用
    """
    from concurrent.futures import ThreadPoolExecutor
    results = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        f_fg   = pool.submit(get_fear_greed)
        f_dom  = pool.submit(get_btc_dominance)
        f_dxy  = pool.submit(get_dxy_realtime)
        f_ms   = pool.submit(macro_score_v2, symbol, signal_dir)
        try: results['fear_greed'] = f_fg.result(timeout=8)
        except: results['fear_greed'] = {'value': 50}
        try: results['btc_dominance'] = f_dom.result(timeout=8)
        except: results['btc_dominance'] = {'btc_dominance': 0}
        try: results['dxy'] = f_dxy.result(timeout=8)
        except: results['dxy'] = {'value': 0}
        try: results['macro_score'] = f_ms.result(timeout=10)
        except: results['macro_score'] = {'score': 0}
    results['ts'] = time.time()
    return results
