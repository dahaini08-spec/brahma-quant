#!/usr/bin/env python3
"""
tradfi_watcher.py — TradFi守望层守护脚本
设计院自主决策 · 2026-07-22

职责（层1守望层，0 tokens）：
  每5分钟静默检查美股代币异动
  发现触发事件 → 层2（openclaw事件触发）
  无触发 → 直接退出，HEARTBEAT_OK

触发事件（E_TF1~E_TF6）：
  E_TF1: SPY 24h涨跌 > ±2.0%（大盘剧烈波动）
  E_TF2: QQQ vs SPY 偏差 > ±1.5%（科技板块分化）
  E_TF3: COIN 24h > +8%（加密概念股爆涨）
  E_TF4: COIN vs BTC 背离 > 5%（加密概念 vs 加密本体背离）
  E_TF5: 进入美股开盘冲击波窗口（14:00 UTC）
  E_TF6: MSTR 24h > +8%（BTC代理持仓爆涨）

静默规则：
  - 当前 overnight 且无 E_TF1~E_TF6 → HEARTBEAT_OK
  - 全部事件均未触发 → HEARTBEAT_OK（不推送）
"""

import sys
import json
import time
import urllib.request
import concurrent.futures
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

def fetch_btc_price() -> float:
    """获取BTC当前价格（用于计算COIN vs BTC背离）"""
    try:
        d = json.loads(urllib.request.urlopen(
            'https://fapi.binance.com/fapi/v1/ticker/24hr?symbol=BTCUSDT', timeout=5
        ).read())
        return float(d.get('priceChangePercent', 0))
    except Exception:
        return 0.0

def fetch_rwa_prices(watchlist: list) -> dict:
    """并行获取RWA代币价格"""
    # 先拿合约列表
    try:
        req = urllib.request.Request(
            'https://www.binance.com/bapi/defi/v1/public/wallet-direct/buw/wallet/market/token/rwa/stock/detail/list/ai',
            headers={'Accept-Encoding': 'identity', 'User-Agent': 'binance-web3/1.1 (TradFi-Watcher)'}
        )
        with urllib.request.urlopen(req, timeout=6) as r:
            token_data = json.loads(r.read())['data']
        contracts = {}
        for t in token_data:
            if t['ticker'] not in contracts:
                contracts[t['ticker']] = t
    except Exception:
        return {}

    def fetch_one(ticker):
        info = contracts.get(ticker)
        if not info:
            return ticker, None
        url = (f'https://www.binance.com/bapi/defi/v2/public/wallet-direct/buw/wallet/market/token/rwa/dynamic/ai'
               f'?chainId={info["chainId"]}&contractAddress={info["contractAddress"]}')
        req = urllib.request.Request(url, headers={'Accept-Encoding': 'identity', 'User-Agent': 'binance-web3/1.1 (TradFi-Watcher)'})
        with urllib.request.urlopen(req, timeout=5) as r:
            d = json.loads(r.read())['data']
        return ticker, {
            'price': float(d['tokenInfo']['price']),
            'pct24h': float(d['tokenInfo']['priceChangePct24h']),
        }

    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(fetch_one, t): t for t in watchlist}
        for fut in concurrent.futures.as_completed(futs, timeout=8):
            try:
                ticker, data = fut.result()
                if data:
                    results[ticker] = data
            except Exception:
                pass
    return results


def check_triggers(prices: dict, btc_pct: float) -> list:
    """检查所有触发事件，返回触发列表"""
    triggers = []
    now = datetime.now(timezone.utc)
    utc_min = now.hour * 60 + now.minute

    spy = prices.get('SPY', {})
    qqq = prices.get('QQQ', {})
    coin = prices.get('COIN', {})
    mstr = prices.get('MSTR', {})

    spy_pct  = spy.get('pct24h', 0)
    qqq_pct  = qqq.get('pct24h', 0)
    coin_pct = coin.get('pct24h', 0)
    mstr_pct = mstr.get('pct24h', 0)

    # E_TF1: SPY大幅波动
    if abs(spy_pct) > 2.0:
        triggers.append({'event': 'E_TF1', 'desc': f'SPY大盘异动 {spy_pct:+.2f}%', 'level': 'P1'})

    # E_TF2: QQQ vs SPY 科技分化
    if qqq and spy and abs(qqq_pct - spy_pct) > 1.5:
        triggers.append({'event': 'E_TF2', 'desc': f'科技板块分化 QQQ={qqq_pct:+.2f}% vs SPY={spy_pct:+.2f}%', 'level': 'P2'})

    # E_TF3: COIN爆涨（加密概念强势信号）
    if coin_pct > 8.0:
        triggers.append({'event': 'E_TF3', 'desc': f'COIN爆涨 +{coin_pct:.2f}% 加密概念强势', 'level': 'P1'})

    # E_TF4: COIN vs BTC 背离
    if abs(coin_pct - btc_pct) > 5.0:
        triggers.append({'event': 'E_TF4', 'desc': f'加密背离 COIN={coin_pct:+.2f}% vs BTC={btc_pct:+.2f}%', 'level': 'P2'})

    # E_TF5: 进入开盘冲击波窗口
    if 14 * 60 <= utc_min < 14 * 60 + 45:
        triggers.append({'event': 'E_TF5', 'desc': '进入美股开盘冲击波窗口 14:00-14:45 UTC', 'level': 'P1'})

    # E_TF6: MSTR爆涨（BTC代理确认）
    if mstr_pct > 8.0:
        triggers.append({'event': 'E_TF6', 'desc': f'MSTR爆涨 +{mstr_pct:.2f}% BTC代理持仓强势', 'level': 'P2'})

    return triggers


def main():
    watchlist = ['SPY', 'QQQ', 'COIN', 'MSTR']

    # 并行获取数据
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        btc_fut = ex.submit(fetch_btc_price)
        rwa_fut = ex.submit(fetch_rwa_prices, watchlist)
        btc_pct = btc_fut.result(timeout=10)
        prices  = rwa_fut.result(timeout=10)

    triggers = check_triggers(prices, btc_pct)

    if not triggers:
        print('HEARTBEAT_OK')
        return

    # 有触发事件 → 输出触发报告（openclaw事件层处理）
    now_str = datetime.now(timezone.utc).strftime('%H:%M UTC')
    lines = [f'🔔 TradFi守望层触发 [{now_str}]']
    for t in triggers:
        icon = '🚨' if t['level'] == 'P1' else '⚡'
        lines.append(f'{icon} {t["event"]}: {t["desc"]}')

    # 当前美股代币快照
    lines.append('')
    lines.append('📊 美股代币快照:')
    for ticker in watchlist:
        if ticker in prices:
            p = prices[ticker]
            arrow = '▲' if p['pct24h'] > 0 else '▼'
            lines.append(f'  {ticker}: ${p["price"]:.2f} {arrow}{abs(p["pct24h"]):.2f}%')
    lines.append(f'  BTC: {btc_pct:+.2f}%')

    lines.append('')
    lines.append('→ 梵天建议：运行1号工程分析 BTC/ETH 确认是否需要调整仓位')

    print('\n'.join(lines))

    # 写入触发日志
    try:
        log = BASE_DIR / 'data' / 'tradfi_trigger_log.jsonl'
        entry = {
            'ts': datetime.now(timezone.utc).isoformat(),
            'triggers': triggers,
            'prices': {k: v.get('pct24h') for k, v in prices.items()},
            'btc_pct': btc_pct,
        }
        with open(log, 'a') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')
    except Exception:
        pass


if __name__ == '__main__':
    main()
