#!/usr/bin/env python3
"""
达摩院 · 离线历史回放引擎 v3.0
====================================
核心使命：
  把梵天系统放进8年历史市场，逐K线回放，
  找出系统的封锁盲区、错杀信号、穿透节点。

架构（v2.0 多周期）：
  ┌──────────────┬──────────────────────────────────────────┐
  │ 扫描层        │ 15M（主力）+ 1H（次要）逐K线触发信号      │
  │ 结构层        │ 1H OB/摆动确认                           │
  │ 上下文层      │ 4H 体制 + 趋势共识                        │
  │ 战略层        │ 1D 大方向                                 │
  └──────────────┴──────────────────────────────────────────┘

信号密度目标：
  v1.0：1H扫描  → ~202条/年 × 8年 = 2,664条
  v2.0：15M+1H  → ~800条/年 × 8年 ≈ 10,000+条

输出：
  dharma/results/replay_{sym}_{TAG}.jsonl   逐信号完整快照
  dharma/results/replay_report_{TAG}.json   诊断报告

数据：data/backtest/fixed/{sym}_*_fixed.parquet
"""
import sys, json, warnings, time, os
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')
BASE    = Path('/root/.openclaw/workspace/trading-system')
FIXED   = BASE / 'data' / 'backtest' / 'fixed'
RESULTS = BASE / 'dharma' / 'results'
RESULTS.mkdir(exist_ok=True)
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'brahma_brain'))

TAG = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')


# ════════════════════════════════════════════════════════════════
# 指标计算工具（离线，纯numpy，零API）
# ════════════════════════════════════════════════════════════════

def _ema(arr: np.ndarray, period: int) -> np.ndarray:
    out = np.empty_like(arr)
    out[:] = np.nan
    k = 2 / (period + 1)
    # 找第一个非NaN
    start = 0
    while start < len(arr) and np.isnan(arr[start]):
        start += 1
    if start >= len(arr):
        return out
    out[start] = arr[start]
    for i in range(start + 1, len(arr)):
        out[i] = arr[i] * k + out[i-1] * (1 - k)
    return out


def _rsi(close: np.ndarray, period: int = 14) -> np.ndarray:
    delta = np.diff(close, prepend=close[0])
    gain  = np.where(delta > 0, delta, 0.0)
    loss  = np.where(delta < 0, -delta, 0.0)
    avg_gain = np.full_like(close, np.nan)
    avg_loss = np.full_like(close, np.nan)
    if len(close) < period + 1:
        return avg_gain
    avg_gain[period] = gain[1:period+1].mean()
    avg_loss[period] = loss[1:period+1].mean()
    for i in range(period + 1, len(close)):
        avg_gain[i] = (avg_gain[i-1] * (period - 1) + gain[i]) / period
        avg_loss[i] = (avg_loss[i-1] * (period - 1) + loss[i]) / period
    rs = np.where(avg_loss == 0, 100.0, avg_gain / avg_loss)
    rsi = 100 - (100 / (1 + rs))
    rsi[:period] = np.nan
    return rsi


def _atr(high: np.ndarray, low: np.ndarray, close: np.ndarray,
         period: int = 14) -> np.ndarray:
    tr = np.maximum(high - low,
         np.maximum(np.abs(high - np.roll(close, 1)),
                    np.abs(low  - np.roll(close, 1))))
    tr[0] = high[0] - low[0]
    atr = np.full_like(tr, np.nan)
    if len(tr) < period:
        return atr
    atr[period-1] = tr[:period].mean()
    alpha = 1 / period
    for i in range(period, len(tr)):
        atr[i] = tr[i] * alpha + atr[i-1] * (1 - alpha)
    return atr


def enrich_df(df: pd.DataFrame) -> pd.DataFrame:
    """为 dataframe 补充技术指标（如原始数据中缺少）"""
    c = df['close'].values.astype(np.float64)
    h = df['high'].values.astype(np.float64)
    l = df['low'].values.astype(np.float64)

    if 'rsi14' not in df.columns:
        df['rsi14'] = _rsi(c, 14)
    if 'ema21' not in df.columns:
        df['ema21'] = _ema(c, 21)
    if 'ema55' not in df.columns:
        df['ema55'] = _ema(c, 55)
    if 'ema200' not in df.columns:
        df['ema200'] = _ema(c, 200)
    if 'atr14' not in df.columns or df['atr14'].isna().all():
        df['atr14'] = _atr(h, l, c, 14)

    # 填充NaN
    for col in ['rsi14', 'ema21', 'ema55', 'ema200', 'atr14']:
        df[col] = df[col].ffill().bfill()

    return df


# ════════════════════════════════════════════════════════════════
# 体制识别（离线版，基于4H指标）
# ════════════════════════════════════════════════════════════════

def get_regime_offline(df4h: pd.DataFrame, idx_4h: int) -> str:
    """
    基于4H指标判断体制，与梵天实盘体制定义对齐：
    BULL_TREND / BULL_EARLY / BULL_CORRECTION /
    BEAR_TREND / BEAR_EARLY / BEAR_RECOVERY / CHOP
    """
    if idx_4h < 5:
        return 'CHOP'

    row = df4h.iloc[idx_4h]
    price   = float(row['close'])
    ema21   = float(row.get('ema21',  price))
    ema55   = float(row.get('ema55',  price))
    ema200  = float(row.get('ema200', price))
    rsi     = float(row.get('rsi14',  50))

    # 最近20根4H的ATR波动率
    w = df4h.iloc[max(0, idx_4h-20):idx_4h+1]
    atr_ratio = float(w['atr14'].mean()) / price if price > 0 else 0.01

    # BULL 体制
    if price > ema21 > ema55 > ema200:
        if rsi > 60:
            return 'BULL_TREND'
        elif rsi > 50:
            return 'BULL_EARLY'
        else:
            return 'BULL_CORRECTION'

    # BEAR 体制
    if price < ema21 < ema55 < ema200:
        if rsi < 40:
            return 'BEAR_TREND'
        elif rsi < 50:
            return 'BEAR_EARLY'
        else:
            return 'BEAR_RECOVERY'

    # 过渡/震荡
    if price > ema55 and rsi > 52:
        return 'BULL_EARLY'
    if price < ema55 and rsi < 48:
        return 'BEAR_EARLY'

    return 'CHOP'


def precompute_regime_series(df4h: pd.DataFrame) -> None:
    """
    [P3 2026-06-27] 向量化预计算4H体制序列 + bars_in_regime。
    结果写入 df4h['_regime_pre'] 和 df4h['_bars_in_regime'] 两列。
    避免 get_context() 每次回溯60根的O(n*60)开销 → O(n)。
    """
    n = len(df4h)
    regimes = [None] * n
    bars    = [1]   * n

    # 先计算每根K线的体制（不依赖 bars_in_regime）
    for i in range(n):
        regimes[i] = get_regime_offline(df4h, i)

    # 顺序扫描，累计连续相同体制的根数
    for i in range(1, n):
        if regimes[i] == regimes[i - 1]:
            bars[i] = bars[i - 1] + 1
        else:
            bars[i] = 1

    df4h['_regime_pre']    = regimes
    df4h['_bars_in_regime'] = bars


# ════════════════════════════════════════════════════════════════
# 摆动结构检测（用于15M/1H信号触发）
# ════════════════════════════════════════════════════════════════

def find_swing_ob(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
                  atrs: np.ndarray, idx: int,
                  lookback_short: int = 8,   # 15M: 2H等效 / 1H: 8H等效
                  lookback_long:  int = 20,  # 15M: 5H等效 / 1H: 20H等效
                  ) -> dict:
    """
    检测当前K线是否触及摆动高/低点的OB区域。
    返回: {'triggered': bool, 'direction': SHORT/LONG, 'ob_hi': float, 'ob_lo': float}
    """
    if idx < lookback_long + 2:
        return {'triggered': False}

    price = closes[idx]
    atr   = atrs[idx]

    # 短周期摆动高低点（精确OB）
    w_s = slice(idx - lookback_short, idx)
    sw_hi_s = float(highs[w_s].max())
    sw_lo_s = float(lows[w_s].min())

    # 长周期摆动（用于趋势确认）
    w_l = slice(idx - lookback_long, idx)
    sw_hi_l = float(highs[w_l].max())
    sw_lo_l = float(lows[w_l].min())

    ob_tol = atr * 0.4  # 进入OB区容忍度

    # 空头OB：价格触及短周期摆动高点
    if sw_hi_s - ob_tol <= price <= sw_hi_s + ob_tol * 0.5:
        # 确认：长周期高点在上方（结构完整）
        if sw_hi_l >= sw_hi_s - atr * 0.1:
            return {
                'triggered': True,
                'direction': 'SHORT',
                'ob_hi': sw_hi_s + atr * 0.1,
                'ob_lo': sw_hi_s - atr * 0.3,
                'sw_ref': sw_hi_s,
                'ob_type': 'SWING_HIGH',
            }

    # 多头OB：价格触及短周期摆动低点
    if sw_lo_s - ob_tol * 0.5 <= price <= sw_lo_s + ob_tol:
        if sw_lo_l <= sw_lo_s + atr * 0.1:
            return {
                'triggered': True,
                'direction': 'LONG',
                'ob_hi': sw_lo_s + atr * 0.3,
                'ob_lo': sw_lo_s - atr * 0.1,
                'sw_ref': sw_lo_s,
                'ob_type': 'SWING_LOW',
            }

    return {'triggered': False}


# ════════════════════════════════════════════════════════════════
# 多周期上下文构建
# ════════════════════════════════════════════════════════════════

def get_context(df1h: pd.DataFrame, df4h: pd.DataFrame, df1d: pd.DataFrame,
                ts: pd.Timestamp) -> dict:
    """获取1H/4H/1D上下文，用于多周期共识判断"""
    # 1H
    idx_1h = df1h.index.searchsorted(ts, side='right') - 1
    idx_1h = max(0, min(idx_1h, len(df1h)-1))
    # 4H
    idx_4h = df4h.index.searchsorted(ts, side='right') - 1
    idx_4h = max(0, min(idx_4h, len(df4h)-1))
    # 1D
    idx_1d = df1d.index.searchsorted(ts, side='right') - 1
    idx_1d = max(0, min(idx_1d, len(df1d)-1))

    r1h = df1h.iloc[idx_1h]
    r4h = df4h.iloc[idx_4h]
    r1d = df1d.iloc[idx_1d]

    price   = float(r1h['close'])

    # [P3 优化] 使用预计算列直接读取，避免 O(n*60) 回溯
    if '_regime_pre' in df4h.columns:
        regime         = str(r4h['_regime_pre'])
        bars_in_regime = int(r4h['_bars_in_regime'])
    else:
        # 备用：未预计算时回落到原逻辑（较慢）
        regime = get_regime_offline(df4h, idx_4h)
        bars_in_regime = 1
        for _back in range(1, min(idx_4h, 60)):
            if get_regime_offline(df4h, idx_4h - _back) == regime:
                bars_in_regime += 1
            else:
                break

    ema200_4h = float(r4h.get('ema200', price))
    ema200_1d = float(r1d.get('ema200', price))
    ema55_4h  = float(r4h.get('ema55',  price))
    rsi_4h    = float(r4h.get('rsi14',  50))
    rsi_1h    = float(r1h.get('rsi14',  50))
    atr_1h    = float(r1h.get('atr14',  price * 0.01))
    atr_4h    = float(r4h.get('atr14',  price * 0.015))

    # 趋势共识得分（-3 ~ +3）
    # +1: 1H价格 > EMA55_4H / -1: 1H价格 < EMA55_4H
    # +1: RSI_4H > 50       / -1: RSI_4H < 50
    # +1: 价格 > EMA200_1D  / -1: 价格 < EMA200_1D
    tc = 0
    tc += 1 if price > ema55_4h else -1
    tc += 1 if rsi_4h > 50 else -1
    tc += 1 if price > ema200_1d else -1

    # [v4.0] MTF EMA绝对值（供score_signal v4.0因子4/6使用）
    ema20_1h = float(r1h.get('ema21', r1h.get('ema20', price)))
    ema20_4h = float(r4h.get('ema21', r4h.get('ema20', price)))

    return {
        'regime':          regime,
        'tc':              tc,           # trend_consensus: -3~+3
        'rsi_1h':          rsi_1h,
        'rsi_4h':          rsi_4h,
        'atr_1h':          atr_1h,
        'atr_4h':          atr_4h,
        'price':           price,        # [v4.0] 供因子计算使用
        'ema20_1h':        ema20_1h,     # [v4.0] MTF因子4
        'ema20_4h':        ema20_4h,     # [v4.0] MTF因子4
        'ema200_1d':       ema200_1d,    # [v4.0] FibMacro因子6（绝对值）
        'price_vs_ema200_4h': (price - ema200_4h) / ema200_4h * 100,
        'price_vs_ema200_1d': (price - ema200_1d) / ema200_1d * 100,
        'bars_in_regime':  bars_in_regime,  # [P3] 4H体制持续根数
    }


# ════════════════════════════════════════════════════════════════
# 离线 confluence_score 适配层
# ════════════════════════════════════════════════════════════════
# 修复：2026-06-13  原 score_signal() 是代理函数（0~110分），
# 与实盘 confluence_score（0~150分）不可比，score分段WR失真。
# 修复方案：从离线OHLCV构建最小 ms/smc 结构，直接调用真实评分器。
# 实时依赖字段（OI/FR/链上/鲸鱼/爆仓）设为中性默认值（0分路径）。

try:
    from brahma_brain.brahma_core import confluence_score as _real_confluence
    from brahma_brain.smc_engine import (
        find_order_blocks, find_fvg, find_liquidity_pools,
        smc_score, detect_bos_choch, calc_premium_discount
    )
    _REAL_CONFLUENCE_AVAILABLE = True
except ImportError:
    _REAL_CONFLUENCE_AVAILABLE = False


def _build_offline_ms(direction: str, ctx: dict, rsi_entry: float,
                      ob_type: str, df1h: pd.DataFrame = None,
                      idx_1h: int = -1) -> dict:
    """
    从离线上下文构建最小 ms 结构供 confluence_score 使用。
    实时字段（OI/FR/鲸鱼）设为中性，不贡献得分也不扣分。
    """
    price   = ctx.get('price', 60000.0)
    regime  = ctx.get('regime', 'CHOP')
    tc      = ctx.get('tc', 0)
    rsi_4h  = ctx.get('rsi_4h', 50.0)
    atr_1h  = ctx.get('atr_1h', price * 0.01)

    # 趋势共识映射（tc -3~+3 → consensus字符串）
    if direction == 'LONG':
        if tc >= 3:    consensus = 'FULL_BULL'
        elif tc >= 2:  consensus = 'LEAN_BULL'
        elif tc >= 1:  consensus = 'MIXED_BULL'
        elif tc == 0:  consensus = 'NEUTRAL'
        elif tc >= -1: consensus = 'MIXED_BEAR'
        else:          consensus = 'FULL_BEAR'
    else:
        if tc <= -3:   consensus = 'FULL_BEAR'
        elif tc <= -2: consensus = 'LEAN_BEAR'
        elif tc <= -1: consensus = 'MIXED_BEAR'
        elif tc == 0:  consensus = 'NEUTRAL'
        elif tc <= 1:  consensus = 'MIXED_BULL'
        else:          consensus = 'FULL_BULL'

    # ADX 离线估算（趋势体制给高ADX，震荡给低ADX）
    adx_est = 28.0 if any(x in regime for x in ('TREND', 'EARLY')) else 18.0

    # BB位置估算（RSI代理：超买→高位，超卖→低位）
    bb_pos = (rsi_entry - 30) / 40  # RSI 30~70 → bb_pos 0.0~1.0
    bb_pos = max(0.0, min(1.0, bb_pos))

    ms = {
        'price':  price,
        'regime': regime,
        'trend': {
            'consensus': {'consensus': consensus},
            '1h': {'adx': adx_est},
        },
        'key_levels': {
            'fib': {},  # 离线无斐波那契，s2贡献0分
        },
        'momentum': {
            'rsi_1h':  rsi_entry,
            'rsi_4h':  rsi_4h,
            'rsi_1d':  50.0,    # 离线无1D RSI，中性
            'bb':      {'pos': bb_pos, 'width': 0.03},
            'atr_pct': atr_1h / price * 100,
        },
        'sentiment': {
            'long_short_ratio': 50.0,   # 中性
            'oi':               1.0,    # 有值但不触发额外加分
            'oi_change_pct':    0.0,
            'oi_momentum':      'NEUTRAL',
            'funding_rate':     0.0001, # ~0.01%/8h 轻微多头付息，接近0
        },
        'wave': {
            'wave':  'CORRECTION_ABC' if direction == 'SHORT' else '4W_OR_2W',
            'bias':  direction,
        },
    }
    return ms


def _build_offline_smc(direction: str, price: float,
                       df_slice_o: list, df_slice_h: list,
                       df_slice_l: list, df_slice_c: list) -> dict:
    """
    用离线OHLCV切片计算SMC结构。
    需要至少50根K线。数据不足时返回中性默认结构。
    """
    if len(df_slice_c) < 20:
        return {
            'order_blocks': {'nearest_bull_ob': None, 'nearest_bear_ob': None},
            'fvg':          {},
            'liquidity':    {},
            'score':        {'score': 5},  # 中性分
        }
    try:
        obs    = find_order_blocks(df_slice_o, df_slice_h, df_slice_l, df_slice_c)
        fvgs   = find_fvg(df_slice_h, df_slice_l, df_slice_c)
        liq    = find_liquidity_pools(df_slice_h, df_slice_l, df_slice_c)
        struct = detect_bos_choch(df_slice_h, df_slice_l, df_slice_c)
        pd_z   = calc_premium_discount(max(df_slice_h[-50:]), min(df_slice_l[-50:]), price)
        sc     = smc_score(struct, obs, fvgs, liq, pd_z, direction)

        # ── [BUG-FIX 2026-06-18] find_order_blocks 返回 list，confluence_score 期望 dict ──
        # 将 list 转化为 nearest_bear_ob / nearest_bull_ob 标准格式
        if isinstance(obs, list):
            bear_obs = sorted(
                [o for o in obs if o.get('type') in ('bear','bearish','BEAR','SHORT')],
                key=lambda x: abs(x.get('dist_pct', 99))
            )
            bull_obs = sorted(
                [o for o in obs if o.get('type') in ('bull','bullish','BULL','LONG')],
                key=lambda x: abs(x.get('dist_pct', 99))
            )
            obs_dict = {
                'nearest_bear_ob': bear_obs[0] if bear_obs else None,
                'nearest_bull_ob': bull_obs[0] if bull_obs else None,
                'all': obs,
            }
        else:
            obs_dict = obs if obs else {'nearest_bear_ob': None, 'nearest_bull_ob': None}

        return {
            'order_blocks': obs_dict,
            'fvg':          fvgs,
            'liquidity':    liq,
            'score':        sc,
        }
    except Exception as e:
        return {
            'order_blocks': {'nearest_bull_ob': None, 'nearest_bear_ob': None},
            'fvg':          {},
            'liquidity':    {},
            'score':        {'score': 5},
        }


def score_signal(direction: str, ctx: dict, rsi_entry: float,
                 ob_type: str,
                 df1h_slice_o: list = None, df1h_slice_h: list = None,
                 df1h_slice_l: list = None, df1h_slice_c: list = None) -> dict:
    """
    离线评分入口：优先调用真实 confluence_score。
    若不可用则降级为旧代理（兼容保底）。
    返回格式与旧版相同：{total, grade, details}
    注：grade 在实盘是0~100的结构质量分，此处用score映射估算（保持回放一致性）。
    """
    # 离线回放模式下，强制使用代理评分（不调用实时_real_confluence）
    # 原因：_real_confluence内部含WebSocket/网络请求，在离线批量回放中会死锁
    _fallback_reason = 'unknown'
    if os.environ.get('OFFLINE_REPLAY') or not _REAL_CONFLUENCE_AVAILABLE:
        _fallback_reason = 'offline_replay_mode'
    elif _REAL_CONFLUENCE_AVAILABLE:
        try:
            import concurrent.futures as _cf
            def _run_confluence():
                price = ctx.get('price', 60000.0)
                ms  = _build_offline_ms(direction, ctx, rsi_entry, ob_type)
                smc = _build_offline_smc(
                    direction, price,
                    df1h_slice_o or [], df1h_slice_h or [],
                    df1h_slice_l or [], df1h_slice_c or [],
                )
                return _real_confluence(ms, smc, direction)
            with _cf.ThreadPoolExecutor(max_workers=1) as _ex:
                _fut = _ex.submit(_run_confluence)
                try:
                    result = _fut.result(timeout=5)
                    raw_score = result.get('total', 0)
                    # [v25.4b] 优先使用 effective_grade（体制感知修正后）
                    # effective_grade 由 brahma_core RegimeGrade 层计算
                    _eff_g = result.get('effective_grade')
                    if _eff_g is not None and _eff_g > 0:
                        grade = round(float(_eff_g), 1)
                    else:
                        grade = min(100, int(raw_score / 150 * 100))
                    return {
                        'total':        raw_score,
                        'grade':        grade,
                        'raw_grade':    result.get('grade', grade),
                        'grade_mult':   result.get('grade_mult', 1.0),
                        'effective_grade': grade,
                        'details':      result.get('breakdown', {}),
                    }
                except _cf.TimeoutError:
                    _fallback_reason = 'confluence_score timeout(5s)'
        except Exception as _e:
            _fallback_reason = str(_e)[:80]
    else:
        _fallback_reason = 'brahma_core not available'

    # ── [v4.0 苏摩111 2026-06-28] 升级代理评分：6因子离线化 ────────────
    # 目标：score范围 0~175，与实盘138阈值对应75%重叠
    # 本地计算，零API消耗，6.6年全量可跑
    score = 0
    details = {'_fallback': _fallback_reason if '_fallback_reason' in dir() else 'unknown',
               '_version': 'v4.0'}

    regime  = ctx['regime']
    price   = ctx.get('price', 60000.0)
    rsi     = rsi_entry
    bars    = int(ctx.get('bars_in_regime', 0) or 0)

    # ── 因子1: OB基础质量 (0~60) ─────────────────────────────────────
    # 沿用旧体制分数作为OB质量代理（与实盘ob_grade对齐）
    _OB_BASE = {
        'LONG': {
            'BULL_TREND':50,'BULL_EARLY':45,'BULL_CORRECTION':30,
            'BEAR_RECOVERY':25,'CHOP':20,'BEAR_EARLY':12,'BEAR_TREND':5,
        },
        'SHORT': {
            'BEAR_TREND':55,'BEAR_EARLY':48,'BEAR_RECOVERY':35,
            'CHOP':28,'BULL_CORRECTION':18,'BULL_EARLY':10,'BULL_TREND':5,
        },
    }
    ob_base = _OB_BASE.get(direction, {}).get(regime, 15)
    score += ob_base
    details['ob_base'] = ob_base

    # ── 因子2: 趋势共识 TC (0~20) ────────────────────────────────────
    tc = ctx['tc']
    if direction == 'LONG':
        tc_score = max(0, int((tc + 3) / 6 * 20))
    else:
        tc_score = max(0, int((-tc + 3) / 6 * 20))
    score += tc_score
    details['tc_score'] = tc_score

    # ── 因子3: 体制乘数（核心倍数，与实盘_REGIME_MULT对齐）──────────
    _REGIME_MULT_V4 = {
        # (regime_prefix, direction): mult
        'BEAR_TREND_SHORT':  1.60, 'BULL_TREND_LONG':   1.60,
        'BEAR_EARLY_SHORT':  1.45, 'BULL_EARLY_LONG':   1.45,
        'BEAR_RECOVERY_LONG':1.30, 'BULL_CORRECTION_SHORT':1.20,
        'CHOP_SHORT':        0.88, 'CHOP_LONG':         0.50,
        'BEAR_TREND_LONG':   0.45, 'BULL_TREND_SHORT':  0.45,
    }
    _mult_key = f'{regime}_{direction}'
    _mult = 1.0
    for k, v in _REGIME_MULT_V4.items():
        if _mult_key.startswith(k) or k in _mult_key:
            _mult = v; break
    score = int(score * _mult)
    details['regime_mult'] = _mult

    # ── 因子4: MTF多周期趋势共识 (±8) ────────────────────────────────
    # 本地计算：1H/4H/1D EMA方向对比
    _mtf_delta = 0
    _ema_1h  = ctx.get('ema20_1h', ctx.get('ema_1h', price))
    _ema_4h  = ctx.get('ema20_4h', ctx.get('ema_4h', price))
    _ema_1d  = ctx.get('ema200_1d', ctx.get('ema_1d', price))
    if direction == 'SHORT':
        # 三周期都空头排列 → +8；混乱 → 0；全多 → -8
        _bear_cnt = sum([price < _ema_1h, price < _ema_4h, price < _ema_1d])
        _mtf_delta = (_bear_cnt - 1) * 4   # 0→-4, 1→0, 2→+4, 3→+8
    else:
        _bull_cnt = sum([price > _ema_1h, price > _ema_4h, price > _ema_1d])
        _mtf_delta = (_bull_cnt - 1) * 4
    _mtf_delta = max(-8, min(8, _mtf_delta))
    score += _mtf_delta
    details['mtf_consensus'] = _mtf_delta

    # ── 因子5: RSI极端分层 (±8) ─────────────────────────────────────
    # 达摩院v3.0铁证：RSI>60做空 WR=68.1% EV=+0.458%（BTC BEAR_TREND）
    _rsi_delta = 0
    if direction == 'SHORT':
        if rsi > 70:    _rsi_delta = 8   # 强超买做空 → 最优
        elif rsi > 60:  _rsi_delta = 5   # RSI>60 → 铁证区
        elif rsi > 50:  _rsi_delta = 2
        elif rsi < 35:  _rsi_delta = -5  # 超卖做空 → 危险
    else:
        if rsi < 30:    _rsi_delta = 8
        elif rsi < 40:  _rsi_delta = 5
        elif rsi < 50:  _rsi_delta = 2
        elif rsi > 65:  _rsi_delta = -5
    score += _rsi_delta
    details['rsi_layer'] = _rsi_delta

    # ── 因子6: 1D FibMacro方向 (+6/0/-6) ─────────────────────────────
    # N21近似：价格 vs EMA200_1D
    # ctx['ema200_1d'] = 绝对均线值（v4.0已在get_context传入）
    _fib_delta = 0
    _ema200_1d = ctx.get('ema200_1d', 0)
    if _ema200_1d > 1000:   # 合理的BTC/ETH价格（非0/空）
        if direction == 'SHORT' and price < _ema200_1d:
            _fib_delta = 6   # DEEP_BEAR：价格在EMA200下方做空 ✅
        elif direction == 'SHORT' and price > _ema200_1d * 1.10:
            _fib_delta = -3  # 强牛市高位做空 = 逆势风险
        elif direction == 'LONG' and price > _ema200_1d:
            _fib_delta = 6   # DEEP_BULL：价格在EMA200上方做多 ✅
        elif direction == 'LONG' and price < _ema200_1d * 0.90:
            _fib_delta = -3  # 强熊市做多 = 逆势风险
    score += _fib_delta
    details['fib_macro'] = _fib_delta

    # ── 因子7: WR铁证矩阵查表 (+4/0) ─────────────────────────────────
    # N22b近似：体制+方向组合的已验证WR
    _WR_BONUS = {
        'BEAR_TREND_SHORT': 4, 'BULL_TREND_LONG': 4,
        'BEAR_EARLY_SHORT': 3, 'BULL_EARLY_LONG': 3,
        'BEAR_RECOVERY_LONG': 4,
    }
    _wr_bonus = _WR_BONUS.get(f'{regime}_{direction}', 0)
    # 死穴惩罚
    if regime == 'BEAR_TREND' and direction == 'LONG':   _wr_bonus = -8
    if regime == 'BULL_TREND' and direction == 'SHORT':  _wr_bonus = -8
    score += _wr_bonus
    details['wr_matrix'] = _wr_bonus

    # ── 因子8: P3体制新鲜度 (+15/+8/0) ──────────────────────────────
    # 达摩院v3.0铁证：age≤2根 WR=75.6% EV=+0.687%（BTC BEAR_TREND_SHORT）
    _p3_delta = 0
    _trend_dir_map = {'BEAR_TREND': 'SHORT', 'BULL_TREND': 'LONG',
                      'BEAR_EARLY': 'SHORT', 'BULL_EARLY': 'LONG'}
    _expected_dir = _trend_dir_map.get(regime)
    if _expected_dir == direction:
        if bars <= 2:   _p3_delta = 15  # 黄金窗口 WR=75.6%
        elif bars <= 4: _p3_delta = 8   # 早期窗口 WR=62.6%
    # 死穴惩罚：BEAR_TREND逆势LONG
    if regime in ('BEAR_TREND', 'BULL_TREND') and _expected_dir and _expected_dir != direction:
        _p3_delta = -10
    score += _p3_delta
    details['p3_fresh'] = _p3_delta

    # ── 因子9: s23-Kronos离线基准 (+4固定) ────────────────────────────
    # 实盘s23平均+8分，离线无ML模型，给固定+4基准
    score += 4
    details['kronos_baseline'] = 4

    # ── 最终裁剪 ──────────────────────────────────────────────────────
    score = max(0, min(int(score), 175))

    # grade：用score/175*100映射到0-100
    grade = min(100, int(score / 175 * 100))
    _GRADE_MULT = {
        'BEAR_TREND_SHORT':1.0,'BULL_TREND_LONG':1.0,'BEAR_EARLY_SHORT':0.95,
        'CHOP_SHORT':0.88,'CHOP_LONG':0.50,'BEAR_TREND_LONG':0.45,
    }
    _gk = f'{regime}_{direction}'
    _gmult = next((v for k,v in _GRADE_MULT.items() if _gk.startswith(k)), 1.0)
    _eff_grade = round(grade * _gmult, 1)

    return {
        'total':           score,
        'grade':           _eff_grade,
        'raw_grade':       grade,
        'grade_mult':      _gmult,
        'effective_grade': _eff_grade,
        'details':         details,
    }


# ════════════════════════════════════════════════════════════════
# 结算引擎
# ════════════════════════════════════════════════════════════════

# ── [v3.0 苏摩111 2026-06-28] 体制分类出场参数（与实盘v4.0完全对齐）──────
# 铁证：BEAR 17H WR=63.5% EV=+0.361% | CHOP 12H WR=50.9% EV=+0.013%
EXIT_PARAMS_V3 = {
    'BEAR_TREND':    {'sl_pct': 2.0, 'rr': 1.0, 'hold_1h': 17},
    'BEAR_EARLY':    {'sl_pct': 2.0, 'rr': 1.0, 'hold_1h': 16},
    'BEAR_RECOVERY': {'sl_pct': 2.0, 'rr': 1.0, 'hold_1h': 12},
    'BEAR_CRASH':    {'sl_pct': 2.0, 'rr': 1.0, 'hold_1h': 14},
    'BULL_TREND':    {'sl_pct': 2.0, 'rr': 1.0, 'hold_1h': 17},
    'BULL_EARLY':    {'sl_pct': 2.0, 'rr': 1.0, 'hold_1h': 16},
    'BULL_CORRECTION':{'sl_pct':2.0, 'rr': 1.0, 'hold_1h': 12},
    'CHOP':          {'sl_pct': 2.5, 'rr': 1.0, 'hold_1h': 12},
    'CHOP_HIGH':     {'sl_pct': 2.5, 'rr': 1.0, 'hold_1h': 10},
    'CHOP_MID':      {'sl_pct': 2.5, 'rr': 1.0, 'hold_1h': 12},
    'CHOP_LOW':      {'sl_pct': 2.5, 'rr': 1.0, 'hold_1h': 12},
}
_DEFAULT_EXIT = {'sl_pct': 2.0, 'rr': 1.0, 'hold_1h': 16}


def settle_signal(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
                  entry_idx: int, entry_price: float,
                  sl: float, tp: float,
                  hold_max_bars: int, direction: str) -> dict:
    """无前视偏差的持仓结算（保留旧接口兼容性）"""
    return _settle_core(highs, lows, closes, entry_idx, entry_price,
                        sl, tp, hold_max_bars, direction)


def settle_signal_v3(
    df1h: 'pd.DataFrame',
    entry_ts: 'pd.Timestamp',
    entry_price: float,
    regime: str,
    direction: str,
) -> dict:
    """
    [v3.0] 1H粒度结算引擎 · 体制分类出场参数
    ─────────────────────────────────────────
    入参：
      df1h        - 全量1H DataFrame（含high/low/close）
      entry_ts    - 信号触发时间戳
      entry_price - 入场价格
      regime      - 4H体制字符串
      direction   - SHORT / LONG
    出参：
      result / pnl_pct / wr_pct / sl_pct / rr / hold_bars_1h /
      max_favorable_pct / max_adverse_pct
    """
    # 匹配出场参数（前缀匹配）
    cfg = _DEFAULT_EXIT
    for k, v in EXIT_PARAMS_V3.items():
        if regime.upper().startswith(k):
            cfg = v
            break

    sl_pct   = cfg['sl_pct']
    rr       = cfg['rr']
    tp_pct   = sl_pct * rr
    hold_1h  = cfg['hold_1h']
    COST     = 0.0004

    # 计算绝对SL/TP价格
    if direction == 'SHORT':
        sl_price = entry_price * (1 + sl_pct / 100)
        tp_price = entry_price * (1 - tp_pct / 100)
    else:
        sl_price = entry_price * (1 - sl_pct / 100)
        tp_price = entry_price * (1 + tp_pct / 100)

    # 找1H数据中的起始位置
    try:
        idx = df1h.index.searchsorted(entry_ts, side='left')
    except Exception:
        return {'result': 'ERROR', 'pnl_pct': 0, 'sl_pct': sl_pct, 'rr': rr, 'hold_bars_1h': 0,
                'max_favorable_pct': 0, 'max_adverse_pct': 0}

    result   = 'TIMEOUT'
    hold_bars = 0
    max_fav  = 0.0
    max_adv  = 0.0
    exit_price = entry_price

    n1h = len(df1h)
    for j in range(idx + 1, min(idx + hold_1h + 1, n1h)):
        row = df1h.iloc[j]
        h, l = float(row['high']), float(row['low'])
        hold_bars += 1

        if direction == 'SHORT':
            adv = (h - entry_price) / entry_price * 100
            fav = (entry_price - l) / entry_price * 100
            if h >= sl_price:
                result = 'SL'; exit_price = sl_price; break
            if l <= tp_price:
                result = 'TP'; exit_price = tp_price; break
        else:
            adv = (entry_price - l) / entry_price * 100
            fav = (h - entry_price) / entry_price * 100
            if l <= sl_price:
                result = 'SL'; exit_price = sl_price; break
            if h >= tp_price:
                result = 'TP'; exit_price = tp_price; break

        max_adv = max(max_adv, adv)
        max_fav = max(max_fav, fav)

    if result == 'TIMEOUT':
        exit_price = float(df1h.iloc[min(idx + hold_1h, n1h - 1)]['close'])

    if direction == 'SHORT':
        pnl = (entry_price - exit_price) / entry_price - COST
    else:
        pnl = (exit_price - entry_price) / entry_price - COST

    return {
        'result':             result,
        'pnl_pct':            round(pnl * 100, 4),
        'sl_pct':             sl_pct,
        'rr':                 rr,
        'tp_pct':             tp_pct,
        'hold_bars_1h':       hold_bars,
        'exit_price':         round(exit_price, 6),
        'max_favorable_pct':  round(max_fav, 3),
        'max_adverse_pct':    round(max_adv, 3),
    }


def _settle_core(highs, lows, closes, entry_idx, entry_price, sl, tp, hold_max_bars, direction):
    """旧版15M结算（内部使用，兼容）"""
    n = len(closes)
    COST = 0.0004
    result = 'TIMEOUT'; hold_bars = 0
    exit_idx = min(entry_idx + hold_max_bars, n - 1)
    max_adv = max_fav = 0.0

    for j in range(entry_idx + 1, min(entry_idx + hold_max_bars + 1, n)):
        h, l = highs[j], lows[j]; hold_bars += 1
        if direction == 'SHORT':
            adv = (h - entry_price) / entry_price
            fav = (entry_price - l) / entry_price
            if h >= sl: result = 'SL'; exit_idx = j; break
            if l <= tp: result = 'TP'; exit_idx = j; break
        else:
            adv = (entry_price - l) / entry_price
            fav = (h - entry_price) / entry_price
            if l <= sl: result = 'SL'; exit_idx = j; break
            if h >= tp: result = 'TP'; exit_idx = j; break
        max_adv = max(max_adv, adv); max_fav = max(max_fav, fav)

    exit_price = float(closes[min(exit_idx, n-1)])
    if direction == 'SHORT': pnl = (entry_price - exit_price) / entry_price - COST
    else: pnl = (exit_price - entry_price) / entry_price - COST
    return {
        'result': result, 'pnl_pct': round(pnl*100,4), 'pnl_5x': round(pnl*5*100,4),
        'hold_bars': hold_bars, 'exit_price': round(exit_price,4),
        'max_adverse_pct': round(max_adv*100,3), 'max_favorable_pct': round(max_fav*100,3),
    }


# ════════════════════════════════════════════════════════════════
# 多周期扫描器
# ════════════════════════════════════════════════════════════════

def scan_signals_multi_tf(
    sym: str,
    df15m: pd.DataFrame,
    df1h:  pd.DataFrame,
    df4h:  pd.DataFrame,
    df1d:  pd.DataFrame,
    # 15M参数
    m15_lookback_short: int   = 6,
    m15_lookback_long:  int   = 16,
    m15_sl_mult:        float = 2.0,
    m15_tp_mult:        float = 1.5,
    m15_hold_max:       int   = 16,
    m15_interval:       int   = 2,
    # 1H参数
    h1_lookback_short:  int   = 8,
    h1_lookback_long:   int   = 20,
    h1_sl_mult:         float = 2.0,
    h1_tp_mult:         float = 1.5,
    h1_hold_max:        int   = 24,
    h1_interval:        int   = 1,
    # 通用
    min_score:          int   = 120,  # [v4.0 苏摩111] score范围0~175，对应实盘138映射
    verbose:            bool  = False,
    out_path            = None,  # 流式写入路径，断点续跟
) -> list:
    """
    主扫描函数：15M + 1H 双层扫描
    4H提供体制，1D提供战略方向
    out_path: 若提供，每1000条实时追加写入，中断后可续跑
    """
    # 流式写入初始化
    _out_f = None
    if out_path:
        import pathlib
        pathlib.Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        _out_f = open(out_path, 'w')

    records = []
    t0 = time.time()

    # ── 15M 扫描 ──────────────────────────────────────────────
    print(f'  [{sym}] 15M 扫描中...', flush=True)
    closes_15m = df15m['close'].values.astype(np.float64)
    highs_15m  = df15m['high'].values.astype(np.float64)
    lows_15m   = df15m['low'].values.astype(np.float64)
    atrs_15m   = df15m['atr14'].values.astype(np.float64)
    rsis_15m   = df15m['rsi14'].values.astype(np.float64)
    n15 = len(df15m)

    last_sig_15m = {'SHORT': -(m15_hold_max + 1), 'LONG': -(m15_hold_max + 1)}

    for i in range(m15_lookback_long + 10, n15 - m15_hold_max - 1, m15_interval):
        if np.isnan(atrs_15m[i]) or np.isnan(rsis_15m[i]):
            continue

        ts = df15m.index[i]
        ob = find_swing_ob(highs_15m, lows_15m, closes_15m, atrs_15m, i,
                           lookback_short=m15_lookback_short,
                           lookback_long=m15_lookback_long)
        if not ob['triggered']:
            continue

        direction = ob['direction']
        if i - last_sig_15m[direction] < m15_hold_max // 2:
            continue

        price = closes_15m[i]
        atr   = atrs_15m[i]
        rsi   = rsis_15m[i]

        ctx = get_context(df1h, df4h, df1d, ts)
        ctx['price'] = float(price)  # 注入price供 _build_offline_ms 使用
        # 取当前1H切片（最多60根，用于SMC离线计算）
        idx_1h = df1h.index.searchsorted(ts, side='right') - 1
        idx_1h = max(0, min(idx_1h, len(df1h)-1))
        _sl = max(0, idx_1h - 60)
        _df1h_slc = df1h.iloc[_sl:idx_1h+1]
        cf  = score_signal(
            direction, ctx, rsi, ob['ob_type'],
            df1h_slice_o=_df1h_slc['open'].tolist(),
            df1h_slice_h=_df1h_slc['high'].tolist(),
            df1h_slice_l=_df1h_slc['low'].tolist(),
            df1h_slice_c=_df1h_slc['close'].tolist(),
        )
        score = cf['total']
        # [effective_grade v25.4b] 使用体制感知grade做统计分层
        grade = cf.get('effective_grade') or cf['grade']  # 优先体制修正值

        sl_price = price + atr * m15_sl_mult if direction == 'SHORT' else price - atr * m15_sl_mult
        tp_price = price - atr * m15_tp_mult if direction == 'SHORT' else price + atr * m15_tp_mult

        settlement = settle_signal(highs_15m, lows_15m, closes_15m,
                                   i, price, sl_price, tp_price,
                                   m15_hold_max, direction)
        # [v3.0] 并行用1H窗口+体制分类参数结算（实盘对齐）
        _v3_result = settle_signal_v3(df1h, ts, price, ctx['regime'], direction)
        settlement['result_v3']           = _v3_result['result']
        settlement['pnl_pct_v3']          = _v3_result['pnl_pct']
        settlement['sl_pct_v3']           = _v3_result['sl_pct']
        settlement['rr_v3']               = _v3_result['rr']
        settlement['hold_bars_1h']        = _v3_result['hold_bars_1h']
        settlement['max_favorable_v3']    = _v3_result['max_favorable_pct']
        settlement['max_adverse_v3']      = _v3_result['max_adverse_pct']

        reject_reasons = []
        if score < min_score:
            reject_reasons.append(f'score={score}<{min_score}')
        # [v25.4b] 注意：grade 已是 effective_grade（体制修正后），门控自动感知体制
        if grade < 55:  # [v4.0] grade=score/175*100，55≈score97（宽结构门）
            reject_reasons.append(f'grade={grade}<55')

        records.append({
            'ts':        str(ts)[:16],
            'sym':       sym,
            'tf':        '15M',
            'direction': direction,
            'regime':    ctx['regime'],
            'tc':        ctx['tc'],
            'price':     round(price, 4),
            'atr':       round(atr, 4),
            'rsi':       round(rsi, 1),
            'rsi_4h':    round(ctx['rsi_4h'], 1),
            'score':     score,
            'grade':     grade,
            'p_up':      cf.get('s23_p_up', 0.5),  # [CHOP专项] Kronos p_up
            'bars_in_regime': int(ctx.get('bars_in_regime', 0)),  # [P3] 4H体制持续根数（ctx计算）
            # [v3.0] 新增分析维度
            'rsi_layer':   'RSI>60' if rsi > 60 else ('RSI<40' if rsi < 40 else 'RSI40-60'),
            'p3_fresh':    int(ctx.get('bars_in_regime', 99)) <= 2,  # P3黄金窗口
            'daily_above_ema200': ctx.get('price_vs_ema200_1d', 0) > 0,  # 1D趋势
            # v3.0结算字段（在settlement步骤后填充）
            'sl':        round(sl_price, 4),
            'tp':        round(tp_price, 4),
            'sw_ref':    round(ob['sw_ref'], 4),
            'pass':      len(reject_reasons) == 0,
            'reject':    reject_reasons,
            **settlement,
        })
        last_sig_15m[direction] = i

        if verbose and len(records) % 1000 == 0:
            pct = i / n15 * 100
            print(f'    15M {pct:.0f}% n={len(records):,} ({(time.time()-t0):.0f}s)', flush=True)
            # 实时刷盘到文件，支持断点续跟
            if _out_f:
                for rec in records[-1000:]:
                    _out_f.write(json.dumps(rec, ensure_ascii=False, default=str) + '\n')
                _out_f.flush()

    n_15m = len(records)
    print(f'  [{sym}] 15M 完成: {n_15m:,}条  ({(time.time()-t0):.1f}s)', flush=True)
    # 15M完成后写入剩余尾部记录
    if _out_f:
        tail_start = (n_15m // 1000) * 1000
        for rec in records[tail_start:]:
            _out_f.write(json.dumps(rec, ensure_ascii=False, default=str) + '\n')
        _out_f.flush()

    # ── 1H 扫描 ───────────────────────────────────────────────
    print(f'  [{sym}] 1H  扫描中...', flush=True)
    closes_1h = df1h['close'].values.astype(np.float64)
    highs_1h  = df1h['high'].values.astype(np.float64)
    lows_1h   = df1h['low'].values.astype(np.float64)
    atrs_1h   = df1h['atr14'].values.astype(np.float64)
    rsis_1h   = df1h['rsi14'].values.astype(np.float64)
    n1h = len(df1h)

    last_sig_1h = {'SHORT': -(h1_hold_max + 1), 'LONG': -(h1_hold_max + 1)}

    for i in range(h1_lookback_long + 10, n1h - h1_hold_max - 1, h1_interval):
        if np.isnan(atrs_1h[i]) or np.isnan(rsis_1h[i]):
            continue

        ts = df1h.index[i]
        ob = find_swing_ob(highs_1h, lows_1h, closes_1h, atrs_1h, i,
                           lookback_short=h1_lookback_short,
                           lookback_long=h1_lookback_long)
        if not ob['triggered']:
            continue

        direction = ob['direction']
        if i - last_sig_1h[direction] < h1_hold_max // 2:
            continue

        price = closes_1h[i]
        atr   = atrs_1h[i]
        rsi   = rsis_1h[i]

        ctx = get_context(df1h, df4h, df1d, ts)
        ctx['price'] = float(price)  # 注入price供 _build_offline_ms 使用
        # 取当前1H切片（最多60根，用于SMC离线计算）
        _sl_1h = max(0, i - 60)
        _df1h_slc = df1h.iloc[_sl_1h:i+1]
        cf  = score_signal(
            direction, ctx, rsi, ob['ob_type'],
            df1h_slice_o=_df1h_slc['open'].tolist(),
            df1h_slice_h=_df1h_slc['high'].tolist(),
            df1h_slice_l=_df1h_slc['low'].tolist(),
            df1h_slice_c=_df1h_slc['close'].tolist(),
        )
        score = cf['total']
        # [effective_grade v25.4b] 使用体制感知grade
        grade = cf.get('effective_grade') or cf['grade']

        sl_price = price + atr * h1_sl_mult if direction == 'SHORT' else price - atr * h1_sl_mult
        tp_price = price - atr * h1_tp_mult if direction == 'SHORT' else price + atr * h1_tp_mult

        settlement = settle_signal(highs_1h, lows_1h, closes_1h,
                                   i, price, sl_price, tp_price,
                                   h1_hold_max, direction)
        # [v3.0] 并行用1H窗口+体制分类参数结算
        _v3_result = settle_signal_v3(df1h, ts, price, ctx['regime'], direction)
        settlement['result_v3']           = _v3_result['result']
        settlement['pnl_pct_v3']          = _v3_result['pnl_pct']
        settlement['sl_pct_v3']           = _v3_result['sl_pct']
        settlement['rr_v3']               = _v3_result['rr']
        settlement['hold_bars_1h']        = _v3_result['hold_bars_1h']
        settlement['max_favorable_v3']    = _v3_result['max_favorable_pct']
        settlement['max_adverse_v3']      = _v3_result['max_adverse_pct']

        reject_reasons = []
        if score < min_score:
            reject_reasons.append(f'score={score}<{min_score}')
        if grade < 55:  # [v4.0] grade=score/175*100，55≈score97（宽结构门）
            reject_reasons.append(f'grade={grade}<55')

        records.append({
            'ts':        str(ts)[:16],
            'sym':       sym,
            'tf':        '1H',
            'direction': direction,
            'regime':    ctx['regime'],
            'tc':        ctx['tc'],
            'price':     round(price, 4),
            'atr':       round(atr, 4),
            'rsi':       round(rsi, 1),
            'rsi_4h':    round(ctx['rsi_4h'], 1),
            'score':     score,
            'grade':     grade,
            'p_up':      cf.get('s23_p_up', 0.5),
            'bars_in_regime': int(ctx.get('bars_in_regime', 0)),  # [P3] 4H体制持续根数（ctx计算）
            'sl':        round(sl_price, 4),
            'tp':        round(tp_price, 4),
            'sw_ref':    round(ob['sw_ref'], 4),
            'pass':      len(reject_reasons) == 0,
            'reject':    reject_reasons,
            **settlement,
        })
        last_sig_1h[direction] = i

    n_1h = len(records) - n_15m
    print(f'  [{sym}] 1H  完成: {n_1h:,}条  ({(time.time()-t0):.1f}s)', flush=True)

    # 全部完成，写入剩余记录并关闭文件
    if _out_f:
        tail_start = (len(records) // 1000) * 1000
        for rec in records[tail_start:]:
            _out_f.write(json.dumps(rec, ensure_ascii=False, default=str) + '\n')
        _out_f.flush()
        _out_f.close()

    return records


# ════════════════════════════════════════════════════════════════
# 诊断报告
# ════════════════════════════════════════════════════════════════

def generate_report(records: list, sym: str) -> dict:
    if not records:
        return {'error': '无信号记录'}

    total    = len(records)
    passed   = [r for r in records if r['pass']]
    rejected = [r for r in records if not r['pass']]
    n_pass   = len(passed)
    n_reject = len(rejected)

    def wr_stats(rs):
        tp = sum(1 for r in rs if r['result'] == 'TP')
        sl = sum(1 for r in rs if r['result'] == 'SL')
        to = sum(1 for r in rs if r['result'] == 'TIMEOUT')
        wr = tp / (tp+sl) if (tp+sl) > 0 else 0
        avg_pnl = float(np.mean([r['pnl_pct'] for r in rs])) if rs else 0
        return {'n': len(rs), 'tp': tp, 'sl': sl, 'to': to,
                'wr': round(wr, 3), 'avg_pnl_pct': round(avg_pnl, 3)}

    # 按 tf × regime × direction 分层
    by_regime = {}
    for r in records:
        k = f'{r["regime"]}_{r["direction"]}'
        if k not in by_regime:
            by_regime[k] = []
        by_regime[k].append(r)
    by_regime_stats = {k: wr_stats(v) for k, v in by_regime.items()}

    # 按 tf 分层
    by_tf = {}
    for r in records:
        tf = r['tf']
        if tf not in by_tf:
            by_tf[tf] = []
        by_tf[tf].append(r)
    by_tf_stats = {tf: wr_stats(v) for tf, v in by_tf.items()}

    # ── [S2升级] by_tf_regime 三维矩阵 (tf × regime × direction) ──────
    # 设计院S2 2026-06-27: 分层揭示15M vs 1H在同一体制下WR是否一致
    by_tf_regime = {}
    for r in records:
        k = f'{r["tf"]}_{r["regime"]}_{r["direction"]}'
        if k not in by_tf_regime:
            by_tf_regime[k] = []
        by_tf_regime[k].append(r)
    by_tf_regime_stats = {k: wr_stats(v) for k, v in by_tf_regime.items()}

    # ── [S2升级] score≥100 高分子集的 by_regime 矩阵 ─────────────────
    # 梵天只执行score≥100信号，这才是真实执行层WR
    records_100 = [r for r in records if r['score'] >= 100]
    if records_100:
        by_regime_100 = {}
        for r in records_100:
            k = f'{r["regime"]}_{r["direction"]}'
            if k not in by_regime_100:
                by_regime_100[k] = []
            by_regime_100[k].append(r)
        by_regime_100_stats = {k: wr_stats(v) for k, v in by_regime_100.items()}
    else:
        by_regime_100_stats = {}

    # ── [S2升级] 时间段稳定性验证（三期切割）────────────────────────
    # 验证矩阵在不同市场周期是否稳定（防前视偏差/过拟合）
    ts_all = sorted([r['ts'] for r in records])
    if len(ts_all) >= 3:
        t0_ts = pd.Timestamp(ts_all[0])
        t1_ts = pd.Timestamp(ts_all[-1])
        span_days = (t1_ts - t0_ts).days
        cut1 = t0_ts + pd.Timedelta(days=span_days // 3)
        cut2 = t0_ts + pd.Timedelta(days=span_days * 2 // 3)
        period_map = {
            'P1_early':  [r for r in records if pd.Timestamp(r['ts']) < cut1],
            'P2_mid':    [r for r in records if cut1 <= pd.Timestamp(r['ts']) < cut2],
            'P3_recent': [r for r in records if pd.Timestamp(r['ts']) >= cut2],
        }
        by_period_stats = {}
        for p_name, p_recs in period_map.items():
            if p_recs:
                # 只统计主战场体制
                bear_short = [r for r in p_recs if 'BEAR' in r['regime'] and r['direction'] == 'SHORT']
                bull_long  = [r for r in p_recs if 'BULL' in r['regime'] and r['direction'] == 'LONG']
                by_period_stats[p_name] = {
                    'n_total':    len(p_recs),
                    'date_range': f'{p_recs[0]["ts"][:10]} ~ {p_recs[-1]["ts"][:10]}',
                    'overall':    wr_stats(p_recs),
                    'bear_short': wr_stats(bear_short) if bear_short else {},
                    'bull_long':  wr_stats(bull_long)  if bull_long  else {},
                }
    else:
        by_period_stats = {}

    # ── [111 死穴分层 2026-06-27] grade × regime × direction × p_up_band ──────────
    # 目标：找到 BULL_EARLY_SHORT / BEAR_EARLY_LONG 内部 WR>=65% 的精华子集
    # ── [P3 2026-06-27] by_regime_age：EARLY体制持续时间×WR分析 ──────────
    # 目标：BEAR_EARLY_STRONG(≤3根) vs WEAK(>5根) WR是否差异显著？
    # 铁证依据：EARLY体制刚进入时信号质量最高（趋势刚确认）
    EARLY_REGIMES = {'BEAR_EARLY_SHORT','BEAR_EARLY_LONG','BULL_EARLY_SHORT','BULL_EARLY_LONG'}
    TREND_REGIMES = {'BEAR_TREND_SHORT','BEAR_TREND_LONG','BULL_TREND_SHORT','BULL_TREND_LONG'}

    by_regime_age = {}  # {regime_dir}__{age_band} → stats
    by_bars_tc    = {}  # {bars_band}__{tc_band}__{regime_dir} → stats (三维)

    for r in records:
        rk   = f'{r["regime"]}_{r["direction"]}'
        bars = r.get('bars_in_regime', 0) or 0
        tc   = r.get('tc', 0) or 0

        # 体制持续时间分段（4H K线根数）
        if bars <= 2:     ab = 'age_1-2_fresh'    # 刚进入：最强确认
        elif bars <= 4:   ab = 'age_3-4_early'    # 早期
        elif bars <= 7:   ab = 'age_5-7_mid'      # 中期
        elif bars <= 12:  ab = 'age_8-12_late'    # 后期
        else:             ab = 'age_13+_stale'    # 体制老化

        # tc 共识分段
        if tc <= -2:   tb = 'tc_strong_bear'
        elif tc == -1: tb = 'tc_lean_bear'
        elif tc == 0:  tb = 'tc_neutral'
        elif tc == 1:  tb = 'tc_lean_bull'
        else:          tb = 'tc_strong_bull'

        # by_regime_age：体制×年龄（EARLY + TREND均统计）
        if rk in EARLY_REGIMES or rk in TREND_REGIMES:
            age_key = f'{rk}__{ab}'
            by_regime_age.setdefault(age_key, []).append(r)

        # by_bars_tc：三维（bars_age × tc × regime_dir）
        if rk in EARLY_REGIMES:
            btc_key = f'{ab}__{tb}__{rk}'
            by_bars_tc.setdefault(btc_key, []).append(r)

    by_regime_age_stats = {k: wr_stats(v) for k,v in by_regime_age.items() if len(v)>=20}
    by_bars_tc_stats    = {k: wr_stats(v) for k,v in by_bars_tc.items()    if len(v)>=20}

    DEATH_REGIMES = {'BULL_EARLY_SHORT', 'BEAR_EARLY_LONG', 'BULL_EARLY_LONG', 'BEAR_EARLY_SHORT'}
    CHOP_REGIMES  = {'CHOP_SHORT', 'CHOP_LONG', 'CHOP_MID_SHORT', 'CHOP_MID_LONG',
                     'CHOP_HIGH_SHORT', 'CHOP_HIGH_LONG', 'CHOP_LOW_SHORT', 'CHOP_LOW_LONG'}

    # ── [CHOP专项 2026-06-27] CHOP体制突破信号三维分层 ────────────────────
    # 目标：找到 CHOP 内部 WR>=63% 的精华子集（突破前兆信号）
    # 维度A: p_up 极值（Kronos 强烈方向判断 <0.30 或 >0.70）
    # 维度B: grade 结构质量 (>=80 优质结构)
    # 维度C: tc 趋势共识 (-3/-2 全空共识 / +2/+3 全多共识)
    by_chop_pup = {}
    by_chop_grade = {}
    by_chop_tc = {}
    by_chop_elite = {}

    for r in records:
        rk = f'{r["regime"]}_{r["direction"]}'
        is_chop = ('CHOP' in r['regime'])
        if not is_chop:
            continue

        pup = r.get('p_up', 0.5) or 0.5
        grade = r['grade']
        tc    = r.get('tc', 0)
        direction = r['direction']

        # 维度A: p_up 分段
        if pup < 0.25:    pb = 'pup_<25_bearish'
        elif pup < 0.35:  pb = 'pup_25-35_lean_bear'
        elif pup < 0.45:  pb = 'pup_35-45_mild_bear'
        elif pup < 0.55:  pb = 'pup_45-55_neutral'
        elif pup < 0.65:  pb = 'pup_55-65_mild_bull'
        elif pup < 0.75:  pb = 'pup_65-75_lean_bull'
        else:             pb = 'pup_>=75_bullish'
        by_chop_pup.setdefault(f'{pb}__{rk}', []).append(r)

        # 维度B: grade
        if grade >= 85:   gb = 'grade_85+'
        elif grade >= 80: gb = 'grade_80-85'
        else:             gb = 'grade_<80'
        by_chop_grade.setdefault(f'{gb}__{rk}', []).append(r)

        # 维度C: tc（趋势共识强度）
        if tc <= -2:   tb = 'tc_strong_bear'
        elif tc == -1: tb = 'tc_lean_bear'
        elif tc == 0:  tb = 'tc_neutral'
        elif tc == 1:  tb = 'tc_lean_bull'
        else:          tb = 'tc_strong_bull'
        by_chop_tc.setdefault(f'{tb}__{rk}', []).append(r)

        # 精华子集：Kronos极值 + grade>=80 + tc共识一致
        pup_extreme  = pup < 0.35 or pup > 0.65
        grade_hi     = grade >= 80
        # 方向一致性：SHORT信号 + Kronos看空 + tc偏空
        if direction == 'SHORT':
            dir_align = (pup < 0.45 and tc <= -1)
        else:
            dir_align = (pup > 0.55 and tc >= 1)
        if pup_extreme and grade_hi and dir_align:
            by_chop_elite.setdefault(f'ELITE__{rk}', []).append(r)

    by_chop_pup_stats   = {k: wr_stats(v) for k,v in by_chop_pup.items()   if len(v)>=20}
    by_chop_grade_stats = {k: wr_stats(v) for k,v in by_chop_grade.items() if len(v)>=20}
    by_chop_tc_stats    = {k: wr_stats(v) for k,v in by_chop_tc.items()    if len(v)>=20}
    by_chop_elite_stats = {k: wr_stats(v) for k,v in by_chop_elite.items() if len(v)>=10}

    # ① grade_band × regime_dir 交叉（发现死穴内高结构子集）
    by_grade_regime = {}
    for r in records:
        rk = f'{r["regime"]}_{r["direction"]}'
        gb = 'grade_90+' if r['grade'] >= 90 else \
             'grade_85-90' if r['grade'] >= 85 else \
             'grade_80-85' if r['grade'] >= 80 else \
             'grade_70-80' if r['grade'] >= 70 else 'grade_<70'
        key = f'{gb}__{rk}'
        if rk in DEATH_REGIMES or r.get('result'):
            by_grade_regime.setdefault(key, []).append(r)
    by_grade_regime_stats = {}
    for k, rs_sub in by_grade_regime.items():
        if len(rs_sub) >= 30:  # 至少30条才统计
            by_grade_regime_stats[k] = wr_stats(rs_sub)

    # ② Kronos p_up_band × regime_dir 交叉（发现死穴内方向一致子集）
    by_pup_regime = {}
    for r in records:
        rk = f'{r["regime"]}_{r["direction"]}'
        pup = r.get('p_up', r.get('s23_p_up', 0.5))
        if pup is None: pup = 0.5
        pb = 'p_up_<0.30' if pup < 0.30 else \
             'p_up_30-40' if pup < 0.40 else \
             'p_up_40-55' if pup < 0.55 else \
             'p_up_55-70' if pup < 0.70 else 'p_up_>=70'
        key = f'{pb}__{rk}'
        by_pup_regime.setdefault(key, []).append(r)
    by_pup_regime_stats = {}
    for k, rs_sub in by_pup_regime.items():
        if len(rs_sub) >= 30:
            by_pup_regime_stats[k] = wr_stats(rs_sub)

    # ③ grade>=85 + p_up方向一致 组合（精华子集核心矩阵）
    by_elite_combo = {}
    for r in records:
        rk = f'{r["regime"]}_{r["direction"]}'
        if rk not in DEATH_REGIMES:
            continue
        pup = r.get('p_up', r.get('s23_p_up', 0.5)) or 0.5
        grade = r['grade']
        direction = r['direction']
        # 方向一致性判断
        if direction == 'SHORT':
            pup_align = 'aligned' if pup < 0.45 else ('neutral' if pup < 0.55 else 'opposed')
        else:
            pup_align = 'aligned' if pup > 0.55 else ('neutral' if pup > 0.45 else 'opposed')
        grade_tier = 'hi' if grade >= 85 else ('mid' if grade >= 80 else 'lo')
        combo_key = f'{rk}__g{grade_tier}__pup{pup_align}'
        by_elite_combo.setdefault(combo_key, []).append(r)
    by_elite_combo_stats = {}
    for k, rs_sub in by_elite_combo.items():
        if len(rs_sub) >= 20:
            by_elite_combo_stats[k] = wr_stats(rs_sub)

    # score分段
    score_bands = [(0,60), (60,80), (80,100), (100,120), (120,138), (138,150), (150,9999)]
    by_score = {}
    for lo, hi in score_bands:
        band_r = [r for r in records if lo <= r['score'] < hi]
        if band_r:
            by_score[f'{lo}-{hi}'] = wr_stats(band_r)

    # grade分段
    grade_bands = [(0,50), (50,60), (60,70), (70,80), (80,100)]  # [v25.4] 70-80为死亡区（WR=47%），已在门控中封堵
    by_grade = {}
    for lo, hi in grade_bands:
        band_r = [r for r in records if lo <= r['grade'] < hi]
        if band_r:
            by_grade[f'{lo}-{hi}'] = wr_stats(band_r)

    # tc分层
    by_tc = {}

    # ── [v3.0] 初始化v3统计容器 ──────────────────────────────────
    by_regime_v3  = {}
    by_rsi_layer  = {}
    by_p3_fresh   = {}
    by_daily_align = {}

    def _v3_bucket(d, k, rv3, pnl_v3):
        if k not in d:
            d[k] = {'n': 0, 'tp': 0, 'sl': 0, 'to': 0, 'pnl': []}
        d[k]['n'] += 1
        if rv3 == 'TP': d[k]['tp'] += 1
        elif rv3 == 'SL': d[k]['sl'] += 1
        else: d[k]['to'] += 1
        d[k]['pnl'].append(pnl_v3)

    def _stats_v3(d_map):
        out = {}
        for k, v in d_map.items():
            settled = v['tp'] + v['sl']
            wr = v['tp'] / max(settled, 1)
            avg_ev = sum(v['pnl']) / max(len(v['pnl']), 1)
            out[k] = {'n': v['n'], 'tp': v['tp'], 'sl': v['sl'], 'to': v['to'],
                      'wr': round(wr, 3), 'ev': round(avg_ev, 3)}
        return out
    # [v3.0] 填充v3统计
    for r in records:
        rv3  = r.get('result_v3', '')
        pv3  = float(r.get('pnl_pct_v3', 0) or 0)
        regi = r.get('regime', 'UNKNOWN')
        dire = r.get('direction', 'SHORT')
        _v3_bucket(by_regime_v3,   f'{regi}_{dire}', rv3, pv3)
        _v3_bucket(by_rsi_layer,   r.get('rsi_layer', '?'), rv3, pv3)
        _v3_bucket(by_p3_fresh,    'P3_fresh' if r.get('p3_fresh') else 'P3_stale', rv3, pv3)
        _v3_bucket(by_daily_align, '1D_bear' if not r.get('daily_above_ema200') else '1D_bull', rv3, pv3)

    for tc_val in [-3, -2, -1, 0, 1, 2, 3]:
        band_r = [r for r in records if r['tc'] == tc_val]
        if band_r:
            by_tc[str(tc_val)] = wr_stats(band_r)

    ts_sorted = sorted([r['ts'] for r in records])
    years = (pd.Timestamp(ts_sorted[-1]) - pd.Timestamp(ts_sorted[0])).days / 365

    # 错杀分析
    missed_tp = [r for r in rejected if r['result'] == 'TP']

    return {
        'sym':        sym,
        'tag':        TAG,
        'generated':  datetime.now(timezone.utc).isoformat(),
        'data_range': f'{ts_sorted[0]} ~ {ts_sorted[-1]}',
        'years':      round(years, 2),
        'total_signals': total,
        'signals_per_year': round(total / years, 0) if years > 0 else 0,

        'passed_stats':   wr_stats(passed),
        'rejected_stats': wr_stats(rejected),

        'missed_tp_count': len(missed_tp),
        'missed_tp_pct':   round(len(missed_tp) / n_reject, 3) if n_reject > 0 else 0,

        'by_tf':              by_tf_stats,
        'by_regime':          by_regime_stats,
        'by_tf_regime':       by_tf_regime_stats,       # [S2] 三维矩阵
        'by_regime_score100': by_regime_100_stats,      # [S2] 高分子集
        'by_period':          by_period_stats,          # [S2] 时间段稳定性
        'by_grade_regime':    by_grade_regime_stats,    # [111] grade×体制分层
        'by_regime_age':      by_regime_age_stats,      # [P3] 体制持续时间×WR
        'by_bars_tc':         by_bars_tc_stats,          # [P3] bars×tc×regime三维
        'by_chop_pup':        by_chop_pup_stats,        # [CHOP] p_up×CHOP分层
        'by_chop_grade':      by_chop_grade_stats,      # [CHOP] grade×CHOP分层
        'by_chop_tc':         by_chop_tc_stats,         # [CHOP] tc×CHOP分层
        'by_chop_elite':      by_chop_elite_stats,      # [CHOP] 精华子集
        'by_pup_regime':      by_pup_regime_stats,      # [111] Kronos_p_up×体制分层
        'by_elite_combo':     by_elite_combo_stats,     # [111] 精华子集核心矩阵
        'by_score_band':      by_score,
        'by_grade_band':      by_grade,
        'by_tc':              by_tc,
        'n_score100':         len(records_100),         # [S2] 执行层信号数
        # ── [v3.0] 实盘对齐统计 ──────────────────────────────────
        'by_regime_v3':   _stats_v3(by_regime_v3),   # 体制分类v4.0参数
        'by_rsi_layer':   _stats_v3(by_rsi_layer),    # RSI分层铁证
        'by_p3_fresh':    _stats_v3(by_p3_fresh),     # P3体制新鲜度
        'by_daily_align': _stats_v3(by_daily_align),  # 1D趋势方向
        'version':        'v3.0',
    }


def print_report(report: dict):
    """控制台打印诊断摘要 [v3.0]"""
    sym = report['sym']
    ver = report.get('version', 'v2.0')
    print(f'\n  {"─"*58}')
    print(f'  【{sym} 诊断摘要 {ver}】')
    print(f'  数据范围: {report["data_range"]}（{report["years"]}年）')
    print(f'  总信号: {report["total_signals"]:,}  ({report["signals_per_year"]:.0f}条/年)')

    ps = report['passed_stats']
    rs = report['rejected_stats']
    print(f'  通过: {ps["n"]:,}条  WR={ps["wr"]:.1%}  avgPnL={ps["avg_pnl_pct"]:.2f}%/信号')
    print(f'  拒绝: {rs["n"]:,}条  错杀率={report["missed_tp_pct"]:.1%}')

    print(f'\n  时间周期分层:')
    for tf, d in sorted(report['by_tf'].items()):
        bar = '█' * int(d['wr'] * 20)
        print(f'    {tf:<5}: n={d["n"]:>6,}  WR={d["wr"]:.0%} {bar}  avgPnL={d["avg_pnl_pct"]:.2f}%')

    print(f'\n  体制×方向 WR（n≥10）:')
    rows = [(k,v) for k,v in report['by_regime'].items() if v['n'] >= 10]
    rows.sort(key=lambda x: x[1]['wr'], reverse=True)
    for k, d in rows:
        bar = '█' * int(d['wr'] * 20)
        flag = '✅' if d['wr'] >= 0.65 else ('❌' if d['wr'] < 0.50 else '➖')
        print(f'    {flag} {k:<35}: n={d["n"]:>5}  WR={d["wr"]:.0%} {bar}')

    print(f'\n  Score分段WR:')
    for band, d in report['by_score_band'].items():
        bar = '█' * int(d['wr'] * 20)
        print(f'    Score {band:<8}: n={d["n"]:>6,}  WR={d["wr"]:.0%} {bar}')

    print(f'\n  趋势共识(tc) WR:')
    for tc, d in sorted(report['by_tc'].items(), key=lambda x: int(x[0])):
        if d['n'] >= 5:
            bar = '█' * int(d['wr'] * 20)
            label = f'tc={tc}'
            print(f'    {label:<6}: n={d["n"]:>6,}  WR={d["wr"]:.0%} {bar}')


# ════════════════════════════════════════════════════════════════
# 主流程
# ════════════════════════════════════════════════════════════════

def main():
    t_global = time.time()
    print('=' * 65)
    print('达摩院 · 离线历史回放引擎 v3.0')
    print('15M + 1H 双层扫描 · 4H体制 · 8年全周期')
    print('=' * 65)

    all_reports = {}

    for sym in ['BTCUSDT', 'ETHUSDT']:
        print(f'\n{"━"*65}')
        print(f'▶ {sym} 加载数据集...')

        df15m = pd.read_parquet(FIXED / f'{sym.lower()}_15m_fixed.parquet')
        df1h  = pd.read_parquet(FIXED / f'{sym.lower()}_1h_fixed.parquet')
        df4h  = pd.read_parquet(FIXED / f'{sym.lower()}_4h_fixed.parquet')
        df1d  = pd.read_parquet(FIXED / f'{sym.lower()}_1d_fixed.parquet')

        # 补充指标
        for df in [df15m, df1h, df4h, df1d]:
            enrich_df(df)

        # [P3 优化] 预计算4H体制序列，避免 get_context 里重复回溯 O(n×60)
        print('  预计算4H体制序列...', flush=True)
        precompute_regime_series(df4h)
        print(f'  4H体制预计算完成 bars_in_regime实例: {df4h["_bars_in_regime"].max()}根(max)', flush=True)

        years = (df15m.index[-1] - df15m.index[0]).days / 365
        print(f'  15M:{len(df15m):,}根 | 1H:{len(df1h):,}根 | 4H:{len(df4h):,}根 | {years:.1f}年')

        out_path = RESULTS / f'replay_{sym.lower()}_{TAG}.jsonl'

        records = scan_signals_multi_tf(
            sym      = sym,
            df15m    = df15m,
            df1h     = df1h,
            df4h     = df4h,
            df1d     = df1d,
            verbose  = True,
            out_path = out_path,   # 流式写入，断点续跟
        )

        print(f'\n  ✅ {sym} 扫描完成: {len(records):,}条')
        print(f'  逐信号文件: {out_path.name}')

        report = generate_report(records, sym)
        all_reports[sym] = report
        print_report(report)

    # 汇总报告
    report_path = RESULTS / f'replay_report_{TAG}.json'
    report_path.write_text(json.dumps({
        'tag': TAG,
        'version': 'v3.0',
        'generated': datetime.now(timezone.utc).isoformat(),
        'elapsed_min': round((time.time()-t_global)/60, 2),
        'reports': all_reports,
    }, indent=2, ensure_ascii=False, default=str))

    elapsed = time.time() - t_global
    print(f'\n{"="*65}')
    print(f'汇总报告: {report_path.name}')
    print(f'总耗时: {elapsed/60:.1f}分钟')
    print('达摩院离线回放 v2.0 ✅')


if __name__ == '__main__':
    main()
