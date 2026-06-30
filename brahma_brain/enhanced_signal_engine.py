"""
enhanced_signal_engine.py · 增强信号引擎
brahma_brain · P1

模块：
  1. CVD 累积成交量差值（买卖压力方向）
  2. 清算热力图（多空清算不对称 → T/SL锚点）
  3. 多空比趋势（5期历史变化方向，不只快照）
  4. MaxPain 磁吸效应（到期前价格引力）
  5. 时段权重精细化（亚洲/欧洲/美国盘特性）
"""

import time
import datetime
import urllib.request
import json

# ── brahma_bus 总线接入（设计院 2026-06-29）──
try:
    from brahma_brain.brahma_bus import bus as _brahma_bus
except Exception:
    _brahma_bus = None

def _bus_price(symbol: str) -> float:
    """统一价格获取：优先走 brahma_bus 缓存，降级走直接HTTP"""
    if _brahma_bus:
        return _brahma_bus.price(symbol)
    import requests
    r = requests.get(f'https://fapi.binance.com/fapi/v1/ticker/price',
                     params={'symbol': symbol}, timeout=5)
    return float(r.json()['price'])



FAPI  = 'https://fapi.binance.com'
DAPI  = 'https://dapi.binance.com'

def _get(url: str, timeout: int = 6) -> dict | list | None:
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None

# ═══════════════════════════════════════════════════════════════
# 1. CVD 累积成交量差值
# ═══════════════════════════════════════════════════════════════

def get_cvd(symbol: str, interval: str = '1h', limit: int = 20) -> dict:
    """
    计算CVD（Cumulative Volume Delta）
    买方主动成交 - 卖方主动成交
    返回：cvd_trend（方向）+ cvd_divergence（与价格方向背离？）
    """
    url = f'{FAPI}/fapi/v1/aggTrades?symbol={symbol}&limit=500'
    trades = _get(url)
    if not trades:
        return {'cvd': 0, 'trend': 'UNKNOWN', 'divergence': False, 'notes': []}

    buy_vol  = sum(float(t['q']) for t in trades if not t['m'])  # m=True是卖方主动
    sell_vol = sum(float(t['q']) for t in trades if t['m'])
    cvd = buy_vol - sell_vol

    # Kline价格方向
    klines_url = f'{FAPI}/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}'
    klines = _get(klines_url)
    if not klines:
        return {'cvd': round(cvd, 2), 'trend': 'BUY' if cvd > 0 else 'SELL',
                'divergence': False, 'notes': []}

    closes = [float(k[4]) for k in klines]
    price_up = closes[-1] > closes[0]

    # 历史CVD趋势（用Taker数据）
    taker_url = f'{FAPI}/futures/data/takerlongshortRatio?symbol={symbol}&period={interval}&limit={limit}'
    taker_data = _get(taker_url)
    cvd_series = []
    if taker_data:
        for td in taker_data:
            buy_r  = float(td.get('buyVol', 0))
            sell_r = float(td.get('sellVol', 0))
            cvd_series.append(buy_r - sell_r)

    cvd_up = (cvd > 0) if not cvd_series else (sum(cvd_series[-5:]) > 0)
    divergence = (price_up and not cvd_up) or (not price_up and cvd_up)

    trend = 'BUY_DOMINANT' if cvd_up else 'SELL_DOMINANT'
    notes = []
    if divergence:
        if price_up and not cvd_up:
            notes.append('CVD顶背离：价格上涨但买压减弱 → 反转信号')
        else:
            notes.append('CVD底背离：价格下跌但卖压减弱 → 反转信号')

    return {
        'cvd':        round(cvd, 2),
        'buy_vol':    round(buy_vol, 2),
        'sell_vol':   round(sell_vol, 2),
        'trend':      trend,
        'divergence': divergence,
        'price_up':   price_up,
        'notes':      notes,
    }

# ═══════════════════════════════════════════════════════════════
# 2. 清算热力图（多空清算不对称）
# ═══════════════════════════════════════════════════════════════

def get_liquidation_levels(symbol: str) -> dict:
    """
    获取清算热力图数据
    关键：多空清算不对称 → 识别价格猎杀方向
    返回：liq_above（上方清算密度）+ liq_below（下方清算密度）
    """
    # Binance 强平委托数据
    url = f'{FAPI}/fapi/v1/forceOrders?symbol={symbol}&autoCloseType=LIQUIDATION&limit=50'
    data = _get(url)

    if not data:
        return {'liq_above': 0, 'liq_below': 0, 'asymmetry': 0, 'notes': []}

    # 获取当前价
    price_url = f'{FAPI}/fapi/v1/ticker/price?symbol={symbol}'
    price_data = _get(price_url)
    price = float(price_data['price']) if price_data else 0

    if price == 0:
        return {'liq_above': 0, 'liq_below': 0, 'asymmetry': 0, 'notes': []}

    liq_above = 0.0  # 价格上方的空头清算量（做空被清）
    liq_below = 0.0  # 价格下方的多头清算量（做多被清）

    for order in data:
        avg_price = float(order.get('avgPrice', 0) or 0)
        qty       = float(order.get('origQty', 0) or 0)
        side      = order.get('side', '')
        if avg_price == 0:
            continue
        if avg_price > price and side == 'SELL':  # 空头清算在上方
            liq_above += qty * avg_price
        elif avg_price < price and side == 'BUY':  # 多头清算在下方
            liq_below += qty * avg_price

    total = liq_above + liq_below + 1e-9
    asymmetry = (liq_above - liq_below) / total  # >0=上方空头多, <0=下方多头多

    notes = []
    if asymmetry > 0.3:
        notes.append(f'上方空头清算密集(+{asymmetry:.1%}) → 价格猎杀上方止损风险')
    elif asymmetry < -0.3:
        notes.append(f'下方多头清算密集({asymmetry:.1%}) → 价格猎杀下方止损风险')

    return {
        'liq_above':  round(liq_above / 1e6, 2),  # 百万美元
        'liq_below':  round(liq_below / 1e6, 2),
        'asymmetry':  round(asymmetry, 3),
        'notes':      notes,
    }

# ═══════════════════════════════════════════════════════════════
# 3. 多空比趋势（历史5期）
# ═══════════════════════════════════════════════════════════════

def get_lsr_trend(symbol: str, period: str = '1h', limit: int = 8) -> dict:
    """
    多空比历史趋势分析
    不只看当前值，看方向变化
    """
    url = f'{FAPI}/futures/data/globalLongShortAccountRatio?symbol={symbol}&period={period}&limit={limit}'
    data = _get(url)

    if not data or len(data) < 3:
        return {'trend': 'UNKNOWN', 'current': 0.5, 'delta': 0, 'notes': []}

    # 多空比序列
    lsr_series = [float(d.get('longShortRatio', 1.0)) for d in data]
    current = lsr_series[-1]
    prev    = lsr_series[-4] if len(lsr_series) >= 4 else lsr_series[0]
    delta   = current - prev  # >0=多头增加, <0=空头增加

    # 趋势判断
    if delta > 0.05 and current > 1.2:
        trend = 'LONG_CROWDED'  # 多头过度拥挤 → 反向做空信号
    elif delta < -0.05 and current < 0.9:
        trend = 'SHORT_CROWDED'  # 空头过度拥挤 → 反向做多信号
    elif delta > 0:
        trend = 'LONG_GROWING'
    elif delta < 0:
        trend = 'SHORT_GROWING'
    else:
        trend = 'NEUTRAL'

    long_pct  = round(current / (1 + current) * 100, 1)
    notes = []
    if trend == 'LONG_CROWDED':
        notes.append(f'多头持续积累({long_pct}%多) → 聪明钱反向空 +反转风险')
    elif trend == 'SHORT_GROWING':
        notes.append(f'多空比下降({delta:+.2f}) → 空头增仓中 动能增强')
    elif trend == 'LONG_GROWING':
        notes.append(f'多空比上升({delta:+.2f}) → 多头增仓 短期强势但需警惕')

    return {
        'trend':    trend,
        'current':  round(current, 3),
        'delta':    round(delta, 3),
        'long_pct': long_pct,
        'series':   [round(x, 3) for x in lsr_series[-5:]],
        'notes':    notes,
    }

# ═══════════════════════════════════════════════════════════════
# 4. 时段权重精细化
# ═══════════════════════════════════════════════════════════════

SESSION_WINDOWS = {
    # UTC时间 (start_h, end_h): (session_name, volatility_mult, notes)
    (22, 3):  ('亚洲盘开盘', 0.8,  '流动性较低 假突破多 信号折扣20%'),
    (3,  8):  ('亚洲盘',    0.7,  '最低波动 等待欧洲盘'),
    (7,  9):  ('欧洲盘开盘', 1.3,  '欧洲资金入场 方向明确 +30%'),
    (9,  13): ('欧洲盘',    1.1,  '欧洲主力时段 趋势延续'),
    (13, 15): ('午盘重叠',   0.9,  '欧美交接 波动短暂下降'),
    (13, 17): ('美国盘开盘', 1.5,  '最高波动 决定日线方向 +50%'),
    (17, 21): ('美国盘主力', 1.3,  '美国资金主导 趋势最可靠'),
    (21, 22): ('美国盘收盘', 0.9,  '尾盘波动 信号可靠性下降'),
}

def get_session_weight(utc_hour: int = None) -> dict:
    """
    根据当前UTC时间返回时段权重
    """
    if utc_hour is None:
        utc_hour = datetime.datetime.utcnow().hour

    # 找当前时段
    session_name = '亚洲盘'
    vol_mult = 0.8
    notes = '低波动时段'

    for (start, end), (name, mult, note) in SESSION_WINDOWS.items():
        if start <= end:
            in_window = start <= utc_hour < end
        else:  # 跨午夜
            in_window = utc_hour >= start or utc_hour < end
        if in_window:
            session_name = name
            vol_mult = mult
            notes = note
            break

    # 下一个高波动窗口
    next_event = ''
    h = utc_hour
    for offset in range(1, 25):
        nh = (h + offset) % 24
        for (start, end), (name, mult, n) in SESSION_WINDOWS.items():
            if mult >= 1.3:
                if start <= end:
                    if nh == start:
                        next_event = f'{offset}h后 {name}'
                        break
                else:
                    if nh == start:
                        next_event = f'{offset}h后 {name}'
                        break
        if next_event:
            break

    return {
        'utc_hour':     utc_hour,
        'session':      session_name,
        'vol_mult':     vol_mult,
        'notes':        notes,
        'next_active':  next_event,
        'score':        round(vol_mult * 5),  # 0~8分
    }

# ═══════════════════════════════════════════════════════════════
# 5. 综合增强评分
# ═══════════════════════════════════════════════════════════════

def enhanced_score(symbol: str, signal_dir: str) -> dict:
    """
    综合增强信号评分 → 0~25分
    包含：CVD(5) + 清算不对称(5) + 多空比趋势(5) + 时段(10)
    """
    score = 0
    notes = []
    breakdown = {}

    # CVD 多周期（星枢引擎 v1.0 升级）
    try:
        import os as _os, sys as _sys
        _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
        from brahma_brain.cvd_engine import cvd_score_for_signal
        cvd_s, cvd_notes = cvd_score_for_signal(symbol, signal_dir)
        breakdown['cvd'] = cvd_s
        score += cvd_s
        notes.extend(cvd_notes)
    except Exception:
        # 降级：原单周期CVD
        try:
            cvd = get_cvd(symbol)
            cvd_s = 0
            if signal_dir == 'SHORT' and cvd['trend'] == 'SELL_DOMINANT':
                cvd_s = 4; notes.append('CVD卖方主导 +4')
            elif signal_dir == 'LONG' and cvd['trend'] == 'BUY_DOMINANT':
                cvd_s = 4; notes.append('CVD买方主导 +4')
            breakdown['cvd'] = cvd_s
            score += cvd_s
        except Exception:
            breakdown['cvd'] = 0

    # 清算不对称
    try:
        liq = get_liquidation_levels(symbol)
        liq_s = 0
        asym = liq['asymmetry']
        if signal_dir == 'SHORT' and asym < -0.2:
            # 下方多头清算密集 → 做空可以打穿止损
            liq_s = 3
            notes.append(f'下方多头清算密集 → 做空可触发 +3')
        elif signal_dir == 'LONG' and asym > 0.2:
            # 上方空头清算密集 → 做多可以触发轧空
            liq_s = 3
            notes.append(f'上方空头清算密集 → 做多可触发轧空 +3')
        breakdown['liquidation'] = liq_s
        score += liq_s
    except Exception:
        breakdown['liquidation'] = 0

    # 多空比趋势
    try:
        lsr = get_lsr_trend(symbol)
        lsr_s = 0
        if signal_dir == 'SHORT':
            if lsr['trend'] == 'LONG_CROWDED':
                lsr_s = 5; notes.append(f'多头拥挤反转信号 +5')
            elif lsr['trend'] == 'SHORT_GROWING':
                lsr_s = 3; notes.append(f'空头持续增仓 +3')
        elif signal_dir == 'LONG':
            if lsr['trend'] == 'SHORT_CROWDED':
                lsr_s = 5; notes.append(f'空头拥挤反转信号 +5')
            elif lsr['trend'] == 'LONG_GROWING':
                lsr_s = 2; notes.append(f'多头持续增仓 +2')
        breakdown['lsr_trend'] = lsr_s
        score += lsr_s
    except Exception:
        breakdown['lsr_trend'] = 0
        lsr = {}

    # 时段权重
    try:
        session = get_session_weight()
        sess_s = session['score']
        breakdown['session'] = sess_s
        score += sess_s
        notes.append(f'{session["session"]}({session["vol_mult"]}x) +{sess_s}')
        if session['next_active']:
            notes.append(f'下一活跃: {session["next_active"]}')
    except Exception:
        breakdown['session'] = 3
        score += 3

    return {
        'score':     min(score, 25),
        'max':       25,
        'breakdown': breakdown,
        'notes':     notes,
        'lsr':       lsr if 'lsr' in dir() else {},
        'session':   session if 'session' in dir() else {},
        # [达摩院V7] 新增vol_spike字段供量能维度加分
        'vol_spike': (cvd_data.get('trend') in ('BUY_DOMINANT','SELL_DOMINANT') and
                      abs(cvd_data.get('cvd', 0)) > 0) if 'cvd_data' in dir() else False,
    }
