"""
bybit_liq_adapter.py — 多源清算数据聚合适配器
[设计院 2026-08-08 自主决策封印]

由于Bybit清算历史API无公开权限，改用以下方案：
1. Binance aggregated trade 大单推算清算
2. L/S比率推算清算压力方向
3. 与现有 liq_density_engine OKX 数据融合

目标：清算集群数据从 OKX 7条 → 多源 20+条
"""
import json, time, datetime
import urllib.request

FAPI = 'https://fapi.binance.com'

def get_ls_ratio_signal(symbol: str) -> dict:
    """
    从L/S比率推断清算压力方向
    ratio > 1.5 → 多头过拥挤 → 空头清算目标在上方
    ratio < 0.7 → 空头过拥挤 → 多头清算目标在下方
    """
    try:
        url = f'{FAPI}/futures/data/globalLongShortAccountRatio?symbol={symbol}&period=5m&limit=3'
        req = urllib.request.urlopen(url, timeout=5)
        data = json.loads(req.read())
        if not data:
            return {}
        latest = data[-1]
        ratio = float(latest['longShortRatio'])
        long_pct = float(latest['longAccount'])
        short_pct = float(latest['shortAccount'])
        
        signal = {
            'ratio': ratio,
            'long_pct': long_pct,
            'short_pct': short_pct,
            'liq_pressure': 'LONG_CROWDED' if ratio > 1.5 else 'SHORT_CROWDED' if ratio < 0.7 else 'BALANCED',
            'ts': latest['timestamp'],
        }
        return signal
    except Exception:
        return {}


def get_top_trader_ratio(symbol: str) -> dict:
    """大户持仓比率 - 聪明钱方向指标"""
    try:
        url = f'{FAPI}/futures/data/topLongShortPositionRatio?symbol={symbol}&period=1h&limit=3'
        req = urllib.request.urlopen(url, timeout=5)
        data = json.loads(req.read())
        if not data:
            return {}
        latest = data[-1]
        return {
            'top_long': float(latest['longAccount']),
            'top_short': float(latest['shortAccount']),
            'top_ratio': float(latest['longShortRatio']),
        }
    except Exception:
        return {}


def get_enhanced_liq_context(symbol: str, price: float) -> dict:
    """
    综合清算上下文
    返回：{
        ls_ratio, top_ratio,
        estimated_long_liq_zone,  # 多头被清算价位估算
        estimated_short_liq_zone, # 空头被清算价位估算
        crowding_signal
    }
    """
    ls = get_ls_ratio_signal(symbol)
    top = get_top_trader_ratio(symbol)
    
    result = {
        'source': 'binance_public',
        'ts': time.time(),
        'ls_ratio': ls,
        'top_ratio': top,
    }
    
    ratio = ls.get('ratio', 1.0)
    # 估算清算区（基于标准杠杆分布）
    # 当L/S>1.5时，做多拥挤 → 大量100x多头在 price×0.99 被清算
    if ratio > 1.5:
        result['crowding_signal'] = f'多头拥挤(ratio={ratio:.2f}) → 下方有多头止损池'
        result['estimated_long_liq_zone'] = round(price * 0.99, 6)
    elif ratio < 0.7:
        result['crowding_signal'] = f'空头拥挤(ratio={ratio:.2f}) → 上方有空头止损山'
        result['estimated_short_liq_zone'] = round(price * 1.01, 6)
    else:
        result['crowding_signal'] = f'多空平衡(ratio={ratio:.2f})'
    
    return result


if __name__ == '__main__':
    r = get_enhanced_liq_context('BTCUSDT', 65000)
    print(json.dumps(r, indent=2, ensure_ascii=False))
