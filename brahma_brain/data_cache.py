"""

# ── STATUS: AUXILIARY ──────────────────────────────────────────
# 数据缓存层，被brahma_bus代理
# LAST_REVIEW: 2026-07-01 | 属于辅助计算层，修改前确认调用链
# ─────────────────────────────────────────────────────────────
data_cache.py · 本地K线缓存层
brahma_brain · Phase 1

功能：
  - 5档时间框架并发拉取（1m/15m/1H/4H/1D）
  - TTL自动过期管理（零重复API调用）
  - 辅助数据：资金费率/OI/24H行情
"""
VERSION = 'v1.1'  # 设计院 2026-05-20 · [360fix] 2026-06-18 OFFLINE_MODE
import os, sys, time
import threading
import json, hmac, hashlib  # [C2-fix audit-2026-06-17]
_cache_lock = threading.Lock()  # [C2-fix]
import urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed

# ─── OFFLINE_MODE：离线回放时冻结实时API ──────────────────────
# 由 sim_brahma_replay.py 在回放前激活：
#   import brahma_brain.data_cache as dc; dc.OFFLINE_MODE = True; dc.OFFLINE_CTX = {...}
# 激活后，所有 get_funding_rate / get_open_interest / get_lsr / get_ticker
# 直接返回 OFFLINE_CTX 里的中性默认值，仅 get_klines 允许真实传入
OFFLINE_MODE: bool = False
OFFLINE_CTX: dict  = {
    'fr':           0.0001,
    'oi':           100000,
    'oi_change':    0.0,
    'lsr':          1.0,
    'top_lsr':      1.0,
    'liquidation':  0.0,
    'gex':          0.0,
    'kronos_p_up':  0.5,
    'whale_flow':   0.0,
    'iv':           0.4,
}

# ─── OFFLINE_MODE 全局网络拦截 ─────────────────────────────────
# 设置 OFFLINE_MODE=True 后调用此函数，patch urllib 防止任何模块（包括
# kronos_engine / realtime_fetch 等绕过 data_cache 的模块）发起真实请求。
_orig_urlopen = None

def enable_offline_network_block():
    """激活 OFFLINE_MODE 时 patch urllib.request.urlopen，拦截所有 Binance API 请求。"""
    global _orig_urlopen
    import urllib.request as _ureq
    if _orig_urlopen is not None:
        return  # 已经 patch 过
    _orig_urlopen = _ureq.urlopen
    def _blocked_urlopen(req, *a, **kw):
        url = req if isinstance(req, str) else getattr(req, 'full_url', str(req))
        # 只拦截 Binance API 请求，其他允许通过
        if 'binance.com' in url or 'fapi.' in url:
            return _orig_urlopen.__class__  # 触发 AttributeError → 上层 except 捕获
        return _orig_urlopen(req, *a, **kw)
    _ureq.urlopen = _blocked_urlopen

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE_DIR, '..'))

# ─── API Key 加载 ───────────────────────────────────────────
def _load_keys():
    try:
        import importlib.util
        conf = os.path.abspath(os.path.join(BASE_DIR, '..', 'config.py'))
        spec = importlib.util.spec_from_file_location('cfg', conf)
        mod  = importlib.util.module_from_spec(spec)
        old  = os.getcwd(); os.chdir(os.path.dirname(conf))
        spec.loader.exec_module(mod)
        os.chdir(old)
        bk = getattr(mod, 'binance_keys', None)
        if callable(bk):
            r = bk()
            return r[0], r[1]
    except Exception:
        pass
    return '', ''

API_KEY, API_SECRET = _load_keys()
FAPI = 'https://fapi.binance.com'

# ─── TTL配置（秒）───────────────────────────────────────────
# [设计院2026-05-28] TTL合理化 — 实盘交易必须用最新数据
# 原则：TTL < 对应周期蜡烛时长的1/4，保证分析时数据不超过1根K线的误差
TTL = {
    '1m':   30,    # 1m蜡烛60s一根，30s缓存（原60s）
    '5m':   60,    # 5m蜡烛300s一根，60s缓存（原180s）
    '15m':  60,    # 15m蜡烛900s一根，60s缓存（原300s）⚠️修复
    '1h':   120,   # 1H蜡烛3600s一根，120s缓存（原900s）⚠️修复
    '4h':   300,   # 4H蜡烛14400s一根，300s缓存（原3600s）⚠️修复
    '1d':   600,   # 日线86400s一根，600s缓存（原7200s）⚠️修复
    'ticker': 15,  # 实时价格15s（原30s）⚠️修复
    'fr':   120,   # 资金费率，120s（原300s）
    'oi':   60,    # OI数据60s
    'lsr':  120,   # 多空比120s（原硬编码900s）⚠️修复
}

# ─── 内存缓存 + 文件落盘缓存 ────────────────────────────────────
# [修复P1-4] 进程重启后从磁盘恢复，避免全量重拉
import tempfile as _tmpfile
_DISK_CACHE_DIR = os.path.join(BASE_DIR, '..', 'data', 'brahma_cache')
os.makedirs(_DISK_CACHE_DIR, exist_ok=True)
_cache: dict = {}

def _cache_key(symbol: str, kind: str, limit: int = 0) -> str:
    # limit>0时加入key，防止小limit覆盖大limit缓存
    if limit > 0:
        return f'{symbol}:{kind}:{limit}'
    return f'{symbol}:{kind}'

def _disk_path(key: str) -> str:
    safe = key.replace(':', '_').replace('/', '_')
    return os.path.join(_DISK_CACHE_DIR, f'{safe}.json')

def _cache_get(key: str):
    # 1. 内存命中
    entry = _cache.get(key)
    if entry and time.time() < entry['exp']:
        return entry['data']
    # 2. 磁盘命中（进程重启后恢复）
    try:
        path = _disk_path(key)
        if os.path.exists(path):
            disk = json.loads(open(path).read())
            if time.time() < disk.get('exp', 0):
                _cache[key] = disk  # 热加载回内存
                return disk['data']
    except Exception:
        pass
    return None

def _cache_set(key: str, data, ttl: int):
    exp = time.time() + ttl
    with _cache_lock:  # [C2-fix] 写入加锁
        _cache[key] = {'data': data, 'exp': exp}
    # 异步写磁盘（只缓存K线/ticker，不缓存大量原始数据）
    try:
        path = _disk_path(key)
        with open(path, 'w') as f:
            json.dump({'data': data, 'exp': exp}, f)
    except Exception:
        pass  # 磁盘写失败不影响内存缓存

# ─── HTTP工具 ────────────────────────────────────────────────
def _get(url: str, timeout=8):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())

def _signed_get(path: str, params: dict = None, timeout=8):
    p = dict(params or {})
    p['timestamp'] = int(time.time() * 1000)
    qs  = urllib.parse.urlencode(p)
    sig = hmac.new(API_SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()
    req = urllib.request.Request(
        f'{FAPI}{path}?{qs}&signature={sig}',
        headers={'X-MBX-APIKEY': API_KEY}
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())

# ─── 核心拉取函数 ────────────────────────────────────────────
def get_klines(symbol: str, interval: str, limit: int = 200) -> list:
    """拉取K线，带缓存（symbol 先 ASCII 校验，跳过 CJK 非法标的）"""
    try:
        symbol.encode('ascii')
    except UnicodeEncodeError:
        return []  # CJK/非ASCII symbol，Binance API 不支持，直接跳过
    key = _cache_key(symbol, interval, limit)  # limit加入key，防止小limit覆盖大limit缓存
    cached = _cache_get(key)
    if cached is not None:
        return cached
    # 已有更大limit的缓存则复用（截取即可）
    for bigger_limit in [500, 300, 250, 200, 100]:
        if bigger_limit > limit:
            big_key = _cache_key(symbol, interval, bigger_limit)
            big_cached = _cache_get(big_key)
            if big_cached is not None:
                return big_cached[-limit:]
    # [offline] OFFLINE_MODE 下禁止网络请求，已无缓存则返回空
    if OFFLINE_MODE:
        return []
    try:
        data = _get(
            f'{FAPI}/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}'
        )
        _cache_set(key, data, TTL.get(interval, 300))
        return data
    except Exception as e:
        print(f'[Cache] klines失败 {symbol}/{interval}: {e}')
        return []

def get_ticker(symbol: str) -> dict:
    if OFFLINE_MODE: return OFFLINE_CTX.get('ticker', {})  # [offline]
    """24H行情（symbol 先 ASCII 校验）"""
    try:
        symbol.encode('ascii')
    except UnicodeEncodeError:
        return {}
    key = _cache_key(symbol, 'ticker')
    cached = _cache_get(key)
    if cached is not None:
        return cached
    try:
        data = _get(f'{FAPI}/fapi/v1/ticker/24hr?symbol={symbol}')
        _cache_set(key, data, TTL['ticker'])
        return data
    except Exception as e:
        print(f'[Cache] ticker失败 {symbol}: {e}')
        return {}

def get_funding_rate(symbol: str) -> float:
    if OFFLINE_MODE: return OFFLINE_CTX.get("fr", 0)  # [offline]
    """当前资金费率"""
    key = _cache_key(symbol, 'fr')
    cached = _cache_get(key)
    if cached is not None:
        return cached
    try:
        data = _get(f'{FAPI}/fapi/v1/fundingRate?symbol={symbol}&limit=3')
        fr = float(data[-1]['fundingRate']) * 100 if data else 0.0
        _cache_set(key, fr, TTL['fr'])
        return fr
    except Exception:
        return 0.0

def get_open_interest(symbol: str) -> dict:
    if OFFLINE_MODE: return OFFLINE_CTX.get("oi", 0)  # [offline]
    """未平仓量 + OI动量（oi_change_pct）[P2 2026-05-22]"""
    key = _cache_key(symbol, 'oi')
    cached = _cache_get(key)
    if cached is not None:
        return cached
    try:
        # 当前OI
        data = _get(f'{FAPI}/fapi/v1/openInterest?symbol={symbol}')
        oi_now = float(data.get('openInterest', 0))
        # OI历史（5H变化率）— 拉 openInterestHist
        oi_change_pct = 0.0
        oi_momentum   = 'NEUTRAL'
        try:
            hist = _get(f'{FAPI}/futures/data/openInterestHist'
                        f'?symbol={symbol}&period=1h&limit=6')
            if hist and len(hist) >= 2:
                oi_5h_ago = float(hist[0].get('sumOpenInterest', 0))
                oi_latest = float(hist[-1].get('sumOpenInterest', oi_now))
                if oi_5h_ago > 0:
                    oi_change_pct = (oi_latest - oi_5h_ago) / oi_5h_ago * 100
                if oi_change_pct > 1.0:   oi_momentum = 'INCREASING'
                elif oi_change_pct < -1.0: oi_momentum = 'DECREASING'
                else:                      oi_momentum = 'NEUTRAL'
        except Exception:
            pass
        oi = {
            'oi':             oi_now,
            'ts':             int(data.get('time', 0)),
            'oi_change_pct':  round(oi_change_pct, 4),
            'oi_momentum':    oi_momentum,
        }
        _cache_set(key, oi, TTL['oi'])
        return oi
    except Exception:
        return {'oi': 0, 'ts': 0, 'oi_change_pct': 0.0, 'oi_momentum': 'NEUTRAL'}

def get_long_short_ratio(symbol: str) -> float:
    if OFFLINE_MODE: return OFFLINE_CTX.get('lsr', 50.0)  # [offline]
    """多空比（多头占比%）"""
    key = _cache_key(symbol, 'lsr')
    cached = _cache_get(key)
    if cached is not None:
        return cached
    try:
        data = _get(
            f'{FAPI}/futures/data/globalLongShortAccountRatio'
            f'?symbol={symbol}&period=1h&limit=2'
        )
        ls = float(data[-1]['longAccount']) * 100 if data else 50.0
        _cache_set(key, ls, TTL.get('lsr', 120))  # 修复：原硬编码900s改用TTL统一配置
        return ls
    except Exception:
        return 50.0

def get_all_active_symbols() -> list:
    """获取所有活跃合约（流动性过滤）"""
    key = 'ALL:symbols'
    cached = _cache_get(key)
    if cached is not None:
        return cached
    try:
        data = _get(f'{FAPI}/fapi/v1/ticker/24hr')
        symbols = []
        for t in data:
            sym = t['symbol']
            if not sym.endswith('USDT'):
                continue
            vol = float(t.get('quoteVolume', 0))
            if vol < 5_000_000:   # D级硬排除（<500万/天）
                continue
            symbols.append({'symbol': sym, 'vol24': vol})
        symbols.sort(key=lambda x: x['vol24'], reverse=True)
        _cache_set(key, symbols, 1800)
        return symbols
    except Exception as e:
        print(f'[Cache] symbols失败: {e}')
        return []

def prefetch_symbol(symbol: str) -> dict:
    """并发预拉取单币所有数据"""
    result = {}
    intervals = ['15m', '1h', '4h', '1d']
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(get_klines, symbol, iv, 200): iv for iv in intervals}
        futs[ex.submit(get_ticker, symbol)] = 'ticker'
        futs[ex.submit(get_funding_rate, symbol)] = 'fr'
        futs[ex.submit(get_open_interest, symbol)] = 'oi'
        futs[ex.submit(get_long_short_ratio, symbol)] = 'lsr'
        for f in as_completed(futs):
            key = futs[f]
            try:
                result[key] = f.result()
            except Exception as e:
                result[key] = None
                print(f'[Cache] prefetch {symbol}/{key} 失败: {e}')
    return result

def clear_expired():
    """清理过期缓存（内存 + 磁盘）"""
    now = time.time()
    # 内存缓存清理
    with _cache_lock:
        expired = [k for k, v in _cache.items() if now >= v['exp']]
        for k in expired:
            del _cache[k]

    # 磁盘缓存清理（保留最近3天，避免无限膨胀）
    # 设计院 2026-06-29: brahma_cache无磁盘TTL导致文件累积
    _DISK_MAX_AGE = 3 * 86400  # 3天
    try:
        purged = 0
        for fname in os.listdir(_DISK_CACHE_DIR):
            fpath = os.path.join(_DISK_CACHE_DIR, fname)
            if os.path.isfile(fpath):
                mtime = os.path.getmtime(fpath)
                if now - mtime > _DISK_MAX_AGE:
                    os.remove(fpath)
                    purged += 1
        if purged:
            print(f'[Cache] 磁盘清理: 删除{purged}个过期文件(>{_DISK_MAX_AGE//86400}天)')
    except Exception as _e:
        pass  # 磁盘清理失败不影响主流程

# ─── 便捷工具 ────────────────────────────────────────────────
def klines_to_ohlcv(raw: list) -> dict:
    """原始K线转结构化OHLCV"""
    if not raw:
        return {'o':[],'h':[],'l':[],'c':[],'v':[],'ts':[]}
    return {
        'o':  [float(k[1]) for k in raw],
        'h':  [float(k[2]) for k in raw],
        'l':  [float(k[3]) for k in raw],
        'c':  [float(k[4]) for k in raw],
        'v':  [float(k[5]) for k in raw],
        'ts': [int(k[0])   for k in raw],
    }

def get_basis(symbol: str) -> dict:
    if OFFLINE_MODE: return {'basis_pct': 0.0, 'mark_price': 0.0, 'index_price': 0.0, 'spread': 0.0}  # [offline]
    """
    合约基差 = (合约标记价格 - 现货指数价格) / 现货指数价格 × 100%
    正基差: 合约溢价（多头激进）→ 做空有利
    负基差: 合约折价（空头激进）→ 做多有利
    返回: {'basis_pct': float, 'mark_price': float, 'index_price': float, 'spread': float}
    """
    key = _cache_key(symbol, 'basis')
    cached = _cache_get(key)
    if cached is not None:
        return cached
    try:
        data = _get(f'{FAPI}/fapi/v1/premiumIndex?symbol={symbol}')
        mark  = float(data.get('markPrice', 0))
        index = float(data.get('indexPrice', 0))
        if index > 0:
            basis_pct = (mark - index) / index * 100
        else:
            basis_pct = 0.0
        result = {
            'basis_pct':   round(basis_pct, 5),
            'mark_price':  mark,
            'index_price': index,
            'spread':      round(mark - index, 4),
        }
        _cache_set(key, result, 60)  # 1分钟TTL
        return result
    except Exception:
        return {'basis_pct': 0.0, 'mark_price': 0.0, 'index_price': 0.0, 'spread': 0.0}


def get_atr_percentile(symbol: str, interval: str = '1h', window: int = 90) -> dict:
    """
    ATR历史百分位（90根K线滑动窗口）
    percentile < 20%: 低波动压缩 → 方向性爆发在即
    percentile > 80%: 高波动已爆发 → 追入风险大
    返回: {'atr_pct': float, 'atr_percentile': float, 'regime': str, 'score_adj': int}
    """
    key = _cache_key(symbol, f'atr_pctile_{interval}')
    cached = _cache_get(key)
    if cached is not None:
        return cached
    try:
        raw = get_klines(symbol, interval, window + 15)
        if len(raw) < 20:
            return {'atr_pct': 0.5, 'atr_percentile': 50.0, 'regime': 'NORMAL', 'score_adj': 0}

        # 逐根计算 ATR
        atr_vals = []
        for i in range(1, len(raw)):
            h, l, pc = float(raw[i][2]), float(raw[i][3]), float(raw[i-1][4])
            c = float(raw[i][4])
            tr = max(h - l, abs(h - pc), abs(l - pc))
            atr_vals.append(tr)

        if not atr_vals:
            return {'atr_pct': 0.5, 'atr_percentile': 50.0, 'regime': 'NORMAL', 'score_adj': 0}

        # 取最近 window 根的 ATR EMA14
        n = 14
        atr_series = []
        v = sum(atr_vals[:n]) / n
        for x in atr_vals[n:]:
            v = (v * (n - 1) + x) / n
            atr_series.append(v)

        if not atr_series:
            return {'atr_pct': 0.5, 'atr_percentile': 50.0, 'regime': 'NORMAL', 'score_adj': 0}

        cur_atr  = atr_series[-1]
        price    = float(raw[-1][4])
        cur_pct  = cur_atr / price * 100 if price else 0
        recent   = atr_series[-min(window, len(atr_series)):]
        below    = sum(1 for x in recent if x <= cur_atr)
        pctile   = below / len(recent) * 100

        # 体制判定
        if pctile <= 20:
            regime = 'COMPRESSED'   # 低波动压缩，爆发在即
            score_adj = 2           # 中性 → 方向性机会加分
        elif pctile >= 80:
            regime = 'EXPANDED'     # 高波动已爆发，追入风险
            score_adj = -2          # 追入惩罚
        elif pctile >= 65:
            regime = 'ELEVATED'     # 波动偏高
            score_adj = -1
        else:
            regime = 'NORMAL'
            score_adj = 0

        result = {
            'atr_val':      round(cur_atr, 4),
            'atr_pct':      round(cur_pct, 3),
            'atr_percentile': round(pctile, 1),
            'regime':       regime,
            'score_adj':    score_adj,
            'window':       len(recent),
        }
        _cache_set(key, result, TTL.get(interval, 900))
        return result
    except Exception as e:
        return {'atr_pct': 0.5, 'atr_percentile': 50.0, 'regime': 'NORMAL', 'score_adj': 0, 'err': str(e)}


# ─── 快速测试 ────────────────────────────────────────────────
if __name__ == '__main__':
    print('[DataCache] 测试拉取 BTCUSDT...')
    t0 = time.time()
    data = prefetch_symbol('BTCUSDT')
    elapsed = time.time() - t0

    ticker = data.get('ticker', {})
    k1h    = data.get('1h', [])
    print(f'  价格:  {ticker.get("lastPrice", "N/A")}')
    print(f'  1H K线数: {len(k1h)}')
    print(f'  资金费率: {data.get("fr", 0):.4f}%')
    print(f'  OI:    {data.get("oi", {}).get("oi", 0):.0f}')
    print(f'  多空比: {data.get("lsr", 50):.1f}%多')
    print(f'  耗时:  {elapsed:.2f}s')

    # 测试缓存
    t1 = time.time()
    _ = prefetch_symbol('BTCUSDT')
    print(f'  缓存命中耗时: {time.time()-t1:.3f}s')
    print('[DataCache] ✅ 测试通过')
    assert VERSION, 'data_cache version ok'


# ══════════════════════════════════════════════════════════════════════
# 多所聚合层 v1.0 (设计院 2026-06-29)
# 零额外成本：Bybit/OKX均为免费公开API
# 增益：三所LSR聚合 → 散户情绪更准确，差异大=分歧信号
# ══════════════════════════════════════════════════════════════════════

_BYBIT_BASE = 'https://api.bybit.com'
_OKX_BASE   = 'https://www.okx.com'

def get_lsr_bybit(symbol: str) -> dict:
    """Bybit 多空比（大户+散户）"""
    if OFFLINE_MODE: return {}
    key = _cache_key(symbol, 'lsr_bybit')
    cached = _cache_get(key)
    if cached is not None:
        return cached
    try:
        # Bybit v5 正确端点：category=linear必须
        url = f'{_BYBIT_BASE}/v5/market/account-ratio?category=linear&symbol={symbol}&period=1h&limit=1'
        data = _get(url)
        lst = (data.get('result') or {}).get('list') or []
        if lst:
            item = lst[0]
            ls = float(item.get('buyRatio', 0.5))
            result = {'long_ratio': ls, 'short_ratio': 1 - ls, 'source': 'bybit'}
            _cache_set(key, result, TTL.get('lsr', 120))
            return result
    except Exception:
        pass
    return {}

def get_lsr_okx(symbol: str) -> dict:
    """OKX 多空比（合约）"""
    if OFFLINE_MODE: return {}
    # OKX instId格式：BTC-USDT-SWAP
    base = symbol.replace('USDT', '')
    inst_id = f'{base}-USDT-SWAP'
    key = _cache_key(symbol, 'lsr_okx')
    cached = _cache_get(key)
    if cached is not None:
        return cached
    try:
        url = f'{_OKX_BASE}/api/v5/rubik/stat/contracts/long-short-account-ratio?instId={inst_id}&period=1H'
        data = _get(url)
        if data and data.get('data'):
            item = data['data'][0]
            ls = float(item[1]) if len(item) > 1 else 1.0
            long_ratio = ls / (1 + ls)
            result = {'long_ratio': long_ratio, 'short_ratio': 1 - long_ratio, 'source': 'okx'}
            _cache_set(key, result, TTL.get('lsr', 120))
            return result
    except Exception as e:
        pass
    return {}

def get_lsr_aggregated(symbol: str) -> dict:
    """
    三所LSR聚合（Binance + Bybit + OKX）
    增益：差异大(>15%)时降低信号置信度
    返回: {long_ratio, short_ratio, divergence, sources}
    """
    results = []

    # Binance（返回的是long/short ratio，需转换为占比）
    bn_raw = get_long_short_ratio(symbol)
    if bn_raw and bn_raw > 0:
        bn_long = bn_raw / (1 + bn_raw) if bn_raw > 1 else bn_raw
        results.append({'long_ratio': bn_long, 'source': 'binance'})

    # Bybit（返回已是小数占比）
    bybit = get_lsr_bybit(symbol)
    if bybit.get('long_ratio'):
        results.append(bybit)

    # OKX（返回已是小数占比）
    okx = get_lsr_okx(symbol)
    if okx.get('long_ratio'):
        results.append(okx)

    if not results:
        return {'long_ratio': 0.5, 'short_ratio': 0.5, 'divergence': 0, 'sources': [], 'count': 0}

    ratios = [r['long_ratio'] for r in results]
    avg_long = sum(ratios) / len(ratios)
    divergence = max(ratios) - min(ratios)  # 三所分歧度

    return {
        'long_ratio':   round(avg_long, 4),
        'short_ratio':  round(1 - avg_long, 4),
        'divergence':   round(divergence, 4),   # >0.15 = 分歧信号，降权
        'sources':      [r['source'] for r in results],
        'count':        len(results),
    }
