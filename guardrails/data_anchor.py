#!/usr/bin/env python3
"""
data_anchor.py — 数据溯源强制层 Layer 0.5
设计院 · 2026-06-04

核心原则：
  任何分析用到的「基准数字」必须来自实时API
  禁止使用训练记忆中的价格数据

触发关键词 → 强制执行实时查询
"""
import urllib.request, json
from datetime import datetime, timezone

# ── 触发词清单 ──────────────────────────────────────────
TRIGGER_KEYWORDS = [
    '最高点', '历史最高', 'ATH', '顶部', '高点', '最高价',
    '最低点', '历史最低', '底部', '低点', '熊市底',
    '跌幅', '涨幅', '跌了多少', '涨了多少',
    '从多少跌', '从顶部', '距顶', '距底',
    '什么时候最高', '最高是多少',
]

def check_needs_anchor(user_text: str) -> bool:
    """检测用户输入是否涉及需要实时验证的价格基准"""
    return any(kw in user_text for kw in TRIGGER_KEYWORDS)

def fetch_btc_ath() -> dict:
    """获取BTC永续合约历史ATH（月线级别）"""
    try:
        r = urllib.request.urlopen(
            'https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=1M&limit=200',
            timeout=8
        )
        klines = json.loads(r.read())
        highs = [(k[0], float(k[2])) for k in klines]
        ath_ts, ath_price = max(highs, key=lambda x: x[1])
        dt = datetime.fromtimestamp(ath_ts/1000, tz=timezone.utc)
        return {
            'ath': ath_price,
            'ath_date': dt.strftime('%Y-%m'),
            'source': 'binance_fapi_1M',
            'verified': True
        }
    except Exception as e:
        return {'ath': None, 'error': str(e), 'verified': False}

def fetch_btc_atl() -> dict:
    """获取BTC永续合约历史最低点"""
    try:
        r = urllib.request.urlopen(
            'https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=1M&limit=200',
            timeout=8
        )
        klines = json.loads(r.read())
        lows = [(k[0], float(k[3])) for k in klines]
        atl_ts, atl_price = min(lows, key=lambda x: x[1])
        dt = datetime.fromtimestamp(atl_ts/1000, tz=timezone.utc)
        return {
            'atl': atl_price,
            'atl_date': dt.strftime('%Y-%m'),
            'source': 'binance_fapi_1M',
            'verified': True
        }
    except Exception as e:
        return {'atl': None, 'error': str(e), 'verified': False}

def fetch_current_price(symbol: str = 'BTCUSDT') -> float:
    """获取实时价格"""
    try:
        r = urllib.request.urlopen(
            f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={symbol}',
            timeout=5
        )
        return float(json.loads(r.read())['price'])
    except:
        return 0.0

def get_market_anchor(symbol: str = 'BTC') -> dict:
    """
    统一数据锚点 — 所有宏观分析的起点
    返回：ATH / ATL / 当前价 / 已跌幅 / 距ATH
    """
    sym = f'{symbol}USDT'
    ath_data = fetch_btc_ath()
    cur = fetch_current_price(sym)
    
    result = {
        'symbol': symbol,
        'current': cur,
        'ath': ath_data.get('ath'),
        'ath_date': ath_data.get('ath_date'),
        'drawdown_pct': round((cur - ath_data['ath']) / ath_data['ath'] * 100, 2) if ath_data.get('ath') else None,
        'verified': ath_data.get('verified', False),
        'source': 'binance_fapi_realtime'
    }
    return result

if __name__ == '__main__':
    print('=== 数据溯源层自检 ===')
    anchor = get_market_anchor('BTC')
    print(f'BTC ATH:     {anchor["ath"]:,.1f}U  ({anchor["ath_date"]})')
    print(f'当前价格:    {anchor["current"]:,.1f}U')
    print(f'已跌幅:      {anchor["drawdown_pct"]}%')
    print(f'数据来源:    {anchor["source"]}')
    print(f'已验证:      {anchor["verified"]}')
    print()
    # 触发词测试
    tests = ['BTC最高点是多少', '从顶部跌幅', '底部预测', '今天天气如何']
    for t in tests:
        print(f'触发检测 "{t}": {check_needs_anchor(t)}')
