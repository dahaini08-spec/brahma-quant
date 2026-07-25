#!/usr/bin/env python3
"""
ETH LONG EMA门控监控 v2
设计院 2026-07-25 升级：双重确认 + 止损池预警

新宪法门控（全部满足才推送执行）：
  1. 价格 > EMA20_1H
  2. L2买卖比 > 1.5x（多头占优）
  3. RSI_1H > 32（超卖修复）

止损池预警：
  价格跌破 $1,855 → 立即推送 BEAR 风险预警
"""
import sys, requests, json

ENTRY_LO  = 1856.0
ENTRY_HI  = 1878.0
SL        = 1812.0
TP1       = 1904.0
TP2       = 1928.0
QTY       = 0.012

BEAR_TRIGGER = 1855.0   # 跌破此价位 → 情景B预警

def main():
    # ETH价格 + K线
    r1 = requests.get('https://fapi.binance.com/fapi/v1/premiumIndex',
                      params={'symbol':'ETHUSDT'}, timeout=5)
    price = float(r1.json()['markPrice'])

    r2 = requests.get('https://fapi.binance.com/fapi/v1/klines',
                      params={'symbol':'ETHUSDT','interval':'1h','limit':25}, timeout=5)
    closes = [float(k[4]) for k in r2.json()]
    k = 2/(20+1); ema = closes[0]
    for c in closes[1:]: ema = c*k + ema*(1-k)

    # RSI_1H
    gains=[]; losses=[]
    for i in range(1,15):
        d = closes[-i]-closes[-i-1]
        gains.append(max(d,0)); losses.append(max(-d,0))
    ag=sum(gains)/14 or 1e-9; al=sum(losses)/14 or 1e-9
    rsi_1h = 100-100/(1+ag/al)

    # L2买卖比（订单簿）
    r3 = requests.get('https://fapi.binance.com/fapi/v1/depth',
                      params={'symbol':'ETHUSDT','limit':20}, timeout=5)
    ob = r3.json()
    bid_vol = sum(float(b[1]) for b in ob.get('bids',[]))
    ask_vol = sum(float(a[1]) for a in ob.get('asks',[]))
    l2_ratio = bid_vol / ask_vol if ask_vol > 0 else 1.0

    gap_pct = (price - ema) / ema * 100

    # ─── 止损池跌破预警（优先检查）────────────────────────
    if price < BEAR_TRIGGER:
        print(f'🚨 ETH 止损池跌破预警！')
        print(f'   价格 ${price:.2f} < 止损池 ${BEAR_TRIGGER}')
        print(f'   情景B触发：多头止损级联风险，LONG计划暂停')
        print(f'   RSI_1H={rsi_1h:.1f} | L2比={l2_ratio:.2f}x | EMA={ema:.2f}')
        print(f'   苏摩，ETH LONG计划暂停，等待价格稳定后重新评估')
        sys.exit(0)

    # ─── 新宪法双重确认门控 ───────────────────────────────
    cond1 = price > ema          # EMA站稳
    cond2 = l2_ratio > 1.5      # 多头占优
    cond3 = rsi_1h > 32         # 超卖修复

    c1s = 'OK' if cond1 else 'NO'
    c2s = 'OK' if cond2 else 'NO'
    c3s = 'OK' if cond3 else 'NO'
    status_str = (
        f'ETH=${price:.2f} EMA={ema:.2f}({gap_pct:+.2f}%) '
        f'RSI={rsi_1h:.1f} L2={l2_ratio:.2f}x C1={c1s} C2={c2s} C3={c3s}'
    )

    if cond1 and cond2 and cond3:
        print(f'🔔 ETH LONG 三重门控全部通过！')
        print(f'   {status_str}')
        print(f'   入场: ${ENTRY_LO}~${ENTRY_HI} | SL=${SL} | TP1=${TP1} | TP2=${TP2}')
        print(f'   苏摩，ETH LONG 条件已全部满足，是否执行？回复「执行」')
    else:
        missing = []
        if not cond1: missing.append(f'EMA未站稳(差{-gap_pct:.2f}%)')
        if not cond2: missing.append(f'L2多头不足({l2_ratio:.2f}x<1.5x)')
        if not cond3: missing.append(f'RSI未修复({rsi_1h:.1f}<32)')
        print(f'HEARTBEAT_OK | {status_str} | 等待: {",".join(missing)}')

if __name__ == '__main__':
    main()
