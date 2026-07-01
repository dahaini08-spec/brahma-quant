"""
cross_market_engine.py · 跨市场相关性引擎
brahma_brain · P2

覆盖：BTC / ETH / DXY / SPY(代理) / VIX(代理)
输出：
  corr_btc_eth   : BTC-ETH相关系数（高→山寨跟随强）
  dxy_signal     : DXY方向（反向指标）
  risk_on_off    : 风险偏好状态
  score          : 0~15分
"""

import json, urllib.request, time, math

FAPI  = 'https://fapi.binance.com'
CGECKO = 'https://api.coingecko.com/api/v3'

_CACHE = {}
_TTL   = 120  # 2分钟（原10分钟，修复：跨市场相关性需要更频繁更新）

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

def _get_closes(symbol: str, interval: str = '1h', limit: int = 48) -> list:
    """获取收盘价序列"""
    url = f'{FAPI}/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}'
    kl  = _get(url)
    if not kl or not isinstance(kl, list):
        return []
    return [float(k[4]) for k in kl]

def _pearson(x: list, y: list) -> float:
    """皮尔森相关系数"""
    n = min(len(x), len(y))
    if n < 10:
        return 0.0
    x, y = x[-n:], y[-n:]
    mx, my = sum(x)/n, sum(y)/n
    num = sum((xi-mx)*(yi-my) for xi,yi in zip(x,y))
    dx  = math.sqrt(sum((xi-mx)**2 for xi in x))
    dy  = math.sqrt(sum((yi-my)**2 for yi in y))
    if dx < 1e-9 or dy < 1e-9:
        return 0.0
    return round(num / (dx * dy), 3)

def _pct_returns(closes: list) -> list:
    """收益率序列"""
    if len(closes) < 2:
        return []
    return [(closes[i]-closes[i-1])/closes[i-1] for i in range(1, len(closes))]

# ═══════════════════════════════════════════════════════════════
# 1. BTC-ETH 相关性
# ═══════════════════════════════════════════════════════════════

def get_btc_eth_corr(interval: str = '1h', lookback: int = 48) -> dict:
    """
    BTC/ETH 相关系数
    >0.8 = 高相关，ETH跟随BTC方向
    <0.5 = ETH独立走势，可能有alpha
    """
    btc_c = _get_closes('BTCUSDT', interval, lookback)
    eth_c = _get_closes('ETHUSDT', interval, lookback)

    if not btc_c or not eth_c:
        return {'corr': 0.0, 'regime': 'UNKNOWN'}

    r_btc = _pct_returns(btc_c)
    r_eth = _pct_returns(eth_c)
    corr  = _pearson(r_btc, r_eth)

    if corr > 0.85:
        regime = 'HIGH_CORR'     # ETH完全跟随BTC
        note   = f'BTC-ETH高相关({corr:.2f})→ ETH跟随BTC方向'
    elif corr > 0.6:
        regime = 'MODERATE_CORR'
        note   = f'BTC-ETH中等相关({corr:.2f})'
    elif corr < 0.3:
        regime = 'DECORRELATED'  # ETH独立
        note   = f'BTC-ETH低相关({corr:.2f})→ ETH独立alpha'
    else:
        regime = 'NORMAL'
        note   = f'BTC-ETH相关({corr:.2f})'

    # BTC当前趋势
    btc_trend = 'UP' if btc_c[-1] > btc_c[-12] else 'DOWN'

    return {
        'corr':      corr,
        'regime':    regime,
        'note':      note,
        'btc_trend': btc_trend,
        'btc_price': round(btc_c[-1], 0),
        'eth_price': round(eth_c[-1], 2),
    }

# ═══════════════════════════════════════════════════════════════
# 2. DXY 代理（USDT/主要货币对代理）
# ═══════════════════════════════════════════════════════════════

def get_dxy_proxy() -> dict:
    """
    DXY代理：用 EURUSD 反向代替 DXY
    DXY↑ = 美元强 = 加密货币空头压力
    DXY↓ = 美元弱 = 加密货币多头利好

    代理：用 EUR/USD 正向（EUR强=DXY弱=加密多头）
    用 Binance EURUSDT 合约（如无则用 Stablecoin 价差代理）
    """
    # 先试 EURUSDT spot
    url  = 'https://api.binance.com/api/v3/klines?symbol=EURUSDT&interval=4h&limit=24'
    kl   = _get(url)

    if kl and isinstance(kl, list) and len(kl) >= 5:
        closes = [float(k[4]) for k in kl]
        eur_chg = (closes[-1] - closes[-5]) / closes[-5] * 100  # 20H变化
        eur_trend = 'UP' if closes[-1] > closes[-5] else 'DOWN'
        # EUR↑ = DXY↓ = 加密利好
        dxy_signal = 'BEARISH_DXY' if eur_trend == 'UP' else 'BULLISH_DXY'
        note = f'EUR/USD {closes[-1]:.4f} ({eur_chg:+.2f}%) → DXY{"弱(加密利好)" if eur_trend=="UP" else "强(加密压力)"}'
        return {
            'proxy':    'EURUSD',
            'value':    round(closes[-1], 4),
            'change':   round(eur_chg, 2),
            'trend':    eur_trend,
            'signal':   dxy_signal,
            'note':     note,
        }

    # 降级：Fear & Greed + BTC dominance 判断美元环境
    return {
        'proxy':  'unavailable',
        'signal': 'NEUTRAL',
        'note':   'DXY数据不可用',
    }

# ═══════════════════════════════════════════════════════════════
# 3. 风险偏好状态（Risk-on / Risk-off）
# ═══════════════════════════════════════════════════════════════

def get_risk_regime() -> dict:
    """
    综合判断当前市场风险偏好
    Risk-on  = 资金流向风险资产 = 加密多头
    Risk-off = 资金逃向避险资产 = 加密空头

    指标代理：
    - BTC主导率变化（↑=避险进BTC，山寨空）
    - 恐贪指数（极恐=risk-off，极贪=risk-on但过热）
    - BTC/ETH相对强弱（BTC>ETH=防御模式）
    """
    # BTC & ETH 价格变化（并发拉取，走data_cache缓存）
    with ThreadPoolExecutor(max_workers=3) as ex:
        f_btc = ex.submit(_get_closes, 'BTCUSDT', '4h', 24)
        f_eth = ex.submit(_get_closes, 'ETHUSDT', '4h', 24)
        f_fg  = ex.submit(_get, 'https://api.alternative.me/fng/?limit=1')
        btc_c = f_btc.result() or []
        eth_c = f_eth.result() or []
        try:
            fg_data = f_fg.result()
        except Exception:
            fg_data = {}

    if not btc_c or not eth_c:
        return {'regime': 'UNKNOWN', 'score': 0}

    btc_24h = (btc_c[-1] - btc_c[0]) / btc_c[0] * 100
    eth_24h = (eth_c[-1] - eth_c[0]) / eth_c[0] * 100

    # BTC outperform ETH = 防御/避险
    btc_vs_eth = btc_24h - eth_24h

    # 恐贪指数（已在并发块中拉取，直接使用）
    fg_val = 50
    if fg_data and fg_data.get('data'):
        fg_val = int(fg_data['data'][0].get('value', 50))

    # 综合判断
    risk_score = 0
    notes = []

    if fg_val >= 60:
        risk_score += 2; notes.append(f'贪婪({fg_val}) 风险偏好高')
    elif fg_val <= 25:
        risk_score -= 3; notes.append(f'极度恐惧({fg_val}) risk-off')
    elif fg_val <= 40:
        risk_score -= 1; notes.append(f'恐惧({fg_val})')

    if btc_vs_eth > 3:
        risk_score -= 1; notes.append(f'BTC跑赢ETH {btc_vs_eth:+.1f}% 防御模式')
    elif btc_vs_eth < -3:
        risk_score += 2; notes.append(f'ETH跑赢BTC {abs(btc_vs_eth):.1f}% 山寨强势')

    if btc_24h > 2:
        risk_score += 1
    elif btc_24h < -3:
        risk_score -= 2; notes.append(f'BTC 24H{btc_24h:+.1f}% 市场走弱')

    if risk_score >= 2:
        regime = 'RISK_ON'
    elif risk_score <= -2:
        regime = 'RISK_OFF'
    else:
        regime = 'NEUTRAL'

    return {
        'regime':      regime,
        'score':       risk_score,
        'fear_greed':  fg_val,
        'btc_24h':     round(btc_24h, 2),
        'eth_24h':     round(eth_24h, 2),
        'btc_vs_eth':  round(btc_vs_eth, 2),
        'notes':       notes,
    }

# ═══════════════════════════════════════════════════════════════
# 4. 综合跨市场评分
# ═══════════════════════════════════════════════════════════════

def cross_market_score(symbol: str, signal_dir: str) -> dict:
    """跨市场信号综合评分 → 0~15分"""
    score = 0
    notes = []
    breakdown = {}

    # BTC-ETH 相关性
    try:
        corr = get_btc_eth_corr()
        corr_s = 0
        btc_trend = corr.get('btc_trend', 'NEUTRAL')
        c = corr.get('corr', 0.5)

        if c > 0.8:
            # 高相关：BTC方向=ETH方向
            if signal_dir == 'SHORT' and btc_trend == 'DOWN':
                corr_s = 4; notes.append(f'BTC下跌+高相关({c:.2f}) → ETH跟跌 +4')
            elif signal_dir == 'LONG' and btc_trend == 'UP':
                corr_s = 4; notes.append(f'BTC上涨+高相关({c:.2f}) → ETH跟涨 +4')
            elif signal_dir == 'SHORT' and btc_trend == 'UP':
                corr_s = -2; notes.append(f'⚠️ BTC上涨+高相关 但做空ETH 风险 -2')
        else:
            corr_s = 1  # 中性
        breakdown['btc_eth_corr'] = max(corr_s, 0)
        score += max(corr_s, 0)
    except Exception:
        breakdown['btc_eth_corr'] = 0
        corr = {}

    # DXY代理
    try:
        dxy = get_dxy_proxy()
        dxy_s = 0
        sig = dxy.get('signal', 'NEUTRAL')
        if signal_dir == 'SHORT' and sig == 'BULLISH_DXY':
            dxy_s = 3; notes.append(dxy.get('note','') + ' +3')
        elif signal_dir == 'LONG' and sig == 'BEARISH_DXY':
            dxy_s = 3; notes.append(dxy.get('note','') + ' +3')
        elif signal_dir == 'SHORT' and sig == 'BEARISH_DXY':
            dxy_s = 0; notes.append('⚠️ ' + dxy.get('note','') + ' 不利做空')
        breakdown['dxy'] = dxy_s
        score += dxy_s
    except Exception:
        breakdown['dxy'] = 0
        dxy = {}

    # 风险偏好
    try:
        risk = get_risk_regime()
        risk_s = 0
        regime = risk.get('regime', 'NEUTRAL')
        if signal_dir == 'SHORT' and regime == 'RISK_OFF':
            risk_s = 5; notes += risk.get('notes', [])
        elif signal_dir == 'LONG' and regime == 'RISK_ON':
            risk_s = 5; notes += risk.get('notes', [])
        elif signal_dir == 'SHORT' and regime == 'NEUTRAL':
            risk_s = 2
        breakdown['risk_regime'] = risk_s
        score += risk_s
    except Exception:
        breakdown['risk_regime'] = 0
        risk = {}

    return {
        'score':     min(score, 15),
        'max':       15,
        'breakdown': breakdown,
        'notes':     notes[:4],
        'corr':      corr,
        'dxy':       dxy,
        'risk':      risk,
    }


# ═══════════════════════════════════════════════════════════════
# [s_cross 2026-07-01] 跨所资金费率 + Perp/Spot Basis
# 设计院·四方共识落地：外部路由P0级，免费公开，零额外成本
# ═══════════════════════════════════════════════════════════════

def get_cross_fr_basis(symbol: str = 'BTCUSDT') -> dict:
    """
    跨所资金费率对比 + Perp/Spot Basis

    返回：
      bybit_fr      : Bybit 资金费率（%）
      okx_fr        : OKX 资金费率（%）
      binance_fr    : Binance 资金费率（%）
      fr_avg        : 三所均值（%）
      fr_extreme    : 是否极值（>0.008%=极高多头成本）
      basis_pct     : Perp/Spot 贴水率（负=贴水=空头主导）
      bias          : SHORT_FAVORABLE / LONG_FAVORABLE / NEUTRAL
      score_adj     : 评分调整建议（做空方向视角）
      note          : 描述
    """
    cache_key = f'cross_fr_{symbol}'
    now = time.time()
    if cache_key in _CACHE and now - _CACHE[cache_key]['ts'] < 60:
        return _CACHE[cache_key]['data']

    sym_base = symbol.replace('USDT', '')
    result = {
        'bybit_fr': 0.0, 'okx_fr': 0.0, 'binance_fr': 0.0,
        'fr_avg': 0.0, 'fr_extreme': False,
        'basis_pct': 0.0, 'bias': 'NEUTRAL',
        'score_adj': 0, 'note': 'N/A',
    }

    # ── Bybit FR ──
    try:
        bybit_sym = symbol  # BTCUSDT → BTCUSDT
        r = _get(f'https://api.bybit.com/v5/market/tickers?category=linear&symbol={bybit_sym}')
        if r and r.get('result', {}).get('list'):
            result['bybit_fr'] = round(float(r['result']['list'][0]['fundingRate']) * 100, 5)
    except Exception:
        pass

    # ── OKX FR ──
    try:
        okx_inst = f'{sym_base}-USDT-SWAP'
        r = _get(f'https://www.okx.com/api/v5/public/funding-rate?instId={okx_inst}')
        if r and r.get('data'):
            result['okx_fr'] = round(float(r['data'][0]['fundingRate']) * 100, 5)
    except Exception:
        pass

    # ── Binance FR ──
    try:
        r = _get(f'https://fapi.binance.com/fapi/v1/premiumIndex?symbol={symbol}')
        if r and r.get('lastFundingRate'):
            result['binance_fr'] = round(float(r['lastFundingRate']) * 100, 5)
    except Exception:
        pass

    # ── Perp/Spot Basis ──
    try:
        spot_r = _get(f'https://api.binance.com/api/v3/ticker/price?symbol={symbol}')
        perp_r = _get(f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={symbol}')
        if spot_r and perp_r:
            spot_p = float(spot_r['price'])
            perp_p = float(perp_r['price'])
            result['basis_pct'] = round((perp_p - spot_p) / spot_p * 100, 4) if spot_p > 0 else 0.0
    except Exception:
        pass

    # ── 综合评估 ──
    fr_vals = [v for v in [result['bybit_fr'], result['okx_fr'], result['binance_fr']] if v != 0]
    if fr_vals:
        result['fr_avg'] = round(sum(fr_vals) / len(fr_vals), 5)

    # 极值判断：任一交易所FR > 0.008% = 多头成本极重
    result['fr_extreme'] = any(v > 0.008 for v in [result['bybit_fr'], result['okx_fr']])

    # 评分逻辑（做空方向为正）
    score_adj = 0
    notes = []

    # 高FR + 做空 → 多头每小时被扣费，趋势加速
    max_fr = max([result['bybit_fr'], result['okx_fr']], default=0)
    if max_fr >= 0.010:
        score_adj += 5; notes.append(f'跨所FR极高({max_fr:.4f}%) 多头成本压垮 +5')
    elif max_fr >= 0.007:
        score_adj += 3; notes.append(f'跨所FR偏高({max_fr:.4f}%) +3')
    elif max_fr >= 0.004:
        score_adj += 1; notes.append(f'跨所FR轻微偏高({max_fr:.4f}%) +1')

    # Basis贴水 + 做空 → 机构不愿做多期货，空头主导
    b = result['basis_pct']
    if b <= -0.08:
        score_adj += 4; notes.append(f'Basis深度贴水({b:.3f}%) 机构空头主导 +4')
    elif b <= -0.04:
        score_adj += 3; notes.append(f'Basis贴水({b:.3f}%) 空头确认 +3')
    elif b <= -0.01:
        score_adj += 1; notes.append(f'Basis轻微贴水({b:.3f}%) +1')
    elif b >= 0.08:
        score_adj -= 3; notes.append(f'Basis升水({b:.3f}%) 多头期货溢价 -3')
    elif b >= 0.04:
        score_adj -= 1; notes.append(f'Basis轻微升水({b:.3f}%) -1')

    result['score_adj'] = score_adj
    result['note'] = ' | '.join(notes) if notes else f'FR均值={result["fr_avg"]:.4f}% Basis={b:.3f}%'

    if score_adj >= 3:
        result['bias'] = 'SHORT_FAVORABLE'
    elif score_adj <= -2:
        result['bias'] = 'LONG_FAVORABLE'
    else:
        result['bias'] = 'NEUTRAL'

    _CACHE[cache_key] = {'data': result, 'ts': now}
    return result


# ═══════════════════════════════════════════════════════════════
# [s_options 2026-07-01] Deribit Put/Call OI Ratio
# 设计院·四方共识落地：免费公开API，期权市场情绪层
# ═══════════════════════════════════════════════════════════════

def get_deribit_pc(symbol: str = 'BTCUSDT') -> dict:
    """
    Deribit Put/Call 未平仓量比率

    解读（结合体制，非孤立信号）：
      BEAR_TREND + P/C OI < 0.7 → Call重 = 做市商负Gamma持续，价格波动放大指向下方
      BEAR_TREND + P/C OI > 1.2 → Put重 = 市场已充分定价看跌，反弹风险增加
    返回：
      pc_oi_ratio : Put/Call OI比率
      put_oi      : Put未平仓量（合约数）
      call_oi     : Call未平仓量
      signal      : CALL_HEAVY / PUT_HEAVY / BALANCED
      score_adj   : 评分调整（做空方向视角，结合体制）
      note        : 描述
    """
    cache_key = f'deribit_pc_{symbol}'
    now = time.time()
    if cache_key in _CACHE and now - _CACHE[cache_key]['ts'] < 300:  # 5分钟缓存
        return _CACHE[cache_key]['data']

    currency = 'BTC' if 'BTC' in symbol else 'ETH'
    default = {'pc_oi_ratio': 0.0, 'put_oi': 0, 'call_oi': 0,
               'signal': 'UNKNOWN', 'score_adj': 0, 'note': 'Deribit unavailable'}

    try:
        url = f'https://deribit.com/api/v2/public/get_book_summary_by_currency?currency={currency}&kind=option'
        data = _get(url, timeout=10)
        if not data or not data.get('result'):
            _CACHE[cache_key] = {'data': default, 'ts': now}
            return default

        put_oi  = sum(float(x.get('open_interest', 0)) for x in data['result'] if '-P' in x.get('instrument_name', ''))
        call_oi = sum(float(x.get('open_interest', 0)) for x in data['result'] if '-C' in x.get('instrument_name', ''))

        if call_oi <= 0:
            _CACHE[cache_key] = {'data': default, 'ts': now}
            return default

        pc_ratio = round(put_oi / call_oi, 3)

        if pc_ratio < 0.6:
            signal = 'CALL_HEAVY'
        elif pc_ratio > 1.2:
            signal = 'PUT_HEAVY'
        else:
            signal = 'BALANCED'

        # 评分逻辑（BEAR_TREND下）：
        # Call重(P/C<0.6) + 做空 → 做市商负Gamma → 价格下跌时做市商被迫抛售 → +2
        # Put重(P/C>1.2) + 做空 → 已过度悲观，可能反弹 → -2
        score_adj = 0
        note = ''
        if signal == 'CALL_HEAVY':
            score_adj = 2
            note = f'P/C OI={pc_ratio:.2f}(Call重) 做市商负Gamma加速下跌 +2'
        elif signal == 'PUT_HEAVY':
            score_adj = -2
            note = f'P/C OI={pc_ratio:.2f}(Put重) 悲观过度，反弹风险 -2'
        else:
            score_adj = 0
            note = f'P/C OI={pc_ratio:.2f} 期权市场平衡 ±0'

        result = {
            'pc_oi_ratio': pc_ratio,
            'put_oi': round(put_oi, 0),
            'call_oi': round(call_oi, 0),
            'signal': signal,
            'score_adj': score_adj,
            'note': note,
            'currency': currency,
        }
        _CACHE[cache_key] = {'data': result, 'ts': now}
        return result

    except Exception as e:
        default['note'] = f'Deribit error: {str(e)[:40]}'
        _CACHE[cache_key] = {'data': default, 'ts': now}
        return default
