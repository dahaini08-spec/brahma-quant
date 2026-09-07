#!/usr/bin/env python3
"""
ledger_backfill.py — EquityLedger历史数据回填
把wuqu_paper_settled.jsonl的历史交易回放到EquityLedger
接入位置：scripts/ledger_backfill.py（一次性运行）
2026-09-07 三方联合封印
"""
import json, time, sys
from pathlib import Path

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))

from brahma_os.config import Settings
from brahma_os.costs import CostModel
from brahma_os.ledger import EquityLedger
from brahma_os.contracts import Fill

DATA = BASE / 'data'
LEDGER_OUT = DATA / 'paper_ledger_backfilled.json'

def backfill():
    cfg = Settings(start_nav=10_000.0)
    cost = CostModel(cfg)
    ledger = EquityLedger(settings=cfg, cost=cost)

    src = DATA / 'wuqu_paper_settled.jsonl'
    trades = []
    for line in src.read_text().splitlines():
        if not line.strip(): continue
        try:
            d = json.loads(line)
            if d.get('outcome') in ('TP1','TP2','SL','TIMEOUT') and d.get('pnl_pct') is not None:
                trades.append(d)
        except: pass

    # 按时间排序回放
    def _to_ts(val):
        if not val: return 0.0
        try: return float(val)
        except:
            from datetime import datetime
            try: return datetime.fromisoformat(str(val).replace('Z','+00:00')).timestamp()
            except: return 0.0

    trades.sort(key=lambda x: _to_ts(x.get('open_ts') or x.get('close_ts')))
    print(f'回放 {len(trades)} 条历史交易...')

    filled = 0
    for t in trades:
        try:
            entry_px  = float(t.get('entry_price') or 0)
            if entry_px <= 0: continue
            pnl_pct   = float(t.get('pnl_pct') or 0)
            notional  = ledger.nav() * 0.05          # 假设5%NAV仓位
            qty       = notional / entry_px if entry_px else 0
            open_ts   = _to_ts(t.get('open_ts')) or time.time()
            close_ts  = _to_ts(t.get('close_ts')) or open_ts + 3600
            hold_hrs  = max(0.5, (close_ts - open_ts) / 3600)
            side      = str(t.get('signal_dir') or 'LONG').upper()
            symbol    = str(t.get('symbol') or 'UNKNOWN')
            outcome   = str(t.get('outcome') or 'SL')

            fee_open  = notional * cfg.taker_fee_bps / 10000
            slip_open = notional * cfg.slippage_bps  / 10000

            entry_fill = Fill(
                fill_id   = f"bf-{t.get('signal_id','?')}-entry",
                intent_id = f"bf-intent-{filled}",
                signal_id = str(t.get('signal_id') or ''),
                ts=open_ts, symbol=symbol, side=side,
                qty=qty, price=entry_px,
                fee=fee_open, slippage=slip_open, role='ENTRY',
            )
            sl = float(t.get('stop_loss') or entry_px * 0.98)
            tp = float(t.get('tp1') or entry_px * 1.03)
            ledger.apply_entry(entry_fill, stop=sl, target=tp)

            # 计算exit价格
            gross_pnl = notional * pnl_pct / 100
            fee_close = notional * cfg.taker_fee_bps / 10000
            slip_close = notional * cfg.slippage_bps / 10000
            funding    = cost.funding(notional, hold_hrs)
            net_pnl    = gross_pnl - fee_open - fee_close - slip_open - slip_close - funding

            if side == 'LONG':
                exit_px = entry_px * (1 + pnl_pct / 100)
            else:
                exit_px = entry_px * (1 - pnl_pct / 100)

            exit_fill = Fill(
                fill_id   = f"bf-{t.get('signal_id','?')}-exit",
                intent_id = f"bf-intent-{filled}",
                signal_id = str(t.get('signal_id') or ''),
                ts=close_ts, symbol=symbol, side=side,
                qty=qty, price=max(0.0001, exit_px),
                fee=fee_close, slippage=slip_close,
                role='TARGET' if outcome in ('TP1','TP2') else 'STOP',
            )
            result_outcome = 'WIN' if outcome in ('TP1','TP2') else 'LOSS'
            ledger.apply_exit(entry_fill.fill_id, exit_fill, hold_hrs, result_outcome)
            filled += 1

        except Exception as e:
            pass  # 静默跳过缺字段的条目

    snap = ledger.snapshot()
    print(f'回填完成: {filled}/{len(trades)} 条')
    print(f'NAV: ${snap.nav:,.2f}  (起始$10,000)')
    print(f'WR: {snap.wr:.1%}  wins={snap.wins} losses={snap.losses}')
    print(f'Sharpe: {snap.sharpe:.3f}' if snap.sharpe else 'Sharpe: 数据不足')
    print(f'MaxDD: {snap.max_drawdown:.1%}')
    print(f'EV/笔: ${snap.ev_usd:.2f}')

    # 保存
    out = {
        'backfill_ts': time.time(),
        'n_trades': filled,
        'nav': snap.nav, 'peak': snap.peak,
        'wr': round(snap.wr, 4),
        'wins': snap.wins, 'losses': snap.losses, 'timeouts': snap.timeouts,
        'max_drawdown': round(snap.max_drawdown, 6),
        'sharpe': snap.sharpe,
        'ev_usd': round(snap.ev_usd, 4),
        'days': snap.days,
    }
    LEDGER_OUT.write_text(json.dumps(out, indent=2))
    print(f'已保存: {LEDGER_OUT}')
    return out

if __name__ == '__main__':
    backfill()
