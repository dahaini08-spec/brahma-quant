"""
cross_market_alpha.py — 跨市场alpha提取器
设计院 2026-09-12 苏摩111

职责：
  1. 从134个cross_fr文件提取跨市场资金费率alpha
  2. 汇总crypto vs tradfi资金流方向
  3. 输出cross_market_alpha信号[-1~+1]供ensemble_engine使用

接入位置：
  - brahma_brain/cross_market_alpha.py（本文件）
  - ensemble_engine.get_ensemble_score() 第13维 cross_market_alpha
  - brahma_core.analyze() I10接入点

数据源：
  - data/cross_fr_*.json（134个标的，3所FR+基差）
  - 实时调用cross_market_engine.get_cross_fr_basis()（BTC/ETH）
"""
import json, time, os
from pathlib import Path
from collections import defaultdict

BASE = Path(__file__).parent.parent
FR_DIR = BASE / 'data'

# 中心标的（实时拉取）
CORE_SYMBOLS = {'BTCUSDT', 'ETHUSDT'}
# FR极值阈值
FR_EXTREME_HIGH = 0.015   # >0.015% = 极端多头拥挤
FR_EXTREME_LOW = -0.005   # <-0.005% = 极端空头拥挤
# 缓存
_CACHE = {'ts': 0, 'data': None}
_CACHE_TTL = 300  # 5min（cross_fr文件更新频率约5-10min）


def get_cross_market_alpha(symbol: str = 'BTCUSDT') -> dict:
    """
    从134个cross_fr文件 + 实时FR提取跨市场alpha
    
    返回：
      {
        'cross_market_alpha': float,    # -1~+1 信号强度
        'crypto_fr_avg': float,         # crypto平均FR
        'tradfi_fr_avg': float,         # tradfi平均FR
        'fr_divergence': float,         # crypto-tradfi FR差异
        'extreme_count': int,           # 极值标的数量
        'direction': str,               # RISK_ON/RISK_OFF/NEUTRAL
        'detail': str,                  # 人类可读描述
      }
    """
    now = time.time()
    if _CACHE['data'] and now - _CACHE['ts'] < _CACHE_TTL:
        return _build_result(symbol, _CACHE['data'])

    # 扫描134个cross_fr文件
    data = _scan_fr_files()
    _CACHE['data'] = data
    _CACHE['ts'] = now

    return _build_result(symbol, data)


def _scan_fr_files() -> dict:
    """扫描所有cross_fr文件，分类汇总"""
    crypto_frs = []
    tradfi_frs = []
    extremes = []

    for f in os.listdir(FR_DIR):
        if not f.startswith('cross_fr_') or not f.endswith('.json'):
            continue
        try:
            with open(FR_DIR / f) as fh:
                d = json.load(fh)
            sym = d.get('symbol', f.replace('cross_fr_', '').replace('.json', ''))
            frs = d.get('frs', {})
            fr_avg = sum(frs.values()) / len(frs) if frs else 0
            spread = d.get('spread', 0)

            # 分类
            is_crypto = sym.startswith(('BTC', 'ETH', 'BNB', 'SOL', 'XRP', 'ADA',
                                       'DOGE', 'AVAX', 'LINK', 'DOT', 'MATIC', 'LTC',
                                       'TRX', '1000'))
            if is_crypto:
                crypto_frs.append(fr_avg)
            else:
                tradfi_frs.append(fr_avg)

            # 极值检测
            if fr_avg > FR_EXTREME_HIGH:
                extremes.append((sym, fr_avg, 'LONG_CROWDED'))
            elif fr_avg < FR_EXTREME_LOW:
                extremes.append((sym, fr_avg, 'SHORT_CROWDED'))

        except Exception:
            continue

    return {
        'crypto_frs': crypto_frs,
        'tradfi_frs': tradfi_frs,
        'extremes': extremes,
        'n_crypto': len(crypto_frs),
        'n_tradfi': len(tradfi_frs),
        'n_total': len(crypto_frs) + len(tradfi_frs),
    }


def _build_result(symbol: str, data: dict) -> dict:
    """从扫描结果构建alpha信号"""
    crypto_frs = data['crypto_frs']
    tradfi_frs = data['tradfi_frs']
    extremes = data['extremes']

    crypto_avg = sum(crypto_frs) / len(crypto_frs) if crypto_frs else 0
    tradfi_avg = sum(tradfi_frs) / len(tradfi_frs) if tradfi_frs else 0

    # FR差异：crypto > tradfi = 风险偏好（资金流向crypto）
    divergence = crypto_avg - tradfi_avg

    # alpha信号：[-1, +1]
    # 正值 = risk_on（利好做多）
    # 负值 = risk_off（利好做空）
    # 逻辑：crypto FR >> tradfi FR → 资金涌入crypto → risk_on
    #        crypto FR << tradfi FR → 资金逃离crypto → risk_off
    alpha = 0.0

    # 差异归一化（0.01%差异 = 满分）
    if abs(divergence) > 0:
        alpha = max(-1.0, min(1.0, divergence / 0.01))

    # 极值修正：极端多头拥挤 → 减弱做多信号
    long_extremes = sum(1 for _, _, t in extremes if t == 'LONG_CROWDED')
    short_extremes = sum(1 for _, _, t in extremes if t == 'SHORT_CROWDED')

    if long_extremes > 5:
        alpha -= 0.2  # 多头过于拥挤 → 做多信号减弱
    if short_extremes > 5:
        alpha += 0.2  # 空头过于拥挤 → 做空信号减弱

    alpha = max(-1.0, min(1.0, alpha))

    # 方向
    if alpha > 0.3:
        direction = 'RISK_ON'
    elif alpha < -0.3:
        direction = 'RISK_OFF'
    else:
        direction = 'NEUTRAL'

    # 描述
    detail = (f'crypto_fr={crypto_avg:.4f}% tradfi_fr={tradfi_avg:.4f}% '
              f'div={divergence:+.4f}% extremes={len(extremes)} '
              f'({long_extremes}L/{short_extremes}S)')

    return {
        'cross_market_alpha': round(alpha, 4),
        'crypto_fr_avg': round(crypto_avg, 5),
        'tradfi_fr_avg': round(tradfi_avg, 5),
        'fr_divergence': round(divergence, 5),
        'extreme_count': len(extremes),
        'long_extremes': long_extremes,
        'short_extremes': short_extremes,
        'direction': direction,
        'detail': detail,
        'n_total': data['n_total'],
    }


def get_cross_market_signal_for_ensemble(symbol: str, direction: str) -> float:
    """
    为ensemble_engine提供标准化[-1,+1]信号
    
    逻辑：
      - RISK_ON + LONG → 正信号（顺势）
      - RISK_OFF + SHORT → 正信号（顺势）
      - RISK_ON + SHORT → 负信号（逆势）
      - RISK_OFF + LONG → 负信号（逆势）
    """
    result = get_cross_market_alpha(symbol)
    alpha = result['cross_market_alpha']

    # 方向调整：做多时risk_on为正，做空时risk_off为正
    if direction == 'LONG':
        return alpha  # risk_on(+)利好做多
    else:
        return -alpha  # risk_off(-)利好做空 → 翻转符号


if __name__ == '__main__':
    r = get_cross_market_alpha()
    print(f'Cross Market Alpha: {r["cross_market_alpha"]:+.4f}')
    print(f'Direction: {r["direction"]}')
    print(f'Crypto FR avg: {r["crypto_fr_avg"]:.5f}%')
    print(f'Tradfi FR avg: {r["tradfi_fr_avg"]:.5f}%')
    print(f'Divergence: {r["fr_divergence"]:+.5f}%')
    print(f'Extremes: {r["extreme_count"]} ({r["long_extremes"]}L/{r["short_extremes"]}S)')
    print(f'Total symbols: {r["n_total"]}')
    print(f'Detail: {r["detail"]}')
