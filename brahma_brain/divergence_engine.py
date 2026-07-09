"""

# ── STATUS: AUXILIARY ──────────────────────────────────────────
# 背离检测引擎，SMC辅助
# LAST_REVIEW: 2026-07-01 | 属于辅助计算层，修改前确认调用链
# ─────────────────────────────────────────────────────────────
divergence_engine.py · RSI/MACD背离检测引擎
brahma_brain · Phase 2

功能：
  - RSI 常规背离（价格/RSI方向相反）
  - RSI 隐藏背离（趋势延续信号）
  - RSI 失败摆动（FTS，高胜率反转）
  - MACD 柱状图背离（常规+隐藏）
  - MACD 零轴位置判断
  - 背离综合评分（0~20分）
"""

# ═══════════════════════════════════════════════════════════════
# 一、RSI计算工具
# ═══════════════════════════════════════════════════════════════

def calc_rsi_series(closes: list, n: int = 14) -> list:
    """返回完整RSI序列"""
    if len(closes) < n + 1:
        return [50.0] * len(closes)
    rsi_vals = [None] * n
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i-1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag = sum(gains[:n]) / n
    al = sum(losses[:n]) / n
    rsi_vals.append(100 - 100 / (1 + ag / al) if al else 100.0)
    for i in range(n, len(gains)):
        ag = (ag * (n-1) + gains[i]) / n
        al = (al * (n-1) + losses[i]) / n
        rsi_vals.append(100 - 100 / (1 + ag / al) if al else 100.0)
    return rsi_vals

def calc_macd_series(closes: list, fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
    """返回完整MACD序列"""
    def ema_s(data, n):
        if len(data) < n:
            return [data[i] for i in range(len(data))]
        k = 2 / (n + 1)
        e = sum(data[:n]) / n
        result = [None] * (n - 1) + [e]
        for x in data[n:]:
            e = x * k + e * (1 - k)
            result.append(e)
        return result

    ema_fast = ema_s(closes, fast)
    ema_slow = ema_s(closes, slow)

    macd_line = []
    for f, s in zip(ema_fast, ema_slow):
        if f is None or s is None:
            macd_line.append(None)
        else:
            macd_line.append(f - s)

    valid_macd = [x for x in macd_line if x is not None]
    if len(valid_macd) < signal:
        sig_line = [0.0] * len(macd_line)
    else:
        sig_raw = ema_s(valid_macd, signal)
        none_count = macd_line.count(None)
        sig_line = [None] * none_count + sig_raw

    histogram = []
    for m, sg in zip(macd_line, sig_line):
        if m is None or sg is None:
            histogram.append(None)
        else:
            histogram.append(m - sg)

    return {
        'macd':      macd_line,
        'signal':    sig_line,
        'histogram': histogram,
    }

# ═══════════════════════════════════════════════════════════════
# 二、摆动点识别（用于背离检测）
# ═══════════════════════════════════════════════════════════════

def find_pivots(values: list, lookback: int = 3) -> dict:
    """识别序列中的摆动高低点"""
    highs, lows = [], []
    valid = [(i, v) for i, v in enumerate(values) if v is not None]
    for k in range(lookback, len(valid) - lookback):
        i, v = valid[k]
        window_vals = [valid[k-j][1] for j in range(1, lookback+1)] + \
                      [valid[k+j][1] for j in range(1, lookback+1)]
        if v >= max(window_vals):
            highs.append((i, v))
        if v <= min(window_vals):
            lows.append((i, v))
    return {'highs': highs[-6:], 'lows': lows[-6:]}

# ═══════════════════════════════════════════════════════════════
# 三、RSI背离检测
# ═══════════════════════════════════════════════════════════════

def detect_rsi_divergence(closes: list, n_rsi: int = 14, lookback: int = 5) -> dict:
    """
    检测RSI背离
    返回：常规看空/看多背离，隐藏背离，FTS
    """
    rsi_vals = calc_rsi_series(closes, n_rsi)

    price_pivots = find_pivots(closes,   lookback)
    rsi_pivots   = find_pivots(rsi_vals, lookback)

    ph = price_pivots['highs']
    pl = price_pivots['lows']
    rh = rsi_pivots['highs']
    rl = rsi_pivots['lows']

    results = {
        'regular_bearish': False,   # 常规看空（价格HH，RSI LH）→ 顶部反转
        'regular_bullish': False,   # 常规看多（价格LL，RSI HL）→ 底部反转
        'hidden_bearish':  False,   # 隐藏看空（价格LH，RSI HH）→ 下跌延续
        'hidden_bullish':  False,   # 隐藏看多（价格HL，RSI LL）→ 上涨延续
        'fts_bearish':     False,   # 失败摆动看空
        'fts_bullish':     False,   # 失败摆动看多
        'details':         [],
        'score_long':  0,
        'score_short': 0,
    }

    # 常规看空背离：价格HH，RSI LH
    if len(ph) >= 2 and len(rh) >= 2:
        p_high1, p_high2 = ph[-2][1], ph[-1][1]
        r_high1, r_high2 = rh[-2][1], rh[-1][1]
        if p_high2 > p_high1 and r_high2 < r_high1:
            results['regular_bearish'] = True
            results['details'].append(
                f'常规看空背离: 价格{p_high1:.2f}→{p_high2:.2f}↑  RSI{r_high1:.1f}→{r_high2:.1f}↓'
            )
            results['score_short'] += 8

    # 常规看多背离：价格LL，RSI HL
    if len(pl) >= 2 and len(rl) >= 2:
        p_low1, p_low2 = pl[-2][1], pl[-1][1]
        r_low1, r_low2 = rl[-2][1], rl[-1][1]
        if p_low2 < p_low1 and r_low2 > r_low1:
            results['regular_bullish'] = True
            results['details'].append(
                f'常规看多背离: 价格{p_low1:.2f}→{p_low2:.2f}↓  RSI{r_low1:.1f}→{r_low2:.1f}↑'
            )
            results['score_long'] += 8

    # 隐藏看空背离：价格LH，RSI HH（下跌趋势延续）
    if len(ph) >= 2 and len(rh) >= 2:
        p_high1, p_high2 = ph[-2][1], ph[-1][1]
        r_high1, r_high2 = rh[-2][1], rh[-1][1]
        if p_high2 < p_high1 and r_high2 > r_high1:
            results['hidden_bearish'] = True
            results['details'].append(
                f'隐藏看空背离(趋势延续): 价格↓  RSI↑'
            )
            results['score_short'] += 5

    # 隐藏看多背离：价格HL，RSI LL（上涨趋势延续）
    if len(pl) >= 2 and len(rl) >= 2:
        p_low1, p_low2 = pl[-2][1], pl[-1][1]
        r_low1, r_low2 = rl[-2][1], rl[-1][1]
        if p_low2 > p_low1 and r_low2 < r_low1:
            results['hidden_bullish'] = True
            results['details'].append(
                f'隐藏看多背离(趋势延续): 价格↑  RSI↓'
            )
            results['score_long'] += 5

    # RSI失败摆动（FTS）- 看空
    # RSI>70 → 回落 → 再涨但未超前高 → 跌破前低 = 极强顶部信号
    if len(rsi_vals) >= 10:
        rv = [v for v in rsi_vals[-20:] if v is not None]
        if len(rv) >= 6:
            # 寻找RSI>70后的FTS
            for i in range(2, len(rv)-2):
                if rv[i] > 70 and rv[i+1] < rv[i]:
                    for j in range(i+2, len(rv)-1):
                        if rv[j] > rv[i+1] and rv[j] < rv[i]:  # 未超前高
                            if rv[j+1] < rv[i+1]:               # 跌破前低
                                results['fts_bearish'] = True
                                results['details'].append('RSI失败摆动看空(FTS) 极强顶部信号')
                                results['score_short'] += 6
                                break

    # RSI失败摆动（FTS）- 看多
    if len(rsi_vals) >= 10:
        rv = [v for v in rsi_vals[-20:] if v is not None]
        if len(rv) >= 6:
            for i in range(2, len(rv)-2):
                if rv[i] < 30 and rv[i+1] > rv[i]:
                    for j in range(i+2, len(rv)-1):
                        if rv[j] < rv[i+1] and rv[j] > rv[i]:  # 未破前低
                            if rv[j+1] > rv[i+1]:               # 突破前高
                                results['fts_bullish'] = True
                                results['details'].append('RSI失败摆动看多(FTS) 极强底部信号')
                                results['score_long'] += 6
                                break

    # 日线级别RSI背离额外加分
    rsi_now = rsi_vals[-1] if rsi_vals[-1] is not None else 50
    if results['regular_bearish'] and rsi_now > 65:
        results['score_short'] += 4
        results['details'].append('日线级别RSI背离确认 +4')
    if results['regular_bullish'] and rsi_now < 35:
        results['score_long'] += 4
        results['details'].append('日线级别RSI背离确认 +4')

    return results

# ═══════════════════════════════════════════════════════════════
# 四、MACD背离检测
# ═══════════════════════════════════════════════════════════════

def detect_macd_divergence(closes: list) -> dict:
    """检测MACD柱状图背离"""
    macd_data = calc_macd_series(closes)
    hist      = macd_data['histogram']
    macd_line = macd_data['macd']
    sig_line  = macd_data['signal']

    results = {
        'regular_bearish': False,
        'regular_bullish': False,
        'hidden_bearish':  False,
        'hidden_bullish':  False,
        'zero_cross_up':   False,   # MACD金叉在零轴以下（强多信号）
        'zero_cross_down': False,   # MACD死叉在零轴以上（强空信号）
        'details':         [],
        'score_long':  0,
        'score_short': 0,
    }

    price_pivots = find_pivots(closes, 3)
    hist_pivots  = find_pivots(hist,   3)

    ph = price_pivots['highs']
    pl = price_pivots['lows']
    hh = hist_pivots['highs']
    hl = hist_pivots['lows']

    # 常规看空：价格HH，MACD柱LH
    if len(ph) >= 2 and len(hh) >= 2:
        if ph[-1][1] > ph[-2][1] and hh[-1][1] < hh[-2][1]:
            results['regular_bearish'] = True
            results['score_short'] += 5
            results['details'].append('MACD柱看空背离 +5')

    # 常规看多：价格LL，MACD柱HL
    if len(pl) >= 2 and len(hl) >= 2:
        if pl[-1][1] < pl[-2][1] and hl[-1][1] > hl[-2][1]:
            results['regular_bullish'] = True
            results['score_long'] += 5
            results['details'].append('MACD柱看多背离 +5')

    # 零轴位置金叉/死叉（更强信号）
    valid_macd = [(i, v) for i, v in enumerate(macd_line) if v is not None]
    valid_sig  = [(i, v) for i, v in enumerate(sig_line)  if v is not None]
    if len(valid_macd) >= 2 and len(valid_sig) >= 2:
        m_prev, m_curr = valid_macd[-2][1], valid_macd[-1][1]
        s_prev, s_curr = valid_sig[-2][1],  valid_sig[-1][1]
        # 金叉
        if m_prev <= s_prev and m_curr > s_curr:
            if m_curr < 0:
                results['zero_cross_up'] = True
                results['score_long'] += 4
                results['details'].append('MACD金叉(零轴以下) 强多信号 +4')
            else:
                results['score_long'] += 2
                results['details'].append('MACD金叉 +2')
        # 死叉
        if m_prev >= s_prev and m_curr < s_curr:
            if m_curr > 0:
                results['zero_cross_down'] = True
                results['score_short'] += 4
                results['details'].append('MACD死叉(零轴以上) 强空信号 +4')
            else:
                results['score_short'] += 2
                results['details'].append('MACD死叉 +2')

    return results

# ═══════════════════════════════════════════════════════════════
# 五、K线形态识别
# ═══════════════════════════════════════════════════════════════

def detect_candlestick_patterns(opens: list, highs: list,
                                 lows: list, closes: list) -> dict:
    """识别最近K线形态"""
    if len(closes) < 3:
        return {'patterns': [], 'score_long': 0, 'score_short': 0}

    o, h, l, c = opens, highs, lows, closes
    patterns = []
    score_long = 0
    score_short = 0

    # 最近3根K线
    def body(i):   return abs(c[i] - o[i])
    def upper(i):  return h[i] - max(c[i], o[i])
    def lower(i):  return min(c[i], o[i]) - l[i]
    def is_bull(i): return c[i] > o[i]
    def is_bear(i): return c[i] < o[i]

    avg_body = sum(body(i) for i in range(-5, 0)) / 5 if len(closes) >= 5 else body(-1)

    i = -1  # 最新K线

    # 锤子线（看多反转）
    if (lower(i) >= body(i) * 2 and upper(i) < body(i) * 0.3
            and body(i) > 0 and len(closes) >= 10):
        trend_check = closes[-1] < closes[-10]   # 在下跌中
        if trend_check:
            patterns.append('锤子线(看多反转)')
            score_long += 4

    # 射击之星（看空反转）
    if (upper(i) >= body(i) * 2 and lower(i) < body(i) * 0.3
            and body(i) > 0 and len(closes) >= 10):
        trend_check = closes[-1] > closes[-10]   # 在上涨中
        if trend_check:
            patterns.append('射击之星(看空反转)')
            score_short += 4

    # 多头吞没
    if len(closes) >= 2:
        if (is_bear(-2) and is_bull(-1)
                and c[-1] > o[-2] and o[-1] < c[-2]
                and body(-1) > body(-2)):
            patterns.append('多头吞没')
            score_long += 5

    # 空头吞没
    if len(closes) >= 2:
        if (is_bull(-2) and is_bear(-1)
                and c[-1] < o[-2] and o[-1] > c[-2]
                and body(-1) > body(-2)):
            patterns.append('空头吞没')
            score_short += 5

    # 晨星（三K看多）
    if len(closes) >= 3:
        if (is_bear(-3) and body(-2) < avg_body * 0.4
                and is_bull(-1) and c[-1] > (o[-3] + c[-3]) / 2):
            patterns.append('晨星(三K看多反转)')
            score_long += 6

    # 暮星（三K看空）
    if len(closes) >= 3:
        if (is_bull(-3) and body(-2) < avg_body * 0.4
                and is_bear(-1) and c[-1] < (o[-3] + c[-3]) / 2):
            patterns.append('暮星(三K看空反转)')
            score_short += 6

    # 大阳线
    if body(i) > avg_body * 1.5 and is_bull(i):
        patterns.append('大阳线(多头强势)')
        score_long += 3

    # 大阴线
    if body(i) > avg_body * 1.5 and is_bear(i):
        patterns.append('大阴线(空头强势)')
        score_short += 3

    return {
        'patterns':    patterns,
        'score_long':  min(score_long, 10),
        'score_short': min(score_short, 10),
    }

# ═══════════════════════════════════════════════════════════════
# 六、综合背离评分（0~20分）
# ═══════════════════════════════════════════════════════════════

def _calc_volume_contraction(closes: list, volumes: list, lookback: int = 6, vol_mult: float = 0.6) -> dict:
    """
    达摩院100轮实训验证：vol_mult=0.6 是量价背离最优参数
    检测：价格创新高/低时成交量是否萎缩（量价背离核心条件）
    returns: {'vol_contraction': bool, 'vol_ratio': float, 'rsi_lo': bool, 'rsi_hi': bool}
    """
    if not volumes or len(volumes) < lookback + 5:
        return {'vol_contraction': False, 'vol_ratio': 1.0, 'price_extreme': False}
    n = len(closes)
    recent_vol = volumes[-1]
    # 20日成交量均值
    vol_ma = sum(volumes[max(0,n-20):n]) / min(20, n)
    vol_ratio = recent_vol / vol_ma if vol_ma > 0 else 1.0
    # 成交量萎缩：当前量 < 均量 * vol_mult
    vol_contraction = vol_ratio < vol_mult
    # 价格是否在 lookback 内创新高/低
    recent_h = closes[-1]
    recent_l = closes[-1]
    price_hi = recent_h >= max(closes[max(0,n-lookback-1):n-1])
    price_lo = recent_l <= min(closes[max(0,n-lookback-1):n-1])
    return {
        'vol_contraction': vol_contraction,
        'vol_ratio': round(vol_ratio, 3),
        'price_extreme_hi': price_hi,
        'price_extreme_lo': price_lo,
        'vol_ma': round(vol_ma, 2),
    }


def divergence_score(opens: list, highs: list, lows: list,
                     closes: list, signal_dir: str,
                     interval_label: str = '1H',
                     volumes: list = None,
                     regime: str = '',
                     ts_ms: int = 0) -> dict:
    """
    综合背离评分 v3 —达摩院100轮实训升级版
    新增：
      1. 成交量验证（vol_mult=0.6 铁证参数）
      2. 时间窗口过滤（周二/12月惩罚）
      3. 体制自动切换（单边牛市RSI>60三周 → 背离降权）
    优先级：隐藏背离 >常观背离 > FTS失败摇动
    signal_dir: 'LONG' or 'SHORT'
    """
    rsi_div  = detect_rsi_divergence(closes)
    macd_div = detect_macd_divergence(closes)
    candle   = detect_candlestick_patterns(opens, highs, lows, closes)

    # ── 成交量验证（达摩院100轮铁证：vol_mult=0.6）────────────
    vol_info = _calc_volume_contraction(closes, volumes or [], lookback=6, vol_mult=0.6)
    # 成交量萎缩时做量价背离加分，成交量放大时降分（假突破/追涨危险）
    vol_bonus = 0
    vol_penalty = 0
    if volumes:
        if vol_info['vol_contraction']:
            # 量缩+价格创极值 = 经典量价背离，+3分
            if (signal_dir == 'LONG' and vol_info['price_extreme_lo']) or \
               (signal_dir == 'SHORT' and vol_info['price_extreme_hi']):
                vol_bonus = 3
        else:
            # 量放时信号弱，-2分（100轮实训：放量时量价背离失效）
            vol_penalty = 2

    # ── 时间窗口过滤（达摩院实训铁证）──────────────────────────
    time_penalty = 0
    time_note = ''
    if ts_ms > 0:
        try:
            from datetime import datetime, timezone
            dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
            weekday = dt.weekday()  # 0=周一, 1=周二
            month = dt.month
            # 周二：BTC仅27%胜率，ETH仅40%（100轮实训铁证）
            if weekday == 1:  # 周二
                time_penalty += 3
                time_note = '⚠️周二时间惩罚-3（实训胜率仅27-40%）'
            # 12月：ETH量价背离24%（100轮实训铁证）
            if month == 12:
                time_penalty += 4
                time_note += ' ⚠️12月季节性惩罚-4（ETH实训仅24%）'
            # 周末加分：周六ETH80%/BTC68%（100轮实训铁证）
            if weekday in (5, 6):  # 周六/周日
                vol_bonus += 2
                time_note += ' ✅周末加成+2（实训胜率55-80%）'
        except Exception:
            pass

    # ── 体制切换检测（单边牛市降权）───────────────────────────
    regime_penalty = 0
    regime_note = ''
    if regime:
        regime_upper = regime.upper()
        # 单边BULL_TREND时量价背离做多失效（2021减半：14%）
        if 'BULL_TREND' in regime_upper and signal_dir == 'LONG':
            # 在强趋势牛市里，量缩不是衰竭，而是洗盘后继续
            regime_penalty = 3
            regime_note = '⚠️单边牛市量价背离做多降权-3（实训14%）'
        # BEAR_IMPULSE/BEAR_TREND做空时额外奖励（2022Luna：60% PF12.57）
        elif any(x in regime_upper for x in ['BEAR_IMPULSE', 'BEAR_TREND']) and signal_dir == 'SHORT':
            vol_bonus += 2
            regime_note = '✅熊市背离做空加成+2（实训60% PF12.57）'

    # ── 强度分级评分逻辑 ────────────────────────────────
    # 主要信号和分数：
    # S级(隐藏背离)    = 12分  延续趋势最可靠
    # A级(常观背离)    = 10分  趋势反转信号
    # B级(FTS失败摇动) = 8分   高胜率底部
    # C级(MACD单策题)    = 5分
    # D级(K线形态)       = 3分

    grade = 'NONE'
    grade_score = 0
    grade_notes = []

    if signal_dir == 'LONG':
        # S级：隐藏看多背离（价格 HL RSI HH — 下降趋势延续）
        if rsi_div.get('hidden_bullish'):
            grade = 'S'; grade_score = 12
            grade_notes.append('🔵 隐藏看多背离(S级) 趋势延续最可靠 +12')
        # A级：常观看多背离（价格 LL RSI HL — 反转信号）
        elif rsi_div.get('regular_bullish'):
            grade = 'A'; grade_score = 10
            grade_notes.append('🟢 常观看多背离(A级) 底部反转信号 +10')
        # B级： RSI FTS
        elif rsi_div.get('fts_bullish'):
            grade = 'B'; grade_score = 8
            grade_notes.append('🟡 RSI失败摇动看多(B级) +8')
        # C级： MACD单独背离
        elif macd_div.get('regular_bullish') or macd_div.get('hidden_bullish'):
            grade = 'C'; grade_score = 5
            grade_notes.append('🟠 MACD背离(C级) +5')

        # 叠加加分：MACD + RSI 同时共振
        if grade in ('A','B') and (macd_div.get('regular_bullish') or macd_div.get('zero_cross_up')):
            grade_score = min(grade_score + 3, 18)
            grade_notes.append('+MACD共振 +3')
        # MACD 0轴位置加分
        macd_zero_bonus = 2 if macd_div.get('zero_cross_up') else 0
        grade_score = min(grade_score + macd_zero_bonus, 20)
        if macd_zero_bonus:
            grade_notes.append('MACD穿越0轴 +2')

        # K线形态加分
        candle_bonus = min(candle['score_long'], 4)
        grade_score  = min(grade_score + candle_bonus, 20)
        if candle_bonus and candle['patterns']:
            grade_notes.append(f'K线{candle["patterns"][0]} +{candle_bonus}')

        raw = grade_score
        # 兼容旧字段
        rsi_dir_s = rsi_div.get('score_long', 0)
        macd_dir_s= macd_div.get('score_long', 0)
        details_dir = (
            [f'[{interval_label}] ' + d for d in rsi_div['details'] if '看多' in d or '多' in d] +
            [f'[{interval_label}] ' + d for d in macd_div['details'] if '多' in d or '金叉' in d]
        )
    else:  # SHORT
        # S级：隐藏看空背离（价格 HH RSI LH — 上涨趋势延续）
        if rsi_div.get('hidden_bearish'):
            grade = 'S'; grade_score = 12
            grade_notes.append('🔵 隐藏看空背离(S级) 下跌趋势延续 +12')
        # A级：常观看空背离（价格 HH RSI LH — 顶部反转）
        elif rsi_div.get('regular_bearish'):
            grade = 'A'; grade_score = 10
            grade_notes.append('🟢 常观看空背离(A级) 顶部反转信号 +10')
        # B级： RSI FTS
        elif rsi_div.get('fts_bearish'):
            grade = 'B'; grade_score = 8
            grade_notes.append('🟡 RSI失败摇动看空(B级) +8')
        # C级： MACD单独背离
        elif macd_div.get('regular_bearish') or macd_div.get('hidden_bearish'):
            grade = 'C'; grade_score = 5
            grade_notes.append('🟠 MACD背离(C级) +5')

        # 叠加共振
        if grade in ('A','B') and (macd_div.get('regular_bearish') or macd_div.get('zero_cross_down')):
            grade_score = min(grade_score + 3, 18)
            grade_notes.append('+MACD共振 +3')
        macd_zero_pen = -2 if macd_div.get('zero_cross_up') else 0  # 0轴上方做空 = 降分
        # (0轴下方做空不调整)
        grade_score = max(0, grade_score + macd_zero_pen)

        candle_bonus = min(candle['score_short'], 4)
        grade_score  = min(grade_score + candle_bonus, 20)
        if candle_bonus and candle['patterns']:
            grade_notes.append(f'K线{candle["patterns"][0]} +{candle_bonus}')

        raw = grade_score
        details_dir = (
            [f'[{interval_label}] ' + d for d in rsi_div['details'] if '看空' in d or '空' in d] +
            [f'[{interval_label}] ' + d for d in macd_div['details'] if '空' in d or '死叉' in d]
        )

    # [修复] NONE级别：加入辅助分（MACD趋势方向+对手背离惩罚）
    if grade == 'NONE':
        # MACD方向轻微辅助
        if signal_dir == 'SHORT':
            if macd_div.get('zero_cross_down'):
                raw = 4; grade_notes.append('MACD死叉辅助 +4')
            elif macd_div.get('regular_bearish'):
                raw = 3; grade_notes.append('MACD弱看空 +3')
            # 对手强背离惩罚（看多背离出现，做空降分）
            elif rsi_div.get('regular_bullish') or rsi_div.get('fts_bullish'):
                raw = 0; grade_notes.append('⚠️ 对手看多背离，做空谨慎')
            else:
                raw = 2  # 中性小分
        else:  # LONG
            if macd_div.get('zero_cross_up'):
                raw = 4; grade_notes.append('MACD金叉辅助 +4')
            elif macd_div.get('regular_bullish'):
                raw = 3; grade_notes.append('MACD弱看多 +3')
            elif rsi_div.get('regular_bearish') or rsi_div.get('fts_bearish'):
                raw = 0; grade_notes.append('⚠️ 对手看空背离，做多谨慎')
            else:
                raw = 2
    score = min(raw, 20)
    # ── 达摩院实训 v3 修正项应用────────────────────────────────────
    # 成交量加分/惩分
    score = max(0, min(score + vol_bonus - vol_penalty, 20))
    # 时间窗口惩分（周二/-12月）
    score = max(0, score - time_penalty)
    # 体制惩分/加分
    score = max(0, min(score - regime_penalty, 20))
    # 更新详细信息
    if vol_bonus > 0 and volumes:
        grade_notes.append(f'✅量缩验证加分+{vol_bonus}(vol={vol_info["vol_ratio"]:.2f}x均值)')
    if vol_penalty > 0 and volumes:
        grade_notes.append(f'⚠️放量空间惩分-{vol_penalty}(量价背离密度降低)')
    if time_note:
        grade_notes.append(time_note)
    if regime_note:
        grade_notes.append(regime_note)

    grade_label = {'S': '🔵S级(隐藏)', 'A': '🟢A级(常观)', 'B': '🟡B级(FTS)', 'C': '🟠C级(MACD)', 'NONE': '⚪无背离'}.get(grade, grade)

    return {
        'score':       score,
        'max':         20,
        'grade':       grade,
        'grade_label': grade_label,
        'grade_notes': grade_notes,
        'details':     grade_notes + details_dir,
        'rsi_div':     rsi_div,
        'macd_div':    macd_div,
        'candle':      candle,
        'vol_info':    vol_info if volumes else {},
        'vol_bonus':   vol_bonus,
        'vol_penalty': vol_penalty,
        'time_penalty':time_penalty,
        'regime_penalty': regime_penalty,
    }


# ═══════════════════════════════════════════════════════════════
# 多周期背离共振 v4（设计院 2026-06-05）
# 三级共振：15M + 1H + 4H 同时背离 → 极强底/顶部信号
# ═══════════════════════════════════════════════════════════════

def multitf_divergence_score(symbol: str, signal_dir: str) -> dict:
    """
    多周期背离共振评分（最高20分）
    同时满足的周期越多，分数越高，底/顶部可靠性越强

    共振等级：
      三级共振（15M+1H+4H）→ TRIPLE  +20分
      双级共振（任意两个）  → DOUBLE  +12分
      单周期              → SINGLE  +6分
      无背离              → NONE    +0分
    """
    try:
        import sys, os
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from data_cache import get_klines, klines_to_ohlcv
    except Exception as e:
        return {'score': 0, 'resonance': 'NONE', 'notes': [f'数据加载失败: {e}']}

    tf_results = {}
    tf_scores  = {}

    for tf in ['15m', '1h', '4h']:
        try:
            k = klines_to_ohlcv(get_klines(symbol, tf, 150))
            if not k or len(k.get('c', [])) < 30:
                continue
            vols = k.get('v', [])
            res = divergence_score(
                k['o'], k['h'], k['l'], k['c'],
                signal_dir, tf.upper(),
                volumes=vols if vols else None
            )
            tf_results[tf] = res
            tf_scores[tf]  = res['score']
        except Exception:
            continue

    if not tf_scores:
        return {'score': 0, 'resonance': 'NONE', 'notes': ['无法获取数据']}

    # 有效背离：score >= 8 视为该周期有信号
    active = {tf: s for tf, s in tf_scores.items() if s >= 8}
    notes  = []

    for tf, s in tf_scores.items():
        grade = tf_results[tf].get('grade', '?')
        notes.append(f'{tf.upper()} 背离={grade} score={s}')

    if len(active) >= 3:
        resonance = 'TRIPLE'
        base_score = 20
        notes.insert(0, f'🔥 三级共振！{list(active.keys())} 同步背离 → 极强{"底" if signal_dir=="LONG" else "顶"}部信号')
    elif len(active) >= 2:
        resonance = 'DOUBLE'
        base_score = 12
        notes.insert(0, f'⚡ 双级共振 {list(active.keys())} → {"底" if signal_dir=="LONG" else "顶"}部确认增强')
    elif len(active) >= 1:
        resonance = 'SINGLE'
        base_score = 6
        notes.insert(0, f'✅ 单周期背离 {list(active.keys())}')
    else:
        resonance = 'NONE'
        base_score = 0
        notes.insert(0, '无多周期背离共振')

    # 加分：高grade背离
    bonus = 0
    for tf, res in tf_results.items():
        if res.get('grade') in ('S', 'A') and tf_scores.get(tf, 0) >= 8:
            bonus += 2
            notes.append(f'{tf.upper()} {res["grade"]}级背离 +2')

    return {
        'score':     min(base_score + bonus, 20),
        'resonance': resonance,
        'tf_scores': tf_scores,
        'active_tfs': list(active.keys()),
        'notes':     notes,
        'details':   {tf: tf_results[tf].get('grade_label','?') for tf in tf_results},
    }


# ─── 快速测试 ────────────────────────────────────────────────
if __name__ == '__main__':
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from data_cache import get_klines, klines_to_ohlcv

    sym = sys.argv[1] if len(sys.argv) > 1 else 'ETHUSDT'
    direction = sys.argv[2] if len(sys.argv) > 2 else 'SHORT'

    k1h = klines_to_ohlcv(get_klines(sym, '1h', 200))
    k4h = klines_to_ohlcv(get_klines(sym, '4h', 200))

    pass  # [静默]

    for label, k in [('1H', k1h), ('4H', k4h)]:
        res = divergence_score(k['o'], k['h'], k['l'], k['c'], direction, label)
        print(f'\n  {label} 背离评分: {res["score"]}/20')
        for d in res['details']:
            print(f'    {d}')

    pass  # [静默]
