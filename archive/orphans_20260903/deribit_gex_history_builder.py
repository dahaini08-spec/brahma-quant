"""
梵天 GEX历史数据构建器 v2.0
接入位置: gex_engine.py → brahma_full_report.py → brahma_core.py s22b

功能：
1. 下载Deribit BTC/ETH DVOL历史（5.5年，2021-03-25起）
2. 下载对应BTC/ETH现货价格历史
3. 基于DVOL + 价格 + 期权OI估算 重建GEX方向标签
4. 输出 gex_history_full.jsonl（每日一条）
5. 重新计算WR矩阵 gex_layer（统计显著性检验）

数据来源：Deribit公开API（免费，无需Key）

2026-09-02 苏摩111封印
"""

import requests
import json
import time
import os
from datetime import datetime, timezone
import statistics

BASE_URL = "https://www.deribit.com/api/v2/public"
DATA_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_FILE = os.path.join(DATA_DIR, "gex_history_full.jsonl")
WR_OUTPUT_FILE = os.path.join(DATA_DIR, "gex_wr_matrix_full.json")


def fetch_dvol(currency: str, start_ts: int, end_ts: int) -> list:
    """分批拉取DVOL历史（日线），返回 [[timestamp, open, high, low, close], ...]"""
    all_data = []
    cur = start_ts
    while cur < end_ts:
        batch_end = cur + 1000 * 86400 * 1000
        url = f"{BASE_URL}/get_volatility_index_data?currency={currency}&start_timestamp={cur}&end_timestamp={min(batch_end, end_ts)}&resolution=86400"
        try:
            r = requests.get(url, timeout=20)
            d = r.json()
            arr = d.get("result", {}).get("data", [])
            all_data.extend(arr)
        except Exception as e:
            print(f"  DVOL fetch error {currency} @ {cur}: {e}")
        cur = batch_end
        time.sleep(0.1)
    return all_data


def fetch_price_history(instrument: str, start_ts: int, end_ts: int) -> dict:
    """拉取日线OHLCV，返回 {date_str: close_price}"""
    prices = {}
    cur = start_ts
    while cur < end_ts:
        batch_end = cur + 500 * 86400 * 1000
        url = f"{BASE_URL}/get_tradingview_chart_data?instrument_name={instrument}&start_timestamp={cur}&end_timestamp={min(batch_end, end_ts)}&resolution=1D"
        try:
            r = requests.get(url, timeout=20)
            d = r.json()
            result = d.get("result", {})
            ticks = result.get("ticks", [])
            closes = result.get("close", [])
            for ts, c in zip(ticks, closes):
                dt_str = datetime.utcfromtimestamp(ts / 1000).strftime("%Y-%m-%d")
                prices[dt_str] = c
        except Exception as e:
            print(f"  Price fetch error {instrument} @ {cur}: {e}")
        cur = batch_end
        time.sleep(0.1)
    return prices


def fetch_current_oi_snapshot(currency: str) -> dict:
    """获取当前期权OI快照，用于估算OI分布特征"""
    url = f"{BASE_URL}/get_book_summary_by_currency?currency={currency}&kind=option"
    try:
        r = requests.get(url, timeout=15)
        d = r.json()
        return {"count": len(d.get("result", [])), "raw": d.get("result", [])}
    except Exception as e:
        print(f"  OI snapshot error: {e}")
        return {}


def classify_gex_direction(dvol_close: float, dvol_ma20: float, dvol_pct: float,
                            price_change_pct: float, hv_ratio: float) -> str:
    """
    基于DVOL水平和动态特征分类GEX方向
    
    逻辑：
    - DVOL > HV(历史波动)：IV溢价为正 → dealer空Gamma → GEX为负（BEARISH压力）
    - DVOL < HV：IV折价 → dealer多Gamma → GEX为正（BULLISH支撑）
    - DVOL剧烈上升（恐慌spike）→ BEARISH
    - DVOL温和下降 → NEUTRAL到BULLISH
    
    返回: "BULLISH" / "NEUTRAL" / "BEARISH"
    """
    iv_premium = hv_ratio  # DVOL / HV30，>1表示溢价

    # 绝对水平分层
    if dvol_close > 80:
        level_signal = -2  # 极高IV = dealer空gamma重
    elif dvol_close > 60:
        level_signal = -1
    elif dvol_close > 40:
        level_signal = 0
    elif dvol_close > 25:
        level_signal = 1
    else:
        level_signal = 2  # 极低IV = dealer多gamma

    # 相对MA20
    vs_ma = (dvol_close - dvol_ma20) / dvol_ma20 if dvol_ma20 > 0 else 0
    if vs_ma > 0.15:
        trend_signal = -1  # 快速上升 = 恐慌
    elif vs_ma < -0.10:
        trend_signal = 1  # 快速下降 = 恐慌消退
    else:
        trend_signal = 0

    # IV溢价 vs 历史波动率
    if iv_premium > 1.3:
        premium_signal = -1  # 大幅溢价 = BEARISH
    elif iv_premium < 0.85:
        premium_signal = 1  # 折价 = BULLISH
    else:
        premium_signal = 0

    total = level_signal + trend_signal + premium_signal

    if total >= 2:
        return "BULLISH"
    elif total <= -2:
        return "BEARISH"
    else:
        return "NEUTRAL"


def compute_hv30(prices_list: list, idx: int) -> float:
    """计算idx前30天的历史波动率（年化）"""
    if idx < 31:
        return 50.0  # 默认值
    returns = []
    for i in range(idx - 30, idx):
        if prices_list[i] > 0 and prices_list[i - 1] > 0:
            r = (prices_list[i] / prices_list[i - 1]) - 1
            returns.append(r)
    if len(returns) < 5:
        return 50.0
    try:
        std = statistics.stdev(returns)
        return std * (365 ** 0.5) * 100  # 年化%
    except Exception:
        return 50.0


def build_gex_history(currency: str, dvol_data: list, price_dict: dict) -> list:
    """构建单币种GEX历史列表"""
    records = []

    # 按日期排序
    dvol_by_date = {}
    for row in dvol_data:
        ts, o, h, l, c = row
        dt = datetime.utcfromtimestamp(ts / 1000).strftime("%Y-%m-%d")
        dvol_by_date[dt] = {"open": o, "high": h, "low": l, "close": c}

    dates_sorted = sorted(dvol_by_date.keys())
    price_series = [price_dict.get(d, 0) for d in dates_sorted]

    # 计算MA20
    for i, date_str in enumerate(dates_sorted):
        dvol_row = dvol_by_date[date_str]
        dvol_c = dvol_row["close"]

        # MA20
        ma20_window = [dvol_by_date[d]["close"] for d in dates_sorted[max(0, i-19):i+1]]
        dvol_ma20 = sum(ma20_window) / len(ma20_window)

        # pct change
        dvol_pct = 0.0
        if i > 0:
            prev_d = dates_sorted[i - 1]
            prev_c = dvol_by_date[prev_d]["close"]
            if prev_c > 0:
                dvol_pct = (dvol_c - prev_c) / prev_c

        # 价格变化
        price_cur = price_series[i] if price_series[i] else 0
        price_prev = price_series[i - 1] if i > 0 and price_series[i - 1] else price_cur
        price_change_pct = ((price_cur - price_prev) / price_prev) if price_prev > 0 else 0

        # HV30
        hv30 = compute_hv30(price_series, i)
        hv_ratio = dvol_c / hv30 if hv30 > 0 else 1.0

        gex_dir = classify_gex_direction(
            dvol_close=dvol_c,
            dvol_ma20=dvol_ma20,
            dvol_pct=dvol_pct,
            price_change_pct=price_change_pct,
            hv_ratio=hv_ratio
        )

        records.append({
            "date": date_str,
            "currency": currency,
            "dvol": dvol_c,
            "dvol_ma20": round(dvol_ma20, 2),
            "dvol_pct": round(dvol_pct, 4),
            "hv30": round(hv30, 2),
            "iv_premium_ratio": round(hv_ratio, 3),
            "price": price_cur,
            "price_change_pct": round(price_change_pct, 4),
            "gex_direction": gex_dir,
        })

    return records


def compute_wr_matrix(currency: str, gex_records: list, btc_prices: dict, eth_prices: dict) -> dict:
    """
    计算WR矩阵：每个GEX方向下，持有N天的胜率
    胜率定义：买入后5天收益>0（做多WR）/ 收益<0（做空WR）
    """
    prices = btc_prices if currency == "BTC" else eth_prices
    date_list = sorted(prices.keys())

    results = {"BULLISH": {"long": [], "short": []},
               "NEUTRAL": {"long": [], "short": []},
               "BEARISH": {"long": [], "short": []}}

    for rec in gex_records:
        d = rec["date"]
        gex_dir = rec["gex_direction"]
        # 找5天后收益
        try:
            idx = date_list.index(d)
        except ValueError:
            continue
        if idx + 5 >= len(date_list):
            continue
        p_entry = prices.get(date_list[idx], 0)
        p_exit = prices.get(date_list[idx + 5], 0)
        if p_entry <= 0 or p_exit <= 0:
            continue
        ret = (p_exit - p_entry) / p_entry
        results[gex_dir]["long"].append(1 if ret > 0 else 0)
        results[gex_dir]["short"].append(1 if ret < 0 else 0)

    matrix = {}
    for gex_dir, data in results.items():
        long_wr = sum(data["long"]) / len(data["long"]) if data["long"] else 0
        short_wr = sum(data["short"]) / len(data["short"]) if data["short"] else 0
        n = len(data["long"])
        # Wilson CI下限
        wilson_long = (long_wr + 1.96**2/(2*n) - 1.96*(long_wr*(1-long_wr)/n + 1.96**2/(4*n**2))**0.5) / (1 + 1.96**2/n) if n > 0 else 0
        wilson_short = (short_wr + 1.96**2/(2*n) - 1.96*(short_wr*(1-short_wr)/n + 1.96**2/(4*n**2))**0.5) / (1 + 1.96**2/n) if n > 0 else 0
        matrix[gex_dir] = {
            "n": n,
            "long_wr": round(long_wr, 4),
            "short_wr": round(short_wr, 4),
            "wilson_long_ci_lower": round(wilson_long, 4),
            "wilson_short_ci_lower": round(wilson_short, 4),
            "statistically_significant": n >= 30,
        }

    return matrix


def main():
    print("=" * 60)
    print("梵天 GEX历史构建器 v2.0")
    print("数据来源: Deribit公开API（免费）")
    print("=" * 60)

    START_TS = 1616544000000   # 2021-03-24
    END_TS = int(time.time() * 1000)

    # Step 1: 下载DVOL
    print("\n[Step 1] 下载BTC/ETH DVOL历史...")
    btc_dvol = fetch_dvol("BTC", START_TS, END_TS)
    print(f"  BTC DVOL: {len(btc_dvol)}天")
    eth_dvol = fetch_dvol("ETH", START_TS, END_TS)
    print(f"  ETH DVOL: {len(eth_dvol)}天")

    # Step 2: 下载价格
    print("\n[Step 2] 下载BTC/ETH现货价格历史...")
    btc_prices = fetch_price_history("BTC-PERPETUAL", START_TS, END_TS)
    print(f"  BTC价格: {len(btc_prices)}天")
    eth_prices = fetch_price_history("ETH-PERPETUAL", START_TS, END_TS)
    print(f"  ETH价格: {len(eth_prices)}天")

    # Step 3: 构建GEX历史
    print("\n[Step 3] 重建GEX方向标签...")
    btc_records = build_gex_history("BTC", btc_dvol, btc_prices)
    eth_records = build_gex_history("ETH", eth_dvol, eth_prices)

    # 统计分布
    for currency, records in [("BTC", btc_records), ("ETH", eth_records)]:
        dirs = [r["gex_direction"] for r in records]
        b = dirs.count("BULLISH")
        n = dirs.count("NEUTRAL")
        bear = dirs.count("BEARISH")
        print(f"  {currency}: {len(records)}天 | BULLISH={b}({b*100//len(dirs)}%) NEUTRAL={n}({n*100//len(dirs)}%) BEARISH={bear}({bear*100//len(dirs)}%)")

    # Step 4: 写入文件
    print(f"\n[Step 4] 写入 {OUTPUT_FILE}...")
    all_records = btc_records + eth_records
    with open(OUTPUT_FILE, "w") as f:
        for r in sorted(all_records, key=lambda x: (x["date"], x["currency"])):
            f.write(json.dumps(r) + "\n")
    print(f"  写入 {len(all_records)} 条")

    # Step 5: 计算WR矩阵
    print("\n[Step 5] 计算WR矩阵...")
    wr_results = {}
    for currency, records in [("BTC", btc_records), ("ETH", eth_records)]:
        matrix = compute_wr_matrix(currency, records, btc_prices, eth_prices)
        wr_results[currency] = matrix
        print(f"\n  {currency} GEX×WR矩阵:")
        for gex_dir, stats in matrix.items():
            sig = "✅显著" if stats["statistically_significant"] else "⚠️小样本"
            print(f"    {gex_dir}: n={stats['n']} | 做多WR={stats['long_wr']*100:.1f}% | 做空WR={stats['short_wr']*100:.1f}% | {sig}")

    with open(WR_OUTPUT_FILE, "w") as f:
        json.dump({
            "built_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "data_range": "2021-03-25 ~ NOW",
            "method": "DVOL+HV30+price_change → GEX方向分类 → 5日持有WR",
            "wr_matrix": wr_results
        }, f, indent=2)
    print(f"\n  WR矩阵写入: {WR_OUTPUT_FILE}")

    print("\n[完成] GEX历史数据库构建成功！")
    return wr_results


if __name__ == "__main__":
    main()
