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
import json, time
from pathlib import Path
from typing import Optional

# [P2-4 2026-09-29 苏摩111] R1扩展：SL避让清算集群
# audit实锤：验证单SL 82065距50x清算价82142仅77点(0.09%)，扫进集群=瀑布接飞刀
R1_LIQ_BUFFER_PCT = 0.5            # SL距最近同侧清算集群 ≥0.5%价格
R1_LIQ_HEATMAP_MAX_AGE_H = 24      # heatmap超过24h视为stale，跳过检查（不误伤）
_DATA_DIR = Path(__file__).parent.parent / 'data'

# ---- 参数常量(设计书§2 L2) ----
RULES = ('R1', 'R2', 'R3', 'R4', 'R5')

R1_MIN_SL_ATR_RATIO = 1.5           # SL距离 ≥ 1.5×ATR1H
R1_HARD_FLOOR = {'ETHUSDT': 37.5, 'BTCUSDT': 825.0}   # 100X另验硬底线(铁律封印)
R2_MAX_NAV_PCT = 10.0               # BTC+ETH最大10%NAV
R3_BEAR_RECOVERY_NO_SHORT = True    # BEAR_RECOVERY仅多，严禁空
R3_CHOP_WATCH_SCORE = 110.0         # CHOP_MID score≥110→WATCH(WARN)
R5_DAILY_LOSS_PCT = -3.0            # 日亏≥3%NAV停机24h
R5_HALT_SECONDS = 24 * 3600


def _sl_liq_clearance_pct(symbol: str, side: str, sl_pct: float, price: float):
    """[P2-4 2026-09-29 苏摩111] SL与最近同侧清算集群的距离（%）。
    返回 (距离%, 集群价位)；无heatmap/数据stale/无同侧集群 → None（跳过检查）。
    LONG只看价下方long_liq_map（多头清算在下方），SHORT只看价上方short_liq_map。
    """
    if not price or price <= 0:
        return None
    f = _DATA_DIR / f'liq_heatmap_{(symbol or "").lower()}.json'
    try:
        if not f.exists():
            return None
        age_h = (time.time() - f.stat().st_mtime) / 3600.0
        if age_h > R1_LIQ_HEATMAP_MAX_AGE_H:
            return None  # stale数据不拦截（数据可得性门，非fail-closed场景）
        d = json.loads(f.read_text())
    except Exception:
        return None
    key = 'long_liq_map' if side == 'LONG' else 'short_liq_map'
    levels = []
    for v in (d.get(key) or {}).values():
        try:
            lv = float(v)
            if lv > 0:
                levels.append(lv)
        except (TypeError, ValueError):
            pass
    if side == 'LONG':
        cands = [lv for lv in levels if lv < price]
    else:
        cands = [lv for lv in levels if lv > price]
    if not cands:
        return None
    sl_price = price * (1 - sl_pct / 100) if side == 'LONG' else price * (1 + sl_pct / 100)
    nearest = min(cands, key=lambda lv: abs(lv - sl_price))
    return abs(sl_price - nearest) / price * 100.0, nearest


def liq_aware_sl_pct(symbol: str, side: str, sl_pct: float, price: float, max_iter: int = 4):
    """[P2-4 2026-09-29 苏摩111] SL自动外推避让清算集群（只放宽不收紧）。
    返回外推后的sl_pct；无法达成0.5%缓冲 → None（调用方SKIP，fail-safe）。
    无heatmap数据 → 原样返回sl_pct（行为不变）。
    """
    for _ in range(max_iter):
        res = _sl_liq_clearance_pct(symbol, side, sl_pct, price)
        if res is None:
            return sl_pct
        dist, nearest = res
        if dist >= R1_LIQ_BUFFER_PCT - 1e-6:
            return sl_pct
        buf = R1_LIQ_BUFFER_PCT / 100.0 * price * 1.01  # 1%安全余量，防浮点边界闪烁
        if side == 'LONG':
            target = nearest - buf          # 外推到集群下方缓冲外（审计姿势：SL放到集群下方）
            sl_pct = (price - target) / price * 100.0
        else:
            target = nearest + buf          # 外推到集群上方缓冲外
            sl_pct = (target - price) / price * 100.0
        # 过窄/过宽由调用方(executor RR门+0.8%下限)收口，此处不做主观拒单
    res = _sl_liq_clearance_pct(symbol, side, sl_pct, price)
    if res and res[0] >= R1_LIQ_BUFFER_PCT - 1e-6:
        return sl_pct
    return None

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
    # [梵天2.0转正 2026-09-27 苏摩111] R1数据面兜底: 信号/调用方都没带ATR时，
    # 从brahma_state_<sym>.json的momentum.atr_1h兜底读取（数据源=1.0同款分析链，
    # 非新增网络调用；仍读不到才fail-closed BLOCK）。根治影子期6/6 MISSING_ATR假拦截。
    if atr1h is None and not state.get('no_state_fallback'):
        try:
            import json as _json
            from pathlib import Path as _Path
            _sym_lower = symbol.replace('USDT', '').lower()
            _state_file = _Path(__file__).resolve().parent.parent / 'data' / f'brahma_state_{_sym_lower}.json'
            if _state_file.exists():
                _ms = (_json.loads(_state_file.read_text()).get('momentum') or {})
                _v = _ms.get('atr_1h')
                if _v:
                    atr1h = float(_v)
        except Exception:
            atr1h = None

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

    # ---- R1-LQ SL避让清算集群 [P2-4 2026-09-29 苏摩111] ----
    # audit实锤：验证单SL 82065距50x清算价82142仅77点(0.09%)，扫进集群=瀑布接飞刀
    # 距离<0.5%且未经liq_aware_sl_pct外推 → WARN（不BLOCK：热力图缺失/stale不误伤）
    # 注意：不early-return，先走完R2/R3/R4/R5实权门，无BLOCK才报WARN（R4重复建仓优先级更高）
    _r1_liq_warn = None
    if price > 0 and sl_pct > 0:
        try:
            _lq = _sl_liq_clearance_pct(symbol, side, sl_pct, price)
            if _lq is not None and _lq[0] < R1_LIQ_BUFFER_PCT:
                _r1_liq_warn = f'LiQ-WARN SL{sl_pct:.2f}%距清算簇{_lq[1]:.0f}仅{_lq[0]:.2f}%(<0.5%)建议外推'
        except Exception:
            pass  # 清算检查是增强层，异常不阻断主判定

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

    # 实权门全过 → 若有LiQ-WARN则透出WARN [P2-4]
    if _r1_liq_warn:
        return {'ok': True, 'rule': 'R1', 'severity': 'WARN', 'reason': _r1_liq_warn}
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
    # R1: 缺ATR → BLOCK（no_state_fallback=测试隔离开关，跳过state文件兜底）
    r = evaluate(sig(), {'no_state_fallback': True})
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
