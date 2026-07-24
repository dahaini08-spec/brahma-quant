#!/usr/bin/env python3
"""
ETH LONG EMA门控监控
- 检查 ETH 价格是否站稳 EMA20_1H（新宪法：价格 > EMA20_1H 才允许LONG）
- 满足条件 → 推送苏摩执行通知
- 不满足 → HEARTBEAT_OK 静默
"""
import sys, os, requests, json
sys.path.insert(0, '/root/.openclaw/workspace/trading-system')
sys.path.insert(0, '/root/.openclaw/workspace/trading-system/scripts')

ENTRY_LO  = 1858.0
ENTRY_HI  = 1880.0   # 入场区上沿（含一定弹性）
SL        = 1812.0   # -2.5%
TP1       = 1904.0   # +2.5%
TP2       = 1928.0   # 清算集群密集
QTY       = 0.012    # ETH数量（5%NAV×5x）

def get_eth_data():
    r1 = requests.get('https://fapi.binance.com/fapi/v1/premiumIndex',
                      params={'symbol':'ETHUSDT'}, timeout=5)
    price = float(r1.json()['markPrice'])
    
    r2 = requests.get('https://fapi.binance.com/fapi/v1/klines',
                      params={'symbol':'ETHUSDT','interval':'1h','limit':25}, timeout=5)
    closes = [float(k[4]) for k in r2.json()]
    k = 2/(20+1)
    ema = closes[0]
    for c in closes[1:]:
        ema = c*k + ema*(1-k)
    return price, ema

def main():
    price, ema20_1h = get_eth_data()
    gap_pct = (price - ema20_1h) / ema20_1h * 100

    # 新宪法门控
    if price <= ema20_1h:
        print(f'HEARTBEAT_OK | ETH={price:.2f} EMA20_1H={ema20_1h:.2f} gap={gap_pct:.2f}% 等待站稳')
        sys.exit(0)

    # 价格站稳且在合理区间（避免已飞离入场区太远）
    if price > ENTRY_HI * 1.015:  # 超过入场区上沿1.5%，入场意义降低
        print(f'HEARTBEAT_OK | ETH={price:.2f} 已超出入场区上沿太多，不追入')
        sys.exit(0)

    # ✅ 条件满足
    print(f'🔔 ETH LONG 门控通过！')
    print(f'   价格: ${price:.2f} > EMA20_1H ${ema20_1h:.2f} (+{gap_pct:.2f}%)')
    print(f'   入场区: ${ENTRY_LO}~${ENTRY_HI} | 现价: ${price:.2f}')
    print(f'   方案: LONG {QTY} ETH | SL=${SL} | TP1=${TP1} | TP2=${TP2}')
    print()
    print(f'苏摩，ETH LONG 新宪法门控已通过，是否立即执行？')
    print(f'回复「执行」→ 设计院立即下单')

if __name__ == '__main__':
    main()
