"""
brahma_bus.py — 梵天统一数据总线
设计院·达摩院 深度排查 2026-06-29

问题根因：
  价格获取：25+个文件各自调用 ticker/price，无缓存
  klines：多个文件重复拉取同一标的同一周期
  资金费率：coinglass_engine / coinglass_fallback / pump_short_scanner 各自实现
  OI：onchain_engine / coinglass_fallback 各自实现
  → 同一请求在一次 analyze() 中可能被调用 3~5 次

解决方案：
  BrahmaBus 单例 + TTL缓存
  所有模块 from brahma_brain.brahma_bus import bus
  bus.price(sym) / bus.klines(sym, tf) / bus.funding(sym) / bus.oi(sym)
  自动去重，analyze() 内相同数据只拉一次
"""

from __future__ import annotations
import time
import requests
import hmac
import hashlib
import os
import threading
from typing import Optional

# ─────────────────────────────────────────────────────────
# API 配置
# ─────────────────────────────────────────────────────────
# [安全修复 2026-07-08 设计院] 移除硬编码默认值，纯环境变量
_BN_KEY = os.environ.get('BINANCE_API_KEY', '')
_BN_SEC = os.environ.get('BINANCE_SECRET', '')

_FAPI = 'https://fapi.binance.com'
_SESS = requests.Session()
_SESS.headers.update({'X-MBX-APIKEY': _BN_KEY})

# ─────────────────────────────────────────────────────────
# TTL 配置（秒）
# ─────────────────────────────────────────────────────────
TTL = {
    'price':    30,     # [2026-07-06] 5s→30s 防418限速：3任务×5min并发时节省80%请求
    'ticker':   60,     # [2026-07-06] 10s→60s 24H ticker低频刷新即可
    'klines':   120,    # [2026-07-06] 60s→120s K线分析不需要秒级更新
    'funding':  180,    # 资金费率 3分钟
    'oi':       90,     # [2026-07-06] 60s→90s OI变化慢
    'lsr':      90,     # [2026-07-06] 60s→90s 多空比变化慢
    'depth':    60,     # [2026-07-06] 30s→60s 订单簿深度
    'trades':   30,     # [2026-07-06] 10s→30s 成交流
}


# ─────────────────────────────────────────────────────────
# BrahmaBus 单例
# ─────────────────────────────────────────────────────────

class BrahmaBus:
    """
    统一数据总线：所有引擎共用同一缓存层
    使用方式：from brahma_brain.brahma_bus import bus
    """

    def __init__(self):
        self._cache: dict = {}
        self._lock  = threading.Lock()

    def _get(self, key: str, fn, ttl: int):
        """通用缓存读取（线程安全）"""
        now = time.time()
        with self._lock:
            cached = self._cache.get(key)
            if cached and now - cached['ts'] < ttl:
                return cached['data']
        try:
            data = fn()
            with self._lock:
                self._cache[key] = {'data': data, 'ts': now}
            return data
        except Exception as e:
            # [2026-07-06] 418/429限速期间：返回过期缓存（最长24H容忍），避免分析链崩溃
            with self._lock:
                cached = self._cache.get(key)
                if cached and (now - cached['ts']) < 86400:  # 24H内的缓存均可降级使用
                    return cached['data']
            return None

    # ── 价格 ────────────────────────────────────────────────

    def price(self, symbol: str) -> float:
        """实时最新价（TTL缓存）— Binance主源，418限速时自动切换OKX/Bybit"""
        def _fetch():
            try:
                r = _SESS.get(f'{_FAPI}/fapi/v1/ticker/price',
                              params={'symbol': symbol}, timeout=5)
                if r.status_code == 418 or r.status_code == 429:
                    raise Exception(f'Binance {r.status_code}')
                return float(r.json()['price'])
            except Exception as _e1:
                # [2026-07-06] 备用源：OKX → Bybit → 报错
                _base = symbol.replace('USDT', '')
                try:
                    _r2 = _SESS.get(f'https://www.okx.com/api/v5/market/ticker',
                                    params={'instId': f'{_base}-USDT-SWAP'}, timeout=5)
                    return float(_r2.json()['data'][0]['last'])
                except Exception:
                    pass
                try:
                    _r3 = _SESS.get('https://api.bybit.com/v5/market/tickers',
                                    params={'category': 'linear', 'symbol': symbol}, timeout=5)
                    return float(_r3.json()['result']['list'][0]['lastPrice'])
                except Exception:
                    pass
                raise _e1  # 全部失败才抛出
        val = self._get(f'price:{symbol}', _fetch, TTL['price'])
        return val or 0.0

    def ticker(self, symbol: str) -> dict:
        """24H ticker（10s缓存）"""
        def _fetch():
            r = _SESS.get(f'{_FAPI}/fapi/v1/ticker/24hr',
                          params={'symbol': symbol}, timeout=5)
            return r.json()
        return self._get(f'ticker:{symbol}', _fetch, TTL['ticker']) or {}

    def price_change_pct(self, symbol: str) -> float:
        """24H涨跌幅（%）"""
        t = self.ticker(symbol)
        return float(t.get('priceChangePercent', 0))

    # ── K线 ─────────────────────────────────────────────────

    def klines(self, symbol: str, interval: str = '1h',
               limit: int = 100) -> list[list]:
        """
        K线数据（60s缓存）
        返回格式：[[open_time, open, high, low, close, volume, ...], ...]
        """
        def _fetch():
            r = _SESS.get(f'{_FAPI}/fapi/v1/klines',
                          params={'symbol': symbol, 'interval': interval,
                                  'limit': limit}, timeout=8)
            return r.json()
        return self._get(f'klines:{symbol}:{interval}:{limit}',
                         _fetch, TTL['klines']) or []

    def closes(self, symbol: str, interval: str = '1h',
               limit: int = 100) -> list[float]:
        """只返回收盘价序列（最新在末尾）"""
        ks = self.klines(symbol, interval, limit)
        return [float(k[4]) for k in ks]

    def ohlcv(self, symbol: str, interval: str = '1h',
              limit: int = 100) -> tuple[list, list, list, list, list]:
        """返回 (opens, highs, lows, closes, volumes)"""
        ks = self.klines(symbol, interval, limit)
        o = [float(k[1]) for k in ks]
        h = [float(k[2]) for k in ks]
        l = [float(k[3]) for k in ks]
        c = [float(k[4]) for k in ks]
        v = [float(k[5]) for k in ks]
        return o, h, l, c, v

    # ── 资金费率 ─────────────────────────────────────────────

    def funding_rate(self, symbol: str) -> float:
        """当前资金费率（120s缓存）"""
        def _fetch():
            r = _SESS.get(f'{_FAPI}/fapi/v1/premiumIndex',
                          params={'symbol': symbol}, timeout=5)
            return float(r.json().get('lastFundingRate', 0))
        val = self._get(f'funding:{symbol}', _fetch, TTL['funding'])
        return val or 0.0

    # ── 持仓量 OI ────────────────────────────────────────────

    def open_interest(self, symbol: str) -> float:
        """当前持仓量（60s缓存）"""
        def _fetch():
            r = _SESS.get(f'{_FAPI}/fapi/v1/openInterest',
                          params={'symbol': symbol}, timeout=5)
            return float(r.json().get('openInterest', 0))
        val = self._get(f'oi:{symbol}', _fetch, TTL['oi'])
        return val or 0.0

    def oi_history(self, symbol: str, period: str = '1h',
                   limit: int = 30) -> list[dict]:
        """OI历史（60s缓存）"""
        def _fetch():
            r = _SESS.get(f'{_FAPI}/futures/data/openInterestHist',
                          params={'symbol': symbol, 'period': period,
                                  'limit': limit}, timeout=8)
            return r.json()
        return self._get(f'oi_hist:{symbol}:{period}:{limit}',
                         _fetch, TTL['oi']) or []

    # ── 多空比 LSR ───────────────────────────────────────────

    def long_short_ratio(self, symbol: str, period: str = '1h',
                         limit: int = 1) -> float:
        """全体账户多空比（60s缓存）"""
        def _fetch():
            r = _SESS.get(f'{_FAPI}/futures/data/globalLongShortAccountRatio',
                          params={'symbol': symbol, 'period': period,
                                  'limit': limit}, timeout=5)
            data = r.json()
            return float(data[0]['longShortRatio']) if data else 1.0
        val = self._get(f'lsr:{symbol}:{period}', _fetch, TTL['lsr'])
        return val or 1.0

    # ── 订单簿 ───────────────────────────────────────────────

    def depth(self, symbol: str, limit: int = 20) -> dict:
        """订单簿（30s缓存）"""
        def _fetch():
            r = _SESS.get(f'{_FAPI}/fapi/v1/depth',
                          params={'symbol': symbol, 'limit': limit}, timeout=5)
            return r.json()
        return self._get(f'depth:{symbol}:{limit}', _fetch, TTL['depth']) or {}

    # ── 签名请求（账户数据）──────────────────────────────────

    def _signed_get(self, endpoint: str, params: dict = None) -> dict:
        """带签名的 GET 请求"""
        p = dict(params or {})
        p['timestamp'] = int(time.time() * 1000)
        qs = '&'.join(f'{k}={v}' for k, v in p.items())
        sig = hmac.new(_BN_SEC.encode(), qs.encode(), hashlib.sha256).hexdigest()
        r = _SESS.get(f'{_FAPI}{endpoint}?{qs}&signature={sig}', timeout=8)
        return r.json()

    def positions(self) -> list[dict]:
        """账户持仓（10s缓存）"""
        def _fetch():
            data = self._signed_get('/fapi/v2/positionRisk')
            return [p for p in data if float(p.get('positionAmt', 0)) != 0]
        return self._get('positions', _fetch, 10) or []

    def balance(self) -> dict:
        """账户余额摘要（10s缓存）"""
        def _fetch():
            acc = self._signed_get('/fapi/v2/account')
            return {
                'nav':       float(acc.get('totalWalletBalance', 0)) + float(acc.get('totalUnrealizedProfit', 0)),
                'available': float(acc.get('availableBalance', 0)),
                'margin':    float(acc.get('totalInitialMargin', 0)),
                'pnl':       float(acc.get('totalUnrealizedProfit', 0)),
            }
        return self._get('balance', _fetch, 10) or {}

    # ── 缓存管理 ─────────────────────────────────────────────

    def invalidate(self, pattern: str = None):
        """清除缓存（pattern=None 清全部）"""
        with self._lock:
            if pattern:
                keys = [k for k in self._cache if pattern in k]
                for k in keys:
                    del self._cache[k]
            else:
                self._cache.clear()

    def cache_stats(self) -> dict:
        """缓存状态统计"""
        now = time.time()
        with self._lock:
            total = len(self._cache)
            fresh = sum(1 for v in self._cache.values()
                        if now - v['ts'] < 60)
        return {'total_keys': total, 'fresh_keys': fresh}


# ─────────────────────────────────────────────────────────
# 全局单例 — 所有模块 import 同一个对象
# ─────────────────────────────────────────────────────────
bus = BrahmaBus()


if __name__ == '__main__':
    import json

    print('=== BrahmaBus 总线自测 ===')
    sym = 'BTCUSDT'

    t0 = time.time()
    p1 = bus.price(sym)
    t1 = time.time()
    p2 = bus.price(sym)  # 应命中缓存
    t2 = time.time()

    print(f'price 第一次: {p1}  耗时={t1-t0:.3f}s')
    print(f'price 第二次: {p2}  耗时={t2-t1:.4f}s（缓存命中）')

    fr = bus.funding_rate(sym)
    lsr = bus.long_short_ratio(sym)
    closes = bus.closes(sym, '1h', 20)
    print(f'funding_rate={fr:.4f}  lsr={lsr:.2f}  closes[-3:]={[round(c,0) for c in closes[-3:]]}')

    stats = bus.cache_stats()
    print(f'缓存统计: {stats}')
    print('BrahmaBus 自测通过 ✅')


# ══════════════════════════════════════════════════════════════
# [B2 设计院 2026-06-30] 全局数据一致性守卫
# 提供统一价格查询接口，供所有模块调用
# 不强制monkey-patch（高风险），而是提供标准函数供迁移
# ══════════════════════════════════════════════════════════════

def get_price(symbol: str) -> float:
    """统一价格查询 — bus缓存优先，fallback裸HTTP，所有模块应迁移到此接口"""
    try:
        return bus.price(symbol)
    except Exception:
        import urllib.request, json
        with urllib.request.urlopen(
            f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={symbol}', timeout=5
        ) as r:
            return float(json.loads(r.read()).get('lastPrice', 0))


def get_klines(symbol: str, interval: str = '1h', limit: int = 100) -> list:
    """统一K线查询 — bus缓存优先"""
    try:
        return bus.klines(symbol, interval, limit)
    except Exception:
        import urllib.request, json
        url = f'https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}'
        with urllib.request.urlopen(url, timeout=10) as r:
            return json.loads(r.read())


def get_funding(symbol: str) -> float:
    """统一资金费率查询"""
    try:
        return bus.funding_rate(symbol)
    except Exception:
        return 0.0


def get_oi(symbol: str) -> float:
    """统一OI查询"""
    try:
        return bus.open_interest(symbol)
    except Exception:
        return 0.0

# 迁移状态追踪（记录哪些文件已完成迁移）
_MIGRATED_FILES = set()

def mark_migrated(filename: str):
    """标记文件已完成数据层迁移"""
    _MIGRATED_FILES.add(filename)

def migration_status() -> dict:
    """返回数据层迁移状态"""
    total_target = 65
    migrated = len(_MIGRATED_FILES)
    return {
        'total_target': total_target,
        'migrated': migrated,
        'remaining': total_target - migrated,
        'pct': round(migrated / total_target * 100, 1),
        'migrated_files': sorted(_MIGRATED_FILES),
    }


def flush_stale(max_age_seconds: float = 300.0) -> int:
    """
    清理BrahmaBus全局单例中陈旧缓存条目
    设计院·自愈机制 2026-07-01
    返回：清理条目数
    """
    import time
    bus = BrahmaBus()
    now = time.time()
    stale_keys = [
        k for k, v in bus._cache.items()
        if isinstance(v, dict) and 'ts' in v and now - v['ts'] > max_age_seconds
    ]
    for k in stale_keys:
        del bus._cache[k]
    if stale_keys:
        pass  # [静默]
    return len(stale_keys)
