#!/usr/bin/env python3
"""
fangcang_db_upgrade.py — 方仓数据库三级补齐
[2026-10-17 苏摩111 设计院封印]

P0: 合并brahma_brain/data/fangcang_*_1w.json → merged_v2（178条，周线覆盖）
P1: BTC/ETH拉取2015~2018历史（Binance klines），扫FVG压缩突破 → merged_v2
P2: 传统资产(XAU/CL/AAPL/AMD/INTC等)用yfinance补全15m/1h/1d/1w → merged_v2

接入位置: 一次性运行，输出写入 data/fangcang_merged_v2.json
冒烟: 运行后验证merged_v2案例数增量 + 各周期分布
"""

import json, sys, time, math
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict

BASE  = Path(__file__).parent.parent
DATA  = BASE / 'data'
BD    = BASE / 'brahma_brain' / 'data'
V2    = DATA / 'fangcang_merged_v2.json'

# ─────────────────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────────────────
def load_v2():
    if V2.exists():
        d = json.loads(V2.read_text())
        return d if isinstance(d, list) else d.get('cases', d)
    return []

def save_v2(cases):
    tmp = V2.with_suffix('.tmp')
    tmp.write_text(json.dumps(cases, ensure_ascii=False, separators=(',',':')))
    tmp.replace(V2)
    print(f'[save] merged_v2 写入完成: {len(cases)} 案例')

def dedup(cases):
    """去重：sym+timeframe+ts_burst"""
    seen = set()
    out  = []
    for c in cases:
        key = (c.get('symbol',''), c.get('timeframe',''), str(c.get('ts_burst','')))
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out

def calc_bbw_squeeze(closes, period=20, mult=2.0, min_squeeze_bars=5):
    """
    布林带压缩检测：识别 BBW < 历史阈值 的连续压缩段
    返回: list of {ts_start, ts_burst, min_bbw, squeeze_bars, direction, rsi, vol_ratio, future_ret, genuine}
    closes: list of (ts_epoch, open, high, low, close, volume)
    """
    if len(closes) < period + 10:
        return []

    results = []
    n = len(closes)

    # 计算每根K线的BBW (百分比)
    bbws = []
    for i in range(period - 1, n):
        window = [c[4] for c in closes[i - period + 1: i + 1]]
        mean   = sum(window) / period
        var    = sum((x - mean)**2 for x in window) / period
        std    = math.sqrt(var)
        bbw    = (2 * std / mean * 100) if mean > 0 else 0
        bbws.append((i, bbw))

    # 历史BBW中位数（用于判断压缩阈值）
    all_bbws = [b for _, b in bbws]
    all_bbws_sorted = sorted(all_bbws)
    median_bbw = all_bbws_sorted[len(all_bbws_sorted) // 2] if all_bbws_sorted else 2.0
    # 压缩阈值：中位数的 60%
    squeeze_thresh = median_bbw * 0.60

    # RSI计算
    def calc_rsi(idx, win=14):
        if idx < win: return 50.0
        gains = losses = 0.0
        for j in range(idx - win + 1, idx + 1):
            diff = closes[j][4] - closes[j-1][4]
            if diff > 0: gains += diff
            else: losses -= diff
        ag = gains / win; al = losses / win
        if al == 0: return 100.0
        return 100 - 100 / (1 + ag / al)

    # 扫描压缩段 → 突破
    i = period
    while i < n - 3:
        idx, bbw = bbws[i - (period - 1)]
        if bbw <= squeeze_thresh:
            # 找压缩开始
            sq_start = i
            min_bbw   = bbw
            while sq_start > period and bbws[sq_start - period][1] <= squeeze_thresh:
                sq_start -= 1
                min_bbw = min(min_bbw, bbws[sq_start - period][1])

            sq_len = i - sq_start + 1
            if sq_len >= min_squeeze_bars:
                # 突破判断：下一根K线
                burst_idx = i + 1
                if burst_idx >= n:
                    i += 1; continue
                cur  = closes[burst_idx]
                prev = closes[burst_idx - 1]
                # 方向：收盘 vs 前收盘
                direction = 'UP' if cur[4] > prev[4] else 'DOWN'
                # 成交量比
                avg_vol = sum(c[5] for c in closes[max(0,burst_idx-20):burst_idx]) / min(20, burst_idx)
                vol_ratio = cur[5] / avg_vol if avg_vol > 0 else 1.0
                # ATR
                atr = sum(abs(closes[j][2] - closes[j][3]) for j in range(max(0,burst_idx-14), burst_idx+1)) / 14
                # 未来24h收益（用后续4根K线的最大/最小）
                fut_end = min(burst_idx + 4, n - 1)
                fut_highs = [closes[j][2] for j in range(burst_idx+1, fut_end+1)]
                fut_lows  = [closes[j][3] for j in range(burst_idx+1, fut_end+1)]
                if fut_highs and fut_lows:
                    fut_ret = (max(fut_highs) - cur[4]) / cur[4] * 100 if direction == 'UP' else \
                              (cur[4] - min(fut_lows)) / cur[4] * 100
                else:
                    fut_ret = 0.0
                genuine = vol_ratio > 1.5 and fut_ret > 0.5
                rsi_val = calc_rsi(burst_idx)

                results.append({
                    'ts_squeeze_start': datetime.fromtimestamp(closes[sq_start][0]/1000, tz=timezone.utc).isoformat(),
                    'ts_burst':         datetime.fromtimestamp(cur[0]/1000, tz=timezone.utc).isoformat(),
                    'direction':        direction,
                    'min_bb_width':     round(min_bbw, 4),
                    'squeeze_bars':     sq_len,
                    'rsi_at_burst':     round(rsi_val, 2),
                    'vol_ratio_peak':   round(vol_ratio, 3),
                    'future_return':    round(fut_ret, 4),
                    'future_return_24h':round(fut_ret, 4),
                    'pnl_long_24h':     round(fut_ret if direction=='UP' else -fut_ret, 4),
                    'is_genuine_breakout': genuine,
                    'atr_at_burst':     round(atr / cur[4] * 100, 4),  # ATR%
                    'burst_atr_mult':   round(abs(cur[4] - prev[4]) / atr, 3) if atr > 0 else 0,
                    '_source':          'fangcang_db_upgrade',
                })
                i = burst_idx + 1
                continue
        i += 1
    return results


# ─────────────────────────────────────────────────────────
# P0: 合并1W案例
# ─────────────────────────────────────────────────────────
def p0_merge_1w(cases):
    print('\n===== P0: 合并1W周线案例 =====')
    w1_files = sorted(BD.glob('fangcang_*_1w.json'))
    added = 0
    for f in w1_files:
        try:
            d = json.loads(f.read_text())
            if not isinstance(d, list): continue
            for c in d:
                c['_source'] = f'p0_merge_{f.name}'
                cases.append(c)
                added += 1
        except Exception as e:
            print(f'  [WARN] {f.name}: {e}')
    print(f'  合并1W案例: +{added} 条')
    return cases


# ─────────────────────────────────────────────────────────
# P1: BTC/ETH 2015~2018 历史（Binance klines）
# ─────────────────────────────────────────────────────────
def p1_crypto_history(cases):
    print('\n===== P1: BTC/ETH 2015~2018 历史补充 =====')
    import urllib.request, time as _t

    SYMBOLS = [('BTC', 'BTCUSDT'), ('ETH', 'ETHUSDT')]
    TFS = [
        ('15m', 15*60*1000,  500),
        ('1h',  60*60*1000,  500),
        ('4h',  4*60*60*1000,500),
        ('1d',  24*60*60*1000,500),
    ]
    # 2015-01-01 ~ 2019-01-01
    START_MS = int(datetime(2015,1,1, tzinfo=timezone.utc).timestamp() * 1000)
    END_MS   = int(datetime(2019,1,1, tzinfo=timezone.utc).timestamp() * 1000)

    total_added = 0
    for sym, usdt in SYMBOLS:
        for tf, interval_ms, limit in TFS:
            print(f'  {sym} {tf}: 拉取2015~2018...', end=' ', flush=True)
            all_klines = []
            cur_start = START_MS
            while cur_start < END_MS:
                url = (f'https://api.binance.com/api/v3/klines'
                       f'?symbol={usdt}&interval={tf}&startTime={cur_start}'
                       f'&endTime={END_MS}&limit={limit}')
                try:
                    resp = json.loads(urllib.request.urlopen(url, timeout=10).read())
                    if not resp: break
                    all_klines.extend(resp)
                    last_ts = int(resp[-1][0])
                    if last_ts <= cur_start: break
                    cur_start = last_ts + interval_ms
                    _t.sleep(0.1)
                except Exception as e:
                    print(f'[ERR:{e}]', end=' ')
                    break

            if not all_klines:
                print('无数据')
                continue

            # 格式: [ts, open, high, low, close, volume, ...]
            kl = [(int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5]))
                  for k in all_klines]
            new_cases = calc_bbw_squeeze(kl)
            for c in new_cases:
                c['symbol']    = sym
                c['timeframe'] = tf
                c['_source']   = 'p1_binance_history'
            cases.extend(new_cases)
            total_added += len(new_cases)
            print(f'+{len(new_cases)}案例 ({len(kl)}根K线)')

    print(f'  P1合计新增: {total_added} 案例')
    return cases


# ─────────────────────────────────────────────────────────
# P2: 传统资产补全周期（yfinance）
# ─────────────────────────────────────────────────────────
def p2_tradfi_all_tf(cases):
    print('\n===== P2: 传统资产补全周期（yfinance）=====')
    import yfinance as yf
    import time as _t

    # 标的映射：方仓symbol → yfinance ticker
    TRADFI = [
        ('XAUUSDT', 'GC=F',   '黄金'),
        ('XAGUSDT', 'SI=F',   '白银'),
        ('CLUSDT',  'CL=F',   '原油'),
        ('AAPLUSDT','AAPL',   'AAPL'),
        ('AMDUSDT', 'AMD',    'AMD'),
        ('INTCUSDT','INTC',   'INTC'),
        ('SNDKUSDT','SNDK',   'SNDK'),  # 已退市，yfinance可能无数据
        ('MUUSDT',  'MU',     'MU'),
        ('MSFTUSDT','MSFT',   'MSFT'),
        ('NVDAUSDT','NVDA',   'NVDA'),
        ('GOOGLUSDT','GOOGL', 'GOOGL'),
        ('QQQUSDT', 'QQQ',    'QQQ'),
        ('TSLAUSDT','TSLA',   'TSLA'),
        ('MSTRUSDT','MSTR',   'MSTR'),
    ]
    # yfinance周期映射（只补缺失的周期）
    # 注意：yfinance 15m/1h数据只有近60天，历史只有1d/1wk
    TF_MAP = [
        ('1d',  '1d',  None),   # 日线：全历史
        ('1w',  '1wk', None),   # 周线：全历史
        # 15m/1h历史超过60天yfinance拿不到，跳过（需要专业数据源）
    ]

    # 已有案例的sym+tf组合（避免重复添加4H已有的部分）
    existing_keys = set()
    for c in cases:
        sym = c.get('symbol','')
        tf  = c.get('timeframe','')
        if sym and tf:
            existing_keys.add((sym, tf))

    total_added = 0
    for fc_sym, yf_ticker, label in TRADFI:
        fc_sym_short = fc_sym.replace('USDT','')
        for tf, yf_interval, _ in TF_MAP:
            if (fc_sym, tf) in existing_keys:
                print(f'  {label} {tf}: 已有，跳过')
                continue
            # 也检查短名
            if (fc_sym_short, tf) in existing_keys:
                print(f'  {label} {tf}: 已有(短名)，跳过')
                continue

            print(f'  {label}({yf_ticker}) {tf}: 拉取...', end=' ', flush=True)
            try:
                ticker = yf.Ticker(yf_ticker)
                hist = ticker.history(period='max', interval=yf_interval, auto_adjust=True)
                if hist.empty:
                    print('无数据')
                    continue

                # 转换为 (ts_ms, o, h, l, c, v) 格式
                kl = []
                for ts_idx, row in hist.iterrows():
                    ts_ms = int(ts_idx.timestamp() * 1000)
                    kl.append((ts_ms, float(row['Open']), float(row['High']),
                               float(row['Low']), float(row['Close']),
                               float(row.get('Volume', 0))))
                kl.sort(key=lambda x: x[0])

                new_cases = calc_bbw_squeeze(kl)
                for c in new_cases:
                    c['symbol']    = fc_sym_short  # 用短名与现有一致
                    c['timeframe'] = tf
                    c['_source']   = f'p2_yfinance_{yf_ticker}'
                cases.extend(new_cases)
                total_added += len(new_cases)
                existing_keys.add((fc_sym_short, tf))
                print(f'+{len(new_cases)}案例 ({len(kl)}根K线，{kl[0][0]//1000//86400//365+1970}~{kl[-1][0]//1000//86400//365+1970})')
                _t.sleep(0.3)  # 防rate limit
            except Exception as e:
                print(f'ERR: {e}')

    print(f'  P2合计新增: {total_added} 案例')
    return cases


# ─────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────
if __name__ == '__main__':
    t0 = time.time()
    print('=== 方仓数据库三级补齐 ===')
    print(f'时间: {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}')

    cases = load_v2()
    before = len(cases)
    print(f'当前merged_v2: {before} 案例')

    # 统计当前分布
    from collections import Counter
    tf_before = Counter(c.get('timeframe') for c in cases)
    print(f'现有周期分布: {dict(tf_before)}')

    # ── P0 ──
    cases = p0_merge_1w(cases)

    # ── P1 ──
    cases = p1_crypto_history(cases)

    # ── P2 ──
    cases = p2_tradfi_all_tf(cases)

    # 去重
    before_dedup = len(cases)
    cases = dedup(cases)
    print(f'\n去重: {before_dedup} → {len(cases)} (移除{before_dedup-len(cases)}重复)')

    # 保存
    save_v2(cases)

    # 冒烟验证
    print('\n=== 冒烟验证 ===')
    after = len(cases)
    tf_after = Counter(c.get('timeframe') for c in cases)
    print(f'案例数: {before} → {after} (+{after-before})')
    print(f'周期分布: {dict(tf_after)}')

    # 各资产统计
    sym_tf = defaultdict(lambda: defaultdict(int))
    for c in cases:
        sym_tf[c.get('symbol','?')][c.get('timeframe','?')] += 1

    print('\n--- 传统资产周期覆盖 ---')
    for sym in ['XAUUSDT','CLUSDT','AAPLUSDT','AMDUSDT','INTCUSDT','MUUSDT']:
        s = sym.replace('USDT','')
        tfs = {k:v for k,v in sym_tf.get(s,{}).items()}
        if tfs:
            print(f'  {s}: {tfs}')

    print('\n--- 加密货币周期覆盖 ---')
    for sym in ['BTC','ETH','SOL']:
        tfs = {k:v for k,v in sym_tf.get(sym,{}).items()}
        print(f'  {sym}: {tfs}')

    # 清除fangcang_engine缓存（强制重新加载）
    cache_flag = BASE / 'brahma_brain' / '__fangcang_cache_dirty__'
    cache_flag.touch()

    elapsed = time.time() - t0
    print(f'\n耗时: {elapsed:.1f}s')
    print('✅ 方仓数据库三级补齐完成')
    print('提示: 重启brahma_state_refresh后fangcang_engine会自动重载新库')
