#!/usr/bin/env python3
"""
hunter_executor.py — 梵天执行层 v1.0
设计院 2026-06-04

execute_open:  下三联单（限价+止损+止盈）
execute_close: 市价平仓
"""
import json, sys, time
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE/'scripts'))
import binance_fapi as bf

STATE_FILE = BASE / 'data' / 'adaptive_order_state.json'
EXEC_LOG   = BASE / 'data' / 'pipeline_exec_log.jsonl'

def _register_soft_sl(symbol: str, direction: str, qty: float,
                       entry: float, sl: float, tp: float):
    """
    PM账户无法挂STOP_MARKET条件单，改用软止损监控注册。
    position_sl_monitor.py 每30分钟轮询，触发时市价平仓。
    """
    import os
    sl_file = BASE / 'data' / 'position_sl_state.json'
    try:
        state = json.loads(sl_file.read_text()) if sl_file.exists() else {}
    except Exception:
        state = {}
    state[symbol] = {
        'symbol':         symbol,
        'side':           direction,          # LONG / SHORT
        'qty':            abs(qty),
        'entry':          entry,
        'sl_price':       sl,
        'tp_price':       tp,
        'registered_at':  datetime.now(timezone.utc).isoformat(),
        'source':         'hunter_executor',
    }
    tmp = str(sl_file) + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    os.replace(tmp, str(sl_file))
    print(f'[Executor] 📋 软止损已注册: {symbol} SL={sl:.6f} TP={tp:.6f}')


def _save_state(state: dict):
    import os
    tmp = str(STATE_FILE)+'.tmp'
    with open(tmp,'w') as f: json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, str(STATE_FILE))

def _log(entry: dict):
    entry['ts'] = datetime.now(timezone.utc).isoformat()
    with open(EXEC_LOG,'a') as f:
        f.write(json.dumps(entry, ensure_ascii=False)+'\n')

def execute_open(signal: dict, sizing: dict, dry_run: bool = True) -> dict:
    """
    下三联单：
      1. 限价做空单（入场）
      2. STOP市价止损单（减仓）
      3. TAKE_PROFIT市价止盈单

    dry_run=True → 仅模拟，不下真实订单
    """
    symbol     = signal['symbol'] if 'USDT' in signal.get('symbol','') else signal['symbol']+'USDT'
    direction  = signal.get('direction','SHORT').upper()
    entry_side = 'SELL' if direction == 'SHORT' else 'BUY'
    close_side = 'BUY'  if direction == 'SHORT' else 'SELL'

    qty       = sizing['qty']
    qp        = sizing.get('qty_precision', 3)
    entry_p   = sizing['entry_price']
    sl_p      = sizing['sl_price']
    tp1_p     = sizing['tp1_price']
    leverage  = sizing.get('leverage', 3)
    notional  = sizing.get('notional', qty * entry_p)

    result = {'status': 'OK', 'orders': [], 'errors': [], 'mode': 'paper' if dry_run else 'live'}

    if dry_run:
        result['orders'] = [
            {'type':'LIMIT',        'side': entry_side, 'qty': qty, 'price': round(entry_p,6)},
            {'type':'STOP_MARKET',  'side': close_side, 'qty': qty, 'price': round(sl_p,6)},
            {'type':'TAKE_PROFIT',  'side': close_side, 'qty': qty, 'price': round(tp1_p,6)},
        ]
        _log({'type':'paper_open', 'symbol': symbol, 'direction': direction,
              'entry': entry_p, 'sl': sl_p, 'tp1': tp1_p, 'qty': qty, 'notional': notional})
        print(f'[Executor] 🟡 PAPER: {symbol} {direction} qty={qty} entry={entry_p:.6f} sl={sl_p:.6f} tp1={tp1_p:.6f}')
        result['success'] = True
        result['fill_price'] = entry_p
        result['qty'] = qty
        return result

    # ── 实盘 ─────────────────────────────────────────────────
    # Step0: 设置杠杆
    r, e = bf.set_leverage(symbol, leverage)
    if e:
        result['errors'].append(f'杠杆设置失败: {e}')
        # 非致命，继续

    order_ids = {}

    # Step1: 限价入场单
    r1, e1 = bf.place_order(symbol, entry_side, 'LIMIT', qty, price=entry_p,
                             qty_precision=qp, tick_size=sizing.get('tick_size'))
    if e1 or not r1:
        result['status'] = 'FAILED'
        result['errors'].append(f'入场单失败: {e1}')
        _log({'type':'order_fail', 'step':'entry', 'error': e1, 'symbol': symbol})
        return result
    order_ids['entry'] = r1.get('orderId')
    result['orders'].append({'type':'LIMIT', 'side': entry_side, 'qty': qty,
                              'price': entry_p, 'orderId': order_ids['entry']})
    print(f'[Executor] ✅ 入场单 orderId={order_ids["entry"]} {symbol} {entry_side} qty={qty} @{entry_p:.6f}')

    # ── Step2/3: 止损 & 止盈 ──────────────────────────────────────────
    # [P2-4 设计院 2026-06-24] 账户核查结果：multiAssetsMargin=False，非PM账户
    # STOP_MARKET 和 TAKE_PROFIT_MARKET 均在 exchangeInfo.orderTypes 中支持
    # 原"PM账户限制"注释为历史错误遗留，现修复为真实条件止损单
    r2, e2 = bf.place_order(symbol, close_side, 'STOP_MARKET', qty,
                             stop_price=sl_p, reduce_only=True,
                             qty_precision=qp, tick_size=sizing.get('tick_size'))
    if e2:
        # 降级：止损下单失败时回退到软止损（避免裸露持仓）
        _register_soft_sl(symbol, direction, qty, entry_p, sl_p, tp1_p)
        result['errors'].append(f'止损单失败(降级软止损): {e2}')
        print(f'[Executor] ⚠️  止损单失败，降级软止损 SL={sl_p:.6f}  err={e2}')
    else:
        order_ids['stop_loss'] = r2.get('orderId')
        result['orders'].append({'type':'STOP_MARKET', 'side': close_side, 'qty': qty,
                                  'stopPrice': sl_p, 'orderId': order_ids['stop_loss']})
        print(f'[Executor] ✅ 止损单 orderId={order_ids["stop_loss"]} stopPrice={sl_p:.6f}')

    r3, e3 = bf.place_order(symbol, close_side, 'TAKE_PROFIT_MARKET', qty,
                             stop_price=tp1_p, reduce_only=True,
                             qty_precision=qp, tick_size=sizing.get('tick_size'))
    if e3:
        result['errors'].append(f'止盈单失败: {e3}')
        print(f'[Executor] ⚠️  止盈单失败: {e3}')
    else:
        order_ids['take_profit'] = r3.get('orderId')
        result['orders'].append({'type':'TAKE_PROFIT_MARKET', 'side': close_side, 'qty': qty,
                                  'stopPrice': tp1_p, 'orderId': order_ids['take_profit']})
        print(f'[Executor] ✅ 止盈单 orderId={order_ids["take_profit"]} stopPrice={tp1_p:.6f}')

    # 同时保留软止损作为双重保险（防止交易所侧条件单失效）
    _register_soft_sl(symbol, direction, qty, entry_p, sl_p, tp1_p)

    # 保存状态
    state = {
        'symbol': symbol, 'direction': direction,
        'entry_price': entry_p, 'sl_price': sl_p, 'tp1_price': tp1_p,
        'qty': qty, 'leverage': leverage, 'notional': notional,
        'order_ids': order_ids,
        'opened_at': datetime.now(timezone.utc).isoformat(),
        'status': 'OPEN',
    }
    _save_state(state)
    _log({'type':'live_open', 'symbol': symbol, 'direction': direction,
          'entry': entry_p, 'sl': sl_p, 'tp1': tp1_p, 'qty': qty,
          'order_ids': order_ids})
    # ← 修复：实盘路径必须设置 success=True，否则 auto_execute_gate 以为失败导致重复建仓
    result['success'] = True
    result['fill_price'] = entry_p
    result['qty'] = qty
    return result


def execute_close(symbol: str, reason: str = 'manual') -> dict:
    """市价平仓"""
    positions, e = bf.get_positions(symbol)
    if e or not positions:
        return {'status': 'ERROR', 'error': e}
    pos = next((p for p in positions if float(p.get('positionAmt',0)) != 0), None)
    if not pos:
        return {'status': 'NO_POSITION'}
    amt = float(pos['positionAmt'])
    side = 'BUY' if amt < 0 else 'SELL'
    qty = abs(amt)
    r, e2 = bf.place_order(symbol, side, 'MARKET', qty, reduce_only=True)
    _log({'type':'close', 'symbol': symbol, 'reason': reason, 'qty': qty, 'result': r})
    return {'status': 'OK', 'result': r, 'error': e2}
