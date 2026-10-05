#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
paper_daily_review.py — B线纸面每日复盘（23:30北京 = 15:30 UTC）
[2026-09-26 苏摩111] 复盘里缺失的第4件套：每日盈亏复盘
输出：当日开/平/WR/费用/NAV变动 → 推送主线程
"""
# [2026-10-05 P1 苏摩111] 统一路径管理，替代裸 sys.path.insert
try:
    import brahma_path_setup  # noqa
except ImportError:
    pass  # 兜底：原有 sys.path.insert 仍保留

import json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import paper_ledger as pl

DATA = Path(__file__).parent.parent / 'data'

def main():
    acc = pl.stats()
    today = time.strftime('%Y-%m-%d', time.gmtime())
    # 当日订单
    opens, closes = [], []
    try:
        for line in open(DATA / 'paper_orders.jsonl'):
            try: j = json.loads(line)
            except Exception: continue
            if str(j.get('ts_iso', ''))[:10] != today: continue
            (closes if j.get('status') == 'CLOSED' else opens).append(j)
    except FileNotFoundError:
        pass
    # 当日CLOSE事件（补账也计入）
    nets = []
    try:
        for line in open(DATA / 'paper_ledger_log.jsonl'):
            try: j = json.loads(line)
            except Exception: continue
            if j.get('ev') == 'CLOSE' and str(j.get('ts_iso', ''))[:10] == today:
                nets.append(float(j.get('net', 0)))
    except FileNotFoundError:
        pass
    wins = sum(1 for n in nets if n > 0); losses = sum(1 for n in nets if n < 0)
    day_pnl = sum(nets)
    dd = acc.get('max_drawdown_pct', 0)
    wr = wins / len(nets) * 100 if nets else 0
    cb = '🚨熔断中' if pl.circuit_breaker_active() else ''
    lines = [
        f"📊 梵天纸面盘每日复盘 | {today}",
        f"NAV: {acc['nav_current']:,.2f} / 100,000 ({(acc['nav_current']-100000)/1000:+.2f}%)",
        f"当日: 开{len(opens)}笔 平{len(closes)}笔 | 净PnL {day_pnl:+.2f}U | WR {wr:.0f}%({wins}W/{losses}L)",
        f"累计: {acc.get('total_trades',0)}笔 W/L={acc.get('win_trades',0)}/{acc.get('loss_trades',0)} | maxDD {dd:.2f}%",
        f"费用: 手续费累计 {acc.get('fees_paid_total',0):.2f}U",
    ]
    if cb: lines.append(cb)
    # 最近3笔明细
    for c in closes[-3:]:
        lines.append(f"  {c.get('symbol')} {c.get('side')} {c.get('close_reason')} net={c.get('net_pnl',0):+.2f}U ({c.get('hours_held',0):.1f}h)")
    print('\n'.join(lines))

if __name__ == '__main__':
    main()
