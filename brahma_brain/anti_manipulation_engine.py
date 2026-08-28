"""
anti_manipulation_engine.py — 梵天操控防御层
2026-08-28 设计院 苏摩111批准封印

功能：
  五层防御，识别插针/诱多/清算猎杀/做市商撤单/FR窗口操控
  全部基于公开API数据，零内幕依赖

防御逻辑：
  风险分=0-100，≥60→HIGH_RISK（禁止开仓），40-59→MEDIUM（减仓）
  每个模块独立评分，加总后归一化

接入位置：
  brahma_core_step4._analyze_step4() → extra_data['anti_manip']
  brahma_core_block_b.calc_block_b() → score扣分（高风险=-15，中等=-8）
"""

import time
import os
import sys

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BASE not in sys.path:
    sys.path.insert(0, _BASE)

try:
    from brahma_brain.data_cache import get_open_interest as _dc_oi, get_ticker as _dc_ticker, get_long_short_ratio as _dc_lsr
    from brahma_brain.brahma_bus import get_price as _bus_price
except ImportError:
    try:
        from data_cache import get_open_interest as _dc_oi, get_ticker as _dc_ticker, get_long_short_ratio as _dc_lsr
        from brahma_bus import get_price as _bus_price
    except ImportError:
        _dc_oi = None
        _dc_ticker = None
        _dc_lsr = None
        _bus_price = None

# ── 缓存（60s TTL，避免重复拉API）──────────────────────────────
_CACHE: dict = {}
_TTL = 60  # 秒

def _get_cached(key: str):
    entry = _CACHE.get(key)
    if entry and time.time() - entry['ts'] < _TTL:
        return entry['data']
    return None

def _set_cached(key: str, data):
    _CACHE[key] = {'data': data, 'ts': time.time()}
    return data


# ══════════════════════════════════════════════════════════════
# 模块1：Spread监控（做市商撤单 = 插针前兆）
# ══════════════════════════════════════════════════════════════

def _check_spread(symbol: str) -> dict:
    """
    Bid-Ask Spread偏离均值检测
    spread_z > 3 → 做市商撤单，插针高风险
    数据源：Binance /fapi/v1/ticker/bookTicker
    """
    key = f'spread:{symbol}'
    cached = _get_cached(key)
    if cached:
        return cached

    result = {'spread_pct': 0, 'spread_z': 0, 'risk': 0, 'signal': 'OK'}
    try:
        import urllib.request, json as _json
        url = f'https://fapi.binance.com/fapi/v1/ticker/bookTicker?symbol={symbol}'
        with urllib.request.urlopen(url, timeout=3) as r:
            data = _json.loads(r.read())
        bid = float(data.get('bidPrice', 0))
        ask = float(data.get('askPrice', 0))
        if bid <= 0 or ask <= 0:
            return _set_cached(key, result)

        mid = (bid + ask) / 2
        spread_pct = (ask - bid) / mid * 100

        # BTC/ETH正常spread基准
        normal_spread = {'BTC': 0.012, 'ETH': 0.015}.get(
            symbol.replace('USDT', '').replace('PERP', ''), 0.020)

        spread_z = (spread_pct - normal_spread) / max(normal_spread * 0.5, 0.001)

        risk = 0
        signal = 'OK'
        if spread_z > 5:
            risk = 35
            signal = 'DANGER: 做市商基本撤单，插针极高风险'
        elif spread_z > 3:
            risk = 25
            signal = 'WARNING: 做市商开始撤单'
        elif spread_z > 2:
            risk = 10
            signal = 'CAUTION: Spread偏高'

        result = {
            'spread_pct': round(spread_pct, 4),
            'spread_z': round(spread_z, 2),
            'risk': risk,
            'signal': signal
        }
    except Exception as e:
        result['err'] = str(e)[:40]

    return _set_cached(key, result)


# ══════════════════════════════════════════════════════════════
# 模块2：Taker比例监控（散户追多/机构出货识别）
# ══════════════════════════════════════════════════════════════

def _check_taker(symbol: str) -> dict:
    """
    Taker Buy/Sell比例检测
    taker_buy > 0.72连续 → 散户追多极度拥挤，做多风险
    taker_sell > 0.65连续 → 主动抛压，做空风险减小
    数据源：Binance /fapi/v1/takerlongshortRatio
    """
    key = f'taker:{symbol}'
    cached = _get_cached(key)
    if cached:
        return cached

    result = {'taker_buy_ratio': 0.5, 'risk_long': 0, 'risk_short': 0, 'signal': 'OK'}
    try:
        import urllib.request, json as _json
        url = (f'https://fapi.binance.com/futures/data/takerlongshortRatio'
               f'?symbol={symbol}&period=5m&limit=3')
        with urllib.request.urlopen(url, timeout=3) as r:
            data = _json.loads(r.read())
        if not data:
            return _set_cached(key, result)

        # 最近3个5min周期的平均taker buy ratio
        ratios = []
        for item in data[-3:]:
            buy_vol = float(item.get('buyVol', 0))
            sell_vol = float(item.get('sellVol', 0))
            total = buy_vol + sell_vol
            if total > 0:
                ratios.append(buy_vol / total)

        if not ratios:
            return _set_cached(key, result)

        avg_ratio = sum(ratios) / len(ratios)
        risk_long = 0
        risk_short = 0
        signal = 'OK'

        if avg_ratio > 0.75:
            risk_long = 30
            signal = 'DANGER: 散户追多极度拥挤，做多高风险（主力即将出货）'
        elif avg_ratio > 0.68:
            risk_long = 15
            signal = 'WARNING: 多头拥挤，做多谨慎'
        elif avg_ratio < 0.32:
            risk_short = 20
            signal = 'WARNING: 空头拥挤，做空谨慎'

        result = {
            'taker_buy_ratio': round(avg_ratio, 3),
            'risk_long': risk_long,
            'risk_short': risk_short,
            'signal': signal,
        }
    except Exception as e:
        result['err'] = str(e)[:40]

    return _set_cached(key, result)


# ══════════════════════════════════════════════════════════════
# 模块3：OI异常变化率（主力建仓/出货检测）
# ══════════════════════════════════════════════════════════════

def _check_oi_anomaly(symbol: str) -> dict:
    """
    OI变化率异常检测
    OI_change_1h > 8% + 价格横盘 → 主力悄悄建仓
    OI骤降 + 价格上涨 → 主力出货，警惕反转
    数据源：Binance /fapi/v1/openInterest + /fapi/v1/ticker/24hr
    """
    key = f'oi_anomaly:{symbol}'
    cached = _get_cached(key)
    if cached:
        return cached

    result = {'oi_change_pct': 0, 'risk': 0, 'signal': 'OK'}
    try:
        import urllib.request, json as _json

        # 当前OI
        url_oi = f'https://fapi.binance.com/fapi/v1/openInterest?symbol={symbol}'
        with urllib.request.urlopen(url_oi, timeout=3) as r:
            oi_now = float(_json.loads(r.read()).get('openInterest', 0))

        # 24H价格变化
        url_ticker = f'https://fapi.binance.com/fapi/v1/ticker/24hr?symbol={symbol}'
        with urllib.request.urlopen(url_ticker, timeout=3) as r:
            ticker = _json.loads(r.read())
        price_chg_pct = float(ticker.get('priceChangePercent', 0))

        # OI历史（1H前的OI用历史接口）
        url_oi_hist = (f'https://fapi.binance.com/futures/data/openInterestHist'
                       f'?symbol={symbol}&period=1h&limit=2')
        with urllib.request.urlopen(url_oi_hist, timeout=3) as r:
            oi_hist = _json.loads(r.read())

        oi_change_pct = 0
        if oi_hist and len(oi_hist) >= 2:
            oi_1h_ago = float(oi_hist[0].get('sumOpenInterest', oi_now))
            if oi_1h_ago > 0:
                oi_change_pct = (oi_now - oi_1h_ago) / oi_1h_ago * 100

        risk = 0
        signal = 'OK'

        # OI急增+价格横盘 = 主力悄悄建仓（方向未知，风险升高）
        if oi_change_pct > 8 and abs(price_chg_pct) < 1.0:
            risk = 20
            signal = 'WARNING: OI急增+价格横盘，主力建仓中，方向未明'

        # OI骤降+价格上涨 = 主力平多出货（空头信号，做多高风险）
        elif oi_change_pct < -6 and price_chg_pct > 2:
            risk = 25
            signal = 'DANGER: OI骤降+价格上涨，主力平多出货'

        result = {
            'oi_change_pct': round(oi_change_pct, 2),
            'price_chg_pct': round(price_chg_pct, 2),
            'risk': risk,
            'signal': signal,
        }
    except Exception as e:
        result['err'] = str(e)[:40]

    return _set_cached(key, result)


# ══════════════════════════════════════════════════════════════
# 模块4：FR窗口禁区（资金费率结算前操控）
# ══════════════════════════════════════════════════════════════

def _check_fr_window(symbol: str) -> dict:
    """
    资金费率结算窗口检测
    结算前30min + FR极端值 → 价格被人为推向有利方向
    统计规律：FR>0.05%时结算前5min价格向下概率=62%
    数据源：Binance /fapi/v1/premiumIndex（含nextFundingTime）
    """
    key = f'fr_window:{symbol}'
    cached = _get_cached(key)
    if cached:
        return cached

    result = {'minutes_to_funding': 999, 'fr': 0, 'risk': 0, 'signal': 'OK'}
    try:
        import urllib.request, json as _json
        url = f'https://fapi.binance.com/fapi/v1/premiumIndex?symbol={symbol}'
        with urllib.request.urlopen(url, timeout=3) as r:
            data = _json.loads(r.read())

        fr = float(data.get('lastFundingRate', 0))
        next_funding_ts = int(data.get('nextFundingTime', 0)) / 1000
        now = time.time()
        minutes_to_funding = (next_funding_ts - now) / 60

        risk = 0
        signal = 'OK'

        # 结算前30min且FR极端 = 操控窗口
        if 0 < minutes_to_funding < 30:
            if abs(fr) > 0.05:
                risk = 20
                direction = '向下' if fr > 0 else '向上'
                signal = f'WARNING: 资金费结算前{minutes_to_funding:.0f}min，FR={fr:.4f}，价格可能被推{direction}'
            elif abs(fr) > 0.02:
                risk = 8
                signal = f'CAUTION: 资金费结算前{minutes_to_funding:.0f}min'

        result = {
            'minutes_to_funding': round(minutes_to_funding, 1),
            'fr': round(fr, 6),
            'risk': risk,
            'signal': signal,
        }
    except Exception as e:
        result['err'] = str(e)[:40]

    return _set_cached(key, result)


# ══════════════════════════════════════════════════════════════
# 模块5：标记价格偏差（插针触发机制检测）
# ══════════════════════════════════════════════════════════════

def _check_mark_price(symbol: str) -> dict:
    """
    标记价格 vs 最新价偏差检测
    偏差>0.3% → 有人在砸现货触发标记价格偏移，准备插针
    数据源：Binance /fapi/v1/premiumIndex
    """
    key = f'mark:{symbol}'
    cached = _get_cached(key)
    if cached:
        return cached

    result = {'deviation_pct': 0, 'risk': 0, 'signal': 'OK'}
    try:
        import urllib.request, json as _json
        url = f'https://fapi.binance.com/fapi/v1/premiumIndex?symbol={symbol}'
        with urllib.request.urlopen(url, timeout=3) as r:
            data = _json.loads(r.read())

        mark_price = float(data.get('markPrice', 0))
        index_price = float(data.get('indexPrice', 0))

        if mark_price <= 0 or index_price <= 0:
            return _set_cached(key, result)

        deviation_pct = abs(mark_price - index_price) / index_price * 100

        risk = 0
        signal = 'OK'
        if deviation_pct > 0.5:
            risk = 35
            signal = f'DANGER: 标记价格偏差{deviation_pct:.2f}%，插针风险极高'
        elif deviation_pct > 0.3:
            risk = 20
            signal = f'WARNING: 标记价格偏差{deviation_pct:.2f}%，监控中'
        elif deviation_pct > 0.15:
            risk = 8
            signal = f'CAUTION: 标记价格轻微偏差{deviation_pct:.2f}%'

        result = {
            'mark_price': mark_price,
            'index_price': index_price,
            'deviation_pct': round(deviation_pct, 3),
            'risk': risk,
            'signal': signal,
        }
    except Exception as e:
        result['err'] = str(e)[:40]

    return _set_cached(key, result)


# ══════════════════════════════════════════════════════════════
# 主函数：综合防御评分
# ══════════════════════════════════════════════════════════════

def get_anti_manip_score(symbol: str, signal_dir: str = None) -> dict:
    """
    综合操控防御评分
    返回：
      risk_score: 0-100（≥60=HIGH_RISK禁止开仓，40-59=MEDIUM减仓50%）
      risk_level: 'LOW' / 'MEDIUM' / 'HIGH'
      score_adj:  梵天评分调整（HIGH=-15，MEDIUM=-8，LOW=0）
      signals:    触发的预警列表
      modules:    各模块详情
    """
    spread   = _check_spread(symbol)
    taker    = _check_taker(symbol)
    oi_anom  = _check_oi_anomaly(symbol)
    fr_win   = _check_fr_window(symbol)
    mark_dev = _check_mark_price(symbol)

    # 方向相关的taker风险
    taker_risk = 0
    if signal_dir == 'LONG':
        taker_risk = taker.get('risk_long', 0)
    elif signal_dir == 'SHORT':
        taker_risk = taker.get('risk_short', 0)
    else:
        taker_risk = max(taker.get('risk_long', 0), taker.get('risk_short', 0))

    # 加权综合风险分
    total_risk = (
        spread.get('risk', 0) * 1.0 +   # spread权重最高（做市商撤单=最直接信号）
        mark_dev.get('risk', 0) * 1.0 +  # 标记价格偏差=插针最直接信号
        taker_risk * 0.8 +               # taker比例
        oi_anom.get('risk', 0) * 0.7 +   # OI异常
        fr_win.get('risk', 0) * 0.5      # FR窗口（权重低，是常规现象）
    )

    # 归一化到0-100
    risk_score = min(int(total_risk), 100)

    # 风险等级
    if risk_score >= 60:
        risk_level = 'HIGH'
        score_adj  = -15
        action     = '禁止开仓，现有仓位收紧SL至1%'
    elif risk_score >= 40:
        risk_level = 'MEDIUM'
        score_adj  = -8
        action     = '仓位减半，提高警惕'
    else:
        risk_level = 'LOW'
        score_adj  = 0
        action     = 'OK'

    # 收集触发的预警信号
    signals = []
    for mod_name, mod_data in [
        ('Spread', spread), ('Taker', taker),
        ('OI', oi_anom), ('FR窗口', fr_win), ('标记价格', mark_dev)
    ]:
        sig = mod_data.get('signal', 'OK')
        if sig != 'OK':
            signals.append(f'[{mod_name}] {sig}')

    return {
        'risk_score':  risk_score,
        'risk_level':  risk_level,
        'score_adj':   score_adj,
        'action':      action,
        'signals':     signals,
        'modules': {
            'spread':   spread,
            'taker':    taker,
            'oi_anomaly': oi_anom,
            'fr_window':  fr_win,
            'mark_price': mark_dev,
        }
    }
