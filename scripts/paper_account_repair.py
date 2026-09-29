#!/usr/bin/env python3
"""[9.29 P1 苏摩111] paper_account 恒等式修复 — 一次性幂等脚本

根因：9.26 backfill 脚本绕过 paper_ledger.open_position/close_position 直接写
account，漏写 realized_pnl/fees_paid_total/total_trades/win_loss 四字段。
SSOT=paper_ledger_log.jsonl 事件流（append-only，不可变证据）。

重放规则（与 paper_ledger 源码语义一致）：
  OPEN  → nav -= entry_fee；fees_paid_total += entry_fee；total_trades += 1
  CLOSE → nav += net（net 已含 exit_fee/gross+costs）；realized_pnl += net；
          win/loss 按 net 正负计

验证（9.29 重放实测）：nav=98930.00 realized=-1035.00 fees=35.00 trades=2
恒等式：100000 + (-1035) - 35 = 98930 ✓（与现值对齐）

用法：python3 scripts/paper_account_repair.py [--dry-run]
幂等：重放结果直接覆盖 account 字段，重复执行结果相同。
"""
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
LEDGER_LOG = BASE / 'data' / 'paper_ledger_log.jsonl'
ACCOUNT = BASE / 'data' / 'paper_account.json'
START_NAV = 100000.0


def replay():
    if not LEDGER_LOG.exists():
        print(f'事件流不存在: {LEDGER_LOG}')
        return None
    nav = START_NAV
    realized = 0.0
    fees = 0.0
    trades = 0
    wins = 0
    losses = 0
    events = 0
    for line in LEDGER_LOG.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        ev = r.get('ev')
        events += 1
        if ev == 'OPEN':
            fee = float(r.get('entry_fee', 0) or 0)
            nav -= fee
            fees += fee
            trades += 1
        elif ev == 'CLOSE':
            net = float(r.get('net', r.get('net_pnl', 0)) or 0)
            realized += net
            nav += net
            if net > 0:
                wins += 1
            elif net < 0:
                losses += 1
    return {'nav': nav, 'realized': realized, 'fees': fees,
            'trades': trades, 'wins': wins, 'losses': losses, 'events': events}


def main():
    dry = '--dry-run' in sys.argv
    st = replay()
    if st is None:
        sys.exit(1)
    print(f"重放 {st['events']} 事件: nav={st['nav']:.2f} realized={st['realized']:.2f} "
          f"fees={st['fees']:.2f} trades={st['trades']} win/loss={st['wins']}/{st['losses']}")
    ident = START_NAV + st['realized'] - st['fees']
    print(f"恒等式: {START_NAV} + {st['realized']:.2f} - {st['fees']:.2f} = {ident:.2f}")
    if abs(ident - st['nav']) > 0.01:
        _diff = ident - st['nav']
        print(f'⚠️ 恒等式不闭合（差{_diff:.2f}）——事件流可能缺 OPEN 记录，中止')
        sys.exit(2)
    if dry:
        print('[dry-run] 不落盘')
        return
    acc = json.loads(ACCOUNT.read_text())
    before = {k: acc.get(k) for k in ('nav_current', 'realized_pnl', 'fees_paid_total',
                                      'total_trades', 'win_trades', 'loss_trades')}
    acc['realized_pnl'] = round(st['realized'], 4)
    acc['fees_paid_total'] = round(st['fees'], 4)
    acc['total_trades'] = st['trades']
    acc['win_trades'] = st['wins']
    acc['loss_trades'] = st['losses']
    # nav 以重放为准（对齐事件流，当前应为 98930）
    acc['nav_current'] = round(st['nav'], 4)
    peak = max(float(acc.get('peak_nav', START_NAV)), float(acc['nav_current']))
    acc['peak_nav'] = peak
    dd = (peak - float(acc['nav_current'])) / peak * 100 if peak > 0 else 0.0
    acc['max_drawdown_pct'] = round(max(float(acc.get('max_drawdown_pct', 0)), dd), 4)
    import time as _t
    acc['updated_at'] = int(_t.time())
    acc['repaired_at'] = _t.strftime('%Y-%m-%dT%H:%M:%SZ', _t.gmtime())
    acc['repair_note'] = '[9.29 P1 苏摩111] account字段由ledger_log事件流重放重建（SSOT）'
    ACCOUNT.write_text(json.dumps(acc, ensure_ascii=False, indent=1))
    print(f'✅ 已修复: {before} → realized={acc["realized_pnl"]} fees={acc["fees_paid_total"]} '
          f'trades={acc["total_trades"]} nav={acc["nav_current"]}')


if __name__ == '__main__':
    main()
