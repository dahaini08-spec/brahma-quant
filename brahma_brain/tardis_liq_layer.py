#!/usr/bin/env python3
"""
tardis_liq_layer.py — Tardis 清算墙真实数据层 v1.0
星枢引擎 Layer 0 · 设计院 2026-06-09

数据源：datasets.tardis.dev CSV（每月1日免费，无需API Key）
功能：
  - 下载并缓存最近可用月份的清算CSV
  - 计算真实清算密集区（价格桶聚合）
  - 输出结构化清算快照供 liq_scanner 调用
  - 替代 Coinglass 估算清算位

免费层限制：只有每月1日的数据（无Key）
升级路径：传入 api_key 后解锁全量历史

接口：
  get_tardis_liq_walls(symbol) -> dict
    {
      "available": bool,
      "source": "tardis_csv" | "fallback_estimate",
      "date": "YYYY-MM-DD",
      "long_walls": [(price, usd_volume), ...],  # 多头密集清算位（价格下跌触发）
      "short_walls": [(price, usd_volume), ...], # 空头密集清算位（价格上涨触发）
      "long_total_usd": float,
      "short_total_usd": float,
      "long_dominant_price": float,  # 最大多头清算价位
      "short_dominant_price": float, # 最大空头清算价位
      "bias": "BEARISH"|"BULLISH"|"NEUTRAL",
    }
"""

# ── STATUS: STANDBY ──────────────────────────────────────────────
# 清算数据层，被tardis_engine代理，直接引用少
# LAST_REVIEW: 2026-07-01 | 调用前运行 auto_review.py 确认引用关系
# ─────────────────────────────────────────────────────────────────

import gzip
import json
import os
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    import requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

# ─── 配置 ──────────────────────────────────────────────────────
DATASETS_BASE = "https://datasets.tardis.dev/v1"
EXCHANGE      = "binance-futures"
CACHE_DIR     = Path(__file__).parent.parent / "data" / "tardis" / "liq_csv"
CACHE_TTL_SEC = 6 * 3600   # CSV缓存6小时（每月1日数据不变，但避免重复下载）

# 价格桶大小（聚合清算密度）
BUCKET_CONFIG = {
    "BTC": 500,
    "ETH": 20,
    "SOL": 1,
    "BNB": 5,
    "DOGE": 0.001,
    "DEFAULT": 10,
}

_mem_cache = {}   # {symbol_date: (ts, result_dict)}


def _bucket_size(symbol: str) -> float:
    base = symbol.upper().replace("USDT", "")
    return BUCKET_CONFIG.get(base, BUCKET_CONFIG["DEFAULT"])


def _get_free_date() -> tuple[str, str, str]:
    """返回最近可用的免费日期（每月1日）: (year, month, day)"""
    now = datetime.now(timezone.utc)
    # 当月1日
    first_this = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    # 数据导出通常延迟1-2天，当月1日若<2天前则用上月1日
    if (now - first_this).total_seconds() < 2 * 86400:
        # 用上月1日
        if now.month == 1:
            y, m = now.year - 1, 12
        else:
            y, m = now.year, now.month - 1
        return str(y), f"{m:02d}", "01"
    return str(now.year), f"{now.month:02d}", "01"


def _cache_path(symbol: str, year: str, month: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{EXCHANGE}_{symbol}_{year}{month}01.csv.gz"


def _download_csv(symbol: str, year: str, month: str, api_key: str = None) -> bytes | None:
    """下载 Tardis CSV，返回 gzip bytes 或 None"""
    if not _HAS_REQUESTS:
        return None

    url = f"{DATASETS_BASE}/{EXCHANGE}/liquidations/{year}/{month}/01/{symbol}.csv.gz"
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    cache_file = _cache_path(symbol, year, month)
    # 检查本地缓存
    if cache_file.exists():
        age = time.time() - cache_file.stat().st_mtime
        if age < CACHE_TTL_SEC:
            return cache_file.read_bytes()

    try:
        r = requests.get(url, headers=headers, timeout=20)
        if r.status_code == 200:
            cache_file.write_bytes(r.content)
            return r.content
        return None
    except Exception:
        return None


def _parse_csv(gz_bytes: bytes, symbol: str) -> dict:
    """解析清算CSV，返回聚合结果"""
    try:
        data = gzip.decompress(gz_bytes).decode("utf-8")
    except Exception:
        return {}

    lines = data.strip().split("\n")
    if len(lines) < 2:
        return {}

    rows = [l.split(",") for l in lines[1:] if l.strip()]
    # CSV格式: exchange,symbol,timestamp,local_timestamp,id,side,price,amount
    long_liq  = []  # sell = 多单被清算（价格下跌触发）
    short_liq = []  # buy  = 空单被清算（价格上涨触发）

    for r in rows:
        if len(r) < 8:
            continue
        try:
            side  = r[5].strip()
            price = float(r[6])
            amt   = float(r[7])
            if side == "sell":
                long_liq.append((price, amt))
            elif side == "buy":
                short_liq.append((price, amt))
        except (ValueError, IndexError):
            continue

    bsize = _bucket_size(symbol)
    long_b  = defaultdict(float)
    short_b = defaultdict(float)

    for p, q in long_liq:
        long_b[round(p / bsize) * bsize] += p * q
    for p, q in short_liq:
        short_b[round(p / bsize) * bsize] += p * q

    long_total  = sum(p * q for p, q in long_liq)
    short_total = sum(p * q for p, q in short_liq)

    top_long  = sorted(long_b.items(),  key=lambda x: -x[1])[:8]
    top_short = sorted(short_b.items(), key=lambda x: -x[1])[:8]

    return {
        "long_walls":           top_long,
        "short_walls":          top_short,
        "long_total_usd":       long_total,
        "short_total_usd":      short_total,
        "long_dominant_price":  top_long[0][0]  if top_long  else 0.0,
        "short_dominant_price": top_short[0][0] if top_short else 0.0,
        "long_count":           len(long_liq),
        "short_count":          len(short_liq),
    }


def get_tardis_liq_walls(symbol: str, api_key: str = None) -> dict:
    """
    主接口：获取 Tardis 真实清算墙数据
    symbol: 如 "BTCUSDT" 或 "BTC"
    """
    sym = symbol.upper()
    if not sym.endswith("USDT"):
        sym += "USDT"

    year, month, day = _get_free_date()
    cache_key = f"{sym}_{year}{month}"

    # 内存缓存
    if cache_key in _mem_cache:
        ts, cached = _mem_cache[cache_key]
        if time.time() - ts < CACHE_TTL_SEC:
            return cached

    empty = {
        "available": False,
        "source": "fallback_estimate",
        "date": f"{year}-{month}-01",
        "long_walls": [],
        "short_walls": [],
        "long_total_usd": 0.0,
        "short_total_usd": 0.0,
        "long_dominant_price": 0.0,
        "short_dominant_price": 0.0,
        "bias": "NEUTRAL",
    }

    gz = _download_csv(sym, year, month, api_key)
    if not gz:
        _mem_cache[cache_key] = (time.time(), empty)
        return empty

    parsed = _parse_csv(gz, sym)
    if not parsed:
        _mem_cache[cache_key] = (time.time(), empty)
        return empty

    # 偏向判断：多头清算远大于空头 → 说明下跌压力更强
    long_t  = parsed["long_total_usd"]
    short_t = parsed["short_total_usd"]
    if long_t > short_t * 3:
        bias = "BEARISH"   # 下方多头密集，下跌爆仓压力大
    elif short_t > long_t * 3:
        bias = "BULLISH"   # 上方空头密集，上涨逼空压力大
    else:
        bias = "NEUTRAL"

    result = {
        "available": True,
        "source": "tardis_csv",
        "date": f"{year}-{month}-01",
        "bias": bias,
        **parsed,
    }

    _mem_cache[cache_key] = (time.time(), result)
    return result


def format_liq_walls(snap: dict, symbol: str = "") -> str:
    """格式化输出清算墙（供 liq_scanner.format_report 调用）"""
    if not snap.get("available"):
        return "  ⚠️ Tardis清算数据不可用（使用估算值）"

    lines = [f"  📅 Tardis真实数据（{snap['date']}）"]
    lines.append(f"  多头爆仓总额: ${snap['long_total_usd']:,.0f}  空头爆仓总额: ${snap['short_total_usd']:,.0f}")

    if snap["long_walls"]:
        lines.append("  ▼ 多头密集清算位（下跌触发）:")
        max_v = max(v for _, v in snap["long_walls"])
        for price, usd in sorted(snap["long_walls"], key=lambda x: -x[1])[:5]:
            bar = "█" * max(1, int(usd / max_v * 15))
            lines.append(f"    ${price:>10,.1f}  ${usd:>10,.0f}  {bar}")

    if snap["short_walls"]:
        lines.append("  ▲ 空头密集清算位（上涨触发）:")
        max_v = max(v for _, v in snap["short_walls"])
        for price, usd in sorted(snap["short_walls"], key=lambda x: x[0])[:5]:
            bar = "█" * max(1, int(usd / max_v * 15))
            lines.append(f"    ${price:>10,.1f}  ${usd:>10,.0f}  {bar}")

    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    symbols = sys.argv[1:] if len(sys.argv) > 1 else ["BTCUSDT", "ETHUSDT"]
    for sym in symbols:
        print(f"\n{'='*60}")
        print(f"  {sym} Tardis清算墙")
        print('='*60)
        snap = get_tardis_liq_walls(sym)
        print(f"数据源: {snap['source']}  日期: {snap['date']}  偏向: {snap['bias']}")
        print(format_liq_walls(snap, sym))
