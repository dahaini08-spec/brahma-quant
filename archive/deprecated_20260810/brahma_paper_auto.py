#!/usr/bin/env python3
"""
scripts/brahma_paper_auto.py — 梵天纸仓自动验证器
设计院六方联合 P-PAPER | 苏摩111批准 2026-07-11

功能:
  1. 读取 live_signal_log.jsonl 中 valid=True 的信号
  2. 用 brahma_v6.paper.paper_forward.PaperExecutor 模拟执行
  3. 信号到期后用当前价格结算，记录 net_pnl
  4. 每次运行输出验收进度（目标300笔）
  5. 推送 P1 以上信号的纸仓报告到 Jarvis

运行方式:
  python3 scripts/brahma_paper_auto.py          # 正常运行
  python3 scripts/brahma_paper_auto.py --report # 输出验收报告

设计:
  - 纸仓数据存储: data/paper_forward/
  - 与真实仓位完全隔离，不触发任何真实下单
  - 使用真实成本模型（手续费+滑点+资金费）
"""
import sys, os, json, time, argparse
from pathlib import Path
from datetime import datetime, timezone, timedelta

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'brahma_brain'))
sys.path.insert(0, str(BASE / 'scripts'))

SIG_LOG    = BASE / 'data' / 'live_signal_log.jsonl'
PAPER_DIR  = BASE / 'data' / 'paper_forward'
PAPER_DIR.mkdir(parents=True, exist_ok=True)

EXEC_LOG   = PAPER_DIR / 'paper_exec_log.jsonl'   # 已建仓的纸仓
SETTLE_LOG = PAPER_DIR / 'paper_settle_log.jsonl'  # 已结算的纸仓
STATE_FILE = PAPER_DIR / 'paper_state.json'         # 组合状态

# ── 参数 ───────────────────────────────────────────────────────
MIN_SCORE       = 80   # 纸仓低门槛，积累样本（真实执行仍需≥120）      # 最低score才做纸仓
MAX_PAPER_OPEN  = 10       # 最大同时持仓纸仓数
SETTLE_HOURS    = 12       # 12H后自动结算（取当时市价）
FAPI_BASE       = os.environ.get('BINANCE_FAPI_BASE', 'https://fapi.binance.com')


def _get_price(symbol: str) -> float:
    """获取当前标记价格"""
    try:
        import requests
        r = requests.get(f'{FAPI_BASE}/fapi/v1/ticker/price',
                         params={'symbol': symbol}, timeout=5)
        return float(r.json()['price'])
    except Exception:
        return 0.0


def _load_open_trades() -> list:
    if not EXEC_LOG.exists():
        return []
    trades = []
    for l in EXEC_LOG.read_text().split('\n'):
        if l.strip():
            try: trades.append(json.loads(l))
            except: pass
    return [t for t in trades if not t.get('settled')]


def _load_all_settled() -> list:
    if not SETTLE_LOG.exists():
        return []
    trades = []
    for l in SETTLE_LOG.read_text().split('\n'):
        if l.strip():
            try: trades.append(json.loads(l))
            except: pass
    return trades


def _save_trade(trade: dict):
    with open(EXEC_LOG, 'a') as f:
        f.write(json.dumps(trade, ensure_ascii=False) + '\n')


def _save_settled(trade: dict):
    with open(SETTLE_LOG, 'a') as f:
        f.write(json.dumps(trade, ensure_ascii=False) + '\n')


def _load_signals(min_score: float = MIN_SCORE) -> list:
    """加载有效信号"""
    if not SIG_LOG.exists():
        return []
    sigs = []
    cutoff = time.time() - 48 * 3600  # 最近48H
    for l in SIG_LOG.read_text().split('\n'):
        if not l.strip():
            continue
        try:
            s = json.loads(l)
            if (s.get('valid') and
                float(s.get('score', 0) or 0) >= min_score and
                s.get('ts', 0) >= cutoff):
                sigs.append(s)
        except:
            pass
    return sigs


def run_paper(dry: bool = False) -> dict:
    """
    主循环: 建仓新信号 + 结算到期仓位
    """
    now = time.time()
    now_dt = datetime.now(timezone.utc)
    results = {'new_entries': [], 'settled': [], 'errors': []}

    # ── 1. 加载现有纸仓 ─────────────────────────────────────────
    open_trades = _load_open_trades()
    open_syms   = {t['symbol'] for t in open_trades}
    print(f"现有纸仓: {len(open_trades)}笔 开仓  {len(open_syms)}个品种")

    # ── 2. 结算到期仓位 ─────────────────────────────────────────
    for trade in open_trades:
        entry_time = trade.get('entry_ts', 0)
        if now - entry_time >= SETTLE_HOURS * 3600:
            symbol     = trade['symbol']
            direction  = trade['direction']
            entry_px   = trade['entry_price']
            size_nav   = trade['size_nav_pct']
            holding_h  = (now - entry_time) / 3600

            exit_px = _get_price(symbol)
            if exit_px <= 0:
                results['errors'].append(f'{symbol}: 无法获取价格')
                continue

            # 计算净PnL
            from brahma_v6.paper.paper_forward import _compute_net_pnl  # noqa
            cost = _compute_net_pnl(symbol, direction, entry_px, exit_px,
                                    qty=1.0, leverage=3,
                                    holding_hours=holding_h)
            # 按 size_nav 比例换算
            nav_factor = size_nav / 100.0
            net_pnl_pct = cost['net_pnl'] / entry_px * 100 if entry_px else 0

            settled = {
                **trade,
                'settled':    True,
                'exit_price': round(exit_px, 6),
                'exit_ts':    now,
                'holding_h':  round(holding_h, 1),
                'net_pnl_pct':round(net_pnl_pct, 3),
                'net_pnl_u':  round(cost['net_pnl'] * nav_factor, 4),
                'fee_u':      round(cost['fee'] * nav_factor, 4),
                'slip_u':     round(cost['slippage'] * nav_factor, 4),
                'settle_dt':  now_dt.isoformat(),
                'win':        net_pnl_pct > 0,
            }
            if not dry:
                _save_settled(settled)
            results['settled'].append(settled)
            print(f"  🏁 结算 {symbol} {direction}  入{entry_px:.5g}→出{exit_px:.5g}  "
                  f"pnl={net_pnl_pct:+.2f}%  {'✅胜' if net_pnl_pct>0 else '❌败'}")

    # ── 3. 建仓新信号 ────────────────────────────────────────────
    if len(open_trades) - len(results['settled']) < MAX_PAPER_OPEN:
        signals = _load_signals(MIN_SCORE)
        for sig in signals:
            sym = sig.get('symbol','')
            if sym in open_syms:
                continue  # 已有该品种纸仓

            direction  = sig.get('direction', 'LONG')
            score      = float(sig.get('score', 0) or 0)
            regime     = sig.get('regime', '')
            entry_hi   = float(sig.get('entry_hi') or sig.get('price') or 0)
            sl         = float(sig.get('stop_loss') or sig.get('sl_price') or 0)
            tp         = float(sig.get('tp1') or sig.get('tp1_price') or 0)
            rr         = float(sig.get('rr1') or 1.0)
            nav_pct    = float(sig.get('nav_pct') or 0.05) * 100  # 转%

            if not entry_hi:
                continue
            # SL为空时按体制默认值计算
            if not sl and entry_hi:
                _sl_pct = 0.02  # 默认2%
                if regime == 'BEAR_TREND':   _sl_pct = 0.020
                elif 'CHOP' in regime:       _sl_pct = 0.025
                elif 'BULL' in regime:       _sl_pct = 0.025
                if direction in ('LONG','BUY'):
                    sl = round(entry_hi * (1 - _sl_pct), 6)
                else:
                    sl = round(entry_hi * (1 + _sl_pct), 6)

            # 用当前市价作为入场价（模拟市价单）
            cur_px = _get_price(sym)
            if cur_px <= 0:
                continue

            trade = {
                'trade_id':    f'PAPER-{int(now)}-{sym[:6]}',
                'symbol':      sym,
                'direction':   direction,
                'score':       score,
                'regime':      regime,
                'entry_price': round(cur_px, 6),
                'signal_entry':round(entry_hi, 6),
                'stop_loss':   round(sl, 6),
                'take_profit': round(tp, 6) if tp else None,
                'rr':          rr,
                'size_nav_pct':round(nav_pct, 2),
                'entry_ts':    now,
                'entry_dt':    now_dt.isoformat(),
                'settled':     False,
                'signal_ts':   sig.get('ts', 0),
            }
            if not dry:
                _save_trade(trade)
            open_syms.add(sym)
            results['new_entries'].append(trade)
            print(f"  📋 建仓 {sym} {direction}  sc={score:.0f}  @{cur_px:.5g}  "
                  f"SL={sl:.5g}  TP={tp:.5g}  size={nav_pct:.1f}%NAV")

            if len(results['new_entries']) + len(open_trades) >= MAX_PAPER_OPEN:
                break

    return results


def report() -> str:
    """生成验收进度报告"""
    settled = _load_all_settled()
    open_t  = _load_open_trades()

    if not settled:
        return "📋 纸仓尚未有结算记录"

    wins    = [t for t in settled if t.get('win')]
    pnls    = [t.get('net_pnl_pct', 0) for t in settled]
    total_p = sum(pnls)
    wr      = len(wins) / len(settled) * 100

    # 按体制分层
    regime_stats = {}
    for t in settled:
        r = t.get('regime', 'UNKNOWN')
        if r not in regime_stats:
            regime_stats[r] = {'n': 0, 'wins': 0, 'pnl': 0}
        regime_stats[r]['n']    += 1
        regime_stats[r]['wins'] += int(t.get('win', False))
        regime_stats[r]['pnl']  += t.get('net_pnl_pct', 0)

    # 最大回撤
    equity = [0.0]
    for p in pnls:
        equity.append(equity[-1] + p)
    peak = equity[0]
    max_dd = 0.0
    for eq in equity:
        if eq > peak: peak = eq
        dd = peak - eq
        if dd > max_dd: max_dd = dd

    now_dt = datetime.now(timezone.utc).strftime('%m-%d %H:%M UTC')
    progress = min(100, len(settled) / 300 * 100)

    L = []
    L.append(f'📋 纸仓验收报告  {now_dt}')
    L.append('───────────────────────')
    L.append(f'进度: {len(settled)}/300笔  {progress:.0f}%  {"✅达标" if len(settled)>=300 else "⏳进行中"}')
    L.append(f'开放中: {len(open_t)}笔')
    L.append('')
    L.append(f'总PnL:  {total_p:+.2f}%  {"✅正收益" if total_p>0 else "❌负收益"}')
    L.append(f'胜率:   {wr:.1f}%  ({len(wins)}/{len(settled)})')
    L.append(f'最大DD: {max_dd:.2f}%  {"✅<12%" if max_dd<12 else "⚠️超限"}')
    L.append(f'均值:   {total_p/len(settled):+.3f}%/笔')
    L.append('')
    L.append('体制分层:')
    for r, s in sorted(regime_stats.items(), key=lambda x: -x[1]['n'])[:5]:
        rwr = s['wins']/s['n']*100 if s['n'] else 0
        L.append(f'  {r[:12]:<14} {s["n"]:3d}笔  WR={rwr:.0f}%  PnL={s["pnl"]:+.2f}%')

    return '\n'.join(L)


def main():
    parser = argparse.ArgumentParser(description='梵天纸仓自动验证')
    parser.add_argument('--report', action='store_true', help='输出验收报告')
    parser.add_argument('--dry',    action='store_true', help='干跑不写入')
    args = parser.parse_args()

    if args.report:
        print(report())
        return

    print(f"🧪 梵天纸仓  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    results = run_paper(dry=args.dry)
    print(f"\n本次: 新建{len(results['new_entries'])}笔  结算{len(results['settled'])}笔  "
          f"错误{len(results['errors'])}笔")

    # 输出当前报告
    print()
    print(report())


if __name__ == '__main__':
    main()
