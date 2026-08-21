#!/usr/bin/env python3
"""
梵天系统验证框架 阶段0 + 阶段1
阶段0: 无前视偏差体制标签重建
阶段1: 体制识别准确率基准测试
"""

import pandas as pd
import numpy as np
import json
from pathlib import Path
from datetime import datetime, timezone

BASE = Path("/root/.openclaw/workspace/trading-system")
OUT_DIR = BASE / "data/historical"
VAL_DIR = BASE / "data/validation"
VAL_DIR.mkdir(parents=True, exist_ok=True)

# ─────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────

def calc_ema_series(closes: np.ndarray, period: int) -> np.ndarray:
    """逐步计算EMA，严格无前视（返回与closes等长的数组）"""
    ema = np.full(len(closes), np.nan)
    # 找到第一个有效起始点
    if len(closes) < period:
        # 数据不足period，用已有数据均值初始化
        ema[0] = closes[0]
        k = 2.0 / (2)  # 用2期EMA近似
    else:
        pass
    
    k = 2.0 / (period + 1)
    # 用前period个收盘价的简单均值作为初始值
    init_end = min(period, len(closes))
    ema[init_end - 1] = np.mean(closes[:init_end])
    for i in range(init_end, len(closes)):
        ema[i] = closes[i] * k + ema[i-1] * (1 - k)
    return ema


def calc_rsi_series(closes: np.ndarray, period: int = 14) -> np.ndarray:
    """逐步计算RSI，严格无前视"""
    n = len(closes)
    rsi = np.full(n, np.nan)
    if n < period + 1:
        return rsi
    
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    
    # 初始Wilder平均（前period个diff的简单均值）
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])
    
    if avg_loss == 0:
        rsi[period] = 100.0
    else:
        rs = avg_gain / avg_loss
        rsi[period] = 100.0 - 100.0 / (1.0 + rs)
    
    for i in range(period + 1, n):
        avg_gain = (avg_gain * (period - 1) + gains[i-1]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i-1]) / period
        if avg_loss == 0:
            rsi[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi[i] = 100.0 - 100.0 / (1.0 + rs)
    
    return rsi


def classify_regime(close, ema20, ema50, ema200, rsi):
    """体制分类（5类）"""
    if np.isnan(ema20) or np.isnan(ema50) or np.isnan(rsi):
        return "CHOP_MID"
    
    if close > ema20 and ema20 > ema50 and rsi > 50:
        return "BULL_TREND"
    elif close < ema50 and ema20 < ema50 and rsi < 55:
        return "BEAR_TREND"
    elif close > ema20 and ema20 < ema50 and rsi > 45:
        return "BEAR_RECOVERY"
    elif close < ema20 and close > ema50:
        return "BEAR_EARLY"
    else:
        return "CHOP_MID"


# ─────────────────────────────────────────────
# 阶段0：无前视偏差体制标签重建
# ─────────────────────────────────────────────

def build_regime_nolookahead(symbol: str) -> pd.DataFrame:
    """
    对每根4H K线时间点T，只用T及之前历史数据计算体制标签。
    EMA计算用1D收盘价（时间对齐到4H K线所在日）。
    RSI用4H收盘价。
    """
    sym_lower = symbol.lower()
    df4h = pd.read_parquet(BASE / f"data/historical/{sym_lower}/{sym_lower}_4h.parquet")
    df1d = pd.read_parquet(BASE / f"data/historical/{sym_lower}/{sym_lower}_1d.parquet")
    
    # 确保索引为UTC时间
    if df4h.index.tz is None:
        df4h.index = df4h.index.tz_localize("UTC")
    if df1d.index.tz is None:
        df1d.index = df1d.index.tz_localize("UTC")
    
    df4h = df4h.sort_index()
    df1d = df1d.sort_index()
    
    # 1D收盘价数组用于EMA计算
    daily_dates = df1d.index
    daily_closes = df1d["close"].values
    
    # 先整体计算1D EMA（在整个1D序列上，逐步展开，保证无前视）
    # 对每根4H bar，找到该bar收盘时点之前的最后一根1D bar，
    # 只使用截至那天的1D数据计算EMA
    # 为效率，预先计算整个1D序列的滚动EMA
    
    n_daily = len(daily_closes)
    ema20_d_all = calc_ema_series(daily_closes, 20)
    ema50_d_all = calc_ema_series(daily_closes, 50)
    ema200_d_all = calc_ema_series(daily_closes, 200)
    
    # 4H收盘价数组，预先计算RSI（对整个4H序列逐步计算）
    closes_4h = df4h["close"].values
    rsi_4h_all = calc_rsi_series(closes_4h, period=14)
    
    # 数据起始日期
    start_date = df4h.index[0]
    reliable_start = start_date + pd.Timedelta(days=180)
    
    print(f"\n{symbol} 4H bars: {len(df4h)}, 1D bars: {n_daily}")
    print(f"  4H range: {df4h.index[0]} → {df4h.index[-1]}")
    print(f"  1D range: {df1d.index[0]} → {df1d.index[-1]}")
    print(f"  可信窗口起始: {reliable_start.date()}")
    
    # 构建结果列
    ts_list = []
    dt_list = []
    close_list = []
    regime_list = []
    rsi_4h_list = []
    ema20_d_list = []
    ema50_d_list = []
    ema200_d_list = []
    reliable_list = []
    
    # 用searchsorted加速：对每根4H bar，找到 <= bar时间的最后一根1D bar索引
    bar_times = df4h.index
    
    # daily_dates转为numpy datetime64用于searchsorted
    daily_dates_np = daily_dates.values
    
    for i, bar_time in enumerate(bar_times):
        close_4h = closes_4h[i]
        rsi_4h = rsi_4h_all[i]
        
        # 找该4H bar之前（含当日）最后一根1D bar
        # 4H bar的日期
        bar_day = bar_time.normalize()  # 当天0点UTC
        
        # searchsorted: 找第一个 > bar_day的位置，减1即为最后一个 <= bar_day
        idx = np.searchsorted(daily_dates_np, bar_day.value, side='right') - 1
        
        if idx < 0:
            # 没有1D数据可用
            ema20 = np.nan
            ema50 = np.nan
            ema200 = np.nan
        else:
            ema20 = ema20_d_all[idx]
            ema50 = ema50_d_all[idx]
            ema200 = ema200_d_all[idx]
        
        regime = classify_regime(close_4h, ema20, ema50, ema200, rsi_4h)
        reliable = bar_time >= reliable_start
        
        ts_list.append(int(bar_time.timestamp() * 1000))
        dt_list.append(bar_time.isoformat())
        close_list.append(close_4h)
        regime_list.append(regime)
        rsi_4h_list.append(round(rsi_4h, 4) if not np.isnan(rsi_4h) else np.nan)
        ema20_d_list.append(round(ema20, 4) if not np.isnan(ema20) else np.nan)
        ema50_d_list.append(round(ema50, 4) if not np.isnan(ema50) else np.nan)
        ema200_d_list.append(round(ema200, 4) if not np.isnan(ema200) else np.nan)
        reliable_list.append(reliable)
    
    result = pd.DataFrame({
        "ts": ts_list,
        "dt": dt_list,
        "close": close_list,
        "regime": regime_list,
        "rsi_4h": rsi_4h_list,
        "ema20_d": ema20_d_list,
        "ema50_d": ema50_d_list,
        "ema200_d": ema200_d_list,
        "reliable": reliable_list,
    })
    
    # 统计体制分布
    reliable_df = result[result["reliable"]]
    dist = reliable_df["regime"].value_counts()
    print(f"\n  体制分布（可信窗口内，共{len(reliable_df)}根4H bars）：")
    for regime, cnt in dist.items():
        pct = cnt / len(reliable_df) * 100
        print(f"    {regime}: {cnt} ({pct:.1f}%)")
    
    return result


# ─────────────────────────────────────────────
# 阶段1：体制识别准确率基准测试
# ─────────────────────────────────────────────

GOLDEN_NODES = [
    {"id": "G1",  "date": "2020-03-12", "expected": "BEAR_TREND",     "desc": "COVID崩盘，单日-50%"},
    {"id": "G2",  "date": "2020-05-01", "expected": "CHOP_MID",       "desc": "崩后横盘累积"},
    {"id": "G3",  "date": "2020-10-21", "expected": "BULL_TREND",     "desc": "牛市启动，突破10K"},
    {"id": "G4",  "date": "2021-01-08", "expected": "BULL_TREND",     "desc": "BTC突破40K新高"},
    {"id": "G5",  "date": "2021-04-14", "expected": "BEAR_EARLY",     "desc": "64K顶部转折开始"},
    {"id": "G6",  "date": "2021-07-20", "expected": "BEAR_RECOVERY",  "desc": "29K反弹启动"},
    {"id": "G7",  "date": "2021-09-07", "expected": "BULL_TREND",     "desc": "51K牛市续涨"},
    {"id": "G8",  "date": "2021-11-10", "expected": "BEAR_EARLY",     "desc": "69K ATH顶部"},
    {"id": "G9",  "date": "2022-01-21", "expected": "BEAR_TREND",     "desc": "确认熊市转折"},
    {"id": "G10", "date": "2022-05-09", "expected": "BEAR_TREND",     "desc": "Luna崩盘加速"},
    {"id": "G11", "date": "2022-06-18", "expected": "BEAR_TREND",     "desc": "跌破20K恐慌"},
    {"id": "G12", "date": "2022-11-09", "expected": "BEAR_TREND",     "desc": "FTX崩盘"},
    {"id": "G13", "date": "2023-01-12", "expected": "BEAR_RECOVERY",  "desc": "反弹启动"},
    {"id": "G14", "date": "2023-04-14", "expected": "CHOP_MID",       "desc": "横盘整理"},
    {"id": "G15", "date": "2023-10-23", "expected": "BULL_TREND",     "desc": "牛市确认突破"},
    {"id": "G16", "date": "2024-01-10", "expected": "BULL_TREND",     "desc": "ETF获批前夕"},
    {"id": "G17", "date": "2024-03-14", "expected": "BULL_TREND",     "desc": "BTC突破73K ATH"},
    {"id": "G18", "date": "2024-05-01", "expected": "BEAR_EARLY",     "desc": "顶部调整开始"},
    {"id": "G19", "date": "2024-08-05", "expected": "BEAR_TREND",     "desc": "日元套利崩盘"},
    {"id": "G20", "date": "2025-01-20", "expected": "BULL_TREND",     "desc": "BTC突破100K"},
]


def evaluate_phase1(btc_df: pd.DataFrame) -> list:
    """对每个黄金节点，检查±24H内梵天的体制标签"""
    # 将dt列解析为datetime，建立时间索引
    btc_df = btc_df.copy()
    btc_df["datetime"] = pd.to_datetime(btc_df["dt"], utc=True)
    btc_df = btc_df.set_index("datetime").sort_index()
    
    results = []
    
    print("\n阶段1：体制识别准确率基准测试")
    print("=" * 100)
    print(f"{'编号':6} {'日期':12} {'预期':16} {'实际(T)':16} {'实际(T±24H众数)':18} {'匹配':6} {'延迟(根)':8} {'描述'}")
    print("-" * 100)
    
    correct_count = 0
    delays = []
    
    for node in GOLDEN_NODES:
        target_date = pd.Timestamp(node["date"], tz="UTC")
        expected = node["expected"]
        
        # ±24H窗口
        window_start = target_date - pd.Timedelta(hours=24)
        window_end = target_date + pd.Timedelta(hours=24)
        
        window_df = btc_df.loc[window_start:window_end]
        
        if len(window_df) == 0:
            actual_at_t = "N/A"
            actual_mode = "N/A"
            match = False
            delay = None
            note = "无数据"
        else:
            # T时刻最近bar
            td_idx = (window_df.index - target_date).to_series().abs()
            closest_idx = td_idx.argmin()
            actual_at_t = window_df.iloc[closest_idx]["regime"]
            
            # ±24H众数
            actual_mode = window_df["regime"].mode()[0]
            
            # 匹配判定：T时刻或±24H众数匹配即为正确
            match = (actual_at_t == expected) or (actual_mode == expected)
            
            # 延迟计算：找期望体制首次出现的bar序号（相对于target_date）
            if match:
                correct_count += 1
                # 找到第一个匹配预期的bar
                match_bars = window_df[window_df["regime"] == expected]
                if len(match_bars) > 0:
                    first_match_time = match_bars.index[0]
                    delay_hours = (first_match_time - target_date).total_seconds() / 3600
                    delay_bars = int(delay_hours / 4)  # 4H bars
                    delays.append(abs(delay_bars))
                    delay = delay_bars
                else:
                    delay = 0
                    delays.append(0)
            else:
                delay = None
            
            note = ""
        
        match_str = "✓" if match else "✗"
        delay_str = f"{delay}" if delay is not None else "-"
        
        print(f"{node['id']:6} {node['date']:12} {expected:16} {actual_at_t:16} {actual_mode:18} {match_str:6} {delay_str:8} {node['desc']}")
        
        results.append({
            "id": node["id"],
            "date": node["date"],
            "expected": expected,
            "actual_at_t": actual_at_t,
            "actual_mode_24h": actual_mode,
            "match": match,
            "delay_bars": delay,
            "desc": node["desc"],
        })
    
    print("-" * 100)
    accuracy = correct_count / len(GOLDEN_NODES) * 100
    avg_delay = np.mean(delays) if delays else 0
    
    print(f"\n  总计: {correct_count}/{len(GOLDEN_NODES)} 正确")
    print(f"  准确率: {accuracy:.1f}%")
    print(f"  平均延迟 (4H根数, 含负值=提前): {avg_delay:.1f} bars")
    
    return results, correct_count, accuracy, avg_delay


# ─────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────

def main():
    print("=" * 60)
    print("梵天系统验证框架 阶段0 + 阶段1")
    print("=" * 60)
    
    report = {}
    
    # ── 阶段0 BTC ──
    print("\n[阶段0-BTC] 无前视偏差体制标签重建...")
    btc_regime = build_regime_nolookahead("BTCUSDT")
    
    out_path = OUT_DIR / "btcusdt_regime_nolookahead.parquet"
    btc_regime.to_parquet(out_path, index=False)
    print(f"  → 已保存: {out_path}")
    
    reliable_btc = btc_regime[btc_regime["reliable"]]
    report["phase0_btc"] = {
        "total_bars": len(btc_regime),
        "reliable_bars": len(reliable_btc),
        "reliable_start": reliable_btc["dt"].iloc[0] if len(reliable_btc) > 0 else None,
        "regime_distribution": reliable_btc["regime"].value_counts().to_dict(),
    }
    
    # ── 阶段0 ETH ──
    print("\n[阶段0-ETH] 无前视偏差体制标签重建...")
    eth_regime = build_regime_nolookahead("ETHUSDT")
    
    out_path_eth = OUT_DIR / "ethusdt_regime_nolookahead.parquet"
    eth_regime.to_parquet(out_path_eth, index=False)
    print(f"  → 已保存: {out_path_eth}")
    
    reliable_eth = eth_regime[eth_regime["reliable"]]
    report["phase0_eth"] = {
        "total_bars": len(eth_regime),
        "reliable_bars": len(reliable_eth),
        "reliable_start": reliable_eth["dt"].iloc[0] if len(reliable_eth) > 0 else None,
        "regime_distribution": reliable_eth["regime"].value_counts().to_dict(),
    }
    
    # ── 阶段1 ──
    print("\n[阶段1] 体制识别准确率基准测试（基于BTC）...")
    eval_results, correct_count, accuracy, avg_delay = evaluate_phase1(btc_regime)
    
    report["phase1"] = {
        "total_nodes": len(GOLDEN_NODES),
        "correct": correct_count,
        "accuracy_pct": round(accuracy, 2),
        "avg_delay_bars": round(avg_delay, 2),
        "details": eval_results,
    }
    
    # ── 保存报告 ──
    report_path = VAL_DIR / "phase0_phase1_report.json"
    
    # JSON序列化处理（bool类型）
    def convert(obj):
        if isinstance(obj, (np.bool_, bool)):
            return bool(obj)
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        raise TypeError(f"Not serializable: {type(obj)}")
    
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=convert)
    
    print(f"\n  → 报告已保存: {report_path}")
    print("\n阶段0+1完成")
    
    return report


if __name__ == "__main__":
    main()
