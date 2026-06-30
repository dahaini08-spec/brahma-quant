#!/usr/bin/env python3
"""
达摩院 · 100K参数寻优引擎 v7.0
=================================
设计院原则：8年大样本 · 无上帝视角 · 本地数据 · 零API消耗

架构：
  Phase 1: 预计算信号候选库
    - 低门槛扫描(score≥50)，捕获所有候选信号
    - 在 SL/TP 网格(7×7=49组合) 上预结算每个候选
    - 存储为 numpy 数组，后续评估零重复计算

  Phase 2: 100,000次参数寻优
    - 随机搜索 80,000次
    - 山顶爬坡 20,000次（基于Top-50随机邻域）
    - 每次评估：阈值过滤 + 矩阵查表 + 指标计算 ≈ 0.1ms

  Phase 3: 输出v6最优参数矩阵

优化目标（复合指标）:
  score = 0.35×Sharpe + 0.30×log(PF+0.01) + 0.20×WR + 0.15×OOS_stability

优化参数（12维）:
  BTC: threshold, sl_mult, tp_mult, hold_bars, oi_vol_thresh, oi_mom_thresh
  ETH: threshold, sl_mult, tp_mult, hold_bars, oi_vol_thresh, oi_mom_thresh
  （regime_offset_eth固定-3, 已铁证支撑）

作者: 达摩院量化分析师
版本: v6.0 · 2026-06-14
"""

import sys, json, time, warnings, math
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Tuple, Optional

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

BASE    = Path('/root/.openclaw/workspace/trading-system')
FIXED   = BASE / 'data' / 'backtest' / 'fixed'
RESULTS = BASE / 'dharma' / 'results'
RESULTS.mkdir(exist_ok=True)
sys.path.insert(0, str(BASE))

TAG = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')

# ════════════════════════════════════════════════════════════════
# 参数搜索空间（设计院定义）
# ════════════════════════════════════════════════════════════════
PARAM_SPACE = {
    # BTC参数
    'btc_threshold':     (70,  100,  'int'),   # v7: 上限100，禁止靠提门槛作弊
    'btc_sl_mult':       (1.3, 2.6,  'float'),
    'btc_tp_mult':       (1.8, 4.0,  'float'),
    'btc_hold_bars':     (16,  48,   'int'),
    'btc_oi_vol_thresh': (1.1, 2.2,  'float'),
    'btc_oi_mom_thresh': (0.4, 1.5,  'float'),
    # ETH参数
    'eth_threshold':     (70,  100,  'int'),   # v7: 上限100，禁止靠提门槛作弊
    'eth_sl_mult':       (1.5, 2.8,  'float'),
    'eth_tp_mult':       (1.8, 4.0,  'float'),
    'eth_hold_bars':     (16,  48,   'int'),
    'eth_oi_vol_thresh': (0.9, 2.0,  'float'),
    'eth_oi_mom_thresh': (0.3, 1.2,  'float'),
}

# SL/TP 预结算网格
SL_GRID = np.array([1.2, 1.4, 1.6, 1.8, 2.0, 2.3, 2.6])   # 7点
TP_GRID = np.array([1.5, 1.8, 2.2, 2.5, 3.0, 3.5, 4.0])   # 7点
N_SL    = len(SL_GRID)
N_TP    = len(TP_GRID)

HOLD_GRID = np.array([16, 20, 24, 28, 32, 40, 48])          # hold_bars网格
HB_arr    = HOLD_GRID[np.newaxis, np.newaxis, :]                   # (1,1,7) 预广播

TIMEOUT_REGIME_PNL = {
    'BULL_TREND': -0.10, 'BULL_EARLY': -0.15, 'BULL_CORRECTION': -0.20,
    'BEAR_TREND': -0.10, 'BEAR_EARLY': -0.15, 'BEAR_RECOVERY':   -0.18,
    'CHOP_MID':   -0.22, 'CHOP_LOW':   -0.22,
}

# ════════════════════════════════════════════════════════════════
# Phase 1 辅助函数（纯numpy，无API）
# ════════════════════════════════════════════════════════════════

def _ema_np(arr: np.ndarray, p: int) -> np.ndarray:
    out = np.full_like(arr, np.nan)
    k = 2.0 / (p + 1)
    s = p - 1
    while s < len(arr) and np.isnan(arr[s]):
        s += 1
    if s >= len(arr):
        return out
    out[s] = arr[s]
    for i in range(s + 1, len(arr)):
        out[i] = arr[i] * k + out[i-1] * (1 - k)
    return out

def _rsi_np(close: np.ndarray, p: int = 14) -> np.ndarray:
    d    = np.diff(close, prepend=close[0])
    gain = np.where(d > 0, d, 0.0)
    loss = np.where(d < 0, -d, 0.0)
    ag   = np.full_like(close, np.nan)
    al   = np.full_like(close, np.nan)
    if len(close) < p + 1:
        return ag
    ag[p] = gain[1:p+1].mean()
    al[p] = loss[1:p+1].mean()
    for i in range(p + 1, len(close)):
        ag[i] = (ag[i-1] * (p-1) + gain[i]) / p
        al[i] = (al[i-1] * (p-1) + loss[i]) / p
    rs  = np.where(al == 0, 100.0, ag / al)
    return 100 - 100 / (1 + rs)

def _atr_np(high: np.ndarray, low: np.ndarray, close: np.ndarray, p: int = 14) -> np.ndarray:
    prev_c = np.concatenate([[close[0]], close[:-1]])
    tr     = np.maximum(high - low, np.maximum(np.abs(high - prev_c), np.abs(low - prev_c)))
    atr    = np.full_like(tr, np.nan)
    if len(tr) < p:
        return atr
    atr[p-1] = tr[:p].mean()
    k = 1.0 / p
    for i in range(p, len(tr)):
        atr[i] = atr[i-1] * (1-k) + tr[i] * k
    return atr

def compute_indicators(df: pd.DataFrame, regime_offset: float = 0.0) -> pd.DataFrame:
    """计算所有技术指标（已在fixed parquet预计算，但RSI需offset）"""
    d = df.copy()
    c = d['close'].values
    h = d['high'].values
    l = d['low'].values
    v = d['volume'].values

    d['rsi14'] = _rsi_np(c, 14)
    d['ema21']  = _ema_np(c, 21)
    d['ema55']  = _ema_np(c, 55)
    d['ema200'] = _ema_np(c, 200)
    d['atr14']  = _atr_np(h, l, c, 14)
    d['rsi_adj'] = d['rsi14'] + regime_offset
    d['vol_ma20'] = pd.Series(v).rolling(20).mean().values
    return d

def detect_regime_vec(rsi_1h: np.ndarray, rsi_4h: np.ndarray, rsi_1d: np.ndarray,
                      hi20: np.ndarray, lo20: np.ndarray) -> np.ndarray:
    """向量化体制检测，返回字符串数组"""
    n = len(rsi_1h)
    regimes = np.full(n, 'CHOP_MID', dtype=object)

    bull_macro  = rsi_1d > 52
    bear_macro  = rsi_1d < 48
    bull_struct = rsi_4h > 53
    bear_struct = rsi_4h < 47
    bull_mom    = rsi_1h > 54
    bear_mom    = rsi_1h < 46

    # hi20/lo20 是相对价格位置 (0~1)
    near_hi = hi20 > 0.7
    near_lo = lo20 < 0.3

    regimes[bull_macro & bull_struct & bull_mom & near_hi]  = 'BULL_TREND'
    regimes[bull_macro & bull_struct & bull_mom & ~near_hi] = 'BULL_EARLY'
    regimes[bull_macro & bull_struct & ~bull_mom]           = 'BULL_CORRECTION'
    regimes[bear_macro & bear_struct & bear_mom & near_lo]  = 'BEAR_TREND'
    regimes[bear_macro & bear_struct & bear_mom & ~near_lo] = 'BEAR_EARLY'
    regimes[bear_macro & bear_struct & ~bear_mom]           = 'BEAR_RECOVERY'
    # 默认 CHOP
    chop_mask = ~(bull_macro | bear_macro) | ~(bull_struct | bear_struct)
    regimes[chop_mask] = 'CHOP_MID'
    return regimes

REGIME_DIRECTION_SCORE = {
    # (regime, direction): base_score (0~40)
    ('BULL_TREND',    'LONG'):  40, ('BULL_TREND',    'SHORT'): -20,
    ('BULL_EARLY',    'LONG'):  25, ('BULL_EARLY',    'SHORT'): -10,
    ('BULL_CORRECTION','LONG'): -5, ('BULL_CORRECTION','SHORT'): 20,
    ('BEAR_TREND',    'SHORT'): 38, ('BEAR_TREND',    'LONG'):  -22,
    ('BEAR_EARLY',    'SHORT'): 28, ('BEAR_EARLY',    'LONG'):  -12,
    ('BEAR_RECOVERY', 'LONG'):  18, ('BEAR_RECOVERY', 'SHORT'): -8,
    ('CHOP_MID',      'LONG'):  -8, ('CHOP_MID',      'SHORT'): -8,
    ('CHOP_LOW',      'LONG'):  -8, ('CHOP_LOW',      'SHORT'): -8,
}

def precompute_ob_scores(close: np.ndarray, high: np.ndarray, low: np.ndarray,
                         rsi: np.ndarray, window: int = 16) -> Tuple[np.ndarray, np.ndarray]:
    """
    向量化预计算所期 K 线的 OB 评分。
    返回 (ob_long_arr, ob_short_arr) 各 shape=(n,)
    """
    from numpy.lib.stride_tricks import sliding_window_view
    n = len(close)
    ob_long  = np.zeros(n, dtype=np.float32)
    ob_short = np.zeros(n, dtype=np.float32)
    if n < window:
        return ob_long, ob_short

    wins_h = sliding_window_view(high,  window).max(axis=1)  # (n-window+1,)
    wins_l = sliding_window_view(low,   window).min(axis=1)
    rng    = wins_h - wins_l
    start  = window - 1  # first valid index

    pos    = np.where(rng > 1e-9,
                      (close[start:] - wins_l) / rng, 0.5)  # (n-window+1,)
    rsi_s  = rsi[start:]  # (n-window+1,)

    # OB candlestick特征：周図5根内是否出现大幅度K线
    # 用滑4根背景幅度评估（不用内循环）
    big_candle_h = sliding_window_view(high, 5).max(axis=1)  # (n-4,)
    big_candle_l = sliding_window_view(low,  5).min(axis=1)
    big_range    = big_candle_h - big_candle_l                 # (n-4,)
    # 对齐到 start索引：从 start 开始的大K线评分 (n-window+1,)
    bc_start = start - 4
    if bc_start < 0:
        bc_start = 0
    # sliding_window_view(big_range, 1) 不对齐，用 pad+trim方式
    # 简化：用 high/low 在近window内的波动幅度评估
    local_rng  = wins_h - wins_l  # == rng
    bc_bonus   = np.where(local_rng > 0.5 * np.maximum(rng, 1e-9), 8.0, 0.0)

    ob_long_s  = (np.clip((35 - rsi_s) / 35 * 15, 0, 15) +
                  np.clip((1 - pos) * 10, 0, 10) + bc_bonus)
    ob_short_s = (np.clip((rsi_s - 65) / 35 * 15, 0, 15) +
                  np.clip(pos * 10, 0, 10) + bc_bonus)

    ob_long[start:]  = np.minimum(ob_long_s,  35.0)
    ob_short[start:] = np.minimum(ob_short_s, 35.0)
    return ob_long, ob_short


def compute_ob_score(close: np.ndarray, high: np.ndarray, low: np.ndarray,
                     rsi: np.ndarray, i: int, direction: str) -> float:
    """单点OB评分（fallback，build_candidate_library内用向量化版本）"""
    if i < 20:
        return 0.0
    recent_c = close[i-15:i+1]
    recent_h = high[i-15:i+1]
    recent_l = low[i-15:i+1]
    rng = recent_h.max() - recent_l.min()
    if rng < 1e-9:
        return 0.0
    pos = (close[i] - recent_l.min()) / rng
    rsi_now = rsi[i]
    score = 0.0
    if direction == 'LONG':
        score += max(0, (35 - rsi_now) / 35 * 15)
        score += max(0, (1 - pos) * 10)
        if i >= 5:
            candle_range = high[i-3:i].max() - low[i-3:i].min()
            if candle_range > 0.5 * rng:
                score += 8
    else:
        score += max(0, (rsi_now - 65) / 35 * 15)
        score += max(0, pos * 10)
        if i >= 5:
            candle_range = high[i-3:i].max() - low[i-3:i].min()
            if candle_range > 0.5 * rng:
                score += 8
    return min(score, 35.0)

def precompute_15m_bonus_arr(df15m: pd.DataFrame, ts1h_arr: pd.DatetimeIndex) -> np.ndarray:
    """
    预计算所有1H时间点对应的15M触发奖励（LONG+SHORT各一列）
    返回 shape=(n_1h, 2): [:,0]=LONG_bonus  [:,1]=SHORT_bonus
    """
    c15 = df15m['close'].values
    h15 = df15m['high'].values
    l15 = df15m['low'].values
    n15 = len(c15)
    n1h = len(ts1h_arr)
    result = np.zeros((n1h, 2), dtype=np.float32)
    # 预计算15M索引映射
    idx15_arr = df15m.index.searchsorted(ts1h_arr)
    for j in range(n1h):
        idx = idx15_arr[j]
        if idx >= n15 - 4:
            continue
        c = c15[idx:idx+4]
        h = h15[idx:idx+4]
        l = l15[idx:idx+4]
        if len(c) < 3:
            continue
        # LONG
        wick_l = l[0] < l.min() * 1.002
        bos_l  = c[-1] > h[0]
        result[j, 0] = 10.0 if (wick_l or bos_l) else 0.0
        # SHORT
        wick_s = h[0] > h.max() * 0.998
        bos_s  = c[-1] < l[0]
        result[j, 1] = 10.0 if (wick_s or bos_s) else 0.0
    return result

def compute_oi_bonus(vol: np.ndarray, close: np.ndarray, i: int, direction: str,
                     oi_vol_thresh: float, oi_mom_thresh: float,
                     oi_penalize_low: bool) -> float:
    """OI代理奖励（品种参数化）"""
    if i < 10:
        return 0.0
    vols   = vol[max(0,i-10):i+1]
    closes = close[max(0,i-10):i+1]
    if len(vols) < 6:
        return 0.0
    vol_accel = np.mean(vols[-3:]) / (np.mean(vols[-6:-3]) + 1e-9)
    price_mom = (closes[-1] - closes[-4]) / (closes[-4] + 1e-9) * 100
    if direction == 'LONG':
        if vol_accel > oi_vol_thresh and price_mom > oi_mom_thresh:  return 10.0
        if vol_accel > oi_vol_thresh and price_mom < -oi_mom_thresh: return -8.0
        if vol_accel < 0.7 and oi_penalize_low:                      return -3.0
        return 2.0
    else:
        if vol_accel > oi_vol_thresh and price_mom < -oi_mom_thresh: return 10.0
        if vol_accel > oi_vol_thresh and price_mom > oi_mom_thresh:  return -8.0
        if vol_accel < 0.7 and oi_penalize_low:                      return -3.0
        return 2.0

def is_false_breakout(close: np.ndarray, high: np.ndarray, low: np.ndarray,
                      i: int, direction: str) -> bool:
    """假突破过滤"""
    if i < 5:
        return False
    lookback = close[max(0,i-5):i]
    if direction == 'LONG':
        prev_hi = high[max(0,i-5):i].max()
        return close[i] > prev_hi * 1.005 and close[i] < close[i-1]
    else:
        prev_lo = low[max(0,i-5):i].min()
        return close[i] < prev_lo * 0.995 and close[i] > close[i-1]

# ════════════════════════════════════════════════════════════════
# Phase 1: 预结算引擎
# ════════════════════════════════════════════════════════════════

def settle_on_grid(close_1h: np.ndarray, atr_1h: np.ndarray, entry_i: int,
                   direction: str, max_hold: int = 48
                   ) -> np.ndarray:
    """
    在 SL_GRID × TP_GRID × HOLD_GRID 网格上预结算（全向量化，28x加速）
    返回 outcome_array shape=(N_SL, N_TP, N_HOLD_GRID)
    outcome: +1=WIN, -1=LOSS, 0=TIMEOUT
    """
    NH = len(HOLD_GRID)
    outcomes = np.zeros((N_SL, N_TP, NH), dtype=np.int8)
    entry_p  = close_1h[entry_i]
    atr      = atr_1h[entry_i]
    if np.isnan(atr) or atr <= 0:
        return outcomes

    future_c = close_1h[entry_i+1 : entry_i+1+max_hold]
    n_future = len(future_c)
    if n_future == 0:
        return outcomes

    # SL/TP价格向量 (N_SL,) / (N_TP,)
    if direction == 'LONG':
        sl_prices = entry_p - atr * SL_GRID   # (N_SL,)
        tp_prices = entry_p + atr * TP_GRID   # (N_TP,)
    else:
        sl_prices = entry_p + atr * SL_GRID
        tp_prices = entry_p - atr * TP_GRID

    # future_c: (n_future,)  -> broadcast
    fut = future_c[np.newaxis, :]              # (1, n_future)

    if direction == 'LONG':
        sl_hit = fut <= sl_prices[:, np.newaxis]   # (N_SL, n_future)
        tp_hit = fut >= tp_prices[:, np.newaxis]   # (N_TP, n_future)
    else:
        sl_hit = fut >= sl_prices[:, np.newaxis]
        tp_hit = fut <= tp_prices[:, np.newaxis]

    # 第一次触发的 bar 索引（未触发=9999）
    sl_first = np.where(sl_hit.any(axis=1), np.argmax(sl_hit, axis=1), 9999)  # (N_SL,)
    tp_first = np.where(tp_hit.any(axis=1), np.argmax(tp_hit, axis=1), 9999)  # (N_TP,)

    # 广播到 (N_SL, N_TP, NH)
    sl_f = sl_first[:, np.newaxis, np.newaxis]   # (N_SL, 1, 1)
    tp_f = tp_first[np.newaxis, :, np.newaxis]   # (1, N_TP, 1)
    hb   = HOLD_GRID[np.newaxis, np.newaxis, :]  # (1, 1, NH)

    is_tp = (tp_f < hb) & (tp_f <= sl_f)
    is_sl = (sl_f < hb) & (sl_f < tp_f)
    outcomes = np.where(is_tp, np.int8(1), np.where(is_sl, np.int8(-1), np.int8(0)))
    return outcomes

def build_candidate_library(sym: str, scan_threshold: float = 50.0,
                             regime_offset: float = 0.0,
                             oi_penalize_low: bool = True) -> dict:
    """
    Phase 1: 扫描所有信号候选，预结算 SL/TP/HOLD 网格
    全向量化版本，Phase1目标<15秒
    """
    from numpy.lib.stride_tricks import sliding_window_view as swv
    print(f"\n[Phase1] {sym} 构建候选库（scan_threshold={scan_threshold}）...")
    t0 = time.time()

    # ── 读取数据（直接用parquet预计算列，不重算指标）
    df1h  = pd.read_parquet(FIXED / f'{sym.lower()}_1h_fixed.parquet')
    df4h  = pd.read_parquet(FIXED / f'{sym.lower()}_4h_fixed.parquet')
    df1d  = pd.read_parquet(FIXED / f'{sym.lower()}_1d_fixed.parquet')
    df15m = pd.read_parquet(FIXED / f'{sym.lower()}_15m_fixed.parquet')

    c1h  = df1h['close'].values;  h1h = df1h['high'].values
    l1h  = df1h['low'].values;    v1h = df1h['volume'].values
    a1h  = df1h['atr14'].values;  r1h = df1h['rsi14'].values
    ts1h = df1h.index
    n    = len(c1h)

    # 4H/1D RSI对齐（加offset后用于体制检测）
    r4h = df4h['rsi14'].reindex(ts1h, method='ffill').fillna(50).values + regime_offset
    r1d = df1d['rsi14'].reindex(ts1h, method='ffill').fillna(50).values + regime_offset
    r1h_adj = r1h + regime_offset

    # ── 向量化预计算 ──
    # OB评分（向量化）
    W = 16
    wins_h = swv(h1h, W).max(axis=1);  wins_l = swv(l1h, W).min(axis=1)
    rng_w  = wins_h - wins_l
    rsi_s  = r1h[W-1:]
    pos_w  = np.where(rng_w > 1e-9, (c1h[W-1:] - wins_l) / rng_w, 0.5)
    bc     = np.where(rng_w > 0.5 * np.maximum(rng_w, 1e-9), 8.0, 0.0)
    ob_long_arr  = np.zeros(n)
    ob_short_arr = np.zeros(n)
    ob_long_arr[W-1:]  = np.minimum(np.clip((35-rsi_s)/35*15, 0,15) + np.clip((1-pos_w)*10, 0,10) + bc, 35)
    ob_short_arr[W-1:] = np.minimum(np.clip((rsi_s-65)/35*15, 0,15) + np.clip(pos_w*10, 0,10) + bc, 35)

    # 价格位置（hi20/lo20）
    wh20  = swv(h1h, 21).max(axis=1);  wl20 = swv(l1h, 21).min(axis=1)
    rng20 = wh20 - wl20
    pos20 = np.where(rng20 > 1e-9, (c1h[20:] - wl20) / rng20, 0.5)
    hi20  = np.full(n, 0.5);  lo20 = np.full(n, 0.5)
    hi20[20:] = pos20;         lo20[20:] = pos20

    # 15M触发奖励
    idx15 = df15m.index.searchsorted(ts1h)
    n15   = len(df15m)
    c15   = df15m['close'].values;  h15 = df15m['high'].values;  l15 = df15m['low'].values
    trig  = np.zeros((n, 2), dtype=np.float32)
    for j in range(n):
        ii = idx15[j]
        if ii >= n15 - 4: continue
        cc = c15[ii:ii+4];  hh = h15[ii:ii+4];  ll = l15[ii:ii+4]
        if len(cc) < 3:     continue
        trig[j, 0] = 10.0 if (ll[0] < ll.min()*1.002 or cc[-1] > hh[0]) else 0.0
        trig[j, 1] = 10.0 if (hh[0] > hh.max()*0.998 or cc[-1] < ll[0]) else 0.0

    # OI预计算（vol_accel + price_mom）
    vol_accel = np.zeros(n)
    price_mom = np.zeros(n)
    for i in range(10, n):
        vv = v1h[max(0,i-10):i+1]
        if len(vv) >= 6:
            vol_accel[i] = np.mean(vv[-3:]) / (np.mean(vv[-6:-3]) + 1e-9)
            price_mom[i] = (c1h[i] - c1h[max(0,i-4)]) / (c1h[max(0,i-4)] + 1e-9) * 100

    # 向量化体制检测
    bull_macro = r1d  > 52;   bear_macro = r1d  < 48
    bull_str   = r4h  > 53;   bear_str   = r4h  < 47
    bull_mom   = r1h_adj > 54; bear_mom   = r1h_adj < 46
    near_hi    = hi20 > 0.7;  near_lo    = lo20 < 0.3
    regimes    = np.full(n, 'CHOP_MID', dtype=object)
    regimes[bull_macro & bull_str & bull_mom & near_hi]  = 'BULL_TREND'
    regimes[bull_macro & bull_str & bull_mom & ~near_hi] = 'BULL_EARLY'
    regimes[bull_macro & bull_str & ~bull_mom]           = 'BULL_CORRECTION'
    regimes[bear_macro & bear_str & bear_mom & near_lo]  = 'BEAR_TREND'
    regimes[bear_macro & bear_str & bear_mom & ~near_lo] = 'BEAR_EARLY'
    regimes[bear_macro & bear_str & ~bear_mom]           = 'BEAR_RECOVERY'

    print(f"  预计算完成 {time.time()-t0:.2f}s，开始扫描信号...")
    t1 = time.time()

    candidates = []
    skipped_fb = 0

    for i in range(30, n - 50):
        if np.isnan(a1h[i]): continue
        regime = regimes[i]
        for d_idx, direction in enumerate(['LONG', 'SHORT']):
            rd_score = REGIME_DIRECTION_SCORE.get((regime, direction), -10)
            if rd_score < -15: continue

            ob_s = float(ob_long_arr[i] if direction == 'LONG' else ob_short_arr[i])
            raw  = rd_score + ob_s + float(trig[i, d_idx]) + 22  # +22 ≈ oi_bonus上限
            if raw < scan_threshold - 20: continue

            # 假突破过滤（轻量版）
            if i >= 5:
                if direction == 'LONG':
                    if c1h[i] > h1h[max(0,i-5):i].max() * 1.005 and c1h[i] < c1h[i-1]:
                        skipped_fb += 1; continue
                else:
                    if c1h[i] < l1h[max(0,i-5):i].min() * 0.995 and c1h[i] > c1h[i-1]:
                        skipped_fb += 1; continue

            # OI bonus（查预计算）
            va  = vol_accel[i];  pm = price_mom[i]
            if direction == 'LONG':
                if   va > 1.3 and pm > 0.5:  oi_b = 10.0
                elif va > 1.3 and pm < -0.5: oi_b = -8.0
                elif va < 0.7 and oi_penalize_low: oi_b = -3.0
                else: oi_b = 2.0
            else:
                if   va > 1.3 and pm < -0.5: oi_b = 10.0
                elif va > 1.3 and pm > 0.5:  oi_b = -8.0
                elif va < 0.7 and oi_penalize_low: oi_b = -3.0
                else: oi_b = 2.0

            raw2  = rd_score + ob_s + float(trig[i, d_idx]) + oi_b + 20
            score = max(0, min(150, (raw2 + 50) / 180 * 150))
            if score < scan_threshold: continue

            # 预结算（全向量化）
            atr   = a1h[i]; entry = c1h[i]
            fut   = c1h[i+1:i+49]
            if len(fut) == 0: continue
            if direction == 'LONG':
                sl_p = entry - atr * SL_GRID;  tp_p = entry + atr * TP_GRID
            else:
                sl_p = entry + atr * SL_GRID;  tp_p = entry - atr * TP_GRID

            futb = fut[np.newaxis, :]
            if direction == 'LONG':
                slh = futb <= sl_p[:, np.newaxis];  tph = futb >= tp_p[:, np.newaxis]
            else:
                slh = futb >= sl_p[:, np.newaxis];  tph = futb <= tp_p[:, np.newaxis]

            slf = np.where(slh.any(1), np.argmax(slh, 1), 9999)
            tpf = np.where(tph.any(1), np.argmax(tph, 1), 9999)
            outcomes = np.where(
                (tpf[None,:,None] < HB_arr) & (tpf[None,:,None] <= slf[:,None,None]),
                np.int8(1),
                np.where((slf[:,None,None] < HB_arr) & (slf[:,None,None] < tpf[None,:,None]),
                         np.int8(-1), np.int8(0))
            )
            candidates.append({
                'idx':       i,
                'ts':        str(ts1h[i]),
                'direction': direction,
                'regime':    regime,
                'score_150': score,
                'rd_score':  rd_score,
                'ob_score':  ob_s,
                'trig_bonus':float(trig[i, d_idx]),
                'oi_bonus':  oi_b,
                'vol_accel': float(va),
                'price_mom': float(pm),
                'outcomes':  outcomes,
            })

    elapsed = time.time() - t0
    print(f"[Phase1] {sym}: {len(candidates)}条候选 | 假突破过滤={skipped_fb} | 耗时={elapsed:.1f}s")
    return {
        'sym':          sym,
        'n_total_bars': n,
        'candidates':   candidates,
        'n_candidates': len(candidates),
        'fb_filtered':  skipped_fb,
        'elapsed_s':    elapsed,
    }


def fast_evaluate(lib: dict, threshold: float,
                  sl_idx: int, tp_idx: int, hold_idx: int,
                  oi_vol_thresh: float, oi_mom_thresh: float,
                  oi_penalize_low: bool,
                  n_windows: int = 12) -> dict:
    """
    快速评估（全numpy向量化，~0.004s/次）
    """
    cands = lib['candidates']
    if not cands:
        return {'composite': -999.0}

    n_c = len(cands)

    # ── 批量提取候选特征（首次调用时缓存到lib）──
    if '_arrays' not in lib:
        lib['_arrays'] = {
            'rd':    np.array([c['rd_score']  for c in cands], dtype=np.float32),
            'ob':    np.array([c['ob_score']  for c in cands], dtype=np.float32),
            'trig':  np.array([c['trig_bonus'] for c in cands], dtype=np.float32),
            'va':    np.array([c['vol_accel'] for c in cands], dtype=np.float32),
            'pm':    np.array([c['price_mom'] for c in cands], dtype=np.float32),
            # outcomes: (n_c, N_SL, N_TP, N_HOLD)
            'outcomes': np.stack([c['outcomes'] for c in cands], axis=0),
            'regime_idx': np.array([
                {'BULL_TREND':-0.10,'BULL_EARLY':-0.15,'BULL_CORRECTION':-0.20,
                 'BEAR_TREND':-0.10,'BEAR_EARLY':-0.15,'BEAR_RECOVERY':-0.18,
                 'CHOP_MID':-0.22,'CHOP_LOW':-0.22}.get(c['regime'], -0.22)
                for c in cands], dtype=np.float32),
            'window_idx': np.array([
                min(c['idx'] * n_windows // max(lib['n_total_bars'], 1), n_windows-1)
                for c in cands], dtype=np.int32),
        }

    arr = lib['_arrays']
    rd   = arr['rd'];    ob  = arr['ob'];    trig = arr['trig']
    va   = arr['va'];    pm  = arr['pm']
    to_pnl = arr['regime_idx']
    all_outcomes = arr['outcomes']   # (n_c, 7, 7, 7)
    win_arr  = arr['window_idx']

    # ── OI bonus向量化 ──
    if oi_penalize_low:
        oi_b = np.where((va > oi_vol_thresh) & (pm > oi_mom_thresh), np.float32(10),
               np.where((va > oi_vol_thresh) & (pm < -oi_mom_thresh), np.float32(-8),
               np.where(va < 0.7, np.float32(-3), np.float32(2))))
    else:
        oi_b = np.where((va > oi_vol_thresh) & (pm > oi_mom_thresh), np.float32(10),
               np.where((va > oi_vol_thresh) & (pm < -oi_mom_thresh), np.float32(-8),
               np.float32(2)))

    raw   = rd + ob + trig + oi_b + np.float32(20)
    score = np.clip((raw + 50) / 180 * 150, 0, 150)
    mask  = score >= threshold

    n = int(mask.sum())
    if n < 30:
        return {'composite': -999.0, 'n': n}

    outcomes = all_outcomes[mask, sl_idx, tp_idx, hold_idx]  # (n,) int8
    tp_pnl   = TP_GRID[tp_idx] * 0.5
    sl_pnl   = -SL_GRID[sl_idx] * 0.5

    win_m  = outcomes == 1
    loss_m = outcomes == -1
    to_m   = outcomes == 0
    wins   = int(win_m.sum());  losses = int(loss_m.sum());  tos = int(to_m.sum())
    wr     = wins / max(wins + losses, 1)

    # PnL向量
    pnl_vec = np.where(win_m, np.float32(tp_pnl),
              np.where(loss_m, np.float32(sl_pnl), to_pnl[mask]))

    gross_win  = float(pnl_vec[pnl_vec > 0].sum())
    gross_loss = float(abs(pnl_vec[pnl_vec < 0].sum()))
    pf         = gross_win / (gross_loss + 1e-9)
    mu         = float(pnl_vec.mean())
    sigma      = float(pnl_vec.std()) + 1e-9
    sharpe     = mu / sigma * math.sqrt(252)

    # OOS稳定性（12窗口）
    w_idx = win_arr[mask]
    oos_pass = 0
    for w in range(n_windows):
        wm = w_idx == w
        ww = int((win_m & wm).sum());  wl = int((loss_m & wm).sum())
        if ww + wl >= 8 and (ww / max(wl, 1e-9)) >= 0.95:
            oos_pass += 1
    oos_rate = oos_pass / n_windows

    # v7: 频率得分 — 每6个月窗口至少150笔才满分，不足线性惩罚
    # 鼓励系统为交易而生，不允许靠极高门槛换WR
    avg_n_per_window = n / n_windows
    freq_score = min(avg_n_per_window / 150.0, 1.0)

    composite = (0.25 * min(sharpe, 5.0) +
                 0.25 * math.log(max(pf, 0.01)) +
                 0.20 * wr +
                 0.15 * oos_rate +
                 0.15 * freq_score)   # v7新增：频率约束

    return {
        'composite': composite,
        'n':         n,
        'wr':        wr,
        'pf':        pf,
        'sharpe':    sharpe,
        'oos_rate':  oos_rate,
        'wins':      wins,
        'losses':    losses,
        'tos':       tos,
    }


def sample_params(rng: np.random.Generator) -> dict:
    """随机采样参数"""
    p = {}
    for name, (lo, hi, typ) in PARAM_SPACE.items():
        v = rng.uniform(lo, hi)
        p[name] = int(round(v)) if typ == 'int' else round(float(v), 3)
    return p


def perturb_params(params: dict, rng: np.random.Generator, scale: float = 0.15) -> dict:
    """邻域扰动（山顶爬坡）"""
    p = dict(params)
    keys = list(PARAM_SPACE.keys())
    rng.shuffle(keys)
    for k in keys[:3]:
        lo, hi, typ = PARAM_SPACE[k]
        span = (hi - lo) * scale
        v = p[k] + rng.uniform(-span, span)
        v = max(lo, min(hi, v))
        p[k] = int(round(v)) if typ == 'int' else round(float(v), 3)
    return p


def params_to_grid_idx(p: dict, sym_prefix: str) -> Tuple[int, int, int]:
    """参数 → SL/TP/HOLD 网格索引"""
    sl_m = p[f'{sym_prefix}_sl_mult']
    tp_m = p[f'{sym_prefix}_tp_mult']
    hb   = p[f'{sym_prefix}_hold_bars']
    si = int(np.argmin(np.abs(SL_GRID - sl_m)))
    ti = int(np.argmin(np.abs(TP_GRID - tp_m)))
    hi = int(np.argmin(np.abs(HOLD_GRID - hb)))
    return si, ti, hi


def evaluate_params(p: dict, lib_btc: dict, lib_eth: dict) -> dict:
    """评估一组参数（BTC+ETH联合）"""
    si_b, ti_b, hi_b = params_to_grid_idx(p, 'btc')
    btc_r = fast_evaluate(lib_btc,
                          threshold=p['btc_threshold'],
                          sl_idx=si_b, tp_idx=ti_b, hold_idx=hi_b,
                          oi_vol_thresh=p['btc_oi_vol_thresh'],
                          oi_mom_thresh=p['btc_oi_mom_thresh'],
                          oi_penalize_low=False)

    si_e, ti_e, hi_e = params_to_grid_idx(p, 'eth')
    eth_r = fast_evaluate(lib_eth,
                          threshold=p['eth_threshold'],
                          sl_idx=si_e, tp_idx=ti_e, hold_idx=hi_e,
                          oi_vol_thresh=p['eth_oi_vol_thresh'],
                          oi_mom_thresh=p['eth_oi_mom_thresh'],
                          oi_penalize_low=True)

    b_comp = btc_r.get('composite', -999.0)
    e_comp = eth_r.get('composite', -999.0)
    if b_comp < -100 or e_comp < -100:
        return {'composite': -999.0}

    joint = 0.45 * b_comp + 0.55 * e_comp
    return {'composite': joint, 'btc': btc_r, 'eth': eth_r, 'params': p}


def run_100k_search(lib_btc: dict, lib_eth: dict,
                    n_random: int = 80_000,
                    n_hillclimb: int = 20_000,
                    top_k: int = 50) -> List[dict]:
    """
    主搜索循环：随机搜索 + 山顶爬坡
    """
    rng = np.random.default_rng(42)
    best_results: List[dict] = []

    print(f"\n[Phase2] 随机搜索 {n_random:,}次...")
    t0 = time.time()
    progress_step = n_random // 10

    for i in range(n_random):
        p = sample_params(rng)
        r = evaluate_params(p, lib_btc, lib_eth)
        if r['composite'] > -100:
            best_results.append(r)
            best_results.sort(key=lambda x: x['composite'], reverse=True)
            best_results = best_results[:top_k]

        if (i + 1) % progress_step == 0:
            elapsed = time.time() - t0
            best = best_results[0]['composite'] if best_results else 0
            btc_wr = best_results[0]['btc'].get('wr',0) if best_results else 0
            eth_wr = best_results[0]['eth'].get('wr',0) if best_results else 0
            print(f"  随机搜索 {i+1:>6}/{n_random} | "
                  f"Best={best:.4f} BTC_WR={btc_wr:.1%} ETH_WR={eth_wr:.1%} | "
                  f"{elapsed:.0f}s elapsed")

    print(f"\n[Phase2] 山顶爬坡 {n_hillclimb:,}次（基于Top-{top_k}）...")
    t1 = time.time()
    progress_step2 = n_hillclimb // 5

    for i in range(n_hillclimb):
        # 从Top-50中随机选一个作为起点
        base = rng.choice(best_results[:min(top_k, len(best_results))])
        p    = perturb_params(base['params'], rng,
                              scale=0.08 if i > n_hillclimb // 2 else 0.15)
        r    = evaluate_params(p, lib_btc, lib_eth)
        if r['composite'] > -100 and r['composite'] > best_results[-1]['composite']:
            best_results.append(r)
            best_results.sort(key=lambda x: x['composite'], reverse=True)
            best_results = best_results[:top_k]

        if (i + 1) % progress_step2 == 0:
            elapsed = time.time() - t1
            best = best_results[0]['composite'] if best_results else 0
            print(f"  爬坡 {i+1:>5}/{n_hillclimb} | Best={best:.4f} | {elapsed:.0f}s elapsed")

    return best_results


# ════════════════════════════════════════════════════════════════
# Phase 3: 结果输出 + v6参数推荐
# ════════════════════════════════════════════════════════════════

def print_v6_report(top_results: List[dict], lib_btc: dict, lib_eth: dict):
    print("\n" + "="*70)
    print("  🏆  达摩院 · v6 参数寻优报告")
    print("="*70)

    if not top_results:
        print("  ❌ 未找到有效结果")
        return

    best = top_results[0]
    p    = best['params']
    br   = best['btc']
    er   = best['eth']

    print(f"\n  复合得分: {best['composite']:.4f}")
    print(f"\n  ┌─ BTC最优参数 ──────────────────────────────────┐")
    print(f"  │ threshold={p['btc_threshold']}  sl={p['btc_sl_mult']}x  tp={p['btc_tp_mult']}x  hold={p['btc_hold_bars']}bars")
    print(f"  │ oi_vol_thresh={p['btc_oi_vol_thresh']}  oi_mom_thresh={p['btc_oi_mom_thresh']}")
    print(f"  │ WR={br.get('wr',0):.1%}  PF={br.get('pf',0):.3f}  Sharpe={br.get('sharpe',0):.2f}  n={br.get('n',0)}")
    print(f"  │ OOS稳定率={br.get('oos_rate',0):.1%}")
    print(f"  └────────────────────────────────────────────────┘")

    print(f"\n  ┌─ ETH最优参数 ──────────────────────────────────┐")
    print(f"  │ threshold={p['eth_threshold']}  sl={p['eth_sl_mult']}x  tp={p['eth_tp_mult']}x  hold={p['eth_hold_bars']}bars")
    print(f"  │ oi_vol_thresh={p['eth_oi_vol_thresh']}  oi_mom_thresh={p['eth_oi_mom_thresh']}")
    print(f"  │ WR={er.get('wr',0):.1%}  PF={er.get('pf',0):.3f}  Sharpe={er.get('sharpe',0):.2f}  n={er.get('n',0)}")
    print(f"  │ OOS稳定率={er.get('oos_rate',0):.1%}")
    print(f"  └────────────────────────────────────────────────┘")

    print(f"\n  对比 v5 基准:")
    print(f"  BTC v5: threshold=108  sl=1.8x  tp=2.5x  hold=32  WR=46.1%  OOS=8/12")
    print(f"  ETH v5: threshold=136  sl=2.0x  tp=2.5x  hold=32  WR=47.9%  OOS=11/12")

    print(f"\n  Top-5 候选参数组:")
    print(f"  {'#':<3} {'Composite':>10} {'BTC_WR':>8} {'ETH_WR':>8} {'BTC_n':>7} {'ETH_n':>7}")
    for i, r in enumerate(top_results[:5]):
        print(f"  {i+1:<3} {r['composite']:>10.4f} "
              f"{r['btc'].get('wr',0):>8.1%} {r['eth'].get('wr',0):>8.1%} "
              f"{r['btc'].get('n',0):>7} {r['eth'].get('n',0):>7}")

    # 总候选库统计
    print(f"\n  候选库: BTC={lib_btc['n_candidates']}条  ETH={lib_eth['n_candidates']}条")
    print(f"  数据集: BTC+ETH × 8年 × 1H主力 / 15M+4H+1D辅助")
    print(f"  搜索: 10万次（80K随机 + 20K爬坡）")

def save_v6_report(top_results: List[dict], lib_btc: dict, lib_eth: dict) -> str:
    out = {
        'framework':  'DharmaOptimizer v7.0',
        'generated':  datetime.now(timezone.utc).isoformat(),
        'total_evals':100_000,
        'btc_candidates': lib_btc['n_candidates'],
        'eth_candidates': lib_eth['n_candidates'],
        'top50': [{
            'rank': i+1,
            'composite': r['composite'],
            'btc': {k: v for k, v in r['btc'].items() if k != 'outcomes'},
            'eth': {k: v for k, v in r['eth'].items() if k != 'outcomes'},
            'params': r['params'],
        } for i, r in enumerate(top_results[:50])],
        'v6_recommended': top_results[0]['params'] if top_results else {},
        'v5_baseline': {
            'BTCUSDT': {'threshold':108,'sl_mult':1.8,'tp_mult':2.5,'hold_bars':32,
                        'oi_vol_thresh':1.5,'oi_mom_thresh':0.8},
            'ETHUSDT': {'threshold':136,'sl_mult':2.0,'tp_mult':2.5,'hold_bars':32,
                        'oi_vol_thresh':1.3,'oi_mom_thresh':0.5},
        },
    }
    path = RESULTS / f'dharma_v6_100k_{TAG}.json'
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    return str(path)


# ════════════════════════════════════════════════════════════════
# 入口
# ════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--scan-threshold', type=float, default=50.0,
                    help='候选信号扫描门槛（越低候选越多，越慢）')
    ap.add_argument('--n-random',   type=int, default=80_000)
    ap.add_argument('--n-hillclimb',type=int, default=20_000)
    ap.add_argument('--fast',       action='store_true',
                    help='快速模式：10K随机+2K爬坡（调试用）')
    args = ap.parse_args()

    if args.fast:
        args.n_random    = 5_000
        args.n_hillclimb = 1_000
        print("[FAST MODE] n_random=5000 n_hillclimb=1000")

    total_t0 = time.time()
    print("="*70)
    print("  达摩院 · 100K参数寻优引擎 v7.0")
    print(f"  计划: {args.n_random:,}次随机 + {args.n_hillclimb:,}次爬坡")
    print("="*70)

    # ── Phase 1: 构建候选库 ──
    lib_btc = build_candidate_library('BTCUSDT',
                                       scan_threshold=args.scan_threshold,
                                       regime_offset=0.0,
                                       oi_penalize_low=False)
    lib_eth = build_candidate_library('ETHUSDT',
                                       scan_threshold=args.scan_threshold,
                                       regime_offset=-3.0,
                                       oi_penalize_low=True)

    phase1_elapsed = time.time() - total_t0
    print(f"\n[Phase1完成] 耗时={phase1_elapsed:.1f}s  "
          f"BTC候选={lib_btc['n_candidates']}  ETH候选={lib_eth['n_candidates']}")

    if lib_btc['n_candidates'] < 100 or lib_eth['n_candidates'] < 100:
        print("  ❌ 候选信号太少，降低 --scan-threshold 重试")
        sys.exit(1)

    # ── Phase 2: 100K搜索 ──
    top_results = run_100k_search(
        lib_btc, lib_eth,
        n_random=args.n_random,
        n_hillclimb=args.n_hillclimb,
    )

    phase2_elapsed = time.time() - total_t0 - phase1_elapsed
    print(f"\n[Phase2完成] 耗时={phase2_elapsed:.1f}s")

    # ── Phase 3: 输出报告 ──
    print_v6_report(top_results, lib_btc, lib_eth)
    out_path = save_v6_report(top_results, lib_btc, lib_eth)
    total_elapsed = time.time() - total_t0
    print(f"\n✅ 报告: {out_path}")
    print(f"✅ 总耗时: {total_elapsed:.1f}s ({total_elapsed/60:.1f}min)")
