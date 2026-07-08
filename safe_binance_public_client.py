"""
safe_binance_public_client.py — 第三方v4.0动态审计补丁包 Step5
只读公开行情客户端，完全无认证，无下单能力。
"""
from __future__ import annotations
import requests
import time
from typing import Optional, Dict, List

FAPI_BASE = "https://fapi.binance.com"
_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "BrahmaAuditClient/1.0"})


def _get(path: str, params: Optional[Dict] = None, timeout: int = 8) -> dict | list:
    """纯 GET，无签名，无 API key。仅公开端点。"""
    url = FAPI_BASE + path
    r = _SESSION.get(url, params=params or {}, timeout=timeout)
    r.raise_for_status()
    return r.json()


# ── 公开行情 API ─────────────────────────────────────────────────

def get_ticker_price(symbol: str) -> float:
    """返回标的最新成交价。"""
    data = _get("/fapi/v1/ticker/price", {"symbol": symbol.upper()})
    return float(data["price"])


def get_ticker_24h(symbol: str) -> dict:
    """24h 行情统计。"""
    return _get("/fapi/v1/ticker/24hr", {"symbol": symbol.upper()})


def get_klines(symbol: str, interval: str = "1h", limit: int = 100) -> List[List]:
    """K线数据。interval: 1m/5m/15m/1h/4h/1d"""
    return _get("/fapi/v1/klines", {"symbol": symbol.upper(), "interval": interval, "limit": limit})


def get_open_interest(symbol: str) -> dict:
    """当前合约未平仓量。"""
    return _get("/fapi/v1/openInterest", {"symbol": symbol.upper()})


def get_funding_rate(symbol: str, limit: int = 10) -> List[dict]:
    """资金费率历史。"""
    return _get("/fapi/v1/fundingRate", {"symbol": symbol.upper(), "limit": limit})


def get_long_short_ratio(symbol: str, period: str = "1h", limit: int = 10) -> List[dict]:
    """多空比。period: 5m/15m/30m/1h/2h/4h/6h/12h/1d"""
    return _get("/futures/data/globalLongShortAccountRatio",
                {"symbol": symbol.upper(), "period": period, "limit": limit})


def get_top_trader_ratio(symbol: str, period: str = "1h", limit: int = 10) -> List[dict]:
    """顶级交易员多空比。"""
    return _get("/futures/data/topLongShortAccountRatio",
                {"symbol": symbol.upper(), "period": period, "limit": limit})


def batch_snapshot(symbols: List[str]) -> Dict[str, dict]:
    """批量获取多个标的的行情快照。"""
    result = {}
    for sym in symbols:
        try:
            t24h = get_ticker_24h(sym)
            oi = get_open_interest(sym)
            result[sym] = {
                "price": float(t24h["lastPrice"]),
                "change_pct": float(t24h["priceChangePercent"]),
                "volume_24h": float(t24h["quoteVolume"]),
                "high_24h": float(t24h["highPrice"]),
                "low_24h": float(t24h["lowPrice"]),
                "open_interest": float(oi["openInterest"]),
                "ts": int(time.time()),
            }
        except Exception as e:
            result[sym] = {"error": str(e)}
    return result


if __name__ == "__main__":
    import json
    print("=== SafeBinancePublicClient 自检 ===")
    for sym in ["BTCUSDT", "ETHUSDT"]:
        price = get_ticker_price(sym)
        oi = get_open_interest(sym)
        fr = get_funding_rate(sym, limit=1)
        print(f"\n{sym}:")
        print(f"  Price:    ${price:,.2f}")
        print(f"  OI:       {float(oi['openInterest']):,.0f}")
        print(f"  Funding:  {float(fr[0]['fundingRate'])*100:.4f}%")
    snap = batch_snapshot(["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    print("\n批量快照:")
    print(json.dumps(snap, indent=2, ensure_ascii=False))
    print("\n✅ SafeBinancePublicClient OK — 无认证，无下单能力")
