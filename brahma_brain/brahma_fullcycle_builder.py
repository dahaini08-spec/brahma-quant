"""
brahma_fullcycle_builder.py — 梵天全周期方仓矩阵构建器
══════════════════════════════════════════════════════
设计院 2026-08-25 苏摩111 Phase1封印

使命：
  47个标的 × 6个周期 = 282维度完整经验矩阵
  让AI议会拥有真正的「全周期梵天思维」

周期设计：
  15m → 精确入场时机（近2年高频，~3000案例/币）
  1h  → 方仓形态确认（近3年，~1500案例/币）
  4h  → 主力方向【已有6966条，跳过重建，只增量】
  1d  → 趋势背景（全历史，~200案例/币）
  1w  → 牛熊周期（全历史，~80案例/币）
  1M  → 40年超长期规律（TradFi月线最关键）

输出格式：
  data/fangcang_{sym}_{tf}.json  例: fangcang_btc_1h.json
  每条案例字段与4H对齐（同schema）
"""

import sys
import os
import json
import time
import requests
from pathlib import Path
from datetime import datetime, timezone

_BASE   = Path(__file__).parent
_DATA   = _BASE.parent / 'data'

# ── 47个标的（33加密+14TradFi）──────────────────────────────────────
CRYPTO_SYMBOLS = [
    'BTCUSDT','ETHUSDT','SOLUSDT','BNBUSDT','XRPUSDT',
    'DOGEUSDT','ADAUSDT','LINKUSDT','BCHUSDT','LTCUSDT',
    'XLMUSDT','XMRUSDT','DASHUSDT','TRXUSDT','ETCUSDT',
    'DOTUSDT','CRVUSDT','ATOMUSDT','ALGOUSDT','ONTUSDT',
    'TRBUSDT','RUNEUSDT','VETUSDT','EGLDUSDT','COMPUSDT',
    'SNXUSDT','THETAUSDT','IOTAUSDT','KAVAUSDT','NEOUSDT',
    'SUSHIUSDT','ZILUSDT','ZECUSDT',
]

# TradFi用Yahoo Finance，此处标记跳过（需单独处理）
TRADFI_SYMBOLS = ['XAUUSD','NDX','SPX','DXY','AAPL','NVDA','TSLA','MSFT']

# ── 周期配置 ─────────────────────────────────────────────────────────
TF_CONFIG = {
    '15m': {'bars_back': 2 * 365,   'bbw_pct': 15, 'min_bars': 12, 'future_bars': 4},
    '1h':  {'bars_back': 3 * 365,   'bbw_pct': 15, 'min_bars': 8,  'future_bars': 6},
    '4h':  {'bars_back': 5.5 * 365, 'bbw_pct': 15, 'min_bars': 6,  'future_bars': 6},
    '1d':  {'bars_back': 10 * 365,  'bbw_pct': 10, 'min_bars': 5,  'future_bars': 5},
    '1w':  {'bars_back': 15 * 365,  'bbw_pct': 10, 'min_bars': 3,  'future_bars': 4},
    '1M':  {'bars_back': 40 * 365,  'bbw_pct': 8,  'min_bars': 2,  'future_bars': 3},
}

# ── K线拉取 ──────────────────────────────────────────────────────────
def fetch_klines(symbol: str, interval: str, days_back: float) -> list:
    url   = 'https://fapi.binance.com/fapi/v1/klines'
    start = int((time.time() - days_back * 86400) * 1000)
    bars  = []

    while True:
        try:
            r = requests.get(url, params={
                'symbol': symbol, 'interval': interval,
                'limit': 1500, 'startTime': start,
            }, timeout=15)
            data = r.json()
        except Exception as e:
            print(f'    [{symbol}/{interval}] 请求失败: {e}')
            break

        if not isinstance(data, list) or not data:
            break

        for b in data:
            bars.append({
                'ts': b[0], 'o': float(b[1]), 'h': float(b[2]),
                'l': float(b[3]), 'c': float(b[4]), 'v': float(b[5]),
            })

        if len(data) < 1500:
            break
        start = data[-1][0] + 1
        time.sleep(0.08)

    # 去重排序
    seen, unique = set(), []
    for b in sorted(bars, key=lambda x: x['ts']):
        if b['ts'] not in seen:
            seen.add(b['ts'])
            unique.append(b)
    return unique


# ── 计算指标 ─────────────────────────────────────────────────────────
def calc_bbw(closes: list, period: int = 20) -> list:
    result = [None] * len(closes)
    for i in range(period - 1, len(closes)):
        w   = closes[i - period + 1: i + 1]
        ma  = sum(w) / period
        std = (sum((x - ma) ** 2 for x in w) / period) ** 0.5
        bb_upper = ma + 2 * std
        bb_lower = ma - 2 * std
        result[i] = (bb_upper - bb_lower) / ma if ma > 0 else 0
    return result


def calc_rsi(closes: list, period: int = 14) -> list:
    rsi = [None] * len(closes)
    if len(closes) < period + 1:
        return rsi
    gains = [max(closes[i] - closes[i-1], 0) for i in range(1, period+1)]
    losses = [max(closes[i-1] - closes[i], 0) for i in range(1, period+1)]
    ag, al = sum(gains)/period, sum(losses)/period
    for i in range(period, len(closes)):
        d  = closes[i] - closes[i-1]
        ag = (ag * (period-1) + max(d, 0)) / period
        al = (al * (period-1) + max(-d, 0)) / period
        rs = ag / al if al > 0 else 100
        rsi[i] = round(100 - 100 / (1 + rs), 2)
    return rsi


def calc_atr(bars: list, period: int = 14) -> list:
    atr = [None] * len(bars)
    trs = []
    for i in range(1, len(bars)):
        prev_c = bars[i-1]['c']
        tr = max(bars[i]['h'] - bars[i]['l'],
                 abs(bars[i]['h'] - prev_c),
                 abs(bars[i]['l'] - prev_c))
        trs.append(tr)
    if len(trs) < period:
        return atr
    avg = sum(trs[:period]) / period
    atr[period] = avg
    for i in range(period, len(trs)):
        avg = (avg * (period-1) + trs[i]) / period
        atr[i+1] = avg
    return atr


# ── 工具：EMA计算 ───────────────────────────────────────────────────
def calc_ema(closes: list, period: int) -> list:
    result = [None] * len(closes)
    if len(closes) < period:
        return result
    k = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    result[period - 1] = ema
    for i in range(period, len(closes)):
        ema = closes[i] * k + ema * (1 - k)
        result[i] = ema
    return result


def build_cases_macro_tf(symbol: str, bars: list, tf: str) -> list:
    """
    1w/1M 专用：用趋势结构切换构建案例
    触发条件：EMA20穿越EMA50（金叉/死叉）
    意义：捕获牛熊周期切换，这才是长周期最有价值的历史经验
    """
    cfg         = TF_CONFIG[tf]
    future_bars = cfg['future_bars']
    closes = [b['c'] for b in bars]
    rsis   = calc_rsi(closes)
    atrs   = calc_atr(bars)
    n      = len(bars)

    ema20 = calc_ema(closes, 20)
    ema50 = calc_ema(closes, 50)

    cases = []
    i = 51
    while i < n - future_bars - 1:
        e20_prev = ema20[i-1]
        e50_prev = ema50[i-1]
        e20_cur  = ema20[i]
        e50_cur  = ema50[i]
        if None in (e20_prev, e50_prev, e20_cur, e50_cur):
            i += 1
            continue

        golden_cross = (e20_prev < e50_prev) and (e20_cur >= e50_cur)
        death_cross  = (e20_prev > e50_prev) and (e20_cur <= e50_cur)

        if not (golden_cross or death_cross):
            i += 1
            continue

        direction = 'UP' if golden_cross else 'DOWN'
        entry_p   = bars[i]['c']
        future_end = min(i + future_bars, n - 1)
        future_ret = (bars[future_end]['c'] - entry_p) / entry_p * 100
        atr_val    = atrs[i] or 0

        cases.append({
            'symbol':            symbol.replace('USDT', ''),
            'timeframe':         tf,
            'ts_squeeze_start':  datetime.fromtimestamp(bars[i]['ts']/1000, tz=timezone.utc).isoformat(),
            'ts_burst':          datetime.fromtimestamp(bars[i]['ts']/1000, tz=timezone.utc).isoformat(),
            'direction':         direction,
            'min_bb_width':      0.0,
            'squeeze_bars':      1,
            'rsi_at_burst':      rsis[i] or 50.0,
            'vol_ratio_peak':    1.0,
            'future_return':     round(future_ret, 4),
            'is_genuine_breakout': True,
            'atr_at_burst':      round(atr_val, 6),
            'trigger':           'golden_cross' if golden_cross else 'death_cross',
        })
        i += 1

    return cases


# ── 方仓案例扫描 ─────────────────────────────────────────────────────
def build_cases_for_tf(symbol: str, bars: list, tf: str) -> list:
    # 长周期用趋势切换逻辑
    if tf in ('1w', '1M'):
        return build_cases_macro_tf(symbol, bars, tf)
    cfg        = TF_CONFIG[tf]
    min_bars   = cfg['min_bars']
    future_bars = cfg['future_bars']
    bbw_pct    = cfg['bbw_pct']   # 用P{bbw_pct}分位动态阈值

    closes = [b['c'] for b in bars]
    bbws   = calc_bbw(closes)
    rsis   = calc_rsi(closes)
    atrs   = calc_atr(bars)
    n      = len(bars)

    # 动态BBW阈值（P{bbw_pct}分位）
    valid_bbws = sorted(b for b in bbws if b is not None)
    if not valid_bbws:
        return []
    p_idx     = max(0, int(len(valid_bbws) * bbw_pct / 100))
    bbw_thresh = valid_bbws[p_idx]
    # 周期越长BB自然越宽，上限放宽：1w/1M用0.35，其他用0.12
    bbw_upper = 0.35 if tf in ('1w', '1M') else 0.12
    bbw_thresh = max(min(bbw_thresh, bbw_upper), 0.003)

    cases = []
    i = 20
    while i < n - future_bars - 1:
        if bbws[i] is None or bbws[i] >= bbw_thresh:
            i += 1
            continue

        # 找压缩起止
        sq_start = i
        while sq_start > 20 and bbws[sq_start-1] is not None and bbws[sq_start-1] < bbw_thresh:
            sq_start -= 1
        sq_end = i
        while sq_end < n-1 and bbws[sq_end+1] is not None and bbws[sq_end+1] < bbw_thresh:
            sq_end += 1

        if sq_end - sq_start + 1 < min_bars:
            i = sq_end + 1
            continue

        min_bbw   = min(b for b in bbws[sq_start:sq_end+1] if b is not None)
        burst_idx = sq_end + 1
        if burst_idx >= n:
            i = sq_end + 1
            continue

        entry_p   = bars[sq_end]['c']
        burst_p   = bars[burst_idx]['c']
        direction = 'UP' if burst_p > entry_p else 'DOWN'

        # 未来N根收益
        future_end = min(burst_idx + future_bars, n - 1)
        future_ret = (bars[future_end]['c'] - entry_p) / entry_p * 100

        # ATR真实突破判断
        atr_val   = atrs[sq_end] or 0
        is_genuine = abs(burst_p - entry_p) > atr_val * 0.8 if atr_val > 0 else False

        # 量比
        burst_vol = bars[burst_idx]['v']
        avg_vol   = sum(bars[j]['v'] for j in range(max(0, sq_start-10), sq_start)) / max(10, 1)
        vol_ratio = round(burst_vol / avg_vol, 2) if avg_vol > 0 else 1.0

        ts_sq  = bars[sq_start]['ts']
        ts_bst = bars[burst_idx]['ts']

        cases.append({
            'symbol':            symbol.replace('USDT',''),
            'timeframe':         tf,
            'ts_squeeze_start':  datetime.fromtimestamp(ts_sq/1000, tz=timezone.utc).isoformat(),
            'ts_burst':          datetime.fromtimestamp(ts_bst/1000, tz=timezone.utc).isoformat(),
            'direction':         direction,
            'min_bb_width':      round(min_bbw * 100, 4),
            'squeeze_bars':      sq_end - sq_start + 1,
            'rsi_at_burst':      rsis[sq_end] or 50.0,
            'vol_ratio_peak':    vol_ratio,
            'future_return':     round(future_ret, 4),
            'is_genuine_breakout': is_genuine,
            'atr_at_burst':      round(atr_val, 6),
        })
        i = sq_end + 1

    return cases


# ── 主构建逻辑 ───────────────────────────────────────────────────────
def build_symbol_tf(symbol: str, tf: str, force: bool = False) -> int:
    """构建单个币种单个周期的方仓案例库"""
    sym_key  = symbol.lower().replace('usdt', '')
    out_path = _DATA / f'fangcang_{sym_key}_{tf}.json'

    # 已存在且不强制重建则跳过（增量模式）
    if out_path.exists() and not force:
        existing = json.loads(out_path.read_text())
        n_exist  = len(existing) if isinstance(existing, list) else 0
        if n_exist > 10:
            return n_exist   # 已有数据，跳过

    cfg     = TF_CONFIG[tf]
    days    = cfg['bars_back']
    bars    = fetch_klines(symbol, tf, days)

    if len(bars) < 50:
        print(f'    [{symbol}/{tf}] 数据不足({len(bars)}根)，跳过')
        return 0

    cases   = build_cases_for_tf(symbol, bars, tf)
    _DATA.mkdir(exist_ok=True)
    out_path.write_text(json.dumps(cases, ensure_ascii=False))

    yrs = round((bars[-1]['ts'] - bars[0]['ts']) / 86400000 / 365, 1)
    up  = sum(1 for c in cases if c['direction'] == 'UP')
    dn  = sum(1 for c in cases if c['direction'] == 'DOWN')
    print(f'    [{symbol}/{tf}] {len(cases)}案例 {yrs}年 UP={up} DOWN={dn}')
    return len(cases)


def build_all(tfs=None, symbols=None, force=False):
    """批量构建全部标的 × 全部周期"""
    if tfs is None:
        tfs = ['15m', '1h', '1d', '1w', '1M']   # 4H已有，默认跳过
    if symbols is None:
        symbols = CRYPTO_SYMBOLS

    summary = {}
    total   = 0

    for sym in symbols:
        sym_total = 0
        print(f'\n[{sym}]')
        for tf in tfs:
            n = build_symbol_tf(sym, tf, force=force)
            sym_total += n
            total     += n
            summary.setdefault(tf, 0)
            summary[tf] += n
            time.sleep(0.2)
        print(f'  → {sym} 各周期合计: {sym_total}案例')

    print(f'\n=== 构建完成 ===')
    print(f'总案例: {total}')
    for tf, n in sorted(summary.items()):
        print(f'  {tf}: {n}')
    return summary


# ── CLI ──────────────────────────────────────────────────────────────
if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--symbol', default=None, help='单个币种')
    parser.add_argument('--tf',     default=None, help='单个周期 15m/1h/1d/1w/1M')
    parser.add_argument('--force',  action='store_true', help='强制重建')
    parser.add_argument('--tfs',    default='15m,1h,1d,1w,1M', help='多周期逗号分隔')
    args = parser.parse_args()

    if args.symbol and args.tf:
        sym = args.symbol.upper()
        if not sym.endswith('USDT'):
            sym += 'USDT'
        n = build_symbol_tf(sym, args.tf, force=args.force)
        print(f'完成: {n}案例')
    else:
        tfs  = args.tfs.split(',')
        syms = [args.symbol.upper() + ('USDT' if not args.symbol.upper().endswith('USDT') else '')] \
               if args.symbol else None
        build_all(tfs=tfs, symbols=syms, force=args.force)
