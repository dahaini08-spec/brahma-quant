"""
设计院 P0-B + P0-C 改造模块
bull_regime_injector.py — BULL_TREND上下文加分 + rsi_trigger_event注入
封印：2026-07-03 设计院自主决策
"""

import json
import time
import requests
from pathlib import Path

BASE = Path(__file__).parent.parent

def get_regime_context_bonus(sym: str, regime: str) -> dict:
    """
    BULL_TREND体制下注入顺势加分（最高+35分）
    参考：QuantConnect/Zipline 因子动态权重调整
    """
    bonus = 0
    reasons = []

    if 'BULL_TREND' not in regime and 'BULL_EARLY' not in regime:
        return {'bonus': 0, 'reasons': [], 'regime': regime}

    try:
        FAPI = 'https://fapi.binance.com'
        # 1H klines
        k1h = requests.get(f'{FAPI}/fapi/v1/klines',
            params={'symbol': sym, 'interval': '1h', 'limit': 22}, timeout=5).json()
        k4h = requests.get(f'{FAPI}/fapi/v1/klines',
            params={'symbol': sym, 'interval': '4h', 'limit': 22}, timeout=5).json()

        if not isinstance(k1h, list) or len(k1h) < 20:
            return {'bonus': 0, 'reasons': ['数据不足'], 'regime': regime}

        c1h = [float(k[4]) for k in k1h]
        c4h = [float(k[4]) for k in k4h] if isinstance(k4h, list) and len(k4h) >= 20 else []

        price = c1h[-1]

        # EMA20计算
        def ema(closes, n):
            k = 2/(n+1); v = closes[0]
            for c in closes[1:]: v = c*k + v*(1-k)
            return v

        ema20_1h = ema(c1h, 20)
        ema50_1h = ema(c1h, 50) if len(c1h) >= 50 else ema20_1h
        ema20_4h = ema(c4h, 20) if c4h else ema20_1h

        # ── 维度1: EMA多头结构（0~20分）──
        if price > ema20_1h and ema20_1h > ema20_4h:
            bonus += 20; reasons.append('EMA完美多头排列 +20')
        elif price > ema20_1h:
            bonus += 12; reasons.append('价格>EMA20_1H +12')
        elif price > ema20_4h:
            bonus += 6;  reasons.append('价格>EMA20_4H仅 +6')

        # ── 维度2: RSI健康区间（0~15分）──
        gains = [max(c1h[i]-c1h[i-1],0) for i in range(1,15)]
        losses= [max(c1h[i-1]-c1h[i],0) for i in range(1,15)]
        ag = sum(gains)/14; al = sum(losses)/14
        rsi = 100-100/(1+ag/al) if al>0 else 100
        if 50 <= rsi <= 70:
            bonus += 15; reasons.append(f'RSI1H={rsi:.0f}黄金多头区 +15')
        elif 45 <= rsi < 50:
            bonus += 8;  reasons.append(f'RSI1H={rsi:.0f}多头入场区 +8')
        elif rsi > 70:
            bonus += 5;  reasons.append(f'RSI1H={rsi:.0f}超买回调等入场 +5')

        # ── 维度3: 4H动能连续（0~10分）──
        if c4h and len(c4h) >= 4:
            consecutive_up = sum(1 for i in range(-1,-4,-1) if c4h[i] > c4h[i-1])
            if consecutive_up >= 3:
                bonus += 10; reasons.append(f'4H连续{consecutive_up}根阳线 +10')
            elif consecutive_up >= 2:
                bonus += 5;  reasons.append(f'4H{consecutive_up}根阳线 +5')

    except Exception as e:
        return {'bonus': 0, 'reasons': [f'计算异常:{e}'], 'regime': regime}

    return {'bonus': min(bonus, 25), 'reasons': reasons, 'regime': regime}  # [设计院 2026-07-06] 上限+35→+25，防止单模块主导信号


def get_event_timing_bonus(sym: str) -> dict:
    """
    读取 rsi_trigger_event.json 2H有效窗口内的事件
    注入 timing_filter 上下文加分
    参考：AQR事件信号融合框架
    """
    trigger_file = BASE / 'data' / 'rsi_trigger_event.json'
    if not trigger_file.exists():
        return {'bonus': 0, 'events': [], 'active': False}

    try:
        data = json.loads(trigger_file.read_text())
        sym_event = data.get(sym, data.get(sym.replace('USDT',''), {}))
        if not sym_event:
            return {'bonus': 0, 'events': [], 'active': False}

        ts = sym_event.get('ts', 0)
        age_h = (time.time() - ts) / 3600
        if age_h > 2.0:
            return {'bonus': 0, 'events': [], 'active': False, 'age_h': age_h}

        events = sym_event.get('events', [])
        bonus = 0
        active_events = []

        for e in events:
            etype = e.get('event', '')
            if etype == 'E2_RSI_OVERBOUGHT_PULLBACK':
                bonus += 35; active_events.append(f'E2超买回落+35')
            elif etype == 'E1_RSI_CROSS_UP':
                bonus += 20; active_events.append(f'E1RSI穿越+20')
            elif etype == 'E3_PRICE_BREAK_HIGH':
                bonus += 25; active_events.append(f'E3突破高点+25')
            elif etype == 'E4_PRICE_BREAK_LOW':
                bonus += 20; active_events.append(f'E4跌破低点+20')
            elif etype == 'E6_VOLUME_SURGE':
                bonus += 15; active_events.append(f'E6量能爆发+15')
            elif etype == 'E7_OI_SURGE':
                bonus += 10; active_events.append(f'E7OI异动+10')
            elif etype == 'E5_BB_EXPAND':
                bonus += 8;  active_events.append(f'E5BB扩张+8')

        return {
            'bonus': min(bonus, 40),
            'events': active_events,
            'active': True,
            'age_h': round(age_h, 2),
            'high_priority': sym_event.get('high_priority', False)
        }
    except Exception as e:
        return {'bonus': 0, 'events': [], 'active': False, 'error': str(e)}


if __name__ == '__main__':
    # 自测
    print("=== BULL Regime Injector 自测 ===")
    r = get_regime_context_bonus('BTCUSDT', 'BULL_TREND')
    print(f"BTC bonus={r['bonus']} reasons={r['reasons']}")
    r = get_regime_context_bonus('ETHUSDT', 'BULL_TREND')
    print(f"ETH bonus={r['bonus']} reasons={r['reasons']}")
    e = get_event_timing_bonus('BTCUSDT')
    print(f"BTC event_bonus={e['bonus']} events={e['events']} active={e['active']}")
    e = get_event_timing_bonus('ETHUSDT')
    print(f"ETH event_bonus={e['bonus']} events={e['events']} active={e['active']}")
