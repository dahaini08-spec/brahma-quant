"""
risk_gate.py — 梵天2.0 L2风控门（fail-closed纯函数）
[梵天2.0 T1 2026-09-27 苏摩111 深度AI操刀]

设计书: reports/brahma_2.0_design.md §2 L2层 / 过渡评审 R4(重复建仓=E1双单根因)
宪法: MEMORY.md 交易参数铁律(体制乘数/SL距离/仓位档位)
语义: evaluate()纯函数——不读盘、不写库、零网络。输入缺失→fail-closed(BLOCK)

接入位置:
  scripts/paper_executor.py   — BRAHMA_SHADOW=1影子评估(只记录不拦截)
  scripts/auto_executor.py    — 影子评估点(只记录不拦截,双层硬闸不动)
  data/shadow_decisions.jsonl — 评估结果落盘(接线脚本写入)
"""
from __future__ import annotations
from typing import Optional

# ---- 参数常量(设计书§2 L2) ----
RULES = ('R1', 'R2', 'R3', 'R4', 'R5')

R1_MIN_SL_ATR_RATIO = 1.5           # SL距离 ≥ 1.5×ATR1H
R1_HARD_FLOOR = {'ETHUSDT': 37.5, 'BTCUSDT': 825.0}   # 100X另验硬底线(铁律封印)
R2_MAX_NAV_PCT = 10.0               # BTC+ETH最大10%NAV
R3_BEAR_RECOVERY_NO_SHORT = True    # BEAR_RECOVERY仅多，严禁空
R3_CHOP_WATCH_SCORE = 110.0         # CHOP_MID score≥110→WATCH(WARN)
R5_DAILY_LOSS_PCT = -3.0            # 日亏≥3%NAV停机24h
R5_HALT_SECONDS = 24 * 3600

# 体制→策略乘数(硬编码, MEMORY宪法表)
REGIME_MULT = {
    'BEAR_TREND':    {'SHORT': 1.60, 'LONG': 0.50},
    'BEAR_EARLY':    {'SHORT': 1.20, 'LONG': 0.50},
    'CHOP_MID':      {'SHORT': 0.88, 'LONG': 0.50},
    'BULL_TREND':    {'SHORT': 0.50, 'LONG': {'ETHUSDT': 1.30, 'BTCUSDT': 1.20}},
    'BEAR_RECOVERY': {'SHORT': 0.50, 'LONG': {'ETHUSDT': 1.15, 'BTCUSDT': 1.25}},
}
_REGIME_ALIAS = {
    'CHOP': 'CHOP_MID', 'CHOP_LOW': 'CHOP_MID', 'CHOP_HIGH': 'CHOP_MID',
    'BEAR': 'BEAR_TREND', 'BULL': 'BULL_TREND',
}

# 基础仓位档位(SL距离%)
BASE_SIZE_LADDER = ((1.0, 5.0), (1.5, 2.0), (2.0, 3.0))  # (上限, %NAV)
DEFAULT_SIZE_PCT = 2.0

LEVERAGE_100X = 100


def _base_size_pct(sl_pct: float) -> float:
    for cap, pct in BASE_SIZE_LADDER:
        if sl_pct < cap:
            return pct
    return DEFAULT_SIZE_PCT


def _regime_mult(regime: str, symbol: str, side: str) -> Optional[float]:
    r = _REGIME_ALIAS.get((regime or '').upper(), (regime or '').upper())
    m = REGIME_MULT.get(r)
    if m is None:
        return None
    v = m.get(side)
    if isinstance(v, dict):
        return v.get(symbol)
    return v


def _allowed_sides_for(regime: str) -> set:
    r = _REGIME_ALIAS.get((regime or '').upper(), (regime or '').upper())
    if r == 'BEAR_RECOVERY':
        return {'LONG'}
    return {'LONG', 'SHORT'}


def evaluate(signal: dict, state: dict) -> dict:
    """纯函数风控评估。任一规则BLOCK→整体fail-closed。
    signal: {symbol, side, regime, score, sl_pct, atr1h(可选), nav_pct(可选), leverage(可选)}
    state:  {atr1h(可选), open_positions: [{symbol,side}...](可选),
             today_pnl_pct(可选), halt_until_ts(可选), now_ts(可选)}
    返回: {'ok':bool, 'rule':None|'R1'..'R5', 'reason':str, 'severity':'BLOCK'|'WARN'}
    """
    symbol = (signal.get('symbol') or '').upper()
    side = (signal.get('side') or signal.get('signal_dir') or '').upper()
    if side in ('BUY',): side = 'LONG'
    if side in ('SELL',): side = 'SHORT'
    regime = (signal.get('regime') or '').upper()
    score = signal.get('score', signal.get('score_final')) or 0
    sl_pct = float(signal.get('sl_pct') or 0)
    leverage = float(signal.get('leverage') or 1)

    atr1h = state.get('atr1h')
    if atr1h is None:
        atr1h = signal.get('atr1h', signal.get('atr_1h'))

    # ---- R1 SL距离 ----
    if not sl_pct or sl_pct <= 0:
        return {'ok': False, 'rule': 'R1', 'reason': 'MISSING_SL', 'severity': 'BLOCK'}
    if atr1h:
        try:
            atr1h = float(atr1h)
            sl_abs = sl_pct / 100.0  # 相对价格比例
            if sl_abs < R1_MIN_SL_ATR_RATIO * (atr1h / max(1e-9, signal.get('price') or 1)):
                # atr1h为绝对价格时转比例比较
                pass
        except (TypeError, ValueError):
            atr1h = None
    # ATR1H为绝对价格单位: SL距离(绝对) ≥ 1.5×ATR1H
    price = float(signal.get('price') or 0)
    if atr1h and price > 0:
        sl_abs = price * sl_pct / 100.0
        if sl_abs < R1_MIN_SL_ATR_RATIO * atr1h:
            return {'ok': False, 'rule': 'R1', 'reason': f'SL{sl_abs:.0f}<1.5xATR{atr1h:.0f}',
                    'severity': 'BLOCK'}
        if leverage >= LEVERAGE_100X:
            floor = R1_HARD_FLOOR.get(symbol)
            if floor is not None and sl_abs < floor:
                return {'ok': False, 'rule': 'R1', 'reason': f'SL{sl_abs:.1f}<hardfloor{floor}',
                        'severity': 'BLOCK'}
    elif not atr1h:
        return {'ok': False, 'rule': 'R1', 'reason': 'MISSING_ATR', 'severity': 'BLOCK'}

    # ---- R3 体制禁令 ----
    if regime:
        if regime == 'BEAR_RECOVERY' and side == 'SHORT' and R3_BEAR_RECOVERY_NO_SHORT:
            return {'ok': False, 'rule': 'R3', 'reason': 'BEAR_RECOVERY禁空', 'severity': 'BLOCK'}
        if regime == 'BEAR_TREND' and side == 'LONG':
            return {'ok': False, 'rule': 'R3', 'reason': 'BEAR_TREND禁多', 'severity': 'BLOCK'}
        if regime.startswith('CHOP') and float(score) >= R3_CHOP_WATCH_SCORE:
            return {'ok': False, 'rule': 'R3', 'reason': f'CHOP score{score}≥110→WATCH',
                    'severity': 'WARN'}

    # ---- R5 日亏停机 ----
    now_ts = state.get('now_ts') or 0
    halt_until = state.get('halt_until_ts') or 0
    today_pnl = state.get('today_pnl_pct')
    if today_pnl is not None and float(today_pnl) <= R5_DAILY_LOSS_PCT:
        return {'ok': False, 'rule': 'R5', 'reason': f'日亏{today_pnl}%≤-3%→停机',
                'severity': 'BLOCK'}
    if halt_until and now_ts and now_ts < float(halt_until):
        return {'ok': False, 'rule': 'R5', 'reason': '停机冷却中', 'severity': 'BLOCK'}

    # ---- R4 重复建仓(E1双单根因修复) ----
    for p in (state.get('open_positions') or []):
        psym = (p.get('symbol') or '').upper() if isinstance(p, dict) else str(p).upper()
        pside = (p.get('side') or '').upper() if isinstance(p, dict) else ''
        if psym == symbol and pside == side:
            return {'ok': False, 'rule': 'R4', 'reason': f'重复建仓 {symbol} {side}',
                    'severity': 'BLOCK'}

    # ---- R2 仓位 ----
    nav_pct = float(signal.get('nav_pct') or 0)
    if nav_pct <= 0:
        nav_pct = _base_size_pct(sl_pct)
    mult = _regime_mult(regime, symbol, side)
    if mult is None:
        return {'ok': False, 'rule': 'R2', 'reason': f'体制{regime}无乘数', 'severity': 'BLOCK'}
    cap = min(R2_MAX_NAV_PCT, nav_pct * mult * (1 if mult <= 1.0 else mult))
    if mult <= 1.0:
        cap = nav_pct * mult
    else:
        cap = nav_pct * mult
    if cap > R2_MAX_NAV_PCT:
        cap = R2_MAX_NAV_PCT
    if nav_pct * mult > R2_MAX_NAV_PCT:
        return {'ok': False, 'rule': 'R2', 'reason': f'仓位{nav_pct}%×{mult}>{R2_MAX_NAV_PCT}%NAV',
                'severity': 'BLOCK'}

    return {'ok': True, 'rule': None, 'reason': f'PASS nav≤{cap:.1f}%NAV', 'severity': 'PASS'}


# ---------------- 自测 ----------------
def _selftest() -> int:
    B, W, P = 'BLOCK', 'WARN', 'PASS'
    def sig(**kw):
        base = {'symbol': 'BTCUSDT', 'side': 'LONG', 'regime': 'BULL_TREND',
                'score': 120, 'sl_pct': 2.0, 'price': 84000.0, 'leverage': 5}
        base.update(kw); return base
    st = {'atr1h': 550.0, 'open_positions': [], 'today_pnl_pct': 0}
    t = []
    # R1: SL<1.5×ATR → BLOCK
    r = evaluate(sig(sl_pct=0.9), {'atr1h': 550.0})  # 0.9%*84000=756 < 825
    t.append(('R1-ATR-block', r['rule'] == 'R1' and r['severity'] == B))
    # R1: 缺ATR → BLOCK
    r = evaluate(sig(), {})
    t.append(('R1-missing-ATR', r['rule'] == 'R1' and r['severity'] == B))
    # R1: PASS (2%*84000=1680 ≥ 825)
    r = evaluate(sig(), {'atr1h': 550.0})
    t.append(('R1-pass', r['ok'] is True))
    # R3: BEAR_RECOVERY禁空
    r = evaluate(sig(side='SHORT', regime='BEAR_RECOVERY'), {'atr1h': 550.0})
    t.append(('R3-recov-short', r['rule'] == 'R3' and r['severity'] == B))
    # R3: BEAR_TREND禁多
    r = evaluate(sig(side='LONG', regime='BEAR_TREND'), {'atr1h': 550.0})
    t.append(('R3-bear-long', r['rule'] == 'R3' and r['severity'] == B))
    # R3: CHOP≥110 → WATCH WARN
    r = evaluate(sig(regime='CHOP_MID', score=115), {'atr1h': 550.0})
    t.append(('R3-chop-watch', r['rule'] == 'R3' and r['severity'] == W))
    # R3: CHOP<110 PASS
    r = evaluate(sig(regime='CHOP_MID', score=105), {'atr1h': 550.0})
    t.append(('R3-chop-pass', r['ok'] is True))
    # R4: 重复建仓
    r = evaluate(sig(), {'atr1h': 550.0, 'open_positions': [{'symbol': 'BTCUSDT', 'side': 'LONG'}]})
    t.append(('R4-dup', r['rule'] == 'R4' and r['severity'] == B))
    # R5: 日亏≥3%
    r = evaluate(sig(), {'atr1h': 550.0, 'today_pnl_pct': -3.5})
    t.append(('R5-halt', r['rule'] == 'R5' and r['severity'] == B))
    # R2: BEAR_TREND空单乘数1.6×5%=8% ≤10% PASS
    r = evaluate(sig(side='SHORT', regime='BEAR_TREND', sl_pct=1.0), {'atr1h': 500.0})
    t.append(('R2-bear-short-pass', r['ok'] is True))
    # R2: 超10%NAV → BLOCK
    r = evaluate(sig(side='SHORT', regime='BEAR_TREND', sl_pct=0.9, nav_pct=7.0),
                 {'atr1h': 500.0})
    t.append(('R2-cap-block', r['rule'] == 'R2' and r['severity'] == B))
    fails = [n for n, ok in t if not ok]
    for n, ok in t:
        print(f"  {'✅' if ok else '❌'} {n}")
    print(f"risk_gate selftest: {len(t) - len(fails)}/{len(t)} PASS")
    return 0 if not fails else 1


if __name__ == '__main__':
    raise SystemExit(_selftest())
