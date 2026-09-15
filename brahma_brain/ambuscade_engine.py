"""
AMBUSCADE 伏击层 — 预判埋伏型决策引擎
2026-09-14 苏摩111 三方联合深度优化封印

核心认知：合约盈利需要预判+提前埋伏，不是事后追确认
触发条件：预判信号≥2个 → 加权投票 → 方向裁决
仓位：0.5%NAV（失效期×0.5=0.25%NAV）
不需要：CVD转正/交叉验证≥3/4/missing=0/score≥140

接入位置：trader_brain.py Layer 6 决策层，action判定之前
"""
from typing import Dict, List, Tuple, Optional
import json
import os
import time


# ── 预判信号权重 ──────────────────────────────────────────
SIGNAL_WEIGHTS = {
    'fvg_magnet':    0.8,  # FVG是核心结构信号
    'liq_hunt':      0.7,  # 清算地图是主力意图
    'hurst_rsi':     0.6,  # 趋势+超卖组合
    'fangcang':      0.7,  # 6.8年历史匹配
    'oi_cvd_div':    0.5,  # 背离是辅助信号
    'regime_break':  0.8,  # 体制切换是强信号
    'event_driven':  0.6,  # 事件是催化剂
    'gex_support':   0.4,  # 期权支撑
}


def detect_ambuscade_signals(
    symbol: str,
    direction: str,
    price: float,
    regime: str,
    fvg_data: Dict,
    liq_data: Dict,
    hurst: float,
    rsi: Dict,
    fangcang_data: Dict,
    oi_signal: str,
    cvd_1h: float,
    gex_data: Dict,
    macro_data: Dict,
    regime_state: str,
) -> List[Dict]:
    """
    检测8个预判信号，返回触发的信号列表。
    每个信号：{name, direction, weight, desc}
    """
    signals = []
    
    # 1. FVG磁铁+距离
    fvg_consensus = fvg_data.get('consensus', '')
    fvg_magnet = fvg_data.get('magnet_price', 0)
    if fvg_magnet > 0 and price > 0:
        dist_pct = abs(fvg_magnet - price) / price * 100
        if dist_pct < 3.0:  # 距磁铁<3%
            mag_dir = 'LONG' if fvg_magnet > price else 'SHORT'
            # 修正1：磁铁到目标=权重0
            if dist_pct < 0.5:
                signals.append({
                    'name': 'fvg_magnet', 'direction': mag_dir, 'weight': 0,
                    'desc': f'FVG磁铁已到目标=${fvg_magnet:,.0f}（拉力消失）'
                })
            else:
                signals.append({
                    'name': 'fvg_magnet', 'direction': mag_dir,
                    'weight': SIGNAL_WEIGHTS['fvg_magnet'],
                    'desc': f'FVG{fvg_consensus}磁铁${fvg_magnet:,.0f} 距{dist_pct:.1f}%'
                })
    
    # 2. 清算猎杀链
    stop_wall = liq_data.get('stop_wall_up', 0)
    support_pool = liq_data.get('support_pool_down', 0)
    if stop_wall > 0 and support_pool > 0 and price > 0:
        # 修正6：做空只在价格>止损墙-1%
        if price > stop_wall * 0.99:
            signals.append({
                'name': 'liq_hunt', 'direction': 'SHORT',
                'weight': SIGNAL_WEIGHTS['liq_hunt'],
                'desc': f'止损墙${stop_wall:,.0f}→猎杀做空'
            })
        elif price < support_pool * 1.01:
            signals.append({
                'name': 'liq_hunt', 'direction': 'LONG',
                'weight': SIGNAL_WEIGHTS['liq_hunt'],
                'desc': f'支撑池${support_pool:,.0f}→猎杀做多'
            })
    
    # 3. Hurst+RSI超卖/超买
    rsi_15m = rsi.get('rsi_15m', 50)
    if hurst > 0.5 and rsi_15m < 25:
        signals.append({
            'name': 'hurst_rsi', 'direction': 'LONG',
            'weight': SIGNAL_WEIGHTS['hurst_rsi'],
            'desc': f'H={hurst:.3f}+RSI={rsi_15m}超卖→回调做多'
        })
    elif hurst > 0.5 and rsi_15m > 75:
        signals.append({
            'name': 'hurst_rsi', 'direction': 'SHORT',
            'weight': SIGNAL_WEIGHTS['hurst_rsi'],
            'desc': f'H={hurst:.3f}+RSI={rsi_15m}超买→回调做空'
        })
    
    # 4. 方仓future_ret
    fc_future = fangcang_data.get('future_ret', 0)
    if abs(fc_future) > 3.0:
        fc_dir = 'LONG' if fc_future > 0 else 'SHORT'
        signals.append({
            'name': 'fangcang', 'direction': fc_dir,
            'weight': SIGNAL_WEIGHTS['fangcang'],
            'desc': f'方仓future={fc_future:+.1f}%→{fc_dir}'
        })
    
    # 5. OI+CVD背离（OI多但CVD空=诱多→做空）
    if oi_signal == 'LONG_BUILD' and cvd_1h < 0:
        signals.append({
            'name': 'oi_cvd_div', 'direction': 'SHORT',
            'weight': SIGNAL_WEIGHTS['oi_cvd_div'],
            'desc': f'OI=LONG但CVD={cvd_1h:.0f}→诱多→做空'
        })
    elif oi_signal == 'SHORT_BUILD' and cvd_1h > 0:
        signals.append({
            'name': 'oi_cvd_div', 'direction': 'LONG',
            'weight': SIGNAL_WEIGHTS['oi_cvd_div'],
            'desc': f'OI=SHORT但CVD={cvd_1h:.0f}→诱空→做多'
        })
    
    # 6. 体制突破（CHOP→BEAR or CHOP→BULL的关键位突破）
    # 价格在关键位附近±1%
    key_levels = liq_data.get('key_levels', [])
    if regime == 'CHOP_MID' and price > 0:
        for level in key_levels:
            if isinstance(level, (int, float)) and abs(price - level) / price < 0.01:
                # 价格在关键位附近=体制即将切换
                if price > level:
                    signals.append({
                        'name': 'regime_break', 'direction': 'LONG',
                        'weight': SIGNAL_WEIGHTS['regime_break'],
                        'desc': f'突破${level:,.0f}→BULL确认'
                    })
                else:
                    signals.append({
                        'name': 'regime_break', 'direction': 'SHORT',
                        'weight': SIGNAL_WEIGHTS['regime_break'],
                        'desc': f'跌破${level:,.0f}→BEAR确认'
                    })
                break
    
    # 7. 事件驱动
    event = macro_data.get('event', '')
    if event and macro_data.get('event_triggered', False):
        event_dir = macro_data.get('event_direction', '')
        if event_dir in ('LONG', 'SHORT'):
            signals.append({
                'name': 'event_driven', 'direction': event_dir,
                'weight': SIGNAL_WEIGHTS['event_driven'],
                'desc': f'事件驱动({event})→{event_dir}'
            })
    
    # 8. GEX支撑（价格接近MIN_GEX）
    min_gex = gex_data.get('min_gex_price', 0)
    if min_gex > 0 and price > 0:
        dist_gex = abs(price - min_gex) / price * 100
        if dist_gex < 2.0:
            gex_dir = 'LONG' if min_gex < price else 'SHORT'
            signals.append({
                'name': 'gex_support', 'direction': gex_dir,
                'weight': SIGNAL_WEIGHTS['gex_support'],
                'desc': f'MIN_GEX${min_gex:,.0f} 距{dist_gex:.1f}%'
            })
    
    return signals


def weighted_vote(signals: List[Dict], regime: str, rsi: Dict, liq_data: Dict, price: float) -> Dict:
    """
    加权投票+方向裁决+修正规则
    返回：{direction, long_score, short_score, margin, action_level, triggers}
    """
    long_score = sum(s['weight'] for s in signals if s['direction'] == 'LONG')
    short_score = sum(s['weight'] for s in signals if s['direction'] == 'SHORT')
    total = long_score + short_score
    
    if total == 0:
        return {
            'direction': 'NEUTRAL', 'long_score': 0, 'short_score': 0,
            'margin': 0, 'action_level': 'NONE', 'triggers': signals
        }
    
    # 方向裁决
    if long_score > short_score:
        direction = 'LONG'
        margin = (long_score - short_score) / total * 100
    elif short_score > long_score:
        direction = 'SHORT'
        margin = (short_score - long_score) / total * 100
    else:
        # 平票：清算猎杀链优先做空
        has_liq_short = any(s['name'] == 'liq_hunt' and s['direction'] == 'SHORT' for s in signals)
        direction = 'SHORT' if has_liq_short else 'LONG'
        margin = 0
    
    # 修正2：多空差距<15%→AMBUSCADE_WATCH
    if margin < 15:
        action_level = 'AMBUSCADE_WATCH'
    else:
        action_level = 'AMBUSCADE'
    
    # 修正4：体制突破+猎杀链同时做空→做空优先（权重1.5>任何做多）
    has_regime_short = any(s['name'] == 'regime_break' and s['direction'] == 'SHORT' for s in signals)
    has_liq_short = any(s['name'] == 'liq_hunt' and s['direction'] == 'SHORT' for s in signals)
    if has_regime_short and has_liq_short and direction == 'LONG':
        direction = 'SHORT'
        action_level = 'AMBUSCADE'
    
    # 修正5：RSI<20+支撑+Hurst>0.5→强制做多
    rsi_15m = rsi.get('rsi_15m', 50)
    support_pool = liq_data.get('support_pool_down', 0)
    has_hurst_signal = any(s['name'] == 'hurst_rsi' and s['weight'] > 0 for s in signals)
    if rsi_15m < 20 and support_pool > 0 and abs(price - support_pool) / price < 0.03 and has_hurst_signal:
        direction = 'LONG'
        action_level = 'AMBUSCADE'
    
    # 修正7：RSI<25+距支撑池<2%→禁止做空（超卖区不追空）
    if direction == 'SHORT' and rsi_15m < 25 and support_pool > 0:
        if abs(price - support_pool) / price < 0.02:
            direction = 'NEUTRAL'
            action_level = 'AMBUSCADE_BLOCK'
    
    # 修正6：清算猎杀链做空只在价格>止损墙-1%
    stop_wall = liq_data.get('stop_wall_up', 0)
    if direction == 'SHORT' and stop_wall > 0 and price < stop_wall * 0.99:
        # 价格在止损墙-1%以下→做空猎杀链不触发
        signals = [s for s in signals if not (s['name'] == 'liq_hunt' and s['direction'] == 'SHORT')]
        # 重新计算
        long_score = sum(s['weight'] for s in signals if s['direction'] == 'LONG')
        short_score = sum(s['weight'] for s in signals if s['direction'] == 'SHORT')
        total = long_score + short_score
        if total == 0:
            return {
                'direction': 'NEUTRAL', 'long_score': 0, 'short_score': 0,
                'margin': 0, 'action_level': 'NONE', 'triggers': signals
            }
        if long_score > short_score:
            direction = 'LONG'
            margin = (long_score - short_score) / total * 100
        elif short_score > long_score:
            direction = 'SHORT'
            margin = (short_score - long_score) / total * 100
        else:
            direction = 'NEUTRAL'
            margin = 0
        if margin < 15:
            action_level = 'AMBUSCADE_WATCH'
        else:
            action_level = 'AMBUSCADE'
    
    return {
        'direction': direction,
        'long_score': round(long_score, 2),
        'short_score': round(short_score, 2),
        'margin': round(margin, 1),
        'action_level': action_level,
        'triggers': signals
    }


def should_ambuscade(
    symbol: str,
    direction: str,
    price: float,
    regime: str,
    fvg_data: Dict,
    liq_data: Dict,
    hurst: float,
    rsi: Dict,
    fangcang_data: Dict,
    oi_signal: str,
    cvd_1h: float,
    gex_data: Dict,
    macro_data: Dict,
    regime_state: str,
    current_action: str,
    missing: List[str],
) -> Optional[Dict]:
    """
    AMBUSCADE层主入口。
    在trader_brain Layer 6之后调用：
    - 如果current_action=ENTER/WATCH → 保持不变
    - 如果current_action=WAIT → 检查AMBUSCADE是否触发
    - 如果AMBUSCADE触发 → 返回新决策（覆盖WAIT）
    
    返回None=不触发AMBUSCADE，保持原决策
    返回Dict=AMBUSCADE决策（覆盖原决策）
    """
    # 只在WAIT时尝试AMBUSCADE（ENTER/WATCH保持不变）
    if current_action in ('ENTER', 'WATCH', 'SKIP'):
        return None
    
    # 检测预判信号
    signals = detect_ambuscade_signals(
        symbol, direction, price, regime,
        fvg_data, liq_data, hurst, rsi,
        fangcang_data, oi_signal, cvd_1h,
        gex_data, macro_data, regime_state
    )
    
    # 预判信号≥2个才考虑AMBUSCADE
    active_signals = [s for s in signals if s['weight'] > 0]
    if len(active_signals) < 2:
        return None
    
    # 加权投票
    vote = weighted_vote(signals, regime, rsi, liq_data, price)
    
    if vote['action_level'] in ('NONE', 'AMBUSCADE_BLOCK'):
        return None
    if vote['direction'] == 'NEUTRAL':
        return None
    
    # 方向翻转冷却检查（简单版：用文件记录上次方向）
    cooldown = _check_cooldown(symbol, vote['direction'])
    if not cooldown:
        return None
    
    return {
        'action': vote['action_level'],  # AMBUSCADE or AMBUSCADE_WATCH
        'direction': vote['direction'],
        'ambuscade_triggers': vote['triggers'],
        'ambuscade_margin': vote['margin'],
        'ambuscade_long_score': vote['long_score'],
        'ambuscade_short_score': vote['short_score'],
    }


def _check_cooldown(symbol: str, direction: str) -> bool:
    """方向翻转冷却24h"""
    cooldown_file = f'data/ambuscade_cooldown_{symbol}.json'
    try:
        if os.path.exists(cooldown_file):
            with open(cooldown_file) as f:
                data = json.load(f)
            last_dir = data.get('direction', '')
            last_ts = data.get('ts', 0)
            if last_dir and last_dir != direction:
                age = time.time() - last_ts
                if age < 86400:  # 24h
                    return False
        # 记录
        with open(cooldown_file, 'w') as f:
            json.dump({'direction': direction, 'ts': time.time()}, f)
        return True
    except Exception:
        return True  # 出错不阻塞
