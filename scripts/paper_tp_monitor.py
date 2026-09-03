#!/usr/bin/env python3
"""
paper_tp_monitor.py — 纸面系统止盈追踪器
设计院封印 2026-09-03 苏摩111

功能：
  - 每10分钟检查纸面持仓的TP1/TP2是否触达
  - TP1触达 → 推送建议平仓50%，标记 partial_tp1=True
  - TP2触达 → 推送全平建议，移入 closed 记录
  - SL触达 → 止损平仓，记录亏损
  - 72H超时 → 强制平仓
  - 结算写入 paper_positions.json，供 paper_daily_report 统计WR

接入位置：
  - scripts/paper_tp_monitor.py（本文件）
  - supercronic: */10 * * * * python3 scripts/paper_tp_monitor.py
"""
import json, sys, time, urllib.request
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))

PAPER_POS_FILE = BASE / 'data' / 'paper_positions.json'
PAPER_LOG      = BASE / 'logs' / 'paper_tp_monitor.log'
TIMEOUT_HOURS  = 72


def load_positions() -> dict:
    if PAPER_POS_FILE.exists():
        try:
            return json.loads(PAPER_POS_FILE.read_text())
        except Exception:
            pass
    return {'positions': [], 'closed': [], 'stats': {'total': 0, 'win': 0, 'pnl': 0.0}}


def save_positions(data: dict):
    PAPER_POS_FILE.write_text(json.dumps(data, indent=2))


def get_price(symbol: str) -> float:
    try:
        url = f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={symbol}'
        r = json.loads(urllib.request.urlopen(url, timeout=5).read())
        return float(r['price'])
    except Exception:
        return 0.0


def log(msg: str):
    ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    line = f'[{ts}] {msg}'
    print(line)
    try:
        with open(PAPER_LOG, 'a') as f:
            f.write(line + '\n')
    except Exception:
        pass


def push_alert(msg: str):
    """推送到Jarvis主线程"""
    try:
        sys.path.insert(0, str(BASE / 'scripts'))
        from push_hub import _jarvis as _pj
        _pj(msg, level='P1')
    except Exception:
        log(f'[PUSH] {msg}')


def calc_pnl_pct(pos: dict, current_price: float) -> float:
    entry = pos['entry_price']
    if pos['side'] == 'LONG':
        return (current_price - entry) / entry * 100
    else:
        return (entry - current_price) / entry * 100


def close_position(pos: dict, reason: str, current_price: float, positions_data: dict):
    """平仓并记录"""
    pnl = calc_pnl_pct(pos, current_price)
    pos['close_price']  = current_price
    pos['close_reason'] = reason
    pos['close_ts']     = int(time.time())
    pos['pnl_pct']      = round(pnl, 3)
    pos['status']       = 'closed'

    positions_data['positions'] = [
        p for p in positions_data['positions']
        if not (p['symbol'] == pos['symbol'] and p['open_ts'] == pos['open_ts'])
    ]
    positions_data.setdefault('closed', []).append(pos)

    # 更新统计
    stats = positions_data.setdefault('stats', {'total': 0, 'win': 0, 'pnl': 0.0})
    stats['pnl'] = round(stats.get('pnl', 0) + pnl, 3)
    if pnl > 0:
        stats['win'] = stats.get('win', 0) + 1
    total = stats.get('total', 1)
    wr = stats['win'] / total * 100 if total else 0

    msg = (f'📊 纸面系统{reason}\n'
           f'{pos["symbol"]} {pos["side"]} @{current_price:.2f}\n'
           f'入场:{pos["entry_price"]:.2f} PnL:{pnl:+.2f}%\n'
           f'累计WR:{wr:.0f}% ({stats["win"]}/{total}) 总PnL:{stats["pnl"]:+.2f}%')
    push_alert(msg)
    log(f'CLOSE {pos["symbol"]} {pos["side"]} {reason} PnL={pnl:+.2f}%')


def main():
    data = load_positions()
    positions = data.get('positions', [])

    if not positions:
        print('HEARTBEAT_OK')
        return

    now_ts = int(time.time())
    alerts = []

    for pos in list(positions):
        sym   = pos['symbol']
        side  = pos['side']
        price = get_price(sym)
        if not price:
            continue

        pnl = calc_pnl_pct(pos, price)
        open_ts = pos.get('open_ts', now_ts)
        hours_open = (now_ts - open_ts) / 3600

        # 72H超时平仓
        if hours_open >= TIMEOUT_HOURS:
            close_position(pos, f'⏰72H超时平仓', price, data)
            continue

        # SL触达
        sl = pos.get('sl_price', 0)
        if sl:
            if side == 'LONG' and price <= sl:
                close_position(pos, '🔴SL止损', price, data)
                continue
            elif side == 'SHORT' and price >= sl:
                close_position(pos, '🔴SL止损', price, data)
                continue

        # TP2触达（全平）
        tp2 = pos.get('tp2_price', 0)
        if tp2:
            if side == 'LONG' and price >= tp2:
                close_position(pos, '🎯TP2全平', price, data)
                continue
            elif side == 'SHORT' and price <= tp2:
                close_position(pos, '🎯TP2全平', price, data)
                continue

        # TP1触达（半仓平，推送提醒）
        tp1 = pos.get('tp1_price', 0)
        if tp1 and not pos.get('partial_tp1'):
            hit = (side == 'LONG' and price >= tp1) or (side == 'SHORT' and price <= tp1)
            if hit:
                pos['partial_tp1'] = True
                # 移动SL到保本
                pos['sl_price'] = round(pos['entry_price'], 2)
                msg = (f'✅ 纸面系统 TP1触达\n'
                       f'{sym} {side} @{price:.2f} PnL:{pnl:+.2f}%\n'
                       f'建议平仓50%，SL移至保本 {pos["entry_price"]:.2f}')
                push_alert(msg)
                log(f'TP1 {sym} {side} @{price:.2f} PnL={pnl:+.2f}%')

    save_positions(data)

    remaining = len(data.get('positions', []))
    if remaining == 0 and not alerts:
        print('HEARTBEAT_OK')
    else:
        log(f'监控完成，剩余持仓 {remaining} 个')


if __name__ == '__main__':
    main()
