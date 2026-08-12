#!/usr/bin/env python3
"""

# STATUS: ACTIVE
# 清算扫描器，三所聚合
# LAST_REVIEW: 2026-07-01 | 属于辅助计算层，修改前确认调用链
# ─────────────────────────────────────────────────────────────
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

    # ── 9. Bybit 三所数据接入 ─────────────────────────────────
    # [2026-08-12 设计院] 正式接入Bybit+Hyperliquid三所清算集群
    BYBIT = "https://api.bybit.com"
    HL_API = "https://api.hyperliquid.xyz"
    sym_bybit = sym  # Bybit用相同格式 BTCUSDT
    sym_base  = sym.replace("USDT", "")

    # Bybit OI
    bybit_oi_raw = _fetch(
        f"{BYBIT}/v5/market/open-interest?category=linear&symbol={sym_bybit}&intervalTime=5min&limit=1",
        ttl=30
    )
    bybit_oi_val = 0.0
    try:
        bybit_oi_val = float(
            bybit_oi_raw.get("result", {}).get("list", [{}])[0].get("openInterest", 0)
        ) * price / 1e9
    except Exception:
        pass
    result["bybit_oi_b"] = round(bybit_oi_val, 3)

    # Bybit 多空比
    bybit_ls_raw = _fetch(
        f"{BYBIT}/v5/market/account-ratio?category=linear&symbol={sym_bybit}&period=5min&limit=1",
        ttl=60
    )
    result["bybit_long_pct"] = 0.0
    result["bybit_short_pct"] = 0.0
    try:
        bb_ls = bybit_ls_raw.get("result", {}).get("list", [])
        if bb_ls:
            result["bybit_long_pct"]  = round(float(bb_ls[0]["buyRatio"]) * 100, 1)
            result["bybit_short_pct"] = round(float(bb_ls[0]["sellRatio"]) * 100, 1)
    except Exception:
        pass

    # Bybit FR + 价格
    bybit_tk_raw = _fetch(
        f"{BYBIT}/v5/market/tickers?category=linear&symbol={sym_bybit}",
        ttl=30
    )
    result["bybit_fr"] = 0.0
    result["bybit_price"] = 0.0
    try:
        bb_info = bybit_tk_raw.get("result", {}).get("list", [{}])[0]
        result["bybit_fr"]    = round(float(bb_info.get("fundingRate", 0)) * 100, 4)
        result["bybit_price"] = float(bb_info.get("lastPrice", 0))
    except Exception:
        pass

    # Hyperliquid OI + FR + 清算位
    result["hl_oi_b"]       = 0.0
    result["hl_fr"]         = 0.0
    result["hl_liq_50x_long"]  = round(price * 0.98, 2)  # 50x多头默认-2%
    result["hl_liq_50x_short"] = round(price * 1.02, 2)  # 50x空头默认+2%
    result["hl_liq_25x_long"]  = round(price * 0.96, 2)  # 25x多头默认-4%
    result["hl_liq_25x_short"] = round(price * 1.04, 2)  # 25x空头默认+4%
    try:
        import json as _json, urllib.request as _req
        hl_req = _req.Request(
            f"{HL_API}/info",
            data=_json.dumps({"type": "metaAndAssetCtxs"}).encode(),
            headers={"Content-Type": "application/json"}
        )
        hl_raw = _json.loads(_req.urlopen(hl_req, timeout=6).read())
        meta_list = hl_raw[0].get("universe", []) if isinstance(hl_raw, list) else []
        ctx_list  = hl_raw[1] if isinstance(hl_raw, list) and len(hl_raw) > 1 else []
        for i, m in enumerate(meta_list):
            if m.get("name", "").upper() == sym_base.upper():
                if i < len(ctx_list):
                    ctx = ctx_list[i]
                    oi_coins = float(ctx.get("openInterest", 0))
                    hl_price = float(ctx.get("markPx", price))
                    result["hl_oi_b"] = round(oi_coins * hl_price / 1e9, 3)
                    result["hl_fr"]   = round(float(ctx.get("funding", 0)) * 100, 4)
                    # Hyperliquid 清算估算（基于杠杆档位）
                    result["hl_liq_50x_long"]  = round(hl_price * (1 - 1/50  * 0.9), 4)
                    result["hl_liq_50x_short"] = round(hl_price * (1 + 1/50  * 0.9), 4)
                    result["hl_liq_25x_long"]  = round(hl_price * (1 - 1/25  * 0.9), 4)
                    result["hl_liq_25x_short"] = round(hl_price * (1 + 1/25  * 0.9), 4)
                break
    except Exception:
        pass

    # 三所汇总OI
    result["total_oi_b"] = round(
        result["oi_b"] + result["bybit_oi_b"] + result["hl_oi_b"], 3
    )

    # ── 10. 综合清算偏向判断（三所加权）────────────────────────
    long_pct    = result["long_pct"]
    top_long    = result["top_long_pct"]
    fund        = result["fund_rate"]
    oi_chg      = result["oi_chg4h"]
    bb_long     = result["bybit_long_pct"]
    bb_fr       = result["bybit_fr"]

    # 三所加权多空比（BN权重0.6 / Bybit权重0.3 / HL无数据权重0.1）
    if bb_long > 0:
        weighted_long = long_pct * 0.6 + bb_long * 0.3 + 50 * 0.1
    else:
        weighted_long = long_pct
    result["weighted_long_pct"] = round(weighted_long, 1)

    # 三所FR一致性（BN+Bybit同向拥挤 → 信号更强）
    fr_agreement = (fund > 0.05 and bb_fr > 0.05) or (fund < -0.05 and bb_fr < -0.05)
    result["fr_cross_agreement"] = fr_agreement

    risks = []
    if long_pct > 68:
        risks.append(f"⚠️ BN散户{long_pct:.0f}%做多（极度拥挤）")
    if bb_long > 68:
        risks.append(f"⚠️ Bybit散户{bb_long:.0f}%做多（双所极拥挤🚨）")
    if long_pct > 65 and bb_long > 65:
        risks.append("🚨 双所散户同步极度拥挤→逆向做空信号极强")
    if top_long < 45:
        risks.append(f"⚠️ 大户仅{top_long:.0f}%做多（主力偏空）")
    if fund > 0.08:
        risks.append(f"⚠️ BN资金费{fund:+.3f}%（多头付费拥挤）")
    if bb_fr > 0.05:
        risks.append(f"⚠️ Bybit资金费{bb_fr:+.4f}%（多头付费）")
    if fr_agreement and fund > 0.05:
        risks.append("🚨 三所FR同向偏高→多头全线付费")
    if oi_chg > 10:
        risks.append(f"📈 OI 4h+{oi_chg:.1f}%（仓位快速堆积）")
    # Hyperliquid 50x清算位提示
    hl_50_long = result["hl_liq_50x_long"]
    hl_50_short = result["hl_liq_50x_short"]
    hl_50_long_pct  = round((price - hl_50_long)  / price * 100, 2)
    hl_50_short_pct = round((hl_50_short - price) / price * 100, 2)
    risks.append(f"HL 50x多头清算: ${hl_50_long:,.2f}(-{hl_50_long_pct}%) | 空头清算: ${hl_50_short:,.2f}(+{hl_50_short_pct}%)")

    # 三所加权偏向判断
    if weighted_long > 65 and top_long < 50:
        result["liq_bias"] = "BEARISH"    # 三所散户多/主力空 → 偏空
    elif weighted_long < 40 and top_long > 55:
        result["liq_bias"] = "BULLISH"    # 三所散户空/主力多 → 偏多
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
