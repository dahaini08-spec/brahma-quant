#!/usr/bin/env python3
"""
coinglass_engine.py — CoinGlass数据引擎 v2.0
设计院 2026-05-27

无需CoinGlass API Key，全部使用公开数据接口：
  - F&G: alternative.me Fear & Greed API
  - OI: Binance /fapi/v1/openInterest
  - 清算: liq_scanner（已有模块）
  - 资金费率: Binance /fapi/v1/fundingRate

返回标准化 get_full_snapshot(symbol) 结构：
{
  'available': True,
  'fear_greed': {'value': 25, 'label': 'EXTREME_FEAR', 'prev_value': 30},
  'oi_momentum': {'oi_change_pct': +1.2, 'oi_usd': 8.5e9, 'direction': 'RISING'},
  'liquidation': {'long_liq': 12.3, 'short_liq': 3.1, 'liq_ratio': 3.97, 'bias': 'BEARISH_CONFIRMED'},
  'funding': {'rate': 0.0005, 'annualized': 0.547, 'label': 'NEUTRAL'},
  'ls_ratio': {'long_pct': 48.5, 'short_pct': 51.5, 'top_long_pct': 52.0},
  'onchain_score': +3,
}
"""
import json, time, os, sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor as _TPE, as_completed as _asc

# ── brahma_bus 总线接入（设计院 2026-06-29）──
try:
    from brahma_brain.brahma_bus import bus as _brahma_bus
except Exception:
    _brahma_bus = None

def _bus_price(symbol: str) -> float:
    """统一价格获取：优先走 brahma_bus 缓存，降级走直接HTTP"""
    if _brahma_bus:
        return _brahma_bus.price(symbol)
    import requests
    r = requests.get(f'https://fapi.binance.com/fapi/v1/ticker/price',
                     params={'symbol': symbol}, timeout=5)
    return float(r.json()['price'])



# ── 缓存 ──────────────────────────────────────────────────────
_CACHE = {}
_CACHE_TTL = 300  # 5分钟

def _cached(key, fn):
    now = time.time()
    if key in _CACHE and now - _CACHE[key]['ts'] < _CACHE_TTL:
        return _CACHE[key]['data']
    try:
        data = fn()
        _CACHE[key] = {'ts': now, 'data': data}
        return data
    except Exception as e:
        if key in _CACHE:
            return _CACHE[key]['data']  # 返回过期缓存
        raise e

# ── HTTP工具 ──────────────────────────────────────────────────
try:
    from onchain_engine import CG_KEY as _CG_KEY
except Exception:
    _CG_KEY = 'a56a2491bca5491ca3f7c7f53b6a6963'

def _get(url, timeout=8):
    import urllib.request
    # CoinGlass v4 接口需要带 API Key
    headers = {'User-Agent': 'Mozilla/5.0'}
    if 'coinglass.com' in url:
        headers['CG-API-KEY'] = _CG_KEY
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())

# ── F&G 恐贪指数 ─────────────────────────────────────────────
def get_fear_greed():
    def _fetch():
        d = _get('https://api.alternative.me/fng/?limit=2')
        data = d.get('data', [])
        cur  = data[0] if data else {}
        prev = data[1] if len(data) > 1 else {}
        val = int(cur.get('value', 50))
        label_map = {
            (0,  25): 'EXTREME_FEAR',
            (26, 40): 'FEAR',
            (41, 59): 'NEUTRAL',
            (60, 75): 'GREED',
            (76, 100): 'EXTREME_GREED',
        }
        label = 'NEUTRAL'
        for (lo, hi), lbl in label_map.items():
            if lo <= val <= hi:
                label = lbl; break
        return {
            'value': val,
            'label': label,
            'classification': cur.get('value_classification', label),
            'prev_value': int(prev.get('value', val)),
        }
    return _cached('fear_greed', _fetch)

# ── OI持仓量 ─────────────────────────────────────────────────
def get_oi_momentum(symbol: str):
    sym = symbol.upper()
    if not sym.endswith('USDT'):
        sym += 'USDT'
    def _fetch():
        # CoinGlass v4 新接口: /api/futures/open-interest/exchange-list
        # 直接返回 open_interest_change_percent_4h，无需历史计算
        coin = sym.replace('USDT','').replace('usdt','')
        d = _get(f'https://open-api-v4.coinglass.com/api/futures/open-interest/exchange-list?symbol={coin}')
        if d and d.get('code') == '0' and d.get('data'):
            all_row = next((x for x in d['data'] if x.get('exchange') == 'All'), d['data'][0])
            oi_usd  = float(all_row.get('open_interest_usd', 0))
            oi_qty  = float(all_row.get('open_interest_quantity', 0))
            oi_chg_4h = float(all_row.get('open_interest_change_percent_4h', 0))
            oi_chg_1h = float(all_row.get('open_interest_change_percent_1h', 0))
            trend = 'RISING' if oi_chg_4h > 1.5 else ('FALLING' if oi_chg_4h < -1.5 else 'NEUTRAL')
            return {
                'oi_now': oi_qty,
                'oi_usd': oi_usd,
                'oi_change_pct': round(oi_chg_4h, 3),
                'oi_chg_1h': round(oi_chg_1h, 3),
                'direction': trend,
                'price_chg_24h': 0,
            }
        # fallback: Binance openInterestHist
        try:
            import sys as _sys, os as _os
            _bd = _os.path.dirname(_os.path.abspath(__file__))
            if _bd not in _sys.path: _sys.path.insert(0, _bd)
            from onchain_engine import get_oi_change as _goi
            oi_data = _goi(sym)
            if oi_data.get('oi_trend') != 'UNKNOWN':
                oi_chg = float(oi_data.get('oi_chg_5', 0))
                oi_now = float(oi_data.get('oi_now', 0))
                d2 = _get(f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={sym}')
                price = float(d2.get('price', 1)) if d2 else 1
                trend = oi_data.get('oi_trend', 'NEUTRAL')
                direction = 'RISING' if trend == 'RISING' else ('FALLING' if trend == 'FALLING' else 'NEUTRAL')
                return {
                    'oi_now': oi_now, 'oi_usd': oi_now * price,
                    'oi_change_pct': round(oi_chg, 3), 'direction': direction, 'price_chg_24h': 0,
                }
        except Exception:
            pass
        return {'oi_now': 0, 'oi_usd': 0, 'oi_change_pct': 0.0, 'direction': 'NEUTRAL', 'price_chg_24h': 0}
    return _cached(f'oi_{sym}', _fetch)

# ── 资金费率 ─────────────────────────────────────────────────
def get_funding(symbol: str):
    sym = symbol.upper()
    if not sym.endswith('USDT'):
        sym += 'USDT'
    def _fetch():
        d = _get(f'https://fapi.binance.com/fapi/v1/premiumIndex?symbol={sym}')
        rate = float(d.get('lastFundingRate', 0))
        annualized = rate * 3 * 365 * 100  # 8h → 年化%
        if abs(rate) < 0.0001:   label = 'NEUTRAL'
        elif rate > 0.005:       label = 'EXTREME_GREED'
        elif rate > 0.002:       label = 'HIGH'
        elif rate > 0.0005:      label = 'ELEVATED'
        elif rate < -0.002:      label = 'EXTREME_FEAR'
        elif rate < -0.0005:     label = 'LOW'
        else:                    label = 'NEUTRAL'
        return {
            'rate': rate,
            'annualized': round(annualized, 2),
            'label': label,
        }
    return _cached(f'funding_{sym}', _fetch)

# ── 多空比 ───────────────────────────────────────────────────
def get_ls_ratio(symbol: str):
    sym = symbol.upper()
    if not sym.endswith('USDT'):
        sym += 'USDT'
    def _fetch():
        # [设计院修复 2026-06-20] LS三端口完整拉取，禁止混淆
        # 全局多空比（散户账户比）— 逆向情绪指标
        d1 = _get(f'https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol={sym}&period=5m&limit=1')
        if d1 and isinstance(d1, list):
            # 修复：直接读 longAccount 字段（百分比），不做 ratio/(1+ratio) 计算
            if 'longAccount' in d1[0]:
                long_pct = float(d1[0]['longAccount']) * 100
            else:
                lsr = float(d1[0].get('longShortRatio', 1.0))
                long_pct = lsr / (1 + lsr) * 100
        else:
            long_pct = 50.0
        # 大户仓位比（topLongShortPositionRatio）— 聪明钱方向，最重要
        # [修复] 原来拉 topLongShortAccountRatio（账户数），改为 topLongShortPositionRatio（仓位）
        d2 = _get(f'https://fapi.binance.com/futures/data/topLongShortPositionRatio?symbol={sym}&period=5m&limit=1')
        if d2 and isinstance(d2, list):
            top_lsr = float(d2[0].get('longShortRatio', 1.0))
            top_long_pct = top_lsr / (1 + top_lsr) * 100
        else:
            top_long_pct = 50.0
        return {
            'long_pct': round(long_pct, 1),
            'short_pct': round(100 - long_pct, 1),
            'top_long_pct': round(top_long_pct, 1),
            'top_short_pct': round(100 - top_long_pct, 1),
        }
    return _cached(f'ls_{sym}', _fetch)

# ── 清算数据（用liq_scanner） ───────────────────────────────
def get_liquidation(symbol: str):
    sym = symbol.upper()
    if not sym.endswith('USDT'):
        sym += 'USDT'
    try:
        _dir = os.path.dirname(os.path.abspath(__file__))
        if _dir not in sys.path:
            sys.path.insert(0, _dir)
        from liq_scanner import get_liq_snapshot
        snap = get_liq_snapshot(sym)
        long_liq  = snap.get('cg_long_liq_m', 0) or 0
        short_liq = snap.get('cg_short_liq_m', 0) or 0
        ratio = long_liq / short_liq if short_liq > 0 else 1.0
        bias  = snap.get('liq_bias', 'NEUTRAL')
        return {
            'long_liq': long_liq,
            'short_liq': short_liq,
            'liq_ratio': round(ratio, 2),
            'bias': bias,
            'available': True,
        }
    except Exception as e:
        return {'long_liq': 0, 'short_liq': 0, 'liq_ratio': 1.0, 'bias': 'NEUTRAL', 'available': False}

# ── 链上综合评分 ─────────────────────────────────────────────
def get_onchain_score(symbol: str, fear_greed_val: int = 50, oi_direction: str = 'NEUTRAL') -> int:
    """
    综合链上评分 -10 ~ +10
    正=看多 负=看空
    """
    score = 0
    # F&G
    if fear_greed_val <= 20:   score += 3   # 极度恐惧 → 反转做多
    elif fear_greed_val <= 35: score += 1
    elif fear_greed_val >= 80: score -= 3   # 极度贪婪 → 反转做空
    elif fear_greed_val >= 65: score -= 1
    # OI方向
    if oi_direction == 'RISING_BEAR':   score -= 2  # 空头加仓
    elif oi_direction == 'RISING_BULL': score += 2
    return max(-10, min(10, score))

# ── 主接口 ───────────────────────────────────────────────────
def get_full_snapshot(symbol: str) -> dict:
    """
    返回标准化CoinGlass快照
    brahma_brain期望的完整结构
    """
    sym = symbol.upper()
    if not sym.endswith('USDT'):
        sym += 'USDT'
    result = {
        'available': False,
        'symbol': sym,
        'ts': time.time(),
    }
    errors = []

    # F&G/OI/FR/LS/LIQ 并发拉取（原串行5个→并发，节省~600ms）
    _defaults = {
        'fear_greed':  {'value': 50, 'label': 'NEUTRAL', 'prev_value': 50},
        'oi_momentum': {'oi_change_pct': 0, 'oi_usd': 0, 'direction': 'NEUTRAL'},
        'funding':     {'rate': 0, 'annualized': 0, 'label': 'NEUTRAL'},
        'ls_ratio':    {'long_pct': 50, 'short_pct': 50, 'top_long_pct': 50},
        'liquidation': {'long_liq': 0, 'short_liq': 0, 'liq_ratio': 1.0, 'bias': 'NEUTRAL', 'available': False},
    }
    _tasks = {
        'fear_greed':  lambda: get_fear_greed(),
        'oi_momentum': lambda: get_oi_momentum(sym),
        'funding':     lambda: get_funding(sym),
        'ls_ratio':    lambda: get_ls_ratio(sym),
        'liquidation': lambda: get_liquidation(sym),
    }
    with _TPE(max_workers=5) as ex:
        _futs = {ex.submit(fn): k for k, fn in _tasks.items()}
        for f in _asc(_futs, timeout=10):
            k = _futs[f]
            try:
                result[k] = f.result()
            except Exception as e:
                result[k] = _defaults[k]
                errors.append(f'{k[:2]}:{e}')

    # 链上综合评分
    fg_val = result['fear_greed'].get('value', 50)
    oi_dir = result['oi_momentum'].get('direction', 'NEUTRAL')
    result['onchain_score'] = get_onchain_score(sym, fg_val, oi_dir)

    # 标记可用
    result['available'] = len(errors) < 3
    if errors:
        result['_errors'] = errors

    return result


if __name__ == '__main__':
    import sys
    sym = sys.argv[1] if len(sys.argv) > 1 else 'BTCUSDT'
    snap = get_full_snapshot(sym)
    fg  = snap['fear_greed']
    oi  = snap['oi_momentum']
    liq = snap['liquidation']
    ls  = snap['ls_ratio']
    print(f"=== CoinGlass {sym} ===")
    print(f"F&G: {fg['value']} [{fg['label']}]  prev={fg['prev_value']}")
    print(f"OI : ${oi['oi_usd']/1e9:.2f}B  方向={oi['direction']}  24h价格={oi['price_chg_24h']:+.2f}%")
    print(f"清算: 多头${liq['long_liq']:.1f}M  空头${liq['short_liq']:.1f}M  偏向={liq['bias']}")
    print(f"多空比: 散户多{ls['long_pct']}%  大户多{ls['top_long_pct']}%")
    print(f"链上评分: {snap['onchain_score']:+d}")
    print(f"available={snap['available']}  errors={snap.get('_errors',[])}")
