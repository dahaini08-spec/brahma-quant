#!/usr/bin/env python3
"""
fangcang_builder_30.py — 为30个币种批量构建方仓案例库
══════════════════════════════════════════════════════
设计院 2026-08-25 苏摩111

策略：
  - 拉取5年+4H K线（约10950根）
  - 滑窗扫描 BB压缩（BBW < threshold）→ 方仓案例
  - 字段与 fangcang_cases_btc.json 完全对齐
  - 输出至 data/fangcang_cases_{sym}.json
"""

import sys, os, json, time, requests
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from pathlib import Path
from datetime import datetime, timezone

_DATA = Path(__file__).parent.parent / 'data'

# 30个候选币种（5年+ 按流动性排序）
TARGET_SYMBOLS = [
    'XRPUSDT', 'ZECUSDT', 'DOGEUSDT', 'BNBUSDT', 'LINKUSDT',
    'ADAUSDT', 'BCHUSDT', 'LTCUSDT', 'XLMUSDT', 'XMRUSDT',
    'DASHUSDT', 'TRXUSDT', 'ETCUSDT', 'DOTUSDT', 'CRVUSDT',
    'ATOMUSDT', 'ALGOUSDT', 'ONTUSDT', 'TRBUSDT', 'RUNEUSDT',
    'VETUSDT', 'EGLDUSDT', 'COMPUSDT', 'SNXUSDT', 'THETAUSDT',
    'IOTAUSDT', 'KAVAUSDT', 'NEOUSDT', 'SUSHIUSDT', 'ZILUSDT',
]

BBW_THRESH_DEFAULT = 0.015   # BTC/ETH默认，山寨币用动态阈值
MIN_BARS     = 8       # 最短压缩时长（bars）
FUTURE_BARS  = 6       # 突破后观察6根4H（24H）


def fetch_klines(symbol: str, interval='4h') -> list:
    url = 'https://fapi.binance.com/fapi/v1/klines'
    bars = []
    start_ts = int((time.time() - 5.5 * 365 * 86400) * 1000)

    while True:
        params = {'symbol': symbol, 'interval': interval, 'limit': 1500, 'startTime': start_ts}
        try:
            r = requests.get(url, params=params, timeout=15)
            data = r.json()
        except Exception as e:
            print(f'  [{symbol}] 请求失败: {e}')
            break
        if not data or not isinstance(data, list) or len(data) == 0:
            break
        for b in data:
            bars.append({'ts': b[0], 'o': float(b[1]), 'h': float(b[2]),
                         'l': float(b[3]), 'c': float(b[4]), 'v': float(b[5])})
        if len(data) < 1500:
            break
        start_ts = data[-1][0] + 1
        time.sleep(0.1)

    bars.sort(key=lambda x: x['ts'])
    seen = set()
    return [b for b in bars if b['ts'] not in seen and not seen.add(b['ts'])]


def calc_bbw(bars: list, period=20) -> list:
    """计算每根K线的 BB Width"""
    closes = [b['c'] for b in bars]
    result = []
    for i in range(len(closes)):
        if i < period - 1:
            result.append(None)
            continue
        window = closes[i - period + 1: i + 1]
        ma = sum(window) / period
        std = (sum((x - ma) ** 2 for x in window) / period) ** 0.5
        upper = ma + 2 * std
        lower = ma - 2 * std
        bbw = (upper - lower) / ma if ma > 0 else 0
        result.append(bbw)
    return result


def calc_rsi(closes: list, period=14) -> list:
    """计算RSI"""
    rsi = [None] * len(closes)
    if len(closes) < period + 1:
        return rsi
    gains, losses = [], []
    for i in range(1, period + 1):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    for i in range(period, len(closes)):
        diff = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(diff, 0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-diff, 0)) / period
        rs = avg_gain / avg_loss if avg_loss > 0 else 100
        rsi[i] = round(100 - 100 / (1 + rs), 2)
    return rsi


def build_cases(symbol: str, bars: list, bbw_thresh: float = None) -> list:
    """扫描K线，提取方仓案例"""
    cases = []
    bbws = calc_bbw(bars)
    closes = [b['c'] for b in bars]
    rsis = calc_rsi(closes)
    n = len(bars)

    # 动态阈值：用该币P15分位BBW，适配不同波动率
    if bbw_thresh is None:
        valid_bbws = sorted(b for b in bbws if b is not None)
        p15_idx = max(0, int(len(valid_bbws) * 0.15))
        bbw_thresh = valid_bbws[p15_idx] if valid_bbws else BBW_THRESH_DEFAULT
        bbw_thresh = min(bbw_thresh, 0.08)   # 上限8%，避免过于宽松
        bbw_thresh = max(bbw_thresh, 0.005)  # 下限0.5%

    i = 20
    while i < n - FUTURE_BARS - 1:
        if bbws[i] is None or bbws[i] >= bbw_thresh:
            i += 1
            continue

        # 找压缩起点（往前找BBW开始<threshold的地方）
        squeeze_start = i
        while squeeze_start > 20 and bbws[squeeze_start - 1] is not None and bbws[squeeze_start - 1] < bbw_thresh:
            squeeze_start -= 1

        squeeze_end = i
        while squeeze_end < n - 1 and bbws[squeeze_end + 1] is not None and bbws[squeeze_end + 1] < bbw_thresh:
            squeeze_end += 1

        squeeze_bars = squeeze_end - squeeze_start + 1
        if squeeze_bars < MIN_BARS:
            i = squeeze_end + 1
            continue

        # 最小BBW
        min_bbw = min(bbws[j] for j in range(squeeze_start, squeeze_end + 1) if bbws[j] is not None)

        # 突破方向
        burst_bar = squeeze_end + 1
        if burst_bar >= n:
            i = squeeze_end + 1
            continue

        entry_price = bars[squeeze_end]['c']
        burst_close = bars[burst_bar]['c']
        burst_pct = (burst_close - entry_price) / entry_price * 100

        direction = 'UP' if burst_pct > 0 else 'DOWN'

        # 未来24H收益
        future_end = min(burst_bar + FUTURE_BARS, n - 1)
        future_closes = [bars[j]['c'] for j in range(burst_bar, future_end + 1)]
        future_ret = (future_closes[-1] - entry_price) / entry_price * 100 if future_closes else 0

        # 是否真实突破（突破幅度 > 1×ATR）
        recent_highs = [bars[j]['h'] for j in range(max(0, squeeze_start - 14), squeeze_start)]
        recent_lows  = [bars[j]['l'] for j in range(max(0, squeeze_start - 14), squeeze_start)]
        atr = sum(h - l for h, l in zip(recent_highs, recent_lows)) / max(len(recent_highs), 1)
        is_genuine = abs(burst_close - entry_price) > atr * 0.8

        # 音量趋势
        burst_vol = bars[burst_bar]['v']
        avg_vol   = sum(bars[j]['v'] for j in range(max(0, squeeze_start - 10), squeeze_start)) / max(10, 1)
        vol_ratio = burst_vol / avg_vol if avg_vol > 0 else 1.0

        # 时间戳
        ts_start = bars[squeeze_start]['ts']
        ts_burst  = bars[burst_bar]['ts']

        case = {
            'symbol':               symbol.replace('USDT', ''),
            'ts_squeeze_start':     datetime.fromtimestamp(ts_start / 1000, tz=timezone.utc).isoformat(),
            'ts_burst':             datetime.fromtimestamp(ts_burst / 1000, tz=timezone.utc).isoformat(),
            'direction':            direction,
            'min_bb_width':         round(min_bbw * 100, 4),  # 转为百分比
            'squeeze_bars':         squeeze_bars,
            'burst_atr_mult':       round(abs(burst_close - entry_price) / atr, 2) if atr > 0 else 0,
            'vol_ratio_peak':       round(vol_ratio, 2),
            'rsi_at_burst':         rsis[squeeze_end] or 50.0,
            'future_return_24h':    round(future_ret, 4),
            'is_genuine_breakout':  is_genuine,
            'pnl_long_24h':         round(-future_ret, 4) if direction == 'DOWN' else round(future_ret, 4),
        }
        cases.append(case)
        i = squeeze_end + 1

    return cases


def build_all():
    results = {}
    for sym in TARGET_SYMBOLS:
        print(f'[{sym}] 拉取K线...')
        bars = fetch_klines(sym)
        if len(bars) < 500:
            print(f'  [{sym}] 数据不足({len(bars)}根)，跳过')
            results[sym] = 0
            continue

        cases = build_cases(sym, bars)  # 内部自动计算动态阈值
        out_path = _DATA / f'fangcang_cases_{sym.lower().replace("usdt","")}.json'
        _DATA.mkdir(exist_ok=True)
        with open(out_path, 'w') as f:
            json.dump(cases, f, ensure_ascii=False, indent=2)

        # 统计
        up   = sum(1 for c in cases if c['direction'] == 'UP')
        down = sum(1 for c in cases if c['direction'] == 'DOWN')
        genuine = sum(1 for c in cases if c['is_genuine_breakout'])
        yrs = round((bars[-1]['ts'] - bars[0]['ts']) / 86400000 / 365, 1)
        print(f'  [{sym}] {len(cases)}案例 {yrs}年 UP={up} DOWN={down} 真实突破={genuine}')
        results[sym] = len(cases)
        time.sleep(0.3)

    return results


if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1:
        # 单个币种模式
        sym = sys.argv[1].upper()
        if not sym.endswith('USDT'):
            sym += 'USDT'
        print(f'单币模式: {sym}')
        bars = fetch_klines(sym)
        print(f'K线: {len(bars)}根')
        cases = build_cases(sym, bars)  # 内部自动计算动态阈值
        out = _DATA / f'fangcang_cases_{sym.lower().replace("usdt","")}.json'
        json.dump(cases, open(out,'w'), ensure_ascii=False, indent=2)
        print(f'写入: {out} ({len(cases)}案例)')
    else:
        print(f'批量构建{len(TARGET_SYMBOLS)}个币种方仓...')
        t0 = time.time()
        results = build_all()
        total = sum(results.values())
        elapsed = time.time() - t0
        print(f'\n完成! 总案例={total} 耗时={elapsed:.0f}s')
