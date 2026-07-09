
# ── STATUS: AUXILIARY ──────────────────────────────────────────
# 15M触发器，CHoCH检测
# LAST_REVIEW: 2026-07-01 | 属于辅助计算层，修改前确认调用链
# ─────────────────────────────────────────────────────────────
from datetime import datetime, timezone
"""
trigger_15m.py · 梵天15分钟精确触发层 v1.0
设计院 2026-06-01

【核心价值】
  1H/4H结构定框架（OB/FVG入场区）
  15M订单流确认精确触发点
  
  触发率：55% → 预期80%+
  止损：1.5-2% → 收窄至0.6-1.0%（15M结构低点）

【工作流程】
  输入：symbol, signal_dir, entry_lo(1H), entry_hi(1H), atr_4h
  
  1. 获取最近96根15M K线
  2. 检查价格是否在1H入场区内或贴近（gap≤1.5%）
  3. 识别15M CHoCH（结构转变确认）
  4. 识别15M OB（精确止损锚点）
  5. 输出精确入场点 + 收窄止损 + 触发置信度
"""

import os, sys, time
import numpy as np
import requests

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



BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _fetch_15m(symbol: str, limit: int = 96) -> list:
    url = 'https://fapi.binance.com/fapi/v1/klines'
    try:
        r = requests.get(url, params={
            'symbol': symbol.upper(),
            'interval': '15m',
            'limit': limit
        }, timeout=8)
        data = r.json()
        if not isinstance(data, list):
            return []
        return [{
            'ts':  d[0],
            'o':   float(d[1]),
            'h':   float(d[2]),
            'l':   float(d[3]),
            'c':   float(d[4]),
            'v':   float(d[5]),
        } for d in data]
    except Exception as e:
        pass  # [静默] f'[Trigger15M] 数据获取失败: {e}'
        return []


def _find_15m_ob(klines: list, direction: str) -> dict | None:
    """
    在最近30根15M K线中找最近的OB
    做空OB：最后一根大阳线前的阴线区域（机构卖出点）
    做多OB：最后一根大阴线前的阳线区域（机构买入点）
    """
    if len(klines) < 5:
        return None

    recent = klines[-30:]

    if direction == 'SHORT':
        for i in range(len(recent)-2, 2, -1):
            k = recent[i]
            k_prev = recent[i-1]
            body_prev = (k_prev['c'] - k_prev['o']) / k_prev['o'] * 100
            body_curr = (k['c'] - k['o']) / k['o'] * 100
            if body_prev > 0.3 and body_curr < -0.1:
                return {'high': k_prev['h'], 'low': k_prev['l'], 'type': 'BEAR_OB_15M', 'idx': i-1}
    else:
        for i in range(len(recent)-2, 2, -1):
            k = recent[i]
            k_prev = recent[i-1]
            body_prev = (k_prev['c'] - k_prev['o']) / k_prev['o'] * 100
            body_curr = (k['c'] - k['o']) / k['o'] * 100
            if body_prev < -0.3 and body_curr > 0.1:
                return {'high': k_prev['h'], 'low': k_prev['l'], 'type': 'BULL_OB_15M', 'idx': i-1}
    return None


def _find_wick_rejection(klines: list, direction: str) -> dict | None:
    """
    [v22.1] Wick Rejection 识别——针形K线（Pin Bar）
    做空: 上影线 >> 实体（约押 / 拒绝测试高位）
    做多: 下影线 >> 实体（约押 / 支撑模式）
    """
    if len(klines) < 3:
        return None
    recent = klines[-5:]   # 查最近5根
    for k in reversed(recent):
        o, h, l, c = k['o'], k['h'], k['l'], k['c']
        body   = abs(c - o)
        total  = h - l
        if total < 1e-9:
            continue
        upper_wick = h - max(c, o)
        lower_wick = min(c, o) - l
        body_ratio = body / total
        if direction == 'SHORT':
            # 废气针：上影线 > 2×实体，实体 < 30%
            if upper_wick > body * 2.0 and body_ratio < 0.35:
                return {'type': 'WICK_REJECT_SHORT', 'high': h, 'low': l, 'body_ratio': round(body_ratio, 3)}
        else:
            if lower_wick > body * 2.0 and body_ratio < 0.35:
                return {'type': 'WICK_REJECT_LONG', 'high': h, 'low': l, 'body_ratio': round(body_ratio, 3)}
    return None


def _find_15m_bos(klines: list, direction: str) -> dict | None:
    """
    [v22.1] BOS（Break of Structure）确认
    做空 BOS: 价格突破近期重要摆高 = 结构转空确认
    做多 BOS: 价格突破近期重要摆低 = 结构转多确认
    """
    if len(klines) < 10:
        return None
    recent = klines[-20:]
    closes = [k['c'] for k in recent]
    highs  = [k['h'] for k in recent]
    lows   = [k['l'] for k in recent]
    curr_c = closes[-1]

    if direction == 'SHORT':
        # 近期重要摆高 = 最近2~8根内的最高点（排除最新一根）
        recent_swing_high = max(highs[-8:-1])
        if curr_c > recent_swing_high:   # 突破高点 = 币价公司上涨，不是做空信号
            return None
        # 近期摆低被突破 = 做空 BOS
        recent_swing_low = min(lows[-8:-1])
        if curr_c < recent_swing_low:
            return {'type': 'BOS_SHORT', 'level': recent_swing_low,
                    'note': f'15M BOS做空确认 跌破${recent_swing_low:.4f}'}
    else:
        recent_swing_low = min(lows[-8:-1])
        if curr_c < recent_swing_low:
            return None
        recent_swing_high = max(highs[-8:-1])
        if curr_c > recent_swing_high:
            return {'type': 'BOS_LONG', 'level': recent_swing_high,
                    'note': f'15M BOS做多确认 突破${recent_swing_high:.4f}'}
    return None



    """
    在最近30根15M K线中找最近的OB
    做空OB：最后一根大阳线前的阴线区域（机构卖出点）
    做多OB：最后一根大阴线前的阳线区域（机构买入点）
    """
    if len(klines) < 5:
        return None

    recent = klines[-30:]

    if direction == 'SHORT':
        # 找大涨后的OB（压力区）：找最近一次价格上冲后的阻力
        # 识别：连续上涨后出现阴线 = OB顶部
        for i in range(len(recent)-2, 2, -1):
            k = recent[i]
            k_prev = recent[i-1]
            # 当前K线是阴线，前一根是阳线，且前一根涨幅>0.3%
            body_prev = (k_prev['c'] - k_prev['o']) / k_prev['o'] * 100
            body_curr = (k['c'] - k['o']) / k['o'] * 100
            if body_prev > 0.3 and body_curr < -0.1:
                return {
                    'high':  k_prev['h'],
                    'low':   k_prev['l'],
                    'open':  k_prev['o'],
                    'close': k_prev['c'],
                    'type':  'BEAR_OB_15M',
                    'idx':   i-1
                }

    else:  # LONG
        # 找大跌后的OB（支撑区）：找最近一次价格下跌后的支撑
        for i in range(len(recent)-2, 2, -1):
            k = recent[i]
            k_prev = recent[i-1]
            body_prev = (k_prev['c'] - k_prev['o']) / k_prev['o'] * 100
            body_curr = (k['c'] - k['o']) / k['o'] * 100
            if body_prev < -0.3 and body_curr > 0.1:
                return {
                    'high':  k_prev['h'],
                    'low':   k_prev['l'],
                    'open':  k_prev['o'],
                    'close': k_prev['c'],
                    'type':  'BULL_OB_15M',
                    'idx':   i-1
                }

    return None


def _find_15m_choch(klines: list, direction: str) -> dict | None:
    """
    检测最近15M K线的CHoCH（结构转变）
    做空：出现lower high后跌破swing low → 趋势转空
    做多：出现higher low后突破swing high → 趋势转多
    """
    if len(klines) < 10:
        return None

    recent = klines[-20:]
    highs  = [k['h'] for k in recent]
    lows   = [k['l'] for k in recent]
    closes = [k['c'] for k in recent]

    if direction == 'SHORT':
        # 找近期高点下降序列（LH序列）
        n = len(highs)
        lh_count = 0
        for i in range(n-3, n-1):
            if highs[i] < highs[i-2]:
                lh_count += 1
        if lh_count >= 1:
            # 价格是否已跌破近期低点（CHoCH确认）
            recent_low = min(lows[-5:])
            if closes[-1] < recent_low * 1.002:
                return {
                    'confirmed': True,
                    'type': 'CHOCH_BEARISH_15M',
                    'level': recent_low,
                    'note': f'15M空头CHoCH确认 跌破${recent_low:.4f}'
                }
        return {
            'confirmed': False,
            'type': 'NO_CHOCH',
            'note': '15M空头结构未确认'
        }

    else:  # LONG
        # 找近期低点上升序列（HL序列）
        n = len(lows)
        hl_count = 0
        for i in range(n-3, n-1):
            if lows[i] > lows[i-2]:
                hl_count += 1
        if hl_count >= 1:
            recent_high = max(highs[-5:])
            if closes[-1] > recent_high * 0.998:
                return {
                    'confirmed': True,
                    'type': 'CHOCH_BULLISH_15M',
                    'level': recent_high,
                    'note': f'15M多头CHoCH确认 突破${recent_high:.4f}'
                }
        return {
            'confirmed': False,
            'type': 'NO_CHOCH',
            'note': '15M多头结构未确认'
        }


def analyze_trigger(
    symbol: str,
    signal_dir: str,
    entry_lo_1h: float,
    entry_hi_1h: float,
    atr_4h: float,
    score_1h: int = 0,
    verbose: bool = True
) -> dict:
    """
    15M精确触发层主入口

    参数：
      symbol       : 标的
      signal_dir   : SHORT/LONG
      entry_lo_1h  : 1H入场区下沿
      entry_hi_1h  : 1H入场区上沿
      atr_4h       : 4H ATR（用于止损兜底）
      score_1h     : 1H体制评分（传入用于联合评分）

    返回：
      trigger_valid  : 是否确认触发
      entry_15m      : 精确入场价（15M OB边沿）
      stop_15m       : 精确止损价（15M结构低/高点）
      sl_pct_15m     : 止损百分比
      rr_15m         : 精确R:R（基于15M止损）
      confidence     : 触发置信度（0-100）
      choch          : CHoCH状态
      ob_15m         : 15M OB信息
    """
    t0  = time.time()
    sym = symbol.upper()
    if not sym.endswith('USDT'):
        sym += 'USDT'

    klines = _fetch_15m(sym, limit=96)
    if not klines:
        return {'trigger_valid': False, 'reason': '无法获取15M数据', 'confidence': 0}

    price     = klines[-1]['c']
    entry_mid = (entry_lo_1h + entry_hi_1h) / 2

    # ── 1. 判断价格是否在1H入场框架内 ────────────────────────────
    if signal_dir == 'SHORT':
        gap_to_entry = (entry_lo_1h - price) / price * 100   # 正值=价格在入场区下方（需反弹）
    else:
        gap_to_entry = (price - entry_hi_1h) / price * 100   # 正值=价格在入场区上方（需回调）

    in_zone    = -0.3 <= gap_to_entry <= 0.2   # 价格在入场区内
    near_zone  = 0.2 < gap_to_entry <= 1.5     # 价格贴近入场区（需等待）
    far_zone   = gap_to_entry > 1.5            # 价格远离

    # ── 2. 15M OB识别 ──────────────────────────────────────────
    ob_15m = _find_15m_ob(klines, signal_dir)

    # ── 2b. [v22.1] Wick Rejection + BOS 确认 ───────────────────
    wick_rej = _find_wick_rejection(klines, signal_dir)
    bos_15m  = _find_15m_bos(klines, signal_dir)

    # ── 3. 15M CHoCH识别 ────────────────────────────────────────
    choch = _find_15m_choch(klines, signal_dir)

    # ── 4. 计算精确止损（15M结构锚点）────────────────────────────
    recent_lows  = sorted([k['l'] for k in klines[-10:]])
    recent_highs = sorted([k['h'] for k in klines[-10:]], reverse=True)

    if signal_dir == 'SHORT':
        # 做空止损 = 15M近期高点 + 缓冲
        sl_anchor  = recent_highs[1] if len(recent_highs) > 1 else entry_hi_1h
        stop_15m   = round(sl_anchor * 1.002, 6)   # 高点上方0.2%
        entry_15m  = round(entry_lo_1h * 0.999, 6) if in_zone else round(entry_lo_1h, 6)
        # TP基于1H框架（不变），用精确止损重算R:R
        tp_target  = entry_mid - abs(entry_mid - stop_15m) * 2.5
        rr_15m     = round(abs(entry_15m - tp_target) / max(abs(stop_15m - entry_15m), 0.0001), 2)
    else:
        # 做多止损 = 15M近期低点 - 缓冲
        sl_anchor  = recent_lows[1] if len(recent_lows) > 1 else entry_lo_1h
        stop_15m   = round(sl_anchor * 0.998, 6)   # 低点下方0.2%
        entry_15m  = round(entry_hi_1h * 1.001, 6) if in_zone else round(entry_hi_1h, 6)
        tp_target  = entry_mid + abs(entry_mid - stop_15m) * 2.5
        rr_15m     = round(abs(tp_target - entry_15m) / max(abs(entry_15m - stop_15m), 0.0001), 2)

    sl_pct_15m = round(abs(entry_15m - stop_15m) / entry_15m * 100, 3)

    # ── 5. 置信度评分（0-100）─────────────────────────────────────
    confidence = 0

    # 价格位置
    if in_zone:   confidence += 35
    elif near_zone: confidence += 15
    else:         confidence += 0

    # CHoCH确认
    if choch and choch.get('confirmed'):
        confidence += 30
    elif choch:
        confidence += 5

    # [v22.1] Wick Rejection废气针
    if wick_rej:
        confidence += 20

    # [v22.1] BOS 结构突破确认
    if bos_15m:
        confidence += 25

    # 15M OB存在
    if ob_15m:
        confidence += 10  # v22.1: 降低至10（已有wick+bos补充）

    # R:R加分
    if rr_15m >= 3.0:   confidence += 15
    elif rr_15m >= 2.0: confidence += 10
    elif rr_15m >= 1.5: confidence += 5

    # 止损合理性（<1%为好）
    if sl_pct_15m <= 0.8:
        confidence += 10
    elif sl_pct_15m <= 1.2:
        confidence += 5

    # ── 6. 触发判断 ──────────────────────────────────────────────
    trigger_valid = (
        confidence >= 55 and
        rr_15m >= 1.5 and
        sl_pct_15m <= 1.5 and
        (in_zone or near_zone)
    )

    elapsed = round(time.time() - t0, 2)

    result = {
        'trigger_valid':  trigger_valid,
        'symbol':         sym,
        'price':          price,
        'signal_dir':     signal_dir,
        'entry_15m':      entry_15m,
        'stop_15m':       stop_15m,
        'sl_pct_15m':     sl_pct_15m,
        'rr_15m':         rr_15m,
        'confidence':     confidence,
        'in_zone':        in_zone,
        'near_zone':      near_zone,
        'gap_to_entry':   round(gap_to_entry, 2),
        'choch':          choch,
        'ob_15m':         ob_15m,
        'wick_rejection': wick_rej,   # [v22.1]
        'bos_15m':        bos_15m,    # [v22.1]
        'entry_lo_1h':    entry_lo_1h,
        'entry_hi_1h':    entry_hi_1h,
        'elapsed':        elapsed,
        # ── 闭环数据层（设计院 2026-06-05）────────────────────────
        'trigger_ts':     datetime.now(timezone.utc).isoformat() if trigger_valid else None,
        'entry_actual':   price if trigger_valid and in_zone else None,
        'entry_source':   'market_15m' if trigger_valid else None,
    }

    if verbose:
        flag = '✅ 触发确认' if trigger_valid else '⏳ 等待触发'
        zone_tag = '📍在区' if in_zone else ('📏贴近' if near_zone else '📐偏远')
        pass  # [静默] f'[Trigger15M] {sym} {signal_dir} {flag}'
        pass  # [静默]
        pass  # [静默]
        _wick_s = wick_rej.get("type","?") if wick_rej else "无"
        _bos_s  = bos_15m.get("type","?") if bos_15m else "无"
        pass  # [静默]

    return result


if __name__ == '__main__':
    # 快速测试
    sym = sys.argv[1] if len(sys.argv) > 1 else 'ETH'
    d   = sys.argv[2] if len(sys.argv) > 2 else 'SHORT'
    # 用当前价模拟入场区
    import requests as _r
    p = float(_r.get('https://fapi.binance.com/fapi/v1/ticker/price',
              params={'symbol': sym.upper()+'USDT'}, timeout=5).json()['price'])
    if d == 'SHORT':
        elo, ehi = p * 1.015, p * 1.020
    else:
        elo, ehi = p * 0.980, p * 0.985
    print(f'\n测试: {sym} {d}  现价=${p:.4f}  模拟入场区=${elo:.4f}~${ehi:.4f}')
    analyze_trigger(sym, d, elo, ehi, atr_4h=p*0.015)
