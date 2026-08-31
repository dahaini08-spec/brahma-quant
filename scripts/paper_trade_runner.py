#!/usr/bin/env python3
"""
paper_trade_runner.py — 梵天纸面开单胜率验证
[P2-C 2026-08-31 苏摩111封印]

梵天信号 → 纸面记录 → 自动结算 → 胜率统计
考核标准: WR≥60% + 月收益≥3%NAV → 申请实盘
"""
import json, time, urllib.request
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).parent.parent
PAPER_LOG = ROOT / 'data/paper_trade_log.jsonl'
PAPER_STATS = ROOT / 'data/paper_trade_stats.json'


def fetch_price(symbol):
    try:
        url = f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={symbol}'
        with urllib.request.urlopen(url, timeout=5) as r:
            return float(json.loads(r.read())['price'])
    except:
        return None


def open_paper_trade(symbol, direction, entry, sl, tp1, tp2, tp3,
                     score, regime, nav_pct, rr, basis=''):
    """记录一笔纸面开单"""
    now = datetime.now(timezone.utc)
    trade = {
        'id': f'PAPER-{symbol[:3]}-{now.strftime("%H%M%S")}',
        'symbol': symbol,
        'direction': direction,
        'entry': entry,
        'sl': sl,
        'tp1': tp1, 'tp2': tp2, 'tp3': tp3,
        'score': score,
        'regime': regime,
        'nav_pct': nav_pct,
        'rr': rr,
        'basis': basis,
        'status': 'OPEN',
        'open_ts': now.isoformat(),
        'result': None,
        'exit_price': None,
        'pnl_pct': None,
        'close_reason': None,
    }
    with open(PAPER_LOG, 'a') as f:
        f.write(json.dumps(trade, ensure_ascii=False) + '\n')
    print(f'📝 纸面开单: {symbol} {direction} @{entry} SL={sl} TP1={tp1} score={score}')
    return trade


def settle_paper_trades():
    """结算所有开放的纸面仓位"""
    if not PAPER_LOG.exists():
        return []

    trades = []
    with open(PAPER_LOG) as f:
        for line in f:
            if line.strip():
                trades.append(json.loads(line))

    settled = []
    updated = []
    for t in trades:
        if t['status'] != 'OPEN':
            updated.append(t)
            continue

        price = fetch_price(t['symbol'])
        if not price:
            updated.append(t)
            continue

        entry = float(t['entry'])
        sl = float(t['sl'])
        tp1 = float(t['tp1'])
        tp2 = float(t.get('tp2') or tp1)
        tp3 = float(t.get('tp3') or tp2)
        direction = t['direction']

        # 判断触发
        if direction == 'SHORT':
            pnl_pct = (entry - price) / entry * 100
            if price >= sl:
                t['status'] = 'CLOSED'; t['result'] = 'LOSS'
                t['exit_price'] = sl; t['pnl_pct'] = (entry-sl)/entry*100*-1
                t['close_reason'] = 'SL'
            elif price <= tp3:
                t['status'] = 'CLOSED'; t['result'] = 'WIN_T3'
                t['exit_price'] = tp3; t['pnl_pct'] = (entry-tp3)/entry*100
                t['close_reason'] = 'TP3'
            elif price <= tp2:
                t['status'] = 'CLOSED'; t['result'] = 'WIN_T2'
                t['exit_price'] = tp2; t['pnl_pct'] = (entry-tp2)/entry*100
                t['close_reason'] = 'TP2'
            elif price <= tp1:
                t['status'] = 'CLOSED'; t['result'] = 'WIN_T1'
                t['exit_price'] = tp1; t['pnl_pct'] = (entry-tp1)/entry*100
                t['close_reason'] = 'TP1'
        else:  # LONG
            if price <= sl:
                t['status'] = 'CLOSED'; t['result'] = 'LOSS'
                t['exit_price'] = sl; t['pnl_pct'] = (sl-entry)/entry*100
                t['close_reason'] = 'SL'
            elif price >= tp3:
                t['status'] = 'CLOSED'; t['result'] = 'WIN_T3'
                t['exit_price'] = tp3; t['pnl_pct'] = (tp3-entry)/entry*100
                t['close_reason'] = 'TP3'
            elif price >= tp2:
                t['status'] = 'CLOSED'; t['result'] = 'WIN_T2'
                t['exit_price'] = tp2; t['pnl_pct'] = (tp2-entry)/entry*100
                t['close_reason'] = 'TP2'
            elif price >= tp1:
                t['status'] = 'CLOSED'; t['result'] = 'WIN_T1'
                t['exit_price'] = tp1; t['pnl_pct'] = (tp1-entry)/entry*100
                t['close_reason'] = 'TP1'

        if t['status'] == 'CLOSED':
            t['close_ts'] = datetime.now(timezone.utc).isoformat()
            settled.append(t)
            print(f'✅ 纸面结算: {t["id"]} {t["result"]} pnl={t["pnl_pct"]:+.2f}% {t["close_reason"]}')
        updated.append(t)

    # 回写
    with open(PAPER_LOG, 'w') as f:
        for t in updated:
            f.write(json.dumps(t, ensure_ascii=False) + '\n')

    return settled


def calc_stats():
    """计算胜率统计"""
    if not PAPER_LOG.exists():
        return {}

    trades = []
    with open(PAPER_LOG) as f:
        for line in f:
            if line.strip():
                trades.append(json.loads(line))

    closed = [t for t in trades if t['status'] == 'CLOSED']
    if not closed:
        return {'total': 0, 'wr': 0, 'msg': '暂无结算记录'}

    wins = [t for t in closed if 'WIN' in (t.get('result') or '')]
    losses = [t for t in closed if t.get('result') == 'LOSS']
    wr = len(wins) / len(closed) * 100 if closed else 0
    total_pnl = sum(float(t.get('pnl_pct') or 0) for t in closed)
    avg_win = sum(float(t.get('pnl_pct') or 0) for t in wins) / len(wins) if wins else 0
    avg_loss = sum(float(t.get('pnl_pct') or 0) for t in losses) / len(losses) if losses else 0

    stats = {
        'total': len(closed), 'wins': len(wins), 'losses': len(losses),
        'wr_pct': round(wr, 1), 'total_pnl_pct': round(total_pnl, 2),
        'avg_win_pct': round(avg_win, 2), 'avg_loss_pct': round(avg_loss, 2),
        'updated': datetime.now(timezone.utc).isoformat(),
        'real_trade_eligible': wr >= 60 and len(closed) >= 30,
    }

    with open(PAPER_STATS, 'w') as f:
        json.dump(stats, f, indent=2, ensure_ascii=False)
    return stats


def print_report():
    stats = calc_stats()
    if not stats.get('total'):
        print('📊 纸面开单: 暂无记录，系统待激活')
        return

    wr = stats['wr_pct']
    grade = '🔥顶级' if wr>=75 else '✅优秀' if wr>=65 else '⚠️达标' if wr>=55 else '❌不足'
    eligible = '✅ 可申请实盘' if stats.get('real_trade_eligible') else f'❌ 需WR≥60%+n≥30（当前n={stats["total"]})'

    print(f'📊 梵天纸面开单统计')
    print(f'  总计: {stats["total"]}笔 | 胜: {stats["wins"]} | 负: {stats["losses"]}')
    print(f'  WR: {wr}% {grade}')
    print(f'  总收益: {stats["total_pnl_pct"]:+.2f}%')
    print(f'  均盈/均亏: {stats["avg_win_pct"]:+.2f}% / {stats["avg_loss_pct"]:+.2f}%')
    print(f'  实盘资格: {eligible}')


if __name__ == '__main__':
    import sys
    if '--settle' in sys.argv:
        settled = settle_paper_trades()
        print(f'结算 {len(settled)} 笔')
    elif '--stats' in sys.argv:
        print_report()
    elif '--open' in sys.argv:
        # 示例：手动开一笔纸面仓
        open_paper_trade('ETHUSDT','SHORT',2501.11,2546,2453,2407,2294,6.1,'CHOP_MID',3,4.61,'CHOP_MID SHORT WR=88%')
    else:
        # 默认：结算+统计
        settle_paper_trades()
        print_report()
