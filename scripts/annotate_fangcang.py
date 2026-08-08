#!/usr/bin/env python3
"""
梵天设计院 - 方仓案例标注脚本（Step 2）
从6.5年4H数据挖掘「压缩→爆发」案例库
"""
import json
import math
import os

DATA_DIR = "/root/.openclaw/workspace/trading-system/data/backtest"
OUT_DIR = "/root/.openclaw/workspace/trading-system/data"


def load_4h(symbol):
    path = os.path.join(DATA_DIR, f"{symbol}_4h.json")
    with open(path) as f:
        raw = json.load(f)
    bars = []
    for r in raw:
        bars.append({
            "ts": r[0],
            "open": float(r[1]),
            "high": float(r[2]),
            "low": float(r[3]),
            "close": float(r[4]),
            "volume": float(r[5]),
        })
    return bars


def calc_bbw(closes, period=20):
    if len(closes) < period:
        return None
    window = closes[-period:]
    mean = sum(window) / period
    variance = sum((c - mean) ** 2 for c in window) / period
    std = math.sqrt(variance)
    if mean == 0:
        return None
    return (2 * 2 * std) / mean  # (upper - lower) / middle


def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    gains = []
    losses = []
    for i in range(-period, 0):
        diff = closes[i] - closes[i - 1]
        if diff > 0:
            gains.append(diff)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(-diff)
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def calc_ema(closes, period=20):
    if len(closes) < period:
        return closes[-1] if closes else 0
    k = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    for c in closes[period:]:
        ema = c * k + ema * (1 - k)
    return ema


def volume_trend(volumes):
    """判断量能趋势：最后半段 vs 前半段"""
    n = len(volumes)
    if n < 2:
        return "flat"
    mid = n // 2
    first_half = sum(volumes[:mid]) / max(mid, 1)
    second_half = sum(volumes[mid:]) / max(n - mid, 1)
    if first_half == 0:
        return "flat"
    ratio = second_half / first_half
    if ratio < 0.8:
        return "shrink"
    elif ratio > 1.2:
        return "expand"
    else:
        return "flat"


def find_all_time_high(bars, idx):
    """找到idx之前所有K线的最高价"""
    return max(b["high"] for b in bars[:idx + 1])


def annotate_fangcang(symbol):
    bars = load_4h(symbol)
    n = len(bars)
    closes = [b["close"] for b in bars]

    # Step A: 计算每根4H的BBW
    bbw_list = []
    for i in range(n):
        if i < 19:
            bbw_list.append(None)
        else:
            bbw_list.append(calc_bbw(closes[:i + 1]))

    # Step B: 识别压缩区间（连续N>=3根 BBW < 0.08）
    compress_zones = []
    i = 20  # 至少需要20根计算BBW
    while i < n:
        if bbw_list[i] is not None and bbw_list[i] < 0.08:
            start = i
            while i < n and bbw_list[i] is not None and bbw_list[i] < 0.08:
                i += 1
            end = i - 1
            length = end - start + 1
            if length >= 3:
                compress_zones.append((start, end))
        else:
            i += 1

    # Step C & D: 标注每个压缩区间
    cases = []
    for (start, end) in compress_zones:
        compress_closes = closes[start:end + 1]
        compress_highs = [bars[j]["high"] for j in range(start, end + 1)]
        compress_lows = [bars[j]["low"] for j in range(start, end + 1)]
        compress_vols = [bars[j]["volume"] for j in range(start, end + 1)]

        compress_high = max(compress_highs)
        compress_low = min(compress_lows)
        bbw_vals = [bbw_list[j] for j in range(start, end + 1) if bbw_list[j] is not None]
        compress_bbw_min = min(bbw_vals) if bbw_vals else 0
        compress_range_pct = (compress_high - compress_low) / compress_low * 100 if compress_low > 0 else 0
        vol_trend = volume_trend(compress_vols)

        # RSI at end
        rsi = calc_rsi(closes[:end + 1])

        # EMA20 判断大趋势
        ema20 = calc_ema(closes[:end + 1], 20)
        last_close = closes[end]
        if last_close > ema20 * 1.02:
            regime_guess = "uptrend"
        elif last_close < ema20 * 0.98:
            regime_guess = "downtrend"
        else:
            regime_guess = "ranging"

        # 距历史高点%
        ath = find_all_time_high(bars, end)
        price_vs_ath_pct = (last_close - ath) / ath * 100 if ath > 0 else 0

        # Step C: 判断爆发方向（后续8根4H）
        look_ahead = 8
        post_start = end + 1
        post_end = min(post_start + look_ahead, n)

        if post_start >= n:
            breakout_direction = "CHOP"
            breakout_pct = 0.0
            breakout_bars = 0
        else:
            post_highs = [bars[j]["high"] for j in range(post_start, post_end)]
            post_lows = [bars[j]["low"] for j in range(post_start, post_end)]

            max_high = max(post_highs) if post_highs else last_close
            min_low = min(post_lows) if post_lows else last_close

            long_break_pct = (max_high - compress_high) / compress_high * 100 if compress_high > 0 else 0
            short_break_pct = (compress_low - min_low) / compress_low * 100 if compress_low > 0 else 0

            up_threshold = 5.0  # +5%
            down_threshold = 5.0  # -5%

            if long_break_pct >= up_threshold and long_break_pct > short_break_pct:
                breakout_direction = "LONG"
                breakout_pct = long_break_pct
                # 找到第几根K线突破
                breakout_bars = 1
                for k, h in enumerate(post_highs):
                    if h >= compress_high * 1.05:
                        breakout_bars = k + 1
                        break
            elif short_break_pct >= down_threshold and short_break_pct > long_break_pct:
                breakout_direction = "SHORT"
                breakout_pct = short_break_pct
                breakout_bars = 1
                for k, l in enumerate(post_lows):
                    if l <= compress_low * 0.95:
                        breakout_bars = k + 1
                        break
            else:
                breakout_direction = "CHOP"
                breakout_pct = max(long_break_pct, short_break_pct)
                breakout_bars = 0

        case = {
            "symbol": symbol,
            "compress_start_ts": bars[start]["ts"],
            "compress_end_ts": bars[end]["ts"],
            "compress_bars": end - start + 1,
            "compress_bbw_min": round(compress_bbw_min, 6),
            "compress_range_pct": round(compress_range_pct, 4),
            "volume_trend": vol_trend,
            "rsi_at_end": round(rsi, 2),
            "oi_change": 0.0,
            "breakout_direction": breakout_direction,
            "breakout_pct": round(breakout_pct, 4),
            "breakout_bars": breakout_bars,
            "regime_guess": regime_guess,
            "price_vs_ath_pct": round(price_vs_ath_pct, 4),
        }
        cases.append(case)

    return cases


def write_jsonl(path, cases):
    with open(path, "w") as f:
        for case in cases:
            f.write(json.dumps(case) + "\n")
    print(f"Written {len(cases)} cases to {path}")


def summarize(btc_cases, eth_cases):
    def stats(cases, symbol):
        total = len(cases)
        longs = [c for c in cases if c["breakout_direction"] == "LONG"]
        shorts = [c for c in cases if c["breakout_direction"] == "SHORT"]
        chops = [c for c in cases if c["breakout_direction"] == "CHOP"]
        wr_long = len(longs) / total * 100 if total > 0 else 0
        wr_short = len(shorts) / total * 100 if total > 0 else 0
        return {
            "symbol": symbol,
            "total_compress": total,
            "long_count": len(longs),
            "long_wr_pct": round(wr_long, 1),
            "short_count": len(shorts),
            "short_wr_pct": round(wr_short, 1),
            "chop_count": len(chops),
        }

    all_cases = btc_cases + eth_cases
    if not all_cases:
        return {}

    avg_compress_h = sum(c["compress_bars"] * 4 for c in all_cases) / len(all_cases)

    breakout_cases = [c for c in all_cases if c["breakout_direction"] != "CHOP"]
    avg_breakout_pct = sum(c["breakout_pct"] for c in breakout_cases) / len(breakout_cases) if breakout_cases else 0

    # 最强案例
    if breakout_cases:
        best = max(breakout_cases, key=lambda c: c["breakout_pct"])
    else:
        best = None

    summary = {
        "btc": stats(btc_cases, "BTCUSDT"),
        "eth": stats(eth_cases, "ETHUSDT"),
        "avg_compress_hours": round(avg_compress_h, 1),
        "avg_breakout_pct": round(avg_breakout_pct, 4),
        "best_case": best,
        "total_cases": len(all_cases),
    }
    return summary


def print_summary(summary):
    b = summary["btc"]
    e = summary["eth"]
    best = summary.get("best_case")
    print("=== 方仓案例库统计 ===")
    print(f"BTC: 总压缩区间{b['total_compress']}个 → "
          f"LONG爆发{b['long_count']}个({b['long_wr_pct']}%) "
          f"SHORT爆发{b['short_count']}个({b['short_wr_pct']}%) "
          f"CHOP {b['chop_count']}个")
    print(f"ETH: 总压缩区间{e['total_compress']}个 → "
          f"LONG爆发{e['long_count']}个({e['long_wr_pct']}%) "
          f"SHORT爆发{e['short_count']}个({e['short_wr_pct']}%) "
          f"CHOP {e['chop_count']}个")
    print(f"平均压缩时长: {summary['avg_compress_hours']}H")
    print(f"平均爆发幅度: {summary['avg_breakout_pct']}%")
    if best:
        import datetime
        ts_sec = best['compress_end_ts'] / 1000
        date_str = datetime.datetime.utcfromtimestamp(ts_sec).strftime('%Y-%m-%d')
        print(f"最强案例: {best['symbol']} {date_str} BBW={best['compress_bbw_min']} → +{best['breakout_pct']}%")


if __name__ == "__main__":
    print("正在标注 BTC 方仓案例...")
    btc_cases = annotate_fangcang("BTCUSDT")
    write_jsonl(os.path.join(OUT_DIR, "fangcang_cases_btc.jsonl"), btc_cases)

    print("正在标注 ETH 方仓案例...")
    eth_cases = annotate_fangcang("ETHUSDT")
    write_jsonl(os.path.join(OUT_DIR, "fangcang_cases_eth.jsonl"), eth_cases)

    summary = summarize(btc_cases, eth_cases)
    summary_path = os.path.join(OUT_DIR, "fangcang_summary.json")
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"Summary written to {summary_path}")

    print()
    print_summary(summary)
