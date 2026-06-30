"""
alpha_market_filter.py
主力建仓猎手 · 市值动态过滤器
从全市场合约中筛选小市值Alpha标的池
2026-05-22 设计院
"""
import sys, os, json, time
# 并入 brahma_brain (设计院 2026-05-23)
# 参数直接内联（原来自 alpha_hunter_config）
MCAP_MIN_USD   = 500_000
MCAP_MAX_USD   = 100_000_000
VOL24H_MIN_USD = 200_000
VOL24H_MAX_USD = 100_000_000
SYMBOL_BLACKLIST = [
    'BTCUSDT','ETHUSDT','BNBUSDT','SOLUSDT','XRPUSDT',
    'USDCUSDT','USDTUSDT','BUSDUSDT','DAIUSDT',
    'ADAUSDT','DOGEUSDT','AVAXUSDT','DOTUSDT',
]

# ── Binance API ────────────────────────────────────────────────
def _get_all_futures_tickers() -> list:
    """拉取全市场合约24H行情"""
    try:
        import urllib.request
        url = "https://fapi.binance.com/fapi/v1/ticker/24hr"
        req = urllib.request.Request(url, headers={'User-Agent': 'brahma/1.0'})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"[AlphaFilter] ticker fetch error: {e}")
        return []


def _estimate_mcap(symbol: str, price: float, vol24h_usdt: float) -> float:
    """
    市值估算：合约市场无直接市值数据
    用日成交量推算流通市值：mcap ≈ vol24h / turnover_rate
    turnover_rate 小市值币约 10-30%/天，取中值20%
    """
    if vol24h_usdt <= 0:
        return 0
    estimated_mcap = vol24h_usdt / 0.20  # 20%换手率估算
    return estimated_mcap


def get_alpha_universe(verbose: bool = False) -> list[dict]:
    """
    获取Alpha标的池
    返回符合市值/成交量条件的合约列表
    """
    tickers = _get_all_futures_tickers()
    if not tickers:
        return []

    candidates = []
    for t in tickers:
        sym = t.get('symbol', '')
        if not sym.endswith('USDT'):
            continue
        if sym in SYMBOL_BLACKLIST:
            continue

        try:
            price     = float(t.get('lastPrice', 0))
            vol24h    = float(t.get('quoteVolume', 0))   # USDT计价成交额
            chg24     = float(t.get('priceChangePercent', 0))
            high24    = float(t.get('highPrice', 0))
            low24     = float(t.get('lowPrice', 0))
        except:
            continue

        if price <= 0 or vol24h <= 0:
            continue

        # 成交量过滤（主要门槛，市值用成交量估算）
        if vol24h < VOL24H_MIN_USD:
            continue
        if vol24h > VOL24H_MAX_USD:
            continue

        # 估算市值
        mcap_est = _estimate_mcap(sym, price, vol24h)
        if mcap_est < MCAP_MIN_USD:
            continue
        if mcap_est > MCAP_MAX_USD:
            continue

        candidates.append({
            'symbol':    sym,
            'price':     price,
            'vol24h':    round(vol24h),
            'chg24':     round(chg24, 2),
            'high24':    high24,
            'low24':     low24,
            'mcap_est':  round(mcap_est),
        })

    # 按成交量排序
    candidates.sort(key=lambda x: x['vol24h'], reverse=True)

    if verbose:
        print(f"[AlphaFilter] 全市场: {len(tickers)} 只 → Alpha池: {len(candidates)} 只")
        for c in candidates[:10]:
            print(f"  {c['symbol']:<16} vol={c['vol24h']/1e6:.1f}M "
                  f"mcap≈{c['mcap_est']/1e6:.1f}M chg={c['chg24']:+.1f}%")

    return candidates


def get_surge_universe() -> list[dict]:
    """
    获取异动池：今日涨跌幅≥15% + 成交量满足条件
    专门捕获启动中的品种
    """
    universe = get_alpha_universe()
    surge = [c for c in universe if abs(c['chg24']) >= 15.0]
    surge.sort(key=lambda x: abs(x['chg24']), reverse=True)
    return surge


if __name__ == '__main__':
    print("=== Alpha标的池 ===")
    pool = get_alpha_universe(verbose=True)
    print(f"\n=== 今日异动 (|chg|≥15%) ===")
    surge = get_surge_universe()
    for s in surge[:10]:
        print(f"  {s['symbol']:<16} {s['chg24']:+.1f}% vol={s['vol24h']/1e6:.1f}M")
