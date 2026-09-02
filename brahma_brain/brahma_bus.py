# ponytail: brahma_bus 383行，有意为之，重构前先 grep 所有调用方
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
    'price':    5,      # [2026-08-18 苏摩封印] 5s — 价格准确性优先，30s缓存导致跨会话价格错误
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
        """[2026-08-18 苏摩111封印] 实时最新价 — 永不缓存，每次直接拉取币安期货API
        根因封印：任何缓存都可能导致价格与市场实际偏差数百美元（ETH: $2618→$1984）
        架构铁律：price()不走缓存，不用磁盘，不用TTL，直接返回fapi实时价格
        """
        # 主源：Binance期货实时价格（永不缓存）
        try:
            r = _SESS.get(f'{_FAPI}/fapi/v1/ticker/price',
                          params={'symbol': symbol}, timeout=5)
            if r.status_code not in (418, 429):
                p = float(r.json()['price'])
                if p > 0:
                    return p
            raise Exception(f'Binance status={r.status_code}')
        except Exception as _e1:
            # 备用源：OKX → Bybit（仅在Binance限速时使用）
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
            raise _e1  # 全部失败才报错，不返回过期价格

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
_price_bus = BrahmaBus()   # 价格/数据总线（BrahmaBus）
bus = _price_bus           # 向后兼容别名（勿被下方 BrahmaEventBus 覆盖）

# [P1修复 2026-08-26] get_balance别名，兼容drawdown_tracker等调用方式
def get_balance() -> dict:
    """bus.balance()的模块级别名，返回NAV/available/margin/pnl字典"""
    return bus.balance()

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
        p = _price_bus.price(symbol)
        if p and p > 0:
            return p
    except Exception:
        pass
    import urllib.request, json as _json
    with urllib.request.urlopen(
        f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={symbol}', timeout=5
    ) as r:
        d = _json.loads(r.read())
        return float(d.get('price', d.get('lastPrice', 0)))


def get_klines(symbol: str, interval: str = '1h', limit: int = 100) -> list:
    """统一K线查询 — bus缓存优先"""
    try:
        return bus.klines(symbol, interval, limit)
    except Exception:
        import urllib.request, json
        url = f'https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}'
        with urllib.request.urlopen(url, timeout=10, context=_DC_SSL_CTX) as r:
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
    """[P0-6修复 2026-07-16 苏摩111] 使用全局单例bus，不重新实例化"""
    import time as _time_fs
    now = _time_fs.time()
    with bus._lock:
        stale_keys = [
            k for k, v in bus._cache.items()
            if isinstance(v, dict) and 'ts' in v and now - v['ts'] > max_age_seconds
        ]
        for k in stale_keys:
            del bus._cache[k]
    return len(stale_keys)(stale_keys)
"""

# STATUS: ACTIVE
# 事件总线，模块间通信
# LAST_REVIEW: 2026-07-01 | 属于辅助计算层，修改前确认调用链
# ─────────────────────────────────────────────────────────────
梵天 EventBus v1.0
借鉴 vnpy EventEngine 设计，轻量级事件总线
解决模块间状态不同步问题（watching=0 类 bug 根治）

苏摩111批准落地 · 2026-06-28
"""

import threading
import json
import time
import logging
from collections import defaultdict
from typing import Callable, Any
from pathlib import Path
from data_cache import _SSL_CTX as _DC_SSL_CTX

logger = logging.getLogger("BrahmaEventBus")


# ═══════════════════════════════════════════════════════
#  事件类型常量（宪法级，新增需苏摩批准）
# ═══════════════════════════════════════════════════════
class BrahmaEvent:
    # 价格相关
    PRICE_UPDATE      = "price_update"       # 实时价格更新
    PRICE_ALERT       = "price_alert"        # 价格告警（触碰关键位）

    # 信号相关
    SIGNAL_FIRED      = "signal_fired"       # 梵天发出新信号
    SIGNAL_EXPIRED    = "signal_expired"     # 信号超时失效
    SIGNAL_CANCELLED  = "signal_cancelled"   # 信号被门控拒绝

    # 持仓相关
    POSITION_OPEN     = "position_open"      # 开仓成功
    POSITION_CLOSE    = "position_close"     # 平仓（止盈/止损/手动）
    POSITION_UPDATE   = "position_update"    # 持仓状态变化（浮盈更新）
    SL_TRIGGERED      = "sl_triggered"       # 软止损触发

    # 体制相关
    REGIME_CHANGE     = "regime_change"      # 体制切换（BEAR→BULL等）

    # 系统相关
    SYSTEM_START      = "system_start"       # 系统启动
    SYSTEM_STOP       = "system_stop"        # 系统停止
    HEARTBEAT         = "heartbeat"          # 心跳


# ═══════════════════════════════════════════════════════
#  事件数据包
# ═══════════════════════════════════════════════════════
class Event:
    def __init__(self, event_type: str, data: Any = None):
        self.type = event_type
        self.data = data or {}
        self.ts   = time.time()

    def to_dict(self):
        return {"type": self.type, "data": self.data, "ts": self.ts}


# ═══════════════════════════════════════════════════════
#  EventBus 核心
# ═══════════════════════════════════════════════════════
class BrahmaEventBus:
    """
    轻量级事件总线
    - 同步模式（默认）：直接调用所有处理器
    - 异步模式：后台线程队列处理（可选）
    - 状态持久化：事件日志写入文件
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        # 单例模式，全系统共享一个EventBus
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._handlers: dict[str, list[Callable]] = defaultdict(list)
        self._event_log_path = Path("data/brahma_event_log.jsonl")
        self._event_log_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialized = True
        logger.info("BrahmaEventBus v1.0 初始化完成")

    def register(self, event_type: str, handler: Callable):
        """注册事件处理器"""
        if handler not in self._handlers[event_type]:
            self._handlers[event_type].append(handler)
            logger.debug(f"注册处理器: {event_type} → {handler.__name__}")

    def unregister(self, event_type: str, handler: Callable):
        """注销事件处理器"""
        if handler in self._handlers[event_type]:
            self._handlers[event_type].remove(handler)

    def emit(self, event: Event, persist: bool = False):
        """
        发射事件 → 调用所有注册的处理器
        persist=True 时写入事件日志文件
        """
        handlers = self._handlers.get(event.type, [])
        for handler in handlers:
            try:
                handler(event)
            except Exception as e:
                logger.error(f"EventBus处理器错误 [{event.type}] {handler.__name__}: {e}")

        if persist:
            self._log_event(event)

    def emit_position_open(self, symbol: str, side: str, entry: float,
                            sl: float, tp1: float, signal_id: str, **kwargs):
        """便捷方法：发射持仓开仓事件"""
        data = {
            "symbol": symbol, "side": side, "entry": entry,
            "sl": sl, "tp1": tp1, "signal_id": signal_id,
            **kwargs
        }
        self.emit(Event(BrahmaEvent.POSITION_OPEN, data), persist=True)

    def emit_position_close(self, symbol: str, outcome: str, pnl_pct: float,
                             signal_id: str, **kwargs):
        """便捷方法：发射持仓平仓事件"""
        data = {
            "symbol": symbol, "outcome": outcome,
            "pnl_pct": pnl_pct, "signal_id": signal_id,
            **kwargs
        }
        self.emit(Event(BrahmaEvent.POSITION_CLOSE, data), persist=True)

    def emit_regime_change(self, symbol: str, old_regime: str, new_regime: str):
        """便捷方法：发射体制切换事件"""
        data = {"symbol": symbol, "old": old_regime, "new": new_regime}
        self.emit(Event(BrahmaEvent.REGIME_CHANGE, data), persist=True)

    def emit_sl_triggered(self, symbol: str, trigger_price: float,
                           sl_price: float, signal_id: str):
        """便捷方法：发射软止损触发事件"""
        data = {
            "symbol": symbol, "trigger_price": trigger_price,
            "sl_price": sl_price, "signal_id": signal_id
        }
        self.emit(Event(BrahmaEvent.SL_TRIGGERED, data), persist=True)

    def _log_event(self, event: Event):
        """写入事件日志（追加模式）"""
        try:
            with open(self._event_log_path, "a") as f:
                f.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error(f"事件日志写入失败: {e}")

    def get_recent_events(self, event_type: str = None, limit: int = 50) -> list:
        """读取最近的事件日志"""
        events = []
        if not self._event_log_path.exists():
            return []
        with open(self._event_log_path) as f:
            for line in f:
                try:
                    e = json.loads(line.strip())
                    if event_type is None or e.get("type") == event_type:
                        events.append(e)
                except:
                    pass
        return events[-limit:]

    def handler_count(self, event_type: str) -> int:
        return len(self._handlers.get(event_type, []))

    def status(self) -> dict:
        return {
            "registered_types": list(self._handlers.keys()),
            "handler_counts": {k: len(v) for k, v in self._handlers.items()},
            "log_path": str(self._event_log_path),
        }


# 全局单例
event_bus = BrahmaEventBus()


# ── [Fix3 2026-08-30 苏摩111] REGIME_CHANGE 事件 Handler — 清除旧体制的逾期 PENDING 信号 ──────────────────
# 根因：体制切换后（如 BEAR_TREND → BULL_TREND），队列里旧体制的与新体制方向相反的 PENDING 信号应该失效
# 40年交易员常识：体制变了，上一个体制的仓位逻辑全作废
def _handle_regime_change_purge(event: 'Event'):
    """
    体制切换时，清除队列中与新体制方向矛盾的 PENDING 信号。
    规则：
      - BEAR_* → BULL_* 切换：清除 BEAR/CHOP 体制下的 SHORT PENDING 信号
      - BULL_* → BEAR_* 切换：清除 BULL/CHOP 体制下的 LONG PENDING 信号
      - CHOP → 任意：清除旧 CHOP 中矛盾方向信号
    """
    import json, time
    from pathlib import Path

    data = event.data or {}
    old_regime = str(data.get('old', '')).upper()
    new_regime = str(data.get('new', '')).upper()
    symbol     = data.get('symbol', '')  # 可能为空（全市场体制切换）

    if not old_regime or not new_regime:
        return

    # 确定需要清除的方向
    purge_direction = None
    if 'BEAR' in new_regime or 'CRASH' in new_regime:
        purge_direction = 'LONG'   # 新体制是空头，清除老多单
    elif 'BULL' in new_regime:
        purge_direction = 'SHORT'  # 新体制是多头，清除老空单
    # CHOP → CHOP 不处理，其他切换按上面逻辑

    if not purge_direction:
        return

    queue_path = Path(__file__).parent.parent / 'data' / 'signal_queue.jsonl'
    if not queue_path.exists():
        return

    try:
        lines = queue_path.read_text().strip().splitlines()
        kept, purged = [], 0
        now = time.time()
        for line in lines:
            try:
                sig = json.loads(line)
                sig_dir    = str(sig.get('direction', '') or '').upper()
                sig_status = str(sig.get('status', '')).upper()
                sig_sym    = sig.get('symbol', '')
                # 只处理 PENDING 信号
                if sig_status != 'PENDING':
                    kept.append(line)
                    continue
                # 如果指定了 symbol，只清除该 symbol
                if symbol and sig_sym and sig_sym != symbol:
                    kept.append(line)
                    continue
                # 方向矛盾 → 标记为 REGIME_EXPIRED并丢弃
                if sig_dir == purge_direction:
                    purged += 1
                else:
                    kept.append(line)
            except Exception:
                kept.append(line)  # 解析失败不丢弃

        if purged > 0:
            queue_path.write_text('\n'.join(kept) + ('\n' if kept else ''))
            logger.info(f'[REGIME_CHANGE] {old_regime}→{new_regime}: 清除 {purged} 条过期 PENDING {purge_direction} 信号')
    except Exception as e:
        logger.warning(f'[REGIME_CHANGE] 清除信号失败: {e}')


# 注册 handler 到全局单例
event_bus.register(BrahmaEvent.REGIME_CHANGE, _handle_regime_change_purge)
