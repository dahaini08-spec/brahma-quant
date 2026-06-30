#!/usr/bin/env python3
"""
hunter_sizer.py — 仓位计算桥接层 v1.0
设计院 2026-06-04

统一入口：
  calc_position_size(signal, nav) → sizing dict
  get_symbol_info(symbol)         → {step_size, qty_precision, min_notional}
"""
import json, sys, math, urllib.request
from decimal import Decimal
from pathlib import Path

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE/'brahma_brain'))


def _calc_precision(tick_float: float) -> int:
    """正确计算tick_size的小数位数，兼容科学计数法（如1e-05）"""
    d = Decimal(str(tick_float))
    normalized = d.normalize()
    sign, digits, exp = normalized.as_tuple()
    return max(0, -exp)

# ── 交易所规格缓存 ─────────────────────────────────────────
_SYMBOL_SPECS = {
    # step_size=qty步长  qty_precision=数量精度  tick_size=价格步长  price_precision=价格精度
    'DOGEUSDT': {'step_size': 1.0,     'qty_precision': 0, 'tick_size': 0.00001,  'price_precision': 5, 'min_notional': 5.0},
    'ETHUSDT':  {'step_size': 0.001,   'qty_precision': 3, 'tick_size': 0.01,     'price_precision': 2, 'min_notional': 5.0},
    'BTCUSDT':  {'step_size': 0.001,   'qty_precision': 3, 'tick_size': 0.1,      'price_precision': 1, 'min_notional': 5.0},
    'SOLUSDT':  {'step_size': 0.1,     'qty_precision': 1, 'tick_size': 0.0001,   'price_precision': 4, 'min_notional': 5.0},
    'BNBUSDT':  {'step_size': 0.01,    'qty_precision': 2, 'tick_size': 0.01,     'price_precision': 2, 'min_notional': 5.0},
    'LTCUSDT':  {'step_size': 0.01,    'qty_precision': 2, 'tick_size': 0.01,     'price_precision': 2, 'min_notional': 5.0},
    'XRPUSDT':  {'step_size': 1.0,     'qty_precision': 0, 'tick_size': 0.0001,   'price_precision': 4, 'min_notional': 5.0},
    'BNBUSDT':  {'step_size': 0.01,    'qty_precision': 2, 'tick_size': 0.01,     'price_precision': 2, 'min_notional': 5.0},
    'ADAUSDT':  {'step_size': 1.0,     'qty_precision': 0, 'tick_size': 0.0001,   'price_precision': 4, 'min_notional': 5.0},
    'LINKUSDT': {'step_size': 0.01,    'qty_precision': 2, 'tick_size': 0.001,    'price_precision': 3, 'min_notional': 5.0},
}

def get_symbol_info(symbol: str) -> dict:
    sym = symbol.upper()
    if sym in _SYMBOL_SPECS:
        return _SYMBOL_SPECS[sym]
    # fallback: 从交易所获取
    try:
        r = urllib.request.urlopen(
            f'https://fapi.binance.com/fapi/v1/exchangeInfo',
            timeout=8
        )
        info = json.loads(r.read())
        for s in info.get('symbols',[]):
            if s['symbol'] == sym:
                step = 0.001; qp = 3; tick = 0.01; pp = 2; min_n = 5.0
                for f in s.get('filters',[]):
                    if f['filterType'] == 'LOT_SIZE':
                        step = float(f['stepSize'])
                        qp = _calc_precision(step)  # ← 修复：用Decimal计算
                    elif f['filterType'] == 'PRICE_FILTER':
                        tick = float(f['tickSize'])
                        pp = _calc_precision(tick)   # ← 修复：用Decimal计算
                    elif f['filterType'] == 'MIN_NOTIONAL':
                        min_n = float(f.get('notional', f.get('minNotional', 5.0)))
                return {'step_size': step, 'qty_precision': qp,
                        'tick_size': tick, 'price_precision': pp, 'min_notional': min_n}
    except: pass
    return {'step_size': 0.001, 'qty_precision': 3, 'tick_size': 0.01, 'price_precision': 2, 'min_notional': 5.0}


def calc_position_size(signal: dict, nav: float = 100.0) -> dict:
    """
    基于2%风险计算仓位
    signal 需要包含: entry_lo/entry_hi/stop_loss/tp1/direction/symbol/score
    """
    symbol    = signal.get('symbol', 'ETHUSDT')
    entry_lo  = float(signal.get('entry_lo', signal.get('params',{}).get('entry_lo', 0)))
    entry_hi  = float(signal.get('entry_hi', signal.get('params',{}).get('entry_hi', entry_lo)))
    sl        = float(signal.get('stop_loss', signal.get('params',{}).get('stop_loss', 0)))
    tp1       = float(signal.get('tp1', signal.get('params',{}).get('tp1', 0)))
    direction = signal.get('direction','SHORT').upper()
    leverage  = 5  # 设计院封印：系统只允许5倍合约

    entry_mid = (entry_lo + entry_hi) / 2 if entry_hi else entry_lo

    if not entry_mid or not sl:
        return None

    sl_pct    = abs(entry_mid - sl) / entry_mid
    risk_usdt = nav * 0.02                   # 2% NAV 风险
    notional  = risk_usdt / sl_pct * 1.0    # 名义价值
    margin    = notional / leverage

    sinfo     = get_symbol_info(symbol)
    step      = sinfo['step_size']
    qp        = sinfo['qty_precision']
    qty       = math.floor(notional / entry_mid / step) * step
    qty       = max(qty, step)

    rr = abs(entry_mid - tp1) / abs(entry_mid - sl) if sl != entry_mid else 0

    # 价格对齐tick_size（防止-4014 Price not increased by tick size）
    # 用Decimal确保浮点精度
    from decimal import Decimal, ROUND_DOWN, ROUND_UP
    _tick = sinfo.get('tick_size', 0.01)
    _pp   = sinfo.get('price_precision', 2)
    _tick_d = Decimal(str(_tick))
    _entry_aligned = float(Decimal(str(entry_mid)).quantize(_tick_d, rounding=ROUND_DOWN))
    # SHORT: sl向上取整（更宽），tp1向下取整（更近）
    # LONG:  sl向下取整（更宽），tp1向上取整（更近）
    _dir = signal.get('direction','SHORT').upper()
    if _dir == 'SHORT':
        _sl_aligned  = float(Decimal(str(sl)).quantize(_tick_d, rounding=ROUND_UP))
        _tp1_aligned = float(Decimal(str(tp1)).quantize(_tick_d, rounding=ROUND_DOWN))
    else:
        _sl_aligned  = float(Decimal(str(sl)).quantize(_tick_d, rounding=ROUND_DOWN))
        _tp1_aligned = float(Decimal(str(tp1)).quantize(_tick_d, rounding=ROUND_UP))

    return {
        'qty':             qty,
        'qty_precision':   qp,
        'tick_size':       _tick,        # ← 新增：透传给executor用于价格对齐
        'price_precision': _pp,
        'notional':        round(qty * entry_mid, 2),
        'entry_price':     _entry_aligned,
        'sl_price':        _sl_aligned,
        'tp1_price':       _tp1_aligned,
        'tp2_price':       signal.get('tp2'),
        'leverage':        leverage,
        'risk_usdt':       round(risk_usdt, 2),
        'sl_pct':          round(sl_pct * 100, 2),
        'rr':              round(rr, 2),
        'margin':          round(margin, 2),
    }
