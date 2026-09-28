#!/usr/bin/env python3
"""
brahma_decision_lifecycle.py — 梵天2.0 P1：D-10深度决策引擎（D1-D3 + 决策包）

设计院封印 2026-09-28 苏摩111（方案: reports/deep_decision_engine_design_20260928.md）

宪法铁律:
1. 本模块为纯函数计算层：输入per-symbol state（brahma_state_<sym>.json），输出DecisionPackage
2. 影子运行：产出决策包，不触发任何执行（9.26 A/B分离铁律；EXECUTION仍走decide()旧路径）
3. 事件流接线：决策包升级/证伪/登记 → brahma_events（decision_made/decision_upgrade/decision_invalidated）
4. 接线位置: scripts/price_trigger_monitor.py（D3时钟tick轮询，5min周期）
5. 全部证据来自11步分析链既有字段，零新增采集

接入位置:
  - scripts/brahma_decision_lifecycle.py（本文件，可独立执行 --tick --symbol BTC）
  - scripts/price_trigger_monitor.py（tick调用）
  - scripts/brahma_state_refresh.py（分析完成后生成/刷新决策包，下一期P2接线）
消费方: paper_executor(P2影子双写) / daily_review / IC周审 / 复盘回路(P3)

决策包生命周期（D3交易员时钟）:
  WAIT_TRACKING → ARMED → TRIGGERED → IN_POSITION → (EXIT/INVALIDATED)
       ↑______________证伪/超时/远离______________|
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT))

DATA = ROOT / 'data'
PACKAGES_DIR = DATA / 'decision_packages'
STATE_FILE = ROOT / 'data' / 'paper_account.json'

# ── D1: 论点成立门槛（复用12维终选+regime_config既有门槛） ──────────────
REGIME_MIN_SCORE = {
    'BULL_TREND:LONG': 100, 'BULL_TREND:SHORT': 120,
    'BEAR_TREND:SHORT': 100, 'BEAR_TREND:LONG': 140,
    'CHOP_MID:LONG': 110, 'CHOP_MID:SHORT': 100,
    'BEAR_EARLY:SHORT': 100, 'BEAR_EARLY:LONG': 118,
    'BULL_EARLY:LONG': 100, 'BULL_EARLY:SHORT': 118,
    'BEAR_RECOVERY:SHORT': 999,  # 严禁空（宪法硬编码）
    'BEAR_RECOVERY:LONG': 100,
}

# ── D3: 时钟状态机阈值（40年交易员口径，方案§五） ──────────────────────
ARMED_DIST_ATR_MULT = 0.5    # 距入场区<0.5×ATR1H → ARMED（武装待命）
PAST_ZONE_ATR_MULT = 1.0     # 穿过挂单区>1×ATR → 已过站（追单禁止→重评）
STALE_HOURS = 6              # 决策包TTL: 超6h未触发 → 重新评估（价格漂移证据失效）
MAX_PACKAGES = 400           # 决策包存档上限（滚动清理）


def _load_paper_account() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {'current_nav': 100000.0, 'daily_pnl': []}


def _load_regime_state() -> str:
    """体制健康度（GREEN/YELLOW/RED）——复用regime_realtime_watcher输出。"""
    try:
        p = DATA / 'regime_realtime.json'
        if p.exists():
            return str(json.loads(p.read_text()).get('regime_state', 'GREEN'))
    except Exception:
        pass
    return 'GREEN'


def _cvd_direction(symbol: str) -> tuple:
    """读CVD实时快照（cvd_ws_collector产出）。返回(cvd_4h, dir)。"""
    sym = symbol.lower().replace('USDT', '')
    p = DATA / f'cvd_realtime_{sym}usdt.json'
    if not p.exists():
        p = DATA / f'cvd_realtime_{symbol.lower()}.json'
    if not p.exists():
        return 0.0, 'NO_DATA'
    try:
        d = json.loads(p.read_text())
        age = time.time() - float(d.get('ts', 0))
        if age > 6 * 3600:
            return 0.0, 'STALE'  # 超6h算过期，不作为证据
        return float(d.get('cvd_4h', 0)), str(d.get('dir_4h', ''))
    except Exception:
        return 0.0, 'NO_DATA'


def _smart_money_from_state(state: dict) -> tuple:
    """从state.extra.liq_snap读大户/散户持仓比（复用step6口径）。"""
    ls = ((state.get('extra') or {}).get('liq_snap') or {})
    long_pct = float(ls.get('long_pct', 50) or 50)
    return long_pct, 100 - long_pct


# ═══════════════════════════════════════════════════════════════
# D1: 论点成立性（Thesis Gate）
# ═══════════════════════════════════════════════════════════════
def d1_thesis(state: dict) -> dict:
    """从11步分析state提取论点。返回论点dict或{'none': True}。"""
    regime = str(state.get('regime', '') or '')
    score = float(state.get('score_final') or state.get('score') or 0)
    cf = state.get('confluence') or {}
    cf_action = str(cf.get('action', '') or '')
    tb = state.get('trader_brain') or {}
    dec = state.get('decision') or {}

    # 方向: trader_brain/dicision已裁决的优先，否则从体制推
    direction = str(tb.get('direction') or dec.get('direction') or '').upper()
    if direction not in ('LONG', 'SHORT'):
        if 'BEAR' in regime:
            direction = 'SHORT'
        elif 'BULL' in regime or 'RECOVERY' in regime:
            direction = 'LONG'
        else:
            return {'none': True, 'reason': 'CHOP无三票方向（trader_brain NONE）'}

    key = f'{regime}:{direction}'
    min_score = REGIME_MIN_SCORE.get(key, 100)
    if score < min_score:
        return {'none': True, 'reason': f'score12={score:.1f}<{key}门{min_score}'}

    # 结构证据清单（11步链既有字段）
    evidence = []
    smc = state.get('smc') or {}
    fvg = smc.get('fvg') or {}
    price = float(state.get('price') or 0)
    momentum = state.get('momentum') or {}
    rsi_1h = float(momentum.get('rsi_1h', 50) or 50)
    ls = (state.get('extra') or {}).get('liq_snap') or {}

    # 证据1: FVG磁铁方向一致（nearest_bear/nearest_bull结构位判方向）
    _nb = fvg.get('nearest_bull') or {}
    _ns = fvg.get('nearest_bear') or {}
    fvg_dir = 'NONE'
    fvg_mid_anchor = 0
    if isinstance(_ns, dict) and _ns.get('mid'):
        fvg_dir = 'SHORT'
        fvg_mid_anchor = float(_ns.get('mid', 0))
    if isinstance(_nb, dict) and _nb.get('mid'):
        # 同时有Bull/Bear FVG时取距离现价近的作磁铁
        _bull_mid = float(_nb.get('mid', 0))
        if fvg_dir == 'SHORT' and abs(_bull_mid - price) < abs(fvg_mid_anchor - price):
            fvg_dir = 'LONG'
            fvg_mid_anchor = _bull_mid
        elif fvg_dir == 'NONE':
            fvg_dir = 'LONG'
            fvg_mid_anchor = _bull_mid
    if fvg_dir == direction:
        _dist = abs(price - fvg_mid_anchor) / price * 100 if fvg_mid_anchor and price else 99
        evidence.append({'name': 'fvg_magnet', 'detail': f'{fvg_dir}磁铁中点${fvg_mid_anchor:,.0f} 距离{_dist:.2f}%',
                         'dist_pct': round(_dist, 3), 'mid': fvg_mid_anchor})
    # 证据2: 有效OB（age<50未穿越——宪法铁律）
    ob_key = 'nearest_bull_ob' if direction == 'LONG' else 'nearest_bear_ob'
    ob = ((smc.get('order_blocks') or {}).get(ob_key)) or {}
    if isinstance(ob, dict) and ob and float(ob.get('age_bars', 999)) < 50 and not ob.get('broken', True):
        evidence.append({'name': 'ob_valid', 'detail': f'OB ${ob.get("low", 0):,.0f}~${ob.get("high", 0):,.0f} age={ob.get("age_bars")}未穿越',
                         'low': ob.get('low'), 'high': ob.get('high'), 'age_bars': ob.get('age_bars')})
    # 证据3: RSI极值
    if direction == 'SHORT' and rsi_1h >= 70:
        evidence.append({'name': 'rsi_extreme', 'detail': f'RSI1h={rsi_1h:.0f}超买'})
    elif direction == 'LONG' and rsi_1h <= 30:
        evidence.append({'name': 'rsi_extreme', 'detail': f'RSI1h={rsi_1h:.0f}超卖'})
    elif direction == 'SHORT' and rsi_1h >= 60:
        evidence.append({'name': 'rsi_bias', 'detail': f'RSI1h={rsi_1h:.0f}偏强（弱证据）'})
    # 证据4: 方仓匹配（标的专属>0.4铁律）
    fc = state.get('fangcang') or {}
    fc_match = float(fc.get('match_score', 0) or 0)
    if fc_match >= 0.4:
        evidence.append({'name': 'fangcang', 'detail': f'方仓匹配{fc_match:.2f}≥0.4'})

    if len(evidence) < 2:
        return {'none': True, 'reason': f'结构证据{len(evidence)}/2不足（{[e["name"] for e in evidence]}）'}

    # 论点强度分级（方案§八）
    resonance_cnt = len(evidence)
    if resonance_cnt >= 4:
        strength = 'A'
    elif resonance_cnt == 3:
        strength = 'B'
    else:
        strength = 'C'

    thesis = {
        'none': False,
        'direction': direction,
        'regime': regime,
        'score12': score,
        'cf_action': cf_action,
        'min_score': min_score,
        'evidence': evidence,
        'evidence_count': resonance_cnt,
        'strength': strength,
        'statement': f'{regime}体制{direction}论点: {" + ".join(e["name"] for e in evidence)}',
    }
    return thesis


# ═══════════════════════════════════════════════════════════════
# D2: 反证检验（Devil's Advocate）
# ═══════════════════════════════════════════════════════════════
def d2_counter_evidence(state: dict, thesis: dict) -> list:
    """主动找反证。返回反证清单（每项带强度）。方案§七。"""
    if not thesis or thesis.get('none'):
        return []
    direction = thesis['direction']
    counter = []
    ls = (state.get('extra') or {}).get('liq_snap') or {}
    oi_chg = float(ls.get('oi_chg4h', 0) or 0)
    long_pct, _short_pct = _smart_money_from_state(state)
    cvd, cvd_dir = _cvd_direction(str(state.get('symbol', '') or 'BTCUSDT') if 'symbol' in state else 'BTCUSDT')

    # 反证1: OI方向矛盾（OI增仓方向与论点反）
    if direction == 'SHORT' and oi_chg > 1.0:
        counter.append({'name': 'oi_against', 'detail': f'OI 4h增仓{oi_chg:+.1f}%与做空矛盾', 'weight': 1})
    elif direction == 'LONG' and oi_chg < -1.0:
        counter.append({'name': 'oi_against', 'detail': f'OI 4h减仓{oi_chg:+.1f}%与做多矛盾', 'weight': 1})

    # 反证2: CVD方向矛盾（强反证需>0.3×ATR1H当量）
    momentum = state.get('momentum') or {}
    atr_1h = float(momentum.get('atr_1h', 0) or 0)
    price = float(state.get('price') or 0)
    _cvd_strong = atr_1h > 0 and price > 0 and abs(cvd) > 0.3 * atr_1h / price * 100
    if _cvd_strong:
        if direction == 'SHORT' and cvd > 0:
            counter.append({'name': 'cvd_against', 'detail': f'CVD 4h={cvd:+.0f}强买压与做空矛盾', 'weight': 2})
        elif direction == 'LONG' and cvd < 0:
            counter.append({'name': 'cvd_against', 'detail': f'CVD 4h={cvd:+.0f}强卖压与做多矛盾', 'weight': 2})

    # 反证3: 聪明钱方向矛盾（大户持仓<45%时做多/ >55%时做空都矛盾）
    if direction == 'LONG' and long_pct < 45:
        counter.append({'name': 'sm_against', 'detail': f'大户仅{long_pct:.0f}%多与做多矛盾', 'weight': 1})
    elif direction == 'SHORT' and long_pct > 55:
        counter.append({'name': 'sm_against', 'detail': f'大户{long_pct:.0f}%多与做空矛盾', 'weight': 1})

    # 反证4: Hurst矛盾（TREND体制但Hurst<0.55）
    bd = ((state.get('confluence') or {}).get('breakdown') or {})
    hurst_str = str(bd.get('Hurst体制验证', ''))
    try:
        hurst = float(hurst_str.split('H=')[1].split(' ')[0])
    except Exception:
        hurst = None
    if hurst is not None:
        if 'TREND' in thesis.get('regime', '') and hurst < 0.55:
            counter.append({'name': 'hurst_against', 'detail': f'H={hurst:.2f}<0.55趋势未确认', 'weight': 1})
        elif 'CHOP' in thesis.get('regime', '') and hurst > 0.60:
            counter.append({'name': 'hurst_against', 'detail': f'H={hurst:.2f}>0.60与CHOP矛盾(转势)', 'weight': 1})

    return counter


def counter_score(counter: list) -> int:
    """反证加权分。方案§七: 0-1不降 / 2降×0.8 / 3降×0.5禁ARMED / ≥4作废。"""
    return sum(c.get('weight', 1) for c in counter)


# ═══════════════════════════════════════════════════════════════
# D3: 交易员时钟（状态机核心）
# ═══════════════════════════════════════════════════════════════
def d3_clock(pkg: dict, price_now: float) -> dict:
    """根据现价vs决策包挂单区，判定/推进决策状态。返回更新后的pkg+transition原因。

    状态机（方案§五）:
      WAIT_TRACKING → ARMED → TRIGGERED → IN_POSITION
           ↑________证伪/超时/远离________|
    P1期: TRIGGERED/IN_POSITION为影子状态（记录但不触发执行）。
    """
    t = pkg.get('thesis') or {}
    direction = t.get('direction', '')
    zone = pkg.get('trigger', {}).get('zone') or [0, 0]
    lo, hi = float(zone[0] or 0), float(zone[1] or 0)
    atr_1h = float(pkg.get('context', {}).get('atr_1h', 0) or 0)
    if not lo or not hi or not price_now:
        return pkg, {'transition': 'none', 'reason': 'no_zone_or_price'}

    # 距离入场区的真实间隙（40年交易员语义：距入场区还有多远才能进场）
    # LONG: 入场区在下方 → 现价高于hi=还没回调到 → gap=price-hi；现价lo≤p≤hi=已在区内
    # SHORT: 入场区在上方 → 现价低于lo=还没反弹到 → gap=lo-price；现价lo≤p≤hi=已在区内
    if direction == 'LONG':
        gap = max(0.0, price_now - hi)          # 区上方→未回调到
        inside = lo <= price_now <= hi
        passed = price_now < lo - PAST_ZONE_ATR_MULT * atr_1h   # 穿过区下方>1×ATR=已过站
    else:  # SHORT
        gap = max(0.0, lo - price_now)          # 区下方→未反弹到
        inside = lo <= price_now <= hi
        passed = price_now > hi + PAST_ZONE_ATR_MULT * atr_1h   # 穿过区上方>1×ATR=已过站

    atr = atr_1h if atr_1h > 0 else price_now * 0.01
    cur = pkg.get('state', 'WAIT_TRACKING')

    # 证伪优先级最高（价格证伪位由决策包falsification.price给出）
    fals_px = float((pkg.get('falsification') or {}).get('price', 0) or 0)
    if fals_px > 0:
        if direction == 'LONG' and price_now <= fals_px:
            return _invalidate(pkg, price_now, f'价格证伪: ${price_now:,.1f}≤${fals_px:,.1f}（论点死亡）')
        if direction == 'SHORT' and price_now >= fals_px:
            return _invalidate(pkg, price_now, f'价格证伪: ${price_now:,.1f}≥${fals_px:,.1f}（论点死亡）')

    if cur == 'WAIT_TRACKING':
        if passed:
            return pkg, {'transition': 'none', 'reason': 'zone passed, awaiting re-evaluation'}
        if gap <= ARMED_DIST_ATR_MULT * atr:
            pkg['state'] = 'ARMED'
            return pkg, {'transition': 'ARMED', 'reason': f'距入场区{gap:.1f}<0.5×ATR1H({ARMED_DIST_ATR_MULT * atr:.1f})武装待命'}
        return pkg, {'transition': 'none', 'reason': f'距入场区{gap:.1f}>0.5×ATR1H继续跟踪'}
    elif cur == 'ARMED':
        if inside:
            pkg['state'] = 'TRIGGERED'
            pkg['triggered_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
            return pkg, {'transition': 'TRIGGERED', 'reason': '现价入区+ARMED→触发（影子记录，P2接执行）'}
        if passed:
            pkg['state'] = 'WAIT_TRACKING'
            return pkg, {'transition': 'WAIT_TRACKING', 'reason': '价格过站（未触发先走）→回跟踪'}
        if gap > ARMED_DIST_ATR_MULT * atr:
            pkg['state'] = 'WAIT_TRACKING'
            return pkg, {'transition': 'WAIT_TRACKING', 'reason': f'价格远离{gap:.1f}>{ARMED_DIST_ATR_MULT * atr:.1f}回跟踪'}
        return pkg, {'transition': 'none', 'reason': 'ARMED待命（区外近处）'}
    elif cur in ('TRIGGERED', 'IN_POSITION'):
        # P1影子期：TRIGGERED后进入IN_POSITION由P2执行链管理，这里只做证伪监控
        pkg['state'] = 'IN_POSITION' if cur == 'TRIGGERED' else cur
        return pkg, {'transition': 'none', 'reason': '影子持仓中（P2接管执行）'}
    return pkg, {'transition': 'none', 'reason': f'unknown state {cur}'}


def _invalidate(pkg: dict, price_now: float, reason: str) -> tuple:
    pkg['state'] = 'INVALIDATED'
    pkg['invalidated_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    pkg['invalidated_reason'] = reason
    return pkg, {'transition': 'INVALIDATED', 'reason': reason}


# ═══════════════════════════════════════════════════════════════
# P2: D5确认K线 → D6风险预算 → D7执行结构（苏摩111授权 2026-09-28）
# ═══════════════════════════════════════════════════════════════

def d5_entry_style(symbol: str, direction: str, zone: list) -> dict:
    """D5确认K线判定（方案§五）：在挂单区内找确认K线，输出entry_style。
    LONG=下影收回+缩量→LIMIT；SHORT=上影收回+缩量→LIMIT；
    无确认K线但BOS突破→STOP；两种都缺→SPLIT（60/40挂单+突破）。
    返回 {'style': 'LIMIT|STOP|SPLIT', 'confirm': bool, 'note': str}
    """
    out = {'style': 'SPLIT', 'confirm': False, 'note': 'default split'}
    try:
        from data_cache import get_klines
        k = get_klines(symbol, '15m', 30)
        if not k or len(k) < 15:
            return out
        lo, hi = float(zone[0]), float(zone[1])
        # 只看最近15根（覆盖入区时刻附近）
        bars = k[-15:]
        o = [float(x[1]) for x in bars]
        h = [float(x[2]) for x in bars]
        l = [float(x[3]) for x in bars]
        c = [float(x[4]) for x in bars]
        v = [float(x[5]) for x in bars]
        avg_v = sum(v) / len(v) if v else 1
        # 找在区内的K线（低点入区才算触区）
        in_zone = [i for i in range(len(bars)) if l[i] <= hi and c[i] >= lo]
        if not in_zone:
            return {'style': 'SPLIT', 'confirm': False, 'note': 'zone not touched yet'}
        i = in_zone[-1]
        rng = max(1e-9, h[i] - l[i])
        lower_wick = min(o[i], c[i]) - l[i]
        upper_wick = h[i] - max(o[i], c[i])
        vol_low = v[i] < avg_v * 0.9
        if direction == 'LONG':
            if lower_wick / rng >= 0.4 and c[i] >= o[i] and vol_low:
                return {'style': 'LIMIT', 'confirm': True, 'note': f'下影{lower_wick/rng:.0%}收回+缩量'}
            if c[i] > h[i-1] if i > 0 else False:
                return {'style': 'STOP', 'confirm': True, 'note': '突破确认(15m收盘破前高)'}
        else:
            if upper_wick / rng >= 0.4 and c[i] <= o[i] and vol_low:
                return {'style': 'LIMIT', 'confirm': True, 'note': f'上影{upper_wick/rng:.0%}收回+缩量'}
            if i > 0 and c[i] < l[i-1]:
                return {'style': 'STOP', 'confirm': True, 'note': '突破确认(15m收盘破前低)'}
        return out
    except Exception as _e:
        return {'style': 'SPLIT', 'confirm': False, 'note': f'error:{_e}'[:60]}


REGIME_BUDGET_MULT = {
    'BEAR_TREND': 1.2, 'BULL_TREND': 1.2, 'BEAR_EARLY': 1.0, 'BULL_EARLY': 1.0,
    'CHOP_MID': 0.8, 'BEAR_RECOVERY': 1.0,
}


def d6_risk_budget(thesis: dict, sl_dist_pct: float, state: dict) -> dict:
    """D6风险预算（方案§六）：预算=NAV×0.5%×系数连乘 → max_loss → 仓位。
    替代score系数连乘：分数决定做不做，预算决定输了疼不疼。
    返回 {'max_loss_pct', 'max_loss_usd', 'position_pct', 'mults': {...}, 'note'}
    """
    nav = 100000.0  # B线纸面本金（paper_ledger.START_NAV）
    try:
        import paper_ledger as _pl
        nav = float(getattr(_pl, 'START_NAV', 100000))
    except Exception:
        pass
    regime = str(thesis.get('regime', 'CHOP_MID') or 'CHOP_MID')
    mults = {'regime': REGIME_BUDGET_MULT.get(regime, 0.8)}
    m = mults['regime']
    # 体制红绿灯（regime_state RED×0.5）
    rs = str(state.get('regime_state', '') or state.get('antifragile', {}).get('state', '') if isinstance(state.get('antifragile'), dict) else '')
    if rs == 'RED':
        m *= 0.5; mults['regime_red'] = 0.5
    # 连损衰减（loss_memory）
    lm = state.get('loss_memory') or {}
    consec = int(lm.get('consecutive_losses', 0) or 0)
    if consec >= 5:
        m *= 0.3; mults['consec_loss'] = 0.3
    elif consec >= 3:
        m *= 0.7; mults['consec_loss'] = 0.7
    # 当日亏损衰减（ledger当日净亏）
    try:
        import paper_ledger as _pl
        daily = _pl.day_pnl_pct() if hasattr(_pl, 'day_pnl_pct') else 0
    except Exception:
        daily = 0
    if daily <= -1.5:
        m *= 0.5; mults['daily_loss'] = 0.5
    # 相关性折扣（同向BTC+ETH已有持仓→×0.6）
    corr = 0
    try:
        import paper_ledger as _pl
        for p in _pl.open_positions():
            if p.get('side') == thesis.get('direction'):
                corr += 1
    except Exception:
        pass
    if corr > 0:
        m *= 0.6; mults['corr'] = 0.6
    strength_mult = {'A': 1.0, 'B': 0.6, 'C': 0.5}.get(thesis.get('strength', 'C'), 0.5)
    m *= strength_mult; mults['strength'] = strength_mult
    loss_pct = 0.5 * m
    sl_dist = max(0.8, float(sl_dist_pct or 2.0))
    pos_pct = loss_pct / sl_dist * 100
    # [宪法铁律 9.28] BTC+ETH最大10%NAV仓位cap——预算/SL距离可能得出16%+，宪法不允许
    if pos_pct > 10.0:
        pos_pct = 10.0
        mults['constitution_cap'] = 10.0
    return {'max_loss_pct': round(loss_pct, 3), 'max_loss_usd': round(nav * loss_pct / 100, 2),
            'position_pct': round(pos_pct, 3), 'mults': mults,
            'note': f'预算{loss_pct:.2f}%NAV=${nav*loss_pct/100:.0f} 仓位{pos_pct:.1f}%（SL{sl_dist:.1f}%）'}


def d7_execution_structure(pkg: dict, entry_style: str, budget: dict) -> dict:
    """D7执行结构（方案§五）：分批+TP+移动SL+超时作废。纯计算，影子期不执行。
    返回执行计划dict（写入decision_package.execution）。
    """
    zone = pkg.get('trigger', {}).get('zone') or [0, 0]
    lo, hi = float(zone[0]), float(zone[1])
    direction = pkg.get('thesis', {}).get('direction', '')
    pos_pct = float(budget.get('position_pct', 0) or 0)
    if entry_style == 'SPLIT':
        legs = [{'leg': 1, 'pct': 60, 'type': 'LIMIT', 'price': lo if direction == 'LONG' else hi},
                {'leg': 2, 'pct': 40, 'type': 'STOP', 'price': hi if direction == 'LONG' else lo}]
    elif entry_style == 'STOP':
        legs = [{'leg': 1, 'pct': 100, 'type': 'STOP', 'price': hi if direction == 'LONG' else lo}]
    else:
        legs = [{'leg': 1, 'pct': 100, 'type': 'LIMIT', 'price': round((lo + hi) / 2, 1)}]
    fals_px = float((pkg.get('falsification') or {}).get('price', 0) or 0)
    tp_dists = [0.5, 1.0, 2.0]  # TP1/2/3距离（×SL距离）
    if fals_px and direction == 'LONG':
        base = abs(hi - fals_px)
    elif fals_px:
        base = abs(fals_px - lo)
    else:
        base = max(1e-9, hi - lo)
    tps = []
    for i, td in enumerate(tp_dists):
        px = (lo + td * base) if direction == 'LONG' else (lo - td * base)
        tps.append({'tp': i + 1, 'pct': [40, 40, 20][i], 'price': round(px, 1)})
    return {
        'legs': legs, 'tps': tps,
        'move_sl': {'rule': '浮盈>1.5×ATR1H→SL移保本', 'atr_mult': 1.5},
        'timeout': {'hours': 6, 'note': '挂单后6h未触发→作废重评（对齐STALE_HOURS）'},
        'position_pct': pos_pct, 'entry_style': entry_style,
    }


# ═══════════════════════════════════════════════════════════════
# 决策包构建（D1+D2+D3一次装配）
# ═══════════════════════════════════════════════════════════════
def build_package(symbol: str, state: dict) -> dict | None:
    """从per-symbol state构建决策包。无论点返回None。"""
    thesis = d1_thesis(state)
    if thesis.get('none'):
        return None
    counter = d2_counter_evidence(state, thesis)
    cs = counter_score(counter)

    # 反证门槛（方案§七）
    if cs >= 4:
        return None  # 论点作废
    strength = thesis['strength']
    if cs >= 3:
        return None  # 禁升ARMED→不建包（连跟踪都不值得）
    # 强度降级
    if cs == 2:
        thesis['strength'] = {'A': 'B', 'B': 'C', 'C': 'C'}.get(strength, 'C')
        thesis['strength_downgrade'] = f'反证{cs}分降级{strength}→{thesis["strength"]}'

    price = float(state.get('price') or 0)
    momentum = state.get('momentum') or {}
    atr_1h = float(momentum.get('atr_1h', 0) or 0)

    # 挂单区：优先trader_brain entry区间，次用FVG中点±0.5×ATR1H，最后ATR推算
    tb = state.get('trader_brain') or {}
    entry_lo = float(tb.get('entry_lo', 0) or 0)
    entry_hi = float(tb.get('entry_hi', 0) or 0)
    zone_src = 'trader_brain'
    if entry_lo <= 0 or entry_hi <= 0:
        # FVG中点锚（证据里取最近的结构位）
        fvg_ev = next((e for e in thesis['evidence'] if e['name'] == 'fvg_magnet'), None)
        ob_ev = next((e for e in thesis['evidence'] if e['name'] == 'ob_valid'), None)
        anchor = None
        if ob_ev:
            anchor = ob_ev['mid'] if direction_ok(thesis, 'mid') else (ob_ev['low'] if thesis['direction'] == 'LONG' else ob_ev['high'])
        elif fvg_ev:
            anchor = fvg_ev.get('mid')
        if anchor and anchor > 0:
            _off = max(atr_1h * 0.3, anchor * 0.002)
            if thesis['direction'] == 'LONG':
                entry_lo, entry_hi = round(anchor - _off, 1), round(anchor + _off * 0.3, 1)
            else:
                entry_lo, entry_hi = round(anchor - _off * 0.3, 1), round(anchor + _off, 1)
            zone_src = 'structure_anchor'
        else:
            _off = max(atr_1h * 0.5, price * 0.003)
            if thesis['direction'] == 'LONG':
                entry_lo, entry_hi = round(price - _off, 1), round(price - _off * 0.3, 1)
            else:
                entry_lo, entry_hi = round(price + _off * 0.3, 1), round(price + _off, 1)
            zone_src = 'atr_fallback'

    # 价格证伪位：论点失效位（多头=入场区下沿-1.5×ATR1H；空头=上沿+1.5×ATR1H）
    # 40年交易员口径：SL放结构失效位，不是最近技术位
    if thesis['direction'] == 'LONG':
        fals_price = round(entry_lo - max(1.5 * atr_1h, entry_lo * 0.015), 1)
    else:
        fals_price = round(entry_hi + max(1.5 * atr_1h, entry_hi * 0.015), 1)

    # [P2 2026-09-28 苏摩111] D4结构确认（包内状态记录，不阻塞建包——包在WAIT_TRACKING等D4）
    d4_ok = direction_ok(thesis, 'mid', state)
    sl_dist_pct = round(max(1.0, abs(fals_price - price) / price * 100), 2)
    budget = d6_risk_budget(thesis, sl_dist_pct, state)
    _es = d5_entry_style(symbol, thesis['direction'], [entry_lo, entry_hi])
    pkg = {
        'decision_id': f'D-{time.strftime("%Y%m%d")}-{symbol.replace("USDT", "")}-{int(time.time()) % 100000}',
        'symbol': symbol,
        'created_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'updated_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'thesis': thesis,
        'state': 'WAIT_TRACKING',
        'd4_structure': {'confirmed': d4_ok, 'note': '15m CHoCH/BOS+缩量+SL结构位（包内随tick刷新）'},
        'trigger': {'zone': [entry_lo, entry_hi], 'zone_src': zone_src, 'style': 'PENDING_CONFIRM', 'expiry_hours': STALE_HOURS},
        'falsification': {
            'price': fals_price,
            'structure': [],  # P2: OI翻转/CVD翻转/体制切换
            'time': {'hours': STALE_HOURS, 'note': '6h未触发→证据漂移重评'},
            'disaster': '单日-3%NAV熔断（宪法既有）',
        },
        'counter_evidence': counter,
        'counter_score': cs,
        'risk_budget': budget,
        'context': {'price': price, 'atr_1h': atr_1h, 'regime_state': _load_regime_state()},
        'shadow': True,  # P1全程影子
        'transitions': [],
        'review': {'triggered_at': None, 'result': None, 'lesson': None},
    }
    pkg['execution'] = d7_execution_structure(pkg, _es['style'], budget)
    pkg['d5_candle'] = _es
    # 初始时钟判定
    pkg, tr = d3_clock(pkg, price)
    if tr.get('transition') and tr['transition'] not in ('none',):
        pkg['transitions'].append({'ts': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), **tr})
    return pkg


def direction_ok(thesis: dict, _mode: str, state: dict = None) -> bool:
    """D4结构确认（P2实装，方案§五）：
    入场区附近时检查15m结构：CHoCH/BOS方向一致 + 缩量回调 + SL在结构失效位。
    state缺省时退化为True（向后兼容旧调用方）。fail-closed：检查失败=False。
    接入位置：build_package锚点选择 / d5确认前置（P2接线）
    """
    if not state:
        return True  # 无state则不阻塞旧路径（兼容层）
    try:
        direction = thesis.get('direction', '')
        price = float(state.get('price') or 0)
        if not price or direction not in ('LONG', 'SHORT'):
            return False
        from data_cache import get_klines
        from smc_engine import detect_bos_choch
        k = get_klines(state.get('symbol', 'BTCUSDT'), '15m', 60)
        if not k or len(k) < 30:
            return False  # 数据不足=结构未确认（fail-closed）
        highs = [float(x[2]) for x in k]
        lows = [float(x[3]) for x in k]
        closes = [float(x[4]) for x in k]
        vols = [float(x[5]) for x in k]
        st = detect_bos_choch(highs, lows, closes)
        # 条件1: 15m结构方向一致（UPTREND↔LONG / DOWNTREND↔SHORT；RANGING看CHoCH）
        struct = st.get('structure', 'UNKNOWN')
        if struct == 'UPTREND' and direction == 'SHORT':
            return False
        if struct == 'DOWNTREND' and direction == 'LONG':
            return False
        if struct in ('UPTREND', 'DOWNTREND'):
            return True
        # RANGING/UNKNOWN: 有同向CHoCH/BOS才确认
        types = [e.get('type', '') for e in (st.get('choch') or []) + (st.get('bos') or [])]
        if direction == 'LONG' and any('BULL' in t for t in types):
            return True
        if direction == 'SHORT' and any('BEAR' in t for t in types):
            return True
        # 条件2: 缩量回调（LONG近3根回调量<前5根均量=健康）
        if len(vols) >= 8:
            recent = sum(vols[-3:]) / 3
            prior = sum(vols[-8:-3]) / 5
            if prior > 0 and recent / prior < 1.2:  # 缩量或温和
                return True
        return False
    except Exception:
        return False  # fail-closed：异常=结构未确认


# ═══════════════════════════════════════════════════════════════
# 决策包存取 + 事件流接线
# ═══════════════════════════════════════════════════════════════
def _pkg_path(decision_id: str) -> Path:
    return PACKAGES_DIR / f'{decision_id}.json'


def save_package(pkg: dict) -> Path:
    PACKAGES_DIR.mkdir(exist_ok=True)
    p = _pkg_path(pkg['decision_id'])
    tmp = p.with_suffix('.tmp')
    tmp.write_text(json.dumps(pkg, ensure_ascii=False, indent=1))
    os.replace(tmp, p)
    _cleanup_old()
    return p


def load_active_packages() -> list:
    """加载所有非终态决策包（WAIT_TRACKING/ARMED/TRIGGERED/IN_POSITION）。"""
    if not PACKAGES_DIR.exists():
        return []
    out = []
    terminal = {'INVALIDATED', 'COMPLETED'}
    for p in PACKAGES_DIR.glob('D-*.json'):
        try:
            pkg = json.loads(p.read_text())
            if pkg.get('state') not in terminal:
                out.append(pkg)
        except Exception:
            continue
    return out


def _cleanup_old() -> None:
    """滚动清理：只留最近MAX_PACKAGES个决策包（终态包归档删除）。"""
    try:
        files = sorted(PACKAGES_DIR.glob('D-*.json'), key=lambda p: p.stat().st_mtime)
        while len(files) > MAX_PACKAGES:
            files.pop(0).unlink()
            files = sorted(PACKAGES_DIR.glob('D-*.json'), key=lambda p: p.stat().st_mtime)
    except Exception:
        pass


def emit_decision_event(event_type: str, pkg: dict, extra: dict = None) -> str:
    """决策事件写入事件流（W0 append-only）。事件类型: decision_made/decision_upgrade/decision_invalidated。"""
    try:
        sys.path.insert(0, str(ROOT / 'scripts'))
        from brahma_events import append as _ev
        payload = {
            'decision_id': pkg.get('decision_id'),
            'symbol': pkg.get('symbol'),
            'state': pkg.get('state'),
            'direction': (pkg.get('thesis') or {}).get('direction'),
            'strength': (pkg.get('thesis') or {}).get('strength'),
            'zone': (pkg.get('trigger') or {}).get('zone'),
            'falsification_price': (pkg.get('falsification') or {}).get('price'),
            'shadow': pkg.get('shadow', True),
        }
        if extra:
            payload.update(extra)
        return _ev(event_type, payload)
    except Exception as _e:
        print(f'[decision_lifecycle] 事件写入失败: {_e}', file=sys.stderr)
        return ''


# ═══════════════════════════════════════════════════════════════
# 主tick：由price_trigger_monitor（5min cron）调用
# ═══════════════════════════════════════════════════════════════
def tick_symbol(symbol: str, price_override: float = None) -> dict:
    """单标的决策包tick：推进时钟/证伪检查/超时重评。返回summary。"""
    price = float(price_override or 0)
    if not price:
        try:
            import urllib.request
            r = urllib.request.urlopen(f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={symbol}', timeout=5)
            price = float(json.loads(r.read())['price'])
        except Exception as _e:
            return {'symbol': symbol, 'error': f'price fetch failed: {_e}'}

    out = {'symbol': symbol, 'price': price, 'actions': []}

    # 1) 已有活跃包→推进时钟
    for pkg in load_active_packages():
        if pkg.get('symbol') != symbol:
            continue
        # 超时重评（TTL）
        created = pkg.get('created_at', '')
        try:
            from datetime import datetime
            age_h = (time.time() - datetime.fromisoformat(created.replace('Z', '+00:00')).timestamp()) / 3600
        except Exception:
            age_h = 0
        if age_h > STALE_HOURS and pkg['state'] == 'WAIT_TRACKING':
            pkg['state'] = 'INVALIDATED'
            pkg['invalidated_reason'] = f'TTL超时{age_h:.1f}h>6h（价格漂移证据失效）'
            save_package(pkg)
            emit_decision_event('decision_invalidated', pkg, {'reason': pkg['invalidated_reason']})
            out['actions'].append({'decision_id': pkg['decision_id'], 'action': 'TTL_INVALIDATED'})
            continue
        pkg, tr = d3_clock(pkg, price)
        if tr.get('transition') and tr['transition'] != 'none':
            pkg['transitions'].append({'ts': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'price': price, **tr})
            pkg['updated_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
            save_package(pkg)
            _evt = {'ARMED': 'decision_upgrade', 'TRIGGERED': 'decision_upgrade',
                    'INVALIDATED': 'decision_invalidated', 'WAIT_TRACKING': 'decision_upgrade'}.get(tr['transition'])
            if _evt:
                emit_decision_event(_evt, pkg, {'transition': tr['transition'], 'reason': tr.get('reason', '')})
            out['actions'].append({'decision_id': pkg['decision_id'], 'action': tr['transition'], 'reason': tr.get('reason', '')})

    # 2) 无活跃包→尝试从最新state建新包
    has_pkg = any(p.get('symbol') == symbol for p in load_active_packages())
    if not has_pkg:
        sym_key = symbol.replace('USDT', '').lower()
        state_file = DATA / f'brahma_state_{sym_key}.json'
        if state_file.exists():
            try:
                st = json.loads(state_file.read_text())
                # state新鲜度守门（>2h的state不建包——证据漂移）
                age_s = time.time() - float(st.get('_snapshot_written_epoch', 0) or 0)
                if age_s <= 2 * 3600:
                    pkg = build_package(symbol, st)
                    if pkg:
                        save_package(pkg)
                        emit_decision_event('decision_made', pkg, {'created': True})
                        out['actions'].append({'decision_id': pkg['decision_id'], 'action': 'CREATED',
                                               'strength': pkg['thesis']['strength'], 'state': pkg['state']})
            except Exception as _e:
                out.setdefault('errors', []).append(str(_e)[:120])
    return out


def tick_all(symbols: list = None) -> list:
    symbols = symbols or ['BTCUSDT', 'ETHUSDT']
    return [tick_symbol(s) for s in symbols]


def cli():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--tick', action='store_true')
    ap.add_argument('--symbols', default='BTC,ETH')
    ap.add_argument('--symbol', default='')
    ap.add_argument('--build', action='store_true', help='强制从最新state建包（调试）')
    args = ap.parse_args()
    if args.symbol:
        r = tick_symbol(args.symbol if 'USDT' in args.symbol else args.symbol + 'USDT')
        print(json.dumps(r, ensure_ascii=False, indent=1))
    elif args.tick:
        syms = [s.strip().upper() + ('USDT' if 'USDT' not in s.strip().upper() else '') for s in args.symbols.split(',')]
        print(json.dumps(tick_all(syms), ensure_ascii=False, indent=1))
    else:
        ap.print_help()


if __name__ == '__main__':
    cli()
