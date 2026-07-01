"""

# ── STATUS: AUXILIARY ──────────────────────────────────────────
# 成交量分析，s4维度辅助
# LAST_REVIEW: 2026-07-01 | 属于辅助计算层，修改前确认调用链
# ─────────────────────────────────────────────────────────────
volume_engine.py · 量能分析引擎
brahma_brain · Phase 2

功能：
  - OBV（能量潮）趋势与背离
  - 成交量背离检测（价量关系）
  - VWAP 计算与方向判断
  - 成交量分布（简化Volume Profile）
  - 量能综合评分（0~20分）
"""
import math

# ═══════════════════════════════════════════════════════════════
# 一、OBV（能量潮）
# ═══════════════════════════════════════════════════════════════

def calc_obv(closes: list, volumes: list) -> list:
    """计算OBV序列"""
    if not closes or len(closes) != len(volumes):
        return []
    obv = [0.0]
    for i in range(1, len(closes)):
        if closes[i] > closes[i-1]:
            obv.append(obv[-1] + volumes[i])
        elif closes[i] < closes[i-1]:
            obv.append(obv[-1] - volumes[i])
        else:
            obv.append(obv[-1])
    return obv

def detect_obv_divergence(closes: list, volumes: list) -> dict:
    """OBV背离检测"""
    obv = calc_obv(closes, volumes)
    if len(obv) < 10:
        return {'bull_div': False, 'bear_div': False, 'trend_match': True, 'details': []}

    # 比较最近20根的价格与OBV走势
    n = min(20, len(closes))
    p_start, p_end = closes[-n],  closes[-1]
    o_start, o_end = obv[-n],     obv[-1]

    p_up  = p_end > p_start
    o_up  = o_end > o_start

    # OBV创新高而价格未到 → 多头蓄力
    obv_new_high  = obv[-1] > max(obv[-n:-1]) if len(obv) >= n+1 else False
    price_new_high = closes[-1] > max(closes[-n:-1]) if len(closes) >= n+1 else False
    obv_new_low   = obv[-1] < min(obv[-n:-1]) if len(obv) >= n+1 else False
    price_new_low  = closes[-1] < min(closes[-n:-1]) if len(closes) >= n+1 else False

    details = []
    bull_div = False
    bear_div = False

    # 看多背离：价格LL，OBV HL
    if not p_up and o_up:
        bull_div = True
        details.append('OBV看多背离：价格下跌但OBV上升，多头积累')
    # 看空背离：价格HH，OBV LH
    if p_up and not o_up:
        bear_div = True
        details.append('OBV看空背离：价格上涨但OBV下降，多头衰竭')
    # OBV领先信号
    if obv_new_high and not price_new_high:
        bull_div = True
        details.append('OBV创新高价格未到，多头蓄力')
    if obv_new_low and not price_new_low:
        bear_div = True
        details.append('OBV创新低价格未到，空头蓄力')

    return {
        'bull_div':    bull_div,
        'bear_div':    bear_div,
        'trend_match': p_up == o_up,   # 量价同向
        'obv_now':     round(obv[-1], 2),
        'details':     details,
    }

# ═══════════════════════════════════════════════════════════════
# 二、成交量分析
# ═══════════════════════════════════════════════════════════════

def analyze_volume(closes: list, volumes: list, n_ma: int = 20) -> dict:
    """分析成交量质量"""
    if len(volumes) < n_ma:
        return {'quality': 'NORMAL', 'details': [], 'score_long': 0, 'score_short': 0}

    vol_ma = sum(volumes[-n_ma:]) / n_ma
    vol_now = volumes[-1]
    vol_ratio = vol_now / vol_ma if vol_ma > 0 else 1.0

    price_up = closes[-1] > closes[-2]
    details  = []
    score_long = score_short = 0

    # 量增价涨 = 健康上涨
    if price_up and vol_ratio > 1.5:
        details.append(f'量增价涨(量={vol_ratio:.1f}x) 强多确认')
        score_long += 5
    # 量减价涨 = 动能衰竭
    elif price_up and vol_ratio < 0.7:
        details.append(f'量减价涨(量={vol_ratio:.1f}x) 上涨动能衰竭')
        score_short += 3
    # 量增价跌 = 恐慌抛售
    elif not price_up and vol_ratio > 1.5:
        details.append(f'量增价跌(量={vol_ratio:.1f}x) 恐慌抛售/主力出货')
        score_short += 4
    # 量减价跌 = 下跌尾声
    elif not price_up and vol_ratio < 0.7:
        details.append(f'量减价跌(量={vol_ratio:.1f}x) 下跌尾声，空头衰竭')
        score_long += 3

    # 超量（>3x均量）
    if vol_ratio > 3.0:
        details.append(f'极端成交量({vol_ratio:.1f}x) 主力入场/反转信号')
        if price_up:   score_long += 3
        else:          score_short += 2

    # 量能萎缩后放量突破
    recent_vols = volumes[-10:-1]
    if recent_vols:
        recent_avg = sum(recent_vols) / len(recent_vols)
        if recent_avg < vol_ma * 0.6 and vol_now > vol_ma * 1.5:
            details.append('量能萎缩后放量突破，方向确认')
            if price_up:   score_long += 5
            else:          score_short += 4

    quality = 'STRONG' if vol_ratio > 2.0 else ('WEAK' if vol_ratio < 0.5 else 'NORMAL')

    return {
        'quality':     quality,
        'vol_ratio':   round(vol_ratio, 2),
        'vol_ma':      round(vol_ma, 2),
        'details':     details,
        'score_long':  min(score_long, 10),
        'score_short': min(score_short, 10),
    }

# ═══════════════════════════════════════════════════════════════
# 三、VWAP 计算
# ═══════════════════════════════════════════════════════════════

def calc_vwap(highs: list, lows: list, closes: list, volumes: list,
              session_bars: int = 96) -> dict:
    """
    计算VWAP（按session，默认96根1H K线≈4天）
    """
    n = min(session_bars, len(closes))
    h = highs[-n:]
    l = lows[-n:]
    c = closes[-n:]
    v = volumes[-n:]

    tp_v = sum(((h[i]+l[i]+c[i])/3) * v[i] for i in range(n))
    total_v = sum(v)
    if total_v == 0:
        return {'vwap': closes[-1], 'above_vwap': True, 'dist_pct': 0}

    vwap = tp_v / total_v
    price = closes[-1]
    above = price >= vwap
    dist  = (price - vwap) / vwap * 100

    return {
        'vwap':       round(vwap, 6),
        'above_vwap': above,
        'dist_pct':   round(dist, 3),
        'note': f'价格在VWAP{"上方" if above else "下方"} {abs(dist):.2f}%',
    }

# ═══════════════════════════════════════════════════════════════
# 四、简化Volume Profile（关键成交量节点）
# ═══════════════════════════════════════════════════════════════

def calc_volume_profile(highs: list, lows: list, closes: list,
                         volumes: list, bins: int = 20) -> dict:
    """简化成交量分布，识别HVN/LVN"""
    if len(closes) < bins:
        return {'poc': closes[-1], 'hvn': [], 'lvn': []}

    price_min = min(lows[-100:])
    price_max = max(highs[-100:])
    if price_max <= price_min:
        return {'poc': closes[-1], 'hvn': [], 'lvn': []}

    bin_size  = (price_max - price_min) / bins
    vol_bins  = [0.0] * bins

    for i in range(len(closes)-100 if len(closes)>100 else 0, len(closes)):
        mid_price = (highs[i] + lows[i]) / 2
        idx = int((mid_price - price_min) / bin_size)
        idx = max(0, min(idx, bins-1))
        vol_bins[idx] += volumes[i]

    max_vol = max(vol_bins) if vol_bins else 1
    poc_idx = vol_bins.index(max_vol)
    poc = price_min + (poc_idx + 0.5) * bin_size

    avg_vol = sum(vol_bins) / bins
    hvn = []  # 高成交量节点（>均量150%）
    lvn = []  # 低成交量节点（<均量50%）

    for i, v in enumerate(vol_bins):
        level = price_min + (i + 0.5) * bin_size
        price = closes[-1]
        if v > avg_vol * 1.5:
            hvn.append(round(level, 4))
        elif v < avg_vol * 0.5:
            lvn.append(round(level, 4))

    return {
        'poc':    round(poc, 4),
        'hvn':    hvn,
        'lvn':    lvn,
        'note':  f'POC(最大成交量价格)=${poc:.4f}',
    }

# ═══════════════════════════════════════════════════════════════
# 五、量能综合评分（0~20分）
# ═══════════════════════════════════════════════════════════════

def volume_score(highs: list, lows: list, closes: list,
                  volumes: list, signal_dir: str) -> dict:
    """量能综合评分"""
    obv_div = detect_obv_divergence(closes, volumes)
    vol_ana = analyze_volume(closes, volumes)
    vwap    = calc_vwap(highs, lows, closes, volumes)
    vp      = calc_volume_profile(highs, lows, closes, volumes)

    score   = 0
    details = []

    # OBV背离
    if signal_dir == 'LONG' and obv_div['bull_div']:
        score += 5; details.append('OBV看多背离 +5')
    if signal_dir == 'SHORT' and obv_div['bear_div']:
        score += 5; details.append('OBV看空背离 +5')
    # 量价同向
    if obv_div['trend_match']:
        score += 2; details.append('量价同向 +2')

    # 成交量分析
    if signal_dir == 'LONG':
        score += vol_ana['score_long']
        details += [d for d in vol_ana['details'] if '多' in d or '上涨' in d or '尾声' in d]
    else:
        score += vol_ana['score_short']
        details += [d for d in vol_ana['details'] if '空' in d or '衰竭' in d or '出货' in d]

    # VWAP方向
    if signal_dir == 'LONG' and vwap['above_vwap']:
        score += 3; details.append(f'价格在VWAP上方 +3')
    elif signal_dir == 'LONG' and not vwap['above_vwap']:
        score += 1; details.append(f'价格在VWAP下方(折价区) +1')
    if signal_dir == 'SHORT' and not vwap['above_vwap']:
        score += 3; details.append(f'价格在VWAP下方 +3')

    # POC磁吸
    price = closes[-1]
    poc_dist = abs(vp['poc'] - price) / price * 100
    if poc_dist < 1.0:
        score += 2; details.append(f'POC磁吸区附近({poc_dist:.2f}%) +2')

    score = min(score, 20)
    return {
        'score':   score,
        'max':     20,
        'details': details,
        'obv':     obv_div,
        'vol':     vol_ana,
        'vwap':    vwap,
        'vp':      vp,
    }

# ─── 快速测试 ────────────────────────────────────────────────
if __name__ == '__main__':
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from data_cache import get_klines, klines_to_ohlcv

    sym = sys.argv[1] if len(sys.argv) > 1 else 'ETHUSDT'
    direction = sys.argv[2] if len(sys.argv) > 2 else 'SHORT'

    k1h = klines_to_ohlcv(get_klines(sym, '1h', 200))
    res = volume_score(k1h['h'], k1h['l'], k1h['c'], k1h['v'], direction)

    print(f'[Volume] {sym} 方向={direction}')
    print(f'  量能评分: {res["score"]}/20')
    print(f'  VWAP:    ${res["vwap"]["vwap"]:,.4f}  {res["vwap"]["note"]}')
    print(f'  POC:     ${res["vp"]["poc"]:,.4f}')
    print(f'  量比:    {res["vol"]["vol_ratio"]}x')
    for d in res['details']:
        print(f'  + {d}')
    print('[Volume] ✅ 测试完成')
