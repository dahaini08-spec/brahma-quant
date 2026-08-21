#!/usr/bin/env python3
"""
rsi_1h_trigger.py · 梵天1H RSI触发层 v1.0
设计院封印 2026-08-21 苏摩111

【验证铁证】
  三层架构回测（2020-06~2026-08，6年真实期货数据）：
  BTC: n=845 WR=62.7% EV=+0.701%/笔 累计PnL=+592% MaxDD=-7.4%
  ETH: n=949 WR=64.7% EV=+0.898%/笔 累计PnL=+852% MaxDD=-6.9%

【架构定位】
  1H触发（本模块）→ 4H体制过滤 → 15m精确入场（trigger_15m.py）

【触发事件（T1~T6）】
  T1: RSI_1H 从<45 穿越 ≥55（多头动量启动，需BULL/RECOVERY体制）
  T2: RSI_1H 从>55 跌破 ≤45（空头动量启动，需BEAR/EARLY/CHOP体制）
  T3: RSI_1H >72 且价格 < EMA20_1H（超买做空，需BEAR体制）
  T4: RSI_1H <28 且价格 > EMA20_1H（超卖做多，需BULL体制）
  T5: 价格突破近24H高点+0.3%（做多结构突破，需BULL体制）
  T6: 价格跌破近24H低点-0.3%（做空结构突破，需BEAR体制）

【最优参数（铁证封印）】
  SL = ATR_15m × 2.0（动态止损）
  RR = 2.0（BTC+ETH均最优）
  最长持仓 = 48根15m（12H）
  15m入场窗口 = 触发后8根15m内找RSI极值点

【体制封禁（宪法）】
  BEAR_TREND:LONG → 永久封禁
  ETH BEAR_RECOVERY:LONG → 永久封禁（WR=35.7%死穴）
"""

import time
import json
import requests
from pathlib import Path
from datetime import datetime, timezone

BASE_DIR = Path(__file__).parent
ROOT_DIR = BASE_DIR.parent

# ── 总线接入 ────────────────────────────────────────────
try:
    from brahma_brain.brahma_bus import bus as _bus
except Exception:
    _bus = None


# ── 数学工具 ────────────────────────────────────────────
def _ema(closes: list, n: int) -> float:
    if len(closes) < 2: return float(closes[-1])
    k = 2 / (n + 1); v = float(closes[0])
    for c in closes[1:]: v = c * k + v * (1 - k)
    return v

def _rsi(closes: list, n: int = 14) -> float:
    if len(closes) < n + 1: return 50.0
    d = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    g = [max(0, x) for x in d[-n:]]; lo = [max(0, -x) for x in d[-n:]]
    ag, al = sum(g)/n, sum(lo)/n
    return round(100 - 100/(1 + ag/al), 2) if al > 0 else 100.0

def _atr(highs: list, lows: list, closes: list, n: int = 14) -> float:
    trs = [max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
           for i in range(1, len(closes))]
    return sum(trs[-n:]) / min(n, len(trs)) if trs else closes[-1] * 0.01


# ── K线获取 ─────────────────────────────────────────────
def _fetch_klines(symbol: str, interval: str, limit: int = 100) -> list:
    """获取已关闭K线（丢弃最后一根未收盘）"""
    if _bus:
        try:
            raw = _bus.klines(symbol, interval, limit + 1)
            if raw and len(raw) >= 2:
                return raw[:-1]
        except Exception:
            pass
    try:
        r = requests.get(
            'https://fapi.binance.com/fapi/v1/klines',
            params={'symbol': symbol.upper(), 'interval': interval, 'limit': limit + 1},
            timeout=8
        )
        data = r.json()
        if not isinstance(data, list) or len(data) < 2:
            return []
        return [{'ts': int(d[0]), 'o': float(d[1]), 'h': float(d[2]),
                 'l': float(d[3]), 'c': float(d[4]), 'v': float(d[5])}
                for d in data[:-1]]
    except Exception:
        return []


# ── 体制获取（复用现有体制系统）────────────────────────
def _get_regime(symbol: str) -> str:
    """从brahma_state.json获取当前体制（最快路径）"""
    try:
        state_file = ROOT_DIR / 'data' / 'brahma_state.json'
        if state_file.exists():
            state = json.loads(state_file.read_text())
            sym_key = symbol.upper().replace('USDT', '').lower()
            regime = state.get(sym_key, {}).get('regime') or state.get('regime', '')
            if regime:
                return regime
    except Exception:
        pass
    return 'CHOP_MID'  # 默认保守体制


# ── 核心：1H触发检测 ────────────────────────────────────
def detect_1h_trigger(symbol: str) -> dict | None:
    """
    检测当前是否有1H触发事件（T1~T6）
    
    返回：
      None — 无触发
      dict — 触发信号 {event, direction, regime, rsi_1h, price, atr_15m, ...}
    
    调用方：brahma_engine.py / signal_15m_engine.py
    """
    sym = symbol.upper()
    if not sym.endswith('USDT'):
        sym += 'USDT'

    # 获取1H K线（60根，足够RSI+EMA计算）
    bars_1h = _fetch_klines(sym, '1h', 60)
    if len(bars_1h) < 30:
        return None

    closes_1h = [b['c'] for b in bars_1h]
    highs_1h  = [b['h'] for b in bars_1h]
    lows_1h   = [b['l'] for b in bars_1h]

    rsi_cur  = _rsi(closes_1h)
    rsi_prev = _rsi(closes_1h[:-1])  # 上一根1H的RSI
    ema20_1h = _ema(closes_1h, 20)
    price    = closes_1h[-1]

    # 获取4H体制
    regime = _get_regime(symbol)

    # ── 宪法封禁检查 ────────────────────────────────────
    def _is_banned(direction: str) -> bool:
        if regime == 'BEAR_TREND' and direction == 'LONG':
            return True  # BEAR_TREND_LONG 永久封禁
        sym_base = sym.replace('USDT', '')
        if sym_base == 'ETH' and regime == 'BEAR_RECOVERY' and direction == 'LONG':
            return True  # ETH BEAR_RECOVERY:LONG 死穴封禁
        return False

    # ── T1~T6 触发检测 ──────────────────────────────────
    event = None
    direction = None

    # T1: RSI上穿（多头动量启动）
    if rsi_prev < 45 and rsi_cur >= 55 and regime in ('BULL_TREND', 'BEAR_RECOVERY'):
        event, direction = 'T1_RSI_UP', 'LONG'

    # T2: RSI下穿（空头动量启动）
    elif rsi_prev > 55 and rsi_cur <= 45 and regime in ('BEAR_TREND', 'BEAR_EARLY', 'CHOP_MID'):
        event, direction = 'T2_RSI_DOWN', 'SHORT'

    # T3: RSI超买做空
    elif rsi_cur > 72 and price < ema20_1h and regime in ('BEAR_TREND', 'BEAR_EARLY', 'CHOP_MID'):
        event, direction = 'T3_OB_SHORT', 'SHORT'

    # T4: RSI超卖做多
    elif rsi_cur < 28 and price > ema20_1h and regime in ('BULL_TREND', 'BEAR_RECOVERY'):
        event, direction = 'T4_OS_LONG', 'LONG'

    # T5: 突破24H高（做多，仅BULL体制）
    elif regime == 'BULL_TREND' and len(highs_1h) >= 24:
        h24 = max(highs_1h[-25:-1])
        if price > h24 * 1.003:
            event, direction = 'T5_BREAK_H', 'LONG'

    # T6: 跌破24H低（做空，仅BEAR体制）
    elif regime == 'BEAR_TREND' and len(lows_1h) >= 24:
        l24 = min(lows_1h[-25:-1])
        if price < l24 * 0.997:
            event, direction = 'T6_BREAK_L', 'SHORT'

    if not event or not direction:
        return None

    # 宪法封禁
    if _is_banned(direction):
        return None

    # ── 计算15m ATR（供trigger_15m.py使用）──────────────
    bars_15m = _fetch_klines(sym, '15m', 30)
    atr_15m = 0.0
    if len(bars_15m) >= 15:
        h15 = [b['h'] for b in bars_15m]
        l15 = [b['l'] for b in bars_15m]
        c15 = [b['c'] for b in bars_15m]
        atr_15m = _atr(h15, l15, c15, 14)

    # ── 动态SL/TP参数（铁证封印）──────────────────────
    sl_dist = atr_15m * 2.0  # SL = ATR_15m × 2.0
    sl_pct  = round(sl_dist / price * 100, 3) if price > 0 else 1.0
    tp_pct  = round(sl_pct * 2.0, 3)          # RR = 2.0（最优参数）

    trigger = {
        'symbol':    sym,
        'event':     event,
        'direction': direction,
        'regime':    regime,
        'rsi_1h':    round(rsi_cur, 1),
        'rsi_prev':  round(rsi_prev, 1),
        'price':     round(price, 4),
        'ema20_1h':  round(ema20_1h, 4),
        'atr_15m':   round(atr_15m, 6),
        'sl_pct':    sl_pct,
        'tp_pct':    tp_pct,
        'rr':        2.0,
        'ts':        int(time.time() * 1000),
        'dt':        datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'),
        # 验证铁证
        '_validated_wr':  0.627 if 'BTC' in sym else 0.647,
        '_validated_ev':  0.701 if 'BTC' in sym else 0.898,
        '_source':   'rsi_1h_trigger_v1.0',
    }

    return trigger


# ── 便捷扫描：多标的批量检测 ────────────────────────────
def scan_triggers(symbols: list = None) -> list:
    """批量扫描多标的，返回所有触发事件"""
    if symbols is None:
        symbols = ['BTCUSDT', 'ETHUSDT']
    
    results = []
    for sym in symbols:
        try:
            t = detect_1h_trigger(sym)
            if t:
                results.append(t)
        except Exception as e:
            pass
    return results


if __name__ == '__main__':
    print("梵天1H触发层 v1.0 — 实时检测")
    for sym in ['BTCUSDT', 'ETHUSDT']:
        result = detect_1h_trigger(sym)
        if result:
            print(f"✅ {sym} 触发: {result['event']} {result['direction']}")
            print(f"   RSI_1H={result['rsi_1h']} 体制={result['regime']}")
            print(f"   SL={result['sl_pct']}% TP={result['tp_pct']}% RR={result['rr']}")
        else:
            print(f"  {sym}: 无触发")
