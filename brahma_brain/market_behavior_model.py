"""
Market Behavior Model  (M4)
============================
Statistically derives institutional / "smart money" behavioral patterns
from 6.8 years of BTC 4H OHLCV data.

Produces data/market_behavior_model.json with three statistical dimensions:
  1. Time-series bias   (hour / weekday / monthly / intra-month)
  2. Integer-level bias ($10k–$100k magnetic zones, ±2%)
  3. Liquidity-hunt / fakeout stats (false breakouts of prior swing highs)

Author: brahma-subagent / 2026-08-08
"""

from __future__ import annotations
import json
import os
import math
from datetime import datetime, timezone
from collections import defaultdict

# ── paths ────────────────────────────────────────────────────────────────────
_DIR  = os.path.dirname(os.path.abspath(__file__))
_DATA = os.path.join(_DIR, "..", "data")
BTCUSDT_4H_PATH = os.path.join(_DATA, "backtest", "BTCUSDT_4h.json")
OUTPUT_PATH     = os.path.join(_DATA, "market_behavior_model.json")

# ── OHLCV column indices ─────────────────────────────────────────────────────
# [open_ts, open, high, low, close, volume, close_ts, qv, n_trades, tb_vol, tb_qv, ignore]
I_TS_OPEN  = 0
I_OPEN     = 1
I_HIGH     = 2
I_LOW      = 3
I_CLOSE    = 4
I_VOLUME   = 5
I_TS_CLOSE = 6

# Integer price levels to analyze
INTEGER_LEVELS = [10_000, 20_000, 30_000, 40_000, 50_000, 60_000, 70_000, 80_000, 90_000, 100_000]
TOUCH_WINDOW_BARS_24H = 6    # 4H bars in 24H
TOUCH_WINDOW_BARS_48H = 12
TOUCH_WINDOW_BARS_72H = 18
INTEGER_ZONE_PCT = 0.02      # ±2%

# Fakeout parameters
FAKEOUT_LOOKBACK   = 48    # bars to look back for prior swing high  (~8 days 4H)
FAKEOUT_REVERT_PCT = 0.05  # must fall 5% below the breakout level within 24H


def _safe_float(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _load_4h_data() -> list:
    """Load BTCUSDT 4H data, return list of bar dicts."""
    with open(BTCUSDT_4H_PATH) as f:
        raw = json.load(f)
    bars = []
    for r in raw:
        ts_ms   = int(r[I_TS_OPEN])
        dt      = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
        bars.append({
            "ts_ms":  ts_ms,
            "dt":     dt,
            "open":   _safe_float(r[I_OPEN]),
            "high":   _safe_float(r[I_HIGH]),
            "low":    _safe_float(r[I_LOW]),
            "close":  _safe_float(r[I_CLOSE]),
            "volume": _safe_float(r[I_VOLUME]),
        })
    return bars


# ── Dimension 1: Time-series bias ────────────────────────────────────────────

def compute_time_bias(bars: list) -> dict:
    """
    Compute directional bias by:
      - hour_of_day  (0-23 UTC)
      - day_of_week  (0=Mon … 6=Sun)
      - month        (1-12)
      - intra_month  (early=1-5, mid=10-20, late=25-31)
    """

    def agg_init():
        return {"sum_chg": 0.0, "ups": 0, "total": 0}

    hourly  = defaultdict(agg_init)
    weekly  = defaultdict(agg_init)
    monthly = defaultdict(agg_init)
    intra   = defaultdict(agg_init)

    for bar in bars:
        dt  = bar["dt"]
        chg = (bar["close"] - bar["open"]) / bar["open"] * 100

        # hour (keyed by hour of bar open)
        h_key = str(dt.hour)
        hourly[h_key]["sum_chg"] += chg
        hourly[h_key]["ups"]     += 1 if chg > 0 else 0
        hourly[h_key]["total"]   += 1

        # weekday
        d_key = str(dt.weekday())
        weekly[d_key]["sum_chg"] += chg
        weekly[d_key]["ups"]     += 1 if chg > 0 else 0
        weekly[d_key]["total"]   += 1

        # month
        m_key = str(dt.month)
        monthly[m_key]["sum_chg"] += chg
        monthly[m_key]["ups"]     += 1 if chg > 0 else 0
        monthly[m_key]["total"]   += 1

        # intra-month
        day = dt.day
        if 1 <= day <= 5:
            im_key = "early"
        elif 10 <= day <= 20:
            im_key = "mid"
        elif 25 <= day <= 31:
            im_key = "late"
        else:
            im_key = "other"
        intra[im_key]["sum_chg"] += chg
        intra[im_key]["ups"]     += 1 if chg > 0 else 0
        intra[im_key]["total"]   += 1

    def finalize(d):
        return {
            k: {
                "avg_chg": round(v["sum_chg"] / v["total"], 4) if v["total"] else 0.0,
                "up_prob": round(v["ups"] / v["total"], 4)      if v["total"] else 0.5,
                "n":       v["total"],
            }
            for k, v in d.items()
        }

    return {
        "hourly_bias":      finalize(hourly),
        "weekly_bias":      finalize(weekly),
        "monthly_bias":     finalize(monthly),
        "intra_month_bias": finalize(intra),
    }


# ── Dimension 2: Integer-level behavior ──────────────────────────────────────

def compute_integer_levels(bars: list) -> dict:
    """
    For each $10k–$100k integer level, detect the first touch within ±2%
    and record price direction over the following 24H/48H/72H.
    """
    n  = len(bars)
    result = {}

    for level in INTEGER_LEVELS:
        lo = level * (1 - INTEGER_ZONE_PCT)
        hi = level * (1 + INTEGER_ZONE_PCT)

        touches_24h_up, touches_24h_dn = 0, 0
        touches_48h_up, touches_48h_dn = 0, 0
        touches_72h_up, touches_72h_dn = 0, 0

        last_touch_idx = -999  # avoid double-counting within 12 bars

        for i, bar in enumerate(bars):
            if i - last_touch_idx < TOUCH_WINDOW_BARS_24H:
                continue  # cooldown

            touched = (lo <= bar["low"] <= hi) or (lo <= bar["high"] <= hi) or \
                      (bar["low"] <= lo and bar["high"] >= hi)  # candle spans zone
            if not touched:
                continue

            last_touch_idx = i
            entry_close = bar["close"]

            # 24H
            if i + TOUCH_WINDOW_BARS_24H < n:
                future_close_24h = bars[i + TOUCH_WINDOW_BARS_24H]["close"]
                if future_close_24h >= entry_close:
                    touches_24h_up += 1
                else:
                    touches_24h_dn += 1

            # 48H
            if i + TOUCH_WINDOW_BARS_48H < n:
                future_close_48h = bars[i + TOUCH_WINDOW_BARS_48H]["close"]
                if future_close_48h >= entry_close:
                    touches_48h_up += 1
                else:
                    touches_48h_dn += 1

            # 72H
            if i + TOUCH_WINDOW_BARS_72H < n:
                future_close_72h = bars[i + TOUCH_WINDOW_BARS_72H]["close"]
                if future_close_72h >= entry_close:
                    touches_72h_up += 1
                else:
                    touches_72h_dn += 1

        total = touches_24h_up + touches_24h_dn
        result[str(level)] = {
            "touch_up_24h":   round(touches_24h_up / total, 4) if total > 0 else None,
            "touch_down_24h": round(touches_24h_dn / total, 4) if total > 0 else None,
            "touch_up_48h":   round(touches_48h_up / (touches_48h_up + touches_48h_dn), 4) if (touches_48h_up + touches_48h_dn) > 0 else None,
            "touch_down_48h": round(touches_48h_dn / (touches_48h_up + touches_48h_dn), 4) if (touches_48h_up + touches_48h_dn) > 0 else None,
            "touch_up_72h":   round(touches_72h_up / (touches_72h_up + touches_72h_dn), 4) if (touches_72h_up + touches_72h_dn) > 0 else None,
            "touch_down_72h": round(touches_72h_dn / (touches_72h_up + touches_72h_dn), 4) if (touches_72h_up + touches_72h_dn) > 0 else None,
            "n": total,
        }

    return {"integer_levels": result}


# ── Dimension 3: Fakeout / liquidity hunt ────────────────────────────────────

def compute_fakeout_stats(bars: list) -> dict:
    """
    Identify all "false breakouts":
      - Bar breaks above the highest prior swing-high within last FAKEOUT_LOOKBACK bars
      - Within 6 bars (24H), price falls back ≥ FAKEOUT_REVERT_PCT below the breakout level

    Collect:
      - avg_retracement_pct  (how far did price fall after fakeout?)
      - avg_recovery_bars    (how many bars to recover?)
      - n                    (number of fakeouts found)
    """
    n = len(bars)
    fakeout_events = []

    for i in range(FAKEOUT_LOOKBACK, n - TOUCH_WINDOW_BARS_24H - 1):
        # find prior swing high in lookback window
        prior_highs = [bars[j]["high"] for j in range(i - FAKEOUT_LOOKBACK, i)]
        swing_high = max(prior_highs)

        bar = bars[i]
        # breakout condition: this bar's high exceeds the swing high
        if bar["high"] <= swing_high:
            continue

        breakout_level = swing_high
        revert_threshold = breakout_level * (1 - FAKEOUT_REVERT_PCT)

        # check if price closes back below threshold within 24H
        reverted = False
        revert_bar = -1
        min_close_after = bar["close"]
        for j in range(i + 1, min(i + TOUCH_WINDOW_BARS_24H + 1, n)):
            min_close_after = min(min_close_after, bars[j]["close"])
            if bars[j]["close"] < revert_threshold:
                reverted = True
                revert_bar = j
                break

        if not reverted:
            continue

        # measure retracement from breakout high to lowest close post-fakeout
        lo_close = min(bars[j]["close"] for j in range(i, min(i + TOUCH_WINDOW_BARS_72H, n)))
        retrace_pct = (bar["high"] - lo_close) / bar["high"] * 100  # positive = drop

        # recovery: next bar that closes above swing_high again
        recovery_bars = None
        for j in range(i + 1, min(i + TOUCH_WINDOW_BARS_72H * 2, n)):
            if bars[j]["close"] > breakout_level:
                recovery_bars = j - i
                break

        fakeout_events.append({
            "i":              i,
            "retrace_pct":    retrace_pct,
            "recovery_bars":  recovery_bars,
            "breakout_price": round(bar["high"], 2),
            "swing_high":     round(swing_high, 2),
        })

    if not fakeout_events:
        return {
            "fakeout_stats": {
                "avg_retracement": None,
                "n":               0,
                "recovery_h":      None,
            }
        }

    avg_retrace  = sum(e["retrace_pct"] for e in fakeout_events) / len(fakeout_events)
    rec_events   = [e["recovery_bars"] for e in fakeout_events if e["recovery_bars"] is not None]
    avg_recovery = (sum(rec_events) / len(rec_events) * 4) if rec_events else None  # bars→hours

    return {
        "fakeout_stats": {
            "avg_retracement": round(-avg_retrace, 2),   # negative = price fell
            "n":               len(fakeout_events),
            "recovery_h":      round(avg_recovery, 1) if avg_recovery is not None else None,
            "recovery_rate":   round(len(rec_events) / len(fakeout_events), 4),
        }
    }


# ── Main builder ──────────────────────────────────────────────────────────────

def build_market_behavior_model() -> dict:
    print("[M4] Loading 4H BTCUSDT data …")
    bars = _load_4h_data()
    print(f"[M4] Loaded {len(bars)} bars (from {bars[0]['dt']} to {bars[-1]['dt']})")

    print("[M4] Computing time-series biases …")
    time_biases = compute_time_bias(bars)

    print("[M4] Computing integer-level statistics …")
    int_levels = compute_integer_levels(bars)

    print("[M4] Computing fakeout / liquidity-hunt statistics …")
    fakeout = compute_fakeout_stats(bars)

    model = {
        "meta": {
            "generated_at":  datetime.now(tz=timezone.utc).isoformat(),
            "bars_analyzed": len(bars),
            "first_bar":     bars[0]["dt"].isoformat(),
            "last_bar":      bars[-1]["dt"].isoformat(),
            "source":        "BTCUSDT_4h.json",
        },
        **time_biases,
        **int_levels,
        **fakeout,
    }

    os.makedirs(_DATA, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(model, f, indent=2, default=str)
    print(f"[M4] Saved → {OUTPUT_PATH}")
    return model


# ── Query helpers ─────────────────────────────────────────────────────────────

def print_current_bias(model: dict | None = None):
    """Print M4 bias for current UTC time."""
    if model is None:
        with open(OUTPUT_PATH) as f:
            model = json.load(f)

    now = datetime.now(tz=timezone.utc)
    print(f"\n{'='*55}")
    print(f"  MARKET BEHAVIOR MODEL — Current UTC: {now.strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*55}")

    # Hourly
    hb = model.get("hourly_bias", {}).get(str(now.hour))
    if hb:
        print(f"  Hour {now.hour:02d}:00 UTC → avg_chg={hb['avg_chg']:+.3f}%  up_prob={hb['up_prob']:.0%}  (n={hb['n']})")

    # Weekly
    dow_names = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]
    wb = model.get("weekly_bias", {}).get(str(now.weekday()))
    if wb:
        print(f"  {dow_names[now.weekday()]} → avg_chg={wb['avg_chg']:+.3f}%  up_prob={wb['up_prob']:.0%}  (n={wb['n']})")

    # Monthly
    month_names = ["","Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
    mb = model.get("monthly_bias", {}).get(str(now.month))
    if mb:
        print(f"  {month_names[now.month]} → avg_chg={mb['avg_chg']:+.3f}%  up_prob={mb['up_prob']:.0%}  (n={mb['n']})")

    # Intra-month
    day = now.day
    if 1 <= day <= 5:
        im_key = "early"
    elif 10 <= day <= 20:
        im_key = "mid"
    elif 25 <= day <= 31:
        im_key = "late"
    else:
        im_key = "other"
    imb = model.get("intra_month_bias", {}).get(im_key)
    if imb:
        print(f"  Intra-month ({im_key}, day {day}) → avg_chg={imb['avg_chg']:+.3f}%  up_prob={imb['up_prob']:.0%}  (n={imb['n']})")


def print_integer_level_analysis(price: float, model: dict | None = None):
    """Print integer-level analysis near current price."""
    if model is None:
        with open(OUTPUT_PATH) as f:
            model = json.load(f)

    print(f"\n{'='*55}")
    print(f"  INTEGER-LEVEL ANALYSIS — Current price: ${price:,.0f}")
    print(f"{'='*55}")

    int_levels = model.get("integer_levels", {})
    for level_str, stats in sorted(int_levels.items(), key=lambda x: int(x[0])):
        level = int(level_str)
        dist_pct = abs(price - level) / level * 100
        if dist_pct <= 15.0:  # show ±15% range
            direction = "above" if price > level else "below"
            marker = " ◄ CURRENT ZONE" if dist_pct <= 2.0 else ""
            n = stats.get("n", 0)
            if n == 0:
                print(f"  ${level:,} ({dist_pct:.1f}% {direction}): no data{marker}")
            else:
                u24 = stats.get("touch_up_24h")
                d24 = stats.get("touch_down_24h")
                u_str = f"{u24:.0%}" if u24 is not None else "n/a"
                d_str = f"{d24:.0%}" if d24 is not None else "n/a"
                print(f"  ${level:,} ({dist_pct:.1f}% {direction}): "
                      f"24H up={u_str} down={d_str}  n={n}{marker}")

    fs = model.get("fakeout_stats", {})
    print(f"\n  Fakeout stats: {fs.get('n',0)} events, "
          f"avg_retrace={fs.get('avg_retracement','?')}%, "
          f"recovery_h={fs.get('recovery_h','?')}h")


# ── CLI entry ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    rebuild = "--rebuild" in sys.argv or not os.path.exists(OUTPUT_PATH)

    if rebuild:
        model = build_market_behavior_model()
    else:
        print(f"[M4] Loading existing model from {OUTPUT_PATH}")
        with open(OUTPUT_PATH) as f:
            model = json.load(f)

    # Print current time bias
    print_current_bias(model)

    # Print BTC $64,300 analysis
    print_integer_level_analysis(64_300.0, model)
