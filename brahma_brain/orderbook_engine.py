#!/usr/bin/env python3
"""
orderbook_engine.py — L2订单簿深度引擎
Phase A-1: 真实买卖盘挂单厚度分析

能力：
  - Binance REST depth20 快照
  - 买卖盘不平衡比（OBI）
  - 上下方流动性墙检测
  - 大单冰山/滑点估算
  - 评分贡献: 0~15分
"""
import json, time, urllib.request, os, sys

FAPI = 'https://fapi.binance.com'
_cache = {}
TTL = 15  # 15秒缓存

def _get(url, timeout=6):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())

def _cached(key, fn):
    entry = _cache.get(key)
    if entry and time.time() < entry['exp']:
        return entry['data']
    data = fn()
    _cache[key] = {'data': data, 'exp': time.time() + TTL}
    return data

def get_depth(symbol: str, limit: int = 20) -> dict:
    """获取订单簿深度快照"""
    def _fetch():
        return _get(f'{FAPI}/fapi/v1/depth?symbol={symbol}&limit={limit}')
    return _cached(f'{symbol}:depth{limit}', _fetch)

def analyze_orderbook(symbol: str, signal_dir: str) -> dict:
    """
    分析订单簿，返回评分和关键指标
    signal_dir: LONG | SHORT
    """
    result = {
        'score': 0,
        'obi': 0.0,          # Order Book Imbalance [-1, +1]
        'bid_wall': 0.0,     # 最近大买单价位
        'ask_wall': 0.0,     # 最近大卖单价位
        'liquidity_short': 0.0,  # 空方流动性（阻力）
        'liquidity_long': 0.0,   # 多方流动性（支撑）
        'slippage_est': 0.0,     # 滑点估算(%)
        'notes': []
    }
    try:
        depth = get_depth(symbol.upper())
        bids = [(float(p), float(q)) for p, q in depth.get('bids', [])[:20]]
        asks = [(float(p), float(q)) for p, q in depth.get('asks', [])[:20]]
        if not bids or not asks:
            return result

        best_bid = bids[0][0]
        best_ask = asks[0][0]
        mid = (best_bid + best_ask) / 2

        # 买卖盘总量
        bid_vol = sum(q for _, q in bids)
        ask_vol = sum(q for _, q in asks)
        total_vol = bid_vol + ask_vol

        # OBI: 正值=买压强，负值=卖压强
        obi = (bid_vol - ask_vol) / max(total_vol, 1e-9)
        result['obi'] = round(obi, 4)

        # 流动性墙检测（单层>总量15%视为墙）
        bid_wall_lvl = next((p for p, q in bids if q > bid_vol * 0.15), 0)
        ask_wall_lvl = next((p for p, q in asks if q > ask_vol * 0.15), 0)
        result['bid_wall'] = bid_wall_lvl
        result['ask_wall'] = ask_wall_lvl

        # 滑点估算（执行$10k名义的价格冲击）
        notional_target = 10000  # $10k
        slippage_price = best_ask
        cum = 0
        for p, q in asks:
            cum += p * q
            if cum >= notional_target:
                slippage_price = p
                break
        slippage_pct = abs(slippage_price - best_ask) / best_ask * 100
        result['slippage_est'] = round(slippage_pct, 4)

        # ── 评分逻辑 ──────────────────────────────────────
        score = 0
        if signal_dir == 'SHORT':
            # 做空：卖压强、上方有墙、OBI负 = 有利
            if obi < -0.2:
                score += 5
                result['notes'].append(f'卖压强OBI={obi:.2f} +5')
            elif obi < -0.1:
                score += 3
                result['notes'].append(f'卖压偏强OBI={obi:.2f} +3')
            if ask_wall_lvl and (ask_wall_lvl - mid) / mid < 0.005:
                score += 4
                result['notes'].append(f'上方卖单墙@{ask_wall_lvl:.2f} +4')
            elif bid_wall_lvl and (mid - bid_wall_lvl) / mid > 0.01:
                score += 2
                result['notes'].append(f'下方支撑远+2')
        else:
            # 做多：买压强、下方有墙、OBI正 = 有利
            if obi > 0.2:
                score += 5
                result['notes'].append(f'买压强OBI={obi:.2f} +5')
            elif obi > 0.1:
                score += 3
                result['notes'].append(f'买压偏强OBI={obi:.2f} +3')
            if bid_wall_lvl and (mid - bid_wall_lvl) / mid < 0.005:
                score += 4
                result['notes'].append(f'下方买单墙@{bid_wall_lvl:.2f} +4')

        # 滑点惩罚（>0.1%扣分）
        if slippage_pct > 0.1:
            score -= 2
            result['notes'].append(f'滑点风险{slippage_pct:.3f}% -2')

        result['score'] = max(0, min(score, 15))

    except Exception as e:
        result['error'] = str(e)[:80]

    return result


if __name__ == '__main__':
    sym = sys.argv[1].upper() if len(sys.argv) > 1 else 'ETHUSDT'
    r = analyze_orderbook(sym, 'SHORT')
    print(json.dumps(r, indent=2, ensure_ascii=False))
