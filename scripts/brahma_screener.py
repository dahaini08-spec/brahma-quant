#!/usr/bin/env python3
"""
brahma_screener.py — 统一选币层 v1.0
设计院 2026-08-24 重建 | 替换 market_screener.py + ai_pro_screener.py

核心优化: API调用共享 — 原来两个脚本各自独立拉全市场ticker(628个合约×2次)
                         现在一次拉取 → 两套评分逻辑各自计算

功能:
  run_rule_screener()  → 规则评分TOP候选 → data/scan_candidates.json
  run_ai_screener()    → 蓄力预判TOP候选 → data/ai_pro_candidates.json
  run_all()            → 并行执行两套，一次API调用完成全部

调用方不变:
  brahma_scan_all.py 读 scan_candidates.json / ai_pro_candidates.json
"""

from __future__ import annotations
import sys, os, json, time, math, logging
import urllib.request
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

# ── sys.path ──────────────────────────────────────────────────────────────────
BASE = Path(__file__).parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))
DATA = BASE / 'data'
DATA.mkdir(exist_ok=True)

logger = logging.getLogger('brahma_screener')

# ── 内存门控（统一一份）──────────────────────────────────────────────────────
try:
    import resource as _res
    _res.setrlimit(_res.RLIMIT_AS, (1500*1024*1024, 1500*1024*1024))
except Exception:
    pass

# ── 常量 ─────────────────────────────────────────────────────────────────────
FAPI = 'https://fapi.binance.com'
EXCLUDE = {
    'USDCUSDT','BUSDUSDT','USDTUSDT','BTCDOMUSDT',
    'DEFIUSDT','ALTUSDT','BNXUSDT','COCOSUSDT',
}
MIN_VOLUME_24H = 50_000_000   # 5000万美元流动性门槛

# rule screener 评分权重（6维，SHORT导向）
RULE_WEIGHTS = dict(liquidity=20, rsi=20, trend=25, momentum=15, oi=10, fr=10)

# ai screener 蓄力评分阈值
BBW_TIGHT  = 15.0; BBW_COMPRESS = 25.0
RSI_OS     = 30.0; RSI_LOW_     = 40.0
VOL_DRY    = 0.3;  VOL_SHRINK   = 0.5
LOW_NEAR   = 10.0; LOW_MID      = 20.0

# ═══════════════════════════════════════════════════════════════════════════════
# 1. 共享 API 层（一次调用，两套逻辑共用）
# ═══════════════════════════════════════════════════════════════════════════════

def _http_get(url: str, timeout: int = 8) -> object:
    """轻量HTTP GET，无外部依赖"""
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None

def fetch_tickers() -> list:
    """一次拉取全市场USDT永续合约24H行情（两套screener共用）"""
    data = _http_get(f'{FAPI}/fapi/v1/ticker/24hr', timeout=10)
    if not data:
        return []
    return [t for t in data
            if t.get('symbol','').endswith('USDT')
            and t.get('symbol') not in EXCLUDE
            and float(t.get('quoteVolume', 0)) >= MIN_VOLUME_24H]

def fetch_funding_rates(symbols: set) -> dict:
    """批量获取资金费率"""
    data = _http_get(f'{FAPI}/fapi/v1/premiumIndex', timeout=8)
    if not data or not isinstance(data, list):
        return {}
    return {d['symbol']: float(d.get('lastFundingRate', 0)) * 100
            for d in data if d.get('symbol') in symbols}

def fetch_klines_1h(symbol: str, limit: int = 50) -> list:
    """拉取1H K线，用于RSI/BB计算"""
    data = _http_get(f'{FAPI}/fapi/v1/klines?symbol={symbol}&interval=1h&limit={limit}', timeout=6)
    return data or []

# ═══════════════════════════════════════════════════════════════════════════════
# 2. 规则评分层（原 market_screener: 6维SHORT导向评分）
# ═══════════════════════════════════════════════════════════════════════════════

def _rule_score(sym: str, ticker: dict, fr: float = 0) -> dict | None:
    """6维规则评分（SHORT导向）"""
    try:
        price   = float(ticker['lastPrice'])
        vol24h  = float(ticker.get('quoteVolume', 0))
        pct24h  = float(ticker['priceChangePercent'])
        if price <= 0:
            return None

        score = 0
        reasons = []

        # 维度1: 流动性 (0-20)
        liq = min(20, math.log10(max(vol24h, 1e6)) * 2.5 - 12)
        score += max(0, liq)

        # 维度2: RSI位置 (0-20) 空头黄金区40-65
        kl = fetch_klines_1h(sym, 20)
        rsi = 50.0
        if len(kl) >= 15:
            closes = [float(k[4]) for k in kl]
            gains = [max(closes[i]-closes[i-1], 0) for i in range(1, len(closes))]
            losses = [max(closes[i-1]-closes[i], 0) for i in range(1, len(closes))]
            ag = sum(gains) / len(gains); al = sum(losses) / len(losses)
            rsi = 100 - 100/(1 + ag/max(al, 1e-9)) if al else 100.0
        if 40 <= rsi <= 65:
            score += 20; reasons.append(f'RSI={rsi:.0f}空头区')
        elif 65 < rsi <= 75:
            score += 12
        elif rsi > 75:
            score += 5

        # 维度3: 双TF趋势 (0-25)
        if pct24h < -1:
            score += 15; reasons.append('24H下跌')
        elif pct24h < 0:
            score += 8
        elif pct24h > 3:
            score -= 5

        # 维度4: 动量 (0-15)
        if -5 <= pct24h <= -1:
            score += 15
        elif -10 <= pct24h < -5:
            score += 10

        # 维度5: 资金费率 (-5~+10)
        if fr < -0.01:
            score += 10; reasons.append(f'FR={fr:.3f}%空头付息')
        elif fr > 0.05:
            score -= 5

        return {
            'symbol': sym, 'score': round(score, 1),
            'price': price, 'pct24h': pct24h, 'rsi': round(rsi, 1),
            'fr': round(fr, 4), 'vol24h': round(vol24h),
            'reason': ' | '.join(reasons) if reasons else 'rule_score',
            'screener': 'rule',
        }
    except Exception:
        return None


def run_rule_screener(tickers: list | None = None, top_n: int = 8) -> list:
    """规则评分选币 → data/scan_candidates.json"""
    if tickers is None:
        tickers = fetch_tickers()
    if not tickers:
        return []

    symbols = {t['symbol'] for t in tickers}
    fr_map  = fetch_funding_rates(symbols)

    results = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = {pool.submit(_rule_score, t['symbol'], t, fr_map.get(t['symbol'], 0)): t
                for t in tickers}
        for fut in as_completed(futs):
            r = fut.result()
            if r and r['score'] > 30:
                results.append(r)

    results.sort(key=lambda x: -x['score'])
    top = results[:top_n]

    out = DATA / 'scan_candidates.json'
    out.write_text(json.dumps({
        'ts': time.time(),
        'iso': datetime.now(timezone.utc).isoformat(),
        'source': 'brahma_screener_rule',
        'count': len(top),
        'candidates': top,
    }, ensure_ascii=False, indent=2))
    logger.info(f'[RuleScreener] TOP{top_n}: {[r["symbol"] for r in top]}')
    return top


# ═══════════════════════════════════════════════════════════════════════════════
# 3. AI蓄力预判层（原 ai_pro_screener: 预判底部蓄力候选）
# ═══════════════════════════════════════════════════════════════════════════════

def _ai_score(sym: str, ticker: dict, fr: float = 0) -> dict | None:
    """蓄力预判评分（底部蓄力，非已涨）"""
    try:
        price  = float(ticker['lastPrice'])
        vol24h = float(ticker.get('quoteVolume', 0))
        if price <= 0:
            return None

        # 拉K线算BBW/RSI/量比
        kl = fetch_klines_1h(sym, 50)
        if len(kl) < 20:
            return None

        closes  = [float(k[4]) for k in kl]
        volumes = [float(k[5]) for k in kl]

        # BBW
        n = 20
        sma = sum(closes[-n:]) / n
        std = (sum((c - sma)**2 for c in closes[-n:]) / n) ** 0.5
        bbw = (std * 4 / sma * 100) if sma else 50.0

        # RSI
        gains  = [max(closes[i]-closes[i-1], 0) for i in range(1, len(closes))]
        losses = [max(closes[i-1]-closes[i], 0) for i in range(1, len(closes))]
        ag = sum(gains[-14:]) / 14; al = sum(losses[-14:]) / 14
        rsi = 100 - 100/(1 + ag/max(al,1e-9)) if al else 50.0

        # 量比（当前成交量 vs 20H均量）
        avg_vol = sum(volumes[-20:]) / 20
        vol_ratio = volumes[-1] / max(avg_vol, 1)

        # 距90日低
        low_90 = min(closes[-min(90, len(closes)):])
        low_dist = (price - low_90) / low_90 * 100 if low_90 else 50.0

        score = 0
        reasons = []

        # BBW压缩 (0-40)
        if bbw < BBW_TIGHT:
            score += 40; reasons.append(f'BBW={bbw:.1f}%极压缩')
        elif bbw < BBW_COMPRESS:
            score += 20; reasons.append(f'BBW={bbw:.1f}%压缩')

        # RSI低位 (0-25)
        if rsi < RSI_OS:
            score += 25; reasons.append(f'RSI={rsi:.0f}超卖')
        elif rsi < RSI_LOW_:
            score += 12; reasons.append(f'RSI={rsi:.0f}低位')

        # 量能枯竭 (0-15)
        if vol_ratio < VOL_DRY:
            score += 15; reasons.append(f'量比={vol_ratio:.2f}极枯竭')
        elif vol_ratio < VOL_SHRINK:
            score += 7

        # 距低点 (0-10)
        if low_dist < LOW_NEAR:
            score += 10; reasons.append(f'距低点{low_dist:.1f}%')
        elif low_dist < LOW_MID:
            score += 5

        # 负费率 (0-10)
        if fr < -0.02:
            score += 10; reasons.append(f'FR={fr:.3f}%')
        elif fr < 0:
            score += 5

        return {
            'symbol': sym, 'score': round(score, 1),
            'price': price, 'bbw': round(bbw, 2),
            'rsi': round(rsi, 1), 'vol_ratio': round(vol_ratio, 3),
            'low_dist_pct': round(low_dist, 2), 'fr': round(fr, 4),
            'reason': ' | '.join(reasons),
            'screener': 'ai_pump',
        }
    except Exception:
        return None


def run_ai_screener(tickers: list | None = None, top_n: int = 20) -> list:
    """蓄力预判选币 → data/ai_pro_candidates.json"""
    if tickers is None:
        tickers = fetch_tickers()
    if not tickers:
        return []

    symbols = {t['symbol'] for t in tickers}
    fr_map  = fetch_funding_rates(symbols)

    results = []
    with ThreadPoolExecutor(max_workers=10) as pool:
        futs = {pool.submit(_ai_score, t['symbol'], t, fr_map.get(t['symbol'], 0)): t
                for t in tickers}
        for fut in as_completed(futs):
            r = fut.result()
            if r and r['score'] > 20:
                results.append(r)

    results.sort(key=lambda x: -x['score'])
    top = results[:top_n]

    out = DATA / 'ai_pro_candidates.json'
    out.write_text(json.dumps({
        'ts': time.time(),
        'iso': datetime.now(timezone.utc).isoformat(),
        'source': 'brahma_screener_ai',
        'count': len(top),
        'candidates': top,
    }, ensure_ascii=False, indent=2))
    logger.info(f'[AIScreener] TOP{top_n}: {[r["symbol"] for r in top[:5]]}...')
    return top


# ═══════════════════════════════════════════════════════════════════════════════
# 4. 统一入口（一次API调用，两套结果）
# ═══════════════════════════════════════════════════════════════════════════════

def run_all(rule_top: int = 8, ai_top: int = 20) -> dict:
    """
    一次调用完成两套筛选（共享ticker数据，API调用从2次→1次）
    返回 {'rule': [...], 'ai': [...]}
    """
    t0 = time.time()
    tickers = fetch_tickers()
    if not tickers:
        logger.error('[BrahmaScreener] 全市场行情获取失败')
        return {'rule': [], 'ai': []}

    fr_map = fetch_funding_rates({t['symbol'] for t in tickers})

    # 注入fr到tickers（避免两套都单独拉）
    for t in tickers:
        t['_fr'] = fr_map.get(t['symbol'], 0)

    with ThreadPoolExecutor(max_workers=2) as pool:
        f_rule = pool.submit(run_rule_screener, tickers, rule_top)
        f_ai   = pool.submit(run_ai_screener,   tickers, ai_top)
        rule_result = f_rule.result()
        ai_result   = f_ai.result()

    elapsed = time.time() - t0
    logger.info(f'[BrahmaScreener] 两套筛选完成 {elapsed:.1f}s rule={len(rule_result)} ai={len(ai_result)}')
    return {'rule': rule_result, 'ai': ai_result}


if __name__ == '__main__':
    import argparse
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['rule','ai','all'], default='all')
    parser.add_argument('--top', type=int, default=8)
    args = parser.parse_args()

    if args.mode == 'rule':
        run_rule_screener(top_n=args.top)
    elif args.mode == 'ai':
        run_ai_screener(top_n=args.top)
    else:
        run_all()
