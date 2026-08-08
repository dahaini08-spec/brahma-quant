#!/usr/bin/env python3
"""
HCME M1+M2 Builder
M1: Structure Annotation Library (structure_db.jsonl)
M2: Liquidity Map (liquidity_map.json)
"""

import json
import jsonlines
import math
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path("/root/.openclaw/workspace/trading-system/data")
BACKTEST_DIR = DATA_DIR / "backtest"

# ─── helpers ───────────────────────────────────────────────────────────────

def load_klines(sym: str) -> list[dict]:
    path = BACKTEST_DIR / f"{sym}_4h.json"
    with open(path) as f:
        raw = json.load(f)
    return [
        {
            "ts": r[0],
            "dt": datetime.fromtimestamp(r[0] / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M"),
            "o": float(r[1]),
            "h": float(r[2]),
            "l": float(r[3]),
            "c": float(r[4]),
            "v": float(r[5]),
        }
        for r in raw
    ]

def compute_ema(values: list[float], period: int) -> list[float]:
    """Classic EMA with multiplier = 2/(period+1)."""
    k = 2.0 / (period + 1)
    ema = [None] * len(values)
    # seed with first value
    ema[0] = values[0]
    for i in range(1, len(values)):
        ema[i] = values[i] * k + ema[i - 1] * (1 - k)
    return ema

def compute_atr(bars: list[dict], period: int = 14) -> list[float]:
    """True Range average (Wilder's smoothing)."""
    trs = [0.0]
    for i in range(1, len(bars)):
        h, l, pc = bars[i]["h"], bars[i]["l"], bars[i - 1]["c"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    # Wilder smoothing
    atrs = [0.0] * len(trs)
    atrs[period] = sum(trs[1 : period + 1]) / period
    for i in range(period + 1, len(trs)):
        atrs[i] = (atrs[i - 1] * (period - 1) + trs[i]) / period
    return atrs

def rolling_percentile(values: list[float], window: int = 500) -> list[float]:
    """For each index i, compute percentile of values[i] within the preceding `window` values."""
    result = [50.0] * len(values)
    for i in range(len(values)):
        start = max(0, i - window + 1)
        subset = [v for v in values[start : i + 1] if v > 0]
        if len(subset) < 2:
            result[i] = 50.0
            continue
        v = values[i]
        rank = sum(1 for x in subset if x <= v)
        result[i] = round(100.0 * rank / len(subset), 2)
    return result

KEY_LEVELS_BTC = list(range(10000, 1000001, 10000))  # $10k multiples up to $1M
KEY_LEVELS_ETH = list(range(100, 20001, 100))         # $100 multiples up to $20k

def is_above_key_level(price: float, sym: str) -> bool:
    levels = KEY_LEVELS_BTC if "BTC" in sym else KEY_LEVELS_ETH
    return any(price >= lv for lv in levels)

def detect_swing_and_structure(bars: list[dict], sym: str, window: int = 2) -> list[dict]:
    n = len(bars)
    closes = [b["c"] for b in bars]
    highs = [b["h"] for b in bars]
    lows = [b["l"] for b in bars]
    volumes = [b["v"] for b in bars]

    ema20 = compute_ema(closes, 20)
    atrs = compute_atr(bars, 14)

    vol_pctile = rolling_percentile(volumes, 500)
    atr_vals = [atrs[i] / closes[i] * 100 if closes[i] > 0 else 0.0 for i in range(n)]
    atr_pctile = rolling_percentile(atr_vals, 500)

    ath_running = [bars[0]["h"]] * n
    atl_running = [bars[0]["l"]] * n
    for i in range(1, n):
        ath_running[i] = max(ath_running[i - 1], bars[i]["h"])
        atl_running[i] = min(atl_running[i - 1], bars[i]["l"])

    # Detect raw swing highs/lows with window=2 (must be higher than prev 2 and next 2)
    swing_high = [False] * n
    swing_low = [False] * n
    for i in range(window, n - window):
        h = highs[i]
        l = lows[i]
        if all(h > highs[i - j] for j in range(1, window + 1)) and \
           all(h > highs[i + j] for j in range(1, window + 1)):
            swing_high[i] = True
        if all(l < lows[i - j] for j in range(1, window + 1)) and \
           all(l < lows[i + j] for j in range(1, window + 1)):
            swing_low[i] = True

    # Assign swing_type using EMA-trend
    swing_types = ["NONE"] * n
    # Track previous swing high/low values for BOS/CHoCH
    prev_swing_high_price = None
    prev_swing_low_price = None
    prev_trend = None  # "up" or "down"
    structure = ["NONE"] * n

    for i in range(n):
        trend = "up" if (ema20[i] is not None and closes[i] > ema20[i]) else "down"

        if swing_high[i]:
            if trend == "up":
                swing_types[i] = "HH"
            else:
                swing_types[i] = "LH"
            # BOS check: price breaks above prev swing high (uptrend)
            if prev_swing_high_price is not None and highs[i] > prev_swing_high_price and trend == "up":
                structure[i] = "BOS"
            # CHoCH: trend changed since last swing
            elif prev_trend is not None and prev_trend != trend:
                structure[i] = "CHoCH"
            prev_swing_high_price = highs[i]
            prev_trend = trend

        elif swing_low[i]:
            if trend == "up":
                swing_types[i] = "HL"
            else:
                swing_types[i] = "LL"
            # BOS check: price breaks below prev swing low (downtrend)
            if prev_swing_low_price is not None and lows[i] < prev_swing_low_price and trend == "down":
                structure[i] = "BOS"
            elif prev_trend is not None and prev_trend != trend:
                structure[i] = "CHoCH"
            prev_swing_low_price = lows[i]
            prev_trend = trend

    annotated = []
    for i in range(n):
        b = bars[i]
        ath = ath_running[i]
        atl = atl_running[i]
        dist_from_ath = round((ath - b["c"]) / ath * 100, 4) if ath > 0 else 0.0
        dist_from_atl = round((b["c"] - atl) / atl * 100, 4) if atl > 0 else 0.0
        annotated.append({
            "sym": sym,
            "ts": b["ts"],
            "dt": b["dt"],
            "o": b["o"],
            "h": b["h"],
            "l": b["l"],
            "c": b["c"],
            "swing_type": swing_types[i],
            "structure": structure[i],
            "dist_from_ath": dist_from_ath,
            "dist_from_atl": dist_from_atl,
            "vol_percentile": vol_pctile[i],
            "atr_percentile": atr_pctile[i],
            "above_key_level": is_above_key_level(b["c"], sym),
        })
    return annotated


# ─── M2: Liquidity Map ─────────────────────────────────────────────────────

def build_liquidity_map(annotated: list[dict], sym: str, bin_size: float) -> dict:
    resistance_buckets = defaultdict(int)  # swing highs → resistance
    support_buckets = defaultdict(int)     # swing lows  → support

    total_swings = 0
    for r in annotated:
        st = r["swing_type"]
        price = r["c"]
        bucket = math.floor(price / bin_size) * bin_size

        if st in ("HH", "LH"):
            resistance_buckets[bucket] += 1
            total_swings += 1
        elif st in ("HL", "LL"):
            support_buckets[bucket] += 1
            total_swings += 1

    key_levels = KEY_LEVELS_BTC if "BTC" in sym else KEY_LEVELS_ETH

    def to_zone_list(buckets: dict[float, int]) -> list[dict]:
        zones = []
        for price_lo, density in sorted(buckets.items()):
            price_hi = price_lo + bin_size
            # key level: any $10k (BTC) or $100 (ETH) multiple within range
            kl = any(price_lo <= lv < price_hi for lv in key_levels)
            zones.append({
                "price_lo": price_lo,
                "price_hi": price_hi,
                "density": density,
                "key_level": kl,
            })
        return zones

    return {
        "metadata": {
            "symbol": sym,
            "bin_size": bin_size,
            "total_swings": total_swings,
            "updated": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        "resistance_zones": to_zone_list(resistance_buckets),
        "support_zones": to_zone_list(support_buckets),
    }


# ─── Main ──────────────────────────────────────────────────────────────────

def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    results = {}

    for sym, bin_size in [("BTCUSDT", 500.0), ("ETHUSDT", 10.0)]:
        print(f"\n=== Processing {sym} ===")
        bars = load_klines(sym)
        print(f"  Loaded {len(bars)} bars")

        annotated = detect_swing_and_structure(bars, sym)

        # Write M1
        out_path = DATA_DIR / f"structure_db_{sym[:3].lower()}.jsonl"
        with jsonlines.open(str(out_path), mode="w") as writer:
            for row in annotated:
                writer.write(row)
        print(f"  Written M1 → {out_path}")

        # M2
        lmap = build_liquidity_map(annotated, sym, bin_size)

        # Stats
        counts = {"HH": 0, "HL": 0, "LH": 0, "LL": 0, "NONE": 0, "BOS": 0, "CHoCH": 0}
        for r in annotated:
            counts[r["swing_type"]] = counts.get(r["swing_type"], 0) + 1
            counts[r["structure"]] = counts.get(r["structure"], 0) + 1

        results[sym] = {
            "annotated": annotated,
            "lmap": lmap,
            "counts": counts,
        }

        print(f"  Swing counts: HH={counts['HH']} HL={counts['HL']} LH={counts['LH']} LL={counts['LL']}")
        print(f"  Structure: BOS={counts['BOS']} CHoCH={counts['CHoCH']}")

    # Write combined liquidity map
    lmap_out = {
        "btc": results["BTCUSDT"]["lmap"],
        "eth": results["ETHUSDT"]["lmap"],
    }
    lmap_path = DATA_DIR / "liquidity_map_btc.json"
    with open(lmap_path, "w") as f:
        json.dump({"btc": results["BTCUSDT"]["lmap"]}, f, indent=2)
    print(f"\n  Written BTC liquidity map → {lmap_path}")

    lmap_eth_path = DATA_DIR / "liquidity_map_eth.json"
    with open(lmap_eth_path, "w") as f:
        json.dump({"eth": results["ETHUSDT"]["lmap"]}, f, indent=2)
    print(f"  Written ETH liquidity map → {lmap_eth_path}")

    # ── Verification ─────────────────────────────────────────────────────

    print("\n\n===== VERIFICATION =====")

    for sym in ["BTCUSDT", "ETHUSDT"]:
        c = results[sym]["counts"]
        print(f"\n{sym} structure_db counts:")
        print(f"  HH={c['HH']}  HL={c['HL']}  LH={c['LH']}  LL={c['LL']}  NONE_swing={c['NONE']}")
        print(f"  BOS={c['BOS']}  CHoCH={c['CHoCH']}")

    # TOP10 BTC liquidity zones by density
    btc_all_zones = (
        results["BTCUSDT"]["lmap"]["resistance_zones"] +
        results["BTCUSDT"]["lmap"]["support_zones"]
    )
    top10 = sorted(btc_all_zones, key=lambda z: z["density"], reverse=True)[:10]
    print("\nTOP10 BTC liquidity zones (resistance + support combined):")
    for i, z in enumerate(top10, 1):
        kl_marker = " [KEY_LEVEL]" if z["key_level"] else ""
        print(f"  {i:2}. ${z['price_lo']:>8,.0f}–${z['price_hi']:>8,.0f}  density={z['density']}{kl_marker}")

    # BTC current price neighbourhood (~$64,300)
    target_price = 64300.0
    print(f"\nBTC structure near ${target_price:,.0f} (last 20 annotated bars in window):")
    btc_annotated = results["BTCUSDT"]["annotated"]
    nearby = [r for r in btc_annotated if abs(r["c"] - target_price) / target_price < 0.05]
    nearby = nearby[-20:]  # last 20 in time
    for r in nearby:
        marker = f"[{r['swing_type']}]" if r["swing_type"] != "NONE" else "     "
        struct_marker = f" ← {r['structure']}" if r["structure"] != "NONE" else ""
        print(f"  {r['dt']}  c={r['c']:>9,.2f}  {marker}{struct_marker}")

    print("\n===== DONE =====\n")
    return results


if __name__ == "__main__":
    main()
