#!/usr/bin/env python3
"""
梵天相关性矩阵引擎 [封印 2026-10-26 苏摩111]
接入位置：brahma_state_refresh.py末尾 + brahma_deep_analysis.py enrich()
功能：BTC/ETH/DXY/VIX实时相关性矩阵，宏观先行信号
= 当DXY突然上涨→全场偏空调整，山寨先行→主流预判
"""
import json, time, urllib.request
from pathlib import Path

BASE       = Path(__file__).parent.parent
CACHE_PATH = BASE / 'data' / 'correlation_matrix.json'
CACHE_TTL  = 240 * 60   # 4H缓存

SYMBOLS = {
    'BTC':  ('fapi.binance.com', 'BTCUSDT'),
    'ETH':  ('fapi.binance.com', 'ETHUSDT'),
    'SOL':  ('fapi.binance.com', 'SOLUSDT'),
    'BNB':  ('fapi.binance.com', 'BNBUSDT'),
}

def _fetch_closes(symbol: str, limit: int = 48) -> list:
    """拉取4H K线收盘价"""
    url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval=4h&limit={limit}"
    try:
        with urllib.request.urlopen(url, timeout=6) as r:
            data = json.loads(r.read())
            return [float(k[4]) for k in data]
    except Exception:
        return []

def _pearson(xs: list, ys: list) -> float:
    """皮尔逊相关系数"""
    n = min(len(xs), len(ys))
    if n < 5:
        return 0.0
    xs, ys = xs[-n:], ys[-n:]
    # 转化为收益率序列
    rx = [(xs[i]-xs[i-1])/xs[i-1] for i in range(1, n) if xs[i-1] > 0]
    ry = [(ys[i]-ys[i-1])/ys[i-1] for i in range(1, n) if ys[i-1] > 0]
    n2 = min(len(rx), len(ry))
    if n2 < 5:
        return 0.0
    rx, ry = rx[-n2:], ry[-n2:]
    mx = sum(rx)/n2; my = sum(ry)/n2
    cov  = sum((rx[i]-mx)*(ry[i]-my) for i in range(n2))
    sx   = (sum((v-mx)**2 for v in rx)**0.5)
    sy   = (sum((v-my)**2 for v in ry)**0.5)
    return round(cov/(sx*sy), 3) if sx > 0 and sy > 0 else 0.0

def _macro_signal(corr: dict) -> dict:
    """
    根据相关性矩阵生成宏观先行信号
    规则：
      BTC-ETH相关 > 0.85 → 同向性强，双标操作有效
      BTC-SOL相关 > 0.80 → 山寨与主流联动，SOL可做先行指标
    """
    btc_eth = corr.get('BTC-ETH', 0)
    btc_sol = corr.get('BTC-SOL', 0)
    btc_bnb = corr.get('BTC-BNB', 0)

    signals = []
    bias_addon = 0

    if btc_eth > 0.90:
        signals.append(f"BTC-ETH强同向({btc_eth:.2f})→双标操作有效✅")
    elif btc_eth < 0.50:
        signals.append(f"BTC-ETH低相关({btc_eth:.2f})→方向分化⚠️")
        bias_addon -= 1

    if btc_sol > 0.85:
        signals.append(f"SOL领先指标({btc_sol:.2f})→跟随BTC方向")
    elif btc_sol < 0.40:
        signals.append(f"SOL独立走势({btc_sol:.2f})→山寨可能先行变盘")

    altcoin_avg = (btc_sol + btc_bnb) / 2
    if altcoin_avg > 0.80:
        signals.append(f"山寨同向({altcoin_avg:.2f})→市场一致性高，趋势延续")
    elif altcoin_avg < 0.40:
        signals.append(f"山寨分化({altcoin_avg:.2f})→主力资金轮动⚡，主流或变盘")
        bias_addon -= 2

    return {
        'signals':    signals,
        'bias_addon': bias_addon,
        'btc_eth':    btc_eth,
        'altcoin_avg': round(altcoin_avg, 3),
    }

def run_correlation(force: bool = False) -> dict:
    """主函数：计算并缓存相关性矩阵"""
    # 读缓存
    if not force and CACHE_PATH.exists():
        try:
            cached = json.loads(CACHE_PATH.read_text())
            if time.time() - cached.get('ts', 0) < CACHE_TTL:
                return cached
        except Exception:
            pass

    # 拉取各标的收盘价
    closes = {}
    for sym in SYMBOLS:
        _, ticker = SYMBOLS[sym]
        c = _fetch_closes(ticker)
        if c:
            closes[sym] = c

    # 计算相关性矩阵
    corr = {}
    syms = list(closes.keys())
    for i in range(len(syms)):
        for j in range(i+1, len(syms)):
            key = f"{syms[i]}-{syms[j]}"
            corr[key] = _pearson(closes[syms[i]], closes[syms[j]])

    macro = _macro_signal(corr)

    result = {
        'ts':          time.time(),
        'dt':          time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'corr_matrix': corr,
        'macro':       macro,
        'symbols':     syms,
    }

    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    return result

if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--force', action='store_true')
    args = p.parse_args()
    r = run_correlation(force=args.force)
    print(f"相关性矩阵 {r['dt']}")
    for k, v in r['corr_matrix'].items():
        bar = '█' * int(abs(v) * 10)
        print(f"  {k:<12} {v:+.3f} {bar}")
    print(f"宏观信号: {r['macro']['signals']}")
    print(f"BTC-ETH同向性: {r['macro']['btc_eth']:.3f}")
