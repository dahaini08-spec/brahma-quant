#!/usr/bin/env python3
"""
达摩院数据更新器 v1.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
功能：
  1. 增量更新现有24个标的的parquet（补齐最新数据）
  2. 扩充新标的（从Binance Top成交量自动选取）
  3. 支持多周期：1h / 4h / 1d
  4. 断点续传，失败自动跳过

设计原则：
  - 零AI调用，纯Python + Binance公开API
  - 增量更新（只拉缺口数据，不重复下载）
  - 速率限制：每次请求后sleep，避免429
  - 新标的优先补3年历史（足够训练）
"""

import os, sys, time, json, urllib.request, urllib.error
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timezone, timedelta

BASE   = Path(__file__).parent.parent
DATA   = BASE / "dharma" / "data"
LOG    = BASE / "logs" / "data_updater.log"

# ── 配置 ──────────────────────────────────────────────
# 现有标的（必须更新）
CORE_SYMS = [
    "BTCUSDT","ETHUSDT","BNBUSDT",
    "DOGEUSDT","ADAUSDT","AVAXUSDT","LINKUSDT","DOTUSDT",
    "ATOMUSDT","LTCUSDT","NEARUSDT","APTUSDT","ARBUSDT",
    "INJUSDT","OPUSDT","SUIUSDT","WLDUSDT","TIAUSDT",
    "1000PEPEUSDT","LUNA2USDT","XGBUSDT","PARTIUSDT",
]

# 扩充标的（Top成交量，达摩院训练需要多样性）
EXPAND_SYMS = [
    "TRXUSDT","MATICUSDT","FILUSDT","AAVEUSDT","UNIUSDT",
    "LDOUSDT","RUNEUSDT","EGLDUSDT","FTMUSDT","SANDUSDT",
    "MANAUSDT","GALAUSDT","AXSUSDT","STXUSDT","CFXUSDT",
    "FETUSDT","AGIXUSDT","RENDERUSDT","TAOUSDT","JUPUSDT",
    "PYTHUSDT","WUSDT","ENAUSDT","EIGENUSDT","SCRUSDT",
]

INTERVALS  = ["1h", "4h", "1d"]          # 训练需要的周期
LIMIT      = 1500                          # 每次最多拉1500条（Binance限制）
SLEEP_MS   = 80                            # 请求间隔(ms)，避免限速
NEW_SYM_DAYS = 1095                        # 新标的历史：3年

# ── 工具 ──────────────────────────────────────────────
def log(msg, level="INFO"):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {level} {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(exist_ok=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")

def fetch_klines(symbol: str, interval: str,
                 start_ms: int = None, limit: int = LIMIT) -> list:
    """调Binance合约K线API，返回list of [ts, o, h, l, c, v, ...]"""
    base_url = "https://fapi.binance.com/fapi/v1/klines"
    params = f"symbol={symbol}&interval={interval}&limit={limit}"
    if start_ms:
        params += f"&startTime={start_ms}"
    url = f"{base_url}?{params}"
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code == 400:
            return []   # 标的不存在
        raise
    except Exception as e:
        log(f"  API错误 {symbol}/{interval}: {e}", "WARN")
        return []

def klines_to_df(klines: list) -> pd.DataFrame:
    """K线列表转DataFrame"""
    if not klines:
        return pd.DataFrame()
    cols = ["timestamp","open","high","low","close","volume",
            "close_time","quote_vol","num_trades",
            "taker_buy_base_asset_volume","taker_buy_quote_vol","ignore"]
    df = pd.DataFrame(klines, columns=cols[:len(klines[0])])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.set_index("timestamp")
    for c in ["open","high","low","close","volume","taker_buy_base_asset_volume"]:
        if c in df.columns:
            df[c] = df[c].astype(float)
    keep = ["open","high","low","close","volume","taker_buy_base_asset_volume"]
    return df[[c for c in keep if c in df.columns]]

def parquet_path(sym: str, interval: str) -> Path:
    sym_lower = sym.lower()
    return DATA / f"{sym_lower}_{interval}_2018_2026.parquet"

def update_symbol(sym: str, interval: str, is_new: bool = False) -> dict:
    """
    增量更新单个标的的parquet
    返回: {"sym":..,"interval":..,"added":N,"total":N,"status":"ok"/"skip"/"error"}
    """
    ppath = parquet_path(sym, interval)

    # 确定起始时间
    if ppath.exists():
        df_exist = pd.read_parquet(ppath)
        last_ts  = df_exist.index[-1]
        # 从最后一条的下一个周期开始
        interval_ms = {"1h":3600000,"4h":14400000,"1d":86400000,"15m":900000}
        step_ms = interval_ms.get(interval, 3600000)
        start_ms = int(last_ts.timestamp()*1000) + step_ms
        # 如果数据已经是最新的（<2根K线的gap），跳过
        now_ms = int(datetime.now(timezone.utc).timestamp()*1000)
        if now_ms - start_ms < step_ms * 2:
            return {"sym":sym,"interval":interval,"added":0,"status":"fresh"}
    else:
        # 新标的：拉指定天数的历史
        if is_new:
            days = NEW_SYM_DAYS
        else:
            days = 90   # 非新标的但文件不存在：补90天
        start_ms = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp()*1000)
        df_exist = None

    # 分批拉取（每批LIMIT根）
    all_new = []
    batch_start = start_ms
    max_batches = 200   # 防止无限循环

    for _ in range(max_batches):
        klines = fetch_klines(sym, interval, start_ms=batch_start, limit=LIMIT)
        time.sleep(SLEEP_MS / 1000.0)

        if not klines:
            break

        batch_df = klines_to_df(klines)
        if batch_df.empty:
            break

        all_new.append(batch_df)

        # 判断是否到最新
        last_in_batch = batch_df.index[-1]
        now = datetime.now(timezone.utc)
        interval_td = {"1h":timedelta(hours=1),"4h":timedelta(hours=4),
                        "1d":timedelta(days=1),"15m":timedelta(minutes=15)}
        td = interval_td.get(interval, timedelta(hours=1))
        if last_in_batch >= now - td * 2:
            break   # 已到最新

        # 下一批
        batch_start = int(last_in_batch.timestamp()*1000) + int(td.total_seconds()*1000)

    if not all_new:
        return {"sym":sym,"interval":interval,"added":0,"status":"no_data"}

    df_new = pd.concat(all_new)
    df_new = df_new[~df_new.index.duplicated(keep='last')]
    df_new = df_new.sort_index()

    # 合并（统一tz-naive，避免tz-naive/tz-aware冲突）
    if df_exist is not None:
        if hasattr(df_exist.index, 'tz') and df_exist.index.tz is not None:
            df_exist.index = df_exist.index.tz_localize(None)
        if hasattr(df_new.index, 'tz') and df_new.index.tz is not None:
            df_new.index = df_new.index.tz_localize(None)
        df_merged = pd.concat([df_exist, df_new])
        df_merged = df_merged[~df_merged.index.duplicated(keep='last')]
        df_merged = df_merged.sort_index()
    else:
        if hasattr(df_new.index, 'tz') and df_new.index.tz is not None:
            df_new.index = df_new.index.tz_localize(None)
        df_merged = df_new

    # 写回
    DATA.mkdir(parents=True, exist_ok=True)
    df_merged.to_parquet(ppath)

    added = len(df_new)
    total = len(df_merged)
    return {"sym":sym,"interval":interval,"added":added,"total":total,"status":"ok"}


def get_top_volume_syms(n=50) -> list:
    """从Binance获取当前成交量最大的n个USDT永续合约"""
    url = "https://fapi.binance.com/fapi/v1/ticker/24hr"
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            data = json.loads(r.read())
        usdt = [d for d in data if d["symbol"].endswith("USDT") and "_" not in d["symbol"]]
        usdt.sort(key=lambda x: float(x["quoteVolume"]), reverse=True)
        return [d["symbol"] for d in usdt[:n]]
    except:
        return []


def main(mode="update", expand=True, max_new=25):
    """
    mode:
      update  — 只更新现有24个标的
      expand  — 更新 + 扩充新标的
      full    — 完整重建（慢）
    """
    log(f"🔱 达摩院数据更新器 v1.0  mode={mode}")
    t0 = time.time()

    # 1. 更新现有标的
    log("▶ 阶段1: 增量更新现有标的")
    updated = 0; skipped = 0; errors = 0
    for sym in CORE_SYMS:
        for interval in INTERVALS:
            r = update_symbol(sym, interval)
            if r["status"] == "ok":
                log(f"  ✅ {sym}/{interval}: +{r['added']}条 总={r['total']}条")
                updated += 1
            elif r["status"] == "fresh":
                skipped += 1
            else:
                log(f"  ⚠️  {sym}/{interval}: {r['status']}", "WARN")
                errors += 1

    log(f"  更新完成: 成功={updated} 跳过={skipped} 错误={errors}")

    # 2. 扩充新标的
    if expand and mode in ("expand","full"):
        log("▶ 阶段2: 扩充新标的")

        # 从Binance获取Top成交量
        top_syms = get_top_volume_syms(80)
        existing = set(CORE_SYMS)
        # 过滤出需要新增的
        new_syms = [s for s in top_syms if s not in existing and s not in ["BTCDOMUSDT"]]
        new_syms = new_syms[:max_new]

        log(f"  待扩充: {len(new_syms)}个标的")
        new_ok = 0
        for sym in new_syms:
            for interval in INTERVALS:
                r = update_symbol(sym, interval, is_new=True)
                if r["status"] == "ok":
                    log(f"  🆕 {sym}/{interval}: +{r['added']}条")
                    new_ok += 1
                else:
                    log(f"  ⚠️  {sym}/{interval}: {r['status']}", "WARN")
        log(f"  扩充完成: {new_ok//len(INTERVALS)}个新标的")

    # 3. 统计
    total_files = len(list(DATA.glob("*.parquet")))
    syms_count  = len(set(f.name.split("_")[0] for f in DATA.glob("*.parquet")))
    elapsed = time.time() - t0

    log(f"✅ 数据更新完成  文件={total_files}  标的={syms_count}  耗时={elapsed:.0f}s")
    return {"files": total_files, "syms": syms_count, "elapsed": elapsed}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default="update", choices=["update","expand","full"])
    parser.add_argument("--no-expand", action="store_true")
    parser.add_argument("--max-new", type=int, default=25)
    args = parser.parse_args()
    main(mode=args.mode, expand=not args.no_expand, max_new=args.max_new)
