#!/usr/bin/env python3
"""
梵天MCP服务器 [封印 2026-10-26 苏摩111]
接入位置：brahma_mcp_server.py → FastMCP工具组
把brahma_deep_analysis.py包装为MCP工具
响应时间：price=0.13s / vip=1.33s / full=4s
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/scripts')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/brahma_brain')

from fastmcp import FastMCP

mcp = FastMCP(
    "梵天量化分析系统 v2.0",
    "梵天设计院 BTC/ETH 合约分析引擎 — 响应<5s"
)


@mcp.tool()
def brahma_analyze(symbols: str = "BTC ETH") -> str:
    """
    梵天全维度实时分析（完整94维报告+VIP策略）。
    symbols: 空格分隔，如 'BTC ETH' 或 'BTC'
    耗时约4s。
    """
    sym_list = [s.strip().upper() for s in symbols.split() if s.strip()] or ["BTC", "ETH"]
    from brahma_deep_analysis import run_deep_analysis
    return run_deep_analysis(sym_list)


@mcp.tool()
def brahma_vip(symbol: str = "ETH") -> str:
    """
    VIP策略快速输出（最快路径，耗时约1.3s）。
    symbol: 'BTC' 或 'ETH'
    返回：姓赵不宣格式VIP策略卡片
    """
    from brahma_deep_analysis import fetch_all, enrich, reason_40yr, format_vip
    raw = fetch_all(symbol.upper())
    d   = enrich(raw)
    r   = reason_40yr(d)
    return format_vip(d, r)


@mcp.tool()
def brahma_price(symbol: str = "BTC") -> dict:
    """
    实时价格+关键指标（最快，耗时约0.1s）。
    返回：价格/LSR/Taker/FR/100x清算位
    """
    import urllib.request, json
    sym = symbol.upper() + "USDT"
    def f(u):
        try:
            with urllib.request.urlopen(u, timeout=5) as r: return json.loads(r.read())
        except: return {}
    px  = float(f(f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={sym}').get('price', 0))
    lsg = f(f'https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol={sym}&period=5m&limit=1')
    lsa = f(f'https://fapi.binance.com/futures/data/topLongShortAccountRatio?symbol={sym}&period=5m&limit=1')
    tk  = f(f'https://fapi.binance.com/futures/data/takerlongshortRatio?symbol={sym}&period=5m&limit=1')
    fr  = f(f'https://fapi.binance.com/fapi/v1/premiumIndex?symbol={sym}')
    oi  = f(f'https://fapi.binance.com/futures/data/openInterestHist?symbol={sym}&period=1h&limit=2')
    oi1h = 0
    if oi and len(oi) >= 2:
        v = [float(x['sumOpenInterest']) for x in oi]
        oi1h = round((v[-1]-v[-2])/v[-2]*100, 3)
    return {
        "symbol":   symbol.upper(),
        "price":    px,
        "lsr_retail": round(float(lsg[0]['longShortRatio']), 3) if lsg else 0,
        "lsr_top_account": round(float(lsa[0]['longShortRatio']), 3) if lsa else 0,
        "taker":    round(float(tk[0]['buySellRatio']), 3) if tk else 1.0,
        "fr_pct":   round(float(fr.get('lastFundingRate', 0)) * 100, 5),
        "oi_1h_pct": oi1h,
        "liq_100x_dn": round(px * 0.9905, 1),
        "liq_100x_up": round(px * 1.0095, 1),
        "liq_50x_dn":  round(px * 0.981, 1),
        "timestamp": time.strftime('%H:%M UTC')
    }


@mcp.tool()
def brahma_health() -> dict:
    """
    梵天系统健康检查。
    返回：state新鲜度/关键文件状态/系统综合状态
    """
    import json
    from pathlib import Path
    base = Path(__file__).parent / 'data'
    checks = {}
    for nm in ['btc', 'eth']:
        sf = base / f'brahma_state_{nm}.json'
        if sf.exists():
            try:
                sd  = json.loads(sf.read_text())
                age = round((time.time() - sd.get('price_ts', 0)) / 60, 1)
                checks[f"state_{nm}"] = {"age_min": age, "ok": age < 60}
            except Exception as e:
                checks[f"state_{nm}"] = {"error": str(e), "ok": False}
        else:
            checks[f"state_{nm}"] = {"ok": False, "error": "文件不存在"}
    status = "OK" if all(v["ok"] for v in checks.values()) else "WARNING"
    return {"timestamp": time.strftime('%H:%M UTC'), "status": status, "checks": checks}


if __name__ == "__main__":
    # 启动MCP服务器（stdio模式）
    mcp.run(transport="stdio")
