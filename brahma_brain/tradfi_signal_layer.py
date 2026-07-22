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
    except Exception:
        pass

    return result
