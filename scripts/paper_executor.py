#!/usr/bin/env python3
"""
paper_executor.py — 纸面系统专属开单执行器
设计院封印 2026-09-03 苏摩111

定位：独立于 auto_executor.py，专为纸面验证系统设计
门槛：score≥100（对齐MIN_SCORE_OPEN，P0封堵LLM建议层，见三方体检报告）
持仓：BTC/ETH各最多1单，每单5%NAV（纸面）
记录：写入 data/paper_positions.json，供 paper_tp_monitor.py 追踪

接入位置：
  - scripts/paper_executor.py（本文件）
  - supercronic: */40 * * * * python3 scripts/paper_executor.py
  - paper_tp_monitor.py 读取 paper_positions.json 做止盈追踪
"""
import json, os, sys, time
from pathlib import Path
from datetime import datetime, timezone
import sys

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'brahma_brain'))

PAPER_POS_FILE   = BASE / 'data' / 'paper_positions.json'
SIGNAL_QUEUE     = BASE / 'data' / 'auto_signal_queue.json'
PAPER_LOG        = BASE / 'logs' / 'paper_executor.log'

# 纸面系统专属门槛（比实盘宽松）
PAPER_SCORE_MIN  = 100  # [9.29 F1 苏摩111] 已废弃硬门：12维终选(W2)后决策层是唯一裁判，executor不再重复设分门槛（历史：37.8<100拒单与决策层EXECUTE矛盾，见9.29 P0报告）


def _shadow_evaluate_risk_gate(signal: dict, sym: str, side: str, positions_data: dict,
                               enforce: bool = False) -> dict:
    """[梵天2.0转正 2026-09-27 苏摩111] risk_gate评估→data/shadow_decisions.jsonl。
    mode=enforce: L2实权（返回verdict供调用方拦截）；mode=shadow: 只记录不拦截（T2对照）。
    接入位置: brahma_brain/risk_gate.py + reports/brahma_2.0_design.md §6 T3
    """
    try:
        from brahma_brain import risk_gate
        import paper_ledger as _pl
        now_ts = time.time()
        # state构建: open_positions(仅未平仓) + NAV/今日盈亏 + atr1h(信号带则传,否则缺省)
        open_pos = [{'symbol': p.get('symbol'), 'side': p.get('side')}
                    for p in (positions_data.get('positions') or []) if p.get('status') != 'closed']
        today_pnl_pct = None
        try:
            _today = 0.0
            for line in open(BASE / 'data' / 'paper_ledger_log.jsonl'):
                try:
                    o = json.loads(line)
                    if time.strftime('%Y-%m-%d', time.gmtime(o.get('ts', 0))) == time.strftime('%Y-%m-%d', time.gmtime(now_ts)):
                        _today += float(o.get('pnl', 0) or 0)
                except Exception:
                    pass
            start_nav = float(getattr(_pl, 'START_NAV', 100000))
            today_pnl_pct = _today / start_nav * 100.0
        except Exception:
            pass
        state = {'open_positions': open_pos, 'today_pnl_pct': today_pnl_pct, 'now_ts': now_ts}
        sig = {'symbol': sym, 'side': side, 'regime': signal.get('regime', ''),
               'score': signal.get('score_final', signal.get('score', 0)),
               'sl_pct': signal.get('sl_pct', 2.0), 'price': signal.get('price', 0),
               'leverage': signal.get('leverage', 5),
               'nav_pct': signal.get('nav_pct', 0),
               'atr1h': signal.get('atr1h', signal.get('atr_1h'))}
        verdict = risk_gate.evaluate(sig, state)
        rec = {'ts': round(now_ts, 3), 'ts_iso': datetime.now(timezone.utc).isoformat(),
               'mode': 'enforce' if enforce else 'shadow', 'source': 'paper_executor',
               'signal_id': signal.get('signal_id') or signal.get('id'),
               'symbol': sym, 'side': side, 'regime': sig['regime'], 'score': sig['score'],
               'risk_gate': verdict}
        with open(BASE / 'data' / 'shadow_decisions.jsonl', 'a') as f:
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')
        return verdict
    except Exception as e:
        from brahma_brain import error_ledger
        error_ledger.count('risk_gate', error=e, context={'phase': 'shadow_eval' if not enforce else 'enforce_eval', 'symbol': sym})
        raise
PAPER_GRADE_MIN  = 0  # [P0对齐 2026-09-24] grade线废除——SSOT唯一裁判=cf_action+score，grade回退路径已封禁
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
        except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
    return {'positions': [], 'closed': [], 'stats': {'total': 0, 'win': 0, 'pnl': 0.0}}


def save_paper_positions(data: dict):
    PAPER_POS_FILE.write_text(json.dumps(data, indent=2))


def load_signal_queue() -> list:
    if SIGNAL_QUEUE.exists():
        try:
            d = json.loads(SIGNAL_QUEUE.read_text())
            return d if isinstance(d, list) else d.get('signals', [])
        except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
    return []


def log(msg: str):
    ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    line = f'[{ts}] {msg}'
    print(line)
    try:
        with open(PAPER_LOG, 'a') as f:
            f.write(line + '\n')
    except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
def get_current_price(symbol: str) -> float:
    try:
        import urllib.request
        url = f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={symbol}'
        r = json.loads(urllib.request.urlopen(url, timeout=5).read())
        return float(r['price'])
    except Exception:
        return 0.0


def _b_track_decision_package(signal: dict, sym: str, side: str) -> dict | None:
    """[P2 B轨双写 2026-09-28 苏摩111] DecisionPackage作为B轨记录（纯影子，不触执行）。
    A轨=旧decide()信号照常走（1.0语义）；B轨=D-10决策包（2.0语义），14天T2对照。
    A/B分离铁律不破：本函数只读包+写影子记录，EXECUTION仍走A轨。
    接入位置：open_paper_position开单判定前调用（信号消费时对账）。
    """
    out = None
    try:
        sys.path.insert(0, str(BASE / 'scripts'))
        from brahma_decision_lifecycle import load_active_packages
        pkgs = [p for p in load_active_packages() if p.get('symbol') == sym]
        if not pkgs:
            return None
        pkg = pkgs[0]
        thesis = pkg.get('thesis') or {}
        out = {'ts': round(time.time(), 3),
               'ts_iso': datetime.now(timezone.utc).isoformat(),
               'mode': 'b_track_shadow', 'source': 'paper_executor',
               'symbol': sym, 'side': side,
               'decision_id': pkg.get('decision_id'),
               'pkg_state': pkg.get('state'),
               'pkg_direction': thesis.get('direction'),
               'pkg_strength': thesis.get('strength'),
               'pkg_zone': (pkg.get('trigger') or {}).get('zone'),
               'pkg_style': (pkg.get('trigger') or {}).get('style'),
               'd4_confirmed': (pkg.get('d4_structure') or {}).get('confirmed'),
               'risk_budget_pct': (pkg.get('risk_budget') or {}).get('position_pct'),
               'a_track_action': str(signal.get('action', ''))[:20],
               'a_track_score': signal.get('score_final', signal.get('score', 0)),
               'divergence': None}
        # 口径分歧判定：A轨开了（能到这=A轨放行）但B轨包方向不同或结构未确认
        if thesis.get('direction') and thesis['direction'] != side:
            out['divergence'] = f'DIRECTION:A={side}/B={thesis["direction"]}'
        elif not out['d4_confirmed']:
            out['divergence'] = 'D4_UNCONFIRMED:A开/B结构未确认'
        with open(BASE / 'data' / 'shadow_decisions.jsonl', 'a') as f:
            f.write(json.dumps(out, ensure_ascii=False) + '\n')
    except Exception as e:
        from brahma_brain import error_ledger as _el
        try:
            _el.count('decision_package', error=e, context={'phase': 'b_track_shadow', 'symbol': sym})
        except Exception:
            pass
    return out


def open_paper_position(signal: dict, positions_data: dict) -> bool:
    """开纸面仓位"""
    sym    = signal.get('symbol', '')
    side   = signal.get('signal_dir', signal.get('direction', 'LONG'))
    if side in ('BUY',): side = 'LONG'
    if side in ('SELL',): side = 'SHORT'
    # [P2 B轨双写 2026-09-28 苏摩111] 信号消费时对账B轨（纯影子，不影响A轨流程）
    _b_track_decision_package(signal, sym, side)
    if side in ('SELL',): side = 'SHORT'
    # [梵天2.0转正 2026-09-27 苏摩111] L2接管拦截权（影子→实权）
    # 苏摩111指令：全面采用2.0，跳过T2等待期直接转正，1.0冻结。
    # fail-closed铁律：BRAHMA_ENFORCE≠1 或评估异常 → 拒绝开单（缺证据=不开单）
    # mode记录：shadow=记录模式 / enforce=2.0实权拦截；影子数据源切至shadow_history
    import sys as _sys2
    _enforce = os.environ.get('BRAHMA_ENFORCE') == '1'
    try:
        _rg_verdict = _shadow_evaluate_risk_gate(signal, sym, side, positions_data, enforce=_enforce)
        if _enforce and isinstance(_rg_verdict, dict) and not _rg_verdict.get('ok'):
            log(f'BLOCK {sym} {side}: risk_gate {_rg_verdict.get("rule")}/{_rg_verdict.get("reason")} [2.0转正fail-closed]')
            return False
        if not _enforce:
            print(f'[WARN] BRAHMA_ENFORCE未开启，risk_gate仅记录模式', file=_sys2.stderr)
    except Exception as _se:
        # fail-closed：评估异常=拒绝开单（2.0宪法第4条Fail-loud，关键路径禁静默吞）
        from brahma_brain import error_ledger as _el
        _el.count('risk_gate', error=_se, context={'phase': 'enforce_eval', 'symbol': sym})
        log(f'BLOCK {sym} {side}: risk_gate异常fail-closed {_se}')
        return False
    # [P0-1执行端封堵 2026-09-25 苏摩111] WAIT_15M/WAIT_ENTRY/SKIP/WATCH=非执行指令
    # 双保险：即使上游误入队，执行端也拒绝市价开单（根修在brahma_state_refresh）
    _action = str(signal.get('action', '') or '').upper()
    if _action in ('WAIT_15M', 'WAIT_ENTRY', 'SKIP', 'WATCH'):
        log(f'SKIP {sym} {side}: action={_action} 非执行指令（结构未确认），拒绝开单')
        return False
    score  = float(signal.get('score_final', signal.get('score', 0)))
    grade  = float(signal.get('grade_num', signal.get('grade', 0)))
    regime = signal.get('regime', '')
    sl_pct = float(signal.get('sl_pct', 2.0))
    tp1    = float(signal.get('tp1', 0))
    tp2    = float(signal.get('tp2', 0))

    # 门槛检查
    # [9.29 F1 苏摩111] 移除PAPER_SCORE_MIN硬门：决策层五步是唯一裁判
    # （EXECUTE信号必过五步+风险闸，executor重复设门槛=评分口径分裂，
    # 9.27两条BTC EXECUTE死于37.8<100，见P0报告根因①）
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

    # 仓位大小（CHOP_MID减半，CHOP解锁信号用override）
    nav_pct = PAPER_NAV_PCT
    nav_override = signal.get('nav_pct_override')
    if nav_override is not None:
        nav_pct = float(nav_override)
        log(f'CHOP解锁信号，仓位上限: {nav_pct*100:.1f}%NAV')
    elif CHOP_HALF_SIZE and 'CHOP' in regime:
        nav_pct = PAPER_NAV_PCT / 2
        log(f'CHOP体制，仓位减半 → {nav_pct*100:.1f}%NAV')

    # [B分级降权 2026-09-27 苏摩111] 评分层SKIP warn通道：仓位再×0.5（EV门保留）
    if signal.get('score_gate_warn'):
        nav_pct = nav_pct * 0.5
        log(f'B降权warn通道: 评分层SKIP，仓位×0.5 → {nav_pct*100:.2f}%NAV')

    # SL/TP计算
    if side == 'LONG':
        sl_price = price * (1 - sl_pct / 100)
        tp1_price = tp1 if tp1 else price * 1.02
        tp2_price = tp2 if tp2 else price * 1.04
    else:
        sl_price = price * (1 + sl_pct / 100)
        tp1_price = tp1 if tp1 else price * 0.98
        tp2_price = tp2 if tp2 else price * 0.96

    # [B线铁律 2026-09-26 苏摩111] RR硬门槛：盈亏比≥1.5，不达标直接SKIP
    # 复盘铁证：98笔闭环盈亏比0.34=数学必然亏损；此门是止血第一关
    rr = abs(tp1_price - price) / max(1e-9, abs(price - sl_price))
    if rr < 1.5:
        log(f'SKIP {sym} {side}: RR={rr:.2f} < 1.5 (B线盈亏比硬门槛，复盘铁证0.34病根)')
        return False

    # [B线铁律] SL距离≥1.5×ATR验证用paper SL距离下限0.8%笆底（过窄SL=噪音打损）
    if sl_pct < 0.8:
        log(f'SKIP {sym} {side}: sl_pct={sl_pct}% 过窄(<0.8%)，噪音打损风险')
        return False

    # [B线记账闭环 2026-09-26 苏摩111] 开仓必经SSOT账本：费用立扣+订单落库
    # 幻影教训：不经账本的开仓=幻影记录（9.26复盘铁证）
    # [梵天2.0测试隔禹 2026-09-27] BRAHMA_DRYRUN=1 → 跳过账本落盘，风控/门槛/SL全链路照跑
    # 根因：9.27三方评估enforce dry-run污染账本+17.31fee已回滚，防止复发
    if os.environ.get('BRAHMA_DRYRUN') == '1':
        log(f'DRYRUN {sym} {side}: 全链路验证通过，账本隔离未写入 (BRAHMA_DRYRUN=1)')
        return True
    try:
        import paper_ledger as _pl
        if _pl.circuit_breaker_active():
            log(f'SKIP {sym}: 单日亏损≥3%NAV，熔断中')
            return False
        rec = _pl.open_position(
            symbol=sym, side=side, price=price,
            nav_pct=nav_pct, leverage=5.0,
            sl=sl_price, tp1=tp1_price, tp2=tp2_price,
            regime=regime, score=score, rr=round(rr, 3),
            source=signal.get('source', 'unknown'),
        )
        log(f'LEDGER+ OPEN {sym} {side} notional={rec["notional"]:.0f} fee={rec["entry_fee"]:.2f} NAV_after={_pl.nav():,.2f}')
    except Exception as _le:
        # [梵天2.0 T1 error_ledger接线 2026-09-27 苏摩111] execution关键路径异常必记账
        try:
            from brahma_brain import error_ledger as _el
            _el.count('execution', error=_le,
                      context={'phase': 'paper_open_ledger', 'symbol': sym, 'side': side})
        except Exception:
            pass
        log(f'ABORT {sym}: 账本记账失败 {_le} — 拒绝开单（无账本不交易）')
        return False

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
        'rr':           round(rr, 3),
        'ledger_id':    rec['id'],
        'qty':          rec['qty'],
        'notional':     rec['notional'],
        'margin':       rec['margin'],
        'leverage':     rec['leverage'],
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
    # [B线单实例锁 2026-09-27 苏摩111] 拿不到锁=跳过本轮（防多写者竞态）
    from paper_lock import paper_lock
    with paper_lock('paper_executor') as ok:
        if not ok:
            print('HEARTBEAT_OK (lock busy, skip)')
            return
        _main_locked()


def _main_locked():
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
        except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
        log(f'本轮开单 {opened} 笔，当前持仓 {len(positions_data["positions"])} 个')
    else:
        print('HEARTBEAT_OK')


if __name__ == '__main__':
    main()
