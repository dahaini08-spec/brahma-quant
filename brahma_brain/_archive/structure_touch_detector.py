#!/usr/bin/env python3
# ponytail: structure_touch_detector 397行，有意为之，重构前先 grep 所有调用方
"""
structure_touch_detector.py — 结构触碰事件检测器 v1.0
设计院封印 2026-08-15 苏摩111

核心哲学：
  「价格在FVG/OB/清算集群附近」≠「价格插针触碰了结构」
  现有维度2只检测距离（静态），本模块检测触碰事件（动态）

三类触碰事件：
  TOUCH_FVG  — 最近N根K线的high/low刺入FVG区间，收盘在区间外（插针反弹）
  TOUCH_OB   — 最近N根K线的high/low刺入OB区间，收盘未有效破位
  TOUCH_LIQ  — 最近N根K线触及清算集群位置后快速反弹（清扫完毕信号）

接入点：
  brahma_scoring.py 维度2（关键位精确度）末尾追加触碰事件加分
  维度7（清算/OI）末尾追加 liq_sweep 事件加分

设计原则（Karpathy铁律）：
  - 只做触碰检测，不重构现有评分逻辑
  - fail-safe: 任何异常→返回空事件，不影响主流程
  - 白名单加分上限：避免单模块评分失控
"""

from __future__ import annotations
import time
from typing import Optional

# ── 触碰衰减系数（距当前K线的K棒数） ──────────────────────────────────
_DECAY = {0: 1.0, 1: 0.85, 2: 0.65, 3: 0.40}
_MAX_LOOKBACK = 3  # 超过3根K线前的触碰不计入


# ══════════════════════════════════════════════════════════════════════
# 核心检测函数
# ══════════════════════════════════════════════════════════════════════

def detect_structure_touch(
    signal_dir: str,          # 'LONG' or 'SHORT'
    current_price: float,
    smc: dict,                # 来自 smc_engine 的完整SMC数据
    klines_1h: dict,          # extra_data['_klines_1h'] 或 ms['klines_1h']
    liq_data: Optional[dict] = None,  # liq_density_engine 返回（可选）
    klines_4h: Optional[dict] = None, # [P2-C 2026-08-15] 4H K线扩展（更高质量触碰）
) -> dict:
    """
    检测最近3根1H K线内是否发生了结构触碰事件。

    返回：
    {
      'fvg_touch': bool,         # FVG触碰事件
      'fvg_touch_score': int,    # FVG触碰加分（0~12）
      'ob_touch': bool,          # OB触碰事件
      'ob_touch_score': int,     # OB触碰加分（0~12）
      'liq_touch': bool,         # 清算集群触碰（被扫后反弹）
      'liq_touch_score': int,    # 清算触碰加分（0~15）
      'multi_touch': bool,       # ≥2个结构同时触碰
      'multi_touch_bonus': int,  # 三重共振奖励（0~8）
      'total_score': int,        # 总加分（上限25）
      'touch_quality': int,      # 综合质量 0~100
      'details': list,           # 文本说明列表
    }
    """
    result = {
        'fvg_touch': False, 'fvg_touch_score': 0,
        'ob_touch': False,  'ob_touch_score': 0,
        'liq_touch': False, 'liq_touch_score': 0,
        'multi_touch': False, 'multi_touch_bonus': 0,
        'total_score': 0,
        'touch_quality': 0,
        'details': [],
    }
    if not klines_1h or not smc:
        return result

    try:
        highs  = klines_1h.get('h', [])
        lows   = klines_1h.get('l', [])
        closes = klines_1h.get('c', [])
        if len(closes) < 4:
            return result

        # 最近3根已收盘K线（index: -4, -3, -2，不含当前未收盘的-1）
        recent_bars = []
        for ago in range(1, _MAX_LOOKBACK + 1):
            idx = -(ago + 1)  # +1是因为-1是当前未收盘
            if abs(idx) > len(closes):
                break
            recent_bars.append({
                'ago': ago,
                'high': highs[idx],
                'low': lows[idx],
                'close': closes[idx],
                'decay': _DECAY.get(ago, 0),
            })

        touch_count = 0

        # ── 1. FVG触碰检测 ────────────────────────────────────────────
        fvg_score = _detect_fvg_touch(signal_dir, current_price, smc, recent_bars)
        if fvg_score > 0:
            result['fvg_touch'] = True
            result['fvg_touch_score'] = fvg_score
            result['details'].append(f'FVG触碰+{fvg_score}')
            touch_count += 1

        # ── 2. OB触碰检测（1H + 4H双层）────────────────────────────────
        ob_score_1h = _detect_ob_touch(signal_dir, current_price, smc, recent_bars, tf='1h')
        ob_score_4h = 0
        if klines_4h and len(klines_4h.get('c', [])) >= 4:
            recent_4h = []
            highs_4h  = klines_4h.get('h', [])
            lows_4h   = klines_4h.get('l', [])
            closes_4h = klines_4h.get('c', [])
            for ago in range(1, _MAX_LOOKBACK + 1):
                idx = -(ago + 1)
                if abs(idx) > len(closes_4h):
                    break
                recent_4h.append({
                    'ago': ago, 'high': highs_4h[idx],
                    'low': lows_4h[idx], 'close': closes_4h[idx],
                    'decay': _DECAY.get(ago, 0),
                })
            # 4H触碰用 order_blocks_4h（如有）否则用1H OB数据
            smc_4h = dict(smc)
            ob4h = smc.get('order_blocks_4h', {})
            if ob4h:
                smc_4h['order_blocks'] = ob4h
            ob_score_4h = _detect_ob_touch(signal_dir, current_price, smc_4h, recent_4h, tf='4h')
        ob_score = max(ob_score_1h, ob_score_4h)  # 取最高质量
        if ob_score > 0:
            result['ob_touch'] = True
            result['ob_touch_score'] = ob_score
            result['details'].append(f'OB触碰+{ob_score}')
            touch_count += 1

        # ── 3. 清算集群触碰检测 ───────────────────────────────────────
        liq_score = _detect_liq_touch(signal_dir, current_price, liq_data, recent_bars)
        if liq_score > 0:
            result['liq_touch'] = True
            result['liq_touch_score'] = liq_score
            result['details'].append(f'清算扫描+{liq_score}')
            touch_count += 1

        # ── 4. 多重共振奖励 ───────────────────────────────────────────
        multi_bonus = 0
        if touch_count >= 2:
            result['multi_touch'] = True
            multi_bonus = 5 if touch_count == 2 else 8  # 双重+5，三重+8
            result['multi_touch_bonus'] = multi_bonus
            result['details'].append(f'{"双" if touch_count==2 else "三"}重结构共振+{multi_bonus}')

        # ── 5. 汇总 ───────────────────────────────────────────────────
        raw_total = fvg_score + ob_score + liq_score + multi_bonus
        result['total_score'] = min(raw_total, 25)  # 全局上限25分
        result['touch_quality'] = min(int(raw_total / 25 * 100), 100)

    except Exception:
        pass  # fail-safe：异常静默，返回空事件

    return result


# ══════════════════════════════════════════════════════════════════════
# 私有检测子函数
# ══════════════════════════════════════════════════════════════════════

def _detect_fvg_touch(signal_dir: str, price: float, smc: dict, bars: list) -> int:
    """检测FVG触碰：K线high/low刺入FVG区间，收盘在区间外（插针反弹/反落）"""
    fvg = smc.get('fvg', {})
    if not fvg:
        return 0

    zone = None
    if signal_dir == 'LONG':
        zone = fvg.get('nearest_bull')  # 做多看Bull FVG（下方支撑）
    else:
        zone = fvg.get('nearest_bear')  # 做空看Bear FVG（上方阻力）

    if not zone:
        return 0

    fvg_low  = float(zone.get('bottom', 0) or zone.get('low', 0))
    fvg_high = float(zone.get('top', 0) or zone.get('high', 0))
    if fvg_low <= 0 or fvg_high <= 0 or fvg_low >= fvg_high:
        return 0

    for bar in bars:
        decay = bar['decay']
        if signal_dir == 'LONG':
            # 做多：K线低点刺入FVG（low进入FVG区间），收盘在FVG上方或内部
            wick_in = bar['low'] <= fvg_high and bar['low'] >= fvg_low * 0.98
            close_above = bar['close'] >= fvg_low * 0.995
            if wick_in and close_above:
                raw = 12
                return max(1, int(raw * decay))
        else:
            # 做空：K线高点刺入FVG（high进入FVG区间），收盘在FVG下方或内部
            wick_in = bar['high'] >= fvg_low and bar['high'] <= fvg_high * 1.02
            close_below = bar['close'] <= fvg_high * 1.005
            if wick_in and close_below:
                raw = 12
                return max(1, int(raw * decay))

    return 0


def _detect_ob_touch(signal_dir: str, price: float, smc: dict, bars: list, tf: str = '1h') -> int:
    """检测OB触碰：K线high/low刺入OB区间，收盘未有效破位
    [P2-B 2026-08-15] 新增OB方向性：区分「从外接近」vs「已穿越走远」
    """
    obs = smc.get('order_blocks', {})
    if not obs:
        return 0

    ob = None
    if signal_dir == 'LONG':
        ob = obs.get('nearest_bull_ob')
    else:
        ob = obs.get('nearest_bear_ob')

    if not ob or ob.get('broken'):
        return 0

    ob_low  = float(ob.get('low', 0))
    ob_high = float(ob.get('high', 0))
    if ob_low <= 0 or ob_high <= 0:
        return 0

    # [P2-B] OB方向性判断：区分「从外部接近」vs「已穿越走远」
    # 三种场景（做多Bull OB为例）:
    #   A: price < ob_low  → 价格在OB下方，从外部接近（标准）
    #   B: ob_low <= price <= ob_high → 价格在OB内（精确触碰区）
    #   C: price > ob_high * 1.015 → 已穿越走远（追高，is_broken应过滤）
    if signal_dir == 'LONG':
        if price > ob_high * 1.015:   # 场景C：已穿越，追高风险
            approach_mult = 0.5
        elif ob_low <= price <= ob_high:  # 场景B：在OB内，最高质量
            approach_mult = 1.3
        else:                             # 场景A：从外接近，标准
            approach_mult = 1.0
    else:
        if price < ob_low * 0.985:    # 场景C：已穿越
            approach_mult = 0.5
        elif ob_low <= price <= ob_high:  # 场景B：在OB内
            approach_mult = 1.3
        else:                             # 场景A：从外接近
            approach_mult = 1.0

    # 新鲜度乘数（继承原有逻辑）
    age_bars = ob.get('age_bars', 0)
    if age_bars <= 3:
        age_mult = 1.0
    elif age_bars <= 6:
        age_mult = 0.75
    elif age_bars <= 10:
        age_mult = 0.50
    else:
        age_mult = 0.30

    # 4H触碰基础分更高（周期更大=更可靠）
    base_raw = 13 if tf == '4h' else 10

    for bar in bars:
        decay = bar['decay']
        if signal_dir == 'LONG':
            wick_in   = bar['low'] <= ob_high and bar['low'] >= ob_low * 0.98
            close_held = bar['close'] >= ob_low * 0.995
            if wick_in and close_held:
                return max(1, int(base_raw * decay * age_mult * approach_mult))
        else:
            wick_in   = bar['high'] >= ob_low and bar['high'] <= ob_high * 1.02
            close_held = bar['close'] <= ob_high * 1.005
            if wick_in and close_held:
                return max(1, int(base_raw * decay * age_mult * approach_mult))

    return 0


def _detect_liq_touch(
    signal_dir: str,
    price: float,
    liq_data: Optional[dict],
    bars: list,
) -> int:
    """
    检测清算集群触碰：最近K线触碰下方(做多)/上方(做空)清算集群后反弹。
    liq_data = liq_density_engine.get_liq_density() 的返回值
    """
    if not liq_data:
        return 0

    walls = liq_data.get('walls', [])
    if not walls:
        return 0

    # 找方向匹配的最近清算墙
    target_wall = None
    for wall in walls:
        wall_price = float(wall[0]) if isinstance(wall, (list, tuple)) else float(wall.get('price', 0))
        wall_usd   = float(wall[1]) if isinstance(wall, (list, tuple)) else float(wall.get('usd', 0))
        if wall_usd < 50_000:  # 忽略太小的清算点
            continue
        if signal_dir == 'LONG' and wall_price < price * 0.97:  # 下方3%内
            target_wall = wall_price
            break
        elif signal_dir == 'SHORT' and wall_price > price * 1.03:  # 上方3%内
            target_wall = wall_price
            break

    if target_wall is None:
        return 0

    for bar in bars:
        decay = bar['decay']
        if signal_dir == 'LONG':
            # 做多：K线低点触及清算墙（±0.5%），收盘在清算墙上方
            touched = abs(bar['low'] - target_wall) / target_wall <= 0.005
            bounced = bar['close'] > target_wall * 1.002
            if touched and bounced:
                raw = 15
                return max(1, int(raw * decay))
        else:
            # 做空：K线高点触及清算墙，收盘在清算墙下方
            touched = abs(bar['high'] - target_wall) / target_wall <= 0.005
            bounced = bar['close'] < target_wall * 0.998
            if touched and bounced:
                raw = 15
                return max(1, int(raw * decay))

    return 0


# ══════════════════════════════════════════════════════════════════════
# 体制豁免检查器
# ══════════════════════════════════════════════════════════════════════

def check_structure_bypass(
    regime: str,
    direction: str,
    touch_result: dict,
    base_score: float,
) -> dict:
    """
    检查是否满足结构共振豁免条件（解除CHOP体制对LONG的封禁）

    仅用于 CHOP_MID × LONG（其他体制死穴不豁免）
    豁免条件：
      1. touch_quality ≥ 70
      2. 触碰数量 ≥ 2
      3. base_score（不含体制乘数）≥ 80
    豁免后：
      - 仓位乘数 × 0.5
      - HCME专项入场形态标签注入

    返回:
    {
      'bypass': bool,
      'reason': str,
      'size_mult': float,      # 仓位乘数（豁免时=0.5，否则=1.0）
      'entry_pattern': str,    # HCME专项标签
    }
    """
    no_bypass = {'bypass': False, 'reason': '', 'size_mult': 1.0, 'entry_pattern': ''}

    # 只豁免 CHOP_MID × LONG 这一个死穴
    if not (regime == 'CHOP_MID' and direction == 'LONG'):
        return no_bypass

    touch_quality = touch_result.get('touch_quality', 0)
    touch_count = sum([
        touch_result.get('fvg_touch', False),
        touch_result.get('ob_touch', False),
        touch_result.get('liq_touch', False),
    ])

    if touch_quality < 70:
        return {**no_bypass, 'reason': f'触碰质量{touch_quality}<70，豁免未触发'}
    if touch_count < 2:
        return {**no_bypass, 'reason': f'触碰数量{touch_count}<2，豁免未触发'}
    if base_score < 80:
        return {**no_bypass, 'reason': f'base_score={base_score:.0f}<80，豁免未触发'}

    # 确定专项标签
    labels = []
    if touch_result.get('fvg_touch'):  labels.append('FVG_BOUNCE')
    if touch_result.get('ob_touch'):   labels.append('OB_TOUCH')
    if touch_result.get('liq_touch'):  labels.append('LIQ_SWEEP')
    pattern = '+'.join(labels) if labels else 'STRUCTURE_TOUCH'

    return {
        'bypass': True,
        'reason': f'CHOP结构共振豁免: {pattern} quality={touch_quality} score={base_score:.0f}',
        'size_mult': 0.5,
        'entry_pattern': pattern,
    }
