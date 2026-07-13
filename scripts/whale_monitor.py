#!/usr/bin/env python3
"""
P1b: 鲸鱼大额转账监控 — whale_monitor.py
设计院 v5.6 | 2026-07-13

策略：
  - Binance 大额成交记录（aggTrades筛选）→ 识别鲸鱼行为
  - 异常OI单笔突变 → 大户建仓/清仓信号
  - 大户多空比突变检测
输出: 鲸鱼行为标签 + 梵天评分贡献
"""
import sys, os, requests, json, time, hmac, hashlib
from pathlib import Path
from collections import defaultdict

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))

KEY = os.environ.get('BINANCE_API_KEY', 'sDqoRAyeYHHzevKNxSj5JfkWpNUd6v8qPAhVy0Y8wbWGwC48eC7uhFOENAlVqV7b')
SEC = os.environ.get('BINANCE_SECRET',  'hXQnzQco9SNVgKgF2m3xvBGlJjOHBVtlzqRlxOTkp0kiJAwAOTeUiGLQSAopqIj7')
HDR = {'X-MBX-APIKEY': KEY}


def get_whale_signal(sym: str = 'BTCUSDT') -> dict:
    """鲸鱼行为综合分析"""
    px = float(requests.get('https://fapi.binance.com/fapi/v1/ticker/price',
                             params={'symbol': sym}, timeout=5).json()['price'])

    # 1. 近1000笔聚合成交 → 找大额单笔（>100 BTC or >200 ETH）
    trades = requests.get('https://fapi.binance.com/fapi/v1/aggTrades',
                          params={'symbol': sym, 'limit': 500}, timeout=8).json()

    threshold_usd = 3_000_000  # 300万U以上视为鲸鱼
    whale_buys = whale_sells = 0.0
    whale_events = []

    if isinstance(trades, list):
        for t in trades:
            qty   = float(t['q'])
            price = float(t['p'])
            usd   = qty * price
            if usd >= threshold_usd:
                side = 'SELL' if t['m'] else 'BUY'  # m=True表示maker卖出
                if side == 'BUY':
                    whale_buys += usd
                else:
                    whale_sells += usd
                whale_events.append({
                    'side': side, 'usd': round(usd, 0),
                    'price': price, 'ts': t['T']
                })

    whale_net = whale_buys - whale_sells
    whale_dir = '🐋买入' if whale_net > 0 else ('🐋卖出' if whale_net < 0 else '中性')

    # 2. 大户多空比趋势（最近3期对比）
    ls_whale = requests.get(
        'https://fapi.binance.com/futures/data/topLongShortPositionRatio',
        params={'symbol': sym, 'period': '1h', 'limit': 4}, timeout=8
    ).json()

    whale_ls_trend = 'STABLE'
    whale_ls_latest = 1.0
    if isinstance(ls_whale, list) and len(ls_whale) >= 3:
        ratios = [float(x['longShortRatio']) for x in ls_whale[-3:]]
        whale_ls_latest = ratios[-1]
        if ratios[-1] > ratios[-3] * 1.05:
            whale_ls_trend = 'LONG_BUILDING↗'   # 大户在加多
        elif ratios[-1] < ratios[-3] * 0.95:
            whale_ls_trend = 'SHORT_BUILDING↘'  # 大户在加空
        else:
            whale_ls_trend = 'STABLE→'

    # 3. OI最近4根1H变化（大幅增减=大户建/清仓）
    oi_hist = requests.get(
        'https://fapi.binance.com/futures/data/openInterestHist',
        params={'symbol': sym, 'period': '1h', 'limit': 4}, timeout=8
    ).json()

    oi_signal = 'NORMAL'
    oi_1h_chg = 0.0
    if isinstance(oi_hist, list) and len(oi_hist) >= 2:
        oi_vals = [float(x['sumOpenInterest']) for x in oi_hist]
        oi_1h_chg = round((oi_vals[-1] - oi_vals[-2]) / oi_vals[-2] * 100, 3)
        if oi_1h_chg > 2.0:   oi_signal = '🔥大幅建仓(+{:.2f}%)'.format(oi_1h_chg)
        elif oi_1h_chg > 1.0: oi_signal = '📈温和建仓(+{:.2f}%)'.format(oi_1h_chg)
        elif oi_1h_chg < -2.0: oi_signal = '🔻大幅清仓({:.2f}%)'.format(oi_1h_chg)
        elif oi_1h_chg < -1.0: oi_signal = '📉温和清仓({:.2f}%)'.format(oi_1h_chg)

    # 4. 梵天评分贡献
    whale_score = 0
    if whale_net > 5_000_000:  whale_score += 10  # 鲸鱼净买入>500万U
    elif whale_net > 0:        whale_score += 5
    if whale_ls_trend == 'LONG_BUILDING↗':  whale_score += 8
    if 'LONG_BUILDING↗' in oi_signal:       whale_score += 6
    if whale_net < -5_000_000: whale_score -= 10  # 鲸鱼净卖出扣分
    if whale_ls_trend == 'SHORT_BUILDING↘': whale_score -= 6

    result = {
        'symbol'          : sym,
        'price'           : px,
        'whale_buys_usd'  : round(whale_buys, 0),
        'whale_sells_usd' : round(whale_sells, 0),
        'whale_net_usd'   : round(whale_net, 0),
        'whale_direction' : whale_dir,
        'whale_event_count': len(whale_events),
        'whale_ls_ratio'  : round(whale_ls_latest, 3),
        'whale_ls_trend'  : whale_ls_trend,
        'oi_1h_chg'       : oi_1h_chg,
        'oi_signal'       : oi_signal,
        'whale_score'     : whale_score,
        'ts'              : time.time(),
    }

    cache = BASE / 'data' / f'whale_{sym}.json'
    cache.write_text(json.dumps(result, indent=2))
    return result


def format_report(r: dict) -> str:
    lines = [
        f'🐋 鲸鱼监控 — {r["symbol"]} ${r["price"]:,.2f}',
        f'  近期大额成交({r["whale_event_count"]}笔≥$3M):',
        f'    净买入: ${r["whale_net_usd"]:+,.0f}  方向: {r["whale_direction"]}',
        f'  大户多空比: {r["whale_ls_ratio"]}  趋势: {r["whale_ls_trend"]}',
        f'  OI 1H变化: {r["oi_1h_chg"]:+.3f}%  {r["oi_signal"]}',
        f'  梵天评分贡献: {r["whale_score"]:+d}',
    ]
    return '\n'.join(lines)


if __name__ == '__main__':
    syms = sys.argv[1:] if len(sys.argv) > 1 else ['BTCUSDT', 'ETHUSDT']
    for sym in syms:
        r = get_whale_signal(sym)
        print(format_report(r))
        print()
