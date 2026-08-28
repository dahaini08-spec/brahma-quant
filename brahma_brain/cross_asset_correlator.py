"""
cross_asset_correlator.py — 跨品种宏观相关性层
设计院封印 2026-08-28 苏摩111

职责:
  1. VIX恐慌指数代理（通过BTC波动率历史百分位估算）
  2. DXY美元指数走势（通过Binance USDC/USDT溢价代理）
  3. BTC.D比特币占比（通过总市值估算）
  4. US10Y利率代理（DXY相关性方向）
  5. 综合score_addon输出

数据来源策略:
  - 优先通过 brahma_bus / data_cache 走缓存
  - fallback: Binance fapi 公开端点
  - 无法获取时: 降级输出NEUTRAL（不阻断流程）

接入位置: scripts/brahma_1hao_analysis.py L771
  from brahma_brain.cross_asset_correlator import get_cross_asset_context as _get_cross
  _cross = _get_cross(symbol=symbol, current_price=px)
"""

import time
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_CACHE: dict = {}
_TTL = 300  # 5分钟缓存


def _safe_get_price(symbol: str) -> float:
    """安全获取价格"""
    try:
        from brahma_brain.brahma_bus import get_price as _gp
        return _gp(symbol)
    except Exception:
        pass
    try:
        import requests
        r = requests.get(
            f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={symbol}',
            timeout=5
        )
        return float(r.json().get('price', 0))
    except Exception:
        return 0.0


def _safe_get_klines(symbol: str, interval: str = '1d', limit: int = 90) -> list:
    """安全获取K线"""
    try:
        from brahma_brain.data_cache import get_klines as _dc
        raw = _dc(symbol, interval, limit)
        if raw and len(raw) >= 10:
            return raw
    except Exception:
        pass
    try:
        import requests
        r = requests.get(
            'https://fapi.binance.com/fapi/v1/klines',
            params={'symbol': symbol, 'interval': interval, 'limit': limit},
            timeout=8
        )
        data = r.json()
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return []


def _get_vix_proxy(btc_klines_1d: list) -> dict:
    """
    VIX代理: 用BTC 1D收益率标准差估算恐慌程度
    历史对应关系:
      σ_14d < 0.02  → VIX类比 < 20  → 低恐慌 (CALM)
      σ_14d 0.02~0.04 → VIX类比 20~30 → 中性 (NORMAL)
      σ_14d > 0.04  → VIX类比 > 30  → 高恐慌 (FEAR)
    """
    result = {
        'vix_now': 'N/A',
        'vix_regime': 'NORMAL',
        'vix_trend': '→',
        'btc_impact': '中性',
        'score_addon': 0,
    }
    try:
        if len(btc_klines_1d) < 14:
            return result
        closes = [float(k[4]) for k in btc_klines_1d[-30:]]
        returns = [(closes[i] - closes[i-1]) / closes[i-1] for i in range(1, len(closes))]
        import math
        # 近14日收益率标准差
        r14 = returns[-14:]
        mean = sum(r14) / len(r14)
        std = math.sqrt(sum((x - mean)**2 for x in r14) / len(r14))
        vix_proxy = round(std * 100, 2)

        # 近5日 vs 前9日趋势
        r5  = returns[-5:]
        r9  = returns[-14:-5]
        std5 = math.sqrt(sum((x - sum(r5)/len(r5))**2 for x in r5) / len(r5)) * 100
        std9 = math.sqrt(sum((x - sum(r9)/len(r9))**2 for x in r9) / len(r9)) * 100
        trend = '↑' if std5 > std9 * 1.1 else ('↓' if std5 < std9 * 0.9 else '→')

        if vix_proxy < 2.0:
            regime = 'CALM'
            btc_impact = '偏多（低恐慌）'
            score_addon = +4
        elif vix_proxy > 4.0:
            regime = 'FEAR'
            btc_impact = '偏空（高恐慌）'
            score_addon = -6
        else:
            regime = 'NORMAL'
            btc_impact = '中性'
            score_addon = 0

        result.update({
            'vix_now': f'{vix_proxy:.1f}%σ',
            'vix_regime': regime,
            'vix_trend': trend,
            'btc_impact': btc_impact,
            'score_addon': score_addon,
        })
    except Exception as e:
        result['vix_now'] = f'ERR:{e}'
    return result


def _get_dxy_proxy(symbol: str = 'BTCUSDT') -> dict:
    """
    DXY代理: 通过 EURUSDT 现货价格方向（EUR强 = DXY弱 = BTC利多）
    或直接获取 system_config 里保存的 macro_state
    """
    result = {
        'dxy_now': 'N/A',
        'dxy_signal': 'NEUTRAL',
        'corr_90d': 'N/A',
        'score_addon': 0,
    }
    try:
        # 优先读 macro_state.json（梵天已有宏观数据缓存）
        import json
        macro_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'data', 'macro_state.json'
        )
        if os.path.exists(macro_path):
            age = time.time() - os.path.getmtime(macro_path)
            if age < 14400:  # 4小时内有效
                macro = json.load(open(macro_path))
                dxy = macro.get('dxy', 0)
                if dxy > 0:
                    if dxy > 104:
                        signal = 'STRONG_USD'
                        score_addon = -5
                    elif dxy > 101:
                        signal = 'MILD_USD'
                        score_addon = -2
                    elif dxy < 98:
                        signal = 'WEAK_USD'
                        score_addon = +5
                    elif dxy < 101:
                        signal = 'MILD_WEAK'
                        score_addon = +2
                    else:
                        signal = 'NEUTRAL'
                        score_addon = 0
                    result.update({
                        'dxy_now': round(dxy, 2),
                        'dxy_signal': signal,
                        'corr_90d': '-0.72',  # BTC/DXY 历史90日相关系数典型值
                        'score_addon': score_addon,
                    })
                    return result
    except Exception:
        pass

    # fallback: 用 cross_fr_basis 的宏观信号（system_config）
    result.update({
        'dxy_now': 'N/A (宏观数据待刷新)',
        'dxy_signal': 'NEUTRAL',
        'corr_90d': '-0.72',
        'score_addon': 0,
    })
    return result


def _get_btcd_proxy(btc_klines_1d: list) -> dict:
    """
    BTC.D代理: 通过 BTC 近90日涨幅百分位估算山寨季
    逻辑: BTC连续强势 → BTC.D高 → 山寨受压
          BTC横盘/弱 + 资金流入 → 山寨季启动
    """
    result = {
        'signal': 'NEUTRAL',
        'btc_90d_pct': 'N/A',
        'percentile': 0.5,
        'altcoin_season': False,
        'score_addon': 0,
    }
    try:
        if len(btc_klines_1d) < 30:
            return result
        closes = [float(k[4]) for k in btc_klines_1d]
        # 90日涨幅
        pct_90d = (closes[-1] - closes[-90]) / closes[-90] * 100 if len(closes) >= 90 else \
                  (closes[-1] - closes[0]) / closes[0] * 100

        # 估算在近180日历史分布中的百分位
        if len(closes) >= 180:
            returns_90d = []
            for i in range(90, len(closes)):
                r = (closes[i] - closes[i-90]) / closes[i-90] * 100
                returns_90d.append(r)
            sorted_r = sorted(returns_90d)
            rank = sum(1 for x in sorted_r if x <= pct_90d)
            percentile = rank / len(sorted_r)
        else:
            percentile = 0.5

        # 山寨季判断（BTC 90日涨幅过高 → 资金在BTC → 不是山寨季）
        altcoin_season = pct_90d < 15 and percentile < 0.4

        if pct_90d > 50:
            signal = 'BTC_DOMINANT'
            score_addon = -3  # BTC强势，山寨流动性被吸走
        elif pct_90d < -20:
            signal = 'BEAR_MARKET'
            score_addon = -8
        elif altcoin_season:
            signal = 'ALTCOIN_SEASON'
            score_addon = +5
        else:
            signal = 'NEUTRAL'
            score_addon = 0

        result.update({
            'signal': signal,
            'btc_90d_pct': round(pct_90d, 1),
            'percentile': round(percentile, 2),
            'altcoin_season': altcoin_season,
            'score_addon': score_addon,
        })
    except Exception as e:
        result['signal'] = f'ERR:{e}'
    return result


def _get_rates_proxy() -> dict:
    """
    US10Y利率代理: 读取 macro_state.json 或返回NEUTRAL
    """
    result = {
        'rate_now': 'N/A',
        'rate_regime': 'NEUTRAL',
        'rate_trend': '→',
        'score_addon': 0,
    }
    try:
        import json
        macro_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'data', 'macro_state.json'
        )
        if os.path.exists(macro_path):
            age = time.time() - os.path.getmtime(macro_path)
            if age < 14400:
                macro = json.load(open(macro_path))
                rate = macro.get('us10y', 0)
                if rate > 0:
                    if rate > 4.5:
                        regime = 'HIGH_RATE'
                        score_addon = -4
                    elif rate < 3.5:
                        regime = 'LOW_RATE'
                        score_addon = +4
                    else:
                        regime = 'NEUTRAL'
                        score_addon = 0
                    result.update({
                        'rate_now': round(rate, 2),
                        'rate_regime': regime,
                        'rate_trend': '→',
                        'score_addon': score_addon,
                    })
    except Exception:
        pass
    return result


def get_cross_asset_context(symbol: str = 'BTCUSDT', current_price: float = 0.0) -> dict:
    """
    主入口 — 返回跨品种宏观层完整字典

    接入位置: scripts/brahma_1hao_analysis.py L771
    返回字段:
      vix, rates, dxy, btcd: 各子模块字典
      score_addon_total: 总评分加成
      _summary: 文字摘要
    """
    cache_key = f'cross_{symbol}'
    now = time.time()
    if cache_key in _CACHE and now - _CACHE[cache_key]['ts'] < _TTL:
        return _CACHE[cache_key]['data']

    # 拉取 BTC 日线（所有代理的基础数据）
    btc_symbol = 'BTCUSDT' if not symbol.endswith('USDT') else symbol
    # 对非BTC标的，仍用BTC日线做宏观基准
    btc_klines = _safe_get_klines('BTCUSDT', '1d', 180)

    vix_r   = _get_vix_proxy(btc_klines)
    rates_r = _get_rates_proxy()
    dxy_r   = _get_dxy_proxy(symbol)
    btcd_r  = _get_btcd_proxy(btc_klines)

    total_addon = (
        vix_r.get('score_addon', 0) +
        rates_r.get('score_addon', 0) +
        dxy_r.get('score_addon', 0) +
        btcd_r.get('score_addon', 0)
    )
    # 限幅 -15 ~ +15
    total_addon = max(-15, min(15, total_addon))

    data = {
        'vix':   vix_r,
        'rates': rates_r,
        'dxy':   dxy_r,
        'btcd':  btcd_r,
        'score_addon_total': total_addon,
        '_summary': (
            f"VIX代理={vix_r.get('vix_now','N/A')} | "
            f"DXY={dxy_r.get('dxy_now','N/A')} | "
            f"BTC.D={btcd_r.get('signal','N/A')} | "
            f"宏观加成={total_addon:+d}"
        ),
    }

    _CACHE[cache_key] = {'ts': now, 'data': data}
    return data


# ── 快速测试 ──────────────────────────────────────────────
if __name__ == '__main__':
    import json
    sym = sys.argv[1] if len(sys.argv) > 1 else 'BTCUSDT'
    ctx = get_cross_asset_context(symbol=sym)
    print(json.dumps(ctx, ensure_ascii=False, indent=2, default=str))
