#!/usr/bin/env python3
"""

# ── STATUS: AUXILIARY ──────────────────────────────────────────
# CoinGlass降级备用
# LAST_REVIEW: 2026-07-01 | 属于辅助计算层，修改前确认调用链
# ─────────────────────────────────────────────────────────────
coinglass_fallback.py — CoinGlass 降级数据源链
设计院 2026-05-30 · 全局落地

降级链：
  L1: CoinGlass v4 API（主）
  L2: Binance /futures/data/globalLongShortAccountRatio（F&G替代）
  L3: /fapi/v1/fundingRate（资金费率）
  L4: 缓存（最长12H）
"""
import json, time, urllib.request
from pathlib import Path

CACHE_FILE = Path('/tmp/coinglass_fallback_cache.json')
CACHE_TTL  = 12 * 3600  # 12小时缓存

def _fetch(url, timeout=6):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None

def _load_cache():
    try:
        if CACHE_FILE.exists():
            d = json.loads(CACHE_FILE.read_text())
            if time.time() - d.get('_ts', 0) < CACHE_TTL:
                return d
    except Exception:
        pass
    return None

def _save_cache(data: dict):
    try:
        data['_ts'] = time.time()
        tmp = str(CACHE_FILE) + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(data, f)
        Path(tmp).rename(CACHE_FILE)
    except Exception:
        pass

def get_fear_greed() -> dict:
    """获取恐惧贪婪指数，多级降级"""
    # L1: CoinGlass
    try:
        from coinglass_engine import get_full_snapshot
        snap = get_full_snapshot('BTCUSDT')
        if snap and snap.get('available'):
            fg = snap.get('fear_greed', {})
            if fg.get('value'):
                result = {'value': fg['value'], 'label': fg.get('label',''), 'source': 'coinglass_l1'}
                _save_cache({'fear_greed': result})
                return result
    except Exception:
        pass

    # L2: alternative.me F&G
    try:
        d = _fetch('https://api.alternative.me/fng/?limit=1', timeout=5)
        if d and d.get('data'):
            val = int(d['data'][0]['value'])
            label_map = {(0,20):'EXTREME_FEAR',(20,40):'FEAR',(40,60):'NEUTRAL',(60,80):'GREED',(80,101):'EXTREME_GREED'}
            label = next((v for (lo,hi),v in label_map.items() if lo<=val<hi), 'NEUTRAL')
            result = {'value': val, 'label': label, 'source': 'alternative_me_l2'}
            _save_cache({'fear_greed': result})
            return result
    except Exception:
        pass

    # L3: 缓存
    cache = _load_cache()
    if cache and 'fear_greed' in cache:
        r = dict(cache['fear_greed'])
        r['source'] = 'cache_l3'
        return r

    # L4: 默认中性
    return {'value': 50, 'label': 'NEUTRAL', 'source': 'default_l4'}


def get_funding_rate(symbol: str = 'BTCUSDT') -> float:
    """获取资金费率，多级降级"""
    # L1: CoinGlass
    try:
        from coinglass_engine import get_full_snapshot
        snap = get_full_snapshot(symbol)
        if snap and snap.get('available'):
            return snap.get('funding_rate', 0.0)
    except Exception:
        pass

    # L2: Binance fapi
    try:
        sym = symbol.upper()
        d = _fetch(f'https://fapi.binance.com/fapi/v1/fundingRate?symbol={sym}&limit=1')
        if d and isinstance(d, list) and d:
            return float(d[0].get('fundingRate', 0))
    except Exception:
        pass

    return 0.0


def get_oi_change(symbol: str = 'BTCUSDT') -> float:
    """获取OI变化率（4H），降级返回0"""
    # L1: CoinGlass
    try:
        from coinglass_engine import get_full_snapshot
        snap = get_full_snapshot(symbol)
        if snap and snap.get('available'):
            oi = snap.get('oi_momentum', {})
            return float(oi.get('oi_change_pct', 0))
    except Exception:
        pass

    # L2: Binance openInterest（只有当前值，无法算变化率，返回0）
    return 0.0


def get_full_snapshot_with_fallback(symbol: str) -> dict:
    """完整快照，带全链路降级"""
    fg   = get_fear_greed()
    fr   = get_funding_rate(symbol)
    oi   = get_oi_change(symbol)

    return {
        'available':    True,
        'fear_greed':   fg,
        'funding_rate': fr,
        'oi_momentum':  {'oi_change_pct': oi},
        'onchain_score': 1 if fg['value'] < 30 else (-1 if fg['value'] > 70 else 0),
        'liquidation':  {'bias': 'NEUTRAL', 'available': False},
        '_fallback':    True,
        '_sources':     [fg.get('source',''), f'binance_fr', f'oi_{oi:.2f}%'],
    }
