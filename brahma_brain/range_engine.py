"""
range_engine.py · 区间结构识别引擎
brahma_brain · Phase 2a

功能：
  - 区间边界识别（多次触碰确认）
  - Premium/Discount Zone 定位
  - 假突破检测（震荡市最高质量信号）
  - 均值严重偏离识别
  - 区间综合评分（0~15分）

数据铁证：
  区间高位做空 WR=71.6%，n=183,223，6年全稳定
  假突破反向   WR预计≥68%（样本积累中）
"""

# ═══════════════════════════════════════════════════════════════
# 一、区间识别
# ═══════════════════════════════════════════════════════════════

def detect_range_structure(highs: list, lows: list, closes: list,
                            lookback: int = 48) -> dict:
    """
    识别价格是否处于区间震荡结构
    lookback=48根K线（4H×48=8天 / 1H×48=2天 / 15m×48=12小时）

    返回：
      is_range     : bool   是否处于区间
      range_high   : float  区间高点
      range_low    : float  区间低点
      position     : float  当前位置（0=低点, 1=高点）
      zone         : str    'PREMIUM'(>70%) / 'DISCOUNT'(<30%) / 'MIDDLE'
      quality      : str    'HIGH' / 'MEDIUM' / 'LOW'
      touch_count  : int    边界触碰次数（越多越可靠）
      range_pct    : float  区间大小（%）
    """
    n = min(lookback, len(closes))
    if n < 20:
        return _no_range()

    h_win = highs[-n:]
    l_win = lows[-n:]
    c_win = closes[-n:]

    range_high = max(h_win)
    range_low  = min(l_win)
    mid        = (range_high + range_low) / 2
    range_pct  = (range_high - range_low) / range_low * 100 if range_low > 0 else 0

    # 区间太窄（<1%）→ 压缩行情不是区间
    # 区间太宽（>20%）→ 可能是趋势，不是区间
    if range_pct < 1.0 or range_pct > 25.0:
        return _no_range()

    cur = closes[-1]
    position = (cur - range_low) / (range_high - range_low) if range_high != range_low else 0.5

    # 统计边界触碰次数（±1% 容差）
    tol_h = range_high * 0.99
    tol_l = range_low  * 1.01
    touch_high = sum(1 for h in h_win if h >= tol_h)
    touch_low  = sum(1 for l in l_win if l <= tol_l)

    # 至少各触碰2次才算有效区间
    if touch_high < 2 or touch_low < 2:
        return _no_range()

    # 区间质量评级
    total_touch = touch_high + touch_low
    if total_touch >= 8:  quality = 'HIGH'
    elif total_touch >= 5: quality = 'MEDIUM'
    else:                  quality = 'LOW'

    # 当前位置区域
    if position >= 0.70:   zone = 'PREMIUM'    # 高位供应区
    elif position <= 0.30: zone = 'DISCOUNT'   # 低位需求区
    else:                  zone = 'MIDDLE'

    return {
        'is_range':    True,
        'range_high':  round(range_high, 4),
        'range_low':   round(range_low, 4),
        'mid':         round(mid, 4),
        'position':    round(position, 3),
        'zone':        zone,
        'quality':     quality,
        'touch_high':  touch_high,
        'touch_low':   touch_low,
        'range_pct':   round(range_pct, 2),
    }


def _no_range():
    return {
        'is_range': False, 'range_high': 0, 'range_low': 0,
        'mid': 0, 'position': 0.5, 'zone': 'UNKNOWN',
        'quality': 'LOW', 'touch_high': 0, 'touch_low': 0, 'range_pct': 0
    }


# ═══════════════════════════════════════════════════════════════
# 二、假突破检测
# ═══════════════════════════════════════════════════════════════

def detect_fakeout(highs: list, lows: list, closes: list,
                   range_high: float, range_low: float) -> dict:
    """
    假突破检测：价格突破区间边界后快速回落
    这是震荡市最高质量信号之一

    条件：
      1. 前1~3根K线高点突破区间高点（或低点跌破区间低点）
      2. 当前收盘价已回到区间内
      3. 突破幅度 <3%（真突破通常>3%且持续）
    """
    if not highs or len(closes) < 5:
        return {'fakeout': False, 'direction': None}

    cur_close = closes[-1]

    # 检查是否有近期假突破向上（做空机会）
    if range_high > 0:
        recent_high = max(highs[-4:-1]) if len(highs) >= 4 else max(highs[:-1])
        breakout_pct = (recent_high - range_high) / range_high * 100

        if (0.1 < breakout_pct < 3.0) and cur_close < range_high * 1.002:
            return {
                'fakeout': True,
                'direction': 'BEARISH_FAKEOUT',
                'breakout_pct': round(breakout_pct, 3),
                'detail': f'假突破向上+{breakout_pct:.2f}%后回落区间内'
            }

    # 检查假突破向下（做多机会）
    if range_low > 0:
        recent_low = min(lows[-4:-1]) if len(lows) >= 4 else min(lows[:-1])
        breakout_pct = (range_low - recent_low) / range_low * 100

        if (0.1 < breakout_pct < 3.0) and cur_close > range_low * 0.998:
            return {
                'fakeout': True,
                'direction': 'BULLISH_FAKEOUT',
                'breakout_pct': round(breakout_pct, 3),
                'detail': f'假突破向下-{breakout_pct:.2f}%后回升区间内'
            }

    return {'fakeout': False, 'direction': None}


# ═══════════════════════════════════════════════════════════════
# 三、均值偏离度
# ═══════════════════════════════════════════════════════════════

def calc_mean_deviation(closes: list, n: int = 20) -> dict:
    """计算当前价格偏离均值的程度（ATR单位）"""
    if len(closes) < n + 2:
        return {'deviation': 0, 'side': 'NEUTRAL', 'severity': 'NONE'}

    win = closes[-n:]
    mean = sum(win) / len(win)
    atr_proxy = sum(abs(win[i]-win[i-1]) for i in range(1,len(win))) / (len(win)-1)
    if atr_proxy == 0:
        return {'deviation': 0, 'side': 'NEUTRAL', 'severity': 'NONE'}

    cur  = closes[-1]
    dev  = (cur - mean) / atr_proxy  # 偏离几个ATR

    if dev > 2.5:    side, severity = 'ABOVE', 'EXTREME'
    elif dev > 1.5:  side, severity = 'ABOVE', 'HIGH'
    elif dev > 0.5:  side, severity = 'ABOVE', 'MILD'
    elif dev < -2.5: side, severity = 'BELOW', 'EXTREME'
    elif dev < -1.5: side, severity = 'BELOW', 'HIGH'
    elif dev < -0.5: side, severity = 'BELOW', 'MILD'
    else:            side, severity = 'NEUTRAL', 'NONE'

    return {
        'deviation': round(dev, 2),
        'side': side,
        'severity': severity,
        'mean': round(mean, 4),
    }


# ═══════════════════════════════════════════════════════════════
# 四、综合评分入口
# ═══════════════════════════════════════════════════════════════

def range_score(highs: list, lows: list, closes: list,
                signal_dir: str = 'SHORT') -> dict:
    """
    区间结构综合评分（0~15分）

    数据铁证（P0扫描结果）：
      区间高位做空 WR=71.6%, n=183,223, 6年稳定
      基线OB做空   WR=70.7%
      → 区间结构额外WR+1.6% → 对应+12分权重
    """
    score    = 0
    details  = []

    rng = detect_range_structure(highs, lows, closes)

    if not rng['is_range']:
        return {
            'score': 0, 'details': ['无区间结构'],
            'zone': 'UNKNOWN', 'is_range': False
        }

    zone    = rng['zone']
    quality = rng['quality']
    pos     = rng['position']
    rng_pct = rng['range_pct']

    # ── 核心加分：位置 ─────────────────────────────────────────
    if signal_dir == 'SHORT':
        if zone == 'PREMIUM':
            if quality == 'HIGH':
                score += 12; details.append(f'Premium区高质量做空+12 pos={pos:.0%}')
            elif quality == 'MEDIUM':
                score += 8;  details.append(f'Premium区中质量做空+8 pos={pos:.0%}')
            else:
                score += 5;  details.append(f'Premium区低质量做空+5 pos={pos:.0%}')
        elif zone == 'MIDDLE':
            score += 2; details.append('区间中部+2')
        else:  # DISCOUNT → 做空不利
            score -= 3; details.append('Discount区做空-3（逆势）')

    elif signal_dir == 'LONG':
        if zone == 'DISCOUNT':
            if quality == 'HIGH':
                score += 12; details.append(f'Discount区高质量做多+12 pos={pos:.0%}')
            elif quality == 'MEDIUM':
                score += 8;  details.append(f'Discount区中质量做多+8 pos={pos:.0%}')
            else:
                score += 5;  details.append(f'Discount区低质量做多+5 pos={pos:.0%}')
        elif zone == 'MIDDLE':
            score += 2; details.append('区间中部+2')
        else:  # PREMIUM → 做多不利
            score -= 3; details.append('Premium区做多-3（逆势）')

    # ── [Phase2d] 极端顶部专属加成 ────────────────────────────
    # 数据铁证: range_pos 0.94-1.00 WR=75.1% n=1,848（全年最强单格）
    # 原Premium区加分未区分0.7~0.93和0.94~1.00，Phase2d单独识别极端顶部
    if signal_dir == 'SHORT' and pos >= 0.94 and zone == 'PREMIUM':
        score += 5  # 极端顶部额外+5（WR75.1% vs 普通Premium区72.8%）
        details.append(f'[Phase2d] 极端顶部+5 pos={pos:.0%} WR=75.1%')
    elif signal_dir == 'LONG' and pos <= 0.06 and zone == 'DISCOUNT':
        score += 5
        details.append(f'[Phase2d] 极端底部+5 pos={pos:.0%}')

    # ── 假突破加分 ─────────────────────────────────────────────
    fk = detect_fakeout(highs, lows, closes, rng['range_high'], rng['range_low'])
    if fk['fakeout']:
        if (signal_dir=='SHORT' and fk['direction']=='BEARISH_FAKEOUT') or \
           (signal_dir=='LONG'  and fk['direction']=='BULLISH_FAKEOUT'):
            score += 5; details.append(f'假突破确认+5 {fk["detail"]}')

    # ── 均值偏离加分 ───────────────────────────────────────────
    dev = calc_mean_deviation(closes)
    if signal_dir == 'SHORT' and dev['side'] == 'ABOVE':
        if dev['severity'] == 'EXTREME':
            score += 3; details.append(f'极端偏离均值+3 dev={dev["deviation"]}ATR')
        elif dev['severity'] == 'HIGH':
            score += 2; details.append(f'高度偏离均值+2')
    elif signal_dir == 'LONG' and dev['side'] == 'BELOW':
        if dev['severity'] == 'EXTREME':
            score += 3; details.append(f'极端偏离均值+3 dev={dev["deviation"]}ATR')
        elif dev['severity'] == 'HIGH':
            score += 2; details.append(f'高度偏离均值+2')

    score = max(0, min(score, 15))

    return {
        'score':      score,
        'details':    details,
        'zone':       zone,
        'quality':    quality,
        'position':   pos,
        'range_pct':  rng_pct,
        'is_range':   True,
        'fakeout':    fk['fakeout'],
    }
