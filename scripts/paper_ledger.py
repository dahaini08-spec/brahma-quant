#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
paper_ledger.py — B线纸面账本 SSOT（单一真相源）

[纸面实盘化 2026-09-26 苏摩111]
- 本金 100,000 USDT（paper_account.json = 唯一账本）
- 所有开仓/平仓必须经过本模块记账，任何脚本不得绕过
- 费用模型与实盘对齐：taker 4bps / slippage 3bps / funding 1bp per 8h
- 每次平仓自动回写 NAV / realized_pnl / max_drawdown / nav_history

调用方（唯一合法入口）:
  - scripts/paper_executor.py   开仓时: paper_ledger.open_position(...)
  - scripts/paper_tp_monitor.py 平仓时: paper_ledger.close_position(...)

铁律（宪法）:
  1. 不经账本的开仓/平仓 = 幻影（9.26教训：本地记录与真实交易所零对账的代价）
  2. NAV = start_nav + Σ realized_pnl − Σ fees − Σ funding
  3. 单日亏损 >3% NAV → 账本记录 circuit_breaker 标记（供复盘）
  4. nav_history.jsonl 每日一行，永不间断（终结0字节黑洞）
"""
import json
import math
import time
from pathlib import Path
import sys as _sys

# [W0 2026-09-28 苏摩111] append-only事件流接线：账本写入点同步append不可变事件
_scripts_dir = str(Path(__file__).resolve().parent)
if _scripts_dir not in _sys.path:
    _sys.path.insert(0, _scripts_dir)
try:
    import brahma_events as _events
except Exception:
    _events = None  # 事件流不可用时账本照常工作（事件流=增强证据层，非账本依赖）

BASE = Path(__file__).parent.parent
DATA = BASE / 'data'
PAPER_ACCOUNT = DATA / 'paper_account.json'
NAV_HISTORY = DATA / 'nav_history.jsonl'
LEDGER_LOG = DATA / 'paper_ledger_log.jsonl'

# ── 费率模型（与实盘对齐，2026-09-26封印）──
TAKER_FEE_BPS = 4.0       # 0.04% 单边
SLIPPAGE_BPS = 3.0        # 0.03% 单边（市价单保守估计）
FUNDING_BPS_8H = 1.0      # 0.01% / 8h（保守均值）
MAX_GROSS_LEV = 5.0       # 杠杆上限（P0停血封印延续）
MAX_SYMBOL_WEIGHT = 0.10  # 单标的最大10%NAV（MEMORY铁律）
DAILY_DD_HALT_PCT = 3.0   # 单日亏损3%NAV → 复盘标记

START_NAV = 100000.0


def _load() -> dict:
    return json.loads(PAPER_ACCOUNT.read_text())


def _save(acc: dict) -> None:
    tmp = PAPER_ACCOUNT.with_suffix('.tmp')
    tmp.write_text(json.dumps(acc, ensure_ascii=False, indent=2))
    tmp.replace(PAPER_ACCOUNT)  # 原子写


def nav() -> float:
    return float(_load().get('nav_current', START_NAV))


def stats() -> dict:
    return _load()


# ── 仓位数学 ──────────────────────────────────────────────
def size_position(nav_pct: float, leverage: float, price: float) -> tuple[float, float, float]:
    """返回 (margin, notional, qty)。铁律：nav_pct≤10%，lev≤5x"""
    acc = _load()
    cur_nav = float(acc.get('nav_current', START_NAV))
    nav_pct = min(float(nav_pct), MAX_SYMBOL_WEIGHT)
    leverage = min(float(leverage), MAX_GROSS_LEV)
    margin = cur_nav * nav_pct
    notional = margin * leverage
    qty = notional / price if price > 0 else 0.0
    return round(margin, 2), round(notional, 2), round(qty, 6)


def open_cost(notional: float) -> float:
    """开仓成本 = taker费 + 滑点"""
    return notional * (TAKER_FEE_BPS + SLIPPAGE_BPS) / 10_000.0


def close_cost(notional: float) -> float:
    """平仓成本 = taker费 + 滑点（与open_cost对称）"""
    return notional * (TAKER_FEE_BPS + SLIPPAGE_BPS) / 10_000.0


def round_trip_cost(notional: float, hours_held: float = 0.0) -> float:
    """往返成本 = 双边taker+滑点 + funding(按8h计提)"""
    base = 2 * notional * (TAKER_FEE_BPS + SLIPPAGE_BPS) / 10_000.0
    funding = abs(notional) * FUNDING_BPS_8H / 10_000.0 * (hours_held / 8.0)
    return round(base + funding, 4)


def pnl_math(side: str, qty: float, entry: float, exit_: float, notional: float, hours_held: float = 0.0) -> dict:
    """毛利 − 全部成本 = 净PnL。唯一口径，任何平仓必须走这里"""
    gross = (exit_ - entry) * qty if side.upper() == 'LONG' else (entry - exit_) * qty
    costs = round_trip_cost(notional, hours_held)
    net = gross - costs
    return {
        'gross': round(gross, 4),
        'costs': costs,
        'net': round(net, 4),
        'net_pct_on_nav': round(net / START_NAV * 100, 4),
    }


# ── 记账入口 ──────────────────────────────────────────────
def open_position(symbol: str, side: str, price: float, nav_pct: float, leverage: float,
                  sl: float, tp1: float, tp2: float = None, regime: str = '', score: float = 0,
                  rr: float = 0, source: str = '', note: str = '') -> dict:
    """开仓记账：扣费入账，写入paper_orders.jsonl，返回完整订单记录"""
    acc = _load()
    margin, notional, qty = size_position(nav_pct, leverage, price)
    if qty <= 0 or price <= 0:
        raise ValueError(f'仓位数学异常: margin={margin} notional={notional} qty={qty} price={price}')
    fee = open_cost(notional)
    # NAV口径：保证金占用不扣NAV，费用立扣
    acc['nav_current'] = round(float(acc['nav_current']) - fee, 4)
    acc['fees_paid_total'] = round(float(acc.get('fees_paid_total', 0)) + fee, 4)
    acc['total_trades'] = int(acc.get('total_trades', 0)) + 1
    acc['updated_at'] = int(time.time())
    _save(acc)

    rec = {
        'id': f'PL-{int(time.time())}-{symbol}-{side}',
        'ts': int(time.time()),
        'ts_iso': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'symbol': symbol, 'side': side.upper(),
        'entry_price': price, 'qty': qty, 'notional': notional, 'margin': margin,
        'leverage': leverage, 'nav_pct': nav_pct,
        'sl': sl, 'tp1': tp1, 'tp2': tp2,
        'regime': regime, 'score': score, 'rr': rr, 'source': source,
        'entry_fee': round(fee, 4),
        'status': 'OPEN',
        'note': note,
    }
    with open(DATA / 'paper_orders.jsonl', 'a') as f:
        f.write(json.dumps(rec, ensure_ascii=False) + '\n')
    with open(LEDGER_LOG, 'a') as f:
        f.write(json.dumps({'ev': 'OPEN', **{k: rec[k] for k in ('id', 'symbol', 'side', 'notional', 'entry_fee')}}, ensure_ascii=False) + '\n')
    # [W0] 同步append事件流（不可变证据，缺事件流不阻账本）
    if _events:
        try:
            _events.append('order_opened', {k: rec[k] for k in ('id', 'symbol', 'side', 'entry_price', 'qty', 'notional', 'leverage', 'nav_pct', 'sl', 'tp1', 'regime', 'score', 'rr', 'source', 'entry_fee')})
        except Exception:
            pass
    return rec


def close_position(order: dict, exit_price: float, reason: str) -> dict:
    """平仓记账：pnl_math唯一口径 → 回写NAV/统计 → nav_history落盘"""
    acc = _load()
    entry = float(order.get('entry_price', 0))
    qty = order.get('qty')
    notional = order.get('notional')
    # [9.26 B线] 兼容旧仓位（无qty/notional字段）：用nav_pct×lev重建，绝不让None炸账本
    if (qty is None or float(qty) <= 0) and (notional is None or float(notional) <= 0):
        _nav_pct = float(order.get('nav_pct', 0.05))
        _lev = float(order.get('leverage', 5.0))
        notional = nav() * _nav_pct * _lev
        qty = notional / entry if entry > 0 else 0.0
    if notional is None or float(notional) <= 0:
        notional = float(qty) * entry
    if qty is None or float(qty) <= 0:
        qty = float(notional) / entry if entry > 0 else 0.0
    qty = float(qty); notional = float(notional)
    open_ts = float(order.get('ts', time.time()))
    hours_held = max(0.0, (time.time() - open_ts) / 3600.0)
    m = pnl_math(order['side'], qty, entry, exit_price, notional, hours_held)
    net = m['net']

    acc['nav_current'] = round(float(acc['nav_current']) + net, 4)
    acc['realized_pnl'] = round(float(acc.get('realized_pnl', 0)) + net, 4)
    if net > 0:
        acc['win_trades'] = int(acc.get('win_trades', 0)) + 1
    elif net < 0:
        acc['loss_trades'] = int(acc.get('loss_trades', 0)) + 1
    # 峰值与回撤
    cur = float(acc['nav_current'])
    peak = max(float(acc.get('peak_nav', START_NAV)), cur)
    acc['peak_nav'] = peak
    dd = (peak - cur) / peak * 100 if peak > 0 else 0.0
    acc['max_drawdown_pct'] = round(max(float(acc.get('max_drawdown_pct', 0)), dd), 4)
    acc['updated_at'] = int(time.time())
    _save(acc)

    closed_rec = dict(order)
    closed_rec.update({
        'status': 'CLOSED',
        'exit_price': exit_price,
        'close_reason': reason,
        'close_ts': int(time.time()),
        'hours_held': round(hours_held, 2),
        'gross_pnl': m['gross'],
        'costs': m['costs'],
        'net_pnl': m['net'],
        'net_pnl_pct_nav': m['net_pct_on_nav'],
    })
    with open(DATA / 'paper_orders.jsonl', 'a') as f:
        f.write(json.dumps(closed_rec, ensure_ascii=False) + '\n')
    with open(LEDGER_LOG, 'a') as f:
        # [P0-1修复 2026-09-30 苏摩111] CLOSE事件补ts/ts_iso——根因：_today_realized()按ts_iso过滤，
        # 旧CLOSE事件缺该字段→单日熔断永远无法触发（熔断器事实性死亡）
        f.write(json.dumps({'ev': 'CLOSE', 'id': order.get('id'), 'symbol': order.get('symbol'),
                            'reason': reason, 'net': m['net'], 'nav_after': acc['nav_current'],
                            'ts': int(time.time()),
                            'ts_iso': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}, ensure_ascii=False) + '\n')
    # [W0] 同步append事件流：平仓+结算双事件（不可变证据）
    if _events:
        try:
            _events.append('order_closed', {'id': order.get('id'), 'symbol': order.get('symbol'), 'side': order.get('side'), 'entry_price': entry, 'exit_price': exit_price, 'close_reason': reason, 'hours_held': round(hours_held, 2)})
            _events.append('pnl_settled', {'id': order.get('id'), 'symbol': order.get('symbol'), 'side': order.get('side'), 'regime': order.get('regime', ''), 'net_pnl': m['net'], 'net_pnl_pct': m.get('net_pct_on_nav', 0), 'costs': m['costs'], 'nav_after': acc['nav_current']})
        except Exception:
            pass
    _append_nav_history(acc, event=f'close:{order.get("symbol")}:{reason}')
    # 单日熔断标记
    today = time.strftime('%Y-%m-%d', time.gmtime())
    day_pnl = _today_realized()
    if day_pnl / START_NAV * 100 <= -DAILY_DD_HALT_PCT:
        _mark_circuit_breaker(today, day_pnl)
    return closed_rec


def _today_realized() -> float:
    today = time.strftime('%Y-%m-%d', time.gmtime())
    total = 0.0
    try:
        for line in open(LEDGER_LOG):
            try:
                j = json.loads(line)
            except Exception:
                continue
            if j.get('ev') != 'CLOSE':
                continue
            # [P0-1修复 2026-09-30 苏摩111] 日期兼容：新事件有ts_iso，旧事件从ts推导
            _d = str(j.get('ts_iso', ''))[:10]
            if not _d and j.get('ts'):
                _d = time.strftime('%Y-%m-%d', time.gmtime(float(j['ts'])))
            if _d == today:
                total += float(j.get('net', 0))
    except FileNotFoundError:
        pass
    return total


def _mark_circuit_breaker(today: str, day_pnl: float) -> None:
    cb = DATA / 'paper_circuit_breaker.json'
    cb.write_text(json.dumps({'date': today, 'day_pnl': day_pnl, 'triggered_at': time.time(),
                              'reason': f'单日亏损{abs(day_pnl)/START_NAV*100:.2f}%NAV ≥ {DAILY_DD_HALT_PCT}%'}, ensure_ascii=False))


def circuit_breaker_active() -> bool:
    cb = DATA / 'paper_circuit_breaker.json'
    if not cb.exists():
        return False
    try:
        d = json.loads(cb.read_text())
        return d.get('date') == time.strftime('%Y-%m-%d', time.gmtime())
    except Exception:
        return False


def _append_nav_history(acc: dict, event: str = 'daily') -> None:
    today = time.strftime('%Y-%m-%d', time.gmtime())
    row = {
        'date': today, 'ts': int(time.time()),
        'nav': acc['nav_current'], 'peak': acc.get('peak_nav', START_NAV),
        'realized_pnl': acc.get('realized_pnl', 0),
        'max_dd_pct': acc.get('max_drawdown_pct', 0),
        'event': event,
    }
    # 同日只保留最新一行（upsert语义）
    rows = []
    if NAV_HISTORY.exists():
        for line in NAV_HISTORY.read_text().splitlines():
            if not line.strip():
                continue
            try:
                j = json.loads(line)
                if j.get('date') != today:
                    rows.append(j)
            except Exception:
                continue
    rows.append(row)
    tmp = NAV_HISTORY.with_suffix('.tmp')
    tmp.write_text('\n'.join(json.dumps(r, ensure_ascii=False) for r in rows) + '\n')
    tmp.replace(NAV_HISTORY)


def daily_snapshot() -> dict | None:
    """每日复盘用：当日NAV快照（幂等）"""
    acc = _load()
    _append_nav_history(acc, event='daily_snapshot')
    return acc


# ── 自检（账本数学断言）────────────────────────────────────
def audit() -> tuple[bool, list[str]]:
    """NAV恒等式: nav_current == start_nav + realized_pnl - fees_paid_total - funding_paid_total(近似)"""
    acc = _load()
    errs = []
    expected = float(acc['start_nav']) + float(acc.get('realized_pnl', 0)) - float(acc.get('fees_paid_total', 0))
    if abs(expected - float(acc['nav_current'])) > 0.01:
        errs.append(f'NAV恒等式破坏: {expected} != {acc["nav_current"]}')
    if float(acc['nav_current']) <= 0:
        errs.append('NAV ≤ 0')
    return (len(errs) == 0), errs


if __name__ == '__main__':
    ok, errs = audit()
    s = stats()
    print(f"📊 Paper Ledger SSOT | NAV={s['nav_current']:,.2f} | realized={s.get('realized_pnl',0):+.2f} | "
          f"trades={s.get('total_trades',0)} W/L={s.get('win_trades',0)}/{s.get('loss_trades',0)} | "
          f"maxDD={s.get('max_drawdown_pct',0):.2f}%")
    print('✅ 账本审计通过' if ok else f'❌ 账本审计失败: {errs}')
