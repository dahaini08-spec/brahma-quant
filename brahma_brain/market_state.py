"""
market_state.py · 市场状态引擎
brahma_brain · Phase 1

功能：
  - 多时间框架趋势判断（EMA排列 + ADX）
  - 12种市场体制识别
  - 关键位自动计算（Fib + Pivot + BB + EMA）
  - Elliott浪型快速定位
  - 输出结构化市场状态报告
"""
import math
from data_cache import get_klines, get_ticker, get_funding_rate, \
                       get_open_interest, get_long_short_ratio, klines_to_ohlcv

# ═══════════════════════════════════════════════════════════════
# 一、基础指标计算
# ═══════════════════════════════════════════════════════════════

def ema(closes: list, n: int) -> float:
    if len(closes) < n:
        return closes[-1] if closes else 0.0
    k = 2 / (n + 1)
    e = sum(closes[:n]) / n
    for c in closes[n:]:
        e = c * k + e * (1 - k)
    return round(e, 8)

def ema_series(closes: list, n: int) -> list:
    """返回完整EMA序列"""
    if len(closes) < n:
        return [closes[i] for i in range(len(closes))]
    k = 2 / (n + 1)
    e = sum(closes[:n]) / n
    result = [None] * n + [e]
    for c in closes[n:]:
        e = c * k + e * (1 - k)
        result.append(e)
    return result

def rsi(closes: list, n: int = 14) -> float:
    if len(closes) < n + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i-1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag = sum(gains[:n]) / n
    al = sum(losses[:n]) / n
    for i in range(n, len(gains)):
        ag = (ag * (n-1) + gains[i]) / n
        al = (al * (n-1) + losses[i]) / n
    return round(100 - 100 / (1 + ag / al), 2) if al else 100.0

def atr(highs: list, lows: list, closes: list, n: int = 14) -> float:
    trs = []
    for i in range(1, len(closes)):
        trs.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i]  - closes[i-1])
        ))
    if not trs:
        return 0.0
    a = sum(trs[:n]) / n
    for t in trs[n:]:
        a = (a * (n-1) + t) / n
    return round(a, 8)

def bb(closes: list, n: int = 20) -> dict:
    if len(closes) < n:
        return {'mid': closes[-1], 'upper': closes[-1], 'lower': closes[-1], 'width': 0}
    w   = closes[-n:]
    mid = sum(w) / n
    std = math.sqrt(sum((x - mid)**2 for x in w) / n)
    upper = mid + 2 * std
    lower = mid - 2 * std
    width = (upper - lower) / mid if mid else 0
    return {
        'mid':   round(mid, 8),
        'upper': round(upper, 8),
        'lower': round(lower, 8),
        'width': round(width, 4),
        'pos':   round((closes[-1] - lower) / (upper - lower), 3) if upper != lower else 0.5
    }

def adx(highs: list, lows: list, closes: list, n: int = 14) -> float:
    """简化ADX计算"""
    if len(closes) < n * 2:
        return 20.0
    pdi_list, mdi_list, tr_list = [], [], []
    for i in range(1, len(closes)):
        tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
        pdi_list.append(max(highs[i]-highs[i-1], 0) if highs[i]-highs[i-1] > lows[i-1]-lows[i] else 0)
        mdi_list.append(max(lows[i-1]-lows[i], 0) if lows[i-1]-lows[i] > highs[i]-highs[i-1] else 0)
        tr_list.append(tr)
    atr_v = sum(tr_list[:n]) / n
    pdi_v = sum(pdi_list[:n]) / n
    mdi_v = sum(mdi_list[:n]) / n
    dx_list = []
    for i in range(n, len(tr_list)):
        atr_v = (atr_v*(n-1) + tr_list[i]) / n
        pdi_v = (pdi_v*(n-1) + pdi_list[i]) / n
        mdi_v = (mdi_v*(n-1) + mdi_list[i]) / n
        if atr_v > 0:
            pdi_pct = pdi_v / atr_v * 100
            mdi_pct = mdi_v / atr_v * 100
            dx = abs(pdi_pct - mdi_pct) / (pdi_pct + mdi_pct) * 100 if (pdi_pct+mdi_pct) > 0 else 0
            dx_list.append(dx)
    if not dx_list:
        return 20.0
    adx_v = sum(dx_list[:n]) / n
    for d in dx_list[n:]:
        adx_v = (adx_v*(n-1) + d) / n
    return round(adx_v, 2)

# ═══════════════════════════════════════════════════════════════
# 二、趋势判断
# ═══════════════════════════════════════════════════════════════

def trend_direction(closes: list, highs: list, lows: list) -> dict:
    """多维度趋势判断"""
    price = closes[-1]
    e20  = ema(closes, 20)
    e50  = ema(closes, 50)
    e100 = ema(closes, 100) if len(closes) >= 100 else ema(closes, len(closes))
    e200 = ema(closes, 200) if len(closes) >= 200 else ema(closes, len(closes))
    adx_v = adx(highs, lows, closes)

    # EMA排列评分
    bull_score = 0
    if price > e20:  bull_score += 1
    if e20 > e50:    bull_score += 1
    if e50 > e100:   bull_score += 1
    if e100 > e200:  bull_score += 1

    if bull_score >= 4:
        direction = 'BULL'
        strength  = 'STRONG' if adx_v > 30 else 'NORMAL'
    elif bull_score >= 3:
        direction = 'BULL'
        strength  = 'WEAK'
    elif bull_score <= 1:
        direction = 'BEAR'
        strength  = 'STRONG' if adx_v > 30 else 'NORMAL'
    elif bull_score <= 2:
        direction = 'BEAR'
        strength  = 'WEAK'
    else:
        direction = 'CHOP'
        strength  = 'NEUTRAL'

    # ADX修正
    if adx_v < 20:
        direction = 'CHOP'
        strength  = 'LOW_VOL'

    return {
        'direction': direction,   # BULL / BEAR / CHOP | 牛市/熊市/震荡
        'strength':  strength,    # STRONG / NORMAL / WEAK / LOW_VOL | 强/正常/弱/低波
        'bull_score': bull_score,
        'adx':  adx_v,
        'price': price,
        'ema20': round(e20, 4),
        'ema50': round(e50, 4),
        'ema100': round(e100, 4),
        'ema200': round(e200, 4),
    }

def three_frame_consensus(td_1h: dict, td_4h: dict, td_1d: dict) -> dict:
    """三框架共识判断"""
    dirs = [td_1h['direction'], td_4h['direction'], td_1d['direction']]

    bull_count = dirs.count('BULL')
    bear_count = dirs.count('BEAR')
    chop_count = dirs.count('CHOP')

    if bull_count == 3:
        consensus = 'FULL_BULL'
        confidence = 95
    elif bear_count == 3:
        consensus = 'FULL_BEAR'
        confidence = 95
    elif bull_count == 2 and chop_count == 1:
        consensus = 'LEAN_BULL'
        confidence = 70
    elif bear_count == 2 and chop_count == 1:
        consensus = 'LEAN_BEAR'
        confidence = 70
    elif bull_count == 2 and bear_count == 1:
        consensus = 'MIXED_BULL'
        confidence = 50
    elif bear_count == 2 and bull_count == 1:
        consensus = 'MIXED_BEAR'
        confidence = 50
    else:
        consensus = 'NEUTRAL'
        confidence = 30

    return {
        'consensus':  consensus,
        'confidence': confidence,
        '1h_dir':     td_1h['direction'],
        '4h_dir':     td_4h['direction'],
        '1d_dir':     td_1d['direction'],
        'signal_dir': 'LONG' if 'BULL' in consensus else ('SHORT' if 'BEAR' in consensus else 'NEUTRAL'),
    }

# ═══════════════════════════════════════════════════════════════
# 三、12种市场体制识别
# ═══════════════════════════════════════════════════════════════

def detect_regime(closes: list, highs: list, lows: list,
                  td_1h: dict, td_4h: dict, td_1d: dict) -> str:
    """识别12种市场体制"""
    price  = closes[-1]
    rsi_1h = rsi(closes)
    atr_v  = atr(highs, lows, closes)
    atr_pct = atr_v / price * 100 if price else 0

    d1h = td_1h['direction']
    d4h = td_4h['direction']
    d1d = td_1d['direction']

    # 暴跌检测（最近3根K线平均跌幅）
    if len(closes) >= 4:
        recent_chg = (closes[-1] - closes[-4]) / closes[-4] * 100
        if recent_chg < -8 and atr_pct > 3.0:
            return 'BEAR_CRASH'

    # 完全牛市
    if d1d == 'BULL' and d4h == 'BULL' and d1h == 'BULL':
        if td_1d['strength'] == 'STRONG':
            return 'BULL_TREND'
        return 'BULL_EARLY'

    # 牛市末期（量价背离信号）
    if d1d == 'BULL' and d4h == 'BULL' and rsi_1h > 70:
        return 'BULL_PEAK'

    # 牛市回调
    if d1d == 'BULL' and (d4h == 'CHOP' or d4h == 'BEAR'):
        return 'BULL_CORRECTION'

    # 完全熊市
    if d1d == 'BEAR' and d4h == 'BEAR' and d1h == 'BEAR':
        if td_1d['strength'] == 'STRONG':
            return 'BEAR_TREND'
        return 'BEAR_EARLY'

    # 熊市反弹
    if d1d == 'BEAR' and (d4h == 'CHOP' or d4h == 'BULL'):
        return 'BEAR_RECOVERY'

    # 震荡体制细分
    if d1d == 'CHOP' or (d4h == 'CHOP' and d1h == 'CHOP'):
        ema200 = td_1d['ema200']
        if price > ema200 * 1.02:
            return 'CHOP_HIGH'
        elif price < ema200 * 0.98:
            return 'CHOP_LOW'
        return 'CHOP_MID'

    # 突破体制
    if d4h != d1d and td_4h['adx'] > 30:
        return 'BREAKOUT'

    return 'CHOP_MID'

# ═══════════════════════════════════════════════════════════════
# 四、关键位计算
# ═══════════════════════════════════════════════════════════════

def calc_fib_levels(high: float, low: float) -> dict:
    """斐波那契回调/延伸位"""
    diff = high - low
    return {
        'high':  round(high, 8),
        'low':   round(low, 8),
        '0.236': round(high - diff * 0.236, 8),
        '0.382': round(high - diff * 0.382, 8),
        '0.500': round(high - diff * 0.500, 8),
        '0.618': round(high - diff * 0.618, 8),
        '0.786': round(high - diff * 0.786, 8),
        '1.272': round(low  - diff * 0.272, 8),
        '1.618': round(low  - diff * 0.618, 8),
        '2.618': round(low  - diff * 1.618, 8),
    }

def calc_pivot_points(high: float, low: float, close: float) -> dict:
    """标准枢轴点"""
    pp = (high + low + close) / 3
    r1 = 2 * pp - low
    r2 = pp + (high - low)
    r3 = high + 2 * (pp - low)
    s1 = 2 * pp - high
    s2 = pp - (high - low)
    s3 = low - 2 * (high - pp)
    return {
        'pp': round(pp, 8),
        'r1': round(r1, 8), 'r2': round(r2, 8), 'r3': round(r3, 8),
        's1': round(s1, 8), 's2': round(s2, 8), 's3': round(s3, 8),
    }

def find_swing_highs_lows(highs: list, lows: list, lookback: int = 5) -> dict:
    """识别摆动高低点"""
    swing_highs, swing_lows = [], []
    for i in range(lookback, len(highs) - lookback):
        if highs[i] == max(highs[i-lookback:i+lookback+1]):
            swing_highs.append((i, highs[i]))
        if lows[i] == min(lows[i-lookback:i+lookback+1]):
            swing_lows.append((i, lows[i]))
    return {
        'swing_highs': swing_highs[-5:],   # 最近5个摆动高点
        'swing_lows':  swing_lows[-5:],    # 最近5个摆动低点
        'last_high':   swing_highs[-1][1] if swing_highs else highs[-1],
        'last_low':    swing_lows[-1][1]  if swing_lows  else lows[-1],
    }

def find_key_levels(closes: list, highs: list, lows: list,
                    td: dict, n_days: int = 30) -> dict:
    """汇总所有关键价位"""
    price = closes[-1]

    # 近期高低点（用于Fib）
    lookback = min(len(highs), n_days * 24)
    recent_high = max(highs[-lookback:])
    recent_low  = min(lows[-lookback:])

    fib = calc_fib_levels(recent_high, recent_low)

    # 日线枢轴
    pivot = calc_pivot_points(highs[-1], lows[-1], closes[-1])

    # 摆动高低点
    swings = find_swing_highs_lows(highs, lows)

    # BB
    bb_data = bb(closes)

    # 关键阻力/支撑（距当前价由近到远）
    resistance_levels = sorted([
        td['ema20'], td['ema50'], td['ema100'], td['ema200'],
        fib['0.382'], fib['0.236'],
        pivot['r1'], pivot['r2'],
        bb_data['upper'],
        swings['last_high'],
    ])
    support_levels = sorted([
        td['ema20'], td['ema50'], td['ema100'], td['ema200'],
        fib['0.618'], fib['0.786'],
        pivot['s1'], pivot['s2'],
        bb_data['lower'],
        swings['last_low'],
    ], reverse=True)

    resistance = [r for r in resistance_levels if r > price]
    support    = [s for s in support_levels    if s < price]

    return {
        'price':       price,
        'fib':         fib,
        'pivot':       pivot,
        'bb':          bb_data,
        'swings':      swings,
        'resistance':  resistance[:5],   # 最近5个阻力位
        'support':     support[:5],      # 最近5个支撑位
        'range_high':  recent_high,
        'range_low':   recent_low,
    }

# ═══════════════════════════════════════════════════════════════
# 五、Elliott浪型快速定位
# ═══════════════════════════════════════════════════════════════

def detect_wave_position(closes: list, highs: list, lows: list) -> dict:
    """简化Elliott浪型定位"""
    swings = find_swing_highs_lows(highs, lows)
    sh = [s[1] for s in swings['swing_highs']]
    sl = [s[1] for s in swings['swing_lows']]
    price = closes[-1]

    if len(sh) < 2 or len(sl) < 2:
        return {'wave': 'UNKNOWN', 'bias': 'NEUTRAL', 'note': '数据不足'}

    last_sh = sh[-1]; prev_sh = sh[-2]
    last_sl = sl[-1]; prev_sl = sl[-2]

    # 判断上升结构
    if last_sh > prev_sh and last_sl > prev_sl:
        # HH + HL = 上升结构
        if price < last_sl * 1.02:
            return {'wave': '4W_OR_2W', 'bias': 'LONG',
                    'note': '上升结构回调中，关注支撑后做多'}
        elif price > last_sh * 0.98:
            # RSI背离判断5浪末
            rsi_v = rsi(closes)
            if rsi_v > 70:
                return {'wave': '5W_TOP', 'bias': 'SHORT',
                        'note': '可能5浪末端，RSI高位，警惕反转'}
            return {'wave': '3W_OR_5W', 'bias': 'LONG',
                    'note': '上升趋势延续中，强势'}
        return {'wave': 'UPTREND', 'bias': 'LONG', 'note': 'HH+HL上升结构'}

    # 判断下降结构
    if last_sh < prev_sh and last_sl < prev_sl:
        # LH + LL = 下降结构
        rsi_v = rsi(closes)
        if price > last_sh * 0.98:
            return {'wave': 'B_WAVE', 'bias': 'SHORT',
                    'note': 'B浪反弹区域，最佳做空入场'}
        elif rsi_v < 30:
            return {'wave': 'C_WAVE_END', 'bias': 'LONG',
                    'note': 'C浪末端可能，RSI超卖，关注反转'}
        return {'wave': 'DOWNTREND', 'bias': 'SHORT', 'note': 'LH+LL下降结构'}

    # 震荡结构
    return {'wave': 'CORRECTION', 'bias': 'NEUTRAL', 'note': '震荡修正中，等待方向'}

# ═══════════════════════════════════════════════════════════════
# 六、主入口：生成完整市场状态报告
# ═══════════════════════════════════════════════════════════════

def analyze(symbol: str) -> dict:
    """
    主分析入口：生成完整市场状态报告
    返回结构化dict，供共振评分器使用
    """
    # [设计院2026-05-28] 强制实时拉取原则
    # 每次分析必须从币安FAPI拉最新数据，缓存仅作降级备用
    # 并发拉取耗时约200ms，保证所有技术指标基于最新K线
    try:
        import sys as _sys_rt, os as _os_rt
        _rt_dir = _os_rt.path.dirname(_os_rt.path.abspath(__file__))
        if _rt_dir not in _sys_rt.path: _sys_rt.path.insert(0, _rt_dir)
        from realtime_fetch import fetch_realtime as _rt_fetch
        _rt = _rt_fetch(symbol)
        if not _rt.get('_errors') or len(_rt['_errors']) < 3:
            # 注入实时数据到缓存层（强制覆盖，TTL=30s仅作短暂防重复）
            from data_cache import _cache_set, _cache_key
            for _iv in ['15m', '1h', '4h', '1d']:
                if _iv in _rt and _rt[_iv]:
                    _cache_set(_cache_key(symbol, _iv, 250), _rt[_iv], 30)
            if 'ticker' in _rt:
                _cache_set(_cache_key(symbol, 'ticker'), _rt['ticker'], 15)
            if 'fr' in _rt and isinstance(_rt['fr'], list) and _rt['fr']:
                _fr_val = float(_rt['fr'][0].get('fundingRate', 0))
                _cache_set(_cache_key(symbol, 'fr'), _fr_val, 30)
            if 'lsr' in _rt and isinstance(_rt['lsr'], list) and _rt['lsr']:
                _ls_val = float(_rt['lsr'][0].get('longAccount', 0.5)) * 100
                _cache_set(_cache_key(symbol, 'lsr'), _ls_val, 30)
    except Exception as _rt_err:
        pass  # 实时拉取失败 → 降级走缓存

    # 拉取数据（优先命中上面注入的实时缓存）
    k15  = klines_to_ohlcv(get_klines(symbol, '15m', 200))
    k1h  = klines_to_ohlcv(get_klines(symbol, '1h',  200))
    k4h  = klines_to_ohlcv(get_klines(symbol, '4h',  200))
    k1d  = klines_to_ohlcv(get_klines(symbol, '1d',  200))
    ticker = get_ticker(symbol)
    fr   = get_funding_rate(symbol)
    oi   = get_open_interest(symbol)
    lsr  = get_long_short_ratio(symbol)

    if not k1h['c']:
        return {'error': f'无法获取{symbol}数据'}

    # [架构修复 2026-05-29] 单一价格真相源
    # 降级链: WS实时价(30s) > ticker.lastPrice(15s) > k1h[-1]
    try:
        import sys as _s2, os as _o2
        _s2.path.insert(0, _o2.path.dirname(_o2.path.abspath(__file__)))
        from live_price_feed import get_best_price as _gprice
        price, _price_src = _gprice(symbol, ticker=ticker, kline_close=k1h['c'][-1])
        if price <= 0:
            price = k1h['c'][-1]
            _price_src = 'kline_fallback'
    except Exception:
        _ticker_price = float(ticker.get('lastPrice', 0) or ticker.get('price', 0) or 0)
        price = _ticker_price if _ticker_price > 0 else k1h['c'][-1]
        _price_src = 'fallback'
    # 价格来源记录在 ms dict 里供调试用
    _price_source_tag = locals().get('_price_src', 'unknown')

    # 趋势分析（三框架）
    td_1h = trend_direction(k1h['c'], k1h['h'], k1h['l'])
    td_4h = trend_direction(k4h['c'], k4h['h'], k4h['l'])
    td_1d = trend_direction(k1d['c'], k1d['h'], k1d['l'])
    consensus = three_frame_consensus(td_1h, td_4h, td_1d)

    # [P1-1 设计院 2026-06-24] 体制 SSOT 统一修复
    # 根因：detect_regime 用规则树(d1h/d4h/d1d)，regime_scorer 用概率模型(19维)
    #       两套算法对同一标的给出不同结果，导致 brahma_analyze / 仪表板 / 结算器 三套体制并行
    # 修复：以 regime_scorer（概率模型，与达摩院铁证同源）为 SSOT
    #       detect_regime 保留为 regime_raw 供调试，不参与信号计算
    # 流程：regime_scorer → regime_state_machine(稳定) → ms['regime']
    _regime_raw = detect_regime(k1h['c'], k1h['h'], k1h['l'], td_1h, td_4h, td_1d)  # 保留调试
    try:
        from regime_scorer import score as _rs_fn
        _rs_result = _rs_fn(symbol, force=False)   # 30分钟缓存，避免重复调用
        _regime_from_scorer = _rs_result.get('regime', _regime_raw)
    except Exception:
        _regime_from_scorer = _regime_raw  # 降级：scorer 失败时用规则树
    # 经状态机稳定（防止单根K线抖动）
    try:
        from regime_state_machine import get_stable_regime as _gsm
        regime = _gsm(symbol, _regime_from_scorer)
    except Exception:
        regime = _regime_from_scorer
    _regime_raw_for_debug = _regime_raw          # 规则树原始值，调试用
    _regime_probs = _rs_result if '_rs_result' in dir() else {}  # scorer概率，调试用

    # 关键位
    key_levels = find_key_levels(k1h['c'], k1h['h'], k1h['l'], td_1h)

    # Elliott浪型
    wave = detect_wave_position(k4h['c'], k4h['h'], k4h['l'])

    # RSI多框架
    rsi_15m = rsi(k15['c'])
    rsi_1h  = rsi(k1h['c'])
    rsi_4h  = rsi(k4h['c'])
    rsi_1d  = rsi(k1d['c'])

    # ATR
    atr_1h  = atr(k1h['h'], k1h['l'], k1h['c'])
    atr_4h  = atr(k4h['h'], k4h['l'], k4h['c'])   # [v13.0] 4H ATR for SL layer
    atr_pct = atr_1h / price * 100 if price else 0

    # 4H 摆动结构（用于止损 layer2）
    sw4h = find_swing_highs_lows(k4h['h'], k4h['l'], lookback=3)
    swing_4h = {
        'highs': [v for _, v in sw4h['swing_highs']],
        'lows':  [v for _, v in sw4h['swing_lows']],
        'last_high': sw4h['last_high'],
        'last_low':  sw4h['last_low'],
    }

    # BB
    bb_1h = key_levels['bb']

    # 24H数据
    chg24  = float(ticker.get('priceChangePercent', 0))
    vol24  = float(ticker.get('quoteVolume', 0))

    return {
        'symbol':    symbol,
        'price':     price,
        'price_source': locals().get('_price_source_tag','?'),
        'chg24':     chg24,
        'vol24':     vol24,

        # 趋势
        'trend': {
            '1h':  td_1h,
            '4h':  td_4h,
            '1d':  td_1d,
            'consensus': consensus,
        },

        # 体制（SSOT: regime_scorer v25.2）
        'regime':       regime,
        'regime_raw':   _regime_raw,   # 原始未经状态机的体制（调试用）
        'regime_probs': _regime_probs,  # bear/bull/chop_prob + phase + momentum + multiplier

        # 关键位
        'key_levels': key_levels,

        # 浪型
        'wave': wave,

        # 动量
        'momentum': {
            'rsi_15m': rsi_15m,
            'rsi_1h':  rsi_1h,
            'rsi_4h':  rsi_4h,
            'rsi_1d':  rsi_1d,
            'atr_1h':  round(atr_1h, 4),
            'atr_4h':  round(atr_4h, 4),   # [v13.0]
            'atr_pct': round(atr_pct, 3),
            'bb':      bb_1h,
        },

        # 4H摆动结构（止损四层架构依据）
        'swing_4h': swing_4h,   # [v13.0]

        # 市场情绪
        'sentiment': {
            'funding_rate':    round(fr, 4),
            'long_short_ratio': round(lsr, 1),
            'oi':              oi.get('oi', 0),
            'oi_change_pct':   oi.get('oi_change_pct', 0.0),   # [P2 2026-05-22]
            'oi_momentum':     oi.get('oi_momentum', 'NEUTRAL'),
        },

        # 信号偏向（供共振评分器使用）
        'signal_bias': consensus['signal_dir'],

        # 快速判断
        'summary': _build_summary(consensus, regime, wave, rsi_1h, atr_pct),

        # [v13.0 OBV修复] OBV计算用原始K线数据
        'raw_closes':  list(k1h['c'][-20:]),
        'raw_volumes': list(k1h['v'][-20:]) if k1h.get('v') else [],
    }

def _build_summary(consensus: dict, regime: str, wave: dict,
                   rsi_1h: float, atr_pct: float) -> str:
    """一句话总结"""
    dir_cn = {'FULL_BULL':'强多头','LEAN_BULL':'偏多','FULL_BEAR':'强空头',
              'LEAN_BEAR':'偏空','MIXED_BULL':'多空分歧偏多','MIXED_BEAR':'多空分歧偏空',
              'NEUTRAL':'中性'}
    regime_cn = {
        'BULL_TREND':'牛市趋势','BULL_EARLY':'牛市初期','BULL_PEAK':'牛市末期',
        'BULL_CORRECTION':'牛市回调','BEAR_TREND':'熊市趋势','BEAR_EARLY':'熊市初期',
        'BEAR_CRASH':'暴跌体制','BEAR_RECOVERY':'熊市反弹',
        'CHOP_HIGH':'高位震荡','CHOP_LOW':'低位震荡','CHOP_MID':'中位震荡',
        'BREAKOUT':'突破体制'
    }
    d = dir_cn.get(consensus['consensus'], consensus['consensus'])
    r = regime_cn.get(regime, regime)
    w = wave.get('note', '')
    rsi_note = ' RSI超卖' if rsi_1h < 30 else (' RSI超买' if rsi_1h > 70 else '')
    vol_note = ' 低波动蓄力' if atr_pct < 0.4 else (' 高波动' if atr_pct > 2.0 else '')
    return f'{d} | {r} | {w}{rsi_note}{vol_note}'

# ═══════════════════════════════════════════════════════════════
# 快速测试
# ═══════════════════════════════════════════════════════════════
if __name__ == '__main__':
    import json, sys
    sym = sys.argv[1] if len(sys.argv) > 1 else 'ETHUSDT'
    pass  # [静默]
    result = analyze(sym)

    if 'error' in result:
        print(f'错误: {result["error"]}')
    else:
        print(f'\n=== {sym} 市场状态报告 ===')
        print(f'价格:  ${result["price"]:,.4f}  24H:{result["chg24"]:+.2f}%')
        print(f'体制:  {result["regime"]}')
        print(f'共识:  {result["trend"]["consensus"]["consensus"]} '
              f'(置信度{result["trend"]["consensus"]["confidence"]}%)')
        print(f'浪型:  {result["wave"]["wave"]} → {result["wave"]["note"]}')
        print(f'RSI:  15m={result["momentum"]["rsi_15m"]} '
              f'1H={result["momentum"]["rsi_1h"]} '
              f'4H={result["momentum"]["rsi_4h"]} '
              f'日={result["momentum"]["rsi_1d"]}')
        print(f'ATR:  {result["momentum"]["atr_1h"]} ({result["momentum"]["atr_pct"]}%)')
        print(f'资金费: {result["sentiment"]["funding_rate"]:+.4f}%  '
              f'多空:{result["sentiment"]["long_short_ratio"]}%多')
        print(f'\n支撑位: {result["key_levels"]["support"][:3]}')
        print(f'阻力位: {result["key_levels"]["resistance"][:3]}')
        print(f'\nFib 0.618: ${result["key_levels"]["fib"]["0.618"]:,.4f}')
        print(f'Fib 0.382: ${result["key_levels"]["fib"]["0.382"]:,.4f}')
        print(f'\n📝 {result["summary"]}')
        print(f'信号偏向: {result["signal_bias"]}')
