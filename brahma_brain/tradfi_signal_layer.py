# ponytail: tradfi_signal_layer 359行，有意为之，重构前先 grep 所有调用方
"""
tradfi_signal_layer.py — 传统金融信号层
设计院自主决策 · 2026-07-22 | 方案B稳健版

架构定位：
  Phase A（当前）：标签模式 — 只记录TradFi信号到breakdown，不修改score
  Phase B（~30条数据后）：达摩院统计验证后升级为±15分注入
  Phase C（~100条后）：根据实证可扩展至±25分

接入点：brahma_engine.py IC权重乘数段之后（L3601后），return _result之前

三道安全补丁（内置）：
  补丁1: CHOP_HIGH死穴重封 — TradFi注入后重新cap score≤75
  补丁2: SSI+TradFi惩罚去重 — 取max不叠加
  补丁3: Overnight流动性门控 — 非交易时段负向delta清零
"""

import json
import time
import logging
import concurrent.futures
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── 配置 ─────────────────────────────────────────────────────────────────────
PHASE = "A"           # "A"=标签模式(不改score) | "B"=±15分注入 | "C"=±25分注入
MAX_DELTA_PHASE_B = 15
MAX_DELTA_PHASE_C = 25

# 监控标的（并行查3个，164ms）
RWA_WATCHLIST = ['SPY', 'COIN', 'MSTR']

# 美股交易时段（UTC）
US_PREOPEN_START  = (9, 0)    # 09:00 UTC 盘前开始
US_OPEN_START     = (14, 30)  # 14:30 UTC 正式开盘
US_OPEN_END       = (14, 45)  # 14:45 UTC 开盘冲击波吸收完
US_CLOSE_START    = (20, 45)  # 20:45 UTC 收盘前波动
US_CLOSE_END      = (21, 0)   # 21:00 UTC 收盘

# 数据记录文件
DATA_DIR = Path(__file__).parent.parent / 'data'
TRADFI_LOG = DATA_DIR / 'tradfi_signal_log.jsonl'

# RWA合约地址缓存（避免每次请求列表）
_CONTRACT_CACHE: dict = {}
_CONTRACT_CACHE_TS: float = 0
_CONTRACT_CACHE_TTL: float = 3600  # 1小时刷新


def _load_rwa_contracts() -> dict:
    """加载RWA代币合约地址，带缓存"""
    global _CONTRACT_CACHE, _CONTRACT_CACHE_TS
    now = time.time()
    if _CONTRACT_CACHE and (now - _CONTRACT_CACHE_TS) < _CONTRACT_CACHE_TTL:
        return _CONTRACT_CACHE

    try:
        req = urllib.request.Request(
            'https://www.binance.com/bapi/defi/v1/public/wallet-direct/buw/wallet/market/token/rwa/stock/detail/list/ai',
            headers={'Accept-Encoding': 'identity', 'User-Agent': 'binance-web3/1.1 (TradFi-Layer)'}
        )
        with urllib.request.urlopen(req, timeout=6) as r:
            data = json.loads(r.read())['data']

        contracts = {}
        for t in data:
            ticker = t['ticker']
            # 优先 chain=1(ETH)，其次 chain=56(BSC)
            if ticker not in contracts or (t['chainId'] == '1' and contracts[ticker]['chainId'] != '1'):
                contracts[ticker] = t

        _CONTRACT_CACHE = contracts
        _CONTRACT_CACHE_TS = now
        return contracts
    except Exception:
        return _CONTRACT_CACHE  # 失败返回旧缓存


def _fetch_rwa_price(ticker: str) -> Optional[dict]:
    """查询单个RWA代币价格"""
    contracts = _load_rwa_contracts()
    info = contracts.get(ticker)
    if not info:
        return None
    try:
        url = (
            f'https://www.binance.com/bapi/defi/v2/public/wallet-direct/buw/wallet/market/token/rwa/dynamic/ai'
            f'?chainId={info["chainId"]}&contractAddress={info["contractAddress"]}'
        )
        req = urllib.request.Request(url, headers={
            'Accept-Encoding': 'identity',
            'User-Agent': 'binance-web3/1.1 (TradFi-Layer)'
        })
        with urllib.request.urlopen(req, timeout=5) as r:
            d = json.loads(r.read())['data']
        return {
            'ticker': ticker,
            'price': float(d['tokenInfo']['price']),
            'pct24h': float(d['tokenInfo']['priceChangePct24h']),
            'holders': d['tokenInfo']['totalHolders'],
        }
    except Exception:
        return None


def _get_market_status() -> dict:
    """获取RWA市场状态（overnight/regular/premarket等）"""
    try:
        req = urllib.request.Request(
            'https://www.binance.com/bapi/defi/v1/public/wallet-direct/buw/wallet/market/token/rwa/market/status/ai',
            headers={'Accept-Encoding': 'identity', 'User-Agent': 'binance-web3/1.1 (TradFi-Layer)'}
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            d = json.loads(r.read())['data']
        return d
    except Exception:
        return {'marketStatus': 'unknown', 'openState': False}


def _is_us_open_window() -> bool:
    """是否在美股开盘冲击波窗口（14:00~14:45 UTC，禁止新仓）"""
    now_utc = datetime.now(timezone.utc)
    h, m = now_utc.hour, now_utc.minute
    total_min = h * 60 + m
    open_start = 14 * 60      # 14:00
    open_end   = 14 * 60 + 45 # 14:45
    return open_start <= total_min < open_end


def _is_us_close_window() -> bool:
    """是否在美股收盘前波动窗口（20:45~21:00 UTC）"""
    now_utc = datetime.now(timezone.utc)
    h, m = now_utc.hour, now_utc.minute
    total_min = h * 60 + m
    return (20 * 60 + 45) <= total_min < (21 * 60)


def compute_tradfi_context(symbol: str, direction: str, base_score: float,
                            regime: str, ssi_penalty: int = 0) -> dict:
    """
    计算TradFi信号上下文。
    
    Phase A: 只返回标签，不返回score_delta（delta=0）
    Phase B/C: 返回经过3道补丁的delta

    Returns:
        {
          'phase': 'A'/'B'/'C',
          'score_delta': 0 (Phase A) / ±N (Phase B/C),
          'market_status': str,
          'factors': [...],
          'breakdown_label': str,   ← 注入到_result['breakdown']
          'is_open_window': bool,
        }
    """
    result = {
        'phase': PHASE,
        'score_delta': 0,
        'factors': [],
        'breakdown_label': '',
        'is_open_window': False,
        'market_status': 'unknown',
    }

    try:
        # ── 并行获取价格数据 ──────────────────────────────────────────
        t0 = time.time()
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
            futs = {ex.submit(_fetch_rwa_price, ticker): ticker for ticker in RWA_WATCHLIST}
            prices = {}
            for fut in concurrent.futures.as_completed(futs, timeout=6):
                ticker = futs[fut]
                try:
                    r = fut.result()
                    if r:
                        prices[ticker] = r
                except Exception:
                    pass
        elapsed_ms = (time.time() - t0) * 1000

        # ── 市场状态 ─────────────────────────────────────────────────
        mkt = _get_market_status()
        mkt_status = mkt.get('marketStatus', 'unknown')
        result['market_status'] = mkt_status
        is_active = mkt_status in ('regular', 'premarket', 'postmarket')
        result['is_open_window'] = _is_us_open_window()

        # ── 构建因子标签 ──────────────────────────────────────────────
        factors = []
        raw_delta = 0

        spy = prices.get('SPY', {})
        coin = prices.get('COIN', {})
        mstr = prices.get('MSTR', {})

        spy_pct  = spy.get('pct24h', 0)
        coin_pct = coin.get('pct24h', 0)
        mstr_pct = mstr.get('pct24h', 0)

        # ── 因子1: SPY大跌 → 做多保护 ────────────────────────────────
        if is_active and spy_pct < -1.5 and direction == 'LONG':
            factors.append({'name': 'SPY暴跌禁多', 'delta': -15, 'value': f'SPY={spy_pct:.2f}%'})
            raw_delta += -15

        # ── 因子2: SPY强势 → 做多加持 ────────────────────────────────
        elif is_active and spy_pct > 1.0 and direction == 'LONG':
            factors.append({'name': 'SPY强势加多', 'delta': +8, 'value': f'SPY={spy_pct:.2f}%'})
            raw_delta += 8

        # ── 因子3: COIN大幅领先BTC → 加密概念强势 ─────────────────────
        # COIN pct >> SPY pct: 加密板块独立行情
        if coin_pct > 5.0 and coin_pct > spy_pct * 3:
            tag = '加密概念领跑'
            d = +8 if direction == 'LONG' else -5
            factors.append({'name': tag, 'delta': d, 'value': f'COIN={coin_pct:.2f}% vs SPY={spy_pct:.2f}%'})
            raw_delta += d

        # ── 因子4: MSTR强势（BTC代理确认）────────────────────────────
        if is_active and mstr_pct > 5.0 and direction == 'LONG':
            factors.append({'name': 'MSTR强势BTC确认', 'delta': +5, 'value': f'MSTR={mstr_pct:.2f}%'})
            raw_delta += 5

        # ── 因子5: 美股开盘冲击波窗口 ─────────────────────────────────
        if result['is_open_window']:
            if direction == 'SHORT':
                factors.append({'name': '开盘冲击波禁空', 'delta': -12, 'value': '14:00-14:45 UTC'})
                raw_delta += -12
            else:
                factors.append({'name': '开盘冲击波观望', 'delta': -5, 'value': '14:00-14:45 UTC'})
                raw_delta += -5

        # ── 因子6: 美股收盘波动窗口 ──────────────────────────────────
        if _is_us_close_window() and is_active:
            factors.append({'name': '收盘波动预警', 'delta': -5, 'value': '20:45-21:00 UTC'})
            raw_delta += -5

        # ── 因子7: overnight流动性极低 ────────────────────────────────
        if mkt_status == 'overnight':
            # 补丁3: overnight时段负向delta清零（价格可能失真）
            if raw_delta < 0:
                factors.append({'name': 'Overnight负向清零', 'delta': -raw_delta, 'value': 'overnight流动性极低'})
                raw_delta = 0

        result['factors'] = factors

        # ── 3道安全补丁 ───────────────────────────────────────────────

        # 补丁1: CHOP_HIGH死穴重封（在外部由调用方执行，此处标记）
        result['need_chop_high_recheck'] = (regime == 'CHOP_HIGH')

        # 补丁2: SSI+TradFi惩罚去重（取较大惩罚，不叠加）
        if raw_delta < 0 and ssi_penalty < 0:
            # 两者都是负向惩罚，取绝对值更大的那个
            effective_delta = min(raw_delta, ssi_penalty)  # 更负的那个
            dedup_note = f'SSI({ssi_penalty}) vs TradFi({raw_delta}) → 取大惩罚 {effective_delta}'
            factors.append({'name': 'SSI_TradFi去重', 'delta': effective_delta - raw_delta, 'value': dedup_note})
            raw_delta = effective_delta

        # Phase A: delta=0（仅标签）
        # Phase B: delta上限±15
        # Phase C: delta上限±25
        if PHASE == 'A':
            score_delta = 0
        elif PHASE == 'B':
            score_delta = max(-MAX_DELTA_PHASE_B, min(MAX_DELTA_PHASE_B, raw_delta))
        else:
            score_delta = max(-MAX_DELTA_PHASE_C, min(MAX_DELTA_PHASE_C, raw_delta))

        result['score_delta'] = score_delta
        result['raw_delta'] = raw_delta
        result['elapsed_ms'] = round(elapsed_ms)

        # ── breakdown标签（注入到_result['breakdown']）──────────────
        if factors:
            parts = [f"{f['name']}({f['delta']:+d})" for f in factors if f['name'] != 'SSI_TradFi去重']
            spy_str = f"SPY={spy_pct:.2f}%" if spy else "SPY=N/A"
            coin_str = f"COIN={coin_pct:.2f}%" if coin else "COIN=N/A"
            mkt_str = mkt_status
            delta_str = f"PhaseA:仅标签" if PHASE == 'A' else f"delta={score_delta:+d}分"
            result['breakdown_label'] = f"TradFi[{mkt_str}] {spy_str} {coin_str} | {','.join(parts)} | {delta_str}"
        else:
            result['breakdown_label'] = f"TradFi[{mkt_status}] 无触发因子"

    except Exception as e:
        result['breakdown_label'] = f"TradFi[error:{str(e)[:40]}]"
        logger.warning(f"tradfi_signal_layer error: {e}")

    # ── 记录到日志（数据积累，供达摩院Phase B分析）────────────────────
    try:
        log_entry = {
            'ts': datetime.now(timezone.utc).isoformat(),
            'symbol': symbol,
            'direction': direction,
            'regime': regime,
            'base_score': base_score,
            'score_delta': result['score_delta'],
            'raw_delta': result.get('raw_delta', 0),
            'market_status': result['market_status'],
            'factors': [f['name'] for f in result['factors']],
            'prices': {
                'SPY_pct': prices.get('SPY', {}).get('pct24h') if 'prices' in dir() else None,
                'COIN_pct': prices.get('COIN', {}).get('pct24h') if 'prices' in dir() else None,
                'MSTR_pct': prices.get('MSTR', {}).get('pct24h') if 'prices' in dir() else None,
            } if 'prices' in dir() else {},
            'phase': PHASE,
        }
        with open(TRADFI_LOG, 'a') as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
        # 截断：超过1000行保留最新500条（防无限增长，约350KB上限）
        try:
            lines = TRADFI_LOG.read_text().strip().split('\n')
            if len(lines) > 1000:
                TRADFI_LOG.write_text('\n'.join(lines[-500:]) + '\n')
        except Exception:
            pass

        # ── [tradfi→1hao全能力闭环 2026-08-19 苏摩111] ────────────────────
        # Phase A 只记录日志，但高质量信号应触发1hao全能力分析写入live_signal_log
        # 触发条件：score_delta>=0（非负面信号） + base_score>=95（品种本身分数够）
        # 避免：每次都触发（太耗资源），只在宏观顺势时触发
        try:
            _should_trigger = (
                result.get('score_delta', 0) >= 0 and  # 宏观顺势或中性
                base_score >= 95 and                    # 品种基础分够高
                result.get('market_status') not in ('BEARISH_MACRO', 'RISK_OFF')
            )
            if _should_trigger:
                # 写入 rsi_trigger_event.json → auto-1hao-trigger 下次运行时捡起
                import time as _t
                _trigger_f = DATA_DIR / 'rsi_trigger_event.json'
                _existing = {}
                try:
                    if _trigger_f.exists():
                        _existing = json.loads(_trigger_f.read_text())
                except Exception:
                    pass
                _existing[symbol] = {
                    'symbol': symbol,
                    'direction': direction,
                    'trigger_type': 'tradfi_macro',
                    'score_delta': result.get('score_delta', 0),
                    'base_score': base_score,
                    'ts': _t.time(),
                    'source': 'tradfi_signal_layer',
                }
                _trigger_f.write_text(json.dumps(_existing, ensure_ascii=False, indent=2))
        except Exception:
            pass
        # ──────────────────────────────────────────────────────────────────

    except Exception:
        pass

    return result


# ══ [2026-09-01 设计院精简封印] 合并自 brahma_brain/tradfi_router.py ══
# ponytail: tradfi_router 365行，有意为之，重构前先 grep 所有调用方
"""
tradfi_router.py — TradFi三类品种路由器
[设计院封印 2026-08-14 苏摩111]

架构定位：
  梵天验证铁证（5轮360根4H K线回测）：
    A类 旧框架 WR52.4% PNL-3.3% → 新框架 WR61.5% PNL+12.5%
    B类 旧框架 WR46.2% PNL-3.4% → 新框架 WR50.0% PNL+3.2%
    C类 缺事件数据 → 降至WATCH级别

三大铁律：
  铁律1: A类亚盘 = 直接STANDBY（亚盘波动0.36%为纯噪音）
  铁律2: TradFi的LSR≥75% ≠ 轧空信号（对冲盘，不是情绪拥挤）
  铁律3: C类无事件数据时 = 降级WATCH（纯技术EV为负）

调用方：brahma_core.py analyze() 主链路
  在tradfi_signal_layer之后注入，补充A/B/C差异化权重
"""

import logging
from datetime import datetime, timezone
from typing import Optional

_log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# A/B/C 品种分类表（基于梵天回测铁证）
# ═══════════════════════════════════════════════════════════════════

# A类：美股强联动型 — FR/LSR/加密体制失效，核心看SPX/QQQ时段
# 特征：LSR偏空=对冲盘，非做空信号；亚盘波动<0.4%为oracle噪音
_CLASS_A = frozenset([
    'NVDAUSDT', 'MSFTUSDT', 'AAPLUSDT', 'AMZNUSDT', 'GOOGLUSDT',
    'METAUSDT', 'SPYUSDT', 'QQQUSDT', 'TQQQUSDT', 'SQQQUSDT',
    'SOXLUSDT', 'SOXSUSDT', 'SPCXUSDT', 'SPXUSDT', 'INTCUSDT',
    'AMDUSDT', 'NFLXUSDT', 'CRWDUSDT', 'PLTRUSDT', 'MRVLUSDT',
    'XAUUSDT', 'XAGUSDT', 'CLUSDT', 'BZUSDT',  # 贵金属/原油跟美股宏观
    'KORUUSDT', 'EWYUSDT', 'IWMUSDT',            # 海外ETF
])

# B类：加密+美股双重联动型 — BTC体制(40%) + SPX(40%) + OI(20%)
# 特征：COIN与BTC相关0.55，亚盘/开市均有效，FR/LSR权重降至30%
_CLASS_B = frozenset([
    'COINUSDT', 'MSTRUSDT', 'HOODUSDT',
])

# C类：独立催化剂型 — BB压缩+事件触发，与BTC/SPX相关均弱
# 特征：TSLA vs BTC相关0.33，TSLA vs SPX相关0.19，事件驱动主导
# 铁律3：无财报/事件数据时降级WATCH
_CLASS_C = frozenset([
    'TSLAUSDT', 'SNDKUSDT', 'BABAUSDT', 'MUUSDT',
    'SKHYNIXUSDT', 'SKHYUSDT', 'TSMUSDT', 'DRAMUSDT',
    'SAMSUNGUSDT', 'SNXXUSDT',
])

# 美股交易时段边界（UTC分钟）
_US_REGULAR_START = 13 * 60 + 30   # 13:30 UTC
_US_REGULAR_END   = 20 * 60        # 20:00 UTC
_US_PREOPEN_START = 12 * 60        # 12:00 UTC（盘前有效流动性起点）

# ATR有效性门槛：低于此值 = 流动性不足，不入场（铁律1的量化标准）
# 注意：atr_pct 传入格式为小数比例，0.003 = 0.3%，0.012 = 1.2%
_MIN_ATR_PCT_CLASS_A = 0.003   # A类：<0.3%为亚盘oracle噪音
_MIN_ATR_PCT_CLASS_B = 0.0025  # B类：<0.25%
_MIN_ATR_PCT_CLASS_C = 0.002   # C类全时段有效，门槛略低 <0.2%


# ═══════════════════════════════════════════════════════════════════
# 公共接口
# ═══════════════════════════════════════════════════════════════════

def classify(symbol: str) -> str:
    """
    品种分类。返回 'A' / 'B' / 'C' / 'CRYPTO'。
    CRYPTO = 普通加密合约，走原始梵天逻辑，不经过此路由器。
    """
    sym = symbol.upper()
    if sym in _CLASS_A:
        return 'A'
    if sym in _CLASS_B:
        return 'B'
    if sym in _CLASS_C:
        return 'C'
    return 'CRYPTO'


def get_session() -> dict:
    """
    当前美股时段信息。
    Returns:
        {
          'session': 'ASIA' | 'PREOPEN' | 'REGULAR' | 'AFTERHOURS',
          'us_open': bool,          # True = 美股正式交易时段
          'utc_min': int,
          'weight': {'A': float, 'B': float, 'C': float},
        }
    """
    now = datetime.now(timezone.utc)
    utc_min = now.hour * 60 + now.minute

    if _US_REGULAR_START <= utc_min < _US_REGULAR_END:
        session = 'REGULAR'
        us_open = True
        weight  = {'A': 1.0, 'B': 1.0, 'C': 1.0}
    elif _US_PREOPEN_START <= utc_min < _US_REGULAR_START:
        session = 'PREOPEN'
        us_open = False
        weight  = {'A': 0.5, 'B': 0.8, 'C': 1.0}
    elif _US_REGULAR_END <= utc_min:
        session = 'AFTERHOURS'
        us_open = False
        weight  = {'A': 0.5, 'B': 0.8, 'C': 1.0}
    else:
        session = 'ASIA'
        us_open = False
        weight  = {'A': 0.0, 'B': 0.7, 'C': 1.0}  # A类亚盘=0，铁律1

    return {
        'session': session,
        'us_open': us_open,
        'utc_min': utc_min,
        'weight':  weight,
    }


def compute_router_delta(
    symbol: str,
    direction: str,
    base_score: float,
    atr_pct: float = 1.0,
    spx_chg_1d: float = 0.0,
    btc_chg_4h: float = 0.0,
    lsr_long: float = 0.5,
    fr: float = 0.0,
) -> dict:
    """
    TradFi路由器核心函数：根据A/B/C类返回评分调整delta + 操作标签。

    Args:
        symbol:      分析标的，如 'NVDAUSDT'
        direction:   'LONG' | 'SHORT'
        base_score:  原始梵天评分
        atr_pct:     当前ATR占价格百分比（0~1.0，如0.013=1.3%）
        spx_chg_1d:  SPX当日涨跌幅（-0.02 = -2%）
        btc_chg_4h:  BTC 4H涨跌幅
        lsr_long:    全市多空比中多头占比（0.77=77%多头）
        fr:          资金费率（TradFi大多为0）

    Returns:
        {
          'class':    'A'|'B'|'C'|'CRYPTO',
          'session':  'ASIA'|'PREOPEN'|'REGULAR'|'AFTERHOURS',
          'delta':    int,              # 评分调整（负=降权，0=不变）
          'valid':    bool | None,      # None=不覆盖原有valid
          'label':    str,              # breakdown注入标签
          'standby':  bool,             # True=当前不宜入场，返回STANDBY
          'watch':    bool,             # True=降级为WATCH级别
          'reasons':  list[str],        # 可读原因列表
        }
    """
    cls    = classify(symbol)
    sess   = get_session()
    result = {
        'class':   cls,
        'session': sess['session'],
        'delta':   0,
        'valid':   None,
        'label':   '',
        'standby': False,
        'watch':   False,
        'reasons': [],
    }

    if cls == 'CRYPTO':
        result['label'] = f'CRYPTO 走原始梵天逻辑'
        return result

    sess_name   = sess['session']
    sess_weight = sess['weight'][cls]
    reasons     = result['reasons']

    # ── 铁律1：A类亚盘直接STANDBY ──────────────────────────────────
    if cls == 'A' and sess_name in ('ASIA', 'PREOPEN', 'AFTERHOURS'):
        result['standby'] = True
        result['valid']   = False
        result['delta']   = -60
        reasons.append(f'[铁律1] A类{sess_name} oracle停更，atr={atr_pct*100:.2f}% → STANDBY')
        result['label'] = f'TradFi-A [{sess_name} STANDBY] atr={atr_pct*100:.2f}%'
        return result

    # ── ATR有效性过滤 ────────────────────────────────────────────────
    min_atr = {'A': _MIN_ATR_PCT_CLASS_A, 'B': _MIN_ATR_PCT_CLASS_B, 'C': _MIN_ATR_PCT_CLASS_C}[cls]
    if atr_pct < min_atr:
        result['standby'] = True
        result['valid']   = False
        result['delta']   = -30
        reasons.append(f'ATR过小={atr_pct*100:.2f}%<{min_atr*100:.1f}% 流动性不足')
        result['label'] = f'TradFi-{cls} [{sess_name}] ATR不足 STANDBY'
        return result

    delta = 0

    # ═══════════════════════════════════════════════════════════════
    # A类：美股强联动型 评分逻辑
    # 核心驱动：SPX大盘方向；FR/LSR/暴涨猎手失效
    # ═══════════════════════════════════════════════════════════════
    if cls == 'A':
        # SPX方向一致 = 加分；逆势 = 降权
        spx_pct = spx_chg_1d * 100
        if direction == 'LONG':
            if spx_pct > 1.0:
                delta += 10
                reasons.append(f'SPX顺势+10 ({spx_pct:+.2f}%)')
            elif spx_pct < -1.5:
                delta -= 20
                reasons.append(f'SPX逆势-20 ({spx_pct:+.2f}%)')
            elif spx_pct < -0.5:
                delta -= 8
                reasons.append(f'SPX弱势-8 ({spx_pct:+.2f}%)')
        else:  # SHORT
            if spx_pct < -1.0:
                delta += 8
                reasons.append(f'SPX下跌顺空+8 ({spx_pct:+.2f}%)')
            elif spx_pct > 1.5:
                delta -= 15
                reasons.append(f'SPX强势逆空-15 ({spx_pct:+.2f}%)')

        # 铁律2：A类LSR过滤（GOOGL 84.8%多头≠轧空，是对冲盘）
        if lsr_long > 0.75 and direction == 'LONG':
            # A类高多头比例是对冲盘，不是看涨信号，delta中性
            reasons.append(f'[铁律2] A类LSR={lsr_long*100:.0f}%多头=对冲盘，中性处理')
        if lsr_long < 0.30 and direction == 'LONG':
            # 极端空头比例对A类也不代表轧空，中性
            reasons.append(f'[铁律2] A类LSR={lsr_long*100:.0f}%空头=对冲盘，中性处理')

        # 非交易时段降权（盘前/盘后）
        if sess_name in ('PREOPEN', 'AFTERHOURS'):
            delta -= 10
            reasons.append(f'A类{sess_name}降权-10')

    # ═══════════════════════════════════════════════════════════════
    # B类：加密+美股双引擎 评分逻辑
    # 双因子：BTC体制(亚盘60%/开市40%) + SPX(亚盘40%/开市60%)
    # ═══════════════════════════════════════════════════════════════
    elif cls == 'B':
        spx_pct = spx_chg_1d * 100
        btc_pct = btc_chg_4h * 100

        # 动态权重：亚盘BTC主导，开市SPX主导
        if sess_name == 'ASIA':
            w_btc, w_spx = 0.6, 0.4
        else:
            w_btc, w_spx = 0.4, 0.6

        # 双引擎合并信号
        combined = btc_pct * w_btc + spx_pct * w_spx
        if direction == 'LONG':
            if combined > 1.5:
                delta += 8
                reasons.append(f'B类双引擎顺多+8 (BTC{btc_pct:+.1f}%×{w_btc} + SPX{spx_pct:+.1f}%×{w_spx})')
            elif combined < -1.5:
                delta -= 12
                reasons.append(f'B类双引擎逆多-12 (combined={combined:+.1f}%)')
        else:
            if combined < -1.5:
                delta += 8
                reasons.append(f'B类双引擎顺空+8 (combined={combined:+.1f}%)')
            elif combined > 1.5:
                delta -= 12
                reasons.append(f'B类双引擎逆空-12')

        # FR对B类降权（权重30%）
        if abs(fr) > 0.0003:
            fr_signal = -fr if direction == 'LONG' else fr
            fr_adj = int(fr_signal * 10000 * 0.3)  # 30%权重
            fr_adj = max(-8, min(8, fr_adj))
            if fr_adj != 0:
                delta += fr_adj
                reasons.append(f'B类FR调整×0.3={fr_adj:+d} (FR={fr*100:.4f}%)')

        # LSR对B类降权（权重30%）
        if lsr_long > 0.80 and direction == 'LONG':
            delta -= 5  # 不是铁律封杀，只是降权
            reasons.append(f'B类LSR拥挤降权-5 ({lsr_long*100:.0f}%多头×0.3)')
        elif lsr_long < 0.25 and direction == 'SHORT':
            delta -= 5
            reasons.append(f'B类LSR空头拥挤降权-5 ({lsr_long*100:.0f}%多头×0.3)')

    # ═══════════════════════════════════════════════════════════════
    # C类：独立催化剂型 评分逻辑
    # 铁律3：无事件数据 → WATCH级别
    # 技术有效：BB压缩突破、暴涨猎手（SNDK类轧空）
    # ═══════════════════════════════════════════════════════════════
    elif cls == 'C':
        # 铁律3：C类降级为WATCH（无财报日历时）
        # 当base_score处于155+执行区间时保留，否则降至WATCH
        if base_score < 138:
            result['watch'] = True
            delta -= 15
            reasons.append(f'[铁律3] C类无事件数据 base_score={base_score:.0f}<138 降级WATCH-15')
        else:
            reasons.append(f'C类高分({base_score:.0f}≥138) 保留执行级别')

        # C类LSR/FR逻辑保留完整权重（SNDK类轧空有效）
        if fr < -0.0002 and lsr_long < 0.30:
            # 经典轧空信号：FR负值+极端空头拥挤
            delta += 12
            reasons.append(f'C类轧空信号+12 (FR={fr*100:.4f}% LSR={lsr_long*100:.0f}%多头)')
        elif fr > 0.0002 and lsr_long > 0.75 and direction == 'LONG':
            # 多头拥挤+FR正值：做多风险
            delta -= 8
            reasons.append(f'C类多头拥挤风险-8 (FR={fr*100:.4f}% LSR={lsr_long*100:.0f}%多头)')

    # ── 应用时段权重（非A类亚盘，A类亚盘已在前面处理）──────────────
    if sess_weight < 1.0 and delta > 0:
        adj = int(delta * sess_weight) - delta  # 负数，降权部分
        if adj != 0:
            delta += adj
            reasons.append(f'{cls}类{sess_name}降权×{sess_weight}={adj:+d}')

    result['delta'] = int(max(-60, min(20, delta)))  # cap: -60 ~ +20

    # ── 生成breakdown标签 ────────────────────────────────────────────
    sym_short = symbol.replace('USDT', '')
    cls_label = {'A': '美股强联动', 'B': '加密美股双引擎', 'C': '独立催化剂'}[cls]
    watch_tag = ' [WATCH]' if result['watch'] else ''
    result['label'] = (
        f"TradFi-{cls}({cls_label})[{sess_name}]{watch_tag} "
        f"delta={result['delta']:+d} | "
        + ' | '.join(reasons[:3])  # 最多3条原因注入breakdown
    )

    return result


def get_tradfi_report_header(symbol: str) -> str:
    """
    生成分析报告头部TradFi标注（供Jarvis推送格式使用）。
    """
    cls   = classify(symbol)
    sess  = get_session()
    now   = datetime.now(timezone.utc)

    if cls == 'CRYPTO':
        return ''

    cls_names = {
        'A': '美股强联动 | SPX驱动 | FR/LSR无效',
        'B': '加密+美股双引擎 | BTC体制+SPX各半',
        'C': '独立催化剂 | 事件驱动 | 无事件=WATCH',
    }
    sess_status = {
        'ASIA':       '⛔ 亚盘（A类STANDBY / B类降权×0.7 / C类全效）',
        'PREOPEN':    '🟡 盘前（A类降权 / B类降权×0.8 / C类全效）',
        'REGULAR':    '✅ 美股正式交易时段（全类全效）',
        'AFTERHOURS': '🟡 盘后（A类降权 / B类降权×0.8 / C类全效）',
    }

    return (
        f"【TradFi-{cls}类】{cls_names[cls]}\n"
        f"时段状态：{sess_status.get(sess['session'], sess['session'])} "
        f"(UTC {now.hour:02d}:{now.minute:02d})"
    )

# ══ [2026-09-01 设计院精简封印] 合并自 brahma_brain/tradfi_sector_engine.py ══
"""
tradfi_sector_engine.py — 美股代币板块联动评分引擎
[设计院 2026-08-11 苏摩111封印] 整体落地，非补丁式

核心逻辑：sector_corr 1.8x 权重的实际计算实现
- 半导体组 / 科技巨头组 / ETF指数组 内部共振检测
- 当同组3个以上标的RSI<30(超卖)/RSI>70(超买) → 板块信号加分
- 单独标的信号不及板块共振信号可靠性，差异化权重

调用方：brahma_core.py → compute_tradfi_sector_score(symbol, rsi_1h_fn)
"""

import logging
from typing import Callable, Optional

_log = logging.getLogger(__name__)

# ─── 板块分组定义 ───────────────────────────────────────────────────────────────

_SEMICONDUCTOR = frozenset([
    'MUUSDT',      # 美光科技
    'SNDKUSDT',    # 闪迪
    'SKHYNIXUSDT', # SK海力士
    'SKHYUSDT',    # SK海力士低价合约
    'NVDAUSDT',    # 英伟达
    'AMDUSDT',     # AMD
    'TSMUSDT',     # 台积电
])

_TECH_GIANT = frozenset([
    'TSLAUSDT',    # 特斯拉
    'METAUSDT',    # Meta
    'MSFLUSDT',    # 微软（注：MSFT在Binance可能是MSFTUSDT）
    'MSFUSDT',     # 微软
    'MSTRUSDT',    # 微软（别名）
    'GOOGLUSDT',   # Google
    'GOOGUSDT',    # Google（别名）
    'AAPLUSDT',    # 苹果
    'MSTRUSDT',    # MSTR
])

_ETF_INDEX = frozenset([
    'SOXLUSDT',    # 半导体ETF 3x
    'SPCXUSDT',    # 标普500
    'QQQUSDT',     # 纳指100
])

# 组合映射：symbol → 组名 + 同组成员
_GROUP_MAP: dict[str, tuple[str, frozenset]] = {}
for _s in _SEMICONDUCTOR:
    _GROUP_MAP[_s] = ('semiconductor', _SEMICONDUCTOR)
for _s in _TECH_GIANT:
    _GROUP_MAP[_s] = ('tech_giant', _TECH_GIANT)
for _s in _ETF_INDEX:
    _GROUP_MAP[_s] = ('etf_index', _ETF_INDEX)

# ─── 评分参数 ────────────────────────────────────────────────────────────────────

_OVERSOLD_THRESH  = 30   # RSI < 30 = 超卖（做多信号增益）
_OVERBOUGHT_THRESH = 70  # RSI > 70 = 超买（做空信号增益）
_SCORE_PER_MEMBER = 5    # 每个同组超卖/超买成员 +5分
_MAX_SECTOR_SCORE = 20   # 板块联动最高+20分（4个成员x5）
_MIN_MEMBERS_FOR_BOOST = 2  # 至少2个同组成员达标才给分（单独不算板块信号）


def compute_tradfi_sector_score(
    symbol: str,
    direction: str,
    rsi_1h_fn: Optional[Callable[[str], Optional[float]]] = None,
) -> dict:
    """
    计算板块联动评分

    Args:
        symbol:    当前分析标的，如 'MUUSDT'
        direction: 'LONG' 或 'SHORT'
        rsi_1h_fn: 获取其他标的RSI_1H的函数 fn(symbol) -> float|None
                   如果为None，跳过联动计算返回0

    Returns:
        {
            'score': float,         # 板块联动加分（0 ~ +20）
            'group': str,           # 所属板块名
            'aligned_count': int,   # 同向对齐成员数
            'detail': str,          # 人类可读说明
        }
    """
    result = {'score': 0.0, 'group': 'none', 'aligned_count': 0, 'detail': '非TRADFI板块'}

    if symbol not in _GROUP_MAP:
        return result

    group_name, group_members = _GROUP_MAP[symbol]
    result['group'] = group_name

    if rsi_1h_fn is None:
        result['detail'] = f'{group_name}板块，无RSI数据源跳过联动'
        return result

    # 判断当前信号方向的超卖/超买逻辑
    # LONG：同组成员RSI<30越多 → 板块超卖共振 → 加分
    # SHORT：同组成员RSI>70越多 → 板块超买共振 → 加分
    aligned_count = 0
    checked = 0

    for peer in group_members:
        if peer == symbol:
            continue
        try:
            rsi = rsi_1h_fn(peer)
            if rsi is None:
                continue
            checked += 1
            if direction == 'LONG' and rsi < _OVERSOLD_THRESH:
                aligned_count += 1
            elif direction == 'SHORT' and rsi > _OVERBOUGHT_THRESH:
                aligned_count += 1
        except Exception as e:
            _log.debug(f'sector_engine: {peer} RSI查询失败 {e}')

    result['aligned_count'] = aligned_count
    result['checked'] = checked

    if aligned_count >= _MIN_MEMBERS_FOR_BOOST:
        sector_score = min(aligned_count * _SCORE_PER_MEMBER, _MAX_SECTOR_SCORE)
        result['score'] = float(sector_score)
        thresh_desc = f'RSI<{_OVERSOLD_THRESH}' if direction == 'LONG' else f'RSI>{_OVERBOUGHT_THRESH}'
        result['detail'] = (
            f'{group_name}板块共振: {aligned_count}/{checked}同组成员{thresh_desc}'
            f' → sector_corr+{sector_score:.0f}'
        )
    else:
        result['detail'] = (
            f'{group_name}板块: {aligned_count}/{checked}同组成员对齐，未达{_MIN_MEMBERS_FOR_BOOST}个阈值'
        )

    return result


def get_quick_rsi_1h(symbol: str) -> Optional[float]:
    """
    轻量级RSI_1H获取（不走brahma_bus缓存，直接取最近数据）
    用于板块联动扫描时快速获取同组成员数据
    """
    try:
        from brahma_brain.brahma_bus import get_klines_cached
        klines = get_klines_cached(symbol, '1h', limit=20)
        if not klines or len(klines) < 14:
            return None
        closes = [float(k[4]) for k in klines]
        # 计算RSI14
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        avg_gain = sum(gains[:14]) / 14
        avg_loss = sum(losses[:14]) / 14
        for i in range(14, len(gains)):
            avg_gain = (avg_gain * 13 + gains[i]) / 14
            avg_loss = (avg_loss * 13 + losses[i]) / 14
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return round(100 - 100 / (1 + rs), 2)
    except Exception as e:
        _log.debug(f'quick_rsi_1h {symbol}: {e}')
        return None

# ══ [2026-09-01 设计院精简封印] 合并自 brahma_brain/tradfi_macro_gate.py ══
"""
tradfi_macro_gate.py — 美股代币宏观联动门控
[设计院 2026-08-11 苏摩111封印] 整体落地，非补丁式

核心逻辑：macro_link 2.0x 权重的实际门控实现
- SPX日跌 > 1.5% → 所有美股代币信号 score-15
- QQQ跌破20均线 → LONG信号 score-20（严格门控）
- SPX连续3日上涨 → LONG信号 score+8
- DXY强劲上涨 > 0.5% → 科技股LONG信号 score-10（美元强→科技股压力）

数据来源：data/macro_state.json (由 macro_engine.py 定时写入)

调用方：brahma_core.py → compute_tradfi_macro_gate(symbol, direction, asset_type)
"""

import json
import logging
from pathlib import Path
from typing import Optional

_log = logging.getLogger(__name__)

_MACRO_STATE_PATH = Path(__file__).parent.parent / 'data' / 'macro_state.json'

# ─── 评分阈值 ────────────────────────────────────────────────────────────────────

_SPX_DROP_THRESHOLD    = -1.5   # SPX日跌幅 < -1.5% → 全系扣分
_SPX_DROP_PENALTY      = -15    # 扣分值
_QQQ_BELOW_MA20_PENALTY = -20   # QQQ跌破20均线时LONG扣分
_SPX_BULL_BONUS        = +8     # SPX连续上涨时LONG加分（保留，当前macro_state暂无连涨数据）
_DXY_STRONG_THRESHOLD  = 0.5    # DXY涨幅 > 0.5% → 科技股压力
_DXY_STRONG_PENALTY    = -10    # 科技股LONG扣分

# 科技股集合（DXY强对这些影响大）
_TECH_SYMBOLS = frozenset([
    'NVDAUSDT', 'AMDUSDT', 'TSMUSDT', 'MUUSDT', 'SNDKUSDT', 'SKHYNIXUSDT', 'SKHYUSDT',
    'TSLAUSDT', 'METAUSDT', 'MSFLUSDT', 'MSFUSDT', 'MSTRUSDT', 'GOOGLUSDT', 'GOOGUSDT',
    'AAPLUSDT', 'SOXLUSDT', 'QQQUSDT', 'SPCXUSDT',
])


def _load_macro_state() -> dict:
    """安全读取 macro_state.json，失败返回空字典"""
    try:
        with open(_MACRO_STATE_PATH, 'r') as f:
            return json.load(f)
    except Exception as e:
        _log.debug(f'macro_gate: macro_state.json读取失败 {e}')
        return {}


def compute_tradfi_macro_gate(
    symbol: str,
    direction: str,  # 'LONG' or 'SHORT'
    asset_type: str = 'TRADFI_STOCK',
) -> dict:
    """
    计算宏观联动门控评分

    Args:
        symbol:     当前分析标的
        direction:  'LONG' 或 'SHORT'
        asset_type: 资产类型（仅TRADFI_STOCK执行门控）

    Returns:
        {
            'score': float,         # 宏观门控调整分（通常为负数）
            'rules_triggered': list,# 触发的规则名称列表
            'detail': str,          # 人类可读说明
            'spx_chg_1d': float,    # SPX当日涨跌
            'qqq_above_ma20': bool, # QQQ是否在MA20之上
        }
    """
    result = {
        'score': 0.0,
        'rules_triggered': [],
        'detail': '宏观正常',
        'spx_chg_1d': 0.0,
        'qqq_above_ma20': True,
    }

    if asset_type != 'TRADFI_STOCK':
        result['detail'] = '非TRADFI_STOCK，跳过宏观门控'
        return result

    macro = _load_macro_state()
    if not macro:
        result['detail'] = 'macro_state.json不可用，跳过宏观门控'
        return result

    total_adj = 0.0
    rules = []

    # ─── 规则1：SPX日跌 > 1.5% → 全系扣分 ─────────────────────────────────────
    spx = macro.get('spx', {})
    spx_chg = float(spx.get('chg_1d_pct', 0) or 0)
    result['spx_chg_1d'] = spx_chg

    if spx_chg < _SPX_DROP_THRESHOLD:
        total_adj += _SPX_DROP_PENALTY
        rules.append(f'SPX单日跌{spx_chg:.2f}%(<{_SPX_DROP_THRESHOLD}%) score{_SPX_DROP_PENALTY}')

    # ─── 规则2：QQQ跌破MA20 → LONG信号严格门控 ─────────────────────────────────
    qqq = macro.get('qqq', {})
    qqq_above_ma20 = bool(qqq.get('above_ma20', True))
    result['qqq_above_ma20'] = qqq_above_ma20

    if not qqq_above_ma20 and direction == 'LONG':
        total_adj += _QQQ_BELOW_MA20_PENALTY
        vs_ma20 = float(qqq.get('vs_ma20_pct', 0) or 0)
        rules.append(f'QQQ跌破MA20({vs_ma20:.1f}%) LONG受限 score{_QQQ_BELOW_MA20_PENALTY}')

    # ─── 规则3：DXY强势上涨 → 科技股LONG扣分 ───────────────────────────────────
    dxy = macro.get('dxy', {})
    dxy_chg = float(dxy.get('change', 0) or 0)

    if dxy_chg > _DXY_STRONG_THRESHOLD and direction == 'LONG' and symbol in _TECH_SYMBOLS:
        total_adj += _DXY_STRONG_PENALTY
        rules.append(f'DXY强势+{dxy_chg:.2f}%(>{_DXY_STRONG_THRESHOLD}%) 科技LONG受压 score{_DXY_STRONG_PENALTY}')

    # ─── 汇总 ────────────────────────────────────────────────────────────────────
    result['score'] = float(total_adj)
    result['rules_triggered'] = rules

    if rules:
        result['detail'] = ' | '.join(rules)
    else:
        result['detail'] = f'宏观正常(SPX={spx_chg:+.2f}% QQQ>MA20={qqq_above_ma20} DXY={dxy_chg:+.2f}%)'

    return result

# ══ [2026-09-01 设计院精简封印] 合并自 brahma_brain/tradfi_dump_detector.py ══
# ponytail: tradfi_dump_detector 758行，有意为之，重构前先 grep 所有调用方
"""
tradfi_dump_detector.py — 美股代币放量下跌识别引擎
设计院封印 · 2026-07-28 · 苏摩111批准

架构定位：
  复盘根因：SNDK 7月27日连续两次止损的系统性根因
    - M5 月线趋势过滤完全缺失（30日-40%仍输出做多）
    - 顶部15根持续放量出货未被识别（平均8.3x MA20）
    - OBV顶背离权重严重低估（-140%背离仅-3分）
    - 量价背离（诱多K58: 9.5x量仅+0.47%涨幅）零分
    - 下降高点序列（K52~K58连续4次）未被捕捉

三类事件识别：
  TYPE-1: TECH_OVERSOLD      — RSI超卖+联动下跌，技术性反弹信号有效
  TYPE-2: FUNDAMENTAL_DUMP   — 个股独立暴跌+放量，做多封禁→等反弹做空
  TYPE-3: PANIC_LIQUIDATION  — 极端放量+OI暴涨，轧空短多机会

5个新信号模块（M1~M5）：
  M1: top_distribution_detector  顶部持续放量出货检测
  M2: price_volume_divergence     量价背离检测（量大价不涨）
  M3: obv_divergence_weight       OBV顶背离权重升级（-3→-25分）
  M4: swing_high_decay            下降高点序列预警
  M5: monthly_trend_filter        月线趋势过滤（封禁做多核心）

使用方式：
  from brahma_brain.tradfi_signal_layer import analyze_tradfi_dump
  result = analyze_tradfi_dump(symbol, klines_1h, direction, ret_30d)
  # result['dump_type'], result['score_delta'], result['breakdown']
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ── 美股代币白名单（RWA代币化股票）────────────────────────────────────────
TRADFI_TOKEN_LIST = {
    'SNDKUSDT', 'MUUSDT', 'SPCXUSDT',
    'NVDAUSDT', 'TSLAMARGIN', 'AAPLMARGIN', 'COINMARGIN', 'MSTRMARGIN',
}

# 加密主流币排除（防止BTCUSDT/ETHUSDT等被误判为TradFi）
CRYPTO_NATIVE_EXCLUDE = {
    'BTC', 'ETH', 'BNB', 'SOL', 'XRP', 'ADA', 'DOGE', 'DOT', 'AVAX', 'MATIC',
    'LINK', 'UNI', 'LTC', 'ATOM', 'ALGO', 'NEAR', 'FTM', 'SAND', 'MANA', 'AXS',
    'CRO', 'VET', 'THETA', 'FIL', 'TRX', 'ETC', 'XLM', 'HBAR', 'EOS', 'FLOW',
    'ICP', 'XTZ', 'EGLD', 'AAVE', 'MKR', 'COMP', 'YFI', 'SNX', 'CRV', 'SUSHI',
    '1INCH', 'ZEC', 'DASH', 'XMR', 'BCH', 'BSV', 'BAT', 'ZIL', 'ENJ', 'CHZ',
    'GALA', 'IMX', 'APE', 'GMT', 'OP', 'ARB', 'BLUR', 'SUI', 'SEI', 'TIA',
    'INJ', 'WLD', 'PYTH', 'JUP', 'STRK', 'EIGEN', 'TRUMP', 'MELANIA',
    'MEME', 'PEPE', 'BONK', 'WIF', 'FLOKI', 'SHIB', 'BABYDOGE',
    'USDT', 'USDC', 'BUSD', 'DAI', 'TUSD', 'USDP',
}

# 判断是否为美股代币
def is_tradfi_token(symbol: str) -> bool:
    if symbol in TRADFI_TOKEN_LIST:
        return True
    # 常见美股代币后缀模式
    for suffix in ['USDT', 'USDC', 'BUSD']:
        base = symbol.replace(suffix, '')
        # 全大写字母且长度2~5，排除加密主流币
        if base.isalpha() and 2 <= len(base) <= 5:
            if base in CRYPTO_NATIVE_EXCLUDE:
                return False
            return True
    return False


# ═══════════════════════════════════════════════════════════════════════════════
# M5: 月线趋势过滤（最高优先级 · 最简单 · 最有效）
# ═══════════════════════════════════════════════════════════════════════════════
def m5_monthly_trend_filter(ret_30d: float, direction: str) -> dict:
    """
    月线趋势过滤
    铁证：SNDK 30日-40.33% + 7日-12.29% 仍输出做多 = 根本性错误

    规则：
      ret_30d < -20% → MONTHLY_BEAR → 封禁做多（score_delta=-30）
      ret_30d < -30% → Kronos降权建议 × 0.3
      ret_30d < -40% → 强制做空方向（score_delta=-50，空单+20）

    Returns:
      {'triggered': bool, 'level': str, 'score_delta': int,
       'force_short': bool, 'kronos_weight': float, 'label': str}
    """
    result = {
        'triggered': False,
        'level': 'NORMAL',
        'score_delta': 0,
        'force_short': False,
        'kronos_weight': 1.0,
        'label': '',
    }

    if direction != 'LONG':
        return result  # M5只保护做多方向

    if ret_30d < -40:
        result.update({
            'triggered': True,
            'level': 'MONTHLY_CRASH',
            'score_delta': -50,
            'force_short': True,
            'kronos_weight': 0.1,
            'label': f'M5🚫月线崩溃{ret_30d:.1f}% → 强制SHORT',
        })
    elif ret_30d < -30:
        result.update({
            'triggered': True,
            'level': 'MONTHLY_BEAR_SEVERE',
            'score_delta': -35,
            'force_short': False,
            'kronos_weight': 0.3,
            'label': f'M5⛔月线重跌{ret_30d:.1f}% → 封禁LONG',
        })
    elif ret_30d < -20:
        result.update({
            'triggered': True,
            'level': 'MONTHLY_BEAR',
            'score_delta': -20,
            'force_short': False,
            'kronos_weight': 0.5,
            'label': f'M5⚠️月线下行{ret_30d:.1f}% → 降权LONG',
        })

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# M1: 顶部持续放量出货检测
# ═══════════════════════════════════════════════════════════════════════════════
def m1_top_distribution_detector(klines: list, recent_high: float) -> dict:
    """
    顶部出货检测
    铁证：K48~K58共11根在高位平均8.3x MA20放量 = 主力分批出货

    检测逻辑：
      1. 识别"高位区域"：近期高点±3%以内
      2. 统计在高位区域内放量（>3x MA20）的K线数量
      3. 连续5根以上平均量>5x → 顶部出货信号

    klines格式：[{'open': float, 'high': float, 'low': float, 'close': float, 'volume': float}, ...]
                时间从旧到新，最后一根=当前K线

    Returns:
      {'triggered': bool, 'score_delta': int, 'consecutive_count': int,
       'avg_vol_ratio': float, 'label': str}
    """
    result = {
        'triggered': False,
        'score_delta': 0,
        'consecutive_count': 0,
        'avg_vol_ratio': 0.0,
        'label': '',
    }

    if not klines or len(klines) < 25:
        return result

    # MA20应使用历史均量，取klines倒数第21~40根（排除当前顶部异常K线的干扰）
    volumes_hist = [k['volume'] for k in klines[-40:-20]] if len(klines) >= 40 else [k['volume'] for k in klines[:-20]]
    if not volumes_hist:
        return result
    ma20 = sum(volumes_hist) / len(volumes_hist)
    if ma20 <= 0:
        return result

    # 高位区域：recent_high ±3%
    high_zone_lower = recent_high * 0.97
    high_zone_upper = recent_high * 1.03

    # 统计近20根K线中在高位区域放量的K线
    high_vol_bars = []
    for k in klines[-20:]:
        bar_mid = (k['high'] + k['close']) / 2
        vol_ratio = k['volume'] / ma20
        in_high_zone = high_zone_lower <= bar_mid <= high_zone_upper
        if in_high_zone and vol_ratio >= 3.0:
            high_vol_bars.append(vol_ratio)

    if not high_vol_bars:
        return result

    count = len(high_vol_bars)
    avg_ratio = sum(high_vol_bars) / count

    if count >= 8 and avg_ratio >= 7.0:
        result.update({
            'triggered': True,
            'score_delta': -25,
            'consecutive_count': count,
            'avg_vol_ratio': avg_ratio,
            'label': f'M1🚨顶部出货{count}根均{avg_ratio:.1f}x → 主力大量出货',
        })
    elif count >= 5 and avg_ratio >= 5.0:
        result.update({
            'triggered': True,
            'score_delta': -20,
            'consecutive_count': count,
            'avg_vol_ratio': avg_ratio,
            'label': f'M1⚠️顶部放量{count}根均{avg_ratio:.1f}x → 出货信号',
        })
    elif count >= 3 and avg_ratio >= 4.0:
        result.update({
            'triggered': True,
            'score_delta': -10,
            'consecutive_count': count,
            'avg_vol_ratio': avg_ratio,
            'label': f'M1🔶顶部疑似出货{count}根均{avg_ratio:.1f}x',
        })

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# M2: 量价背离检测（量大价不涨 = 诱多/对倒）
# ═══════════════════════════════════════════════════════════════════════════════
def m2_price_volume_divergence(klines: list) -> dict:
    """
    量价背离检测
    铁证：K58 量9.5x MA20 但价格仅+0.47% = 诱多出货完毕，下一根K38暴跌

    两个检测模式：
      模式A: 量大价不涨（量>5x 但涨幅<1%，阳线）→ 诱多/对倒
      模式B: 量大高位阴线（量>5x 且收阴且价格在近期高点80%+分位）→ 主力出货

    只检测最近3根K线（当前K及前2根）

    Returns:
      {'triggered': bool, 'mode': str, 'score_delta': int,
       'vol_ratio': float, 'price_change_pct': float, 'label': str}
    """
    result = {
        'triggered': False,
        'mode': '',
        'score_delta': 0,
        'vol_ratio': 0.0,
        'price_change_pct': 0.0,
        'label': '',
    }

    if not klines or len(klines) < 25:
        return result

    # MA20用历史均量（倒数21~40根），排除当前异常K线的干扰
    hist_bars = klines[-40:-20] if len(klines) >= 40 else klines[:-20]
    if not hist_bars:
        return result
    ma20 = sum(k['volume'] for k in hist_bars) / len(hist_bars)
    if ma20 <= 0:
        return result

    # 近期20根高点（判断价格位置）
    recent_high_20 = max(k['high'] for k in klines[-20:])

    # 检测最近3根
    for k in klines[-3:]:
        vol_ratio = k['volume'] / ma20
        price_chg = (k['close'] - k['open']) / k['open'] * 100 if k['open'] > 0 else 0
        is_bullish = k['close'] > k['open']
        upper_shadow = (k['high'] - max(k['open'], k['close'])) / (k['high'] - k['low'] + 1e-9)
        near_high = k['high'] / recent_high_20 >= 0.95  # 在近期高点95%以上

        # 模式A: 量大价不涨（诱多）
        if vol_ratio >= 5.0 and is_bullish and abs(price_chg) < 1.0 and near_high:
            result.update({
                'triggered': True,
                'mode': 'A_TRAP_BULL',
                'score_delta': -15,
                'vol_ratio': vol_ratio,
                'price_change_pct': price_chg,
                'label': f'M2🚨量大价不涨{vol_ratio:.1f}x涨{price_chg:.2f}% → 诱多出货',
            })
            return result

        # 模式B: 高位放量阴线（主力出货）
        if vol_ratio >= 5.0 and not is_bullish and near_high and upper_shadow >= 0.3:
            result.update({
                'triggered': True,
                'mode': 'B_HIGH_BEAR',
                'score_delta': -12,
                'vol_ratio': vol_ratio,
                'price_change_pct': price_chg,
                'label': f'M2⚠️高位放量阴线{vol_ratio:.1f}x上影{upper_shadow:.0%} → 主力出货',
            })
            return result

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# M3: OBV顶背离权重升级
# ═══════════════════════════════════════════════════════════════════════════════
def m3_obv_divergence_weight(klines: list, direction: str) -> dict:
    """
    OBV顶背离权重升级
    现状：OBV反向仅-3分（严重低估）
    铁证：K23→K31价格涨+2.2%但OBV从+42,159→-17,593（-140%背离）仅得-3分

    升级规则：
      OBV 24H内从正转负（转负事件）→ -15分
      OBV 背离幅度 > 50%（价涨OBV跌）→ -20分
      OBV 顶背离确认（价创新高但OBV转负）→ -25分

    Returns:
      {'triggered': bool, 'level': str, 'score_delta': int,
       'divergence_pct': float, 'label': str}
    """
    result = {
        'triggered': False,
        'level': '',
        'score_delta': 0,
        'divergence_pct': 0.0,
        'label': '',
    }

    if direction != 'LONG':
        return result  # OBV背离主要保护做多

    if not klines or len(klines) < 24:
        return result

    # 计算OBV序列（过去24根1H K线）
    def calc_obv(bars):
        obv = 0.0
        obv_series = []
        for i, k in enumerate(bars):
            if i == 0:
                obv_series.append(0.0)
                continue
            if k['close'] > bars[i-1]['close']:
                obv += k['volume']
            elif k['close'] < bars[i-1]['close']:
                obv -= k['volume']
            obv_series.append(obv)
        return obv_series

    bars_24 = klines[-24:]
    obv_series = calc_obv(bars_24)

    if not obv_series:
        return result

    obv_start = obv_series[0]
    obv_end = obv_series[-1]
    obv_mid_max = max(obv_series[:12]) if obv_series[:12] else 0

    # 价格变化
    price_start = bars_24[0]['close']
    price_end = bars_24[-1]['close']
    price_mid_high = max(k['high'] for k in bars_24[:12])
    price_pct_chg = (price_end - price_start) / price_start * 100 if price_start > 0 else 0

    # OBV变化方向
    obv_turned_negative = obv_mid_max > 0 and obv_end < 0
    price_near_high = price_end / price_mid_high >= 0.95 if price_mid_high > 0 else False

    # 背离幅度计算（价格涨但OBV跌）
    # 使用总量归一化，避免obv_start接近0时溢出
    if price_pct_chg > 0 and obv_end < obv_start:
        total_vol = sum(k['volume'] for k in bars_24) + 1e-9
        obv_drop_ratio = (obv_start - obv_end) / total_vol * 100  # 归一化OBV降幅
        divergence_score = min(obv_drop_ratio * 10, 200)  # cap at 200%
    else:
        divergence_score = 0

    # 顶背离：价格创新高但OBV转负
    if price_near_high and obv_turned_negative and price_pct_chg > 0:
        result.update({
            'triggered': True,
            'level': 'APEX_BEARISH_DIV',
            'score_delta': -25,
            'divergence_pct': divergence_score,
            'label': f'M3🚨OBV顶背离+转负 背离{divergence_score:.0f}% → 顶部确认',
        })
    elif obv_turned_negative and price_pct_chg > 0:
        result.update({
            'triggered': True,
            'level': 'OBV_TURNED_NEG',
            'score_delta': -15,
            'divergence_pct': divergence_score,
            'label': f'M3⚠️OBV24H内转负 价格仍在上涨 → 资金出逃',
        })
    elif divergence_score > 50 and price_pct_chg > 0:
        result.update({
            'triggered': True,
            'level': 'OBV_DIVERGE_50',
            'score_delta': -20,
            'divergence_pct': divergence_score,
            'label': f'M3⚠️OBV背离{divergence_score:.0f}% → 价涨量退做空警示',
        })

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# M4: 下降高点序列预警
# ═══════════════════════════════════════════════════════════════════════════════
def m4_swing_high_decay(klines: list) -> dict:
    """
    下降高点序列预警
    铁证：K52~K58高点序列 1518→1515→1509→1499 连续4次下移
    = 空头已控盘，暴跌前5小时的结构信号

    检测：近10根1H K线中，连续3根以上高点下移（每次下移>0.1%）

    Returns:
      {'triggered': bool, 'count': int, 'score_delta': int,
       'decay_pct': float, 'label': str}
    """
    result = {
        'triggered': False,
        'count': 0,
        'score_delta': 0,
        'decay_pct': 0.0,
        'label': '',
    }

    if not klines or len(klines) < 8:
        return result

    bars = klines[-10:]
    highs = [k['high'] for k in bars]

    # 找连续下降高点
    max_consecutive = 1
    current = 1
    for i in range(1, len(highs)):
        # 高点下移 > 0.1%（过滤噪音）
        if highs[i] < highs[i-1] * 0.999:
            current += 1
            max_consecutive = max(max_consecutive, current)
        else:
            current = 1

    if max_consecutive < 3:
        return result

    # 计算下降幅度
    first_high = max(highs)
    last_high = highs[-1]
    decay_pct = (first_high - last_high) / first_high * 100 if first_high > 0 else 0

    if max_consecutive >= 5:
        result.update({
            'triggered': True,
            'count': max_consecutive,
            'score_delta': -15,
            'decay_pct': decay_pct,
            'label': f'M4🚨连续{max_consecutive}次高点下移-{decay_pct:.1f}% → 空头全面控盘',
        })
    elif max_consecutive >= 3:
        result.update({
            'triggered': True,
            'count': max_consecutive,
            'score_delta': -10,
            'decay_pct': decay_pct,
            'label': f'M4⚠️连续{max_consecutive}次高点下移-{decay_pct:.1f}% → 空头占优',
        })

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# 事件类型识别（TYPE-1/2/3）
# ═══════════════════════════════════════════════════════════════════════════════
def identify_dump_type(
    symbol: str,
    vol_ratio_current: float,    # 当前K线量倍（vs MA20）
    price_chg_24h: float,        # 个股24H涨跌幅（%）
    spx_chg_24h: float,          # SPX 24H涨跌幅（%）
    ret_30d: float,              # 30日收益率（%）
    oi_chg_1h: float = 0.0,      # OI 1H变化（%）
    rsi_1h: float = 50.0,        # RSI 1H
) -> dict:
    """
    三类事件识别

    TYPE-1: TECH_OVERSOLD      技术性超卖，做多信号有效
    TYPE-2: FUNDAMENTAL_DUMP   基本面利空，封禁做多，等反弹做空
    TYPE-3: PANIC_LIQUIDATION  恐慌踩踏，轧空轻多机会

    Returns: {'dump_type': str, 'confidence': float, 'reason': str,
              'allow_long': bool, 'allow_short': bool,
              'short_entry_note': str}
    """
    result = {
        'dump_type': 'UNKNOWN',
        'confidence': 0.0,
        'reason': '',
        'allow_long': True,
        'allow_short': True,
        'short_entry_note': '',
    }

    # 个股 vs SPX 偏离度
    gap_vs_spx = price_chg_24h - spx_chg_24h  # 负值=个股比大盘更弱

    # TYPE-3: 恐慌踩踏（极端放量+OI暴涨+RSI极低）
    if vol_ratio_current >= 15.0 and rsi_1h <= 15.0 and oi_chg_1h >= 20.0:
        result.update({
            'dump_type': 'PANIC_LIQUIDATION',
            'confidence': 0.85,
            'reason': f'极端放量{vol_ratio_current:.0f}x + OI+{oi_chg_1h:.0f}% + RSI={rsi_1h:.0f}',
            'allow_long': True,  # 轧空机会，极轻仓
            'allow_short': False,
            'short_entry_note': '',
        })
        return result

    # TYPE-2: 基本面利空暴跌
    # 条件：个股独立下跌（vs SPX偏离>5%）+ 放量（>5x）
    fundamental_dump = (
        gap_vs_spx < -5.0 and vol_ratio_current >= 5.0
    ) or (
        ret_30d < -30.0 and price_chg_24h < -5.0 and vol_ratio_current >= 3.0
    )

    if fundamental_dump:
        confidence = 0.7
        reasons = []
        if gap_vs_spx < -8.0:
            confidence += 0.1
            reasons.append(f'个股独立跌{gap_vs_spx:.1f}%vs大盘')
        if vol_ratio_current >= 10.0:
            confidence += 0.1
            reasons.append(f'极端放量{vol_ratio_current:.0f}x')
        if ret_30d < -30.0:
            confidence += 0.1
            reasons.append(f'30日趋势{ret_30d:.0f}%')

        result.update({
            'dump_type': 'FUNDAMENTAL_DUMP',
            'confidence': min(confidence, 0.95),
            'reason': ' | '.join(reasons),
            'allow_long': False,
            'allow_short': True,
            'short_entry_note': '等反弹至阻力位（FVG/Bear OB）入空，非抄底',
        })
        return result

    # TYPE-1: 技术性超卖（默认，当不满足TYPE-2/3时）
    result.update({
        'dump_type': 'TECH_OVERSOLD',
        'confidence': 0.6,
        'reason': f'标准联动下跌 gap_vs_spx={gap_vs_spx:.1f}% vol={vol_ratio_current:.1f}x',
        'allow_long': True,
        'allow_short': False,
        'short_entry_note': '',
    })
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# 主入口：全量分析
# ═══════════════════════════════════════════════════════════════════════════════
def analyze_tradfi_dump(
    symbol: str,
    klines_1h: list,             # 过去40根1H K线（从旧到新）
    direction: str,              # 'LONG' 或 'SHORT'
    ret_30d: float,              # 30日收益率（%）
    price_chg_24h: float = 0.0,  # 个股24H涨跌（%）
    spx_chg_24h: float = 0.0,   # SPX 24H涨跌（%）
    vol_ratio_current: float = 1.0,  # 当前K线量倍
    oi_chg_1h: float = 0.0,
    rsi_1h: float = 50.0,
) -> dict:
    """
    美股代币全量分析入口

    Returns:
      {
        'is_tradfi': bool,
        'dump_type': str,          TYPE-1/2/3
        'score_delta': int,        对梵天score的调整量（负数=降分）
        'force_direction': str,    '' / 'SHORT'（强制方向）
        'kronos_weight': float,    Kronos权重乘数
        'allow_long': bool,
        'allow_short': bool,
        'breakdown': dict,         各模块详细信号
        'summary_label': str,      单行汇总（注入梵天breakdown）
      }
    """
    is_tf = is_tradfi_token(symbol)

    result = {
        'is_tradfi': is_tf,
        'dump_type': 'NORMAL',
        'score_delta': 0,
        'force_direction': '',
        'kronos_weight': 1.0,
        'allow_long': True,
        'allow_short': True,
        'breakdown': {},
        'summary_label': '',
    }

    if not is_tf:
        return result

    breakdown = {}

    # ── M5: 月线趋势过滤（最优先）─────────────────────────────────────
    m5 = m5_monthly_trend_filter(ret_30d, direction)
    breakdown['M5_monthly'] = m5
    if m5['triggered']:
        result['score_delta'] += m5['score_delta']
        result['kronos_weight'] = min(result['kronos_weight'], m5['kronos_weight'])
        if m5['force_short']:
            result['force_direction'] = 'SHORT'
            result['allow_long'] = False

    # ── 事件类型识别（TYPE-1/2/3）──────────────────────────────────────
    recent_high = max(k['high'] for k in klines_1h[-20:]) if klines_1h else 0
    dump_info = identify_dump_type(
        symbol=symbol,
        vol_ratio_current=vol_ratio_current,
        price_chg_24h=price_chg_24h,
        spx_chg_24h=spx_chg_24h,
        ret_30d=ret_30d,
        oi_chg_1h=oi_chg_1h,
        rsi_1h=rsi_1h,
    )
    result['dump_type'] = dump_info['dump_type']
    breakdown['dump_type_info'] = dump_info

    if dump_info['dump_type'] == 'FUNDAMENTAL_DUMP':
        result['allow_long'] = False
        if direction == 'LONG':
            result['score_delta'] += -30  # 基本面利空额外-30
        result['kronos_weight'] = min(result['kronos_weight'], 0.2)
    elif dump_info['dump_type'] == 'PANIC_LIQUIDATION':
        result['allow_short'] = False
        result['kronos_weight'] = 1.0  # 轧空时Kronos权重恢复

    # ── M1: 顶部持续放量出货（有K线数据时运行）────────────────────────
    if klines_1h and len(klines_1h) >= 20:
        m1 = m1_top_distribution_detector(klines_1h, recent_high)
        breakdown['M1_top_dist'] = m1
        if m1['triggered'] and direction == 'LONG':
            result['score_delta'] += m1['score_delta']

    # ── M2: 量价背离────────────────────────────────────────────────────
    if klines_1h and len(klines_1h) >= 22:
        m2 = m2_price_volume_divergence(klines_1h)
        breakdown['M2_pv_div'] = m2
        if m2['triggered'] and direction == 'LONG':
            result['score_delta'] += m2['score_delta']

    # ── M3: OBV顶背离──────────────────────────────────────────────────
    if klines_1h and len(klines_1h) >= 24:
        m3 = m3_obv_divergence_weight(klines_1h, direction)
        breakdown['M3_obv'] = m3
        if m3['triggered']:
            result['score_delta'] += m3['score_delta']

    # ── M4: 下降高点──────────────────────────────────────────────────
    if klines_1h and len(klines_1h) >= 8:
        m4 = m4_swing_high_decay(klines_1h)
        breakdown['M4_swing'] = m4
        if m4['triggered'] and direction == 'LONG':
            result['score_delta'] += m4['score_delta']

    result['breakdown'] = breakdown

    # ── 汇总标签──────────────────────────────────────────────────────
    labels = []
    if m5['triggered']:
        labels.append(m5['label'])
    labels.append(f"[{dump_info['dump_type']}]")
    if klines_1h and len(klines_1h) >= 20:
        if breakdown.get('M1_top_dist', {}).get('triggered'):
            labels.append(breakdown['M1_top_dist']['label'])
    if klines_1h and len(klines_1h) >= 22:
        if breakdown.get('M2_pv_div', {}).get('triggered'):
            labels.append(breakdown['M2_pv_div']['label'])
    if klines_1h and len(klines_1h) >= 24:
        if breakdown.get('M3_obv', {}).get('triggered'):
            labels.append(breakdown['M3_obv']['label'])
    if klines_1h and len(klines_1h) >= 8:
        if breakdown.get('M4_swing', {}).get('triggered'):
            labels.append(breakdown['M4_swing']['label'])

    result['summary_label'] = ' | '.join(labels) if labels else f'[{dump_info["dump_type"]}]正常'
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# SNDK 7月27日历史回测验证
# ═══════════════════════════════════════════════════════════════════════════════
def backtest_sndk_0727():
    """
    回测验证：SNDK 7月27日历史数据
    验证5个模块是否能在暴跌前给出正确信号
    """
    # 模拟K线数据（基于复盘的真实数据）
    ma20_vol = 4234  # 真实MA20成交量

    klines = []
    # 背景K线（正常量级）
    for _ in range(20):
        klines.append({'open': 1400, 'high': 1420, 'low': 1390, 'close': 1410,
                        'volume': ma20_vol * 1.1})

    # K48~K58 顶部出货区
    top_data = [
        (1480, 1499, 1478, 1499, 5.5),   # K48 +1.3%
        (1499, 1505, 1477, 1479, 6.7),   # K49 -1.3%
        (1479, 1495, 1478, 1488, 8.2),   # K50 +0.6% 长上影
        (1488, 1500, 1487, 1497, 5.3),   # K51 +0.6%
        (1497, 1518, 1496, 1512, 7.6),   # K52 +1.0% 创高
        (1512, 1518, 1510, 1511, 9.8),   # K53 -0.1% 顶部横盘
        (1511, 1516, 1507, 1508, 11.2),  # K54 -0.2% 顶部横盘
        (1508, 1518, 1507, 1515, 6.4),   # K55 +0.5%
        (1515, 1516, 1507, 1509, 8.9),   # K56 -0.4% 长上影
        (1509, 1512, 1497, 1499, 13.8),  # K57 -0.7% 放量阴线
        (1480, 1494, 1479, 1487, 9.5),   # K58 +0.5% 量大价不涨（诱多）
    ]

    for o, h, l, c, vol_mult in top_data:
        klines.append({'open': o, 'high': h, 'low': l, 'close': c,
                        'volume': ma20_vol * vol_mult})

    # 分析
    result = analyze_tradfi_dump(
        symbol='SNDKUSDT',
        klines_1h=klines,
        direction='LONG',
        ret_30d=-40.80,
        price_chg_24h=-10.2,
        spx_chg_24h=2.87,
        vol_ratio_current=9.5,  # K58的量倍（最新K线）
        oi_chg_1h=8.0,
        rsi_1h=25.0,
    )
    return result


if __name__ == '__main__':
    import json as _json
    print("═" * 60)
    print("SNDK 7月27日回测验证")
    print("═" * 60)
    r = backtest_sndk_0727()
    print(f"is_tradfi:      {r['is_tradfi']}")
    print(f"dump_type:      {r['dump_type']}")
    print(f"score_delta:    {r['score_delta']}")
    print(f"allow_long:     {r['allow_long']}")
    print(f"allow_short:    {r['allow_short']}")
    print(f"force_dir:      {r['force_direction']}")
    print(f"kronos_weight:  {r['kronos_weight']}")
    print(f"\n汇总标签：{r['summary_label']}")
    print("\n各模块详情：")
    for k, v in r['breakdown'].items():
        if isinstance(v, dict) and v.get('triggered'):
            print(f"  {k}: {v.get('label', '')} delta={v.get('score_delta', 0)}")