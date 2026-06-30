"""
btc_entry_execute.py — BTC SHORT 自动入场脚本
梵天自主执行 2026-06-29 10:42 UTC

信号参数：
  方向    SHORT
  体制    BEAR_TREND WR=64.5%
  评分    141.7 极强 valid=True
  入场区  $60,171 ~ $60,292
  止损    $61,436（SL=2.0%）
  TP1     $59,027（RR=1.0）
  TP2     $57,822（RR=2.0）
  仓位    NAV×2% = $10.12 → 0.0005 BTC × 3x
"""

import requests, hmac, hashlib, time, json, sys
sys.path.insert(0, '/root/.openclaw/workspace/trading-system')
from brahma_brain.brahma_bus import bus

API_KEY = 'sDqoRAyeYHHzevKNxSj5JfkWpNUd6v8qPAhVy0Y8wbWGwC48eC7uhFOENAlVqV7b'
SECRET  = 'hXQnzQco9SNVgKgF2m3xvBGlJjOHBVtlzqRlxOTkp0kiJAwAOTeUiGLQSAopqIj7'
FAPI    = 'https://fapi.binance.com'

ENTRY_LO   = 60171.31
ENTRY_HI   = 60291.65
STOP_LOSS  = 61436.11
TP1        = 59026.85
TP2        = 57822.22
QTY        = 0.001        # BTC数量（精度调整后）
LEVERAGE   = 3


def signed_post(endpoint, params):
    ts = int(time.time() * 1000)
    params['timestamp'] = ts
    qs = '&'.join(f'{k}={v}' for k, v in params.items())
    sig = hmac.new(SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()
    r = requests.post(f'{FAPI}{endpoint}?{qs}&signature={sig}',
                      headers={'X-MBX-APIKEY': API_KEY}, timeout=10)
    return r.json()


def execute_short():
    px = bus.price('BTCUSDT')
    print(f'当前价: ${px:.2f}  入场区: ${ENTRY_LO:.0f}~${ENTRY_HI:.0f}')

    if not (ENTRY_LO * 0.998 <= px <= ENTRY_HI * 1.002):
        print(f'❌ 价格不在入场区，取消执行')
        return False

    # 1. 设置杠杆
    lev = signed_post('/fapi/v1/leverage', {'symbol': 'BTCUSDT', 'leverage': LEVERAGE})
    print(f'杠杆设置: {lev}')

    # 2. 市价做空
    order = signed_post('/fapi/v1/order', {
        'symbol':   'BTCUSDT',
        'side':     'SELL',
        'type':     'MARKET',
        'quantity': QTY,
        'reduceOnly': 'false',
    })
    print(f'开仓结果: {json.dumps(order, ensure_ascii=False)}')

    if order.get('status') in ('FILLED', 'NEW', 'PARTIALLY_FILLED'):
        fill_price = float(order.get('avgPrice', order.get('price', px)))
        print(f'✅ 开仓成功！fill={fill_price:.2f}  qty={QTY}  方向=SHORT')

        # 3. 写入 wuqu_positions
        with open('data/wuqu_positions.json') as f:
            wp = json.load(f)
        wp['BTCUSDT'] = {
            'symbol': 'BTCUSDT', 'direction': 'SHORT',
            'qty': QTY, 'entry_price': fill_price,
            'stop_loss': STOP_LOSS, 'tp1': TP1, 'tp2': TP2,
            'leverage': LEVERAGE, 'opened_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            'source': 'brahma_auto_execute',
        }
        with open('data/wuqu_positions.json', 'w') as f:
            json.dump(wp, f, indent=2, ensure_ascii=False)

        # 4. 写入 position_sl_state
        with open('data/position_sl_state.json') as f:
            sl = json.load(f)
        sl['BTCUSDT'] = {
            'sl_price': STOP_LOSS, 'tp_price': TP1,
            'direction': 'SHORT', 'regime': 'BEAR_TREND',
            'score': 141.7, 'registered_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            'source': 'brahma_auto_execute',
        }
        with open('data/position_sl_state.json', 'w') as f:
            json.dump(sl, f, indent=2, ensure_ascii=False)

        print(f'✅ 止损登记完成  SL=${STOP_LOSS:.0f}  TP1=${TP1:.0f}')
        return True
    else:
        print(f'❌ 开仓失败: {order}')
        return False


if __name__ == '__main__':
    execute_short()
