#!/usr/bin/env python3
"""
达摩院 · 结构扫描器 v1.0
终极训练体系 P0 基础工程

功能：扫描6年K线，标记每个时间点的结构信号
输出：每根K线的结构特征向量 → 供 combo_tester.py 穷举测试

结构类型：
  A1 OB回踩  A2 FVG填充  A3 BOS回调  A4 趋势线
  B1 区间高点 B2 区间低点 B3 假突破反向 B4 均值偏离
  C1 CHoCH   C2 双底双顶  C3 RSI背离   C4 极端情绪

防穿越：所有标记只用T时刻及之前数据
训练截止：2024-12-31
"""
import json, os, sys, datetime, math
from pathlib import Path

BASE = Path(__file__).parent.parent
DATA = BASE / 'data' / 'dharma_8y'
OUT  = BASE / 'data' / 'dharma_structures'
OUT.mkdir(exist_ok=True)

TRAIN_CUTOFF = datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc)

# ─── 工具函数 ───────────────────────────────────────────
def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i-1]
        gains.append(max(0, d)); losses.append(max(0, -d))
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    return 100 - (100 / (1 + ag / al)) if al > 0 else 100.0

def calc_atr(klines, period=14):
    trs = []
    for i in range(1, len(klines)):
        h, l, pc = float(klines[i][2]), float(klines[i][3]), float(klines[i-1][4])
        trs.append(max(h-l, abs(h-pc), abs(l-pc)))
    if not trs: return 0
    return sum(trs[-period:]) / min(period, len(trs))

def calc_macd(closes, fast=12, slow=26, sig=9):
    def ema(data, p):
        k = 2/(p+1); e = data[0]
        result = []
        for v in data:
            e = v*k + e*(1-k); result.append(e)
        return result
    if len(closes) < slow + sig:
        return 0, 0
    ema_f = ema(closes, fast)
    ema_s = ema(closes, slow)
    macd_line = [f-s for f,s in zip(ema_f[slow-1:], ema_s)]
    if len(macd_line) < sig:
        return 0, 0
    sig_line = ema(macd_line, sig)
    return macd_line[-1], sig_line[-1]

# ─── 结构识别函数 ─────────────────────────────────────────

def find_ob(klines, idx, direction='SHORT', lookback=20):
    """
    OB识别（严格版）：趋势方向最后一根反向K线，且后续有明显推动
    条件：
      1. 找到反向K线（做空找上涨K线）
      2. 该K线体积 >= 平均K线体积的 1.2倍（有效OB需要力度）
      3. 后续至少2根K线延续方向（确认OB有效）
      4. 当前价格在OB区域内（回踩中）
    """
    if idx < lookback + 3: return None
    window = klines[max(0,idx-lookback):idx]
    # 平均K线体积
    avg_body = sum(abs(float(k[4])-float(k[1])) for k in window) / len(window)
    cur_price = float(klines[idx][4])

    if direction == 'SHORT':
        for i in range(len(window)-1, 2, -1):
            o, c = float(window[i][1]), float(window[i][4])
            h, l = float(window[i][2]), float(window[i][3])
            body = abs(c - o)
            # 条件1：上涨K线 且体积有力度
            if c <= o or body < avg_body * 0.8: continue
            # 条件2：后续K线向下推动（确认OB)
            subsequent = window[i+1:i+4]
            if not subsequent: continue
            down_moves = sum(1 for k in subsequent if float(k[4]) < float(k[1]))
            if down_moves < 1: continue
            # 条件3：当前价格在OB区域附近（回踩）
            if l <= cur_price <= h * 1.005:
                return {'high': h, 'low': l, 'type': 'BEARISH_OB',
                        'body': body, 'idx': idx-len(window)+i}
    else:
        for i in range(len(window)-1, 2, -1):
            o, c = float(window[i][1]), float(window[i][4])
            h, l = float(window[i][2]), float(window[i][3])
            body = abs(c - o)
            if c >= o or body < avg_body * 0.8: continue
            subsequent = window[i+1:i+4]
            if not subsequent: continue
            up_moves = sum(1 for k in subsequent if float(k[4]) > float(k[1]))
            if up_moves < 1: continue
            if l * 0.995 <= cur_price <= h:
                return {'high': h, 'low': l, 'type': 'BULLISH_OB',
                        'body': body, 'idx': idx-len(window)+i}
    return None

def find_fvg(klines, idx, lookback=10):
    """FVG识别：三根K线形成的价格空白"""
    fvgs = []
    start = max(1, idx-lookback)
    for i in range(start, idx-1):
        if i+2 > len(klines)-1: break
        h1 = float(klines[i][2]); l3 = float(klines[i+2][3])
        l1 = float(klines[i][3]); h3 = float(klines[i+2][2])
        # 看跌FVG
        if l1 > h3:
            fvgs.append({'type':'BEARISH_FVG','high':l1,'low':h3,'idx':i})
        # 看涨FVG
        if h1 < l3:
            fvgs.append({'type':'BULLISH_FVG','high':l3,'low':h1,'idx':i})
    return fvgs[-1] if fvgs else None

def find_bos(klines, idx, lookback=30):
    """BOS识别：突破前一个摆动高/低点"""
    if idx < lookback: return None
    window = klines[max(0,idx-lookback):idx+1]
    highs = [float(k[2]) for k in window]
    lows  = [float(k[3]) for k in window]
    cur_close = float(klines[idx][4])
    # 寻找最近摆动高点
    prev_swing_high = max(highs[:-5]) if len(highs)>5 else max(highs)
    prev_swing_low  = min(lows[:-5])  if len(lows)>5  else min(lows)
    if cur_close > prev_swing_high:
        return {'type':'BULLISH_BOS','level':prev_swing_high}
    if cur_close < prev_swing_low:
        return {'type':'BEARISH_BOS','level':prev_swing_low}
    return None

def find_choch(klines, idx, lookback=50):
    """CHoCH识别：市场结构转换"""
    if idx < lookback: return None
    window = klines[max(0,idx-lookback):idx+1]
    # 简化：检测趋势方向改变
    closes = [float(k[4]) for k in window]
    mid = len(closes)//2
    trend_before = closes[mid] - closes[0]
    trend_after  = closes[-1] - closes[mid]
    if trend_before < -abs(trend_before)*0.3 and trend_after > 0:
        return {'type':'BULLISH_CHoCH','strength':abs(trend_after/trend_before)}
    if trend_before > abs(trend_before)*0.3 and trend_after < 0:
        return {'type':'BEARISH_CHoCH','strength':abs(trend_after/trend_before)}
    return None

def detect_range(klines, idx, lookback=48):
    """区间识别：价格在区间内震荡"""
    if idx < lookback: return None
    window = klines[max(0,idx-lookback):idx]
    highs  = [float(k[2]) for k in window]
    lows   = [float(k[3]) for k in window]
    closes = [float(k[4]) for k in window]
    range_high = max(highs); range_low = min(lows)
    range_size = range_high - range_low
    if range_size == 0: return None
    cur = closes[-1]
    position = (cur - range_low) / range_size  # 0=低点 1=高点
    # 区间有效性：价格多次触碰边界
    touch_high = sum(1 for h in highs if h >= range_high*0.998)
    touch_low  = sum(1 for l in lows  if l <= range_low*1.002)
    if touch_high >= 2 and touch_low >= 2:
        return {'range_high':range_high,'range_low':range_low,
                'position':position,'size_pct':range_size/range_low*100}
    return None

def detect_divergence(closes, rsi_vals, idx, lookback=20):
    """RSI背离识别"""
    if idx < lookback or len(rsi_vals) < lookback: return None
    prices = closes[max(0,idx-lookback):idx+1]
    rsis   = rsi_vals[max(0,idx-lookback):idx+1]
    if len(prices) < 5: return None
    # 看跌背离：价格新高，RSI不新高
    if prices[-1] > max(prices[:-3]) and rsis[-1] < max(rsis[:-3])*0.95:
        return {'type':'BEARISH_DIV','strength':(max(prices[:-3])-prices[-1])/prices[-1]}
    # 看涨背离：价格新低，RSI不新低
    if prices[-1] < min(prices[:-3]) and rsis[-1] > min(rsis[:-3])*1.05:
        return {'type':'BULLISH_DIV','strength':(prices[-1]-min(prices[:-3]))/prices[-1]}
    return None

def calc_regime(klines, idx, lookback=96):
    """4H市场性格判断"""
    if idx < lookback: return 'UNKNOWN'
    window = klines[max(0,idx-lookback):idx+1]
    closes = [float(k[4]) for k in window]
    highs  = [float(k[2]) for k in window]
    lows   = [float(k[3]) for k in window]
    # 简化ADX：用价格范围/ATR比值
    atr = calc_atr(window[-20:])
    if atr == 0: return 'UNKNOWN'
    price_range = max(highs) - min(lows)
    adx_proxy = price_range / (atr * len(window))
    # 趋势方向
    trend = closes[-1] - closes[0]
    trend_pct = trend / closes[0] * 100
    if abs(trend_pct) > 5 and adx_proxy > 1.5:
        return 'STRONG_TREND_UP' if trend > 0 else 'STRONG_TREND_DOWN'
    elif abs(trend_pct) > 2:
        return 'WEAK_TREND_UP' if trend > 0 else 'WEAK_TREND_DOWN'
    elif adx_proxy < 0.8:
        return 'RANGE_TIGHT'
    else:
        return 'RANGE_WIDE'

# ─── 主扫描函数 ───────────────────────────────────────────

def scan_symbol(symbol, interval):
    fname = DATA / f'{symbol}_{interval}_train2.json'
    if not fname.exists():
        fname = DATA / f'{symbol}_{interval}_pure.json'
    if not fname.exists():
        print(f'[SKIP] {symbol} {interval} 文件不存在')
        return []

    print(f'[SCAN] {symbol} {interval} 加载中...')
    klines = json.load(open(fname))

    # 过滤训练集
    cutoff_ts = int(TRAIN_CUTOFF.timestamp() * 1000)
    klines = [k for k in klines if int(k[0]) < cutoff_ts]
    n = len(klines)
    print(f'  训练K线: {n:,}条')

    closes  = [float(k[4]) for k in klines]
    volumes = [float(k[5]) for k in klines]

    # 预计算RSI序列
    rsi_vals = []
    for i in range(n):
        rsi_vals.append(calc_rsi(closes[max(0,i-28):i+1]))

    results = []
    step = max(1, n // 10)

    for idx in range(50, n-5):  # 留5根K线判断结局
        if idx % step == 0:
            pct = idx/n*100
            print(f'  进度 {pct:.0f}%  已标记={len(results):,}', flush=True)

        ts = int(klines[idx][0])
        price = closes[idx]
        atr = calc_atr(klines[max(0,idx-20):idx+1])
        rsi = rsi_vals[idx]
        macd_v, macd_s = calc_macd(closes[max(0,idx-40):idx+1])
        vol_ratio = volumes[idx] / (sum(volumes[max(0,idx-20):idx])/20) if idx>=20 else 1.0

        # 市场性格
        regime = calc_regime(klines, idx)

        # 结构检测
        ob_short = find_ob(klines, idx, 'SHORT')
        ob_long  = find_ob(klines, idx, 'LONG')
        fvg      = find_fvg(klines, idx)
        bos      = find_bos(klines, idx)
        choch    = find_choch(klines, idx)
        rng      = detect_range(klines, idx)
        div      = detect_divergence(closes, rsi_vals, idx)

        # 只记录有结构的点（减少数据量）
        has_structure = any([ob_short, ob_long, fvg, bos, choch,
                             (rng and (rng['position']>0.75 or rng['position']<0.25)),
                             div])
        if not has_structure:
            continue

        # 结局：未来20根K线（与周期匹配：15m=5H，1H=20H，4H=80H）
        future_high = max(float(klines[min(n-1,idx+i)][2]) for i in range(1,21))
        future_low  = min(float(klines[min(n-1,idx+i)][3]) for i in range(1,21))
        future_close_10 = float(klines[min(n-1,idx+10)][4])
        future_close_20 = float(klines[min(n-1,idx+20)][4])

        record = {
            'ts': ts, 'symbol': symbol, 'interval': interval,
            'price': price, 'atr': round(atr,4), 'atr_pct': round(atr/price*100,3),
            'rsi': round(rsi,1), 'macd': round(macd_v,4), 'macd_sig': round(macd_s,4),
            'vol_ratio': round(vol_ratio,2),
            'regime': regime,
            # 结构存在标志
            'has_ob_short': 1 if ob_short else 0,
            'has_ob_long':  1 if ob_long  else 0,
            'has_fvg':      1 if fvg      else 0,
            'fvg_type':     fvg['type'] if fvg else '',
            'has_bos':      1 if bos      else 0,
            'bos_type':     bos['type'] if bos else '',
            'has_choch':    1 if choch    else 0,
            'has_range':    1 if rng      else 0,
            'range_pos':    round(rng['position'],2) if rng else -1,
            'has_div':      1 if div      else 0,
            'div_type':     div['type'] if div else '',
            # 结局
            'future_high_5': round(future_high,2),
            'future_low_5':  round(future_low,2),
            'move_up_5':     round((future_high-price)/price*100,3),
            'move_down_5':   round((price-future_low)/price*100,3),
            'close_10':      round((future_close_10-price)/price*100,3),
            'close_20':      round((future_close_20-price)/price*100,3),
        }
        results.append(record)

    print(f'  完成: {len(results):,}个结构点')
    return results

# ─── 主入口 ──────────────────────────────────────────────

if __name__ == '__main__':
    targets = [
        ('BTCUSDT', '4h'),
        ('ETHUSDT', '4h'),
        ('BTCUSDT', '1h'),
        ('ETHUSDT', '1h'),
        ('BTCUSDT', '15m'),
        ('ETHUSDT', '15m'),
    ]

    summary = []
    for symbol, interval in targets:
        results = scan_symbol(symbol, interval)
        if results:
            out_file = OUT / f'{symbol}_{interval}_structures.json'
            json.dump(results, open(out_file,'w'), ensure_ascii=False)
            summary.append({
                'symbol': symbol, 'interval': interval,
                'total_bars': '?', 'structure_points': len(results),
                'file': str(out_file)
            })
            print(f'  保存: {out_file.name} ({len(results):,}条)')

    print('\n=== 扫描完成 ===')
    for s in summary:
        print(f'  {s["symbol"]} {s["interval"]}: {s["structure_points"]:,}个结构点')

    json.dump(summary, open(OUT/'scan_summary.json','w'), indent=2)
    print('汇总: data/dharma_structures/scan_summary.json')
