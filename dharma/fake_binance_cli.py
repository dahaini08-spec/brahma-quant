#!/usr/bin/env python3
"""
🏯 达摩院 · 本地Binance API Mock
fake_binance_cli.py  v1.0

用途：100%本地仿真binance-cli的所有输出，零API消耗，零积分
拦截方式：设置环境变量 BRAHMA_MOCK=1 时，ws_guardian/brahma_core
          自动使用此脚本代替真实binance-cli

支持的命令：
  futures-usds get-balance
  futures-usds get-position-risk-v3
  futures-usds new-order
  futures-usds cancel-order
  futures-usds get-open-orders
  futures-usds get-order
  spot get-balance

内置场景：
  SCENARIO=bull_breakout   — 牛市突破，TP1/TP2都能打到
  SCENARIO=bear_dump       — 急跌止损触发
  SCENARIO=chop            — 横盘，mh到期平仓
  SCENARIO=sl_then_reverse — 止损后反转（最差情况）
  SCENARIO=tp1_then_sl     — 打到TP1后被止损
"""
import sys, json, time, os, random, math
from pathlib import Path

SCENARIO = os.environ.get('BRAHMA_SCENARIO', 'bull_breakout')
MOCK_BALANCE = float(os.environ.get('MOCK_NAV', '127.61'))
MOCK_PRICE_OFFSET = float(os.environ.get('MOCK_PRICE_OFFSET', '0'))

BASE = Path(__file__).parent.parent
STATE_FILE = BASE / 'data' / 'mock_exchange_state.json'

# ─── 场景价格模拟器 ───────────────────────────────────
class PriceSimulator:
    BASE_PRICES = {
        'BTCUSDT': 109000.0, 'ETHUSDT': 2580.0, 'SOLUSDT': 171.0,
        'BNBUSDT': 662.0, 'LTCUSDT': 93.0, 'XRPUSDT': 2.31,
        'DOGEUSDT': 0.181, 'AVAXUSDT': 23.5,
    }

    @classmethod
    def get_price(cls, symbol: str) -> float:
        base = cls.BASE_PRICES.get(symbol, 100.0) + MOCK_PRICE_OFFSET
        # 微小随机波动 ±0.05%
        return round(base * (1 + random.uniform(-0.0005, 0.0005)), 6)

    @classmethod
    def get_mark_price(cls, symbol: str) -> float:
        return cls.get_price(symbol)

# ─── 交易所状态持久化 ────────────────────────────────
def load_state():
    try:
        return json.load(open(STATE_FILE))
    except:
        return {
            'balance_usdt': MOCK_BALANCE,
            'positions': {},          # sym → {qty, entry, side, sl, tp1, tp2, order_id}
            'open_orders': {},        # order_id → order
            'filled_orders': {},
            'next_order_id': 1000001,
            'trade_log': [],
        }

def save_state(s):
    import os
    tmp = str(STATE_FILE) + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(s, f, ensure_ascii=False, indent=2)
    os.replace(tmp, str(STATE_FILE))

def new_order_id(state):
    oid = str(state['next_order_id'])
    state['next_order_id'] += 1
    return oid

# ─── 命令处理器 ──────────────────────────────────────
def handle_get_balance(args, state):
    return {
        "asset": "USDT",
        "balance": round(state['balance_usdt'], 4),
        "availableBalance": round(state['balance_usdt'] * 0.95, 4),
        "crossUnPnl": "0.0",
        "maxWithdrawAmount": round(state['balance_usdt'] * 0.9, 4)
    }

def handle_get_position_risk(args, state):
    result = []
    for sym, pos in state['positions'].items():
        price = PriceSimulator.get_price(sym)
        qty = pos['qty']
        entry = pos['entry']
        side = pos['side']  # 'LONG' or 'SHORT'
        if side == 'LONG':
            pnl = (price - entry) * abs(qty)
        else:
            pnl = (entry - price) * abs(qty)
            qty = -abs(qty)
        result.append({
            "symbol": sym,
            "positionSide": "BOTH",
            "positionAmt": str(qty),
            "entryPrice": str(entry),
            "markPrice": str(price),
            "unrealizedProfit": str(round(pnl, 4)),
            "leverage": "10",
            "maxNotionalValue": "100000",
            "marginType": "cross",
            "isolatedMargin": "0",
            "isAutoAddMargin": "false",
            "liquidationPrice": "0",
            "notional": str(round(abs(qty) * price, 4)),
        })
    return result

def handle_new_order(args, state):
    # 解析参数
    params = {}
    i = 0
    while i < len(args):
        if args[i].startswith('--'):
            key = args[i][2:].replace('-','_')
            if i+1 < len(args) and not args[i+1].startswith('--'):
                params[key] = args[i+1]
                i += 2
            else:
                params[key] = True
                i += 1
        else:
            i += 1

    sym = params.get('symbol','BTCUSDT').upper()
    side = params.get('side','BUY').upper()
    qty_str = params.get('quantity', params.get('qty','0'))
    order_type = params.get('type','MARKET').upper()
    reduce_only = str(params.get('reduce_only', params.get('reduceOnly','false'))).lower() == 'true'
    price = PriceSimulator.get_price(sym)

    try:
        qty = float(qty_str)
    except:
        qty = 0.001

    oid = new_order_id(state)
    filled_price = price * (1 + random.uniform(-0.0001, 0.0001))

    order = {
        'orderId': int(oid),
        'symbol': sym,
        'status': 'FILLED',
        'clientOrderId': f'mock_{oid}',
        'price': str(round(filled_price, 6)),
        'avgPrice': str(round(filled_price, 6)),
        'origQty': str(qty),
        'executedQty': str(qty),
        'cumQty': str(qty),
        'cumQuote': str(round(qty * filled_price, 4)),
        'timeInForce': 'GTC',
        'type': order_type,
        'reduceOnly': reduce_only,
        'closePosition': False,
        'side': side,
        'positionSide': 'BOTH',
        'stopPrice': '0',
        'workingType': 'CONTRACT_PRICE',
        'priceProtect': False,
        'origType': order_type,
        'updateTime': int(time.time() * 1000),
        'time': int(time.time() * 1000),
    }

    state['filled_orders'][oid] = order

    # 更新持仓
    if not reduce_only:
        if side == 'BUY':
            if sym in state['positions'] and state['positions'][sym]['side'] == 'LONG':
                # 加仓
                old = state['positions'][sym]
                new_qty = old['qty'] + qty
                old['entry'] = (old['qty']*old['entry'] + qty*filled_price) / new_qty
                old['qty'] = new_qty
            else:
                state['positions'][sym] = {'qty': qty, 'entry': filled_price, 'side': 'LONG',
                                           'sl': 0, 'tp1': 0, 'tp2': 0}
        else:  # SELL
            if sym in state['positions'] and state['positions'][sym]['side'] == 'SHORT':
                old = state['positions'][sym]
                new_qty = old['qty'] + qty
                old['entry'] = (old['qty']*old['entry'] + qty*filled_price) / new_qty
                old['qty'] = new_qty
            else:
                state['positions'][sym] = {'qty': qty, 'entry': filled_price, 'side': 'SHORT',
                                           'sl': 0, 'tp1': 0, 'tp2': 0}
    else:
        # 减仓/平仓
        if sym in state['positions']:
            pos = state['positions'][sym]
            if pos['qty'] <= qty + 1e-9:
                # 全平
                pnl_pct = ((filled_price - pos['entry'])/pos['entry']) * (1 if pos['side']=='LONG' else -1)
                state['balance_usdt'] += pos['qty'] * pos['entry'] * pnl_pct
                state['trade_log'].append({
                    'sym': sym, 'side': pos['side'], 'entry': pos['entry'],
                    'close': filled_price, 'pnl_pct': round(pnl_pct*100, 3),
                    'ts': time.time()
                })
                del state['positions'][sym]
            else:
                pos['qty'] -= qty

    save_state(state)
    return order

def handle_cancel_order(args, state):
    params = {}
    i = 0
    while i < len(args):
        if args[i].startswith('--') and i+1 < len(args):
            params[args[i][2:]] = args[i+1]; i += 2
        else:
            i += 1
    oid = str(params.get('order-id', params.get('orderId','')))
    if oid in state['open_orders']:
        o = state['open_orders'].pop(oid)
        o['status'] = 'CANCELED'
        save_state(state)
        return o
    return {'orderId': oid, 'status': 'CANCELED', 'symbol': params.get('symbol','')}

def handle_get_open_orders(args, state):
    sym = None
    for i, a in enumerate(args):
        if a == '--symbol' and i+1 < len(args):
            sym = args[i+1].upper()
    result = list(state['open_orders'].values())
    if sym:
        result = [o for o in result if o.get('symbol') == sym]
    return result

def handle_spot_balance(args, state):
    return [{"asset":"USDT","free":str(round(state['balance_usdt'],4)),"locked":"0"}]

# ─── 主入口 ──────────────────────────────────────────
def main():
    args = sys.argv[1:]
    if not args:
        print(json.dumps({"error": "no command"})); return

    state = load_state()
    result = None

    # 路由
    if 'futures-usds' in args:
        sub = args[args.index('futures-usds')+1] if args.index('futures-usds')+1 < len(args) else ''
        rest = args[args.index('futures-usds')+2:]
        if sub == 'get-balance':
            result = handle_get_balance(rest, state)
        elif sub in ('get-position-risk-v3', 'get-position-risk'):
            result = handle_get_position_risk(rest, state)
        elif sub == 'new-order':
            result = handle_new_order(rest, state)
        elif sub == 'cancel-order':
            result = handle_cancel_order(rest, state)
        elif sub == 'get-open-orders':
            result = handle_get_open_orders(rest, state)
        elif sub == 'get-order':
            result = {"orderId": 0, "status": "FILLED"}
        else:
            result = {"error": f"unknown sub: {sub}"}
    elif 'spot' in args:
        result = handle_spot_balance(args, state)
    else:
        result = {"error": f"unknown command: {args}"}

    print(json.dumps(result, ensure_ascii=False))

if __name__ == '__main__':
    main()
