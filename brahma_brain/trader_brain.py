#!/usr/bin/env python3
"""
trader_brain.py — 交易员大脑 v2.0
顶层重写 2026-09-11 苏摩111

12项能力一次写对：
1. 6层确定性决策（零LLM）
2. IC验证（全体制fallback）
3. WATCH分档（4-5/6=WATCH轻仓）
4. κ价格确认（FVG磁铁+价格双确认）
5. CHOP区间交易（高抛低吸）
6. 体制自我怀疑（三选二→降级）
7. 观点卡片（WAIT也出点位）
8. 交易员叙事（6段推断）
9. 多剧本+概率+时间预期
10. 触发器（决策直接生成）
11. council已移除
12. 纸面系统信号（decide输出可写入信号池）

接入位置：brahma_manual_analysis.py → trader_brain.decide() + format_output()
"""
import math
import json
import pathlib
from typing import Dict, Any, List, Tuple

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# IC数据加载（全局缓存）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_IC_STATS = None
def _load_ic_stats() -> dict:
    global _IC_STATS
    if _IC_STATS is not None:
        return _IC_STATS
    try:
        _p = pathlib.Path(__file__).parent.parent / 'data' / 'brahma_ic_stats.json'
        if _p.exists():
            _IC_STATS = json.loads(_p.read_text())
        else:
            _IC_STATS = {}
    except Exception:
        _IC_STATS = {}
    return _IC_STATS

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 6层决策引擎
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def decide(
    regime: str, score: float, grade: float,
    macro: Dict, risk: Dict, hurst: float,
    fvg: Dict, ob: Dict, liq: Dict,
    atr_1h: float, atr_4h: float, price: float,
    oi: Dict, sm: Dict, vol: Dict,
    res: Dict, symbol: str = '',
) -> Dict[str, Any]:
    """
    6层确定性决策 + 12项能力统一输出
    """
    # ════════════════════════════════════════════════════════════
    # Layer 1: 大环境层 — 体制+强度+自我怀疑+CHOP区间
    # ════════════════════════════════════════════════════════════
    regime_state = risk.get('regime_state', 'GREEN')
    _weak_trend = ('TREND' in regime or 'EARLY' in regime) and score < 60

    # 方向判定
    direction = 'LONG' if 'BULL' in regime or 'RECOVERY' in regime else 'SHORT' if 'BEAR' in regime else 'NONE'

    # CHOP区间交易模式
    _chop_range = False
    if regime == 'CHOP_MID':
        _ls = liq.get('nearest_short', 0)
        _ll = liq.get('nearest_long', 0)
        if _ls > 0 and _ll > 0 and price > 0:
            _mid = (_ls + _ll) / 2
            _chop_range = True
            if score >= 60:
                direction = 'LONG' if price < _mid else 'SHORT'

    # 体制自我怀疑（OI/κ/Hurst三选二矛盾→降级CHOP）
    _downgraded = False
    if direction != 'NONE':
        _contra = 0
        _contra_list = []
        _oi_dir = 'LONG' if oi.get('signal') in ('LONG_BUILD','SHORT_SQUEEZE') else 'SHORT' if oi.get('signal') in ('SHORT_BUILD','LONG_UNWIND') else 'NONE'
        if _oi_dir != 'NONE' and _oi_dir != direction:
            _contra += 1; _contra_list.append(f'OI={_oi_dir}')
        _kappa_dir = 'LONG' if vol.get('kappa', 0) < -0.05 else 'SHORT' if vol.get('kappa', 0) > 0.05 else 'NONE'
        if _kappa_dir != 'NONE' and _kappa_dir != direction:
            _contra += 1; _contra_list.append(f'κ={_kappa_dir}')
        if 'TREND' in regime and hurst < 0.5:
            _contra += 1; _contra_list.append(f'Hurst={hurst:.3f}<0.5')
        if _contra >= 2:
            _downgraded = True
            direction = 'NONE'

    # 交易许可
    permission = True
    if regime == 'CHOP_MID' and not (_chop_range and score >= 60):
        if score < 110:
            permission = False
    if regime_state == 'RED' and score < 120:
        permission = False
    if _weak_trend and regime_state == 'RED':
        permission = False
    if _downgraded:
        permission = False

    # 杠杆基数
    lev_base = 10
    if regime_state == 'RED': lev_base = 5
    elif regime_state == 'YELLOW': lev_base = 7
    if macro.get('high_impact'): lev_base = min(lev_base, 5)

    # ════════════════════════════════════════════════════════════
    # Layer 2: 结构层 — FVG+OB+清算+共振+入场区+SL
    # ════════════════════════════════════════════════════════════
    fvg_consensus = fvg.get('consensus', fvg.get('dir', 'NONE'))
    structure_dir = 'LONG' if fvg_consensus == 'BULL' else 'SHORT' if fvg_consensus == 'BEAR' else 'NONE'

    entry_lo = res.get('entry_lo', 0)
    entry_hi = res.get('entry_hi', 0)
    liq_support = res.get('liq_nearest_long', 0) or liq.get('nearest_long', 0)

    # 做多入场区=共振区，但如果低于支撑池则上移
    if direction == 'LONG' and liq_support > 0 and entry_lo > 0 and entry_lo < liq_support:
        _shift = liq_support - entry_lo
        entry_lo = round(liq_support, 1)
        entry_hi = round(entry_hi + _shift, 1)
        if entry_hi <= entry_lo:
            entry_hi = round(entry_lo * 1.005, 1)

    # SL = max(SL_PCT铁律, 1.5×ATR4H)
    if direction == 'LONG':
        _sl_pct_req = 0.02
        _min_sl = max(entry_lo * _sl_pct_req, atr_4h * 1.5) if atr_4h else entry_lo * _sl_pct_req
        sl = round(entry_lo - _min_sl, 1) if entry_lo > 0 else 0
        sl_pct = round((entry_lo - sl) / entry_lo * 100, 2) if entry_lo > 0 and sl > 0 else 0
        tp1 = liq.get('nearest_short', 0) if liq.get('nearest_short', 0) > price else round(price + atr_1h * 2.5, 1)
        tp2 = round(tp1 + atr_1h * 2, 1) if tp1 > 0 else 0
        tp3 = round(tp2 + atr_1h * 1.5, 1) if tp2 > 0 else 0
    elif direction == 'SHORT':
        _sl_pct_req = 0.025 if 'BULL' in regime else 0.02
        _min_sl = max(entry_hi * _sl_pct_req, atr_4h * 1.5) if atr_4h else entry_hi * _sl_pct_req
        sl = round(entry_hi + _min_sl, 1) if entry_hi > 0 else 0
        sl_pct = round((sl - entry_hi) / entry_hi * 100, 2) if entry_hi > 0 and sl > 0 else 0
        tp1 = liq.get('nearest_long', 0) if liq.get('nearest_long', 0) > 0 and liq.get('nearest_long', 0) < price else round(price - atr_1h * 2.5, 1)
        tp2 = round(tp1 - atr_1h * 2, 1) if tp1 > 0 else 0
        tp3 = round(tp2 - atr_1h * 1.5, 1) if tp2 > 0 else 0
    else:
        sl = 0; sl_pct = 0; tp1 = 0; tp2 = 0; tp3 = 0
        _sl_pct_req = 0.02

    # RR计算
    if direction == 'LONG' and entry_lo > 0 and sl > 0 and tp1 > 0:
        rr = round((tp1 - entry_lo) / (entry_lo - sl), 2)
    elif direction == 'SHORT' and entry_hi > 0 and sl > 0 and tp1 > 0:
        rr = round((entry_hi - tp1) / (sl - entry_hi), 2)
    else:
        rr = 0

    # SL铁律验证
    _sl_dist = abs(entry_lo - sl) if direction == 'LONG' else abs(sl - entry_hi) if direction == 'SHORT' else 0
    _atr4h_thresh = atr_4h * 1.5 if atr_4h else 0
    sl_valid = (_sl_dist >= _atr4h_thresh and sl_pct >= _sl_pct_req * 100 - 0.01) if _sl_dist > 0 else False

    # ════════════════════════════════════════════════════════════
    # Layer 3: 资金流层 — OI+CVD+聪明钱
    # ════════════════════════════════════════════════════════════
    oi_signal = oi.get('signal', 'NO_DATA')
    cvd_1h = oi.get('cvd_1h', 0)
    oi_bull = oi_signal in ('LONG_BUILD', 'SHORT_SQUEEZE')
    money_flow_dir = 'LONG' if oi_bull else 'SHORT' if oi_signal in ('SHORT_BUILD', 'LONG_UNWIND') else 'NONE'
    cvd_consistent = (oi_bull and cvd_1h >= 0) or (not oi_bull and cvd_1h <= 0) if oi_signal != 'NO_DATA' else True

    big_long = sm.get('big_long', 50)
    retail_long = sm.get('retail_long', 50)
    sm_divergence = abs(big_long - retail_long)
    sm_bull = big_long > retail_long

    # ════════════════════════════════════════════════════════════
    # Layer 4: 波动率层 — κ价格确认+Hurst+GEX
    # ════════════════════════════════════════════════════════════
    kappa = vol.get('kappa', 0)
    gex_bias = vol.get('gex_bias', 'NEUTRAL')
    _fvg_magnet = fvg.get('magnet', 0)
    _price_trend = 'FLAT'
    if _fvg_magnet > 0:
        if price > _fvg_magnet * 1.002:
            _price_trend = 'UP'
        elif price < _fvg_magnet * 0.998:
            _price_trend = 'DOWN'

    vol_dir = 'NONE'
    if kappa < -0.05 and gex_bias != 'POSITIVE':
        if _price_trend == 'UP': vol_dir = 'LONG'
        elif _price_trend == 'DOWN': vol_dir = 'SHORT'
        else: vol_dir = 'LONG'  # 横盘默认κ方向
    elif kappa > 0.05 and gex_bias != 'NEGATIVE':
        if _price_trend == 'DOWN': vol_dir = 'SHORT'
        elif _price_trend == 'UP': vol_dir = 'LONG'
        else: vol_dir = 'SHORT'

    # ════════════════════════════════════════════════════════════
    # Layer 5: 交叉验证层 — 4层方向一致性
    # ════════════════════════════════════════════════════════════
    layer_dirs = {
        'regime': direction,
        'structure': structure_dir,
        'money_flow': money_flow_dir,
        'volatility': vol_dir,
    }
    consistent_count = sum(1 for v in layer_dirs.values() if v == direction) if direction != 'NONE' else 0
    conflicts = [f'{k}={v}' for k, v in layer_dirs.items() if v != 'NONE' and v != direction and direction != 'NONE']

    if consistent_count >= 3: confidence = 'HIGH'; _conf_mult = 1.0
    elif consistent_count == 2: confidence = 'MED'; _conf_mult = 0.7
    else: confidence = 'LOW'; _conf_mult = 0.5
    if not cvd_consistent:
        _conf_mult *= 0.8
        confidence = 'MED' if confidence == 'HIGH' else 'LOW' if confidence == 'MED' else confidence

    # ════════════════════════════════════════════════════════════
    # Layer 6: 决策层 — IC验证+条件检查+ENTER/WATCH/WAIT分档
    # ════════════════════════════════════════════════════════════

    # IC验证（全体制fallback）
    _ic = _load_ic_stats()
    _ic_ev = None; _ic_wr = None
    if _ic and direction != 'NONE':
        _bucket = f'{regime}:{direction}'
        _ev_data = _ic.get('ev_by_bucket', {})
        for _key, _data in _ev_data.items():
            if _bucket in _key:
                _score_range = _key.split(':')[-1]
                if _score_range == '<120' and score < 120:
                    _ic_ev = _data.get('ev'); _ic_wr = _data.get('wr')
                elif _score_range == '120-139' and 120 <= score < 140:
                    _ic_ev = _data.get('ev'); _ic_wr = _data.get('wr')
                elif _score_range == '140+' and score >= 140:
                    _ic_ev = _data.get('ev'); _ic_wr = _data.get('wr')

    # 条件检查
    missing = []
    if not permission:
        if _downgraded: missing.append(f'体制降级CHOP（三选二矛盾）')
        elif regime == 'CHOP_MID' and score < 110: missing.append(f'CHOP体制score={score:.0f}<110')
        elif regime_state == 'RED' and score < 120: missing.append(f'失效期RED score={score:.0f}<120')
        elif _weak_trend: missing.append(f'弱趋势score={score:.0f}<60')
        else: missing.append('环境许可未通过')
    if direction == 'NONE': missing.append(f'体制{regime}无方向')
    if consistent_count < 2 and direction != 'NONE': missing.append(f'交叉验证仅{consistent_count}/4')
    if (entry_lo == 0 or entry_hi == 0) and direction != 'NONE': missing.append('入场区=0（方向矛盾）')
    if sl > 0 and not sl_valid: missing.append(f'SL不通过ATR4H铁律(SL={sl_pct:.2f}%, 需≥{_sl_pct_req*100:.1f}%)')
    if 0 < rr < 2.0: missing.append(f'RR={rr:.1f}<2.0')
    if rr == 0 and direction != 'NONE': missing.append('RR无法计算')
    # 死穴门控
    _dead_zone = 'BULL' in regime and direction == 'LONG' and score >= 140
    if _dead_zone: missing.append(f'死穴:BULL:LONG:score≥140→WR=0%~30%')
    # OI矛盾
    if direction == 'LONG' and oi_signal == 'SHORT_BUILD': missing.append(f'做多但OI={oi_signal}')
    if direction == 'SHORT' and oi_signal == 'LONG_BUILD': missing.append(f'做空但OI={oi_signal}')
    # IC验证标注
    if _ic_ev is not None and _ic_ev < 0 and direction != 'NONE':
        missing.append(f'IC验证:score<120区间EV={_ic_ev:+.2f}%历史亏损')

    # ENTER/WATCH/WAIT分档
    _passed = 6 - len(missing) if direction != 'NONE' else 0
    if len(missing) == 0 and direction != 'NONE':
        action = 'ENTER'
    elif _passed >= 4 and direction != 'NONE':
        action = 'WATCH'
    else:
        action = 'WAIT'

    # 仓位计算
    if action in ('ENTER', 'WATCH'):
        if 120 <= score < 140: _sm = 1.0 + (score - 120) / 100.0
        elif score >= 110: _sm = 0.9
        else: _sm = 0.8
        _nav = risk.get('nav_mult', 1.0)
        _base = 5.0
        _mult = 1.0 if action == 'ENTER' else 0.4  # WATCH轻仓×0.4
        position_pct = max(1, round(_base * _conf_mult * _nav * _sm * _mult))
        leverage = lev_base if action == 'ENTER' else max(3, lev_base // 2)
        if regime_state == 'RED':
            position_pct = max(1, position_pct // 2)
            leverage = max(3, leverage // 2)
    else:
        position_pct = 0; leverage = 0

    # 理由
    if action == 'ENTER':
        reason = f'{regime}体制顺势{direction} + FVG{fvg_consensus} + OI={oi_signal} + 交叉验证{consistent_count}/4 + 置信{confidence}'
    elif action == 'WATCH':
        reason = f'WATCH({_passed}/6通过) — ' + ' / '.join(missing[:3])
    else:
        reason = f'WAIT — ' + ' / '.join(missing[:3]) if missing else 'WAIT'

    # 矛盾裁决
    conflict_res = ''
    if oi_bull != sm_bull and oi_signal != 'NO_DATA':
        _oi_change = abs(oi.get('total_change', 0))
        conflict_res = f'OI vs 聪明钱矛盾 → 跟随OI({oi_signal})' if _oi_change > 5000 else f'OI vs 聪明钱矛盾 → 跟随聪明钱({sm.get("signal","")})'

    # 触发器（决策直接生成）
    triggers = _build_triggers(direction, action, missing, score, regime_state, hurst, oi_signal, liq, price, atr_1h)

    # 剧本推演
    scenarios = _build_scenarios(hurst, liq, price, oi_signal, direction)

    return {
        'action': action, 'direction': direction,
        'entry_lo': entry_lo, 'entry_hi': entry_hi, 'sl': sl,
        'tp1': tp1, 'tp2': tp2, 'tp3': tp3,
        'rr': rr, 'sl_pct': sl_pct, 'leverage': leverage, 'position_pct': position_pct,
        'confidence': confidence, 'consistent_count': consistent_count,
        'cross_check': {'layer_directions': layer_dirs, 'conflicts': conflicts},
        'conflict_resolution': conflict_res,
        'reason': reason, 'missing': missing, 'dead_zone': _dead_zone,
        'triggers': triggers, 'scenarios': scenarios,
        'ic_ev': _ic_ev, 'ic_wr': _ic_wr,
        'score': score, 'regime': regime,
    }


def _build_triggers(direction, action, missing, score, regime_state, hurst, oi_signal, liq, price, atr_1h) -> list:
    """决策直接生成触发器"""
    _t = []
    if direction != 'NONE':
        if regime_state == 'RED': _t.append(f'失效期RED→仓位减半，等score过120={120-score:.0f}分')
        if 'RR' in ' '.join(missing): _t.append(f'等RR≥2.0（止损墙拉远或入场区上移）')
        if 'OI' in ' '.join(missing): _t.append(f'如果OI从{oi_signal}翻转 → 资金流确认（通常1-2根4H内）')
        if 'score' in ' '.join(missing) or 'CHOP' in ' '.join(missing): _t.append(f'如果score从{score:.0f}涨过{120 if regime_state == "RED" else 110} → 体制确认')
        if 'IC' in ' '.join(missing): _t.append(f'IC历史亏损区间，等score过120脱离')
    else:
        if hurst >= 0.60: _t.append(f'Hurst={hurst:.3f}已>0.60✅，等score站上{110 if regime_state != "RED" else 120}={110-score:.0f}分 → ${price*0.985:,.0f}附近多单可试')
        elif hurst >= 0.55: _t.append(f'等Hurst突破0.60（当前{hurst:.3f}）+score站上{110 if regime_state != "RED" else 120} → ${price*0.985:,.0f}附近多单可试')
        else: _t.append(f'等Hurst突破0.55+score站上{110 if regime_state != "RED" else 120} → ${price*0.985:,.0f}附近多单可试')
        if liq.get('nearest_long', 0) > 0 and liq.get('nearest_long', 0) < price:
            _t.append(f'如果价格先跌到${liq["nearest_long"]:,.0f}支撑池+1H收阳 → 可轻仓试探')
        if liq.get('nearest_short', 0) > price:
            _t.append(f'如果价格先涨到${liq["nearest_short"]:,.0f}止损墙+1H收阴 → 可轻仓试空')
    # 浮盈保护
    if atr_1h > 0 and action in ('ENTER', 'WATCH'):
        _t.append(f'浮盈>${atr_1h*1.5:,.0f}(1.5×ATR1H)→移动SL保本')
    return _t


def _build_scenarios(hurst, liq, price, oi_signal, direction) -> list:
    """多剧本+概率+时间预期"""
    _ts = 'high' if hurst >= 0.6 else 'medium' if hurst >= 0.55 else 'low'
    _ls = liq.get('nearest_short', 0)
    _ll = liq.get('nearest_long', 0)
    _ls2 = liq.get('second_short', 0)
    _ll2 = liq.get('second_long', 0)
    _up_pct = ((_ls - price) / price * 100) if _ls > price else 0
    _dn_pct = ((price - _ll) / price * 100) if _ll > 0 and _ll < price else 0
    # OI权重
    _ua, _da = 1.0, 1.0
    if 'UNWIND' in oi_signal: _ua, _da = 0.7, 1.3
    elif oi_signal == 'SHORT_BUILD': _ua, _da = 0.8, 1.2
    # 概率
    if _ts == 'high' and _up_pct < 3: _up = 65
    elif _ts == 'medium' and _up_pct < 3: _up = 45
    elif _ts == 'high' and _up_pct < 5: _up = 50
    else: _up = 30
    _up = round(_up * _ua)
    if _ts == 'low' and _dn_pct < 2: _dn = 55
    elif _ts == 'medium' and _dn_pct < 2: _dn = 40
    elif _ts == 'low' and _dn_pct < 4: _dn = 45
    else: _dn = 25
    _dn = round(_dn * _da)
    _chop = max(15, 100 - _up - _dn)
    # 时间预期
    _bars = max(2, int((0.60 - hurst) / 0.015)) if hurst < 0.60 else 0
    _time = f'约{_bars}根4H({_bars*4}h)' if _bars > 0 else '已在趋势区'

    _sc = []
    if _ls > price:
        _ut = f'${_ls2:,.0f}' if _ls2 > _ls else f'${_ls:,.0f}'
        _sc.append(f'剧本A({_up}%): 破${_ls:,.0f}止损墙→逼空到{_ut}，{max(_up-30,15)}%假突破回落')
    if _ll > 0 and _ll < price:
        _dt = f'${_ll2:,.0f}' if _ll2 > 0 and _ll2 < _ll else f'${_ll:,.0f}'
        _sc.append(f'剧本B({_dn}%): 破${_ll:,.0f}支撑池→猎杀到{_dt}，{max(100-_dn-15,20)}%支撑有效')
    _sc.append(f'剧本C({_chop}%): 横盘，Hurst趋势确认{_time}')
    return _sc


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 统一输出：观点卡片 + 交易员叙事
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def format_opinion(result: Dict, symbol: str, price: float, regime: str) -> str:
    """观点卡片（ENTER=完整VIP / WATCH=轻仓VIP / WAIT=观点）"""
    sym = symbol.replace('USDT', '')
    d = result['direction']
    emoji = '🟢' if d == 'LONG' else '🔴' if d == 'SHORT' else '⚪'
    action = result['action']

    if action == 'ENTER':
        return _format_vip_enter(result, sym, d, emoji, regime)
    elif action == 'WATCH':
        return _format_vip_watch(result, sym, d, emoji)
    else:
        return _format_opinion_wait(result, sym, d, emoji, price)


def _format_vip_enter(r, sym, d, emoji, regime):
    return (
        f'🌿 姓赵不宣 | {sym} 今日布局\n\n'
        f'{emoji} {"多单" if d == "LONG" else "空单"}｜{"回调" if d == "LONG" else "反弹"}入场区 ${r["entry_lo"]:,.1f}~${r["entry_hi"]:,.1f}\n'
        f'止损 ${r["sl"]:,.1f}｜目标 ${r["tp1"]:,.0f}→${r["tp2"]:,.0f}→${r["tp3"]:,.0f}\n'
        f'杠杆 {r["leverage"]}x｜仓位 {r["position_pct"]}%  RR={r["rr"]:.1f}x  SL={r["sl_pct"]:.1f}% ✅\n\n'
        f'{"🔴 暂无空单｜等待结构" if "BULL" in regime or "RECOVERY" in regime else "🟢 暂无多单｜等待结构" if "BEAR" in regime else "⚠️ CHOP体制｜区间交易"}\n'
        f'\n🚫 破${r["sl"]:,.1f}策略作废\n\n'
        f'⚠️ {r["reason"]}\n'
        f'{"⚙️ " + r["conflict_resolution"] + chr(10) if r.get("conflict_resolution") else ""}'
        f'📊 梵天系统｜数据驱动｜不是建议'
    )


def _format_vip_watch(r, sym, d, emoji):
    return (
        f'🌿 姓赵不宣 | {sym} 今日布局（WATCH轻仓）\n\n'
        f'{emoji} {"多单" if d == "LONG" else "空单"}｜{"回调" if d == "LONG" else "反弹"}入场区 ${r["entry_lo"]:,.1f}~${r["entry_hi"]:,.1f}\n'
        f'止损 ${r["sl"]:,.1f}｜目标 ${r["tp1"]:,.0f}→${r["tp2"]:,.0f}→${r["tp3"]:,.0f}\n'
        f'杠杆 {r["leverage"]}x｜仓位 {r["position_pct"]}%  RR={r["rr"]:.1f}x  SL={r["sl_pct"]:.1f}% ⏳\n\n'
        f'⏳ 待确认：{" / ".join(r["missing"][:4])}\n'
        f'⚠️ 条件未满，轻仓试探\n'
        f'{"⚙️ " + r["conflict_resolution"] + chr(10) if r.get("conflict_resolution") else ""}'
        f'📊 梵天系统｜数据驱动｜不是建议'
    )


def _format_opinion_wait(r, sym, d, emoji, price):
    lines = [f'🌿 姓赵不宣 | {sym} 今日观点', '']
    if d != 'NONE' and r['entry_lo'] > 0 and r['entry_hi'] > 0:
        lines.append(f'{emoji} {"多单" if d == "LONG" else "空单"}｜{"回调" if d == "LONG" else "反弹"}入场区 ${r["entry_lo"]:,.1f}~${r["entry_hi"]:,.1f}')
        if r['sl'] > 0:
            lines.append(f'止损 ${r["sl"]:,.1f}｜目标 ${r["tp1"]:,.0f}→${r["tp2"]:,.0f}→${r["tp3"]:,.0f}')
            lines.append(f'RR={r["rr"]:.1f}x  SL={r["sl_pct"]:.1f}%')
        lines.append(f'交叉验证 {r["consistent_count"]}/4  ' + ' '.join(f'{k}={v}' for k,v in r['cross_check']['layer_directions'].items()))
        lines.append('')
        if r['missing']: lines.append(f'⏳ 待确认：{" / ".join(r["missing"])}')
        lines.append(f'⚠️ 方向{d}，条件未满，等确认后入场')
    elif d != 'NONE':
        lines.append(f'{emoji} 偏{d}｜监测位 ${price:,.1f}')
        lines.append(f'交叉验证 {r["consistent_count"]}/4  ' + ' '.join(f'{k}={v}' for k,v in r['cross_check']['layer_directions'].items()))
        lines.append('')
        if r['missing']: lines.append(f'⏳ 待确认：{" / ".join(r["missing"])}')
        lines.append(f'⚠️ 方向{d}但无共振入场区，等结构形成')
    else:
        lines.append(f'⚪ 无方向｜现价 ${price:,.1f}')
        lines.append(f'交叉验证 {r["consistent_count"]}/4  ' + ' '.join(f'{k}={v}' for k,v in r['cross_check']['layer_directions'].items()))
        lines.append('')
        if r['missing']: lines.append(f'⏳ {" / ".join(r["missing"])}')
        lines.append('⚠️ 体制无方向，等趋势确认')
    if r.get('conflict_resolution'): lines.append(f'⚙️ {r["conflict_resolution"]}')
    lines.append('📊 梵天系统｜数据驱动｜不是建议')
    return '\n'.join(lines)


def format_narrative(result: Dict, symbol: str, price: float, regime: str, fvg: Dict, oi: Dict, sm: Dict, vol: Dict, risk: Dict, res: Dict) -> str:
    """交易员叙事6段：市场现状→主力意图→趋势状态→剧本→价位→结论"""
    d = result['direction']
    hurst = vol.get('hurst', 0.5)
    kappa = vol.get('kappa', 0)
    big_long = sm.get('big_long', 50)
    retail_long = sm.get('retail_long', 50)
    oi_signal = oi.get('signal', 'NO_DATA')
    liq_short = liq.get('nearest_short', 0) if 'liq' in dir() else res.get('liq_nearest_short', 0)
    liq_long = res.get('liq_nearest_long', 0) or (liq.get('nearest_long', 0) if 'liq' in dir() else 0)
    scenarios = result.get('scenarios', [])
    triggers = result.get('triggers', [])
    missing = result.get('missing', [])
    regime_state = risk.get('regime_state', 'GREEN')
    score = result.get('score', 0)

    parts = []
    # 1. 市场现状
    parts.append(f'{symbol.replace("USDT","")}在${price:,.0f}，{regime} score={score:.0f}，FVG{fvg.get("consensus","NONE")}共识，OI={oi_signal}。')

    # 2. 主力意图+推断
    if big_long >= 60 and 'UNWIND' in oi_signal:
        parts.append(f'大户{big_long:.0f}%多但OI全线撤退=在等不是在加。等Hurst突破0.60确认趋势，一旦确认OI会从UNWIND转BUILD。')
    elif big_long >= 55 and oi_signal == 'SHORT_BUILD':
        parts.append(f'大户{big_long:.0f}%多但OI={oi_signal}=有人在高位挂空单对冲。等OI从SHORT_BUILD翻转=空头被扫=做多信号确认。')
    elif 'UNWIND' in oi_signal:
        parts.append(f'OI={oi_signal}=资金在减仓离场。等OI减仓结束出现BUILD=新方向确认。')
    else:
        parts.append(f'大户{big_long:.0f}%多 vs 散户{retail_long:.0f}%多，OI={oi_signal}。')

    # 3. 趋势状态
    _h = f'Hurst={hurst:.3f}'
    if hurst >= 0.6: _h += '趋势区'
    elif hurst >= 0.55: _h += '趋势性隐现'
    else: _h += '随机游走'
    if kappa < -0.1: _h += f'，κ={kappa:.3f}Call强'
    elif kappa > 0.1: _h += f'，κ={kappa:.3f}Put强'
    else: _h += f'，κ={kappa:.3f}中性'
    parts.append(_h + '。')

    # 4. 剧本推演
    if scenarios:
        parts.append('。'.join(scenarios) + '。')

    # 5. 关键价位
    _lv = []
    if result['entry_lo'] > 0 and d != 'NONE': _lv.append(f'入场区${result["entry_lo"]:,.1f}~${result["entry_hi"]:,.1f}')
    if result['sl'] > 0: _lv.append(f'SL=${result["sl"]:,.1f}')
    if liq_short > price: _lv.append(f'上方${liq_short:,.0f}')
    if liq_long > 0 and liq_long < price: _lv.append(f'下方${liq_long:,.0f}')
    if fvg.get('magnet', 0) > 0: _lv.append(f'FVG磁铁${fvg["magnet"]:,.1f}')
    if _lv: parts.append('关键价位：' + ' / '.join(_lv) + '。')

    # 6. 结论+触发器
    if triggers:
        parts.append('触发器：' + ' / '.join(triggers) + '。')

    return ' '.join(parts)


# 向后兼容
def format_vip_card(result: Dict, symbol: str, price: float, regime: str) -> str:
    return format_opinion(result, symbol, price, regime)
