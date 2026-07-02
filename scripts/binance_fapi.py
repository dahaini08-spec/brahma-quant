#!/usr/bin/env python3
"""
binance_fapi.py — Binance FAPI 下单执行层
设计院 2026-06-04

真实 REST 下单：限价单 + 止损单 + 止盈单
"""
import hmac, hashlib, json, os, time, urllib.request, urllib.parse
from pathlib import Path

ENV_FILE = Path(__file__).parent.parent.parent / 'alerts' / '.env'

def _load_env():
    global API_KEY, API_SECRET
    try:
        from dotenv import load_dotenv
        load_dotenv(ENV_FILE)
    except ImportError:
        # 手动解析 .env
        if ENV_FILE.exists():
            for line in open(ENV_FILE):
                line = line.strip()
                if '=' in line and not line.startswith('#'):
                    k,_,v = line.partition('=')
                    os.environ.setdefault(k.strip(), v.strip())
    API_KEY    = os.getenv('BINANCE_API_KEY','')
    API_SECRET = os.getenv('BINANCE_SECRET', os.getenv('BINANCE_SECRET_KEY',''))

_load_env()
API_KEY    = os.getenv('BINANCE_API_KEY','')
API_SECRET = os.getenv('BINANCE_SECRET', os.getenv('BINANCE_SECRET_KEY',''))
BASE_URL   = 'https://fapi.binance.com'

def _sign(params: dict) -> str:
    qs = urllib.parse.urlencode(params)
    sig = hmac.new(API_SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()
    return sig

def _req(method: str, path: str, params: dict = None, body: dict = None):
    params = params or {}
    params['timestamp'] = int(time.time() * 1000)
    params['signature'] = _sign(params)
    qs = urllib.parse.urlencode(params)
    url = f'{BASE_URL}{path}?{qs}'
    data = urllib.parse.urlencode(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header('X-MBX-APIKEY', API_KEY)
    req.add_header('Content-Type', 'application/x-www-form-urlencoded')
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read()), None
    except urllib.error.HTTPError as e:
        err = e.read().decode()
        return None, err
    except Exception as e:
        return None, str(e)

def set_leverage(symbol: str, leverage: int):
    params = {'symbol': symbol, 'leverage': leverage}
    return _req('POST', '/fapi/v1/leverage', params=params)

def _round_to_tick(price: float, tick_size: float) -> float:
    """将价格严格对齐到tick_size精度，避免-4014 / -1111错误"""
    from decimal import Decimal, ROUND_DOWN
    tick_d = Decimal(str(tick_size))
    return float(Decimal(str(price)).quantize(tick_d, rounding=ROUND_DOWN))


def place_order(symbol: str, side: str, order_type: str,
                qty: float, price: float = None,
                stop_price: float = None,
                reduce_only: bool = False,
                qty_precision: int = 3,
                price_precision: int = None,
                tick_size: float = None) -> tuple:
    """
    side: BUY / SELL
    order_type: LIMIT / STOP_MARKET / TAKE_PROFIT_MARKET
    price_precision: 价格小数位数（优先用tick_size对齐）
    tick_size: 价格步长（用于精确对齐，防止-4014）
    返回: (result, error)
    """
    try:
        params = {
            'symbol':       symbol,
            'side':         side,
            'type':         order_type,
            'positionSide': 'BOTH',
        }
        # 数量：LIMIT用quantity，STOP_MARKET/TAKE_PROFIT_MARKET也用quantity
        params['quantity'] = round(qty, qty_precision)

        if order_type == 'LIMIT':
            params['timeInForce'] = 'GTC'
            if price:
                if tick_size:
                    # 严格tick对齐，防止-4014
                    params['price'] = _round_to_tick(price, tick_size)
                elif price_precision is not None:
                    params['price'] = round(price, price_precision)
                else:
                    params['price'] = round(price, 8)  # fallback（可能触发-4014）
        elif order_type in ('STOP_MARKET', 'TAKE_PROFIT_MARKET'):
            if stop_price:
                if tick_size:
                    params['stopPrice'] = _round_to_tick(stop_price, tick_size)
                elif price_precision is not None:
                    params['stopPrice'] = round(stop_price, price_precision)
                else:
                    params['stopPrice'] = round(stop_price, 8)
            params['reduceOnly'] = 'true' if reduce_only else 'false'
            # 不传price字段，用市价成交

        return _req('POST', '/fapi/v1/order', params=params)
    except Exception as _e:
        import logging as _log
        _log.getLogger('brahma.execution').error(
            f'[EXEC_GUARD] place_order 执行异常: {_e}', exc_info=True)
        return {'error': str(_e), 'func': 'place_order', 'status': 'FAILED'}

def cancel_order(symbol: str, order_id: int) -> tuple:
    params = {'symbol': symbol, 'orderId': order_id}
    return _req('DELETE', '/fapi/v1/order', params=params)

def get_open_orders(symbol: str) -> tuple:
    params = {'symbol': symbol}
    return _req('GET', '/fapi/v1/openOrders', params=params)

def get_positions(symbol: str = None) -> tuple:
    params = {'symbol': symbol} if symbol else {}
    return _req('GET', '/fapi/v2/positionRisk', params=params)

def get_account() -> tuple:
    return _req('GET', '/fapi/v2/account', params={})


def place_algo_order(symbol: str, side: str, order_type: str,
                     qty: float, trigger_price: float,
                     reduce_only: bool = True,
                     qty_precision: int = 3) -> tuple:
    """
    止损/止盈单 — 走 /fapi/v1/algo/orders 端点 (Algo API)
    order_type: STOP_MARKET / TAKE_PROFIT_MARKET
    trigger_price: 触发价格
    """
    params = {
        'symbol':       symbol,
        'side':         side,
        'type':         order_type,
        'algoType':     'CONDITIONAL',
        'quantity':     round(qty, qty_precision),
        'triggerPrice': round(trigger_price, 8),
        'reduceOnly':   'true' if reduce_only else 'false',
        'positionSide': 'BOTH',
        'workingType':  'MARK_PRICE',
    }
    return _req('POST', '/fapi/v1/algo/orders', params=params)


def cancel_algo_order(symbol: str, algo_id: int) -> tuple:
    params = {'symbol': symbol, 'algoId': algo_id}
    return _req('DELETE', '/fapi/v1/algo/orders', params=params)


def get_algo_orders(symbol: str = None) -> tuple:
    params = {'symbol': symbol} if symbol else {}
    return _req('GET', '/fapi/v1/algo/orders/current', params=params)


def get_account_balance(asset='USDT') -> float:
    """获取期货账户余额（TODO-003修复）"""
    import hmac, hashlib, urllib.request, time as _time, json as _json
    tools_path = '/root/.openclaw/workspace/TOOLS.md'
    api_key = secret = None
    for line in open(tools_path):
        if '- API Key:' in line: api_key = line.split('API Key:')[1].strip().split()[0]
        elif '- Secret:' in line: secret = line.split('Secret:')[1].strip().split()[0]
    if not (api_key and secret): raise ValueError('API Key未找到')
    ts = int(_time.time()*1000)
    params = f'timestamp={ts}'
    sig = hmac.new(secret.encode(), params.encode(), hashlib.sha256).hexdigest()
    url = f'https://fapi.binance.com/fapi/v2/balance?{params}&signature={sig}'
    req = urllib.request.Request(url, headers={'X-MBX-APIKEY': api_key})
    data = _json.loads(urllib.request.urlopen(req, timeout=8).read())
    for item in data:
        if item.get('asset') == asset:
            return float(item.get('balance', 0))
    return 0.0
