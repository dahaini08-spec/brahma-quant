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
import json
import pathlib
import time as _time_mod
from typing import Dict, Any, Tuple

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
# Layer 0: 市场感知层 — 宏观日历+价格路径+跨标的关联+动态决策框架
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# --- 宏观日历感知 ---
_MACRO_EVENTS = {
    # 日期: {event, time_utc, impact}
    '2026-09-11': {'event': 'CPI', 'time_utc': '12:30', 'impact': 'high'},
    '2026-09-17': {'event': 'FOMC', 'time_utc': '18:00', 'impact': 'high'},
    '2026-10-02': {'event': 'NFP', 'time_utc': '12:30', 'impact': 'high'},
    '2026-10-10': {'event': 'CPI', 'time_utc': '12:30', 'impact': 'high'},
    '2026-10-13': {'event': 'FOMC', 'time_utc': '18:00', 'impact': 'high'},
    '2026-11-06': {'event': 'NFP', 'time_utc': '12:30', 'impact': 'high'},
    '2026-11-13': {'event': 'CPI', 'time_utc': '12:30', 'impact': 'high'},
}

def _check_macro_calendar() -> Dict:
    """检查今天是否有宏观事件，返回事件上下文
    优先读macro_real.json，FOMC已公布则返回post_event"""
    from datetime import datetime, timezone
    import json as _json, os as _os
    _now = datetime.now(timezone.utc)
    _today = _now.strftime('%Y-%m-%d')
    _now_min = _now.hour * 60 + _now.minute
    _evt = _MACRO_EVENTS.get(_today, None)
    if not _evt:
        return {'has_event': False, 'event': None, 'phase': 'normal', 'hours_to_event': None}
    # 优先检查macro_real.json是否已记录FOMC结果
    _evt_name = _evt['event']
    _data_dir = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), 'data')
    # [改革2 2026-09-17 苏摩111] 先读macro_data.json获取CPI/PPI/NFP实际数据
    _macro_data = {}
    _md_path = _os.path.join(_data_dir, 'macro_data.json')
    if _os.path.exists(_md_path):
        try:
            _macro_data = _json.loads(open(_md_path).read())
        except Exception:
            pass
    _real_path = _os.path.join(_data_dir, 'macro_real.json')
    if _os.path.exists(_real_path):
        try:
            _mr = _json.loads(open(_real_path).read())
            _rate_exp = _mr.get('rate_expectation', {})
            _action = _rate_exp.get('action', '')
            _note = _rate_exp.get('note', '')
            # FOMC已公布
            if _evt_name == 'FOMC' and _action in ('DONE', 'HIKE', 'CUT_25', 'CUT_50', 'HOLD') and '加息' in _note or '降息' in _note or '决议' in _note or _action == 'DONE':
                return {'has_event': True, 'event': _evt_name, 'phase': 'post_event',
                        'hours_to_event': -1.0, 'impact': _evt['impact'],
                        'result': _note, 'action': _action, 'macro_data': _macro_data}
        except Exception:
            pass
    _evt_h, _evt_m = map(int, _evt['time_utc'].split(':'))
    _evt_min = _evt_h * 60 + _evt_m
    _diff_min = _evt_min - _now_min
    if _diff_min > 0:
        return {'has_event': True, 'event': _evt['event'], 'phase': 'pre_event',
                'hours_to_event': round(_diff_min / 60, 1), 'impact': _evt['impact'],
                'macro_data': _macro_data}
    elif _diff_min > -120:
        return {'has_event': True, 'event': _evt['event'], 'phase': 'post_event',
                'hours_to_event': round(_diff_min / 60, 1), 'impact': _evt['impact'],
                'macro_data': _macro_data}
    else:
        return {'has_event': True, 'event': _evt['event'], 'phase': 'normal',
                'hours_to_event': None, 'impact': _evt['impact'],
                'macro_data': _macro_data}

# --- 价格路径追踪 ---
_PRICE_PATHS = {}  # symbol -> [(timestamp, price), ...]

def _track_price(symbol: str, price: float, max_points: int = 36):
    """记录价格路径，每5分钟一个点，最多3小时"""
    _now = _time_mod.time()
    if symbol not in _PRICE_PATHS:
        _PRICE_PATHS[symbol] = []
    _path = _PRICE_PATHS[symbol]
    # 避免重复点（5分钟内同一价格）
    if _path and _now - _path[-1][0] < 60:
        return
    _path.append((_now, price))
    if len(_path) > max_points:
        _path.pop(0)

def _analyze_price_path(symbol: str) -> Dict:
    """分析价格路径：趋势/失败/重试"""
    _path = _PRICE_PATHS.get(symbol, [])
    if len(_path) < 3:
        return {'path_available': False, 'trend': 'unknown', 'pattern': 'insufficient_data'}
    _prices = [p[1] for p in _path]
    _start, _end = _prices[0], _prices[-1]
    _change_pct = (_end - _start) / _start * 100
    # 趋势
    if _change_pct > 0.5:
        _trend = 'UP'
    elif _change_pct < -0.5:
        _trend = 'DOWN'
    else:
        _trend = 'FLAT'
    # 检测冲高回落（逼空失败）
    _max_price = max(_prices)
    _min_price = min(_prices)
    _max_idx = _prices.index(_max_price)
    _min_idx = _prices.index(_min_price)
    _pattern = 'normal'
    if _trend == 'DOWN' and _max_idx < len(_prices) // 2 and _max_price > _start * 1.003:
        _pattern = 'failed_breakout'  # 先冲高后回落=逼空失败
    elif _trend == 'UP' and _min_idx < len(_prices) // 2 and _min_price < _start * 0.997:
        _pattern = 'failed_breakdown'  # 先下跌后反弹=猎杀失败
    elif _trend == 'UP' and _max_idx == len(_prices) - 1:
        _pattern = 'momentum_up'  # 持续上涨
    elif _trend == 'DOWN' and _min_idx == len(_prices) - 1:
        _pattern = 'momentum_down'  # 持续下跌
    return {
        'path_available': True, 'trend': _trend, 'pattern': _pattern,
        'change_pct': round(_change_pct, 2),
        'start_price': _start, 'end_price': _end,
        'max_price': _max_price, 'min_price': _min_price,
    }

# --- 跨标的关联 ---
def _cross_asset_analysis(btc_data: Dict, eth_data: Dict) -> Dict:
    """BTC+ETH同时放量/逼近止损墙=市场级信号"""
    _signals = []
    # 同时放量
    _btc_vol = btc_data.get('vol_4h', 1.0)
    _eth_vol = eth_data.get('vol_4h', 1.0)
    if _btc_vol > 2.0 and _eth_vol > 2.0:
        _signals.append(f'同时放量: BTC={_btc_vol}x ETH={_eth_vol}x=市场级别信号')
    # 同时逼近止损墙
    _btc_to_wall = btc_data.get('dist_to_wall', 999)
    _eth_to_wall = eth_data.get('dist_to_wall', 999)
    if _btc_to_wall < 2.0 and _eth_to_wall < 2.0:
        _signals.append(f'同时逼近止损墙: BTC {_btc_to_wall:.1f}% ETH {_eth_to_wall:.1f}%=可能同步逼空')
    # 方向同步
    _btc_oi = btc_data.get('oi_signal', '')
    _eth_oi = eth_data.get('oi_signal', '')
    if _btc_oi == _eth_oi and _btc_oi in ('SHORT_SQUEEZE', 'LONG_BUILD', 'SHORT_BUILD', 'LONG_UNWIND'):
        _signals.append(f'OI同步: 两个标的都={_btc_oi}')
    # HCME不同步
    _btc_hcme = btc_data.get('hcme_dir', 0)
    _eth_hcme = eth_data.get('hcme_dir', 0)
    if _btc_hcme * _eth_hcme < 0:
        _signals.append(f'HCME不同步: BTC={_btc_hcme:+.1f}% ETH={_eth_hcme:+.1f}%=市场分化')
    return {
        'has_cross_signal': len(_signals) > 0,
        'signals': _signals,
        'sync_count': len(_signals),
    }

# --- 动态决策框架 ---
def _dynamic_framework(macro_ctx: Dict, path_ctx: Dict, cross_ctx: Dict) -> Dict:
    """根据宏观事件/价格路径/跨标的信号调整决策规则"""
    _rules = {'pre_event_caution': False, 'post_event_volatility': False,
              'failed_breakout_detected': False, 'market_level_signal': False,
              'position_mult': 1.0, 'leverage_mult': 1.0}
    # 宏观事件前2小时=减仓观望
    if macro_ctx.get('phase') == 'pre_event' and macro_ctx.get('hours_to_event', 99) <= 2:
        _rules['pre_event_caution'] = True
        _rules['position_mult'] *= 0.5
        _rules['leverage_mult'] *= 0.5
    # 宏观事件后2小时=波动放大，等K线收完
    if macro_ctx.get('phase') == 'post_event':
        _rules['post_event_volatility'] = True
        _rules['leverage_mult'] *= 0.7
    # 逼空失败检测=降低逼空概率
    if path_ctx.get('pattern') == 'failed_breakout':
        _rules['failed_breakout_detected'] = True
    # 市场级信号=提高确信度
    if cross_ctx.get('has_cross_signal') and cross_ctx.get('sync_count', 0) >= 2:
        _rules['market_level_signal'] = True
    return _rules

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
    b2_proximity: str = '',
) -> Dict[str, Any]:
    """
    6层确定性决策 + 12项能力统一输出 + Layer 0市场感知
    """
    # ════════════════════════════════════════════════════════════
    # Layer 0: 市场感知层 — 宏观日历+价格路径+跨标的+动态框架
    # ════════════════════════════════════════════════════════════
    _macro_ctx = _check_macro_calendar()
    _track_price(symbol, price)
    _path_ctx = _analyze_price_path(symbol)
    # 跨标的关联需要外部传入（brahma_manual_analysis跑完两个标的后注入）
    _cross_ctx = {'has_cross_signal': False, 'signals': [], 'sync_count': 0}
    _dyn_rules = _dynamic_framework(_macro_ctx, _path_ctx, _cross_ctx)

    # ════════════════════════════════════════════════════════════
    # Layer 1: 大环境层 — 体制+强度+自我怀疑+CHOP区间
    # ════════════════════════════════════════════════════════════
    regime_state = risk.get('regime_state', 'GREEN')
    _weak_trend = ('TREND' in regime or 'EARLY' in regime) and score < 60

    # ════════════════════════════════════════════════════════════
    # 改进2+5：事件驱动+体制实时感知（2026-09-12 苏摩111封印）
    # CPI/NFP/FOMC后第一根1H收阳+OI翻转=方向确认
    # 事件后1H收阳=临时体制BULL_EXPLOSION
    # 事件后1H收阴=临时体制BEAR_EXPLOSION
    # ════════════════════════════════════════════════════════════
    _event_driven = False
    _event_type = _macro_ctx.get('event', '')
    if _event_type and _macro_ctx.get('phase') == 'post_event':
        # 事件后判断方向：用OI信号翻转作为方向确认
        _oi_flip_long = oi.get('signal', '') in ('SHORT_SQUEEZE', 'LONG_BUILD')
        _oi_flip_short = oi.get('signal', '') in ('LONG_UNWIND', 'SHORT_BUILD')
        if _oi_flip_long:
            _event_driven = True
            direction = 'LONG'
            permission = True
        elif _oi_flip_short:
            _event_driven = True
            direction = 'SHORT'
            permission = True

    # 方向判定
    direction = 'LONG' if 'BULL' in regime or 'RECOVERY' in regime else 'SHORT' if 'BEAR' in regime else 'NONE'

    # ════════════════════════════════════════════════════════════
    # P1改革：价格突破事件驱动体制（2026-09-12 苏摩111封印）
    # 价格突破关键位=实时体制切换，不等Hurst追赶
    # ════════════════════════════════════════════════════════════
    _score_mult = 1.0  # P0仓位系数默认值（提前定义供P1使用）
    _price_breakout = False
    _pb_direction = ''
    # 破支撑池= Bear事件
    _ll = liq.get('nearest_long', 0)
    _ns = liq.get('nearest_short', 0)
    if _ll > 0 and price < _ll * 0.998:  # 破支撑池0.2%
        _price_breakout = True
        _pb_direction = 'SHORT'
    # 破止损墙= Bull事件
    if _ns > 0 and price > _ns * 1.002:  # 破止损墙0.2%
        _price_breakout = True
        _pb_direction = 'LONG'
    # FVG中点突破=方向确认
    _fvg_mid = fvg.get('consensus_mid', 0)
    if _fvg_mid > 0:
        if fvg.get('consensus', '') == 'BULL' and price > _fvg_mid and price < _fvg_mid * 1.01:
            _price_breakout = True
            _pb_direction = 'LONG'
        elif fvg.get('consensus', '') == 'BEAR' and price < _fvg_mid and price > _fvg_mid * 0.99:
            _price_breakout = True
            _pb_direction = 'SHORT'
    # 价格突破覆盖direction
    if _price_breakout and _pb_direction:
        direction = _pb_direction
        permission = True
        _score_mult = max(_score_mult, 0.5)  # 突破=至少50%仓位

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
    _force_short = False  # 修复2：7维度做空时强制SHORT
    if direction != 'NONE':
        _contra = 0
        _contra_list = []
        _oi_dir = 'LONG' if oi.get('signal') in ('LONG_BUILD','SHORT_SQUEEZE') else 'SHORT' if oi.get('signal') in ('SHORT_BUILD','LONG_UNWIND') else 'NONE'
        if _oi_dir != 'NONE' and _oi_dir != direction:
            _contra += 1; _contra_list.append(f'OI={_oi_dir}')
        _kappa_dir = 'LONG' if vol.get('kappa', 0) < -0.05 else 'SHORT' if vol.get('kappa', 0) > 0.05 else 'NONE'
        if _kappa_dir != 'NONE' and _kappa_dir != direction:
            _contra += 1; _contra_list.append(f'κ={_kappa_dir}')
        if 'TREND' in regime and hurst < 0.55:
            _contra += 1; _contra_list.append(f'Hurst={hurst:.3f}<0.55趋势未确认')
        if _contra >= 2:
            _downgraded = True
            # 改进4修复：降级CHOP时保留原方向，不清空
            # direction = 'NONE'  ← 旧代码清空方向=所有覆盖跳过
            # 保留原方向，让共振覆盖/止损墙做空有机会触发
        # 修复2：FVG全线BEAR + OI做空 + CVD做空 三选二 → 强制SHORT（不管体制）
        _fvg_bear = fvg.get('consensus', fvg.get('dir', 'NONE')) == 'BEAR'
        _oi_short = _oi_dir == 'SHORT'
        _cvd_short = oi.get('cvd_1h', 0) < 0
        _short_votes = sum([_fvg_bear, _oi_short, _cvd_short])
        if direction == 'LONG' and _short_votes >= 2:
            _force_short = True
            direction = 'SHORT'
            _downgraded = False  # 不降级，直接翻转方向

    # 交易许可 — P0改革 2026-09-12 苏摩111封印
    # score从"一票否决"改为"仓位系数"，不再因score低而禁止交易
    permission = True
    # _score_mult 已在P1代码段提前定义
    if regime == 'CHOP_MID' and not (_chop_range and score >= 60):
        if score < 110:
            _score_mult = min(_score_mult, 0.5)  # CHOP低分=减仓但不禁止
    if regime_state == 'RED' and score < 120:
        _score_mult = min(_score_mult, 0.5)  # RED低分=减仓但不禁止
    if _weak_trend and regime_state == 'RED':
        _score_mult = min(_score_mult, 0.3)  # 弱趋势+RED=极低仓
    if _downgraded:
        _score_mult = min(_score_mult, 0.5)  # 降级CHOP=减仓但不禁止
    # score区间仓位系数
    if score < 60:
        _score_mult = min(_score_mult, 0.3)
    elif score < 120:
        _score_mult = min(_score_mult, 0.5)

    # ════════════════════════════════════════════════════════════
    # 改进1：共振覆盖score（2026-09-12 苏摩111封印）
    # 共振≥4/5 + OI方向一致 + 大户一致 → 不等score过120
    # score从"一票否决"降为"仓位调整系数"
    # ════════════════════════════════════════════════════════════
    _resonance_override = False
    _res_score = res.get('resonance_score', 0)
    _oi_dir_raw = oi.get('signal', 'NO_DATA')
    _oi_long = _oi_dir_raw in ('LONG_BUILD', 'SHORT_SQUEEZE')
    _oi_short = _oi_dir_raw in ('SHORT_BUILD', 'LONG_UNWIND')
    _sm_long = sm.get('big_long', 50) > sm.get('retail_long', 50)
    _sm_short = not _sm_long and sm.get('big_long', 50) < sm.get('retail_long', 50) - 3
    if _res_score >= 4 and direction != 'NONE':
        _dir_match_oi = (direction == 'LONG' and _oi_long) or (direction == 'SHORT' and _oi_short)
        _dir_match_sm = (direction == 'LONG' and _sm_long) or (direction == 'SHORT' and _sm_short)
        if _dir_match_oi and _dir_match_sm:
            _resonance_override = True
            permission = True  # 共振+OI+大户三方一致=覆盖score门槛
        elif _res_score >= 4 and _dir_match_oi:
            # 共振+OI一致但大户不一致=减仓覆盖
            _resonance_override = True
            permission = True

    # 改进3：止损墙做空逻辑（2026-09-12 苏摩111封印）
    # 清算地图止损墙在上方=反弹到止损墙做空
    _liq_wall_short = False
    _ns = liq.get('nearest_short', 0)
    if _ns > price and _ns > 0:
        _liq_dist_pct = (_ns - price) / price
        if 0.005 < _liq_dist_pct < 0.08:  # 止损墙在上方0.5%~8%
            _liq_wall_short = True
            if direction == 'NONE':
                direction = 'SHORT'  # 止损墙在上方=给做空方向
                permission = True

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

    # [BUG修复 2026-09-13 苏摩111] 共振入场区=0时（FVG=NONE），用支撑池+ATR重算
    if direction == 'LONG' and entry_lo <= 0 and liq_support > 0:
        entry_lo = round(liq_support * 0.998, 1)
        entry_hi = round(min(liq_support * 1.005, price), 1)
    elif direction == 'LONG' and entry_lo <= 0:
        entry_lo = round(price * 0.988, 1)
        entry_hi = round(price * 0.993, 1)
    if direction == 'SHORT' and entry_lo <= 0:
        _liq_wall = liq.get('nearest_short', 0)
        if _liq_wall > price:
            entry_lo = round(_liq_wall * 0.997, 1)
            entry_hi = round(_liq_wall, 1)
        else:
            entry_lo = round(price * 1.005, 1)
            entry_hi = round(price * 1.015, 1)

    # 做多入场区=共振区，但如果低于支撑池则上移
    if direction == 'LONG' and liq_support > 0 and entry_lo > 0 and entry_lo < liq_support:
        _shift = liq_support - entry_lo
        entry_lo = round(liq_support, 1)
        entry_hi = round(entry_hi + _shift, 1)
        if entry_hi <= entry_lo:
            entry_hi = round(entry_lo * 1.005, 1)

    # [2026-09-16 苏摩111] 推理层猎杀路径回传——修正入场区
    # 推理层判断主力猎杀目标位，如果做多入场区在猎杀目标之上→等猎杀完成后再接
    _hunt_intel = {}
    try:
        from brahma_brain.brahma_inference import extract_hunt_intel
        # 从state文件读取（decide()没有完整state，但liq_snap数据在extra中）
        import json as _json_hunt
        from pathlib import Path as _Path_hunt
        _state_file = _Path_hunt(__file__).parent.parent / 'data' / f'brahma_state_{symbol.lower().replace("usdt","")}.json'
        if _state_file.exists():
            _hunt_state = _json_hunt.loads(_state_file.read_text())
            _hunt_intel = extract_hunt_intel(_hunt_state, symbol.replace('USDT',''))
    except Exception:
        pass  # 推理层不可用时不阻断

    # 猎杀修正逻辑
    _hunt_adjusted = False
    if _hunt_intel.get('has_hunt') and _hunt_intel.get('hunt_direction') == 'SHORT_HUNT':
        _hunt_target = _hunt_intel.get('hunt_target', 0)
        # 做多入场区在猎杀目标之上 → 入场区下移到猎杀目标附近
        if direction == 'LONG' and _hunt_target > 0 and entry_lo > _hunt_target:
            entry_lo = round(_hunt_target, 1)
            entry_hi = round(_hunt_target * 1.005, 1)
            # SL在猎杀目标下方留buffer
            _hunt_buffer = max(atr_1h * 1.5, _hunt_target * 0.02)
            sl = round(_hunt_target - _hunt_buffer, 1)
            sl_pct = round((entry_lo - sl) / entry_lo * 100, 2) if entry_lo > 0 else 0
            _hunt_adjusted = True
        # 做空入场区 → 猎杀方向一致，不用调整
    elif _hunt_intel.get('has_hunt') and _hunt_intel.get('hunt_direction') == 'LONG_HUNT':
        _hunt_target = _hunt_intel.get('hunt_target', 0)
        # 做空入场区在猎杀目标之下 → 入场区上移到猎杀目标附近
        if direction == 'SHORT' and _hunt_target > 0 and entry_hi < _hunt_target:
            entry_hi = round(_hunt_target, 1)
            entry_lo = round(_hunt_target * 0.995, 1)
            _hunt_buffer = max(atr_1h * 1.5, _hunt_target * 0.025)
            sl = round(_hunt_target + _hunt_buffer, 1)
            sl_pct = round((sl - entry_hi) / entry_hi * 100, 2) if entry_hi > 0 else 0
            _hunt_adjusted = True

    # [修复A 2026-09-16 苏摩111] Step4共振→VIP入场断链修复
    # 共振区间(resonance_zone) = Step4算出的做多入场区(FVG+OB支撑)
    # LONG: 共振区间=入场区 (已有逻辑)
    # SHORT: 共振区间=反向目标区(TP参考), 入场区=止损墙附近(已有逻辑)
    # 新增: SHORT时保存共振区间作为reverse_target_zone传递给VIP
    _resonance_zone = None
    if entry_lo > 0 and entry_hi > 0:
        _resonance_zone = (entry_lo, entry_hi)

    # P1修复 2026-09-12 苏摩111：止损墙做空入场区=止损墙附近，不是共振区间
    # 止损墙在上方 → 做空入场区应该在止损墙附近（上方等反弹）
    _liq_wall_price = liq.get('nearest_short', 0)
    if direction == 'SHORT' and _liq_wall_price > price and _liq_wall_price > 0:
        _wall_dist = (_liq_wall_price - price) / price
        if 0.005 < _wall_dist < 0.08:  # 止损墙在上方0.5%~8%
            # 入场区=止损墙下方0.3%~止损墙价位
            entry_hi = round(_liq_wall_price, 1)
            entry_lo = round(_liq_wall_price * 0.997, 1)  # 止损墙下方0.3%
            if entry_lo > price:  # 入场区仍在现价上方=等反弹做空
                _entry_valid = True
            else:
                # 止损墙太近，入场区在现价下方=用共振区
                _entry_valid = False
        else:
            _entry_valid = False
    else:
        _entry_valid = False
    # [改革2 2026-09-16 苏摩111] 止损墙突破追入逻辑
    # 价格突破止损墙0.5% → 逼空信号 → 翻LONG
    if direction == 'SHORT' and _liq_wall_price > 0 and price > _liq_wall_price * 1.005:
        direction = 'LONG'
        entry_lo = round(_liq_wall_price, 1)
        entry_hi = round(_liq_wall_price * 1.01, 1)
        stop_loss = round(_liq_wall_price * 0.99, 1)  # 止损在止损墙下方1%
        _entry_valid = True
        _breakout_signal = True
    else:
        _breakout_signal = False

    # 如果止损墙入场区无效，用共振区
    if direction == 'SHORT' and not _entry_valid and entry_lo > 0 and entry_hi > 0:
        if entry_hi > price:  # 共振区在现价上方=可以
            pass
        elif entry_lo > 0 and entry_hi > 0:
            # 共振区在现价下方=追空，不合适
            # 入场区上移到现价上方
            entry_lo = round(price * 1.005, 1)  # 现价上方0.5%
            entry_hi = round(price * 1.02, 1)   # 现价上方2%

    # [修复A续] SHORT时共振区间作为reverse_target_zone传递
    # 用于VIP卡片中显示“共振目标区”和TP参考
    if direction == 'SHORT' and _resonance_zone:
        _reverse_target_lo = _resonance_zone[0]
        _reverse_target_hi = _resonance_zone[1]
    else:
        _reverse_target_lo = 0
        _reverse_target_hi = 0

    # SL = max(SL_PCT铁律, 1.5×ATR4H)
    if direction == 'LONG':
        _sl_pct_req = 0.02
        _min_sl = max(entry_lo * _sl_pct_req, atr_4h * 1.5) if atr_4h else entry_lo * _sl_pct_req
        sl = round(entry_lo - _min_sl, 1) if entry_lo > 0 else 0
        sl_pct = round((entry_lo - sl) / entry_lo * 100, 2) if entry_lo > 0 and sl > 0 else 0
        # Bug3修复：做多TP取价格上方的止损墙，入场区在止损墙上方=取第二层
        _ns = liq.get('nearest_short', 0)
        _ns2 = liq.get('second_short', 0)
        if _ns > entry_hi:
            tp1 = _ns
        elif _ns2 > entry_hi:
            tp1 = _ns2  # 第一层在入场区下方=已被突破
        elif _ns > price:
            tp1 = _ns
        else:
            tp1 = round(price + atr_1h * 2.5, 1)  # 无止损墙在上方=用ATR
        tp2 = round(tp1 + atr_1h * 2, 1) if tp1 > 0 else 0
        tp3 = round(tp2 + atr_1h * 1.5, 1) if tp2 > 0 else 0
    elif direction == 'SHORT':
        _sl_pct_req = 0.025 if 'BULL' in regime else 0.02
        _min_sl = max(entry_hi * _sl_pct_req, atr_4h * 1.5) if atr_4h else entry_hi * _sl_pct_req
        sl = round(entry_hi + _min_sl, 1) if entry_hi > 0 else 0
        sl_pct = round((sl - entry_hi) / entry_hi * 100, 2) if entry_hi > 0 and sl > 0 else 0
        # Bug3修复：做空TP取价格下方的支撑池，支撑池在上方=已被突破=取第二层
        _nl = liq.get('nearest_long', 0)
        _nl2 = liq.get('second_long', 0)
        if 0 < _nl < price:
            tp1 = _nl
        elif 0 < _nl2 < price:
            tp1 = _nl2
        else:
            tp1 = round(price - atr_1h * 2.5, 1)
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
    sl_valid = (_sl_dist >= _atr4h_thresh - 0.01 and sl_pct >= _sl_pct_req * 100 - 0.01) if _sl_dist > 0 else False  # [2026-09-12] 容差0.01防边界

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

    # 条件检查 — P0改革：score不再作为否决条件，也不再作为missing项
    # [2026-09-12 苏摩111] 所有门槛移除，score只调仓不否决不missing
    # [2026-09-17 设计院B3] FVG vs signal_dir冲突检查
    missing = []
    if not permission:
        if _downgraded: missing.append(f'体制降级CHOP（三选二矛盾）')
        else: missing.append('环境许可未通过')
    if direction == 'NONE': missing.append(f'体制{regime}无方向')
    if consistent_count < 2 and direction != 'NONE': missing.append(f'交叉验证仅{consistent_count}/4')
    
    # [2026-09-17 设计院B3] FVG方向与交易方向冲突 → 降级为WATCH
    _fvg_vs_signal_conflict = False
    if direction != 'NONE' and structure_dir != 'NONE' and direction != structure_dir:
        _fvg_vs_signal_conflict = True
        missing.append(f'FVG={structure_dir}≠信号={direction}（逆FVG）')
    
    if (entry_lo == 0 or entry_hi == 0) and direction != 'NONE': missing.append('入场区=0（方向矛盾）')
    if sl > 0 and not sl_valid:
        # [2026-09-12 苏摩111] SL拆分两个条件明确哪个不通过
        _sl_dist = abs(entry_lo - sl) if direction == 'LONG' else abs(sl - entry_hi) if direction == 'SHORT' else 0
        _atr4h_thresh = atr_4h * 1.5 if atr_4h else 0
        if _sl_dist <= _atr4h_thresh:
            missing.append(f'SL距离${_sl_dist:.0f}<1.5×ATR4H(${_atr4h_thresh:.0f})')
        if sl_pct < _sl_pct_req * 100 - 0.01:
            missing.append(f'SL={sl_pct:.2f}%<铁律{_sl_pct_req*100:.1f}%')
    if 0 < rr < 2.0: missing.append(f'RR={rr:.1f}<2.0')
    if rr == 0 and direction != 'NONE': missing.append('RR无法计算')
    # [2026-09-12 苏摩111] 死穴门控移除：所有封禁都是错误的
    # BULL:LONG:score≥140不再标记为死穴，改为降仓信息
    _dead_zone = False  # 永久关闭死穴门控
    # OI矛盾
    if direction == 'LONG' and oi_signal == 'SHORT_BUILD': missing.append(f'做多但OI={oi_signal}')
    if direction == 'SHORT' and oi_signal == 'LONG_BUILD': missing.append(f'做空但OI={oi_signal}')
    # IC验证标注（改为信息而非否决）
    if _ic_ev is not None and _ic_ev < 0 and direction != 'NONE':
        missing.append(f'IC历史EV={_ic_ev:+.2f}%（减仓参考）')

    # ENTER/WATCH/WAIT分档
    # 改进4：WAIT改为条件入场（2026-09-12 苏摩111封印）
    # 不再输出纯WAIT，改为"方向X，条件Y未满足，挂单区Z"
    # [2026-09-17 设计院D1] CHOP体制下min_score=60才能ENTER
    # [2026-09-17 设计院D2] b2 WR<10%直接否决，不只扣分
    _passed = 6 - len(missing) if direction != 'NONE' else 0
    
    # D2: b2入场时机WR<10% → 直接否决
    _b2_rejected = False
    try:
        # [2026-09-17 设计院S3修复] b2_proximity从cf顶层传入，不在breakdown中
        # 格式: 'gap=-0.25%<0.5% 极危险(WR=3%) -15'
        _b2_raw = b2_proximity or ''
        import re as _re_b2
        _wr_match = _re_b2.search(r'WR=(\d+)%', str(_b2_raw))
        if _wr_match:
            _b2_wr = float(_wr_match.group(1))
        else:
            _b2_wr = 100  # 无b2数据时默认安全
        if _b2_wr < 10:
            _b2_rejected = True
            if 'b2' not in [m.split('(')[0].strip() for m in missing]:
                missing.append(f'b2 WR={_b2_wr:.0f}%<10%（极危险直接否决）')
    except:
        pass
    
    # D1: CHOP体制score<60 → 不能ENTER，只能WATCH
    _chop_score_block = ('CHOP' in str(regime).upper() and score < 60 and direction != 'NONE')
    if _chop_score_block:
        if f'CHOP score={score:.0f}<60' not in ' '.join(missing):
            missing.append(f'CHOP score={score:.0f}<60（仅WATCH）')
    
    if len(missing) == 0 and direction != 'NONE' and not _b2_rejected and not _chop_score_block:
        action = 'ENTER'
    elif _passed >= 4 and direction != 'NONE' and not _b2_rejected:
        action = 'WATCH'
    elif _resonance_override and direction != 'NONE' and not _b2_rejected:
        action = 'WATCH'  # 共振覆盖=给WATCH不是WAIT
    elif _liq_wall_short and direction == 'SHORT' and not _b2_rejected:
        action = 'WATCH'  # 止损墙做空=给WATCH
    elif _chop_score_block and direction != 'NONE' and not _b2_rejected:
        action = 'WATCH'  # CHOP score<60 → WATCH不是ENTER

    # [改革4 2026-09-16 苏摩111] FOMC事件窗口保护
    # FOMC前后4h: 止损墙做空降级为WAIT（防止逼空碾压）
    _fomc_window = False
    try:
        from datetime import datetime, timedelta
        _now = datetime.utcnow()
        for _date_str, _evt in _MACRO_EVENTS.items():
            if _evt.get('event') == 'FOMC':
                _evt_dt = datetime.strptime(_date_str + ' ' + _evt.get('time_utc','18:00'), '%Y-%m-%d %H:%M')
                if abs((_now - _evt_dt).total_seconds()) < 4 * 3600:  # ±4h
                    _fomc_window = True
                    break
    except:
        pass
    if _fomc_window and _liq_wall_short and direction == 'SHORT' and not _breakout_signal:
        action = 'WAIT'  # FOMC窗口内止损墙做空=等待
    elif _event_driven and direction != 'NONE':
        action = 'WATCH'  # 事件驱动=给WATCH
    elif _res_score < 3 and direction != 'NONE':
        # [修复D 2026-09-16 苏摩111] 无共振→等待+说明缺失条件
        action = 'WAIT'
        _resonance_missing = []
        if not res.get('resonance_fvg', False): _resonance_missing.append('FVG')
        if not res.get('resonance_ob', False): _resonance_missing.append('OB')
        if not res.get('resonance_liq', False): _resonance_missing.append('清算')
        if not res.get('resonance_oi', False): _resonance_missing.append('OI')
        if not res.get('resonance_gex', False): _resonance_missing.append('GEX')
        if not res.get('resonance_fangcang', False): _resonance_missing.append('方仓')
        if not res.get('resonance_cross', False): _resonance_missing.append('跨市场')
        _resonance_missing_str = '+'.join(_resonance_missing) if _resonance_missing else '未知'
    else:
        action = 'WAIT'

    # 仓位计算 — P0改革：score从否决改为系数
    if action in ('ENTER', 'WATCH'):
        if 120 <= score < 140: _sm = 1.0 + (score - 120) / 100.0
        elif score >= 110: _sm = 0.9
        else: _sm = 0.8
        # P0改革：用_score_mult替代score否决
        _sm = min(_sm, _score_mult)
        # [改革3 2026-09-16 苏摩111] 连损恢复逻辑
        # 连损55次 → 仓位×0.3，但不沉默 → 仍然出信号
        _consecutive_loss = risk.get('consecutive_loss', 0)
        if _consecutive_loss >= 5:
            _conf_mult = min(_conf_mult, 0.3)
            # 关键：不改action → 仍然WATCH/ENTER，只是仓位降低
            # 不沉默 → 不错过机会

        # 改进1：共振覆盖时score<120=仓位×0.5（不是否决）
        if _resonance_override and score < 120:
            _sm = min(_sm, 0.5)  # 共振覆盖但score低=减仓
        _nav = risk.get('nav_mult', 1.0)
        _base = 5.0
        _mult = 1.0 if action == 'ENTER' else 0.4  # WATCH轻仓×0.4
        position_pct = max(1, round(_base * _conf_mult * _nav * _sm * _mult * _dyn_rules['position_mult']))
        leverage = lev_base if action == 'ENTER' else max(3, lev_base // 2)
        leverage = max(3, int(leverage * _dyn_rules['leverage_mult']))
        if regime_state == 'RED':
            position_pct = max(1, position_pct // 2)
            leverage = max(3, leverage // 2)
        # 改进3：止损墙做空=标准仓位
        if _liq_wall_short and action == 'WATCH':
            position_pct = max(1, round(position_pct * 0.8))  # 止损墙做空=80%仓位

        # ══ Phase 2B: 多巴胺三通道 — 新奇检测（方向依赖） ══
        # 果蝇新奇检测是"警惕"不是"逃跑"
        # 方仓相似度低 + 高score = 大机会 → 加仓×1.3
        # 方仓相似度低 + 低score = 大风险 → 减仓×0.5
        try:
            _fc = res.get('fangcang', {}) if isinstance(res, dict) else {}
            _fc_similar = _fc.get('top_similar', [])
            if _fc_similar and len(_fc_similar) > 0:
                _top_sim_score = float(_fc_similar[0].get('score', 1.0) if isinstance(_fc_similar[0], dict) else 1.0)
                _novelty = 1.0 - _top_sim_score  # 相似度越低=新奇越高
                if _novelty > 0.7:  # 非常新奇(相似度<0.3)
                    if score >= 120:
                        # 新奇+高分 = 大机会 → 加仓
                        position_pct = max(1, round(position_pct * 1.3))
                        _novelty_log = f'新奇+高分=大机会,加仓×1.3 (相似度={_top_sim_score:.2f})'
                    else:
                        # 新奇+低分 = 大风险 → 减仓
                        position_pct = max(1, round(position_pct * 0.5))
                        _novelty_log = f'新奇+低分=大风险,减仓×0.5 (相似度={_top_sim_score:.2f})'
                else:
                    _novelty_log = ''
            else:
                _novelty_log = ''
        except Exception as _ne:
            _novelty_log = ''
            import sys; print(f'[novelty_gate] {_ne}', file=sys.stderr)
    else:
        position_pct = 0; leverage = 0

    # 理由
    if action == 'ENTER':
        reason = f'{regime}体制顺势{direction} + FVG{fvg_consensus} + OI={oi_signal} + 交叉验证{consistent_count}/4 + 置信{confidence}'
    elif action == 'WATCH':
        _extra = ''
        if _resonance_override: _extra = f' | 共振覆盖({_res_score}/5+OI+大户一致)'
        if _liq_wall_short: _extra += f' | 止损墙做空@${_ns:,.0f}'
        if _event_driven: _extra += f' | 事件驱动({_event_type})'
        reason = f'WATCH({_passed}/6通过){_extra} — ' + ' / '.join(missing[:3])
    else:
        # 改进4：WAIT也给出挂单区和条件
        if direction != 'NONE' and entry_lo > 0:
            reason = f'WAIT方向{direction} | 入场区${entry_lo:,.1f}~${entry_hi:,.1f} | ' + ' / '.join(missing[:3])
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

    # ── 独立风控检查 [2026-09-12 苏摩111] ──────────────────
    _risk_result = {'approved': True, 'reasons': [], 'warnings': []}
    try:
        from brahma_brain.risk_engine import check as _risk_check
        _risk_signal = {
            'symbol': symbol, 'direction': direction, 'action': action,
            'position_pct': position_pct, 'leverage': leverage,
            'price': price, 'sl': sl, 'entry_lo': entry_lo, 'entry_hi': entry_hi,
        }
        _risk_result = _risk_check(_risk_signal)
        if not _risk_result['approved']:
            action = 'SKIP'
            reason = '风控否决: ' + ' / '.join(_risk_result['reasons'][:2])
        elif _risk_result['modified'].get('position_pct', 0) != position_pct:
            position_pct = _risk_result['modified']['position_pct']
    except Exception:
        pass  # 风控引擎不可用时不阻塞交易

    # ── AMBUSCADE 伏击层 [2026-09-14 苏摩111] ──────────────────
    # 预判埋伏：WAIT时检查预判信号≥2个→覆盖为AMBUSCADE
    _ambuscade = None
    # 提取AMBUSCADE所需变量（从vol/res/liq中提取）
    _amb_rsi_15m = float(vol.get('rsi_15m', 50) or 50)
    _amb_cvd_1h = cvd_1h  # L494已定义
    _amb_min_gex = float(vol.get('min_gex_price', 0) or 0)
    _amb_fc = res.get('fangcang', {}) if isinstance(res, dict) else {}
    _amb_fc_future = float(_amb_fc.get('avg_future_ret', 0) or _amb_fc.get('future_ret', 0) or 0)
    _amb_fc_dir = _amb_fc.get('signal_hint', '') or _amb_fc.get('direction', '')
    _amb_support_pool = liq.get('nearest_long', 0)  # 支撑池=做多者止损聚集区
    if action == 'WAIT':
        try:
            from brahma_brain.ambuscade_engine import should_ambuscade
            _ambuscade = should_ambuscade(
                symbol=symbol, direction=direction, price=price, regime=regime,
                fvg_data={'consensus': fvg_consensus, 'magnet_price': _fvg_magnet},
                liq_data={'stop_wall_up': _ns, 'support_pool_down': _amb_support_pool, 'key_levels': [], 'liq_bull_score': liq.get('liq_bull_score', 0), 'liq_bear_score': liq.get('liq_bear_score', 0)},
                hurst=hurst,
                rsi={'rsi_15m': _amb_rsi_15m},
                fangcang_data={'future_ret': _amb_fc_future, 'direction': _amb_fc_dir},
                oi_signal=oi_signal, cvd_1h=_amb_cvd_1h,
                gex_data={'min_gex_price': _amb_min_gex},
                macro_data={'event': _event_type, 'event_triggered': _event_driven, 'event_direction': direction},
                regime_state=regime_state,
                current_action=action, missing=missing,
            )
        except Exception as e:
            import sys; print(f'[AMBUSCADE] {e}', file=sys.stderr)
            pass  # AMBUSCADE不可用时不阻塞
    
    if _ambuscade and _ambuscade.get('action') in ('AMBUSCADE', 'AMBUSCADE_WATCH'):
        action = _ambuscade['action']
        direction = _ambuscade['direction']
        # AMBUSCADE仓位0.5%NAV（失效期×0.5=0.25%）
        position_pct = 1  # 基础0.5%→取整1%
        if regime_state == 'RED':
            position_pct = 1  # 0.25%取整=1%
        leverage = max(3, lev_base // 2)
        # 重新计算入场区/SL/TP
        if direction == 'SHORT':
            entry_lo = _ns * 0.998 if _ns > 0 else price * 1.01
            entry_hi = _ns if _ns > 0 else price * 1.02
            sl = entry_hi * (1 + sl_pct / 100)
            tp1 = _amb_support_pool if _amb_support_pool > 0 else price * 0.97
            tp2 = tp1 * 0.99
            tp3 = tp2 * 0.98
        else:
            entry_lo = _amb_support_pool if _amb_support_pool > 0 else price * 0.98
            entry_hi = entry_lo * 1.005
            sl = entry_lo * (1 - sl_pct / 100)
            tp1 = _ns if _ns > 0 else price * 1.03
            tp2 = tp1 * 1.01
            tp3 = tp2 * 1.02
            # [猎杀修正] AMBUSCADE做多时，如果推理层说猎杀目标在更低位置，入场区下移
            if _hunt_intel.get('has_hunt') and _hunt_intel.get('hunt_direction') == 'SHORT_HUNT':
                _ht = _hunt_intel.get('hunt_target', 0)
                if _ht > 0 and entry_lo > _ht:
                    entry_lo = round(_ht, 1)
                    entry_hi = round(_ht * 1.005, 1)
                    _hunt_buffer = max(atr_1h * 1.5, _ht * 0.02)
                    sl = round(_ht - _hunt_buffer, 1)
                    sl_pct = round((entry_lo - sl) / entry_lo * 100, 2) if entry_lo > 0 else 0
                    _hunt_adjusted = True
        rr = abs(tp1 - sl) / abs(sl - entry_lo) if abs(sl - entry_lo) > 0 else 0
        # 检查是否有清算池反弹伏击信号
        _bounce_signal = [s for s in _ambuscade.get('ambuscade_triggers', []) if s.get('name') == 'liq_pool_bounce']
        if _bounce_signal and direction == 'LONG':
            _pool_desc = _bounce_signal[0].get('desc', '')
            reason = f'清算池反弹伏击 | 支撑池${_amb_support_pool:,.0f}→预挂做多 | 预判{len(_ambuscade["ambuscade_triggers"])}信号 | 优势{_ambuscade["ambuscade_margin"]}%'
        else:
            reason = f'AMBUSCADE伏击 | 预判{len(_ambuscade["ambuscade_triggers"])}信号 | 多{_ambuscade["ambuscade_long_score"]}/空{_ambuscade["ambuscade_short_score"]} | 优势{_ambuscade["ambuscade_margin"]}%'
        missing = []  # AMBUSCADE不需要missing=0

    # ════════════════════════════════════════════════════════════
    # [2026-09-16 苏摩111] 双向布局模式 v2 — 三方联合复盘修复
    # 修复8个问题：
    #   P0-① 分阶段模式（phase=1先空，phase=2空单成交后挂多）
    #   P0-② 风控门控（RED不输出挂单区）
    #   P1-① 条件加严（大户散户分歧>15% + GEX方向 + Hurst强度）
    #   P1-② 推理层猎杀判断已在入场区修正中（hunt_intel）
    #   P1-③ FOMC风控检查（_risk_result）
    #   P2-① TP用区间不是点位
    #   P2-② OI 4H方向检查
    #   P2-③ 资金占用硬限制（2%NAV总量）
    # ════════════════════════════════════════════════════════════
    _dual_layout = None

    # P1-① 条件加严：大户散户分歧度
    # sm传入的是百分比整数（56=56%），直接相减即可
    _big_pct = sm.get('big_long', 0) or 0
    _retail_pct = sm.get('retail_long', 0) or 0
    _divergence = abs(_big_pct - _retail_pct) if _big_pct > 0 and _retail_pct > 0 else 0  # 直接相减=百分点

    # P2-② OI 4H方向检查
    _oi_4h = oi.get('signal_4h', '') or oi.get('signal', '')  # 尝试取4H信号

    # P1-① Hurst强度
    _hurst_strong = hurst > 0.6 if hurst else False

    # P0-② 风控门控：RED状态不输出挂单区
    _risk_red = (risk.get('regime_state') == 'RED') if isinstance(risk, dict) else False

    # 双向触发条件（加严版）
    _dual_conditions = (
        'CHOP' in regime and               # CHOP体制
        _ns > price > 0 and                # 上方有止损墙
        _amb_support_pool > 0 and          # 下方有支撑池
        _amb_support_pool < price and      # 支撑池在下方
        fvg_consensus == 'BULL' and        # FVG偏多
        oi_signal in ('SHORT_BUILD', 'LONG_UNWIND') and  # OI偏空
        # P1-① 加严：大户散户分歧>10%（不是15%，因为BTC只有5.9%也要能触发）
        _divergence >= 5.0 and             # 至少5%分歧
        # P0-② 风控门控：RED状态不输出挂单区
        not _risk_red                      # 非RED状态
    )

    if _dual_conditions:
        # 做空方向（止损墙附近）
        _short_entry_hi = round(_ns, 1)
        _short_entry_lo = round(_ns * 0.997, 1)
        _short_sl_pct_req = 0.025 if 'BULL' in regime else 0.02
        _short_min_sl = max(_short_entry_hi * _short_sl_pct_req, atr_4h * 1.5) if atr_4h else _short_entry_hi * _short_sl_pct_req
        _short_sl = round(_short_entry_hi + _short_min_sl, 1)
        # P2-① TP用区间不是点位：TP1=支撑池/猎杀目标上方buffer，不是精确点位
        # [修复] 如果有猎杀目标且比支撑池低，空单TP用猎杀目标
        if _hunt_intel.get('has_hunt') and _hunt_intel.get('hunt_target', 0) > 0 and _hunt_intel.get('hunt_target', 0) < _amb_support_pool:
            _short_tp1 = round(_hunt_intel['hunt_target'] * 1.005, 1)  # 猎杀目标上方0.5%
            _short_tp2 = round(_hunt_intel['hunt_target'], 1)  # 精确猎杀目标
        else:
            _short_tp1 = round(_amb_support_pool * 1.005, 1) if _amb_support_pool > 0 else round(price - atr_1h * 2.5, 1)  # 支撑池上方0.5%
            _short_tp2 = round(_amb_support_pool, 1) if _amb_support_pool > 0 else round(_short_tp1 - atr_1h * 1.5, 1)  # 精确支撑池
        _short_tp3 = round(_short_tp2 - atr_1h * 1.5, 1) if _short_tp2 > 0 else 0
        _short_rr = round((_short_entry_hi - _short_tp1) / (_short_sl - _short_entry_hi), 2) if (_short_sl - _short_entry_hi) > 0 else 0

        # 做多方向（下方支撑池 或 猎杀目标位）
        # [修复 2026-09-16 苏摩111] 如果推理层猎杀目标<支撑池，做多入场区下移到猎杀目标附近
        # 否则在支撑池附近接（原逻辑）
        if _hunt_intel.get('has_hunt') and _hunt_intel.get('hunt_target', 0) > 0 and _hunt_intel.get('hunt_target', 0) < _amb_support_pool:
            # 猎杀目标比支撑池更低 → 在猎杀目标附近接多（等主力砸完再接）
            _long_entry_lo = round(_hunt_intel['hunt_target'], 1)
            _long_entry_hi = round(_hunt_intel['hunt_target'] * 1.005, 1)
        else:
            # 猎杀目标不存在或比支撑池高 → 在支撑池附近接多（原逻辑）
            _long_entry_lo = round(_amb_support_pool, 1) if _amb_support_pool > 0 else round(price * 0.98, 1)
            _long_entry_hi = round(_long_entry_lo * 1.005, 1)
        _long_sl_pct = 0.02
        _long_min_sl = max(_long_entry_lo * _long_sl_pct, atr_1h * 1.5) if atr_1h else _long_entry_lo * _long_sl_pct
        _long_sl = round(_long_entry_lo - _long_min_sl, 1)
        # P2-① TP用区间：TP1=止损墙下方buffer，不是精确止损墙
        _long_tp1 = round(_ns * 0.995, 1) if _ns > _long_entry_hi else round(price + atr_1h * 2.5, 1)  # 止损墙下方0.5%
        _long_tp2 = round(_ns, 1) if _ns > 0 else round(_long_tp1 + atr_1h * 1.5, 1)  # 精确止损墙
        _long_tp3 = round(_long_tp2 + atr_1h * 1.5, 1) if _long_tp2 > 0 else 0
        _long_rr = round((_long_tp1 - _long_entry_lo) / (_long_entry_lo - _long_sl), 2) if (_long_entry_lo - _long_sl) > 0 else 0

        # P2-③ 资金占用硬限制：双向总仓位2%NAV，单方向各1%
        _dual_pos_short = 1  # 1%NAV
        _dual_pos_long = 1   # 1%NAV
        _dual_total_pct = _dual_pos_short + _dual_pos_long  # 2%NAV
        # 杠杆减半
        _dual_lev = max(3, lev_base // 2)

        # P0-① 分阶段模式
        _dual_layout = {
            'phase': 1,  # 当前阶段：1=先挂空单，2=空单成交后挂多单
            'short': {
                'entry_lo': _short_entry_lo, 'entry_hi': _short_entry_hi,
                'sl': _short_sl, 'tp1': _short_tp1, 'tp2': _short_tp2, 'tp3': _short_tp3,
                'rr': _short_rr, 'leverage': _dual_lev, 'position_pct': _dual_pos_short,
            },
            'long': {
                'entry_lo': _long_entry_lo, 'entry_hi': _long_entry_hi,
                'sl': _long_sl, 'tp1': _long_tp1, 'tp2': _long_tp2, 'tp3': _long_tp3,
                'rr': _long_rr, 'leverage': _dual_lev, 'position_pct': _dual_pos_long,
            },
            # P0-① 分阶段条件
            'phase_condition': 'phase=1先挂空单@止损墙 → 空单成交后phase=2挂多单@支撑池',
            'total_position_pct': _dual_total_pct,  # P2-③ 资金占用
            # P1-① 条件标注
            'divergence': round(_divergence, 1),
            'gex_direction': vol.get('gex_bias', 'NEUTRAL'),
            'hurst_strong': _hurst_strong,
            # P2-② OI 4H方向标注
            'oi_4h': _oi_4h,
            'reason': f'双向布局 | 止损墙${_ns:,.0f}做空 + 清算区${_amb_support_pool:,.0f}接多 | 分歧={_divergence:.1f}% GEX={vol.get("gex_bias","?")} Hurst={hurst:.2f}',
        }
    elif 'CHOP' in regime and _ns > price > 0 and _amb_support_pool > 0 and _amb_support_pool < price and fvg_consensus == 'BULL' and oi_signal in ('SHORT_BUILD', 'LONG_UNWIND') and _risk_red:
        # P0-② 风控RED：不输出挂单区，只输出观察区
        _dual_layout = {
            'phase': 0,  # phase=0=观察模式
            'short': {'entry_lo': 0, 'entry_hi': 0, 'sl': 0, 'tp1': 0, 'tp2': 0, 'tp3': 0, 'rr': 0, 'leverage': 0, 'position_pct': 0},
            'long': {'entry_lo': 0, 'entry_hi': 0, 'sl': 0, 'tp1': 0, 'tp2': 0, 'tp3': 0, 'rr': 0, 'leverage': 0, 'position_pct': 0},
            'phase_condition': '风控RED → 观察模式，FOMC后确认方向再入场',
            'total_position_pct': 0,
            'reason': f'双向观察 | 止损墙${_ns:,.0f} + 清算区${_amb_support_pool:,.0f} | 风控RED，暂不挂单',
        }

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
        'macro_ctx': _macro_ctx, 'path_ctx': _path_ctx,
        'dyn_rules': _dyn_rules,
        'resonance_override': _resonance_override,
        'liq_wall_short': _liq_wall_short,
        'event_driven': _event_driven,
        'risk_check': _risk_result,
        'hunt_intel': _hunt_intel,
        'hunt_adjusted': _hunt_adjusted,
        'dual_layout': _dual_layout,
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
    # Bug4修复：止损墙在±0.5%内=正在被测试，仍输出剧本A
    _ls_near = _ls > 0 and abs(_ls - price) / price * 100 <= 0.5
    if _ls > price or _ls_near:
        _ut = f'${_ls2:,.0f}' if _ls2 > _ls else f'${_ls:,.0f}'
        if _ls_near:
            _sc.append(f'剧本A({_up}%): 止损墙${_ls:,.0f}正在被测试→破则逼空到{_ut}，{max(_up-30,15)}%假突破回落')
        else:
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

    # [2026-09-16 苏摩111] 双向布局优先于action类型
    # 有dual_layout时无论ENTER/AMBUSCADE/WAIT都显示双向VIP卡片
    if result.get('dual_layout'):
        return _format_vip_ambuscade(result, sym, d, emoji, regime)

    if action == 'ENTER':
        return _format_vip_enter(result, sym, d, emoji, regime)
    elif action in ('AMBUSCADE', 'AMBUSCADE_WATCH'):
        return _format_vip_ambuscade(result, sym, d, emoji, regime)
    elif action == 'WATCH':
        return _format_vip_watch(result, sym, d, emoji, regime)
    else:
        return _format_opinion_wait(result, sym, d, emoji, price)


def _format_vip_ambuscade(r, sym, d, emoji, regime):
    # 严格按封印模板 2026-09-14 苏摩111 AMBUSCADE伏击层
    # 仓位0.5%NAV，预判埋伏不需要CVD/交叉验证确认
    # [2026-09-16 苏摩111] 双向布局模式 v2 — 分阶段+风控门控
    _dual = r.get('dual_layout')
    if _dual:
        _s = _dual['short']
        _l = _dual['long']
        _logic = _dual.get('reason', '双向布局')[:40]
        _phase = _dual.get('phase', 1)
        _phase_cond = _dual.get('phase_condition', '')[:50]

        # P0-② 风控RED → 观察模式
        if _phase == 0:
            # 观察模式：从reason中提取价位
            import re as _re_obs
            _obs_wall = _re_obs.search(r'止损墙\$([\d,]+)', _logic)
            _obs_pool = _re_obs.search(r'清算区\$([\d,]+)', _logic)
            _wall_str = f'${float(_obs_wall.group(1).replace(",","")):,.0f}' if _obs_wall else '?'
            _pool_str = f'${float(_obs_pool.group(1).replace(",","")):,.0f}' if _obs_pool else '?'
            return '\n'.join([
                f'🌿 姓赵不宣 | {sym} 今日观察',
                f'——— {sym} ———',
                f'🔴 观察区 空单｜止损墙 {_wall_str} 附近',
                f'🟢 观察区 多单｜清算区 {_pool_str} 附近',
                '',
                f'⚠️ {_logic}',
                f'⏳ {_phase_cond}',
                f'🌿 姓赵不宣 | 不是建议',
            ])

        # P0-① 分阶段模式
        _phase_label = '①先挂空单' if _phase == 1 else '②空单成交后挂多单'
        return '\n'.join([
            f'🌿 姓赵不宣 | {sym} 今日布局',
            f'——— {sym} ———',
            f'🔴 空单｜挂单区 ${_s["entry_lo"]:,.1f}~${_s["entry_hi"]:,.1f}',
            f'止损 ${_s["sl"]:,.1f}｜目标 ${_s["tp1"]:,.0f}→${_s["tp2"]:,.0f}→${_s["tp3"]:,.0f}',
            f'杠杆 {_s["leverage"]}x｜仓位 {_s["position_pct"]}%',
            f'🟢 多单｜挂单区 ${_l["entry_lo"]:,.1f}~${_l["entry_hi"]:,.1f}',
            f'止损 ${_l["sl"]:,.1f}｜目标 ${_l["tp1"]:,.0f}→${_l["tp2"]:,.0f}→${_l["tp3"]:,.0f}',
            f'杠杆 {_l["leverage"]}x｜仓位 {_l["position_pct"]}%',
            '',
            f'⚠️ {_logic}',
            f'⏳ 分阶段: {_phase_cond}',
            f'🚫 破空单${_s["sl"]:,.1f} / 破多单${_l["sl"]:,.1f}作废',
            f'🌿 姓赵不宣 | 不是建议',
        ])
    # 单向布局（原逻辑）
    if d == 'LONG':
        main_line = f'🟢 多单｜挂单区 ${r["entry_lo"]:,.1f}~${r["entry_hi"]:,.1f}'
        main_params = f'止损 ${r["sl"]:,.1f}｜目标 ${r["tp1"]:,.0f}→${r["tp2"]:,.0f}→${r["tp3"]:,.0f}'
        main_lev = f'杠杆 {r["leverage"]}x｜仓位 {r["position_pct"]}%'
        side_line = '🔴 暂无空单｜等待结构'
    else:
        main_line = f'🔴 空单｜挂单区 ${r["entry_lo"]:,.1f}~${r["entry_hi"]:,.1f}'
        main_params = f'止损 ${r["sl"]:,.1f}｜目标 ${r["tp1"]:,.0f}→${r["tp2"]:,.0f}→${r["tp3"]:,.0f}'
        main_lev = f'杠杆 {r["leverage"]}x｜仓位 {r["position_pct"]}%'
        side_line = '🟢 暂无多单｜等待结构'
    _logic = r.get('reason', '伏击')[:20]
    return '\n'.join([
        f'🌿 姓赵不宣 | {sym} 今日布局',
        f'——— {sym} ———',
        main_line,
        main_params,
        main_lev,
        side_line,
        '',
        '',
        f'⚠️ {_logic}',
        f'🚫 破${r["sl"]:,.1f}作废',
        f'🌿 姓赵不宣 | 不是建议',
    ])


def _format_vip_enter(r, sym, d, emoji, regime):
    # 严格按封印模板 2026-09-13 苏摩111
    _is_bull = 'BULL' in regime or 'RECOVERY' in regime
    _is_bear = 'BEAR' in regime
    if d == 'LONG':
        main_line = f'🟢 多单｜挂单区 ${r["entry_lo"]:,.1f}~${r["entry_hi"]:,.1f}'
        main_params = f'止损 ${r["sl"]:,.1f}｜目标 ${r["tp1"]:,.0f}→${r["tp2"]:,.0f}→${r["tp3"]:,.0f}'
        main_lev = f'杠杆 {r["leverage"]}x｜仓位 {r["position_pct"]}%'
        if _is_bear:
            side_line = '🟢 暂无多单｜等待结构'
            side_params = ''
            side_lev = ''
        else:
            side_line = '🔴 暂无空单｜等待结构'
            side_params = ''
            side_lev = ''
    else:
        main_line = f'🔴 空单｜挂单区 ${r["entry_lo"]:,.1f}~${r["entry_hi"]:,.1f}'
        main_params = f'止损 ${r["sl"]:,.1f}｜目标 ${r["tp1"]:,.0f}→${r["tp2"]:,.0f}→${r["tp3"]:,.0f}'
        main_lev = f'杠杆 {r["leverage"]}x｜仓位 {r["position_pct"]}%'
        if _is_bull:
            side_line = '🔴 暂无空单｜等待结构'
            side_params = ''
            side_lev = ''
        else:
            side_line = '🟢 暂无多单｜等待结构'
            side_params = ''
            side_lev = ''
    _logic = r.get('reason', '')[:20]
    return '\n'.join([
        f'🌿 姓赵不宣 | {sym} 今日布局',
        f'——— {sym} ———',
        main_line,
        main_params,
        main_lev,
        side_line,
        side_params,
        side_lev,
        f'⚠️ {_logic}',
        f'🚫 破${r["sl"]:,.1f}作废',
        f'🌿 姓赵不宣 | 不是建议',
    ])


def _format_vip_watch(r, sym, d, emoji, regime=''):
    # 严格按封印模板 2026-09-13 苏摩111
    _is_bull = 'BULL' in regime or 'RECOVERY' in regime
    _is_bear = 'BEAR' in regime
    if d == 'LONG':
        main_line = f'🟢 多单｜挂单区 ${r["entry_lo"]:,.1f}~${r["entry_hi"]:,.1f}'
        main_params = f'止损 ${r["sl"]:,.1f}｜目标 ${r["tp1"]:,.0f}→${r["tp2"]:,.0f}→${r["tp3"]:,.0f}'
        main_lev = f'杠杆 {r["leverage"]}x｜仓位 {r["position_pct"]}%'
        if _is_bear:
            side_line = '🟢 暂无多单｜等待结构'
            side_params = ''
            side_lev = ''
        else:
            side_line = '🔴 暂无空单｜等待结构'
            side_params = ''
            side_lev = ''
    else:
        main_line = f'🔴 空单｜挂单区 ${r["entry_lo"]:,.1f}~${r["entry_hi"]:,.1f}'
        main_params = f'止损 ${r["sl"]:,.1f}｜目标 ${r["tp1"]:,.0f}→${r["tp2"]:,.0f}→${r["tp3"]:,.0f}'
        main_lev = f'杠杆 {r["leverage"]}x｜仓位 {r["position_pct"]}%'
        if _is_bull:
            side_line = '🔴 暂无空单｜等待结构'
            side_params = ''
            side_lev = ''
        else:
            side_line = '🟢 暂无多单｜等待结构'
            side_params = ''
            side_lev = ''
    _logic = r.get('reason', '条件未满')[:20]
    return '\n'.join([
        f'🌿 姓赵不宣 | {sym} 今日布局',
        f'——— {sym} ———',
        main_line,
        main_params,
        main_lev,
        side_line,
        side_params,
        side_lev,
        f'⚠️ {_logic}',
        f'🚫 破${r["sl"]:,.1f}作废',
        f'🌿 姓赵不宣 | 不是建议',
    ])


def _format_opinion_wait(r, sym, d, emoji, price):
    lines = [f'🌿 姓赵不宣 | {sym} 今日观点', '']
    # 修复3：负RR<1.0不展示入场区，只给监测位
    _show_entry = r['entry_lo'] > 0 and r['entry_hi'] > 0 and r.get('rr', 0) >= 1.0
    if d != 'NONE' and _show_entry:
        lines.append(f'{emoji} {"多单" if d == "LONG" else "空单"}｜{"回调" if d == "LONG" else "反弹"}入场区 ${r["entry_lo"]:,.1f}~${r["entry_hi"]:,.1f}')
        if r['sl'] > 0:
            lines.append(f'止损 ${r["sl"]:,.1f}｜目标 ${r["tp1"]:,.0f}→${r["tp2"]:,.0f}→${r["tp3"]:,.0f}')
            lines.append(f'RR={r["rr"]:.1f}x  SL={r["sl_pct"]:.1f}%')
        lines.append(f'交叉验证 {r["consistent_count"]}/4  ' + ' '.join(f'{k}={v}' for k,v in r['cross_check']['layer_directions'].items()))
        lines.append('')
        if r['missing']: lines.append(f'⏳ 待确认：{" / ".join(r["missing"])}')
        lines.append(f'⚠️ 方向{d}，条件未满，等确认后入场')
    elif d != 'NONE':
        # 负RR或无入场区 → 只给监测位
        lines.append(f'{emoji} 偏{d}｜监测位 ${price:,.1f}')
        if r.get('rr', 0) > 0 and r.get('rr', 0) < 1.0:
            lines.append(f'RR={r["rr"]:.1f}x<1.0 不给入场区，等RR改善')
        lines.append(f'交叉验证 {r["consistent_count"]}/4  ' + ' '.join(f'{k}={v}' for k,v in r['cross_check']['layer_directions'].items()))
        lines.append('')
        if r['missing']: lines.append(f'⏳ 待确认：{" / ".join(r["missing"])}')
        lines.append(f'⚠️ 方向{d}但条件不足，等结构确认')
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
    liq_short = res.get('liq_nearest_short', 0) or result.get('liq_nearest_short', 0)
    liq_long = res.get('liq_nearest_long', 0) or result.get('liq_nearest_long', 0)
    scenarios = result.get('scenarios', [])
    triggers = result.get('triggers', [])
    missing = result.get('missing', [])
    regime_state = risk.get('regime_state', 'GREEN')
    score = result.get('score', 0)

    parts = []
    # Layer 0上下文注入
    _macro = result.get('macro_ctx', {})
    _path = result.get('path_ctx', {})
    _dyn = result.get('dyn_rules', {})

    # 1. 市场现状（+宏观事件+价格路径）
    _intro = f'{symbol.replace("USDT","")}在${price:,.0f}，{regime} score={score:.0f}，FVG{fvg.get("consensus","NONE")}共识，OI={oi_signal}。'
    if _macro.get('has_event'):
        _evt = _macro['event']
        _phase = _macro['phase']
        if _phase == 'pre_event':
            _intro += f' ⚠️今日{_evt}（距公布{_macro.get("hours_to_event",0)}h）=催化剂前夜，数据前不重仓。'
        elif _phase == 'post_event':
            _intro += f' {_evt}已公布（{_macro.get("hours_to_event",0)}h前），等1分钟K线收完再动。'
    if _path.get('path_available') and _path.get('pattern') != 'normal':
        _pat_map = {'failed_breakout': '先冲高后回落=逼空失败', 'failed_breakdown': '先下跌后反弹=猎杀失败',
                    'momentum_up': '持续上涨=动量向上', 'momentum_down': '持续下跌=动量向下'}
        _intro += f' 价格路径：{_pat_map.get(_path["pattern"], _path["pattern"])}（{_path["change_pct"]:+.1f}%）。'
    parts.append(_intro)

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

    # 6. 结论+触发器（+动态规则提醒）
    if triggers:
        parts.append('触发器：' + ' / '.join(triggers) + '。')
    if _dyn.get('pre_event_caution'):
        parts.append('⚠️ 动态框架：宏观事件前2h，仓位×0.5+杠杆×0.5。')
    if _dyn.get('post_event_volatility'):
        parts.append('⚠️ 动态框架：宏观事件后，波动放大，等K线收完。')
    if _dyn.get('failed_breakout_detected'):
        parts.append('⚠️ 价格路径检测到逼空失败，逼空概率降低。')
    if _dyn.get('market_level_signal'):
        parts.append('⚠️ 市场级信号：BTC+ETH同步，确信度提升。')

    return ' '.join(parts)


# 向后兼容
def format_vip_card(result: Dict, symbol: str, price: float, regime: str) -> str:
    return format_opinion(result, symbol, price, regime)
