"""

# ── STATUS: AUXILIARY ──────────────────────────────────────────
# 大户引擎，smart_money辅助
# LAST_REVIEW: 2026-07-01 | 属于辅助计算层，修改前确认调用链
# ─────────────────────────────────────────────────────────────
whale_engine.py · 链上巨鲸 & 交易所资金流引擎
brahma_brain · P2

数据源（免费公开）:
  1. CoinGlass  - 交易所净流入/净流出（BTC/ETH）
  2. Binance    - 大额成交聚合（aggTrades 筛选）
  3. CoinGecko  - 链上持仓变化代理
  4. Whale Alert 公开 RSS（无需API Key）
  5. [P2 2026-05-22] Binance FAPI - 衍生品聪明钱（资金费率+多空比+OI）

输出:
  exchange_flow: 净流入(负=流出=看多) / 净流出(正=流入=看空)
  whale_buys / whale_sells: 近1H大单方向
  smart_money_signal: 综合判断
  score: 0~20分（P2升级: +5分衍生品维度）
"""

import json, urllib.request, time

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



CG_BASE = 'https://open-api.coinglass.com/public/v2'
FAPI    = 'https://fapi.binance.com'
CGECKO  = 'https://api.coingecko.com/api/v3'

_CACHE = {}
_CACHE_TTL = 300  # 5分钟缓存

def _get(url: str, timeout: int = 8) -> dict | list | None:
    now = time.time()
    if url in _CACHE and now - _CACHE[url]['ts'] < _CACHE_TTL:
        return _CACHE[url]['data']
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0',
            'Accept': 'application/json',
        })
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
            _CACHE[url] = {'data': data, 'ts': now}
            return data
    except Exception:
        return None

# ═══════════════════════════════════════════════════════════════
# 1. 交易所净流入/流出（CoinGlass）
# ═══════════════════════════════════════════════════════════════

def get_exchange_flow(symbol: str) -> dict:
    """
    获取主要交易所 ETH/BTC 净流入流出
    净流出(负值) = 提币离场 = 看多信号（减少抛压）
    净流入(正值) = 入金备售 = 看空信号（增加抛压）
    """
    sym = symbol.replace('USDT', '').upper()
    url = f'{CG_BASE}/exchange/net-inflow?symbol={sym}&intervalType=h1'
    data = _get(url)

    if not data or not isinstance(data, dict):
        # 降级：用 Binance 大额转账代理
        return _fallback_exchange_flow(symbol)

    try:
        d = data.get('data', {})
        # CoinGlass 格式: allExchange.inflow / outflow
        all_ex = d.get('allExchange', {})
        inflow  = float(all_ex.get('inflow', 0) or 0)
        outflow = float(all_ex.get('outflow', 0) or 0)
        net = inflow - outflow  # 正=净流入(看空), 负=净流出(看多)
        notes = []
        if net < -1e6:
            notes.append(f'链上净流出 ${abs(net)/1e6:.1f}M → 减少抛压 看多')
        elif net > 1e6:
            notes.append(f'链上净流入 ${net/1e6:.1f}M → 增加抛压 看空')
        return {
            'inflow':  round(inflow / 1e6, 2),
            'outflow': round(outflow / 1e6, 2),
            'net':     round(net / 1e6, 2),
            'notes':   notes,
        }
    except Exception:
        return _fallback_exchange_flow(symbol)


def _fallback_exchange_flow(symbol: str) -> dict:
    """降级：用 Binance 大额 aggTrades 作为代理"""
    url = f'{FAPI}/fapi/v1/aggTrades?symbol={symbol}&limit=1000'
    trades = _get(url)
    if not trades:
        return {'inflow': 0, 'outflow': 0, 'net': 0, 'notes': ['数据不可用']}

    # 大额成交（>10万美元）
    price_url = f'{FAPI}/fapi/v1/ticker/price?symbol={symbol}'
    p_data = _get(price_url)
    price = float(p_data['price']) if p_data else 2000

    big_buy = big_sell = 0
    for t in trades:
        qty = float(t['q']) * price
        if qty >= 100000:  # 10万美元以上
            if not t['m']:  # 买方主动
                big_buy += qty
            else:
                big_sell += qty

    net = big_buy - big_sell
    notes = []
    if big_buy > big_sell * 1.5:
        notes.append(f'大单净买入 ${(big_buy-big_sell)/1e6:.2f}M → 机构积累')
    elif big_sell > big_buy * 1.5:
        notes.append(f'大单净卖出 ${(big_sell-big_buy)/1e6:.2f}M → 机构减仓')

    return {
        'inflow':  round(big_sell / 1e6, 3),
        'outflow': round(big_buy / 1e6, 3),
        'net':     round(-net / 1e6, 3),  # 负=净买入(看多)
        'notes':   notes,
        'source':  'aggTrades_proxy',
    }

# ═══════════════════════════════════════════════════════════════
# 2. 巨鲸地址动向（用 Binance 大额成交 + Taker 综合判断）
# ═══════════════════════════════════════════════════════════════

def get_whale_activity(symbol: str) -> dict:
    """
    巨鲸活动分析
    - 近5分钟大额聚合成交（>50万美元）
    - Taker 方向 vs 小单方向分化
    """
    # 最近500笔聚合成交
    url = f'{FAPI}/fapi/v1/aggTrades?symbol={symbol}&limit=500'
    trades = _get(url)

    price_url = f'{FAPI}/fapi/v1/ticker/price?symbol={symbol}'
    p_data = _get(price_url)
    price = float(p_data['price']) if p_data else 2000

    if not trades:
        return {'whale_dir': 'NEUTRAL', 'big_buy': 0, 'big_sell': 0, 'notes': []}

    SMALL_THRESHOLD = 10000   # <1万美元 = 散户
    BIG_THRESHOLD   = 500000  # >50万美元 = 巨鲸

    small_buy = small_sell = big_buy = big_sell = 0

    for t in trades:
        qty_usd = float(t['q']) * price
        is_buy  = not t['m']
        if qty_usd < SMALL_THRESHOLD:
            if is_buy: small_buy += qty_usd
            else:      small_sell += qty_usd
        elif qty_usd >= BIG_THRESHOLD:
            if is_buy: big_buy += qty_usd
            else:      big_sell += qty_usd

    whale_net = big_buy - big_sell
    retail_net = small_buy - small_sell

    # 巨鲸 vs 散户方向相反 = 经典信号
    diverge = (whale_net > 0 and retail_net < 0) or (whale_net < 0 and retail_net > 0)

    if big_buy + big_sell < 1000:
        whale_dir = 'NEUTRAL'
    elif whale_net > 0:
        whale_dir = 'BUY'
    else:
        whale_dir = 'SELL'

    notes = []
    if diverge and abs(whale_net) > 100000:
        if whale_dir == 'BUY':
            notes.append(f'巨鲸净买 ${whale_net/1e6:.2f}M vs 散户卖 → 机构吸筹')
        else:
            notes.append(f'巨鲸净卖 ${abs(whale_net)/1e6:.2f}M vs 散户买 → 机构出货')
    elif whale_dir == 'BUY' and big_buy > 500000:
        notes.append(f'巨鲸积极买入 ${big_buy/1e6:.2f}M')
    elif whale_dir == 'SELL' and big_sell > 500000:
        notes.append(f'巨鲸积极卖出 ${big_sell/1e6:.2f}M')

    return {
        'whale_dir':   whale_dir,
        'big_buy':     round(big_buy / 1e6, 3),
        'big_sell':    round(big_sell / 1e6, 3),
        'whale_net':   round(whale_net / 1e6, 3),
        'retail_net':  round(retail_net / 1e6, 3),
        'diverge':     diverge,
        'notes':       notes,
    }

# ═══════════════════════════════════════════════════════════════
# 3. 综合鲸鱼评分
# ═══════════════════════════════════════════════════════════════

def whale_score(symbol: str, signal_dir: str) -> dict:
    """综合链上鲸鱼信号评分 → 0~15分"""
    score = 0
    notes = []
    breakdown = {}

    # 交易所流向
    try:
        flow = get_exchange_flow(symbol)
        net  = flow.get('net', 0)
        flow_s = 0
        if signal_dir == 'SHORT' and net > 0.5:    # 净流入=看空
            flow_s = 5; notes += flow['notes']
        elif signal_dir == 'LONG' and net < -0.5:   # 净流出=看多
            flow_s = 5; notes += flow['notes']
        elif signal_dir == 'SHORT' and net > 0.1:
            flow_s = 2
        elif signal_dir == 'LONG' and net < -0.1:
            flow_s = 2
        breakdown['exchange_flow'] = flow_s
        score += flow_s
    except Exception:
        breakdown['exchange_flow'] = 0
        flow = {}

    # 巨鲸方向
    try:
        whale = get_whale_activity(symbol)
        whale_dir = whale.get('whale_dir', 'NEUTRAL')
        whale_s = 0
        if signal_dir == 'SHORT' and whale_dir == 'SELL':
            whale_s = 6
            if whale.get('diverge'):
                whale_s = 8  # 巨鲸出货+散户接盘 = 最强信号
            notes += whale['notes']
        elif signal_dir == 'LONG' and whale_dir == 'BUY':
            whale_s = 6
            if whale.get('diverge'):
                whale_s = 8
            notes += whale['notes']
        breakdown['whale_activity'] = whale_s
        score += whale_s
    except Exception:
        breakdown['whale_activity'] = 0
        whale = {}

    return {
        'score':     min(score, 15),
        'max':       15,
        'breakdown': breakdown,
        'notes':     notes,
        'flow':      flow,
        'whale':     whale,
    }


# ═══════════════════════════════════════════════════════════════
# 4. [P2 2026-05-22] 衍生品聪明钱信号（Binance FAPI 公开接口）
#    资金费率方向 + 多空比分化 + OI变化
# ═══════════════════════════════════════════════════════════════

def get_derivatives_smart_money(symbol: str, signal_dir: str) -> dict:
    """
    [P2] 衍生品聪明钱（真实数据源： Binance FAPI 公开接口）

    三个维度：
      1. 资金费率方向：负费率=多头付空头=市场偏多；正费率=空头付多头=市场偏空
      2. 多空比分化：多空比远面>2.5=多头凥热（小心働空）；<0.4=空头凥热（小心做多）
      3. OI 5分钟变化：裁减且方向匹配=平仓压力方向一致

    输出 score: 0~5分（叠加到 whale_score 的 0~15 上，总上限 0~20）
    """
    score = 0
    notes = []
    breakdown = {}

    # ── 维度1: 资金费率 ───────────────────────────────
    try:
        url_fr = f'{FAPI}/fapi/v1/fundingRate?symbol={symbol}&limit=3'
        fr_data = _get(url_fr)
        fr_s = 0
        fr_note = ''
        if fr_data and isinstance(fr_data, list) and len(fr_data) > 0:
            latest_fr = float(fr_data[-1].get('fundingRate', 0))
            # 负费率感=多头付空头=市场偏多，加空分；正费率=空头付多头=市场偏空，加多分
            if signal_dir == 'SHORT' and latest_fr > 0.0003:
                fr_s = 2
                fr_note = f'资金费率+{latest_fr*100:.4f}%（多头付费，凥热利空）'
            elif signal_dir == 'SHORT' and latest_fr > 0.0001:
                fr_s = 1
                fr_note = f'资金费率+{latest_fr*100:.4f}%（偶数偏多）'
            elif signal_dir == 'LONG' and latest_fr < -0.0003:
                fr_s = 2
                fr_note = f'资金费率{latest_fr*100:.4f}%（空头付费，凥热利多）'
            elif signal_dir == 'LONG' and latest_fr < -0.0001:
                fr_s = 1
                fr_note = f'资金费率{latest_fr*100:.4f}%（偶数偏空）'
            if fr_note:
                notes.append(fr_note)
        breakdown['funding_rate'] = fr_s
        score += fr_s
    except Exception:
        breakdown['funding_rate'] = 0

    # ── 维度2: 多空比 ────────────────────────────────
    try:
        url_lsr = f'{FAPI}/futures/data/globalLongShortAccountRatio?symbol={symbol}&period=5m&limit=3'
        lsr_data = _get(url_lsr)
        lsr_s = 0
        if lsr_data and isinstance(lsr_data, list) and len(lsr_data) > 0:
            lsr = float(lsr_data[-1].get('longShortRatio', 1.0))
            # 多空比远面时小心反向操作
            if signal_dir == 'SHORT' and lsr > 2.5:
                lsr_s = 2
                notes.append(f'多空比={lsr:.2f}（多头凥热，裁多香を小心）')
            elif signal_dir == 'SHORT' and lsr > 1.8:
                lsr_s = 1
            elif signal_dir == 'LONG' and lsr < 0.4:
                lsr_s = 2
                notes.append(f'多空比={lsr:.2f}（空头凥热，裁空香を小心）')
            elif signal_dir == 'LONG' and lsr < 0.7:
                lsr_s = 1
        breakdown['long_short_ratio'] = lsr_s
        score += lsr_s
    except Exception:
        breakdown['long_short_ratio'] = 0

    # ── 维度3: OI 5分钟变化 ─────────────────────────
    try:
        url_oi = f'{FAPI}/futures/data/openInterestHist?symbol={symbol}&period=5m&limit=6'
        oi_data = _get(url_oi)
        oi_s = 0
        if oi_data and isinstance(oi_data, list) and len(oi_data) >= 4:
            oi_new = float(oi_data[-1].get('sumOpenInterest', 0))
            oi_old = float(oi_data[-4].get('sumOpenInterest', 0))
            oi_chg = (oi_new - oi_old) / max(oi_old, 1e-9)
            # OI下降（裁减）时：方向匹配=平仓压力一致
            # OI上升（新开）：方向匹配=势头活跃
            if signal_dir == 'SHORT' and oi_chg < -0.005:
                oi_s = 1
                notes.append(f'OI轻微下降{oi_chg*100:.2f}%（多头平仓压力）')
            elif signal_dir == 'SHORT' and oi_chg > 0.005:
                # 空头新开，对空信号有利
                oi_s = 1
                notes.append(f'OI上升{oi_chg*100:.2f}%（空头新开，势头活跃）')
            elif signal_dir == 'LONG' and oi_chg < -0.005:
                oi_s = 1
                notes.append(f'OI下降{oi_chg*100:.2f}%（空头平仓压力）')
            elif signal_dir == 'LONG' and oi_chg > 0.005:
                oi_s = 1
                notes.append(f'OI上升{oi_chg*100:.2f}%（多头新开，势头活跃）')
        breakdown['oi_change'] = oi_s
        score += oi_s
    except Exception:
        breakdown['oi_change'] = 0

    return {
        'score':     min(score, 5),
        'max':       5,
        'breakdown': breakdown,
        'notes':     notes,
        'source':    'binance_fapi_realtime',
    }


def whale_score(symbol: str, signal_dir: str) -> dict:
    """综合鲸鱼+衍生品聪明钱信号评分 → 0~20分
    [P2 2026-05-22] 旧 0~15 升级至 0~20：+5分 binance_fapi 衍生品维度
    """
    score = 0
    notes = []
    breakdown = {}

    # 交易所流向
    try:
        flow = get_exchange_flow(symbol)
        net  = flow.get('net', 0)
        flow_s = 0
        if signal_dir == 'SHORT' and net > 0.5:    # 净流入=看空
            flow_s = 5; notes += flow['notes']
        elif signal_dir == 'LONG' and net < -0.5:   # 净流出=看多
            flow_s = 5; notes += flow['notes']
        elif signal_dir == 'SHORT' and net > 0.1:
            flow_s = 2
        elif signal_dir == 'LONG' and net < -0.1:
            flow_s = 2
        breakdown['exchange_flow'] = flow_s
        score += flow_s
    except Exception:
        breakdown['exchange_flow'] = 0
        flow = {}

    # 巨鲸方向
    try:
        whale = get_whale_activity(symbol)
        whale_dir = whale.get('whale_dir', 'NEUTRAL')
        whale_s = 0
        if signal_dir == 'SHORT' and whale_dir == 'SELL':
            whale_s = 6
            if whale.get('diverge'):
                whale_s = 8  # 巨鲸出货+散户接盘 = 最强信号
            notes += whale['notes']
        elif signal_dir == 'LONG' and whale_dir == 'BUY':
            whale_s = 6
            if whale.get('diverge'):
                whale_s = 8
            notes += whale['notes']
        breakdown['whale_activity'] = whale_s
        score += whale_s
    except Exception:
        breakdown['whale_activity'] = 0
        whale = {}

    # [P2] 衍生品聪明钱（资金费率+多空比+OI）— Binance FAPI 公开接口
    try:
        deriv = get_derivatives_smart_money(symbol, signal_dir)
        d_s = deriv.get('score', 0)
        notes += deriv.get('notes', [])
        breakdown['derivatives_sm'] = d_s
        score += d_s
    except Exception:
        breakdown['derivatives_sm'] = 0
        deriv = {}

    return {
        'score':     min(score, 20),   # P2升级: 上限 15 → 20
        'max':       20,
        'breakdown': breakdown,
        'notes':     notes,
        'flow':      flow,
        'whale':     whale,
        'derivatives': deriv,
        'source':    'whale_engine_v2_p2',
    }
