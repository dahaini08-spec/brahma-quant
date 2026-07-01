"""

# ── STATUS: AUXILIARY ──────────────────────────────────────────
# 微观结构分析，订单流辅助
# LAST_REVIEW: 2026-07-01 | 属于辅助计算层，修改前确认调用链
# ─────────────────────────────────────────────────────────────
microstructure_engine.py · 市场微观结构引擎
brahma_brain · P2

核心概念：
  吸收（Absorption）：大买单/卖单被对手方完全吸收，价格不动
  → 吸收后的方向 = 真实方向
  
  耗尽（Exhaustion）：价格大幅移动但成交量递减
  → 动能耗尽 = 反转信号

  停顿（Stall）：价格在关键位停止推进
  → 可能突破失败

数据源：Binance aggTrades（不需要WebSocket，用REST批量拉取）
"""

import json, urllib.request, time, statistics

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



FAPI = 'https://fapi.binance.com'

_CACHE = {}
_TTL   = 60  # 1分钟（微观数据需要更新）

def _get(url, timeout=8):
    t = time.time()
    if url in _CACHE and t - _CACHE[url]['ts'] < _TTL:
        return _CACHE[url]['data']
    try:
        req = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read())
            _CACHE[url] = {'data': d, 'ts': t}
            return d
    except Exception:
        return None

# ═══════════════════════════════════════════════════════════════
# 1. 大单吸收检测
# ═══════════════════════════════════════════════════════════════

def detect_absorption(symbol: str) -> dict:
    """
    大单吸收：大额成交出现但价格几乎不动
    判断逻辑：
      - 拉取最近1000笔 aggTrades
      - 找出大额成交（top 5%）
      - 看大额成交后价格变化
      - 变化<ATR的10% = 被吸收
    """
    url = f'{FAPI}/fapi/v1/aggTrades?symbol={symbol}&limit=1000'
    trades = _get(url)

    if not trades or len(trades) < 50:
        return {'absorbed': False, 'type': 'UNKNOWN', 'notes': []}

    price_url = f'{FAPI}/fapi/v1/ticker/price?symbol={symbol}'
    p_data = _get(price_url)
    price = float(p_data['price']) if p_data else 0
    if price == 0:
        return {'absorbed': False, 'type': 'UNKNOWN', 'notes': []}

    # 计算各笔成交金额
    qtys = [float(t['q']) * price for t in trades]
    threshold = sorted(qtys)[-len(qtys)//20]  # top 5% 门槛

    # 找大单
    big_trades = [(i, t) for i, t in enumerate(trades) if qtys[i] >= threshold]

    absorbed_buys  = 0
    absorbed_sells = 0
    exhausted_buys = 0
    exhausted_sells= 0

    for idx, t in big_trades:
        is_buy = not t['m']
        entry_p = float(t['p'])

        # 看后续10笔价格变化
        if idx + 10 >= len(trades):
            continue
        future_p = float(trades[idx+10]['p'])
        move_pct  = abs(future_p - entry_p) / entry_p * 100

        # ATR代理：最近成交价标准差
        recent_prices = [float(trades[max(0,idx-20):idx][j]['p']) for j in range(min(20, idx))]
        atr_proxy = statistics.stdev(recent_prices) / entry_p * 100 if len(recent_prices) > 2 else 0.1

        # 吸收判断：大单成交但价格变化<ATR代理的20%
        if move_pct < atr_proxy * 0.2:
            if is_buy:
                absorbed_buys += 1
            else:
                absorbed_sells += 1
        # 耗尽判断：一系列同向成交后价格反转
        elif move_pct > 0 and future_p < entry_p and is_buy:
            exhausted_buys += 1
        elif move_pct > 0 and future_p > entry_p and not is_buy:
            exhausted_sells += 1

    notes = []
    absorption_type = 'NONE'
    absorbed = False

    if absorbed_buys >= 3:
        # 大买单被吸收 → 上方有大卖压 → 看空
        absorption_type = 'BUY_ABSORBED'
        absorbed = True
        notes.append(f'大买单被吸收({absorbed_buys}次) → 上方卖压强 看空')
    elif absorbed_sells >= 3:
        # 大卖单被吸收 → 下方有大买支撑 → 看多
        absorption_type = 'SELL_ABSORBED'
        absorbed = True
        notes.append(f'大卖单被吸收({absorbed_sells}次) → 下方支撑强 看多')

    if exhausted_buys >= 2:
        notes.append(f'多头动能耗尽({exhausted_buys}次) → 上涨乏力')
    if exhausted_sells >= 2:
        notes.append(f'空头动能耗尽({exhausted_sells}次) → 下跌乏力')

    return {
        'absorbed':       absorbed,
        'type':           absorption_type,
        'absorbed_buys':  absorbed_buys,
        'absorbed_sells': absorbed_sells,
        'exhausted_buys': exhausted_buys,
        'exhausted_sells':exhausted_sells,
        'notes':          notes,
    }

# ═══════════════════════════════════════════════════════════════
# 2. 订单流失衡（不平衡压力）
# ═══════════════════════════════════════════════════════════════

def detect_order_flow_imbalance(symbol: str) -> dict:
    """
    买卖失衡检测：连续N笔成交中买方/卖方主导比例
    高度失衡 = 方向性压力，价格即将跟随
    """
    url = f'{FAPI}/fapi/v1/aggTrades?symbol={symbol}&limit=200'
    trades = _get(url)

    if not trades:
        return {'imbalance': 0, 'direction': 'NEUTRAL', 'notes': []}

    price_url = f'{FAPI}/fapi/v1/ticker/price?symbol={symbol}'
    p_data = _get(price_url)
    price = float(p_data['price']) if p_data else 1

    buy_vol  = sum(float(t['q'])*price for t in trades if not t['m'])
    sell_vol = sum(float(t['q'])*price for t in trades if t['m'])
    total    = buy_vol + sell_vol + 1e-9

    imbalance = (buy_vol - sell_vol) / total  # -1到+1

    # 动态阈值：最近50笔 vs 前150笔 对比
    recent = trades[-50:]
    older  = trades[:150]

    recent_buy = sum(1 for t in recent if not t['m'])
    older_buy  = sum(1 for t in older  if not t['m'])

    recent_ratio = recent_buy / len(recent)
    older_ratio  = older_buy  / len(older)
    momentum_shift = recent_ratio - older_ratio  # 正=买方在加速

    notes = []
    direction = 'NEUTRAL'

    if imbalance > 0.25:
        direction = 'BUY'
        notes.append(f'买方主导失衡 {imbalance:+.1%}')
    elif imbalance < -0.25:
        direction = 'SELL'
        notes.append(f'卖方主导失衡 {imbalance:+.1%}')

    if abs(momentum_shift) > 0.15:
        shift_dir = '买方加速' if momentum_shift > 0 else '卖方加速'
        notes.append(f'订单流动量转移: {shift_dir}({momentum_shift:+.2f})')

    return {
        'imbalance':      round(imbalance, 3),
        'direction':      direction,
        'buy_vol':        round(buy_vol / 1e6, 3),
        'sell_vol':       round(sell_vol / 1e6, 3),
        'momentum_shift': round(momentum_shift, 3),
        'notes':          notes,
    }

# ═══════════════════════════════════════════════════════════════
# 3. 价格停顿检测（关键位测试）
# ═══════════════════════════════════════════════════════════════

def detect_price_stall(symbol: str, key_levels: list = None) -> dict:
    """
    检测价格在关键位附近的停顿行为
    连续多根K线在同一价位附近 = 积累/分配
    """
    url = f'{FAPI}/fapi/v1/klines?symbol={symbol}&interval=5m&limit=24'
    kl  = _get(url)

    if not kl:
        return {'stall': False, 'notes': []}

    closes = [float(k[4]) for k in kl]
    highs  = [float(k[2]) for k in kl]
    lows   = [float(k[3]) for k in kl]

    price = closes[-1]

    # 检测近12根K线高点和低点的收缩
    recent_range = max(highs[-12:]) - min(lows[-12:])
    full_range   = max(highs) - min(lows)
    range_ratio  = recent_range / (full_range + 1e-9)

    notes = []
    stall = False
    stall_type = 'NONE'

    if range_ratio < 0.3:
        stall = True
        # 判断在高位还是低位
        position = (price - min(lows)) / (full_range + 1e-9)
        if position > 0.7:
            stall_type = 'TOP_DISTRIBUTION'
            notes.append(f'价格在高位盘整(波动收窄{range_ratio:.1%}) → 顶部分配 看空')
        elif position < 0.3:
            stall_type = 'BOTTOM_ACCUMULATION'
            notes.append(f'价格在低位盘整(波动收窄{range_ratio:.1%}) → 底部积累 看多')
        else:
            stall_type = 'MID_CONSOLIDATION'
            notes.append(f'中部盘整，等待方向选择')

    return {
        'stall':        stall,
        'stall_type':   stall_type,
        'range_ratio':  round(range_ratio, 3),
        'recent_range': round(recent_range, 4),
        'notes':        notes,
    }

# ═══════════════════════════════════════════════════════════════
# 4. 综合微观结构评分
# ═══════════════════════════════════════════════════════════════

def microstructure_score(symbol: str, signal_dir: str) -> dict:
    """市场微观结构综合评分 → 0~15分"""
    score = 0
    notes = []
    breakdown = {}

    # 大单吸收
    try:
        absorption = detect_absorption(symbol)
        abs_s = 0
        abs_type = absorption.get('type', 'NONE')
        if signal_dir == 'SHORT' and abs_type == 'BUY_ABSORBED':
            abs_s = 6; notes += absorption['notes']
        elif signal_dir == 'LONG' and abs_type == 'SELL_ABSORBED':
            abs_s = 6; notes += absorption['notes']
        # 动能耗尽加分
        if signal_dir == 'SHORT' and absorption.get('exhausted_buys', 0) >= 2:
            abs_s += 2; notes.append('多头动能耗尽 +2')
        elif signal_dir == 'LONG' and absorption.get('exhausted_sells', 0) >= 2:
            abs_s += 2; notes.append('空头动能耗尽 +2')
        breakdown['absorption'] = min(abs_s, 8)
        score += min(abs_s, 8)
    except Exception:
        breakdown['absorption'] = 0
        absorption = {}

    # 订单流失衡
    try:
        ofi = detect_order_flow_imbalance(symbol)
        ofi_s = 0
        if signal_dir == 'SHORT' and ofi['direction'] == 'SELL':
            ofi_s = 4; notes += ofi['notes']
        elif signal_dir == 'LONG' and ofi['direction'] == 'BUY':
            ofi_s = 4; notes += ofi['notes']
        breakdown['order_flow_imbalance'] = ofi_s
        score += ofi_s
    except Exception:
        breakdown['order_flow_imbalance'] = 0
        ofi = {}

    # 价格停顿
    try:
        stall = detect_price_stall(symbol)
        stall_s = 0
        stall_type = stall.get('stall_type', 'NONE')
        if signal_dir == 'SHORT' and stall_type == 'TOP_DISTRIBUTION':
            stall_s = 3; notes += stall['notes']
        elif signal_dir == 'LONG' and stall_type == 'BOTTOM_ACCUMULATION':
            stall_s = 3; notes += stall['notes']
        breakdown['price_stall'] = stall_s
        score += stall_s
    except Exception:
        breakdown['price_stall'] = 0
        stall = {}

    return {
        'score':      min(score, 15),
        'max':        15,
        'breakdown':  breakdown,
        'notes':      notes[:4],
        'absorption': absorption,
        'ofi':        ofi,
        'stall':      stall,
    }
