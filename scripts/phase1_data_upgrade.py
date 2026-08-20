#!/usr/bin/env python3
"""
梵天认知升级 阶段1 - 历史数据扩充脚本
功能：
  1. 下载BTC_1d 2014~2019早期历史（补全到当前数据）
  2. 下载SPX/NDX/GOLD/DXY 20年日线 TradFi宏观数据
  3. 统一保存到 data/historical/macro/ 目录

作者：设计院 2026-08-20
"""
import os
import json
import gzip
import datetime
import time

import yfinance as yf
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HIST_DIR = os.path.join(BASE_DIR, "data", "historical")
MACRO_DIR = os.path.join(HIST_DIR, "macro")
os.makedirs(MACRO_DIR, exist_ok=True)

def log(msg):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def download_yf(symbol, start, end, label):
    """下载Yahoo Finance日线数据，返回标准OHLCV列表"""
    log(f"下载 {label} ({symbol}) {start} ~ {end} ...")
    try:
        df = yf.download(symbol, start=start, end=end, interval="1d", progress=False, auto_adjust=True)
        if df.empty:
            log(f"  ⚠️ {label}: 返回空数据")
            return []
        # 标准化列名
        df.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() for c in df.columns]
        df = df.reset_index()
        # date列
        if 'date' in df.columns:
            df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')
        records = []
        for _, row in df.iterrows():
            rec = {
                'date': row.get('date', ''),
                'o': float(row.get('open', 0)),
                'h': float(row.get('high', 0)),
                'l': float(row.get('low', 0)),
                'c': float(row.get('close', 0)),
                'v': float(row.get('volume', 0)),
            }
            records.append(rec)
        log(f"  ✅ {label}: {len(records)} 条 ({records[0]['date']} ~ {records[-1]['date']})")
        return records
    except Exception as e:
        log(f"  ❌ {label} 下载失败: {e}")
        return []


def save_jsonl_gz(records, filepath):
    """保存为jsonl.gz"""
    with gzip.open(filepath, 'wt', encoding='utf-8') as f:
        for r in records:
            f.write(json.dumps(r) + '\n')
    size_kb = os.path.getsize(filepath) / 1024
    log(f"  💾 保存: {filepath} ({size_kb:.1f} KB)")


def load_existing_btc_1d():
    """加载现有BTC_1d数据，返回最早日期"""
    fpath = os.path.join(HIST_DIR, "BTCUSDT_1d.jsonl.gz")
    if not os.path.exists(fpath):
        return None
    with gzip.open(fpath, 'rt') as f:
        lines = f.readlines()
    if not lines:
        return None
    first = json.loads(lines[0])
    ts_ms = first.get('ts', 0)
    if ts_ms:
        return datetime.datetime.fromtimestamp(ts_ms / 1000)
    return None


def merge_btc_early_history(early_records):
    """将早期BTC数据（日期字符串格式）合并写入BTCUSDT_1d_early.jsonl.gz"""
    # 转成ts格式与现有数据兼容
    out = []
    for r in early_records:
        try:
            dt = datetime.datetime.strptime(r['date'], '%Y-%m-%d')
            ts_ms = int(dt.timestamp() * 1000)
            out.append({
                'ts': ts_ms,
                'o': r['o'],
                'h': r['h'],
                'l': r['l'],
                'c': r['c'],
                'v': r['v'],
                'qv': 0.0,
                'tb': 0.0,
                'n': 0,
                '_source': 'yfinance_early_history'
            })
        except Exception:
            continue
    fpath = os.path.join(HIST_DIR, "BTCUSDT_1d_early.jsonl.gz")
    with gzip.open(fpath, 'wt', encoding='utf-8') as f:
        for r in out:
            f.write(json.dumps(r) + '\n')
    size_kb = os.path.getsize(fpath) / 1024
    log(f"  💾 BTC早期历史保存: {fpath} ({size_kb:.1f} KB, {len(out)} 条)")
    return fpath


def main():
    log("=" * 60)
    log("🏛️ 梵天认知升级 阶段1 - 历史数据扩充")
    log("=" * 60)

    # ── 步骤1：BTC早期历史 2014~2019 ──
    log("\n📥 步骤1：下载BTC早期历史数据 2014~2019")
    existing_start = load_existing_btc_1d()
    if existing_start:
        log(f"  现有数据起始: {existing_start.strftime('%Y-%m-%d')}")
        btc_end = existing_start.strftime('%Y-%m-%d')
    else:
        btc_end = "2019-11-01"
    
    btc_early = download_yf('BTC-USD', '2013-01-01', btc_end, 'BTC早期历史')
    if btc_early:
        merge_btc_early_history(btc_early)
    
    time.sleep(1)  # 避免频繁请求

    # ── 步骤2：TradFi宏观数据 ──
    log("\n📥 步骤2：下载TradFi宏观数据（20年）")
    
    tradfi_targets = [
        ('^GSPC',    '2000-01-01', '2026-08-20', 'SPX标普500',   'SPX_1d.jsonl.gz'),
        ('^NDX',     '2000-01-01', '2026-08-20', 'NDX纳斯达克100','NDX_1d.jsonl.gz'),
        ('GC=F',     '2000-01-01', '2026-08-20', 'GOLD黄金',     'GOLD_1d.jsonl.gz'),
        ('DX-Y.NYB', '2000-01-01', '2026-08-20', 'DXY美元指数',  'DXY_1d.jsonl.gz'),
        ('^VIX',     '2004-01-01', '2026-08-20', 'VIX恐慌指数',  'VIX_1d.jsonl.gz'),
        ('^TNX',     '2000-01-01', '2026-08-20', 'US10Y美债收益率','US10Y_1d.jsonl.gz'),
    ]
    
    summary = {'btc_early': len(btc_early), 'tradfi': {}}
    
    for symbol, start, end, label, fname in tradfi_targets:
        records = download_yf(symbol, start, end, label)
        if records:
            fpath = os.path.join(MACRO_DIR, fname)
            save_jsonl_gz(records, fpath)
            summary['tradfi'][label] = len(records)
        time.sleep(0.5)
    
    # ── 步骤3：写入汇总 ──
    log("\n📊 步骤3：写入数据汇总")
    summary_path = os.path.join(MACRO_DIR, "macro_data_summary.json")
    summary['downloaded_at'] = datetime.datetime.now().isoformat()
    summary['total_records'] = sum(summary['tradfi'].values()) + summary['btc_early']
    with open(summary_path, 'w') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    
    log("\n" + "=" * 60)
    log("✅ 阶段1-数据下载完成")
    log(f"  BTC早期历史: {summary['btc_early']} 条")
    for label, cnt in summary['tradfi'].items():
        log(f"  {label}: {cnt} 条")
    log(f"  总记录数: {summary['total_records']}")
    log("=" * 60)


if __name__ == '__main__':
    main()
