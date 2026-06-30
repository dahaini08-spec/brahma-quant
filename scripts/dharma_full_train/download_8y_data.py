#!/usr/bin/env python3
"""
达摩院 · 8年全周期数据下载器 v1.0
设计院 2026-06-02

训练集: 2017-01-01 ~ 2024-12-31 (严格截止，防穿越)
OOS集:  2025-01-01 ~ 今日       (样本外验证)
标的:   BTCUSDT / ETHUSDT
周期:   15m / 1h / 4h
"""
import os, json, time, datetime, sys, urllib.request

BASE     = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(BASE, 'data', 'dharma_8y')
os.makedirs(DATA_DIR, exist_ok=True)

# 严格截止线（防数据穿越）
TRAIN_CUTOFF    = datetime.datetime(2025, 1, 1, 0, 0, 0, tzinfo=datetime.timezone.utc)
TRAIN_CUTOFF_MS = int(TRAIN_CUTOFF.timestamp() * 1000)
SYMBOLS         = ['BTCUSDT', 'ETHUSDT']
INTERVALS       = ['15m', '1h', '4h']
START_DATE      = datetime.datetime(2017, 1, 1, tzinfo=datetime.timezone.utc)
START_MS        = int(START_DATE.timestamp() * 1000)
INTERVAL_MS     = {'15m': 15*60*1000, '1h': 3600*1000, '4h': 4*3600*1000}
BATCH_SIZE      = 1500

def fetch_klines(symbol, interval, start_ms, end_ms):
    url = (f"https://fapi.binance.com/fapi/v1/klines"
           f"?symbol={symbol}&interval={interval}"
           f"&startTime={start_ms}&endTime={end_ms}&limit={BATCH_SIZE}")
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'dharma/1.0'})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"  WARN {e}"); return []

def download_symbol_interval(symbol, interval):
    out = os.path.join(DATA_DIR, f"{symbol}_{interval}_train.json")
    all_bars = []
    resume_ms = START_MS
    if os.path.exists(out):
        try:
            ex = json.load(open(out))
            if ex:
                all_bars = ex
                resume_ms = ex[-1][0] + INTERVAL_MS[interval]
                print(f"  续传: {len(ex)}条 从{datetime.datetime.fromtimestamp(resume_ms//1000).strftime('%Y-%m-%d')}")
        except: pass

    step_ms = BATCH_SIZE * INTERVAL_MS[interval]
    cur_ms  = resume_ms
    n = 0
    while cur_ms < TRAIN_CUTOFF_MS:
        batch_end = min(cur_ms + step_ms - 1, TRAIN_CUTOFF_MS - 1)
        bars = fetch_klines(symbol, interval, cur_ms, batch_end)
        if not bars:
            time.sleep(3)
            bars = fetch_klines(symbol, interval, cur_ms, batch_end)
        if not bars:
            cur_ms += step_ms; continue
        # 硬过滤：严禁穿越
        bars = [b for b in bars if b[0] < TRAIN_CUTOFF_MS]
        all_bars.extend(bars)
        cur_ms = bars[-1][0] + INTERVAL_MS[interval]
        n += 1
        if n % 100 == 0:
            seen = {b[0]:b for b in all_bars}
            all_bars = sorted(seen.values(), key=lambda x:x[0])
            with open(out,'w') as f: json.dump(all_bars, f)
            pct = (cur_ms - START_MS)/(TRAIN_CUTOFF_MS - START_MS)*100
            print(f"  [{n}批] {len(all_bars)}条 {pct:.1f}%", flush=True)
        time.sleep(0.07)

    seen = {b[0]:b for b in all_bars}
    all_bars = sorted(seen.values(), key=lambda x:x[0])
    with open(out,'w') as f: json.dump(all_bars, f)
    leaks = [b for b in all_bars if b[0] >= TRAIN_CUTOFF_MS]
    t0 = datetime.datetime.fromtimestamp(all_bars[0][0]//1000).strftime('%Y-%m-%d') if all_bars else '?'
    t1 = datetime.datetime.fromtimestamp(all_bars[-1][0]//1000).strftime('%Y-%m-%d') if all_bars else '?'
    status = f"泄漏{len(leaks)}条❌" if leaks else "✅干净"
    print(f"  完成 {symbol} {interval}: {len(all_bars)}条 {t0}~{t1} {status}")
    return out

def main():
    print(f"达摩院 8年数据下载 | 截止={TRAIN_CUTOFF.date()} | 输出={DATA_DIR}")
    for s in SYMBOLS:
        for i in ['1h','4h','15m']:
            print(f"\n→ {s} {i}")
            download_symbol_interval(s, i)
    # manifest
    m = {}
    for s in SYMBOLS:
        m[s] = {}
        for i in INTERVALS:
            fp = os.path.join(DATA_DIR, f"{s}_{i}_train.json")
            if os.path.exists(fp):
                d = json.load(open(fp))
                if d:
                    m[s][i] = {'count':len(d),
                        'start': datetime.datetime.fromtimestamp(d[0][0]//1000).strftime('%Y-%m-%d'),
                        'end':   datetime.datetime.fromtimestamp(d[-1][0]//1000).strftime('%Y-%m-%d'),
                        'cutoff_clean': not any(b[0]>=TRAIN_CUTOFF_MS for b in d)}
    with open(os.path.join(DATA_DIR,'manifest.json'),'w') as f:
        json.dump(m, f, indent=2)
    print(f"\n✅ 完成，清单已写入 {DATA_DIR}/manifest.json")

if __name__ == '__main__':
    main()
