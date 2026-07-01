#!/usr/bin/env python3
"""

# ── STATUS: AUXILIARY ──────────────────────────────────────────
# CVD累计成交量差，s12维度
# LAST_REVIEW: 2026-07-01 | 属于辅助计算层，修改前确认调用链
# ─────────────────────────────────────────────────────────────
cvd_engine.py — 多周期 CVD 引擎 v1.0
星枢引擎 Layer 1 · 设计院 2026-06-09

CVD（Cumulative Volume Delta）= 主动买方成交量 - 主动卖方成交量

数据来源（优先级）：
  1. Binance Taker Long/Short Ratio（周期级别，最准）
  2. Binance aggTrades（短周期微观，最新500条）
  3. Kline takerBuyBaseAssetVolume（降级fallback）

多周期输出：
  - micro:  5m × 12根（1H微观买卖压力）
  - meso:   1h × 24根（日内中期趋势）
  - macro:  4h × 14根（多日宏观方向）
  - signal: 综合方向 + 背离 + 梯度

评分贡献（接入 enhanced_signal_engine）：
  SHORT方向：macro SELL + meso SELL → +6
             macro SELL + micro SELL → +4
             单独 meso SELL → +2
             背离（价格涨但CVD降）→ +2
  LONG方向：反向同理
"""

import time
import json
import urllib.request

FAPI = "https://fapi.binance.com"
_cache: dict = {}


def _get(url: str, ttl: int = 30):
    now = time.time()
    if url in _cache and now - _cache[url][0] < ttl:
        return _cache[url][1]
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())
            _cache[url] = (now, data)
            return data
    except Exception:
        return None


def _taker_cvd(symbol: str, period: str, limit: int) -> list[float]:
    """用 Taker Long/Short Ratio 计算各周期 CVD 序列（买卖量差）"""
    url = (f"{FAPI}/futures/data/takerlongshortRatio"
           f"?symbol={symbol}&period={period}&limit={limit}")
    data = _get(url, ttl=60)
    if not data:
        return []
    series = []
    for d in data:
        buy_v  = float(d.get("buyVol",  0) or 0)
        sell_v = float(d.get("sellVol", 0) or 0)
        series.append(buy_v - sell_v)
    return series


def _kline_cvd(symbol: str, interval: str, limit: int) -> list[float]:
    """用 Kline takerBuyBaseAssetVolume 计算 CVD（降级方案）"""
    url = (f"{FAPI}/fapi/v1/klines"
           f"?symbol={symbol}&interval={interval}&limit={limit}")
    data = _get(url, ttl=60)
    if not data:
        return []
    series = []
    for k in data:
        total_vol = float(k[5])
        taker_buy = float(k[9])
        taker_sell = total_vol - taker_buy
        series.append(taker_buy - taker_sell)
    return series


def _aggTrades_cvd(symbol: str) -> float:
    """最新500条 aggTrades 微观CVD（绝对量）"""
    url = f"{FAPI}/fapi/v1/aggTrades?symbol={symbol}&limit=500"
    trades = _get(url, ttl=15)
    if not trades:
        return 0.0
    buy_vol  = sum(float(t["q"]) for t in trades if not t["m"])
    sell_vol = sum(float(t["q"]) for t in trades if t["m"])
    return buy_vol - sell_vol


def _classify(series: list[float]) -> dict:
    """分析 CVD 序列方向、强度、梯度"""
    if not series:
        return {"direction": "UNKNOWN", "strength": 0, "gradient": 0.0, "score": 0}

    recent = sum(series[-5:])
    total  = sum(series)
    n      = len(series)

    # 梯度：后半段 vs 前半段
    half = n // 2
    front = sum(series[:half]) if half else 0
    back  = sum(series[half:]) if half else 0
    gradient = (back - front) / (abs(front) + 1e-9)

    direction = "SELL" if recent < 0 else "BUY"
    # 强度：连续同向比例
    same_dir = sum(1 for v in series[-5:] if (v < 0) == (recent < 0))
    strength = same_dir  # 0~5

    # 评分：连续性 + 梯度加强
    score = strength
    if abs(gradient) > 0.3 and (gradient < 0) == (recent < 0):
        score += 1  # 加速

    return {
        "direction":  direction,
        "strength":   strength,
        "gradient":   round(gradient, 3),
        "recent_sum": round(recent, 2),
        "score":      min(score, 6),
    }


def get_multi_tf_cvd(symbol: str) -> dict:
    """
    主接口：获取多周期 CVD 分析
    返回 micro / meso / macro 三层 + 综合评分
    """
    sym = symbol.upper()
    if not sym.endswith("USDT"):
        sym += "USDT"

    # 三个周期
    micro_series = _taker_cvd(sym, "5m",  12) or _kline_cvd(sym, "5m",  12)
    meso_series  = _taker_cvd(sym, "1h",  24) or _kline_cvd(sym, "1h",  24)
    macro_series = _taker_cvd(sym, "4h",  14) or _kline_cvd(sym, "4h",  14)

    micro = _classify(micro_series)
    meso  = _classify(meso_series)
    macro = _classify(macro_series)

    # 微观绝对量（aggTrades）
    spot_cvd = _aggTrades_cvd(sym)

    # 当前价格趋势（1H）
    k1h = _get(f"{FAPI}/fapi/v1/klines?symbol={sym}&interval=1h&limit=6", ttl=60)
    if k1h and len(k1h) >= 2:
        price_up = float(k1h[-1][4]) > float(k1h[0][4])
    else:
        price_up = None

    # 背离检测
    divergence = None
    divergence_type = None
    if price_up is not None and macro["direction"] != "UNKNOWN":
        macro_buy = macro["direction"] == "BUY"
        if price_up and not macro_buy:
            divergence = True
            divergence_type = "BEARISH_DIV"   # 价格涨但宏观CVD降 → 顶背离
        elif not price_up and macro_buy:
            divergence = True
            divergence_type = "BULLISH_DIV"   # 价格跌但宏观CVD升 → 底背离

    # 综合评分（供 enhanced_signal_engine 调用）
    def score_for_dir(direction: str) -> tuple[int, list[str]]:
        is_sell = direction == "SHORT"
        notes = []
        s = 0
        macro_match = (macro["direction"] == "SELL") == is_sell
        meso_match  = (meso["direction"]  == "SELL") == is_sell
        micro_match = (micro["direction"] == "SELL") == is_sell

        if macro_match and meso_match:
            s += 6; notes.append(f"CVD宏观+中期{'卖方' if is_sell else '买方'}主导 +6")
        elif macro_match and micro_match:
            s += 4; notes.append(f"CVD宏观+微观{'卖方' if is_sell else '买方'}主导 +4")
        elif macro_match:
            s += 3; notes.append(f"CVD宏观{'卖方' if is_sell else '买方'}主导 +3")
        elif meso_match:
            s += 2; notes.append(f"CVD中期{'卖方' if is_sell else '买方'}主导 +2")

        # 背离加分
        if divergence:
            if is_sell and divergence_type == "BEARISH_DIV":
                s += 2; notes.append("CVD顶背离 +2")
            elif not is_sell and divergence_type == "BULLISH_DIV":
                s += 2; notes.append("CVD底背离 +2")

        return min(s, 8), notes

    long_score,  long_notes  = score_for_dir("LONG")
    short_score, short_notes = score_for_dir("SHORT")

    return {
        "symbol":     sym,
        "micro":      micro,
        "meso":       meso,
        "macro":      macro,
        "spot_cvd":   round(spot_cvd, 2),
        "divergence": divergence,
        "divergence_type": divergence_type,
        "price_up":   price_up,
        "scores": {
            "LONG":  {"score": long_score,  "notes": long_notes},
            "SHORT": {"score": short_score, "notes": short_notes},
        },
    }


def cvd_score_for_signal(symbol: str, direction: str) -> tuple[int, list[str]]:
    """
    简化接口：供 enhanced_signal_engine.enhanced_score() 调用
    返回 (score, notes)
    """
    try:
        result = get_multi_tf_cvd(symbol)
        d = direction.upper()
        if d in ("做多", "LONG"):
            d = "LONG"
        elif d in ("做空", "SHORT"):
            d = "SHORT"
        else:
            return 0, []
        entry = result["scores"].get(d, {})
        return entry.get("score", 0), entry.get("notes", [])
    except Exception:
        return 0, []


if __name__ == "__main__":
    import sys
    symbols = sys.argv[1:] if len(sys.argv) > 1 else ["ETHUSDT", "BTCUSDT"]
    for sym in symbols:
        print(f"\n{'='*55}")
        print(f"  {sym} 多周期 CVD")
        print('='*55)
        r = get_multi_tf_cvd(sym)
        print(f"  Macro(4H): {r['macro']['direction']}  strength={r['macro']['strength']}  gradient={r['macro']['gradient']}")
        print(f"  Meso (1H): {r['meso']['direction']}  strength={r['meso']['strength']}")
        print(f"  Micro(5M): {r['micro']['direction']}  strength={r['micro']['strength']}")
        print(f"  Spot CVD:  {r['spot_cvd']:+.2f}")
        print(f"  背离: {r['divergence_type'] or '无'}")
        print(f"  SHORT评分: {r['scores']['SHORT']['score']}  {r['scores']['SHORT']['notes']}")
        print(f"  LONG 评分: {r['scores']['LONG']['score']}   {r['scores']['LONG']['notes']}")
