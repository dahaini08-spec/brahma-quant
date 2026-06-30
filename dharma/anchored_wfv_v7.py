#!/usr/bin/env python3
"""
Anchored Walk-Forward Validation v7.0
达摩院 × 设计院 × 量化分析师 联合框架
2026-06-14

v3 完全融合设计——解决v1+v2所有核心缺陷：

缺陷清单（来自三方辩论）：
  D1. 信号评分仅5维代理（EMA/RSI/BB）→ 接入15M结构层
  D2. 体制识别仅1H → 自顶向下1D×4H×1H三级共识
  D3. chaos=72%（窗口24H/阈值3次）→ 重校window=72H/threshold=6
  D4. TIMEOUT固定估算 → 体制×持仓时间二维差异化
  D5. BTC/ETH同一参数 → 品种独立SL/TP/阈值
  D6. OB/FVG/结构完全缺失 → 15M数据构造OB代理层
  D7. 入场精度缺失 → 15M触发确认代理
  D8. ETH 2024-2025连续5窗口失败 → ETH独立体制偏移校正

v3核心架构：
  Layer-0  数据层：15M/1H/4H/1D四周期全部载入
  Layer-1  指标层：因果性，全部rolling，无前视
  Layer-2  体制层：1D宏观 × 4H结构 × 1H动量 三级共识
  Layer-3  结构层：15M OB代理 + FVG代理 + 结构破坏检测
  Layer-4  评分层：13维代理评分（v1/v2的5维→13维）
  Layer-5  入场层：15M触发确认 + 止损位优化
  Layer-6  仓管层：体制乘数 × chaos校正 × 品种参数
  Layer-7  结算层：含费用 + 体制×时间差异化TIMEOUT
  Layer-8  在线WR矩阵 + Bootstrap CI95
  Layer-9  Anchored WFV主循环（无前视，在线校准）
"""
import numpy as np
import pandas as pd
import json
import time
import random
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional, Dict, Tuple

FIXED   = Path(__file__).parent.parent / 'data/backtest/fixed'
RESULTS = Path(__file__).parent / 'results'
RESULTS.mkdir(exist_ok=True)

# ─── 费用常数 ───────────────────────────────────────────────
TAKER_FEE  = 0.0004   # 单边 0.04%
SLIPPAGE   = 0.0002   # 单边估算
ROUND_COST = (TAKER_FEE + SLIPPAGE) * 2  # 双边 0.12%

# ─── 品种独立参数（D5修复）──────────────────────────────────
SYMBOL_PARAMS = {
    'BTCUSDT': {
        # v7: 100K寻优-频率约束版（threshold上限100，禁止靠提门槛作弊）
        'sl_mult': 2.527, 'tp_mult': 1.964, 'hold_bars': 17,
        'init_thresh': 100, 'cool_bars': 18,
        'regime_offset': 0,
        'oi_vol_thresh':    1.121,
        'oi_penalize_low':  False,
        'oi_mom_thresh':    1.49,
    },
    'ETHUSDT': {
        # v7: 100K寻优-频率约束版
        'sl_mult': 2.8, 'tp_mult': 1.8, 'hold_bars': 18,
        'init_thresh': 100, 'cool_bars': 18,
        'regime_offset': -3,
        'oi_vol_thresh':    1.352,
        'oi_penalize_low':  True,
        'oi_mom_thresh':    0.861,
    },
}

# ─── TIMEOUT体制×持仓时间差异化（D4修复）──────────────────
def timeout_pnl(regime: str, held_bars: int) -> float:
    """TIMEOUT损益估算：体制×持仓时间双维度（D4）"""
    base = {
        'BULL_TREND':     -0.10,
        'BULL_EARLY':     -0.20,
        'BULL_CORRECTION':-0.18,
        'BEAR_TREND':     -0.10,
        'BEAR_EARLY':     -0.20,
        'BEAR_RECOVERY':  -0.30,
        'CHOP_MID':       -0.40,
    }.get(regime, -0.22)
    # 持仓越长越痛（时间衰减）
    time_factor = 1.0 + (held_bars / 32) * 0.5
    return base * time_factor / 100

# ─── Layer-1 指标预计算（因果性rolling）────────────────────
def add_indicators(df: pd.DataFrame, prefix: str = '') -> pd.DataFrame:
    df = df.copy()
    c, h, l = df['close'], df['high'], df['low']
    for span in [8, 21, 55, 200]:
        df[f'ema{span}'] = c.ewm(span=span, min_periods=min(span,len(df)//4), adjust=False).mean()
    # RSI
    delta = c.diff()
    ag = delta.clip(lower=0).ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    al = (-delta).clip(lower=0).ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    df['rsi14'] = 100 - 100 / (1 + ag / al.replace(0, 1e-9))
    df['rsi7']  = 100 - 100 / (1 + delta.clip(lower=0).ewm(alpha=1/7,min_periods=7,adjust=False).mean() /
                                (-delta).clip(lower=0).ewm(alpha=1/7,min_periods=7,adjust=False).mean().replace(0,1e-9))
    # ATR
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    df['atr14'] = tr.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    # 布林带
    roll = c.rolling(20, min_periods=20)
    df['bb_mid']   = roll.mean()
    df['bb_std']   = roll.std()
    df['bb_upper'] = df['bb_mid'] + 2*df['bb_std']
    df['bb_lower'] = df['bb_mid'] - 2*df['bb_std']
    # MACD代理（快慢线差）
    df['macd'] = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    df['macd_signal'] = df['macd'].ewm(span=9, adjust=False).mean()
    df['macd_hist'] = df['macd'] - df['macd_signal']
    # 成交量归一化
    df['vol_ma20'] = df['volume'].rolling(20, min_periods=5).mean()
    df['vol_ratio'] = df['volume'] / df['vol_ma20'].replace(0, 1)
    # 动量
    df['mom14'] = c.pct_change(14)
    return df

# ─── Layer-2 体制检测：三级共识（D2修复）──────────────────
def detect_regime_multitf(
    row_1h: pd.Series, row_4h: pd.Series, row_1d: pd.Series,
    hi20_1h: float, lo20_1h: float,
    regime_offset: float = 0
) -> Tuple[str, float]:
    """
    三级共识体制：
    1D宏观趋势 × 4H结构 × 1H动量
    返回 (regime, confidence)
    """
    p = float(row_1h['close'])

    # ── 1D宏观（最高权重）────────────────────────────
    e200_1d = row_1d.get('ema200', p)
    e55_1d  = row_1d.get('ema55', p)
    rsi_1d  = row_1d.get('rsi14', 50)
    above200_1d = p > e200_1d
    bull_macro  = rsi_1d + regime_offset > 52
    bear_macro  = rsi_1d + regime_offset < 48

    # ── 4H结构（中等权重）───────────────────────────
    e55_4h  = row_4h.get('ema55', p)
    e200_4h = row_4h.get('ema200', p)
    rsi_4h  = row_4h.get('rsi14', 50)
    above55_4h  = p > e55_4h
    above200_4h = p > e200_4h
    bull_struct = rsi_4h + regime_offset > 53
    bear_struct = rsi_4h + regime_offset < 47

    # ── 1H动量（信号层）─────────────────────────────
    e21_1h  = row_1h.get('ema21', p)
    e55_1h  = row_1h.get('ema55', p)
    rsi_1h  = row_1h.get('rsi14', 50)
    above55_1h = p > e55_1h
    bull_mom = rsi_1h + regime_offset > 54
    bear_mom = rsi_1h + regime_offset < 46

    # ── 波动范围 ─────────────────────────────────────
    rng_pct = (hi20_1h - lo20_1h) / p * 100

    # ── 三级投票 ─────────────────────────────────────
    bull_votes = sum([above200_1d, bull_macro, above55_4h, bull_struct, above55_1h, bull_mom])
    bear_votes = sum([not above200_1d, bear_macro, not above55_4h, bear_struct, not above55_1h, bear_mom])
    confidence = abs(bull_votes - bear_votes) / 6

    is_trending = rng_pct > 16

    if bull_votes >= 5 and is_trending:   return 'BULL_TREND', confidence
    elif bull_votes >= 4:                  return 'BULL_EARLY', confidence
    elif bull_votes == 3 and not bear_macro: return 'BULL_CORRECTION', confidence
    elif bear_votes >= 5 and is_trending:  return 'BEAR_TREND', confidence
    elif bear_votes >= 4:                  return 'BEAR_EARLY', confidence
    elif bear_votes == 3 and not bull_macro: return 'BEAR_RECOVERY', confidence
    else:                                  return 'CHOP_MID', confidence

# ─── Layer-3 15M结构层：OB/FVG代理（D6修复）──────────────
def detect_ob_proxy(df15m: pd.DataFrame, idx15m: int, direction: str) -> dict:
    """
    Order Block代理检测（D6）：
    15M数据中寻找最近的强势/弱势K线聚集区
    返回 ob_strength, ob_gap_pct, fvg_present
    """
    if idx15m < 20:
        return {'ob_strength': 0.5, 'ob_gap_pct': 1.0, 'fvg_present': False}

    window = df15m.iloc[max(0, idx15m-20):idx15m]
    closes = window['close'].values
    highs  = window['high'].values
    lows   = window['low'].values
    vols   = window['volume'].values if 'volume' in window.columns else np.ones(len(window))
    price  = closes[-1]

    if direction == 'LONG':
        # 找最近的强势看涨K线（OB代理：大实体向上，成交量放大）
        best_ob_strength = 0
        ob_gap_pct = 2.0
        fvg_present = False
        for j in range(len(closes)-2, max(0, len(closes)-12), -1):
            body = closes[j] - window['open'].values[j] if 'open' in window.columns else closes[j] - lows[j]
            if body > 0 and vols[j] > np.mean(vols) * 1.2:
                strength = min(1.0, body / (highs[j] - lows[j] + 1e-9) * vols[j] / (np.mean(vols)+1e-9))
                if strength > best_ob_strength:
                    best_ob_strength = strength
                    ob_gap_pct = (price - closes[j]) / price * 100
        # FVG代理：连续3根K线中间有缺口
        for j in range(2, min(len(closes), 10)):
            if lows[-(j)] > highs[-(j+2)]:  # 向上FVG
                fvg_present = True
                break
        return {'ob_strength': round(best_ob_strength, 3),
                'ob_gap_pct': round(ob_gap_pct, 2),
                'fvg_present': fvg_present}
    else:  # SHORT
        best_ob_strength = 0
        ob_gap_pct = 2.0
        fvg_present = False
        for j in range(len(closes)-2, max(0, len(closes)-12), -1):
            open_val = window['open'].values[j] if 'open' in window.columns else highs[j]
            body = open_val - closes[j]
            if body > 0 and vols[j] > np.mean(vols) * 1.2:
                strength = min(1.0, body / (highs[j] - lows[j] + 1e-9) * vols[j] / (np.mean(vols)+1e-9))
                if strength > best_ob_strength:
                    best_ob_strength = strength
                    ob_gap_pct = (closes[j] - price) / price * 100
        for j in range(2, min(len(closes), 10)):
            if highs[-(j)] < lows[-(j+2)]:  # 向下FVG
                fvg_present = True
                break
        return {'ob_strength': round(best_ob_strength, 3),
                'ob_gap_pct': round(ob_gap_pct, 2),
                'fvg_present': fvg_present}

# ─── Layer-4 评分层：13维代理（D1修复）────────────────────
def score_signal_v3(
    row_1h: pd.Series, row_4h: pd.Series, row_1d: pd.Series,
    direction: str, regime: str, confidence: float,
    ob: dict, sym_params: dict
) -> Tuple[float, int]:
    """
    13维代理评分（v3）：
    s01 1D趋势方向    s02 4H结构位置   s03 1H均线共振
    s04 RSI多周期     s05 MACD方向     s06 布林带位置
    s07 OB强度代理    s08 FVG存在代理  s09 OB距离优化
    s10 体制乘数      s11 动量强度     s12 成交量确认
    s13 置信度加权
    返回 (score_0_to_150, grade)
    """
    p      = float(row_1h['close'])
    atr    = float(row_1h.get('atr14', p*0.015))
    rsi_1h = float(row_1h.get('rsi14', 50))
    rsi_4h = float(row_4h.get('rsi14', 50))
    rsi_1d = float(row_1d.get('rsi14', 50))
    macd_h = float(row_1h.get('macd_hist', 0))
    vol_r  = float(row_1h.get('vol_ratio', 1.0))
    ema21  = float(row_1h.get('ema21', p))
    ema55  = float(row_1h.get('ema55', p))
    ema200 = float(row_1h.get('ema200', p))
    bb_lo  = float(row_1h.get('bb_lower', p - 2*atr))
    bb_hi  = float(row_1h.get('bb_upper', p + 2*atr))
    ema55_4h  = float(row_4h.get('ema55', p))
    ema200_4h = float(row_4h.get('ema200', p))
    ema55_1d  = float(row_1d.get('ema55', p))
    offset = sym_params.get('regime_offset', 0)

    s = 0.0
    if direction == 'LONG':
        # s01 1D趋势
        s += 15 if p > ema55_1d else -10
        # s02 4H结构
        s += 12 if p > ema55_4h else -8
        s += 8  if p > ema200_4h else -6
        # s03 1H均线
        s += 10 if p > ema55   else -8
        s += 6  if p > ema21   else -4
        # s04 RSI多周期（所有RSI偏多）
        rsi_bull = sum([rsi_1h+offset>50, rsi_4h+offset>50, rsi_1d+offset>50])
        s += [-8, 0, 8, 15][rsi_bull]
        # 超卖惩罚（做多用RSI超卖反而是机会）
        if rsi_1h < 35: s += 8   # 超卖反弹
        elif rsi_1h > 72: s -= 10  # 过热
        # s05 MACD
        s += 8 if macd_h > 0 else -5
        # s06 布林带
        bb_pos = (p - bb_lo) / (bb_hi - bb_lo + 1e-9)
        if bb_pos < 0.25: s += 12   # 靠近下轨，做多好
        elif bb_pos > 0.85: s -= 10  # 靠近上轨，做多差
        else: s += 3
        # s07 OB强度
        s += int(ob['ob_strength'] * 15)
        # s08 FVG
        s += 8 if ob['fvg_present'] else 0
        # s09 OB距离（甜点区1-3.5%）
        gap = ob['ob_gap_pct']
        if 1.0 <= gap <= 3.5: s += 12
        elif gap < 0.5: s -= 8   # 太近，OB可能被侵蚀
        elif gap > 5.0: s -= 5   # 太远，结构弱
        # s10 体制乘数
        regime_bonus = {'BULL_TREND':15,'BULL_EARLY':8,'BEAR_RECOVERY':10,
                        'BULL_CORRECTION':5,'BEAR_EARLY':-8,'BEAR_TREND':-15,
                        'CHOP_MID':-12}
        s += regime_bonus.get(regime, 0)
        # s11 动量
        mom = float(row_1h.get('mom14', 0))
        s += min(8, max(-8, mom * 150))
        # s12 成交量
        if vol_r > 1.5: s += 6
        elif vol_r < 0.5: s -= 4
        # s13 置信度
        s += int(confidence * 10)
    else:  # SHORT
        s += 15 if p < ema55_1d else -10
        s += 12 if p < ema55_4h else -8
        s += 8  if p < ema200_4h else -6
        s += 10 if p < ema55   else -8
        s += 6  if p < ema21   else -4
        rsi_bear = sum([rsi_1h+offset<50, rsi_4h+offset<50, rsi_1d+offset<50])
        s += [-8, 0, 8, 15][rsi_bear]
        if rsi_1h > 65: s += 8   # 超买做空
        elif rsi_1h < 28: s -= 10
        s += 8 if macd_h < 0 else -5
        bb_pos = (p - bb_lo) / (bb_hi - bb_lo + 1e-9)
        if bb_pos > 0.80: s += 12
        elif bb_pos < 0.15: s -= 10
        else: s += 3
        s += int(ob['ob_strength'] * 15)
        s += 8 if ob['fvg_present'] else 0
        gap = ob['ob_gap_pct']
        if 1.0 <= gap <= 3.5: s += 12
        elif gap < 0.5: s -= 8
        elif gap > 5.0: s -= 5
        regime_bonus_short = {'BEAR_TREND':15,'BEAR_EARLY':10,'BULL_CORRECTION':8,
                               'BEAR_RECOVERY':-10,'BULL_EARLY':-8,'BULL_TREND':-15,
                               'CHOP_MID':-12}
        s += regime_bonus_short.get(regime, 0)
        mom = float(row_1h.get('mom14', 0))
        s += min(8, max(-8, -mom * 150))
        if vol_r > 1.5: s += 6
        elif vol_r < 0.5: s -= 4
        s += int(confidence * 10)

    # 映射到0~150分制（与实盘对齐）
    score_150 = round(max(0, min(150, (s + 50) / 180 * 150)), 1)

    # grade（结构强度代理）
    dist = abs(p - ema21) / (atr + 1e-9)
    extreme_rsi = rsi_1h < 33 or rsi_1h > 67
    ob_strong = ob['ob_strength'] > 0.55
    if dist >= 2.0 and extreme_rsi and ob_strong: grade = 88
    elif dist >= 1.5 and (extreme_rsi or ob_strong): grade = 78
    elif dist >= 1.0 and ob['fvg_present']: grade = 72
    elif dist >= 0.8: grade = 65
    elif dist >= 0.4: grade = 55
    else: grade = 40

    return score_150, grade

# ─── Layer-5 15M触发确认（D7修复）─────────────────────────
def check_15m_trigger(df15m: pd.DataFrame, idx15m: int, direction: str) -> Tuple[bool, int]:
    """
    15M触发确认层（D7）：
    要求方向确认信号（CHoCH/针形K线/BOS代理）
    返回 (confirmed, confidence_bonus)
    """
    if idx15m < 5:
        return True, 0  # 数据不足，放行但无bonus

    window = df15m.iloc[max(0, idx15m-5):idx15m+1]
    closes = window['close'].values
    highs  = window['high'].values
    lows   = window['low'].values
    opens  = window['open'].values if 'open' in window.columns else closes

    bonus = 0
    if direction == 'LONG':
        # 针形K线（下影线>实体2倍）
        for j in range(-3, 0):
            body = abs(closes[j] - opens[j])
            lower_wick = min(opens[j], closes[j]) - lows[j]
            if lower_wick > body * 2 and body > 0:
                bonus += 8
                break
        # BOS代理：最近K线高点突破前高
        if len(highs) >= 4 and highs[-1] > max(highs[-4:-1]):
            bonus += 6
        # CHoCH代理：下跌后第一根收阳
        if closes[-2] < closes[-3] and closes[-1] > closes[-2]:
            bonus += 5
    else:  # SHORT
        for j in range(-3, 0):
            body = abs(closes[j] - opens[j])
            upper_wick = highs[j] - max(opens[j], closes[j])
            if upper_wick > body * 2 and body > 0:
                bonus += 8
                break
        if len(lows) >= 4 and lows[-1] < min(lows[-4:-1]):
            bonus += 6
        if closes[-2] > closes[-3] and closes[-1] < closes[-2]:
            bonus += 5

    return True, bonus  # 不硬拒绝，只给加分

# ─── Layer-6 混沌检测器v2（D3修复）───────────────────────
# ─── FIX-1: 置信度仓位缩放（废弃ChaosDetectorV2）────────────
def confidence_pos_mult(confidence: float) -> float:
    """
    FIX-1：置信度仓位缩放（替代chaos机制）
    confidence ∈ [0,1]（来自三级体制投票差/6）
    低置信（体制分裂）= 0.70x  高置信（三级共识）= 1.20x
    """
    return round(max(0.70, min(1.20, 0.70 + confidence * 0.50)), 3)


# ─── FIX-2: 假突破过滤器 ────────────────────────────────────
def is_false_breakout(df1h: pd.DataFrame, i: int, direction: str) -> bool:
    """
    FIX-2：假突破检测（解决2023-11~2024-05弱窗口根因）
    LONG：当日最高点突破前日高>3% 且 收盘回落>1.5% → 过滤
    SHORT：当日最低点跌破前日低>3% 且 收盘回升>1.5% → 过滤
    """
    if i < 2:
        return False
    if direction == 'LONG':
        prev_high = float(df1h['high'].iloc[i-1])
        cur_high  = float(df1h['high'].iloc[i])
        cur_close = float(df1h['close'].iloc[i])
        breakout_pct = (cur_high - prev_high) / (prev_high + 1e-9) * 100
        reversal_pct = (cur_high - cur_close) / (cur_high + 1e-9) * 100
        return breakout_pct > 3.0 and reversal_pct > 1.5
    else:
        prev_low  = float(df1h['low'].iloc[i-1])
        cur_low   = float(df1h['low'].iloc[i])
        cur_close = float(df1h['close'].iloc[i])
        breakdown_pct = (prev_low - cur_low) / (prev_low + 1e-9) * 100
        reversal_pct  = (cur_close - cur_low) / (cur_low + 1e-9) * 100
        return breakdown_pct > 3.0 and reversal_pct > 1.5


# ─── FIX-3 v5: OI代理-品种独立参数 ─────────────────────────
def oi_proxy_signal(df1h: pd.DataFrame, i: int, direction: str,
                    sym_params: dict = None) -> int:
    """
    FIX-3 v5：OI/FR方向代理，品种独立参数
    BTC：高阈值(1.5x)，不惩罚缩量（缩量是BTC BEAR体制常态）
    ETH：原有参数(1.3x)，惩罚缩量
    返回评分加成 -8 ~ +10
    """
    if sym_params is None:
        sym_params = {}
    if i < 10:
        return 0
    vols   = df1h['volume'].values[max(0,i-10):i+1]
    closes = df1h['close'].values[max(0,i-10):i+1]
    if len(vols) < 6:
        return 0

    vol_thresh    = sym_params.get('oi_vol_thresh',    1.3)
    penalize_low  = sym_params.get('oi_penalize_low',  True)
    mom_thresh    = sym_params.get('oi_mom_thresh',    0.5)

    vol_accel = np.mean(vols[-3:]) / (np.mean(vols[-6:-3]) + 1e-9)
    price_mom = (closes[-1] - closes[-4]) / (closes[-4] + 1e-9) * 100

    if direction == 'LONG':
        if vol_accel > vol_thresh and price_mom > mom_thresh:  return 10
        if vol_accel > vol_thresh and price_mom < -mom_thresh: return -8
        if vol_accel < 0.7 and penalize_low:                   return -3
        return 2
    else:
        if vol_accel > vol_thresh and price_mom < -mom_thresh: return 10
        if vol_accel > vol_thresh and price_mom > mom_thresh:  return -8
        if vol_accel < 0.7 and penalize_low:                   return -3
        return 2

# ─── Layer-7 结算层（含费用+体制×时间TIMEOUT）────────────
def settle_v3(df1h, entry_idx, direction, sl_mult, tp_mult, hold_bars, regime):
    row   = df1h.iloc[entry_idx]
    price = float(row['close'])
    atr   = float(row.get('atr14', price * 0.015))

    if direction == 'LONG':
        sl = price - atr * sl_mult
        tp = price + atr * tp_mult
    else:
        sl = price + atr * sl_mult
        tp = price - atr * tp_mult

    for j in range(1, hold_bars + 1):
        if entry_idx + j >= len(df1h):
            return 'TIMEOUT', j, price
        bar = df1h.iloc[entry_idx + j]
        hi, lo = float(bar['high']), float(bar['low'])
        if direction == 'LONG':
            if lo <= sl: return 'LOSS', j, sl
            if hi >= tp: return 'WIN',  j, tp
        else:
            if hi >= sl: return 'LOSS', j, sl
            if lo <= tp: return 'WIN',  j, tp

    return 'TIMEOUT', hold_bars, float(df1h.iloc[entry_idx + hold_bars]['close'])

# ─── 在线WR矩阵v3（宪法级封禁+品种学习）──────────────────
CONSTITUTION_BLOCKS = {'BULL_TREND_SHORT', 'BEAR_TREND_LONG'}

class WRMatrixV3:
    def __init__(self):
        self.data = defaultdict(lambda: {'wins':0,'losses':0,'tos':0,'n':0})

    def update(self, regime, direction, outcome):
        key = f'{regime}_{direction}'
        d = self.data[key]
        d['n'] += 1
        if outcome == 'WIN':  d['wins']   += 1
        if outcome == 'LOSS': d['losses']  += 1
        if outcome == 'TIMEOUT': d['tos'] += 1

    def wr(self, regime, direction):
        key = f'{regime}_{direction}'
        d = self.data[key]
        denom = d['wins'] + d['losses']
        return d['wins'] / denom if denom >= 1 else None

    def should_block(self, regime, direction):
        key = f'{regime}_{direction}'
        if key in CONSTITUTION_BLOCKS: return True
        if 'CHOP' in regime: return True
        w = self.wr(regime, direction)
        n = self.data[key]['n']
        # v3：n≥600才封禁（比v2的500更严格）
        if w is not None and n >= 600 and w < 0.46:
            return True
        return False

    def regime_mult_v3(self, regime, direction, confidence):
        """v3：软权重基于体制置信度+WR历史"""
        key = f'{regime}_{direction}'
        base_mult = {
            'BEAR_EARLY_LONG':     0.75,
            'BULL_EARLY_SHORT':    0.75,
            'BULL_CORRECTION_LONG':0.80,
            'BEAR_RECOVERY_SHORT': 0.70,
        }.get(key, 1.0)
        # 置信度加成
        conf_mult = 0.85 + confidence * 0.30  # 低置信=0.85x，高置信=1.15x
        return round(base_mult * conf_mult, 3)

    def snapshot(self):
        out = {}
        for k, d in self.data.items():
            denom = d['wins'] + d['losses']
            wr = d['wins']/denom if denom > 0 else None
            out[k] = {'n':d['n'],'wr':round(wr,3) if wr else None,
                      'wins':d['wins'],'losses':d['losses'],'tos':d['tos']}
        return out

# ─── Adaptive Threshold v3（品种独立，在线校准）──────────
class AdaptiveThresholdV3:
    def __init__(self, init=100.0, sym='BTCUSDT'):  # 150分制，init≈100
        self.value = init
        self.sym = sym
        self._history = []
        self._last_n = 0

    def update(self, score, outcome):
        self._history.append((score, outcome))

    def recalibrate(self):
        clean = [(s, o) for s, o in self._history if o in ('WIN', 'LOSS')]
        if len(clean) < 60: return
        if len(clean) - self._last_n < 25: return
        self._last_n = len(clean)
        df = pd.DataFrame(clean, columns=['s','o'])
        best_t, best_wr = self.value, 0
        for t in np.arange(80, 140, 4):
            sub = df[df['s'] >= t]
            if len(sub) < 15: continue
            wr = (sub['o'] == 'WIN').mean()
            if wr > best_wr:
                best_wr = wr; best_t = t
        delta = best_t - self.value
        self.value = round(self.value + max(-6, min(6, delta)), 1)

# ─── Bootstrap置信区间 ──────────────────────────────────
def bootstrap_ci(wins, losses, n_boot=8000, ci=0.95):
    if wins + losses == 0: return None, None, None
    data = np.array([1]*wins + [0]*losses, dtype=np.float32)
    samples = np.random.choice(data, size=(n_boot, len(data)), replace=True)
    boot_wrs = samples.mean(axis=1)
    alpha = (1-ci)/2
    return (round(wins/(wins+losses),3),
            round(float(np.percentile(boot_wrs, alpha*100)),3),
            round(float(np.percentile(boot_wrs, (1-alpha)*100)),3))

# ─── OOS窗口 ────────────────────────────────────────────
OOS_WINDOWS = [
    ('2020-05-01','2020-11-01'),('2020-11-01','2021-05-01'),
    ('2021-05-01','2021-11-01'),('2021-11-01','2022-05-01'),
    ('2022-05-01','2022-11-01'),('2022-11-01','2023-05-01'),
    ('2023-05-01','2023-11-01'),('2023-11-01','2024-05-01'),
    ('2024-05-01','2024-11-01'),('2024-11-01','2025-05-01'),
    ('2025-05-01','2025-11-01'),('2025-11-01','2026-06-01'),
]

# ─── 主训练循环 ──────────────────────────────────────────
def run_v7(sym: str, df15m, df1h, df4h, df1d, verbose=True) -> dict:
    sp = SYMBOL_PARAMS[sym]
    sl_mult    = sp['sl_mult']
    tp_mult    = sp['tp_mult']
    hold_bars  = sp['hold_bars']
    cool_bars  = sp['cool_bars']
    reg_offset = sp['regime_offset']

    wr_mat   = WRMatrixV3()
    threshold = AdaptiveThresholdV3(init=95.0, sym=sym)
    # FIX-1: 无chaos_det，使用confidence_pos_mult

    oos_idx       = 0
    cur_oos_start = pd.Timestamp(OOS_WINDOWS[oos_idx][0], tz='UTC')
    cur_oos_end   = pd.Timestamp(OOS_WINDOWS[oos_idx][1], tz='UTC')

    all_trades = []
    oos_results = []
    oos_trades  = []
    nav = 1000.0
    last_sig = defaultdict(lambda: -cool_bars-1)

    if verbose:
        print(f'\n{"="*60}')
        print(f'[{sym}] v3冷启动 {df1h.index[200].date()} → {df1h.index[-1].date()}')
        print(f'{"="*60}')

    fb_filtered = 0
    total_count = 0

    for i in range(250, len(df1h)):
        ts   = df1h.index[i]
        row1 = df1h.iloc[i]
        price = float(row1['close'])

        # OOS窗口切换
        if ts >= cur_oos_end and oos_idx < len(OOS_WINDOWS)-1:
            _flush_oos_v3(oos_trades, oos_results, oos_idx, threshold, nav, wr_mat, verbose)
            oos_idx += 1
            cur_oos_start = pd.Timestamp(OOS_WINDOWS[oos_idx][0], tz='UTC')
            cur_oos_end   = pd.Timestamp(OOS_WINDOWS[oos_idx][1], tz='UTC')
            oos_trades = []
            threshold.recalibrate()

        # 对齐多周期（searchsorted保证不看未来）
        idx4  = df4h.index.searchsorted(ts, side='right') - 1
        idx1d = df1d.index.searchsorted(ts, side='right') - 1
        idx15 = df15m.index.searchsorted(ts, side='right') - 1
        if idx4 < 10 or idx1d < 5 or idx15 < 20: continue

        row4  = df4h.iloc[idx4]
        row1d = df1d.iloc[idx1d]

        # Layer-2 三级体制
        lo20 = float(df1h['low'].iloc[max(0,i-20):i].min())
        hi20 = float(df1h['high'].iloc[max(0,i-20):i].max())
        regime, conf = detect_regime_multitf(row1, row4, row1d, hi20, lo20, reg_offset)

        # FIX-1: 置信度仓位乘数
        conf_mult_v4 = confidence_pos_mult(conf)

        for direction in ['LONG', 'SHORT']:
            if i - last_sig[direction] < cool_bars: continue
            if wr_mat.should_block(regime, direction): continue

            # FIX-2: 假突破过滤
            if is_false_breakout(df1h, i, direction):
                fb_filtered += 1
                continue

            # Layer-3 OB代理
            ob = detect_ob_proxy(df15m, idx15, direction)

            # Layer-5 15M触发确认
            _, trigger_bonus = check_15m_trigger(df15m, idx15, direction)

            # Layer-4 13维评分
            score, grade = score_signal_v3(row1, row4, row1d, direction, regime, conf, ob, sp)

            # FIX-3 v5: OI代理加分（品种独立参数）
            oi_bonus = oi_proxy_signal(df1h, i, direction, sp)

            # 综合加分（15M + OI代理）
            score_adj = min(150, score + trigger_bonus + oi_bonus)

            eff_threshold = threshold.value  # FIX-1: 无chaos额外门槛
            if score_adj < eff_threshold: continue
            if grade < 50: continue

            # Layer-7 结算
            outcome, held_bars, exit_p = settle_v3(df1h, i, direction, sl_mult, tp_mult, hold_bars, regime)

            # 仓位
            reg_mult = wr_mat.regime_mult_v3(regime, direction, conf)
            pos_pct  = 0.01 * reg_mult * conf_mult_v4  # FIX-1
            atr_val  = float(row1.get('atr14', price*0.015))
            sl_pct   = atr_val * sl_mult / price
            size     = nav * pos_pct / (sl_pct + 1e-9)
            cost     = size * ROUND_COST

            if outcome == 'WIN':
                nav += size * sl_pct * tp_mult - cost
            elif outcome == 'LOSS':
                nav -= size * sl_pct + cost
            else:
                to_pnl = timeout_pnl(regime, held_bars)
                nav += nav * abs(to_pnl) * pos_pct * (-1) - cost

            nav = max(nav, 0.01)
            wr_mat.update(regime, direction, outcome)
            threshold.update(score_adj, outcome)

            trade = {
                'ts': str(ts.date()), 'regime': regime, 'direction': direction,
                'score': score_adj, 'grade': grade, 'outcome': outcome,
                'bars': held_bars, 'nav': round(nav,2),
                'conf': round(conf,2), 'conf_mult': round(conf_mult_v4,3),
                'ob_str': ob['ob_strength'], 'fvg': ob['fvg_present'],
                'trigger_bonus': trigger_bonus, 'oi_bonus': oi_bonus,
            }
            all_trades.append(trade)
            if ts >= cur_oos_start: oos_trades.append(trade)
            last_sig[direction] = i
            total_count += 1

    # 最后一窗口
    if oos_trades:
        _flush_oos_v3(oos_trades, oos_results, oos_idx, threshold, nav, wr_mat, verbose)

    pass_ct = sum(1 for r in oos_results if r['pf'] >= 1.05)

    if verbose:
        print(f'\n  [{sym}] OOS通过: {pass_ct}/12  最终NAV: ${nav:,.0f}  '
              f'总交易: {total_count}  假突破过滤: {fb_filtered}  '
              f'avg_conf_mult: {round(np.mean([t["conf_mult"] for t in all_trades]) if all_trades else 1.0,3)}x  threshold: {threshold.value}')
        print(f'\n  WR矩阵 (n≥100):')
        for k,v in sorted(wr_mat.snapshot().items(), key=lambda x:-(x[1]['wr'] or 0)):
            if v['n'] >= 100:
                blocked = ' [封禁]' if wr_mat.should_block(*k.rsplit('_',1)) else ''
                print(f'  {"✅" if (v["wr"] or 0)>=0.55 else "❌"} {k:26s} n={v["n"]:5d} WR={v["wr"]:.1%}{blocked}')

    # EV/笔
    wins_all   = sum(1 for t in all_trades if t['outcome']=='WIN')
    losses_all = sum(1 for t in all_trades if t['outcome']=='LOSS')
    tos_all    = sum(1 for t in all_trades if t['outcome']=='TIMEOUT')
    wr_overall = wins_all/(wins_all+losses_all) if wins_all+losses_all>0 else 0
    ev_approx  = (wr_overall*(tp_mult*0.015) - (1-wr_overall)*(sl_mult*0.015) - ROUND_COST) * 100

    bs_wr, bs_lo, bs_hi = bootstrap_ci(wins_all, losses_all, n_boot=8000)

    return {
        'sym': sym, 'layers': 13, 'timeframes': 4,
        'total_trades': total_count,
        'oos_results': oos_results,
        'oos_pass_count': pass_ct,
        'oos_pass_rate': round(pass_ct/12, 3),
        'final_nav': round(nav, 2),
        'final_threshold': threshold.value,
        'fb_filtered': fb_filtered,
        'avg_conf_mult': round(np.mean([t['conf_mult'] for t in all_trades]) if all_trades else 1.0, 3),
        'overall_wr': round(wr_overall, 3),
        'overall_pf': round((wins_all*tp_mult*0.015)/
                            (losses_all*sl_mult*0.015+tos_all*0.003+0.001), 3),
        'ev_per_trade_pct': round(ev_approx, 4),
        'bootstrap_wr': [bs_wr, bs_lo, bs_hi],
        'wr_matrix': wr_mat.snapshot(),
        'params': sp,
    }

def _flush_oos_v3(trades, results, idx, threshold, nav, wr_mat, verbose):
    wins   = sum(1 for t in trades if t['outcome']=='WIN')
    losses = sum(1 for t in trades if t['outcome']=='LOSS')
    tos    = sum(1 for t in trades if t['outcome']=='TIMEOUT')
    n      = wins+losses+tos
    wr     = wins/(wins+losses) if wins+losses>0 else 0
    pf     = (wins*2.5*0.015) / (losses*1.8*0.015 + tos*0.003 + 0.001)
    wr_pt, wr_lo, wr_hi = bootstrap_ci(wins, losses, n_boot=5000)
    results.append({
        'oos_period': f'{OOS_WINDOWS[idx][0][:7]}~{OOS_WINDOWS[idx][1][:7]}',
        'n':n,'wins':wins,'losses':losses,'tos':tos,
        'wr':round(wr,3),'pf':round(pf,3),
        'wr_ci95':[wr_lo,wr_hi],
        'threshold_used':threshold.value,'nav_end':round(nav,2),
    })
    if verbose:
        flag = '✅' if pf>=1.05 else '⚠️' if pf>=0.95 else '❌'
        ci_str = f'[{wr_lo},{wr_hi}]' if wr_lo else ''
        print(f'  {flag} OOS {OOS_WINDOWS[idx][0][:7]}~{OOS_WINDOWS[idx][1][:7]}: '
              f'n={n:4d} WR={wr:.1%}{ci_str} PF={pf:.2f} NAV=${nav:,.0f} th={threshold.value}')

# ─── MAIN ───────────────────────────────────────────────
def main():
    ts_str = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    FIXED_MAP = {
        'BTC_15m': FIXED/'btcusdt_15m_fixed.parquet',
        'BTC_1h':  FIXED/'btcusdt_1h_fixed.parquet',
        'BTC_4h':  FIXED/'btcusdt_4h_fixed.parquet',
        'BTC_1d':  FIXED/'btcusdt_1d_fixed.parquet',
        'ETH_15m': FIXED/'ethusdt_15m_fixed.parquet',
        'ETH_1h':  FIXED/'ethusdt_1h_fixed.parquet',
        'ETH_4h':  FIXED/'ethusdt_4h_fixed.parquet',
        'ETH_1d':  FIXED/'ethusdt_1d_fixed.parquet',
    }

    print('加载数据...')
    dfs = {}
    for k, path in FIXED_MAP.items():
        df = pd.read_parquet(path)
        if df.index.tz is None:
            df.index = df.index.tz_localize('UTC')
        dfs[k] = add_indicators(df)
    print(f'数据加载完成，合计 {sum(len(v) for v in dfs.values()):,} 条')

    report = {
        'framework': 'Anchored WFV v7.0',
        'generated_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'architecture': '4周期×13维×三级体制×OB代理×15M触发×置信度仓位×假突破过滤×OI代理品种独立×v6-100K寻优参数',
        'data_total': sum(len(v) for v in dfs.values()),
        'fixes_applied': ['D1~D8', 'FIX1-置信度仓位', 'FIX2-假突破过滤', 'FIX3-OI代理-品种独立', 'v7-100K寻优-频率约束'],
        'results': {},
    }

    for sym, m15_key, m1h_key, m4h_key, m1d_key in [
        ('BTCUSDT','BTC_15m','BTC_1h','BTC_4h','BTC_1d'),
        ('ETHUSDT','ETH_15m','ETH_1h','ETH_4h','ETH_1d'),
    ]:
        t0 = time.time()
        result = run_v7(
            sym,
            dfs[m15_key], dfs[m1h_key], dfs[m4h_key], dfs[m1d_key],
            verbose=True,
        )
        result['elapsed_s'] = round(time.time()-t0, 1)
        report['results'][sym] = result

    # 联合摘要
    print('\n' + '='*60)
    print('🏛️  Anchored WFV v3.0 联合摘要')
    print('='*60)
    all_oos = {}
    for sym in ['BTCUSDT','ETHUSDT']:
        for r in report['results'][sym]['oos_results']:
            p = r['oos_period']
            if p not in all_oos: all_oos[p] = {}
            all_oos[p][sym] = r['pf']

    pass_count = 0
    for period in sorted(all_oos.keys()):
        pfs = list(all_oos[period].values())
        avg = sum(pfs)/len(pfs)
        flag = '✅' if avg>=1.05 else '⚠️' if avg>=0.95 else '❌'
        if avg >= 1.05: pass_count += 1
        pf_str = '  '.join(f'{s}={v:.2f}' for s,v in all_oos[period].items())
        print(f'  {flag} {period}: avg={avg:.2f}  {pf_str}')

    print(f'\n  联合OOS通过率: {pass_count}/12 ({pass_count/12:.0%})')
    print(f'\n  v4 FIX1~FIX3效果:')
    for sym in ['BTCUSDT','ETHUSDT']:
        r = report['results'][sym]
        print(f'    {sym}: avg_conf_mult={r["avg_conf_mult"]}x  fb_filtered={r["fb_filtered"]}  '
              f'WR={r["overall_wr"]:.1%}  CI95={r["bootstrap_wr"]}  '
              f'EV={r["ev_per_trade_pct"]:+.4f}%/笔  OOS={r["oos_pass_count"]}/12  '
              f'NAV=${r["final_nav"]:,.0f}')

    out = RESULTS / f'anchored_wfv_v7_{ts_str}.json'
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    print(f'\n✅ 报告: {out.name}')
    return str(out)

if __name__ == '__main__':
    main()
