#!/usr/bin/env python3
"""
paper_executor.py — 纸面系统专属开单执行器
设计院封印 2026-09-03 苏摩111

定位：独立于 auto_executor.py，专为纸面验证系统设计
门槛：score≥80 + grade≥75（比实盘宽松，用于系统验证）
持仓：BTC/ETH各最多1单，每单5%NAV（纸面）
记录：写入 data/paper_positions.json，供 paper_tp_monitor.py 追踪

接入位置：
  - scripts/paper_executor.py（本文件）
  - supercronic: */40 * * * * python3 scripts/paper_executor.py
  - paper_tp_monitor.py 读取 paper_positions.json 做止盈追踪
"""
import json, sys, time
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'brahma_brain'))

PAPER_POS_FILE   = BASE / 'data' / 'paper_positions.json'
SIGNAL_QUEUE     = BASE / 'data' / 'auto_signal_queue.json'
PAPER_LOG        = BASE / 'logs' / 'paper_executor.log'

# 纸面系统专属门槛（比实盘宽松）
PAPER_SCORE_MIN  = 80
PAPER_GRADE_MIN  = 75
PAPER_NAV_PCT    = 0.05   # 5%NAV per trade
MAX_POSITIONS    = 1       # 每个标的最多1单

# 允许的体制（纸面系统不开死穴）
ALLOWED_REGIMES = {
    'LONG':  ['BULL_EARLY', 'BULL_TREND', 'BEAR_RECOVERY', 'CHOP_MID'],
    'SHORT': ['BEAR_TREND', 'BEAR_EARLY', 'CHOP_MID'],
}
# CHOP_MID纸面允许但仓位减半
CHOP_HALF_SIZE = True


def load_paper_positions() -> dict:
    if PAPER_POS_FILE.exists():
        try:
            return json.loads(PAPER_POS_FILE.read_text())
        except Exception:
            pass
    return {'positions': [], 'closed': [], 'stats': {'total': 0, 'win': 0, 'pnl': 0.0}}


def save_paper_positions(data: dict):
    PAPER_POS_FILE.write_text(json.dumps(data, indent=2))


def load_signal_queue() -> list:
    if SIGNAL_QUEUE.exists():
        try:
            d = json.loads(SIGNAL_QUEUE.read_text())
            return d if isinstance(d, list) else d.get('signals', [])
        except Exception:
            pass
    return []


def log(msg: str):
    ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    line = f'[{ts}] {msg}'
    print(line)
    try:
        with open(PAPER_LOG, 'a') as f:
            f.write(line + '\n')
    except Exception:
        pass


def get_current_price(symbol: str) -> float:
    try:
        import urllib.request
        url = f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={symbol}'
        r = json.loads(urllib.request.urlopen(url, timeout=5).read())
        return float(r['price'])
    except Exception:
        return 0.0


def open_paper_position(signal: dict, positions_data: dict) -> bool:
    """开纸面仓位"""
    sym    = signal.get('symbol', '')
    side   = signal.get('signal_dir', signal.get('direction', 'LONG'))
    score  = float(signal.get('score_final', signal.get('score', 0)))
    grade  = float(signal.get('grade_num', signal.get('grade', 0)))
    regime = signal.get('regime', '')
    sl_pct = float(signal.get('sl_pct', 2.0))
    tp1    = float(signal.get('tp1', 0))
    tp2    = float(signal.get('tp2', 0))

    # 门槛检查
    if score < PAPER_SCORE_MIN:
        log(f'SKIP {sym} {side}: score={score:.1f} < {PAPER_SCORE_MIN}')
        return False
    if grade < PAPER_GRADE_MIN:
        log(f'SKIP {sym} {side}: grade={grade:.1f} < {PAPER_GRADE_MIN}')
        return False

    # 体制检查
    allowed = ALLOWED_REGIMES.get(side, [])
    if regime and regime not in allowed:
        log(f'SKIP {sym} {side}: regime={regime} 不在允许列表 {allowed}')
        return False

    # 重复持仓检查
    existing = [p for p in positions_data['positions'] if p['symbol'] == sym]
    if len(existing) >= MAX_POSITIONS:
        log(f'SKIP {sym}: 已有{len(existing)}个持仓，上限{MAX_POSITIONS}')
        return False

    price = get_current_price(sym)
    if not price:
        log(f'SKIP {sym}: 无法获取实时价格')
        return False

    # 仓位大小（CHOP_MID减半）
    nav_pct = PAPER_NAV_PCT
    if CHOP_HALF_SIZE and 'CHOP' in regime:
        nav_pct = PAPER_NAV_PCT / 2
        log(f'CHOP体制，仓位减半 → {nav_pct*100:.1f}%NAV')

    # SL/TP计算
    if side == 'LONG':
        sl_price = price * (1 - sl_pct / 100)
        tp1_price = tp1 if tp1 else price * 1.02
        tp2_price = tp2 if tp2 else price * 1.04
    else:
        sl_price = price * (1 + sl_pct / 100)
        tp1_price = tp1 if tp1 else price * 0.98
        tp2_price = tp2 if tp2 else price * 0.96

    pos = {
        'symbol':       sym,
        'side':         side,
        'entry_price':  price,
        'sl_price':     round(sl_price, 2),
        'tp1':          round(tp1_price, 2),   # 统一字段名
        'tp1_price':    round(tp1_price, 2),
        'tp2':          round(tp2_price, 2),
        'tp2_price':    round(tp2_price, 2),
        'nav_pct':      nav_pct,
        'score':        score,
        'grade':        grade,
        'regime':       regime,
        'sl_pct':       sl_pct,
        'open_ts':      int(time.time()),
        'open_at':      __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),
        'partial_tp1':  False,
        'status':       'open',
        'source':       signal.get('source', 'unknown'),
    }
    positions_data['positions'].append(pos)
    positions_data['stats']['total'] = positions_data['stats'].get('total', 0) + 1
    log(f'OPEN {sym} {side} @{price:.2f} SL={sl_price:.2f} TP1={tp1_price:.2f} score={score:.1f} regime={regime}')
    return True


def main():
    signals = load_signal_queue()
    if not signals:
        print('HEARTBEAT_OK')
        return

    positions_data = load_paper_positions()
    opened = 0

    for sig in signals:
        # 只处理未被纸面系统消费的信号
        if sig.get('paper_consumed'):
            continue
        if open_paper_position(sig, positions_data):
            sig['paper_consumed'] = True
            opened += 1

    if opened:
        save_paper_positions(positions_data)
        # 更新signal_queue标记已消费
        try:
            SIGNAL_QUEUE.write_text(json.dumps(signals, indent=2))
        except Exception:
            pass
        log(f'本轮开单 {opened} 笔，当前持仓 {len(positions_data["positions"])} 个')
    else:
        print('HEARTBEAT_OK')


if __name__ == '__main__':
    main()
