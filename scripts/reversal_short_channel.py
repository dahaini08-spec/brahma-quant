#!/usr/bin/env python3
"""
reversal_short_channel.py
设计院封印 2026-08-03 | 苏摩举一反三封印

反转做空独立通道 — 顶部形态识别 + 做空信号
与 reversal_long_channel.py 完全对称

触发条件（AND逻辑）：
  ① BULL_TREND 或 BEAR_RECOVERY 体制（反弹过热）
     OR BEAR_TREND + 价格反弹至关键阻力（反弹做空）
  ② 周线RSI > 65（大周期超买）
     OR 价格在4周区间 > 75%（偏高位）
  ③ 4H趋势向下（短期回落确认）
     OR 4H RSI > 70（超买回落）
  ④ 形态确认（M顶/头肩顶/RSI顶背离）OR Kronos p_up<0.15

仓位分级（与做多通道对称）：
  L1: 仅超买+4H下           → 2%NAV
  L2: L1 + 形态确认          → 5%NAV
  L3: L1 + 形态 + Kronos<0.15 → 8%NAV
"""
import json, time
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).parent.parent
DATA = BASE / 'data'

# ── 数据获取 ──────────────────────────────────────────────────

def get_regime(symbol: str) -> str:
    try:
        regime = json.loads((DATA / 'regime_state.json').read_text())
        r = regime.get(symbol, {})
        return r.get('confirmed', '?') if isinstance(r, dict) else str(r)
    except:
        return 'UNKNOWN'

def get_weekly_rsi_and_position(symbol: str) -> dict:
    """获取周线RSI + 4周区间位置"""
    try:
        import requests
        API = 'https://fapi.binance.com'
        kl = requests.get(f'{API}/fapi/v1/klines',
                          params={'symbol': symbol, 'interval': '1w', 'limit': 20},
                          timeout=5).json()
        closes = [float(k[4]) for k in kl]
        highs  = [float(k[2]) for k in kl]
        lows   = [float(k[3]) for k in kl]

        # RSI
        deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
        gains  = [max(0, d) for d in deltas[-14:]]
        losses = [max(0, -d) for d in deltas[-14:]]
        ag, al = sum(gains)/14, sum(losses)/14
        rsi    = round(100 - 100/(1 + ag/al), 1) if al > 0 else 50.0

        # 4周区间位置
        w4_high = max(highs[-4:])
        w4_low  = min(lows[-4:])
        pos     = (closes[-1] - w4_low) / (w4_high - w4_low) * 100 if w4_high > w4_low else 50
        return {'rsi': rsi, 'position_pct': round(pos, 1),
                'w4_high': w4_high, 'w4_low': w4_low}
    except:
        return {'rsi': 50.0, 'position_pct': 50.0}

def get_4h_rsi_and_trend(symbol: str) -> dict:
    """获取4H趋势 + RSI"""
    try:
        import requests
        API = 'https://fapi.binance.com'
        kl = requests.get(f'{API}/fapi/v1/klines',
                          params={'symbol': symbol, 'interval': '4h', 'limit': 25},
                          timeout=5).json()
        closes = [float(k[4]) for k in kl]
        highs  = [float(k[2]) for k in kl]
        lows   = [float(k[3]) for k in kl]

        # EMA20
        ema = closes[0]
        for c in closes[1:]:
            ema = c * (2/21) + ema * (19/21)
        trend = 'DOWN' if closes[-1] < ema else 'UP'

        # RSI
        deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
        gains  = [max(0, d) for d in deltas[-14:]]
        losses = [max(0, -d) for d in deltas[-14:]]
        ag, al = sum(gains)/14, sum(losses)/14
        rsi    = round(100 - 100/(1 + ag/al), 1) if al > 0 else 50.0

        return {'trend': trend, 'rsi': rsi,
                'highs': highs, 'lows': lows, 'closes': closes}
    except:
        return {'trend': 'UNKNOWN', 'rsi': 50.0, 'highs': [], 'lows': [], 'closes': []}

def get_kronos_pup(symbol: str) -> float:
    try:
        cache_file = DATA / 'kronos_cache.json'
        if cache_file.exists():
            cache = json.loads(cache_file.read_text())
            sym_data = cache.get(symbol, {})
            return float(sym_data.get('p_up', 0.5))
    except:
        pass
    return 0.5

def get_active_short_positions(symbol: str) -> list:
    try:
        pos_file = DATA / 'wuqu_positions.json'
        if not pos_file.exists():
            return []
        data = json.loads(pos_file.read_text())
        positions = data if isinstance(data, list) else data.get('positions', [])
        return [p for p in positions
                if p.get('symbol') == symbol
                and p.get('status', '') in ('OPEN', 'open', 'active')
                and p.get('direction', '').upper() == 'SHORT']
    except:
        return []

# ── 核心判断 ──────────────────────────────────────────────────

def calc_reversal_short_grade(
    weekly_rsi: float,
    weekly_position_pct: float,
    trend_4h: str,
    rsi_4h: float,
    kronos_pup: float,
    regime: str,
    pattern_result: dict = None,
    rsi_div_result: dict = None,
) -> dict:
    """
    计算反转做空信号等级
    返回 {grade: 'L1'|'L2'|'L3'|None, position_pct: float, reasons: list}

    做空触发逻辑（与做多对称）：
      超买条件：周线RSI>65 OR 4周区间位置>75%
      方向条件：4H趋势DOWN OR 4H RSI>70回落
    """
    reasons = []

    # 超买判断（周线RSI > 65 OR 区间位置偏高）
    overbought_by_rsi = weekly_rsi > 65
    overbought_by_pos = weekly_position_pct > 75

    # BULL体制下反转做空 OR BEAR体制反弹做空
    bull_regime    = 'BULL' in regime
    bear_rebound   = 'BEAR' in regime and weekly_position_pct > 60

    regime_ok = bull_regime or bear_rebound

    if not regime_ok:
        return {'grade': None, 'position_pct': 0.0,
                'reasons': [f'体制={regime} 区间位置={weekly_position_pct}%，不满足做空条件']}

    # 超买确认（满足其一）
    if overbought_by_rsi:
        reasons.append(f'周线RSI={weekly_rsi}>65 超买 ✅')
    elif overbought_by_pos:
        reasons.append(f'4周区间位置={weekly_position_pct}%>75% 偏高位 ✅')
    else:
        # BEAR体制反弹做空：不强制要求超买，但需要4H已转DOWN
        if not (trend_4h == 'DOWN' or rsi_4h > 68):
            return {'grade': None, 'position_pct': 0.0,
                    'reasons': [f'周线RSI={weekly_rsi}未超买，4H未转空，条件不足']}
        reasons.append(f'BEAR体制反弹做空，区间位置={weekly_position_pct}% ✅')

    # 4H方向确认
    if trend_4h == 'DOWN':
        reasons.append(f'4H趋势=DOWN ✅')
    elif rsi_4h > 68:
        reasons.append(f'4H RSI={rsi_4h}>68 超买待回落 ✅')
    else:
        return {'grade': None, 'position_pct': 0.0,
                'reasons': reasons + [f'4H趋势={trend_4h} RSI={rsi_4h}，等待回落确认']}

    grade   = 'L1'
    pos_pct = 0.02

    # 形态确认 → 升级L2
    pattern_ok = pattern_result and (pattern_result.get('detected') or pattern_result.get('forming'))
    rsi_div_ok = rsi_div_result and rsi_div_result.get('detected')

    if pattern_ok:
        pname = pattern_result.get('pattern', '?')
        conf  = pattern_result.get('confidence', 0)
        reasons.append(f'形态={pname} 置信度={conf} ✅')
        grade   = 'L2'
        pos_pct = 0.05

    if rsi_div_ok:
        reasons.append(f'RSI顶背离 ✅ bonus={rsi_div_result.get("score_bonus", 0)}')
        grade   = 'L2'
        pos_pct = 0.05

    # Kronos极低 → 升级L3
    if kronos_pup < 0.15:
        reasons.append(f'Kronos p_up={kronos_pup}<0.15 极度看空 ✅')
        if grade == 'L2':
            grade   = 'L3'
            pos_pct = 0.08

    return {'grade': grade, 'position_pct': pos_pct, 'reasons': reasons}

# ── 主函数 ────────────────────────────────────────────────────

def run_reversal_short_check(symbol: str, price: float = None) -> dict:
    """完整反转做空通道检查"""
    regime     = get_regime(symbol)
    weekly     = get_weekly_rsi_and_position(symbol)
    h4         = get_4h_rsi_and_trend(symbol)
    kronos_pup = get_kronos_pup(symbol)

    weekly_rsi    = weekly['rsi']
    weekly_pos    = weekly['position_pct']
    trend_4h      = h4['trend']
    rsi_4h        = h4['rsi']
    highs_4h      = h4['highs']
    lows_4h       = h4['lows']
    closes_4h     = h4['closes']

    # 检查是否已有空单
    short_pos = get_active_short_positions(symbol)
    if short_pos:
        return {'triggered': False, 'reason': f'已有{len(short_pos)}个SHORT持仓'}

    # 形态检测
    pattern_result = None
    rsi_div_result = None
    cur_price      = price or (closes_4h[-1] if closes_4h else 0)

    if highs_4h and lows_4h and closes_4h:
        try:
            from scripts.pattern_detector import (
                detect_m_top, detect_head_shoulders_top, detect_rsi_divergence
            )
            m_res  = detect_m_top(highs_4h, lows_4h, closes_4h, cur_price)
            hs_res = detect_head_shoulders_top(highs_4h, lows_4h, closes_4h, cur_price)

            if m_res.get('detected') or m_res.get('forming'):
                pattern_result = m_res
            elif hs_res.get('detected') or hs_res.get('forming'):
                pattern_result = hs_res

            # RSI顶背离
            rsi_vals = []
            for i in range(14, len(closes_4h)):
                sub    = closes_4h[i-14:i]
                deltas = [sub[j]-sub[j-1] for j in range(1, len(sub))]
                g = sum(max(0, d) for d in deltas) / 14
                l = sum(max(0, -d) for d in deltas) / 14
                rsi_vals.append(100 - 100/(1+g/l) if l > 0 else 50)

            if len(rsi_vals) >= 20:
                div = detect_rsi_divergence(closes_4h, rsi_vals, 'BEARISH')
                if div.get('detected'):
                    rsi_div_result = div
        except Exception as e:
            pass

    # 计算等级
    grade_result = calc_reversal_short_grade(
        weekly_rsi, weekly_pos, trend_4h, rsi_4h,
        kronos_pup, regime, pattern_result, rsi_div_result,
    )

    grade   = grade_result['grade']
    pos_pct = grade_result['position_pct']
    reasons = grade_result['reasons']

    if not grade:
        return {
            'triggered':  False,
            'reasons':    reasons,
            'weekly_rsi': weekly_rsi,
            'weekly_pos': weekly_pos,
            'trend_4h':   trend_4h,
            'rsi_4h':     rsi_4h,
        }

    # 入场参数（达摩院铁证SL=2.0%）
    sl_price  = cur_price * 1.02
    tp1_price = cur_price * 0.98
    tp2_price = cur_price * 0.96

    if pattern_result and pattern_result.get('target'):
        tp1_price = pattern_result['target']
        tp2_price = pattern_result['target'] * 0.99

    return {
        'triggered':     True,
        'symbol':        symbol,
        'regime':        regime,
        'grade':         grade,
        'position_pct':  pos_pct,
        'direction':     'SHORT',
        'price':         cur_price,
        'sl_price':      round(sl_price, 2),
        'tp1_price':     round(tp1_price, 2),
        'tp2_price':     round(tp2_price, 2),
        'sl_pct':        2.0,
        'weekly_rsi':    weekly_rsi,
        'weekly_pos':    weekly_pos,
        'trend_4h':      trend_4h,
        'rsi_4h':        rsi_4h,
        'kronos_pup':    kronos_pup,
        'pattern':       pattern_result,
        'rsi_divergence':rsi_div_result,
        'reasons':       reasons,
        'generated_at':  datetime.now(timezone.utc).isoformat(),
    }

# ── CLI ───────────────────────────────────────────────────────

if __name__ == '__main__':
    import requests
    API = 'https://fapi.binance.com'

    print('=== reversal_short_channel.py 当前市场检测 ===')
    print()
    for sym in ['BTCUSDT', 'ETHUSDT']:
        try:
            price = float(requests.get(f'{API}/fapi/v1/ticker/price',
                                       params={'symbol': sym}, timeout=5).json()['price'])
        except:
            price = 0.0

        result = run_reversal_short_check(sym, price)

        print(f'{sym} @ ${price:.2f}:')
        if result['triggered']:
            print(f'  🚨 反转做空信号触发！')
            print(f'  等级: {result["grade"]}  仓位: {result["position_pct"]*100:.0f}%NAV')
            print(f'  方向: SHORT')
            print(f'  SL: ${result["sl_price"]:.2f}  TP1: ${result["tp1_price"]:.2f}  TP2: ${result["tp2_price"]:.2f}')
            print(f'  止损: {result["sl_pct"]}%（达摩院铁证）')
            for r in result['reasons']:
                print(f'  ✅ {r}')
            if result.get('pattern'):
                pat = result['pattern']
                print(f'  形态: {pat.get("pattern")} 置信度={pat.get("confidence")} 颈线={pat.get("neckline","?")}')
        else:
            reasons = result.get('reasons', [result.get('reason', '?')])
            r_str   = reasons[0] if isinstance(reasons, list) else reasons
            print(f'  未触发: {r_str}')
        print()
