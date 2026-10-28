#!/usr/bin/env python3
"""
account_gateway.py — 梵天账户感知网关
[封印 2026-10-27 苏摩111] Binance Agent OS Phase 1~3 落地

三阶段架构：
  Phase 1: 只读感知  — 余额/持仓/NAV写入account_state.json（今日）
  Phase 2: 草案确认  — S级信号生成订单草案→推送→苏摩111确认→执行（本周）
  Phase 3: papi自动  — papi权限到位后OCO全自动（待审批）

调用方式：
  python3 scripts/account_gateway.py --mode sense    # Phase 1: 感知
  python3 scripts/account_gateway.py --mode draft    # Phase 2: 草案
  python3 scripts/account_gateway.py --mode status   # 查看当前状态
"""
import sys, os, json, time, subprocess
from pathlib import Path

BASE       = Path(__file__).parent.parent
STATE_PATH = BASE / 'data' / 'account_state.json'
DRAFT_PATH = BASE / 'data' / 'order_draft.json'

# ── 工具函数 ──────────────────────────────────────────────────────────────

def _cli(*args, timeout=10) -> dict | list | None:
    """调用binance-cli，返回解析后的JSON"""
    try:
        r = subprocess.run(
            ['binance-cli'] + list(args),
            capture_output=True, text=True, timeout=timeout
        )
        if r.stdout.strip():
            return json.loads(r.stdout)
    except Exception as e:
        print(f'[account_gateway] binance-cli error: {e}', file=sys.stderr)
    return None

def _push(msg: str, priority: str = 'P2'):
    try:
        sys.path.insert(0, str(BASE / 'scripts'))
        sys.path.insert(0, str(BASE / 'brahma_brain'))
        from push_hub import push_jarvis
        push_jarvis(msg, priority=priority)
    except Exception as e:
        print(f'[account_gateway] 推送失败: {e}', file=sys.stderr)

# ══════════════════════════════════════════════════════════════════════════
# Phase 1: 账户感知（只读，零风险）
# ══════════════════════════════════════════════════════════════════════════

def sense_account() -> dict:
    """
    拉取账户状态，写入 data/account_state.json
    数据来源：binance-cli（OpenClaw已授权）
    """
    state = {
        'ts': time.time(),
        'dt': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'source': 'binance-cli',
    }

    # ── 1. futures-usds 余额（普通合约账户）──
    bal = _cli('futures-usds', 'futures-account-balance-v3')
    if isinstance(bal, list):
        # [修复 2026-10-28 苏摩111] 兼容资产名'U'（Portfolio Margin账户）
        usdt = next((b for b in bal if b.get('asset') in ('USDT','U')), {})
        state['futures_usdt_balance']   = float(usdt.get('balance', 0))
        state['futures_usdt_available'] = float(usdt.get('availableBalance', 0))

    # ── 2. futures-usds 账户总览 ──
    acct = _cli('futures-usds', 'account-information-v3')
    if isinstance(acct, dict):
        state['total_wallet_balance']    = float(acct.get('totalWalletBalance', 0))
        state['total_unrealized_profit'] = float(acct.get('totalUnrealizedProfit', 0))
        state['total_margin_balance']    = float(acct.get('totalMarginBalance', 0))
        # NAV估算
        state['nav_usdt'] = state['total_wallet_balance'] + state['total_unrealized_profit']

        # 活跃持仓
        positions = [p for p in acct.get('positions', [])
                     if float(p.get('positionAmt', 0)) != 0]
        state['positions'] = [
            {
                'symbol':    p['symbol'],
                'side':      'LONG' if float(p['positionAmt']) > 0 else 'SHORT',
                'amt':       float(p['positionAmt']),
                'entry_px':  float(p.get('entryPrice', 0)),
                'mark_px':   float(p.get('markPrice', 0)),
                'pnl':       float(p.get('unrealizedProfit', 0)),
                'leverage':  int(p.get('leverage', 1)),
                'notional':  abs(float(p.get('notional', 0))),
            }
            for p in positions
        ]
        state['position_count'] = len(positions)

    # ── 3. Portfolio Margin 账户（papi，可能无权限）──
    pm_acct = _cli('derivatives-portfolio-margin', 'account-information')
    if isinstance(pm_acct, dict) and 'uniMMR' in pm_acct:
        state['pm_equity']    = float(pm_acct.get('actualEquity', 0))
        state['pm_uni_mmr']   = float(pm_acct.get('uniMMR', 0))
        state['pm_available'] = float(pm_acct.get('availableBalance', 0))
        state['has_pm_access'] = True
        # PM持仓
        pm_pos = _cli('derivatives-portfolio-margin', 'query-um-position-information')
        if isinstance(pm_pos, list):
            pm_active = [p for p in pm_pos if float(p.get('positionAmt', 0)) != 0]
            state['pm_positions'] = [
                {
                    'symbol':   p['symbol'],
                    'side':     'LONG' if float(p['positionAmt']) > 0 else 'SHORT',
                    'amt':      float(p['positionAmt']),
                    'entry_px': float(p.get('entryPrice', 0)),
                    'pnl':      float(p.get('unrealizedProfit', 0)),
                }
                for p in pm_active
            ]
    else:
        state['has_pm_access'] = False
        state['pm_positions']  = []

    # ── 4. 写入文件 ──
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    print(f"[account_gateway] 账户状态已更新 NAV={state.get('nav_usdt',0):.2f} USDT "
          f"持仓={state.get('position_count',0)}个 PM={state.get('has_pm_access')}")
    return state

# ══════════════════════════════════════════════════════════════════════════
# Phase 2: 订单草案生成（S级信号触发）
# ══════════════════════════════════════════════════════════════════════════

def generate_draft(symbol: str, direction: str,
                   entry_lo: float, entry_hi: float,
                   sl: float, tp1: float, tp2: float,
                   rr: float, confluence_score: float) -> dict:
    """
    根据梵天VIP信号生成订单草案，推送给苏摩确认
    苏摩回复"111"后调用 execute_draft()
    """
    # 读取当前账户状态
    acct = {}
    if STATE_PATH.exists():
        try:
            acct = json.loads(STATE_PATH.read_text())
        except Exception:
            pass

    nav    = acct.get('nav_usdt', acct.get('pm_equity', 0))
    pos_n  = acct.get('position_count', 0)

    # 仓位计算（梵天铁律：SL<1%→5%NAV / 1~1.5%→2% / 1.5~2%→3%）
    sl_pct = abs(entry_hi - sl) / entry_hi * 100 if entry_hi > 0 else 2.0
    if sl_pct < 1.0:
        pos_pct = 5.0
    elif sl_pct < 1.5:
        pos_pct = 2.0
    else:
        pos_pct = 3.0

    notional  = nav * pos_pct / 100 if nav > 0 else 0
    entry_mid = (entry_lo + entry_hi) / 2

    draft = {
        'ts':              time.time(),
        'dt':              time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'symbol':          symbol + 'USDT',
        'direction':       direction,
        'entry_lo':        entry_lo,
        'entry_hi':        entry_hi,
        'entry_mid':       entry_mid,
        'sl':              sl,
        'tp1':             tp1,
        'tp2':             tp2,
        'rr':              rr,
        'sl_pct':          round(sl_pct, 2),
        'pos_pct':         pos_pct,
        'notional_usdt':   round(notional, 2),
        'nav_at_draft':    nav,
        'confluence':      confluence_score,
        'status':          'PENDING_CONFIRM',  # 等苏摩111
        'existing_positions': pos_n,
    }

    # 写入草案文件
    DRAFT_PATH.write_text(json.dumps(draft, ensure_ascii=False, indent=2))

    # 推送给苏摩
    side_emoji = '🔴' if direction == 'SHORT' else '🟢'
    msg = (
        f"⚡ S级信号 | {symbol} {direction}  score={confluence_score:.0f}\n"
        f"\n"
        f"{side_emoji} {direction}｜挂单区 ${entry_lo:,.1f}~${entry_hi:,.1f}\n"
        f"止损 ${sl:,.1f}｜目标 ${tp1:,.1f}→${tp2:,.1f}\n"
        f"RR={rr:.1f}  SL距离={sl_pct:.1f}%  仓位={pos_pct:.0f}%NAV\n"
        f"名义价值≈${notional:.0f} USDT\n"
        f"\n"
        f"回复 111 确认执行｜忽略此消息跳过"
    )
    _push(msg, priority='P1')
    print(f'[account_gateway] 草案已推送: {symbol} {direction} notional=${notional:.0f}')
    return draft

# ══════════════════════════════════════════════════════════════════════════
# Phase 2: 草案执行（苏摩111后调用）
# ══════════════════════════════════════════════════════════════════════════

def execute_draft() -> dict:
    """
    读取 order_draft.json，执行待确认的订单
    通过 binance-cli futures-usds new-um-order 下单
    """
    if not DRAFT_PATH.exists():
        print('[account_gateway] 无待执行草案')
        return {'status': 'NO_DRAFT'}

    draft = json.loads(DRAFT_PATH.read_text())
    if draft.get('status') != 'PENDING_CONFIRM':
        print(f"[account_gateway] 草案状态={draft.get('status')}，无需执行")
        return draft

    symbol    = draft['symbol']
    direction = draft['direction']
    entry_lo  = draft['entry_lo']
    entry_hi  = draft['entry_hi']
    sl        = draft['sl']
    tp1       = draft['tp1']
    notional  = draft['notional_usdt']
    entry_mid = draft['entry_mid']

    # 计算数量（以entry_mid估算）
    if entry_mid > 0 and notional > 0:
        qty = round(notional / entry_mid, 3)
    else:
        print('[account_gateway] 无法计算数量：entry_mid或notional为0')
        return {'status': 'CALC_ERROR'}

    side = 'SELL' if direction == 'SHORT' else 'BUY'

    print(f'[account_gateway] 执行: {symbol} {side} qty={qty} price={entry_mid:.2f}')

    # ── 入场限价单 ──
    order_result = _cli(
        'futures-usds', 'new-um-order',
        '--symbol', symbol,
        '--side', side,
        '--type', 'LIMIT',
        '--quantity', str(qty),
        '--price', str(round(entry_mid, 2)),
        '--time-in-force', 'GTC',
        timeout=15
    )

    if not order_result or 'orderId' not in (order_result or {}):
        err = str(order_result)[:100] if order_result else '无返回'
        print(f'[account_gateway] 下单失败: {err}')
        draft['status']   = 'FAILED'
        draft['error']    = err
        DRAFT_PATH.write_text(json.dumps(draft, ensure_ascii=False, indent=2))
        _push(f'❌ 梵天下单失败\n{symbol} {direction}\n{err}', priority='P1')
        return draft

    order_id = order_result['orderId']
    print(f'[account_gateway] 入场单成功: orderId={order_id}')

    # ── 软止损监控（papi不可用时的替代方案）──
    # 止损由 position_sl_state.json + cron软止损监控执行

    # ── 更新草案状态 ──
    draft['status']        = 'EXECUTED'
    draft['order_id']      = order_id
    draft['executed_at']   = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    draft['executed_qty']  = qty
    draft['executed_price']= entry_mid
    DRAFT_PATH.write_text(json.dumps(draft, ensure_ascii=False, indent=2))

    # ── 写入prediction_ledger ──
    try:
        sys.path.insert(0, str(BASE / 'scripts'))
        from prediction_ledger import record_prediction
        record_prediction(
            symbol=symbol,
            direction=direction,
            entry_lo=entry_lo,
            entry_hi=entry_hi,
            sl=sl,
            tp1=tp1,
            tp2=draft.get('tp2', tp1),
            rr=draft.get('rr', 1.5),
            regime=draft.get('regime', ''),
        )
        print('[account_gateway] prediction_ledger记录✅')
    except Exception as e:
        print(f'[account_gateway] prediction记录失败: {e}')

    _push(
        f"✅ 梵天执行确认\n"
        f"{symbol} {direction}\n"
        f"入场=${entry_mid:,.2f}  qty={qty}\n"
        f"orderId={order_id}\n"
        f"SL=${sl:,.2f}  TP1=${tp1:,.2f}",
        priority='P1'
    )
    return draft

# ══════════════════════════════════════════════════════════════════════════
# Phase 3: papi OCO预备（权限到位后启用）
# ══════════════════════════════════════════════════════════════════════════

def execute_oco_papi(symbol: str, direction: str,
                     qty: float, entry_price: float,
                     sl: float, tp1: float) -> dict:
    """
    Phase 3: 通过 /papi/v1 下OCO单（SL+TP一次设置）
    需要 Portfolio Margin papi 权限（已申请，等审批）
    """
    # 检查papi权限
    acct = {}
    if STATE_PATH.exists():
        acct = json.loads(STATE_PATH.read_text())
    if not acct.get('has_pm_access'):
        print('[account_gateway] papi权限未就绪，跳过OCO')
        return {'status': 'PAPI_NOT_READY'}

    # papi OCO: 通过 binance-cli request 直接调用
    side      = 'SELL' if direction == 'SHORT' else 'BUY'
    close_side= 'BUY' if direction == 'SHORT' else 'SELL'

    # 1. 入场单
    entry = _cli('derivatives-portfolio-margin', 'new-um-order',
                 '--symbol', symbol + 'USDT',
                 '--side', side,
                 '--type', 'LIMIT',
                 '--quantity', str(qty),
                 '--price', str(round(entry_price, 2)),
                 '--time-in-force', 'GTC', timeout=15)

    if not entry or 'orderId' not in (entry or {}):
        return {'status': 'ENTRY_FAILED', 'error': str(entry)[:100]}

    # 2. 止损单（STOP_MARKET）
    sl_order = _cli('derivatives-portfolio-margin', 'new-um-conditional-order',
                    '--symbol', symbol + 'USDT',
                    '--side', close_side,
                    '--strategy-type', 'STOP_MARKET',
                    '--quantity', str(qty),
                    '--stop-price', str(round(sl, 2)),
                    '--reduce-only', 'true', timeout=15)

    # 3. 止盈单（TAKE_PROFIT_MARKET）
    tp_order = _cli('derivatives-portfolio-margin', 'new-um-conditional-order',
                    '--symbol', symbol + 'USDT',
                    '--side', close_side,
                    '--strategy-type', 'TAKE_PROFIT_MARKET',
                    '--quantity', str(qty),
                    '--stop-price', str(round(tp1, 2)),
                    '--reduce-only', 'true', timeout=15)

    result = {
        'status':    'OCO_PLACED',
        'entry_id':  entry.get('orderId'),
        'sl_id':     (sl_order or {}).get('strategyId'),
        'tp_id':     (tp_order or {}).get('strategyId'),
        'symbol':    symbol,
        'direction': direction,
        'qty':       qty,
    }
    print(f'[account_gateway] OCO下单完成: {result}')
    return result

# ══════════════════════════════════════════════════════════════════════════
# 状态查询
# ══════════════════════════════════════════════════════════════════════════

def get_status() -> str:
    lines = ['🏛️ 梵天账户网关状态']
    if STATE_PATH.exists():
        s = json.loads(STATE_PATH.read_text())
        age = (time.time() - s.get('ts', 0)) / 60
        nav = s.get('nav_usdt', s.get('pm_equity', 0))
        pos = s.get('positions', []) + s.get('pm_positions', [])
        lines += [
            f"  账户更新: {age:.0f}min前",
            f"  NAV: ${nav:,.2f} USDT",
            f"  PM权限: {'✅' if s.get('has_pm_access') else '❌ 待审批'}",
            f"  活跃持仓: {len(pos)}个",
        ]
        for p in pos:
            lines.append(f"    {p['symbol']} {p['side']} "
                         f"amt={p['amt']} pnl={p.get('pnl',0):+.2f}")
    else:
        lines.append('  ❌ 账户状态未初始化，运行 --mode sense')

    if DRAFT_PATH.exists():
        d = json.loads(DRAFT_PATH.read_text())
        lines += [
            f"  草案状态: {d.get('status')} {d.get('symbol')} {d.get('direction')}",
            f"  草案时间: {d.get('dt','?')}",
        ]
    return '\n'.join(lines)

# ══════════════════════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--mode', choices=['sense','draft','execute','status','oco'],
                   default='sense')
    p.add_argument('--symbol',    default='ETH')
    p.add_argument('--direction', default='SHORT')
    p.add_argument('--entry-lo',  type=float, default=0)
    p.add_argument('--entry-hi',  type=float, default=0)
    p.add_argument('--sl',        type=float, default=0)
    p.add_argument('--tp1',       type=float, default=0)
    p.add_argument('--tp2',       type=float, default=0)
    p.add_argument('--rr',        type=float, default=1.5)
    p.add_argument('--score',     type=float, default=165)
    args = p.parse_args()

    if args.mode == 'sense':
        s = sense_account()
        print(get_status())
    elif args.mode == 'draft':
        generate_draft(args.symbol, args.direction,
                       args.entry_lo, args.entry_hi,
                       args.sl, args.tp1, args.tp2,
                       args.rr, args.score)
    elif args.mode == 'execute':
        result = execute_draft()
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.mode == 'status':
        print(get_status())
    elif args.mode == 'oco':
        print('[Phase 3] papi OCO预备就绪，等权限审批')
