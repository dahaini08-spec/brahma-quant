#!/usr/bin/env python3
"""
brahma_lifecycle.py  — 梵天持仓全生命周期管理器
[设计院封印 2026-07-17 苏摩111授权]

架构：开单 → 持仓监控 → 自动止盈/止损 → 结果归档

                    ┌─────────────────────────────┐
  梵天信号           │     brahma_lifecycle.py      │
  score≥155  ──────▶│                             │
                    │  ① Entry Gate（五重门控）     │
                    │  ② 自动开单（市价/限价）      │
                    │  ③ 写入 lifecycle_db.jsonl   │
                    │  ④ 每5min感知SL/TP触发       │
                    │  ⑤ 自动执行平仓              │
                    │  ⑥ 推送结果 → Jarvis         │
                    │  ⑦ 归档至 lifecycle_archive  │
                    └─────────────────────────────┘

状态机：
  PENDING  → 信号触发，等待开仓条件
  OPEN     → 已开仓，监控中
  PARTIAL  → TP1已止盈50%，剩余追踪
  CLOSED   → 全部平仓（SL触发/TP2触发/手动）
"""
import sys, os
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, 'scripts'))
sys.path.insert(0, os.path.join(_ROOT, 'brahma_brain'))

import json, time, subprocess, requests, hashlib, math
from pathlib import Path

# ═══════════════════════════════════════════════════════
# 配置常量
# ═══════════════════════════════════════════════════════
DIR          = _ROOT
DB_FILE      = os.path.join(DIR, 'data', 'lifecycle_db.jsonl')
ARCHIVE_FILE = os.path.join(DIR, 'data', 'lifecycle_archive.jsonl')
SIGNAL_LOG   = os.path.join(DIR, 'data', 'live_signal_log.jsonl')
WUQU_FILE    = os.path.join(DIR, 'data', 'wuqu_positions.json')

# 门控参数
AUTO_SCORE_MIN   = 155    # 自动开单最低评分
MAX_NAV_PER_POS  = 0.05   # 单仓最大5% NAV
MAX_TOTAL_NAV    = 0.60   # 总仓位最大60% NAV
DEFAULT_LEV      = 3      # 默认杠杆
MAX_POSITIONS    = 12     # 最大并行仓位数

# SL/TP规则（宪法级）
SL_PCT_DEFAULT   = 2.0    # 默认止损2%
TP1_RR           = 1.5    # TP1 = SL × 1.5
TP2_RR           = 3.0    # TP2 = SL × 3.0
TP1_CLOSE_PCT    = 0.50   # TP1触及平仓50%

# 死穴（绝对禁止）
HARD_BLOCK = {
    'BEAR_TREND_LONG', 'BULL_TREND_SHORT',
    'BEAR_RECOVERY_SHORT', 'BULL_CORRECTION_LONG',
}


def _jarvis_target() -> str:
    try:
        from system_config import JARVIS_USER_ID, JARVIS_THREAD_ID
        return f'{JARVIS_USER_ID}:thread:{JARVIS_THREAD_ID}'
    except Exception:
        return '73295708:thread:019fd9dd-4b0f-71db-87fb-1e192ccb2291'


def push(msg: str):
    subprocess.run(
        ['openclaw', 'message', 'send', '--channel', 'jarvis',
         '--to', _jarvis_target(), '--message', msg],
        capture_output=True, timeout=15
    )


def get_price(sym: str) -> float:
    try:
        r = requests.get(f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={sym}', timeout=5)
        return float(r.json()['price'])
    except Exception:
        return 0.0


def get_account() -> dict:
    """获取账户信息：余额、持仓数、NAV"""
    try:
        r = subprocess.run(
            ['binance-cli', 'futures-usds', 'account-information-v3'],
            capture_output=True, text=True, timeout=15
        )
        acct = json.loads(r.stdout)
        nav    = float(acct.get('totalMarginBalance', 0))
        avail  = float(acct.get('availableBalance', 0))
        wallet = float(acct.get('totalWalletBalance', 0))
        n_pos  = len([p for p in acct.get('positions', []) if abs(float(p.get('positionAmt', 0))) > 0])
        return dict(nav=nav, avail=avail, wallet=wallet, n_positions=n_pos, ok=True)
    except Exception as e:
        return dict(nav=0, avail=0, wallet=0, n_positions=0, ok=False, err=str(e))


def place_market_order(sym: str, side: str, qty: str, reduce_only: bool = False) -> dict:
    """下市价单"""
    cmd = ['binance-cli', 'futures-usds', 'new-order',
           '--symbol', sym, '--side', side, '--type', 'MARKET',
           '--quantity', qty]
    if reduce_only:
        cmd += ['--reduceOnly', 'true']
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    try:
        resp = json.loads(r.stdout)
        return dict(ok=True, orderId=resp.get('orderId'), status=resp.get('status'),
                    avgPrice=float(resp.get('avgPrice', 0) or 0),
                    executedQty=float(resp.get('executedQty', 0) or 0),
                    raw=resp)
    except Exception as e:
        return dict(ok=False, err=r.stdout[:200], exc=str(e))


def place_algo_stop(sym: str, side: str, qty: str, trigger: str) -> dict:
    """挂保本/止损algo order（PM账户需要）"""
    r = subprocess.run([
        'binance-cli', 'futures-usds', 'new-algo-order',
        '--algo-type', 'CONDITIONAL',
        '--symbol', sym, '--side', side,
        '--type', 'STOP_MARKET',
        '--quantity', qty,
        '--trigger-price', str(trigger),
        '--reduce-only', 'true',
        '--working-type', 'MARK_PRICE',
    ], capture_output=True, text=True, timeout=15)
    try:
        resp = json.loads(r.stdout)
        return dict(ok=True, algoId=resp.get('algoId'), triggerPrice=trigger)
    except Exception as e:
        return dict(ok=False, err=r.stdout[:200], exc=str(e))


# ═══════════════════════════════════════════════════════
# 持仓DB操作
# ═══════════════════════════════════════════════════════

def load_db() -> list:
    """读取所有活跃持仓记录"""
    records = []
    if not os.path.exists(DB_FILE):
        return records
    with open(DB_FILE) as f:
        for line in f:
            try:
                r = json.loads(line.strip())
                if r.get('status') not in ('CLOSED',):
                    records.append(r)
            except Exception:
                pass
    return records


def save_record(record: dict, mode: str = 'append'):
    """追加一条记录"""
    os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
    if mode == 'append':
        with open(DB_FILE, 'a') as f:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')
    elif mode == 'archive':
        with open(ARCHIVE_FILE, 'a') as f:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')


def update_db(records: list):
    """重写活跃持仓DB（覆盖写）"""
    os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
    with open(DB_FILE, 'w') as f:
        for r in records:
            if r.get('status') != 'CLOSED':
                f.write(json.dumps(r, ensure_ascii=False) + '\n')
            else:
                save_record(r, mode='archive')


def sync_wuqu(record: dict):
    """同步到 wuqu_positions.json"""
    try:
        data = json.load(open(WUQU_FILE)) if os.path.exists(WUQU_FILE) else []
        # 找到并更新，否则追加
        found = False
        for p in data:
            if p.get('symbol') == record['symbol'] and p.get('status', 'open') != 'closed':
                p.update({
                    'entry_price': record.get('entry_price', 0),
                    'stop_loss':   record.get('sl', 0),
                    'take_profit': record.get('tp1', 0),
                    'leverage':    record.get('lev', DEFAULT_LEV),
                    'size':        record.get('size', 0),
                    'notional_usdt': record.get('notional', 0),
                    'side':        record.get('direction', 'LONG'),
                    'status':      'open' if record['status'] != 'CLOSED' else 'closed',
                })
                found = True
                break
        if not found:
            data.append({
                'symbol':      record['symbol'],
                'side':        record.get('direction', 'LONG'),
                'size':        record.get('size', 0),
                'entry_price': record.get('entry_price', 0),
                'stop_loss':   record.get('sl', 0),
                'take_profit': record.get('tp1', 0),
                'leverage':    record.get('lev', DEFAULT_LEV),
                'notional_usdt': record.get('notional', 0),
                'status':      'open',
            })
        json.dump(data, open(WUQU_FILE, 'w'), indent=2, ensure_ascii=False)
    except Exception as e:
        print(f'[lifecycle] wuqu sync err: {e}')


# ═══════════════════════════════════════════════════════
# 模块1：Entry Gate + 自动开仓
# ═══════════════════════════════════════════════════════

def entry_gate(signal: dict, acct: dict, db: list) -> tuple:
    """五重门控，返回 (pass: bool, reason: str, qty: str)"""
    sym     = signal.get('symbol', '')
    score   = float(signal.get('score_final', signal.get('score', 0)) or 0)
    dr      = signal.get('direction', signal.get('signal_dir', ''))
    regime  = signal.get('regime', '')
    rr      = float(signal.get('rr1', signal.get('rr', 0)) or 0)
    sl_pct  = float(signal.get('sl_pct', SL_PCT_DEFAULT) or SL_PCT_DEFAULT)
    nav     = acct.get('nav', 0)
    avail   = acct.get('avail', 0)
    n_pos   = acct.get('n_positions', 0)

    # 门1：评分
    if score < AUTO_SCORE_MIN:
        return False, f'score={score}<{AUTO_SCORE_MIN}', ''

    # 门2：死穴
    regime_dir = f'{regime}_{dr}'
    if regime_dir in HARD_BLOCK:
        return False, f'HARD_BLOCK: {regime_dir}', ''

    # 门3：持仓数上限
    active_syms = {r['symbol'] for r in db if r.get('status') != 'CLOSED'}
    if len(active_syms) >= MAX_POSITIONS:
        return False, f'持仓数{len(active_syms)}≥{MAX_POSITIONS}', ''

    # 门4：该标的已有仓位
    if sym in active_syms:
        return False, f'{sym}已有持仓', ''

    # 门5：计算仓位+余额
    pos_usdt = nav * MAX_NAV_PER_POS
    if pos_usdt < 4.5:
        return False, f'仓位${pos_usdt:.1f}<$4.5 min', ''
    if avail < pos_usdt * 0.3:
        return False, f'可用余额${avail:.1f}不足', ''

    # 计算数量
    cp = get_price(sym)
    if cp <= 0:
        return False, '无法获取价格', ''
    raw_qty = (pos_usdt * DEFAULT_LEV) / cp

    # 获取步长
    try:
        ei = requests.get('https://fapi.binance.com/fapi/v1/exchangeInfo', timeout=8).json()
        step = 1.0
        for s in ei.get('symbols', []):
            if s['symbol'] == sym:
                for flt in s.get('filters', []):
                    if flt['filterType'] == 'LOT_SIZE':
                        step = float(flt['stepSize'])
                        break
                break
        decimals = max(0, round(-math.log10(step)))
        qty = str(round(math.floor(raw_qty / step) * step, decimals))
    except Exception:
        qty = str(round(raw_qty, 2))

    if float(qty) <= 0:
        return False, f'数量计算为0 (pos=${pos_usdt:.1f} cp={cp})', ''

    return True, 'PASS', qty


def auto_open(signal: dict) -> dict:
    """自动开仓主函数"""
    sym    = signal.get('symbol', '')
    dr     = signal.get('direction', signal.get('signal_dir', 'LONG'))
    elo    = float(signal.get('entry_lo', 0) or 0)
    sl     = float(signal.get('stop_loss', 0) or 0)
    tp1    = float(signal.get('tp1', 0) or 0)
    tp2    = float(signal.get('tp2', 0) or tp1 * 1.03 if tp1 else 0)
    score  = float(signal.get('score_final', signal.get('score', 0)) or 0)
    regime = signal.get('regime', '')
    sl_pct = float(signal.get('sl_pct', SL_PCT_DEFAULT) or SL_PCT_DEFAULT)

    acct = get_account()
    db   = load_db()

    ok, reason, qty = entry_gate(signal, acct, db)
    if not ok:
        return dict(ok=False, reason=reason, sym=sym)

    # 下单
    side = 'BUY' if dr == 'LONG' else 'SELL'
    order = place_market_order(sym, side, qty)
    if not order.get('ok'):
        return dict(ok=False, reason=f'下单失败: {order.get("err")}', sym=sym)

    # 实际成交价
    cp     = get_price(sym)
    entry  = order.get('avgPrice') or cp
    if entry <= 0:
        entry = elo if elo > 0 else cp

    # 重算SL/TP（基于真实成交价）
    if sl <= 0:
        sl  = entry * (1 - sl_pct / 100) if dr == 'LONG' else entry * (1 + sl_pct / 100)
    if tp1 <= 0:
        tp1 = entry * (1 + sl_pct * TP1_RR / 100) if dr == 'LONG' else entry * (1 - sl_pct * TP1_RR / 100)
    if tp2 <= 0:
        tp2 = entry * (1 + sl_pct * TP2_RR / 100) if dr == 'LONG' else entry * (1 - sl_pct * TP2_RR / 100)

    notional = float(qty) * entry

    # 挂止损algo单
    stop_side = 'SELL' if dr == 'LONG' else 'BUY'
    stop_result = place_algo_stop(sym, stop_side, qty, str(round(sl, 6)))

    # 写入lifecycle_db
    now = time.time()
    record = dict(
        id         = hashlib.md5(f'{sym}{now}'.encode()).hexdigest()[:8],
        symbol     = sym,
        direction  = dr,
        status     = 'OPEN',
        score      = score,
        regime     = regime,
        size       = float(qty),
        entry_price= round(entry, 6),
        sl         = round(sl, 6),
        tp1        = round(tp1, 6),
        tp2        = round(tp2, 6),
        sl_pct     = sl_pct,
        lev        = DEFAULT_LEV,
        notional   = round(notional, 2),
        order_id   = order.get('orderId'),
        algo_sl_id = stop_result.get('algoId'),
        opened_at  = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        opened_ts  = now,
        tp1_closed = False,
        closed_at  = None,
        realized_pnl = 0.0,
    )
    save_record(record)
    sync_wuqu(record)

    # 推送开仓通知
    msg = (
        f'🚀 梵天自动开仓\n'
        f'标的: {sym}  方向: {dr}  {DEFAULT_LEV}x\n'
        f'成交价: {entry:.4g}  数量: {qty}\n'
        f'名义: ${notional:.1f}  score={score:.0f}  {regime}\n'
        f'止损: {sl:.4g}(-{sl_pct:.1f}%)  TP1: {tp1:.4g}  TP2: {tp2:.4g}\n'
        f'止损单: {"✅algoId="+str(stop_result.get("algoId")) if stop_result.get("ok") else "⚠️止损单失败"}'
    )
    push(msg)
    print(f'[lifecycle] 开仓成功 {sym} {dr} qty={qty} entry={entry:.4g}')

    # 同步推送公域跟单卡片 [2026-07-17 苏摩111授权]
    try:
        from copy_signal_pusher import push_copy_signal
        push_copy_signal(record)
    except Exception as _ce:
        print(f'[lifecycle] copy pusher warn: {_ce}')

    return dict(ok=True, sym=sym, record=record)


# ═══════════════════════════════════════════════════════
# 模块2：持仓监控 + 自动平仓
# ═══════════════════════════════════════════════════════

def monitor_and_execute():
    """核心监控：每5min调用，感知SL/TP并自动执行"""
    db  = load_db()
    now = time.time()
    actions = []

    for rec in db:
        if rec.get('status') == 'CLOSED':
            continue

        sym    = rec['symbol']
        dr     = rec['direction']
        entry  = float(rec.get('entry_price', 0))
        sl     = float(rec.get('sl', 0))
        tp1    = float(rec.get('tp1', 0))
        tp2    = float(rec.get('tp2', 0))
        size   = float(rec.get('size', 0))
        tp1_closed = rec.get('tp1_closed', False)

        cp = get_price(sym)
        if cp <= 0 or entry <= 0:
            continue

        pnl_pct = (cp - entry) / entry * 100 if dr == 'LONG' else (entry - cp) / entry * 100
        sl_hit  = (cp <= sl) if dr == 'LONG' and sl > 0 else (cp >= sl if sl > 0 else False)
        tp1_hit = (cp >= tp1) if dr == 'LONG' and tp1 > 0 else (cp <= tp1 if tp1 > 0 else False)
        tp2_hit = (cp >= tp2) if dr == 'LONG' and tp2 > 0 else (cp <= tp2 if tp2 > 0 else False)

        close_side = 'SELL' if dr == 'LONG' else 'BUY'

        # ── SL触发 → 全量市价平仓 ──────────────────────────
        if sl_hit:
            order = place_market_order(sym, close_side, str(size), reduce_only=True)
            pnl_usdt = (cp - entry) * size if dr == 'LONG' else (entry - cp) * size
            rec.update(status='CLOSED', closed_at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                       closed_ts=now, close_reason='SL', realized_pnl=round(pnl_usdt, 3),
                       close_price=cp)
            sync_wuqu({**rec, 'status': 'CLOSED', 'exit_price': cp})
            msg = (f'🚨 梵天自动止损 {sym}\n'
                   f'方向: {dr}  入场: {entry:.4g}  平仓: {cp:.4g}\n'
                   f'盈亏: {pnl_pct:+.2f}%  ({pnl_usdt:+.2f}U)\n'
                   f'下单: {"✅成交" if order.get("ok") else "❌失败: "+order.get("err","")[:60]}')
            push(msg)
            actions.append(f'SL {sym} {pnl_pct:+.1f}%')
            # 公域平仓提醒
            try:
                from copy_signal_pusher import push_copy_close
                push_copy_close(rec, 'SL', cp, pnl_pct)
            except Exception: pass

        # ── TP2触发 → 剩余全量平仓 ─────────────────────────
        elif tp2_hit and tp1_closed:
            remain = round(size * 0.5, 8)
            order = place_market_order(sym, close_side, str(remain), reduce_only=True)
            pnl_usdt = (cp - entry) * remain if dr == 'LONG' else (entry - cp) * remain
            rec.update(status='CLOSED', closed_at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                       closed_ts=now, close_reason='TP2', realized_pnl=round(rec.get('realized_pnl', 0) + pnl_usdt, 3),
                       close_price=cp)
            msg = (f'🎯 梵天自动止盈TP2 {sym}\n'
                   f'方向: {dr}  入场: {entry:.4g}  TP2: {cp:.4g}\n'
                   f'盈亏: {pnl_pct:+.2f}%  本次: ({pnl_usdt:+.2f}U)\n'
                   f'下单: {"✅" if order.get("ok") else "❌"}')
            push(msg)
            actions.append(f'TP2 {sym} {pnl_pct:+.1f}%')
            # 公域平仓提醒
            try:
                from copy_signal_pusher import push_copy_close
                push_copy_close(rec, 'TP2', cp, pnl_pct)
            except Exception: pass

        # ── TP1触发且未做过 → 50%止盈 + 保本止损 ──────────────
        elif tp1_hit and not tp1_closed:
            close_qty = round(size * TP1_CLOSE_PCT, 8)
            # 精度修正
            try:
                ei = requests.get(f'https://fapi.binance.com/fapi/v1/exchangeInfo', timeout=6).json()
                for s in ei.get('symbols', []):
                    if s['symbol'] == sym:
                        for flt in s.get('filters', []):
                            if flt['filterType'] == 'LOT_SIZE':
                                step = float(flt['stepSize'])
                                decimals = max(0, round(-math.log10(step))) if step < 1 else 0
                                close_qty = round(math.floor(close_qty / step) * step, decimals)
                                if close_qty < step:
                                    close_qty = size  # 不足一手，全量
                                break
                        break
            except Exception:
                pass

            order = place_market_order(sym, close_side, str(close_qty), reduce_only=True)
            pnl_usdt = (cp - entry) * close_qty if dr == 'LONG' else (entry - cp) * close_qty

            # 保本止损
            breakeven = round(entry * 1.001 if dr == 'LONG' else entry * 0.999, 6)
            remain    = round(size - close_qty, 8)
            stop_res  = place_algo_stop(sym, close_side, str(remain), str(breakeven)) if remain > 0 else dict(ok=False)

            rec.update(tp1_closed=True, status='PARTIAL',
                       sl=breakeven,
                       realized_pnl=round(rec.get('realized_pnl', 0) + pnl_usdt, 3),
                       tp1_close_price=cp, tp1_close_qty=close_qty,
                       algo_sl_id=stop_res.get('algoId') or rec.get('algo_sl_id'))
            msg = (f'🎯 梵天自动止盈TP1 {sym}\n'
                   f'方向: {dr}  入场: {entry:.4g}  TP1: {cp:.4g}\n'
                   f'止盈: {close_qty}手  盈亏: {pnl_pct:+.2f}%  ({pnl_usdt:+.2f}U)\n'
                   f'剩余: {remain}手  保本止损→{breakeven:.4g}\n'
                   f'止损单: {"✅algoId="+str(stop_res.get("algoId")) if stop_res.get("ok") else "⚠️止损单失败"}')
            push(msg)
            actions.append(f'TP1 {sym} {pnl_pct:+.1f}%')
            # 公域平仓提醒
            try:
                from copy_signal_pusher import push_copy_close
                push_copy_close(rec, 'TP1', cp, pnl_pct)
            except Exception: pass

    # 更新DB
    update_db(db)

    if actions:
        print(f'[lifecycle] 执行了 {len(actions)} 笔: {actions}')
    else:
        print('HEARTBEAT_OK')

    return actions


# ═══════════════════════════════════════════════════════
# 模块3：信号扫描 → 自动开仓
# ═══════════════════════════════════════════════════════

def scan_and_open():
    """扫描最新信号，触发自动开仓"""
    opened = []
    now = time.time()
    cutoff = now - 3600  # 只处理1H内的新信号

    # 已处理信号集合
    executed_file = os.path.join(DIR, 'data', 'lifecycle_executed_signals.json')
    executed = set()
    try:
        executed = set(json.load(open(executed_file)))
    except Exception:
        pass

    # 读信号
    signals = {}
    try:
        with open(SIGNAL_LOG) as f:
            for line in f:
                try:
                    s = json.loads(line.strip())
                    if not (s.get('valid') or s.get('valid_signal')):
                        continue
                    ts = float(s.get('ts', s.get('timestamp', 0)) or 0)
                    if ts < cutoff:
                        continue
                    sym = s.get('symbol', '')
                    sc  = float(s.get('score_final', s.get('score', 0)) or 0)
                    if sc < AUTO_SCORE_MIN:
                        continue
                    sig_id = f"{sym}_{int(ts)}"
                    if sig_id in executed:
                        continue
                    prev = float(signals[sym].get('score_final', signals[sym].get('score', 0)) or 0) if sym in signals else -1
                    if sc > prev:
                        signals[sym] = {**s, '_sig_id': sig_id}
                except Exception:
                    pass
    except Exception:
        pass

    for sym, sig in signals.items():
        result = auto_open(sig)
        if result.get('ok'):
            opened.append(sym)
            executed.add(sig['_sig_id'])

    # 保存已处理集合
    with open(executed_file, 'w') as f:
        json.dump(list(executed)[-500:], f)  # 保留最近500条

    return opened


# ═══════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════

def main(mode: str = 'monitor'):
    """
    mode=monitor : 只做持仓监控+自动平仓（每5min cron）
    mode=entry   : 扫描信号+自动开仓（每10min cron，与signal_change_detector协同）
    mode=full    : monitor + entry 全量运行
    """
    if mode in ('monitor', 'full'):
        actions = monitor_and_execute()

    if mode in ('entry', 'full'):
        opened = scan_and_open()
        if opened:
            print(f'[lifecycle] 开仓: {opened}')

    return True


if __name__ == '__main__':
    import sys
    mode = sys.argv[1] if len(sys.argv) > 1 else 'monitor'
    main(mode)
