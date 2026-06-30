#!/usr/bin/env python3
"""
liq_scanner.py — 清算数据扫描器 v1.0
设计院 2026-05-25

数据来源：Binance 公开接口（无需API Key）
功能：
  1. 实时多空账户比 + 大户持仓比
  2. OI持仓量
  3. 资金费率
  4. 清算价格带估算（基于杠杆分布推算）
  5. Coinglass Key 有效时补充真实清算热力图

输出：结构化清算快照，供 brahma_matrix / 手动分析使用
"""

import json
import time
import urllib.request
from datetime import datetime, timezone, timedelta

FAPI = "https://fapi.binance.com"
_cache = {}


def _fetch(url: str, ttl: int = 30) -> dict | list | None:
    now = time.time()
    if url in _cache and now - _cache[url][0] < ttl:
        return _cache[url][1]
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=6) as r:
            data = json.loads(r.read())
            _cache[url] = (now, data)
            return data
    except Exception:
        return None


def get_liq_snapshot(symbol: str) -> dict:
    """
    获取完整清算快照
    返回标准化字典，所有字段保证存在（失败为0/N/A）
    """
    sym = symbol.upper().replace("USDT", "") + "USDT"
    sym_base = sym.replace("USDT", "")

    result = {
        "symbol":       sym,
        "price":        0.0,
        "chg24":        0.0,
        "vol24b":       0.0,   # 成交额（十亿美元）
        "oi_b":         0.0,   # OI（十亿美元）
        "oi_chg4h":     0.0,   # 4h OI变化%
        "long_pct":     50.0,  # 散户多头%
        "short_pct":    50.0,
        "top_long_pct": 50.0,  # 大户多头%
        "fund_rate":    0.0,   # 资金费率%
        "fund_bias":    "NEUTRAL",
        # 清算带估算（基于杠杆分布）
        "liq_short_5pct":  0.0,  # 空头被轧（上方5%，20x）
        "liq_short_10pct": 0.0,  # 空头被轧（上方10%，10x）
        "liq_long_5pct":   0.0,  # 多头踩踏（下方5%，20x）
        "liq_long_10pct":  0.0,  # 多头踩踏（下方10%，10x）
        # 综合清算偏向
        "liq_bias":     "NEUTRAL",
        "liq_risk":     "",    # 文字风险提示
        # Coinglass 真实数据（有Key时补充）
        "cg_long_liq_m":  None,
        "cg_short_liq_m": None,
        "cg_available":   False,
        "ts": datetime.now(timezone(timedelta(hours=8))).strftime("%H:%M 北京时间"),
    }

    # ── 1. 现价 + 24h数据 ─────────────────────────────────────
    t = _fetch(f"{FAPI}/fapi/v1/ticker/24hr?symbol={sym}", ttl=15)
    if t and isinstance(t, dict):
        result["price"]  = float(t.get("lastPrice", 0))
        result["chg24"]  = float(t.get("priceChangePercent", 0))
        result["vol24b"] = float(t.get("quoteVolume", 0)) / 1e9
    if result["price"] == 0:
        return result

    price = result["price"]

    # ── 2. OI 实时 ────────────────────────────────────────────
    oi = _fetch(f"{FAPI}/fapi/v1/openInterest?symbol={sym}", ttl=30)
    if oi:
        result["oi_b"] = float(oi.get("openInterest", 0)) * price / 1e9

    # ── 3. OI 历史（4h变化）──────────────────────────────────
    oi_hist = _fetch(
        f"{FAPI}/futures/data/openInterestHist?symbol={sym}&period=4h&limit=2",
        ttl=120
    )
    if oi_hist and len(oi_hist) >= 2:
        v0 = float(oi_hist[0].get("sumOpenInterest", 0))
        v1 = float(oi_hist[-1].get("sumOpenInterest", 0))
        if v0 > 0:
            result["oi_chg4h"] = round((v1 - v0) / v0 * 100, 2)

    # ── 4. 多空账户比（散户）────────────────────────────────
    ls = _fetch(
        f"{FAPI}/futures/data/globalLongShortAccountRatio?symbol={sym}&period=1h&limit=1",
        ttl=60
    )
    if ls and isinstance(ls, list) and ls:
        result["long_pct"]  = float(ls[0].get("longAccount",  0.5)) * 100
        result["short_pct"] = float(ls[0].get("shortAccount", 0.5)) * 100

    # ── 5. 大户持仓比 ─────────────────────────────────────────
    top = _fetch(
        f"{FAPI}/futures/data/topLongShortPositionRatio?symbol={sym}&period=1h&limit=1",
        ttl=60
    )
    if top and isinstance(top, list) and top:
        result["top_long_pct"] = float(top[0].get("longAccount", 0.5)) * 100

    # ── 6. 资金费率 ───────────────────────────────────────────
    fr = _fetch(f"{FAPI}/fapi/v1/premiumIndex?symbol={sym}", ttl=60)
    if fr and isinstance(fr, dict):
        rate = float(fr.get("lastFundingRate", 0)) * 100
        result["fund_rate"] = round(rate, 4)
        if rate > 0.05:
            result["fund_bias"] = "LONG_CROWDED"   # 多头拥挤
        elif rate < -0.05:
            result["fund_bias"] = "SHORT_CROWDED"  # 空头拥挤

    # ── 7. 清算价格带估算 ──────────────────────────────────────
    result["liq_short_5pct"]  = round(price * 1.05, 1)   # 空头20x清算位
    result["liq_short_10pct"] = round(price * 1.10, 1)   # 空头10x清算位
    result["liq_long_5pct"]   = round(price * 0.95, 1)   # 多头20x清算位
    result["liq_long_10pct"]  = round(price * 0.90, 1)   # 多头10x清算位

    # ── 8. Tardis 真实清算墙（星枢引擎 Layer 0）───────────────
    try:
        import sys, os
        sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
        from brahma_brain.tardis_liq_layer import get_tardis_liq_walls
        tdw = get_tardis_liq_walls(sym)
        result["tardis_walls"] = tdw
        result["cg_available"] = tdw.get("available", False)
        if tdw.get("available"):
            # 用 Tardis 真实主导清算位替换估算值
            ld = tdw.get("long_dominant_price",  0)
            sd = tdw.get("short_dominant_price", 0)
            if ld > 0: result["liq_long_5pct"]  = ld
            if sd > 0: result["liq_short_5pct"] = sd
    except Exception:
        result["tardis_walls"] = {"available": False}
        result["cg_available"] = False

    # ── 8b. Coinglass 字段兼容（Key 已失效，保留字段）───────
    result.setdefault("cg_long_liq_m",  None)
    result.setdefault("cg_short_liq_m", None)

    # ── 8c. ws_guardian 实时清算流（最新1H真实数据）─────────
    try:
        import sys as _sys, os as _os
        _sys.path.insert(0, _os.path.dirname(_os.path.dirname(__file__)))
        from ws_guardian import get_liq_stats
        live_liq = get_liq_stats(sym)
        result["live_liq"] = live_liq
        if live_liq.get("available"):
            # 实时数据覆盖 Tardis 月初快照
            result["cg_long_liq_m"]  = round(live_liq["long_usd_1h"]  / 1e6, 3)
            result["cg_short_liq_m"] = round(live_liq["short_usd_1h"] / 1e6, 3)
            result["cg_available"]   = True
    except Exception:
        result.setdefault("live_liq", {"available": False})

    # ── 9. 综合清算偏向判断 ──────────────────────────────────
    long_pct = result["long_pct"]
    top_long = result["top_long_pct"]
    fund     = result["fund_rate"]
    oi_chg   = result["oi_chg4h"]

    risks = []
    if long_pct > 68:
        risks.append(f"⚠️ 散户{long_pct:.0f}%做多（极度拥挤→逆向看空）")
    if top_long < 45:
        risks.append(f"⚠️ 大户仅{top_long:.0f}%做多（主力偏空）")
    if fund > 0.08:
        risks.append(f"⚠️ 资金费率{fund:+.3f}%（多头付费，拥挤）")
    if oi_chg > 10:
        risks.append(f"📈 OI 4h+{oi_chg:.1f}%（仓位快速堆积）")

    if long_pct > 65 and top_long < 50:
        result["liq_bias"] = "BEARISH"    # 散户多/主力空 → 偏空
    elif long_pct < 40 and top_long > 55:
        result["liq_bias"] = "BULLISH"    # 散户空/主力多 → 偏多
    else:
        result["liq_bias"] = "NEUTRAL"

    result["liq_risk"] = " | ".join(risks) if risks else "暂无极端信号"

    return result


def format_report(snap: dict) -> str:
    """格式化清算快照为可读报告"""
    sym   = snap["symbol"].replace("USDT", "")
    price = snap["price"]
    p     = lambda v: f"${v:,.1f}" if v > 100 else f"${v:.4f}" if v < 1 else f"${v:.2f}"

    lines = [
        f"━━━ {sym}/USDT 清算快照 {snap['ts']} ━━━",
        f"现价: {p(price)}  24h: {snap['chg24']:+.1f}%  成交: ${snap['vol24b']:.1f}B",
        f"OI持仓: ${snap['oi_b']:.2f}B  4H变化: {snap['oi_chg4h']:+.1f}%",
        "",
        f"多空结构:",
        f"  散户: 多{snap['long_pct']:.1f}% / 空{snap['short_pct']:.1f}%",
        f"  大户: 多{snap['top_long_pct']:.1f}%",
        f"  资金费率: {snap['fund_rate']:+.4f}%  ({snap['fund_bias']})",
        "",
        f"清算价格带 (估算):",
        f"  🔺 空头清算位: {p(snap['liq_short_5pct'])}(20x) / {p(snap['liq_short_10pct'])}(10x)",
        f"  🔻 多头清算位: {p(snap['liq_long_5pct'])}(20x) / {p(snap['liq_long_10pct'])}(10x)",
    ]

    # Tardis 真实清算墙（星枢引擎）
    tdw = snap.get("tardis_walls", {})
    if tdw.get("available"):
        try:
            from brahma_brain.tardis_liq_layer import format_liq_walls
            lines.append(format_liq_walls(tdw, snap["symbol"]))
        except Exception:
            lines.append(f"  📡 Tardis真实数据已接入（{tdw.get('date','')}）")
    else:
        lines.append(f"  ⚠️ Tardis数据不可用，清算位为估算值")

    lines += [
        "",
        f"综合清算偏向: {snap['liq_bias']}",
        f"{snap['liq_risk']}",
    ]

    return "\n".join(lines)


def scan_majors() -> str:
    """快速扫描BTC+ETH，返回双币清算报告"""
    out = []
    for sym in ["BTCUSDT", "ETHUSDT"]:
        snap = get_liq_snapshot(sym)
        out.append(format_report(snap))
    return "\n\n".join(out)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="", help="指定品种，不填则BTC+ETH")
    parser.add_argument("--json",   action="store_true", help="JSON输出")
    args = parser.parse_args()

    if args.symbol:
        snap = get_liq_snapshot(args.symbol)
        if args.json:
            print(json.dumps(snap, ensure_ascii=False, indent=2))
        else:
            print(format_report(snap))
    else:
        print(scan_majors())
