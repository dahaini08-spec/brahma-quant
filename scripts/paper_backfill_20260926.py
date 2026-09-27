#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
paper_backfill_20260926.py — B线账本对账补记 [2026-09-27 苏摩111 顶层修复]

背景：9.26 07:19旧库归档(reset)与07:21-22 executor两笔BTC新单写入竞态，
07:22:41三文件被第二写者截断清零。两笔单的真实轨迹：
- 07:21:XX OPEN BTC LONG @83985.00 SL=82305.30 TP1=88184.20 score=150 BULL_TREND
- 07:22:XX OPEN BTC LONG @83972.30 SL=82292.85 TP1=88170.90 score=150 BULL_TREND
- 9.26晚 BTC最低77600.00 → 双双穿SL
- BTC当晚第一次触及SL≈82305的时间：查1hK线定位精确穿损时刻

本脚本：把两笔单补记进新账本（orders+ledger_log+positions.closed），
并按当时触及SL的价格结算平仓，费用按SSOT费率模型（taker4bps+滑点3bps）。
执行后 total_trades=2, loss=2, NAV按真实SL回写。
幂等：若orders.jsonl已有PL-20260926记录则跳过。
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import paper_ledger as pl

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / 'data'
ORDERS = DATA / 'paper_orders.jsonl'
LEDGER_LOG = DATA / 'paper_ledger_log.jsonl'

# 9.26日志还原的两笔单（与executor日志逐字对齐）
RESTORED = [
    {'id': 'PL-1787614860-BTCUSDT-LONG-0721', 'ts': 1787614860, 'symbol': 'BTCUSDT',
     'entry_price': 83985.00, 'sl': 82305.30, 'tp1': 88184.20,
     'nav_pct': 5.0, 'leverage': 5.0, 'notional': 25000.0,
     'margin': 5000.0, 'qty': 25000.0 / 83985.0,
     'regime': 'BULL_TREND', 'score': 150.0, 'rr': 2.43,
     'source': 'auto_signal_queue', 'note': 'backfill 07:21单(9.26竞态丢失)'},
    {'id': 'PL-1787614940-BTCUSDT-LONG-0722', 'ts': 1787614940, 'symbol': 'BTCUSDT',
     'entry_price': 83972.30, 'sl': 82292.85, 'tp1': 88170.90,
     'nav_pct': 5.0, 'leverage': 5.0, 'notional': 25000.0,
     'margin': 5000.0, 'qty': 25000.0 / 83972.30,
     'regime': 'BULL_TREND', 'score': 150.0, 'rr': 2.43,
     'source': 'auto_signal_queue', 'note': 'backfill 07:22单(9.26竞态丢失)'},
]


def btc_sl_touch_ts(sl: float) -> int:
    """查1h K线找第一次触及SL的时刻（9.26 07:21之后）。"""
    url = (f'https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=1h'
           f'&startTime={1787614860000}&limit=72')
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'brahma-backfill'})
        k = json.load(urllib.request.urlopen(req, timeout=15))
        for row in k:
            open_ms, high, low = row[0], float(row[2]), float(row[3])
            if low <= sl:
                return int(open_ms / 1000)
        # 72h内未触及（不该发生）——用最后K线
        return int(k[-1][0] / 1000) if k else 0
    except Exception as e:
        print(f'K线查询失败({e})，回退用保守估计9.26 20:00 UTC')
        return 1787640000


def main():
    # 幂等检查
    if ORDERS.exists():
        existing = ORDERS.read_text()
        if 'PL-1787614860' in existing or 'PL-1787614940' in existing:
            print('SKIP: backfill已存在（幂等）')
            return

    acc = pl._load()
    total_loss = 0.0
    closed_records = []

    for r in RESTORED:
        sl_touch = btc_sl_touch_ts(r['sl'])
        exit_price = r['sl']  # SL触发=按SL价结算（保守，不算滑点损失）
        qty = r['qty']
        gross_pnl = (exit_price - r['entry_price']) * qty  # LONG
        exit_fee = pl.close_cost(r['notional'])
        net = gross_pnl - exit_fee
        total_loss += net

        rec = dict(r)
        rec.update({
            'ts_iso': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(r['ts'])),
            'entry_fee': pl.open_cost(r['notional']),
            'status': 'CLOSED_SL',
            'exit_price': exit_price,
            'exit_ts': sl_touch,
            'exit_fee': round(exit_fee, 4),
            'gross_pnl': round(gross_pnl, 2),
            'net_pnl': round(net, 2),
            'close_reason': '🔴SL止损(补记:9.26竞态丢失后穿SL)',
        })
        closed_records.append(rec)

        with open(LEDGER_LOG, 'a') as f:
            f.write(json.dumps({'ev': 'OPEN', 'id': rec['id'], 'symbol': rec['symbol'],
                                'side': 'LONG', 'notional': rec['notional'],
                                'entry_fee': rec['entry_fee']}, ensure_ascii=False) + '\n')
            f.write(json.dumps({'ev': 'CLOSE', 'id': rec['id'], 'symbol': rec['symbol'],
                                'side': 'LONG', 'net_pnl': rec['net_pnl'],
                                'exit_fee': rec['exit_fee']}, ensure_ascii=False) + '\n')

    # 写orders
    with open(ORDERS, 'a') as f:
        for rec in closed_records:
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')

    # 回写account
    acc['nav_current'] = round(float(acc.get('nav_current', pl.START_NAV))
                               - sum(pl.open_cost(r['notional']) for r in RESTORED)  # 双倍开仓费补偿
                               + sum(-1 * 0 for _ in RESTORED), 4)
    # 上面一行简化：补记成本=2笔各自(开仓费已在07:21/22扣过一次的假设不成立——账本被清零)
    # 严谨口径：账本被清零=费用也丢了，重放全链路
    acc['nav_current'] = round(pl.START_NAV, 4)
    for rec in closed_records:
        acc['nav_current'] = round(acc['nav_current'] - rec['entry_fee'] + rec['gross_pnl'] - rec['exit_fee'], 4)
    acc['total_trades'] = 2
    acc['loss_trades'] = 2
    acc['win_trades'] = 0
    acc['total_pnl_pct'] = round(sum(r['net_pnl'] for r in closed_records) / pl.START_NAV * 100, 4)
    acc['updated_at'] = int(time.time())
    acc['backfilled'] = '2026-09-27 backfill 9.26竞态丢失两笔BTC单'
    pl._save(acc)

    print('✅ 补记完成:')
    for rec in closed_records:
        print(f"  {rec['id']}: entry={rec['entry_price']} exit={rec['exit_price']} net={rec['net_pnl']:+.2f}U")
    print(f"NAV={acc['nav_current']:.2f} total_pnl={acc['total_pnl_pct']:+.3f}%")


if __name__ == '__main__':
    main()
