#!/usr/bin/env python3
"""
reversal_long_channel.py
设计院封印 2026-08-02 | 苏摩授权自主决策

反转做多独立通道 — 完全绕开BEAR_TREND体制门控
解决「梵天只会做空，错过50%机会」的核心问题

触发条件（AND逻辑）：
  ① BEAR_TREND体制（前提）
  ② 周线RSI < 35（大周期超卖）
  ③ 4H趋势向上（短期反弹确认）
  ④ 形态确认（W底/头肩底/RSI底背离）OR Kronos p_up>0.90
  ⑤ 当前无同向持仓

仓位分级（分级仓位封印）：
  L1: 仅周线超卖+4H上           → 2%NAV
  L2: L1 + 形态确认              → 5%NAV
  L3: L1 + 形态 + Kronos>0.90   → 8%NAV
"""
import json
import time
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).parent.parent
DATA = BASE / 'data'

def get_regime(symbol: str) -> str:
    # [regime_bus 2026-08-05] 统一体制总线优先
    try:
        import sys as _rbs_m, os as _rbo_m
        _rbs_m.path.insert(0, _rbo_m.path.join(_rbo_m.path.dirname(_rbo_m.path.abspath(__file__)), '..', 'scripts'))
        from regime_bus import get as _rb_get_m
        _rb_r_m = _rb_get_m('ETHUSDT', layer='SIGNAL')
        if _rb_r_m and _rb_r_m != 'UNKNOWN':
            return _rb_r_m
    except Exception:
        pass
    try:
        regime = json.loads((DATA / 'regime_state.json').read_text())
        r = regime.get(symbol, {})
        return r.get('confirmed', '?') if isinstance(r, dict) else str(r)
    except:
        return 'UNKNOWN'

def get_weekly_rsi(symbol: str) -> float:
    """获取周线RSI"""
    try:
        import requests
        API = 'https://fapi.binance.com'
        kl = requests.get(f'{API}/fapi/v1/klines',
                          params={'symbol': symbol, 'interval': '1w', 'limit': 20},
                          timeout=5).json()
        closes = [float(k[4]) for k in kl]
        deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
        gains  = [max(0, d) for d in deltas[-14:]]
        losses = [max(0, -d) for d in deltas[-14:]]
        ag, al = sum(gains)/14, sum(losses)/14
        return round(100 - 100/(1 + ag/al), 1) if al > 0 else 50.0
    except:
        return 50.0

def get_4h_trend(symbol: str) -> str:
    """获取4H趋势方向"""
    try:
        import requests
        API = 'https://fapi.binance.com'
        kl = requests.get(f'{API}/fapi/v1/klines',
                          params={'symbol': symbol, 'interval': '4h', 'limit': 25},
                          timeout=5).json()
        closes = [float(k[4]) for k in kl]
        # EMA20
        ema = closes[0]
        for c in closes[1:]:
            ema = c * (2/21) + ema * (19/21)
        return 'UP' if closes[-1] > ema else 'DOWN'
    except:
        return 'UNKNOWN'

def get_kronos_pup(symbol: str) -> float:
    """获取Kronos p_up"""
    try:
        cache_file = DATA / 'kronos_cache.json'
        if cache_file.exists():
            cache = json.loads(cache_file.read_text())
            sym_data = cache.get(symbol, {})
            return float(sym_data.get('p_up', 0.5))
    except:
        pass
    return 0.5

def get_active_positions(symbol: str) -> list:
    """获取当前活跃持仓"""
    try:
        pos_file = DATA / 'wuqu_positions.json'
        if not pos_file.exists():
            return []
        data = json.loads(pos_file.read_text())
        positions = data if isinstance(data, list) else data.get('positions', [])
        return [p for p in positions
                if p.get('symbol') == symbol
                and p.get('status', '') in ('OPEN', 'open', 'active')]
    except:
        return []

def calc_reversal_grade(
    weekly_rsi: float,
    trend_4h: str,
    kronos_pup: float,
    pattern_result: dict = None,
    rsi_div_result: dict = None,
) -> dict:
    """
    计算反转做多信号等级
    返回 {grade: 'L1'|'L2'|'L3'|None, position_pct: float, reasons: list}
    """
    reasons   = []
    grade     = None
    pos_pct   = 0.0

    # 基础条件
    weekly_ok = weekly_rsi < 35
    trend_ok  = trend_4h == 'UP'

    if not weekly_ok:
        return {'grade': None, 'position_pct': 0.0,
                'reasons': [f'周线RSI={weekly_rsi}未达标(<35)']}
    if not trend_ok:
        return {'grade': None, 'position_pct': 0.0,
                'reasons': [f'4H趋势={trend_4h}非UP']}

    reasons.append(f'周线RSI={weekly_rsi}<35 ✅')
    reasons.append(f'4H趋势=UP ✅')
    grade   = 'L1'
    pos_pct = 0.02  # 2%NAV

    # 形态确认
    pattern_ok   = pattern_result and (pattern_result.get('detected') or pattern_result.get('forming'))
    rsi_div_ok   = rsi_div_result and rsi_div_result.get('detected')
    kronos_ok    = kronos_pup >= 0.85

    if pattern_ok:
        pname = pattern_result.get('pattern', '?')
        conf  = pattern_result.get('confidence', 0)
        reasons.append(f'形态={pname} 置信度={conf} ✅')
        grade   = 'L2'
        pos_pct = 0.05  # 5%NAV

    if rsi_div_ok:
        reasons.append(f'RSI底背离 ✅ bonus={rsi_div_result.get("score_bonus",0)}')
        grade   = 'L2'
        pos_pct = 0.05

    if kronos_ok:
        reasons.append(f'Kronos p_up={kronos_pup}>0.85 ✅')
        if grade == 'L2':
            grade   = 'L3'
            pos_pct = 0.08  # 8%NAV

    return {
        'grade':       grade,
        'position_pct':pos_pct,
        'reasons':     reasons,
    }

def run_reversal_check(symbol: str, price: float = None) -> dict:
    """
    完整反转做多通道检查
    返回：是否触发 + 完整信号参数
    """
    regime = get_regime(symbol)

    # 必须在BEAR体制下才激活
    if 'BEAR' not in regime:
        return {
            'triggered': False,
            'reason':    f'体制={regime}，非BEAR不激活反转通道',
        }

    # 检查活跃持仓
    active_pos = get_active_positions(symbol)
    long_pos   = [p for p in active_pos if p.get('direction', '').upper() == 'LONG']
    if long_pos:
        return {'triggered': False, 'reason': f'已有{len(long_pos)}个LONG持仓'}

    # 获取市场数据
    weekly_rsi = get_weekly_rsi(symbol)
    trend_4h   = get_4h_trend(symbol)
    kronos_pup = get_kronos_pup(symbol)

    # 尝试形态检测
    pattern_result  = None
    rsi_div_result  = None
    try:
        import requests
        API = 'https://fapi.binance.com'
        kl4h = requests.get(f'{API}/fapi/v1/klines',
                            params={'symbol': symbol, 'interval': '4h', 'limit': 60},
                            timeout=5).json()
        highs  = [float(k[2]) for k in kl4h]
        lows   = [float(k[3]) for k in kl4h]
        closes = [float(k[4]) for k in kl4h]
        cur    = price or closes[-1]

        from scripts.pattern_detector import detect_w_bottom, detect_head_shoulders_bottom, detect_rsi_divergence

        # RSI计算
        rsi_vals = []
        for i in range(14, len(closes)):
            sub = closes[i-14:i]
            deltas = [sub[j]-sub[j-1] for j in range(1,len(sub))]
            g = sum(max(0,d) for d in deltas)/14
            l = sum(max(0,-d) for d in deltas)/14
            rsi_vals.append(100-100/(1+g/l) if l>0 else 50)

        w_res   = detect_w_bottom(highs, lows, closes, cur)
        hs_res  = detect_head_shoulders_bottom(highs, lows, closes, cur)
        div_res = detect_rsi_divergence(closes, rsi_vals, 'BULLISH') if len(rsi_vals)>=20 else {}

        if w_res.get('detected') or w_res.get('forming'):
            pattern_result = w_res
        elif hs_res.get('detected') or hs_res.get('forming'):
            pattern_result = hs_res

        if div_res.get('detected'):
            rsi_div_result = div_res
    except Exception as e:
        pass

    # 计算等级
    grade_result = calc_reversal_grade(
        weekly_rsi, trend_4h, kronos_pup,
        pattern_result, rsi_div_result,
    )

    grade   = grade_result['grade']
    pos_pct = grade_result['position_pct']
    reasons = grade_result['reasons']

    if not grade:
        return {
            'triggered': False,
            'reasons':   reasons,
            'weekly_rsi':weekly_rsi,
            'trend_4h':  trend_4h,
        }

    # 计算入场参数（达摩院铁证SL=2%）
    cur_price = price or 0
    sl_price  = cur_price * 0.98 if cur_price > 0 else 0
    tp1_price = cur_price * 1.02 if cur_price > 0 else 0
    tp2_price = cur_price * 1.04 if cur_price > 0 else 0

    # 若有形态，用形态目标
    if pattern_result and pattern_result.get('target'):
        tp1_price = pattern_result['target']
        tp2_price = pattern_result['target'] * 1.01

    result = {
        'triggered':     True,
        'symbol':        symbol,
        'regime':        regime,
        'grade':         grade,
        'position_pct':  pos_pct,
        'direction':     'LONG',
        'price':         cur_price,
        'sl_price':      round(sl_price, 2),
        'tp1_price':     round(tp1_price, 2),
        'tp2_price':     round(tp2_price, 2),
        'sl_pct':        2.0,
        'weekly_rsi':    weekly_rsi,
        'trend_4h':      trend_4h,
        'kronos_pup':    kronos_pup,
        'pattern':       pattern_result,
        'rsi_divergence':rsi_div_result,
        'reasons':       reasons,
        'generated_at':  datetime.now(timezone.utc).isoformat(),
    }
    return result


if __name__ == '__main__':
    import requests
    API = 'https://fapi.binance.com'

    print('=== reversal_long_channel.py 当前市场检测 ===')
    print()
    for sym in ['BTCUSDT', 'ETHUSDT']:
        try:
            price = float(requests.get(f'{API}/fapi/v1/ticker/price',
                                       params={'symbol': sym}, timeout=5).json()['price'])
        except:
            price = 0.0

        result = run_reversal_check(sym, price)

        print(f'{sym} @ ${price:.2f}:')
        if result['triggered']:
            print(f'  🚨 反转做多信号触发！')
            print(f'  等级: {result["grade"]} 仓位: {result["position_pct"]*100:.0f}%NAV')
            print(f'  SL: ${result["sl_price"]:.2f} TP1: ${result["tp1_price"]:.2f}')
            for r in result['reasons']:
                print(f'  ✅ {r}')
        else:
            print(f'  未触发: {result.get("reason", result.get("reasons", "?"))}')
        print()
