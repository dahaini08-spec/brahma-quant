#!/usr/bin/env python3
"""
梵天暴涨猎手 - Phase1: 历史数据拉取 + 妖币清单生成
设计院×达摩院  量化工程师
目标：拉取近2年全市场1H K线 → 还原每日涨幅榜 → 找出所有暴涨事件
"""
import requests, json, time, datetime, os, sqlite3
from collections import defaultdict

API = 'https://fapi.binance.com'
DB_PATH = os.path.join(os.path.dirname(__file__), 'pump_hunter.db')
CHECKPOINT_PATH = os.path.join(os.path.dirname(__file__), 'phase1_checkpoint.json')

# ── 参数 ──────────────────────────────────────────────
YEARS_BACK = 2          # 拉取近2年
INTERVAL = '1h'         # 1H周期
PUMP_THRESHOLD = 0.50   # 24H涨幅≥50% = 妖币事件
MIN_VOL_USDT = 2_000_000  # 最低24H成交额$2M（过滤垃圾币）
MAX_MARKET_CAP_VOL = 5_000_000_000  # 排除BTC/ETH等超大流通量
WEIGHT_LIMIT = 500      # 每分钟最大权重（安全线，Binance限制1200）
EXCLUDE_SYMBOLS = {'BTCUSDT','ETHUSDT','BNBUSDT','SOLUSDT','XRPUSDT'}

# ── DB初始化 ──────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS klines_1h (
        symbol TEXT, open_time INTEGER, open REAL, high REAL,
        low REAL, close REAL, volume REAL, quote_volume REAL,
        PRIMARY KEY (symbol, open_time)
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS pump_events (
        symbol TEXT, event_time INTEGER, pump_pct REAL,
        price_before REAL, price_peak REAL, vol_24h REAL,
        detected_at TEXT,
        PRIMARY KEY (symbol, event_time)
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS daily_top10 (
        date TEXT, rank INTEGER, symbol TEXT, pct_24h REAL,
        price REAL, vol_24h REAL,
        PRIMARY KEY (date, rank)
    )''')
    conn.commit()
    return conn

# ── 获取所有活跃合约 ──────────────────────────────────
def get_active_symbols():
    info = requests.get(f'{API}/fapi/v1/exchangeInfo', timeout=15).json()
    syms = [s['symbol'] for s in info['symbols']
            if s['status'] == 'TRADING'
            and s['symbol'].endswith('USDT')
            and 'UP' not in s['symbol']
            and 'DOWN' not in s['symbol']
            and s['symbol'] not in EXCLUDE_SYMBOLS]
    print(f'[Phase1] 活跃合约: {len(syms)}个')
    return syms

# ── 拉取单币K线（带限速+重试）────────────────────────
def fetch_klines(symbol, interval, start_ms, end_ms, conn, weight_tracker):
    c = conn.cursor()
    cursor = start_ms
    total_inserted = 0

    while cursor < end_ms:
        # 限速控制
        if weight_tracker['used'] >= WEIGHT_LIMIT:
            sleep_time = 60 - (time.time() - weight_tracker['reset_time'])
            if sleep_time > 0:
                time.sleep(sleep_time + 1)
            weight_tracker['used'] = 0
            weight_tracker['reset_time'] = time.time()

        try:
            resp = requests.get(f'{API}/fapi/v1/klines', params={
                'symbol': symbol, 'interval': interval,
                'startTime': cursor, 'endTime': end_ms,
                'limit': 1000
            }, timeout=15)
            weight_tracker['used'] += 2  # limit=1000 = 2权重

            if resp.status_code == 429:
                print(f'  [限速] {symbol} 等待60s...')
                time.sleep(62)
                weight_tracker['used'] = 0
                weight_tracker['reset_time'] = time.time()
                continue

            data = resp.json()
            if not isinstance(data, list) or not data:
                break

            rows = [(symbol, int(k[0]), float(k[1]), float(k[2]),
                     float(k[3]), float(k[4]), float(k[5]), float(k[7]))
                    for k in data]

            c.executemany('''INSERT OR IGNORE INTO klines_1h
                (symbol,open_time,open,high,low,close,volume,quote_volume)
                VALUES (?,?,?,?,?,?,?,?)''', rows)
            conn.commit()
            total_inserted += len(rows)

            last_ts = int(data[-1][0])
            if last_ts <= cursor:
                break
            cursor = last_ts + 3_600_000  # +1H

        except Exception as e:
            print(f'  [Error] {symbol}: {e}  重试3s...')
            time.sleep(3)

    return total_inserted

# ── 保存/读取断点 ─────────────────────────────────────
def save_checkpoint(done_symbols):
    with open(CHECKPOINT_PATH, 'w') as f:
        json.dump({'done': list(done_symbols), 'ts': datetime.datetime.utcnow().isoformat()}, f)

def load_checkpoint():
    if os.path.exists(CHECKPOINT_PATH):
        with open(CHECKPOINT_PATH) as f:
            data = json.load(f)
        return set(data.get('done', []))
    return set()

# ── 主拉取流程 ────────────────────────────────────────
def run_phase1_fetch():
    print('=' * 60)
    print('梵天暴涨猎手 Phase1 - 数据拉取启动')
    print(f'时间: {datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")}')
    print('=' * 60)

    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = init_db()
    symbols = get_active_symbols()

    # 时间范围：近2年
    now_ms = int(datetime.datetime.utcnow().timestamp() * 1000)
    start_ms = now_ms - int(YEARS_BACK * 365.25 * 24 * 3600 * 1000)

    # 断点续传
    done = load_checkpoint()
    remaining = [s for s in symbols if s not in done]
    print(f'待拉取: {len(remaining)}个  已完成: {len(done)}个')

    weight_tracker = {'used': 0, 'reset_time': time.time()}
    t0 = time.time()

    for i, sym in enumerate(remaining):
        n = fetch_klines(sym, INTERVAL, start_ms, now_ms, conn, weight_tracker)
        done.add(sym)

        elapsed = time.time() - t0
        eta = elapsed / (i + 1) * len(remaining) - elapsed
        print(f'[{i+1}/{len(remaining)}] {sym}: +{n}根  '
              f'权重={weight_tracker["used"]}  ETA={eta/60:.0f}分钟')

        # 每50个保存断点
        if (i + 1) % 50 == 0:
            save_checkpoint(done)
            print(f'  ✅ 断点保存 ({len(done)}个完成)')

    save_checkpoint(done)
    conn.close()
    print(f'\n✅ Phase1拉取完成！耗时={time.time()-t0:.0f}秒')

# ── 妖币事件提取 ──────────────────────────────────────
def extract_pump_events():
    """从K线数据中提取所有暴涨事件"""
    print('\n=== 提取妖币暴涨事件 ===')
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # 获取所有币种
    c.execute('SELECT DISTINCT symbol FROM klines_1h')
    symbols = [r[0] for r in c.fetchall()]
    print(f'分析币种: {len(symbols)}个')

    pump_events = []
    daily_top10_records = []

    # 按天分析涨幅榜
    # 以UTC 08:00为日线切割点（北京时间16:00）
    c.execute('SELECT MIN(open_time), MAX(open_time) FROM klines_1h')
    min_ts, max_ts = c.fetchone()
    if not min_ts:
        print('无数据！')
        return

    dt_start = datetime.datetime.utcfromtimestamp(min_ts/1000).replace(hour=8, minute=0, second=0)
    dt_end   = datetime.datetime.utcfromtimestamp(max_ts/1000)

    current_dt = dt_start
    day_count = 0

    while current_dt < dt_end:
        day_str = current_dt.strftime('%Y-%m-%d')
        snap_ts = int(current_dt.timestamp() * 1000)         # 08:00
        prev_ts = snap_ts - 24 * 3600 * 1000                 # 前一天08:00

        day_perf = []  # (symbol, pct_24h, price_now, vol_24h)

        for sym in symbols:
            c.execute('''SELECT close, quote_volume FROM klines_1h
                WHERE symbol=? AND open_time>=? AND open_time<?
                ORDER BY open_time''', (sym, prev_ts, snap_ts + 3600000))
            rows = c.fetchall()

            if len(rows) < 20:  # 数据不足
                continue

            closes = [r[0] for r in rows]
            vols   = [r[1] for r in rows]
            vol_24h = sum(vols[-24:]) if len(vols) >= 24 else sum(vols)
            price_24h_ago = closes[0]
            price_now     = closes[-1]

            if price_24h_ago <= 0 or vol_24h < MIN_VOL_USDT:
                continue

            pct = (price_now - price_24h_ago) / price_24h_ago * 100
            day_perf.append((sym, pct, price_now, vol_24h))

        # 日涨幅榜TOP10
        day_perf.sort(key=lambda x: x[1], reverse=True)
        for rank, (sym, pct, price, vol) in enumerate(day_perf[:10], 1):
            daily_top10_records.append((day_str, rank, sym, round(pct,2), price, vol))

        # 妖币事件：24H涨幅≥50%
        for sym, pct, price, vol in day_perf:
            if pct >= PUMP_THRESHOLD * 100:
                pump_events.append({
                    'symbol': sym, 'event_time': snap_ts,
                    'event_date': day_str, 'pump_pct': round(pct, 1),
                    'price_at_event': price, 'vol_24h': round(vol, 0)
                })

        current_dt += datetime.timedelta(days=1)
        day_count += 1
        if day_count % 30 == 0:
            print(f'  处理中... {day_str}  妖币事件: {len(pump_events)}个')

    # 写入DB
    c.executemany('''INSERT OR REPLACE INTO pump_events
        (symbol,event_time,pump_pct,price_before,price_peak,vol_24h,detected_at)
        VALUES (?,?,?,?,?,?,?)''',
        [(e['symbol'], e['event_time'], e['pump_pct'],
          e['price_at_event'], e['price_at_event'], e['vol_24h'],
          e['event_date']) for e in pump_events])

    c.executemany('''INSERT OR REPLACE INTO daily_top10
        (date,rank,symbol,pct_24h,price,vol_24h) VALUES (?,?,?,?,?,?)''',
        daily_top10_records)

    conn.commit()
    conn.close()

    print(f'\n✅ 妖币事件总数: {len(pump_events)}')
    print(f'✅ 涨幅榜记录: {len(daily_top10_records)}')

    # 输出汇总
    by_sym = defaultdict(list)
    for e in pump_events:
        by_sym[e['symbol']].append(e['pump_pct'])

    print(f'\n=== 暴涨频次TOP20 ===')
    top_syms = sorted(by_sym.items(), key=lambda x: len(x[1]), reverse=True)[:20]
    for sym, events in top_syms:
        max_pct = max(events)
        print(f'  {sym:<18} {len(events):3d}次暴涨  最大单次={max_pct:.0f}%')

    # 保存汇总JSON
    summary = {
        'generated_at': datetime.datetime.utcnow().isoformat(),
        'total_pump_events': len(pump_events),
        'unique_symbols': len(by_sym),
        'top_pumpers': [{'sym': s, 'count': len(v), 'max_pct': max(v)}
                        for s, v in top_syms],
        'pump_events_sample': sorted(pump_events, key=lambda x: x['pump_pct'],
                                     reverse=True)[:50]
    }
    out_path = os.path.join(os.path.dirname(__file__), 'pump_events_summary.json')
    with open(out_path, 'w') as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f'\n✅ 汇总保存: {out_path}')
    return pump_events

if __name__ == '__main__':
    import sys
    if '--extract-only' in sys.argv:
        extract_pump_events()
    else:
        run_phase1_fetch()
        extract_pump_events()
