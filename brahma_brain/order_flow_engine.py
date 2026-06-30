#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║  梵天大脑 · order_flow_engine.py  · P2c 订单流引擎               ║
║  数据源：Binance Futures 公开端点（主动买卖 + 深度 + 大单）        ║
║  核心：机构vs散户主动成交方向、挂单失衡、冰山大单追踪               ║
╚══════════════════════════════════════════════════════════════════╝
"""
import urllib.request, json, time

FAPI = "https://fapi.binance.com"
_cache: dict = {}
_TTL = 60  # 1分钟

def _get(url: str):
    now = time.time()
    if url in _cache and now - _cache[url]['ts'] < _TTL:
        return _cache[url]['data']
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'brahma/4.0'})
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read())
            _cache[url] = {'ts': now, 'data': data}
            return data
    except Exception:
        return None


def get_order_book_imbalance(symbol: str, depth: int = 20) -> dict:
    """
    订单簿失衡：bid_vol vs ask_vol
    机构建仓时会造成bid_vol > ask_vol（做多）
    """
    url = f"{FAPI}/fapi/v1/depth?symbol={symbol}&limit={depth}"
    data = _get(url)
    if not data:
        return {'bid_vol': 0, 'ask_vol': 0, 'imbalance': 0.0, 'bias': 'NEUTRAL'}

    bid_vol = sum(float(b[1]) for b in data.get('bids', []))
    ask_vol = sum(float(a[1]) for a in data.get('asks', []))
    total = bid_vol + ask_vol
    if total == 0:
        return {'bid_vol': 0, 'ask_vol': 0, 'imbalance': 0.0, 'bias': 'NEUTRAL'}

    imbalance = (bid_vol - ask_vol) / total  # -1 ~ +1

    if imbalance > 0.15:
        bias = 'BUY_WALL'    # 大量买单挂单，支撑强
    elif imbalance < -0.15:
        bias = 'SELL_WALL'   # 大量卖单挂单，压力大
    else:
        bias = 'NEUTRAL'

    return {
        'bid_vol':    round(bid_vol, 2),
        'ask_vol':    round(ask_vol, 2),
        'imbalance':  round(imbalance, 3),
        'bias':       bias,
    }


def get_recent_trades_flow(symbol: str, limit: int = 100) -> dict:
    """
    最近成交主动买卖比（大单方向）
    """
    url = f"{FAPI}/fapi/v1/aggTrades?symbol={symbol}&limit={limit}"
    data = _get(url)
    if not data:
        return {'buy_vol': 0, 'sell_vol': 0, 'buy_pct': 50.0, 'large_buy': 0, 'large_sell': 0}

    buy_vol = sell_vol = 0
    large_buy = large_sell = 0

    if data:
        # 估算大单阈值（前20%成交量）
        vols = [float(t['q']) for t in data]
        threshold = sorted(vols)[-max(1, len(vols)//5)]

        for t in data:
            qty = float(t['q'])
            is_buy = not t['m']  # m=True表示maker是买单（即taker是卖单）
            if is_buy:
                buy_vol += qty
                if qty >= threshold:
                    large_buy += 1
            else:
                sell_vol += qty
                if qty >= threshold:
                    large_sell += 1

    total = buy_vol + sell_vol
    buy_pct = buy_vol / total * 100 if total > 0 else 50.0

    return {
        'buy_vol':   round(buy_vol, 4),
        'sell_vol':  round(sell_vol, 4),
        'buy_pct':   round(buy_pct, 2),
        'large_buy': large_buy,
        'large_sell': large_sell,
    }


def order_flow_score(symbol: str, signal_dir: str) -> dict:
    """
    订单流综合评分 0~15分
      挂单失衡方向  0~7
      大单成交方向  0~8
    """
    is_long = signal_dir in ('LONG', '做多')
    ob  = get_order_book_imbalance(symbol)
    tf  = get_recent_trades_flow(symbol)

    s_ob = 0
    if is_long and ob['bias'] == 'BUY_WALL':
        s_ob = 7
        ob_note = '挂单BUY_WALL支撑'
    elif not is_long and ob['bias'] == 'SELL_WALL':
        s_ob = 7
        ob_note = '挂单SELL_WALL压力'
    elif ob['bias'] == 'NEUTRAL':
        s_ob = 3
        ob_note = '挂单均衡'
    else:
        s_ob = 1
        ob_note = '挂单逆向'

    s_tf = 0
    buy_pct = tf['buy_pct']
    lb, ls = tf['large_buy'], tf['large_sell']
    if is_long:
        if buy_pct > 58 and lb > ls:
            s_tf = 8; tf_note = f'大单做多{buy_pct:.1f}%  大单:{lb}vs{ls}'
        elif buy_pct > 52:
            s_tf = 5; tf_note = f'主动买入偏多{buy_pct:.1f}%'
        else:
            s_tf = 2; tf_note = f'主动买入不足{buy_pct:.1f}%'
    else:
        sell_pct = 100 - buy_pct
        if sell_pct > 58 and ls > lb:
            s_tf = 8; tf_note = f'大单做空{sell_pct:.1f}%  大单:{ls}vs{lb}'
        elif sell_pct > 52:
            s_tf = 5; tf_note = f'主动卖出偏多{sell_pct:.1f}%'
        else:
            s_tf = 2; tf_note = f'主动卖出不足{sell_pct:.1f}%'

    total = min(s_ob + s_tf, 15)
    return {
        'score':    total,
        'breakdown': {'ob_score': s_ob, 'tf_score': s_tf},
        'notes':    [ob_note, tf_note],
        'raw':      {'order_book': ob, 'trade_flow': tf},
    }


if __name__ == '__main__':
    import sys
    sym = sys.argv[1] if len(sys.argv) > 1 else 'BTCUSDT'
    dr  = sys.argv[2] if len(sys.argv) > 2 else 'SHORT'
    r = order_flow_score(sym, dr)
    print(f"\n=== 订单流引擎 · {sym} {dr} ===")
    print(f"订单流总分: {r['score']}/15")
    for n in r['notes']:
        print(f"  {n}")
    ob = r['raw']['order_book']
    tf = r['raw']['trade_flow']
    print(f"挂单失衡: {ob['imbalance']:+.3f} ({ob['bias']})")
    print(f"主动买卖: 买{tf['buy_pct']:.1f}% 大单买{tf['large_buy']}vs卖{tf['large_sell']}")
