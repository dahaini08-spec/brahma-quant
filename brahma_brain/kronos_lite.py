"""
brahma_brain/kronos_lite.py — Kronos-Lite 统计代理引擎 v2.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
设计院 × 达摩院 · v1.0: 2026-06-17 / v2.0: 2026-06-25

v2.0 改进（六方联合审核落地）：
  ① 体制自适应动态权重：BEAR_TREND/BULL_TREND/CHOP各自专属权重矩阵
     铁证依据：BEAR_TREND下4H回测准确率35.9%（反向），
               根因=EMA滞后，修复：EMA权重0.25→0.10，K线权重0.15→0.28
  ② 周期自适应参数集：tf_hint='15m'/'1h'/'4h'，自动切换P1~P4参数
     4H专项：P1动量窗口5根，P2 EMA5/EMA10，P3 RSI(7)，P4形态10根K
     修复反向问题目标：35.9% → ≥50%
  ③ P6新增：高低点结构BOS检测（近期突破新低/高额外加权）
  ④ BTC领先信号接口：btc_p_up参数，r=0.893相关性修正
  ⑤ 历史新高/低检测：价格在N日高低点附近时自动调整

苏摩约束：
  - 纯numpy实现，零外部依赖，失败概率≈0
  - 缓存TTL = 900s（与Kronos完整版一致）
  - CHOP体制系数 0.3×（与kronos_engine.py一致）
  - 当torch可用时，自动升级为完整Kronos推理
  - 所有参数变更需n≥100验证后才正式升权
"""


from brahma_brain.math_utils import _ema, _rsi, calc_rsi, rsi, ema  # 统一数学库

import sys
import os
import time
import math
import logging
from typing import Tuple, Dict, Any, Optional

import numpy as np

logger = logging.getLogger("kronos_lite")

# ── 体制系数（与 kronos_engine.py 保持一致）─────────────────────
REGIME_COEFF = {
    "CHOP_MID":          0.3,
    "CHOP_HIGH":         0.3,
    "CHOP_LOW":          0.3,
    "BULL_EARLY":        1.0,
    "BEAR_EARLY":        1.0,
    "BULL_TREND":        0.7,
    "BEAR_TREND":        0.7,
    "BULL_CORRECTION":   0.0,
    "BEAR_RECOVERY":     0.0,
}

# ── v2.0 体制自适应权重矩阵 ─────────────────────────────────────
# 铁证依据（2026-06-25六方辩论）：
#   BEAR_TREND：EMA严重滞后（4H EMA20=3.3天延迟），K线形态是领先指标
#   BULL_TREND：EMA方向正确，动量延续性强
#   CHOP：EMA均值回归有效，K线形态噪音大
#
# 格式：{p1_momentum, p2_ema, p3_rsi, p4_candle, p5_volume, p6_bos}
# 注：p6_bos为新增，其他权重之和仍=1.0，p6在集成后叠加
REGIME_WEIGHTS = {
    "BEAR_TREND": {
        "p1": 0.38,  # 动量延续优先 (+0.08)
        "p2": 0.10,  # EMA严重滞后，大幅降权 (-0.15)
        "p3": 0.22,  # RSI方向性稳定 (+0.02)
        "p4": 0.28,  # K线领先信号，加权 (+0.13)
        "p5": 0.02,  # 趋势中量能噪音大 (-0.08)
    },
    "BEAR_EARLY": {
        "p1": 0.35,
        "p2": 0.15,
        "p3": 0.22,
        "p4": 0.23,
        "p5": 0.05,
    },
    "BULL_TREND": {
        "p1": 0.38,
        "p2": 0.10,  # 同理滞后降权
        "p3": 0.22,
        "p4": 0.28,
        "p5": 0.02,
    },
    "BULL_EARLY": {
        "p1": 0.35,
        "p2": 0.15,
        "p3": 0.22,
        "p4": 0.23,
        "p5": 0.05,
    },
    "CHOP_MID": {
        "p1": 0.20,  # 震荡中动量无效
        "p2": 0.35,  # EMA均值回归在CHOP有效
        "p3": 0.25,  # RSI超买超卖有效
        "p4": 0.08,  # K线噪音大
        "p5": 0.12,  # 量能在震荡中是破局信号
    },
    "CHOP_HIGH": {
        "p1": 0.18,
        "p2": 0.38,
        "p3": 0.25,
        "p4": 0.07,
        "p5": 0.12,
    },
    "BULL_CORRECTION": {
        "p1": 0.25,
        "p2": 0.30,
        "p3": 0.25,
        "p4": 0.12,
        "p5": 0.08,
    },
    "BEAR_RECOVERY": {
        "p1": 0.25,
        "p2": 0.30,
        "p3": 0.25,
        "p4": 0.12,
        "p5": 0.08,
    },
    # 默认（未知体制）
    "_default": {
        "p1": 0.30,
        "p2": 0.25,
        "p3": 0.20,
        "p4": 0.15,
        "p5": 0.10,
    },
}

# ── v2.0 周期自适应参数集 ─────────────────────────────────────────
# 铁证依据：4H EMA20滞后=80小时，需要更短周期EMA才能响应趋势变化
TF_PARAMS = {
    "15m": {
        "momentum_window":  20,   # 近20根vs前80根
        "ema_fast":         20,   # EMA20
        "ema_slow":         50,   # EMA50
        "rsi_period":       14,   # RSI14
        "candle_window":     5,   # 近5根K
        "bos_lookback":     50,   # BOS检测回溯
    },
    "1h": {
        "momentum_window":  12,   # 近12根（12H）
        "ema_fast":         12,   # EMA12（12H）
        "ema_slow":         26,   # EMA26（26H）
        "rsi_period":       10,   # RSI10（更灵敏）
        "candle_window":     7,   # 近7根K
        "bos_lookback":     30,
    },
    "4h": {
        "momentum_window":   5,   # 近5根（20H，更敏感）← 核心修复
        "ema_fast":          5,   # EMA5（20H）← 核心修复：替代滞后EMA20
        "ema_slow":         10,   # EMA10（40H）← 核心修复：替代滞后EMA50
        "rsi_period":        7,   # RSI7（更敏感）← 核心修复
        "candle_window":    10,   # 近10根K（更大样本）← 核心修复
        "bos_lookback":     20,
    },
    "1d": {
        "momentum_window":   5,
        "ema_fast":          5,
        "ema_slow":         10,
        "rsi_period":        7,
        "candle_window":    10,
        "bos_lookback":     14,
    },
}

_CACHE: Dict[str, Tuple[float, float, float]] = {}
_CACHE_TTL = 900  # 15分钟


def _ema(arr: np.ndarray, period: int) -> np.ndarray:
    out = np.empty_like(arr, dtype=float)
    out[:] = np.nan
    k = 2.0 / (period + 1)
    start = next((i for i, v in enumerate(arr) if not np.isnan(v)), None)
    if start is None:
        return out
    out[start] = arr[start]
    for i in range(start + 1, len(arr)):
        out[i] = arr[i] * k + out[i - 1] * (1 - k)
    return out


def _rsi(close: np.ndarray, period: int = 14) -> np.ndarray:
    delta = np.diff(close, prepend=close[0])
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_g = np.full_like(close, np.nan)
    avg_l = np.full_like(close, np.nan)
    if len(close) < period + 1:
        return avg_g
    avg_g[period] = gain[1:period + 1].mean()
    avg_l[period] = loss[1:period + 1].mean()
    for i in range(period + 1, len(close)):
        avg_g[i] = (avg_g[i - 1] * (period - 1) + gain[i]) / period
        avg_l[i] = (avg_l[i - 1] * (period - 1) + loss[i]) / period
    rs = np.where(avg_l == 0, 100.0, avg_g / avg_l)
    return 100 - (100 / (1 + rs))


def _compute_p_up(
    klines: list,
    regime: str = "",
    tf_hint: str = "15m",
    btc_p_up: Optional[float] = None,
) -> Tuple[float, Dict[str, float]]:
    """
    v2.0: 体制×周期双重自适应 p_up 计算

    Args:
        klines:    OHLCV列表
        regime:    当前体制，用于自适应权重选择
        tf_hint:   时间周期提示 '15m'/'1h'/'4h'/'1d'，用于参数集选择
        btc_p_up:  BTC的p_up值（0~1），用于相关性修正（可选）

    Returns:
        (p_up, debug_dict)
        p_up: 0.0 ~ 1.0
    """
    if len(klines) < 60:
        return 0.5, {"error": "insufficient_data"}

    arr = np.array(klines[-200:], dtype=float)
    close = arr[:, 3]
    high  = arr[:, 1]
    low   = arr[:, 2]
    vol   = arr[:, 4]
    open_ = arr[:, 0]

    # 选择周期参数集
    tf = tf_hint if tf_hint in TF_PARAMS else "15m"
    params = TF_PARAMS[tf]
    mom_win   = params["momentum_window"]
    ema_fast  = params["ema_fast"]
    ema_slow  = params["ema_slow"]
    rsi_per   = params["rsi_period"]
    cnd_win   = params["candle_window"]
    bos_lb    = params["bos_lookback"]

    # 选择体制权重
    w = REGIME_WEIGHTS.get(regime, REGIME_WEIGHTS["_default"])

    debug = {"tf": tf, "regime": regime}
    raw_signals = {}

    # ── P1: 动量概率（周期自适应窗口）─────────────────────────────
    try:
        returns_all  = np.diff(close) / (close[:-1] + 1e-10)
        hist_end     = -mom_win
        hist_start   = -(mom_win * 5)
        returns_rec  = returns_all[-mom_win:]
        returns_hist = returns_all[hist_start:hist_end]
        if len(returns_hist) >= 10:
            p_mom = float(np.searchsorted(np.sort(returns_hist),
                                          returns_rec.mean()) / len(returns_hist))
            raw_signals["p1"] = p_mom
            debug["p_momentum"] = round(p_mom, 3)
    except Exception:
        pass

    # ── P2: EMA结构（周期自适应 EMA参数）─────────────────────────
    try:
        ema_f = _ema(close, ema_fast)
        ema_s = _ema(close, ema_slow)
        if not np.isnan(ema_f[-1]) and not np.isnan(ema_s[-1]):
            cur = close[-1]
            above_fast = float(cur > ema_f[-1])
            above_slow = float(cur > ema_s[-1])
            # 斜率：用5根或可用长度
            slope_n = min(5, len(ema_f) - 1)
            ema_slope = (ema_f[-1] - ema_f[-slope_n - 1]) / (abs(ema_f[-slope_n - 1]) + 1e-10)
            slope_bull = float(ema_slope > 0)
            p_ema = (above_fast * 0.4 + above_slow * 0.4 + slope_bull * 0.2)
            raw_signals["p2"] = p_ema
            debug["p_ema"] = round(p_ema, 3)
            debug["ema_fast_val"] = round(float(ema_f[-1]), 2)
            debug["ema_slow_val"] = round(float(ema_s[-1]), 2)
            debug["ema_slope_pct"] = round(ema_slope * 100, 4)
    except Exception:
        pass

    # ── P3: RSI偏离（周期自适应 RSI周期）─────────────────────────
    try:
        rsi_arr = _rsi(close, rsi_per)
        rsi_cur = rsi_arr[-1]
        if not np.isnan(rsi_cur):
            p_rsi = max(0.1, min(0.9, (rsi_cur - 20) / 60))
            raw_signals["p3"] = p_rsi
            debug["rsi_cur"] = round(float(rsi_cur), 1)
            debug["p_rsi"] = round(p_rsi, 3)
    except Exception:
        pass

    # ── P4: K线形态（周期自适应数量）─────────────────────────────
    try:
        n = min(cnd_win, len(close))
        recent_close = close[-n:]
        recent_open  = open_[-n:]
        bullish_count = int(np.sum(recent_close > recent_open))
        p_candle = bullish_count / n
        raw_signals["p4"] = p_candle
        debug["p_candle"] = round(p_candle, 3)
        debug["bullish_k"] = f"{bullish_count}/{n}"
    except Exception:
        pass

    # ── P5: 成交量确认（价涨量增 vs 价跌量增）───────────────────
    try:
        if len(close) >= 10 and len(vol) >= 10:
            price_chg = (close[-1] - close[-5]) / (close[-5] + 1e-10)
            vol_ratio  = vol[-3:].mean() / (vol[-8:-3].mean() + 1e-10)
            if price_chg > 0 and vol_ratio > 1.1:
                p_vol = 0.65
            elif price_chg < 0 and vol_ratio > 1.1:
                p_vol = 0.35
            else:
                p_vol = 0.5
            raw_signals["p5"] = p_vol
            debug["p_volume"] = round(p_vol, 3)
            debug["vol_ratio"] = round(float(vol_ratio), 2)
    except Exception:
        pass

    # ── P6: 高低点结构 BOS检测（新增）──────────────────────────
    try:
        lb = min(bos_lb, len(close) - 1)
        recent_high = float(high[-lb:].max())
        recent_low  = float(low[-lb:].min())
        cur = float(close[-1])
        # 跌破近期低点 → p6偏空（0.2）；突破近期高点 → p6偏多（0.8）
        near_low_pct  = (cur - recent_low)  / (recent_low  + 1e-10)
        near_high_pct = (recent_high - cur) / (recent_high + 1e-10)
        if near_low_pct < 0.003:   # 在近期低点0.3%以内（新低区域）
            p_bos = 0.20
        elif near_high_pct < 0.003:  # 在近期高点0.3%以内（新高区域）
            p_bos = 0.80
        elif near_low_pct < 0.015:   # 靠近低点1.5%以内
            p_bos = 0.30
        elif near_high_pct < 0.015:  # 靠近高点1.5%以内
            p_bos = 0.70
        else:
            p_bos = 0.50
        raw_signals["p6"] = p_bos
        debug["p_bos"] = round(p_bos, 3)
        debug["near_low_pct"] = round(near_low_pct * 100, 2)
        debug["recent_low"]   = round(recent_low, 2)
        debug["recent_high"]  = round(recent_high, 2)
    except Exception:
        pass

    if not raw_signals:
        return 0.5, {"error": "no_signals"}

    # ── 加权集成（体制自适应权重，P6独立叠加）──────────────────
    weighted_sum = 0.0
    weight_total = 0.0
    for key in ("p1", "p2", "p3", "p4", "p5"):
        if key in raw_signals:
            wt = w.get(key, 0.0)
            weighted_sum += raw_signals[key] * wt
            weight_total += wt
    if weight_total <= 0:
        return 0.5, {"error": "zero_weight"}
    p_base = weighted_sum / weight_total

    # P6 BOS调整（独立叠加，权重0.15，不影响其他信号比例）
    if "p6" in raw_signals:
        p6_w = 0.15
        p_combined = p_base * (1 - p6_w) + raw_signals["p6"] * p6_w
    else:
        p_combined = p_base

    # 拉伸到更有区分度的范围：[0.3,0.7] → [0.1,0.9]
    p_up = 0.5 + (p_combined - 0.5) * 2.0
    p_up = max(0.05, min(0.95, p_up))

    # ── BTC领先信号修正（可选，r=0.893）──────────────────────────
    # 当BTC方向与ETH预测方向背离时，BTC以0.3权重修正ETH预测
    if btc_p_up is not None:
        BTC_CORR_W = 0.25  # BTC领先权重（保守，待n≥100验证后可升至0.35）
        p_up_btc_adj = p_up * (1 - BTC_CORR_W) + btc_p_up * BTC_CORR_W
        debug["btc_p_up"]      = round(btc_p_up, 3)
        debug["p_before_btc"]  = round(p_up, 3)
        p_up = max(0.05, min(0.95, p_up_btc_adj))

    debug["p_base"]    = round(p_base, 3)
    debug["p_up_final"] = round(p_up, 3)
    debug["signal_count"] = len(raw_signals)
    debug["weights_used"] = {k: round(w.get(k, 0), 2) for k in ("p1","p2","p3","p4","p5")}

    return p_up, debug


def _p_up_to_score(p_up: float, direction: str) -> int:
    """方向概率 → 原始分数（与kronos_engine.py保持一致）"""
    if direction == "LONG":
        p = p_up
    else:
        p = 1.0 - p_up

    if p > 0.70:   return +12
    elif p > 0.60: return +8
    elif p > 0.55: return +4
    elif p > 0.45: return 0
    elif p > 0.35: return -8
    else:           return -12


def get_s23_score(
    symbol: str,
    direction: str,
    klines_15m: list,
    regime: str = "",
    tf_hint: str = "15m",
    btc_p_up: Optional[float] = None,
) -> Tuple[int, Dict[str, Any]]:
    """
    Kronos-Lite v2.0 主接口（兼容 kronos_engine.py 的 get_kronos_score）

    v2.0新增参数：
        tf_hint:   时间周期 '15m'/'1h'/'4h'，用于自适应参数选择
        btc_p_up:  BTC的p_up（可选），用于相关性领先修正

    Returns:
        (score, meta)
        score: -12 ~ +12（含体制系数）
        meta:  {p_up, direction_conflict, reason, ...}
    """
    null_meta = {"p_up": 0.5, "direction_conflict": False, "reason": "skip"}

    if len(klines_15m) < 60:
        return 0, {**null_meta, "reason": "insufficient_data"}

    now = time.time()

    # 缓存key包含tf_hint，不同周期独立缓存
    cache_key = f"{symbol}_{tf_hint}"
    if cache_key in _CACHE:
        ts, p_up, _ = _CACHE[cache_key]
        if now - ts < _CACHE_TTL:
            raw_score = _p_up_to_score(p_up, direction)
            coeff = REGIME_COEFF.get(regime, 1.0)
            score = max(-12, min(12, int(raw_score * coeff)))
            meta = {
                "p_up": round(p_up, 3),
                "direction_conflict": (direction == "LONG" and p_up < 0.4) or
                                      (direction == "SHORT" and p_up > 0.6),
                "reason": f"cache|p_up={p_up:.2f}|tf={tf_hint}|coeff={coeff:.1f}",
                "source": "lite_cache",
            }
            return score, meta

    # [设计院 Phase3-1 2026-07-06] 升级: 优先使用本地WF-LightGBM模型
    try:
        import sys as _sys_ke, os as _os_ke
        _brain_dir = _os_ke.path.dirname(_os_ke.path.abspath(__file__))
        if _brain_dir not in _sys_ke.path:
            _sys_ke.path.insert(0, _brain_dir)
        from kronos_engine import _load_model as _ke_load, _predictor as _ke_pred, _model_loaded as _ke_ok
        if not _ke_ok:
            _ke_load()  # 尝试加载
        # 重新获取最新状态
        import brahma_brain.kronos_engine as _ke_mod
        if _ke_mod._model_loaded and _ke_mod._predictor is not None:
            _pred_lgbm = _ke_mod._predictor
            # 用klines计算特征
            _closes = [float(k[4]) for k in klines_15m]
            _vols   = [float(k[5]) for k in klines_15m] if len(klines_15m[0]) > 5 else [1.0]*len(klines_15m)
            _highs  = [float(k[2]) for k in klines_15m]
            _lows   = [float(k[3]) for k in klines_15m]
            _price  = _closes[-1]
            _gains  = [max(0, _closes[i]-_closes[i-1]) for i in range(1, len(_closes))]
            _losses = [max(0, _closes[i-1]-_closes[i]) for i in range(1, len(_closes))]
            _ag = sum(_gains[-14:])/14; _al = sum(_losses[-14:])/14
            _rsi_v = (100-100/(1+_ag/_al))/100 if _al > 0 else 0.5
            _ema14 = _closes[0]
            for _c in _closes[1:]: _ema14 = _c*(2/15)+_ema14*(1-2/15)
            _vol_avg = sum(_vols[-10:])/10
            _h48 = max(_highs[-48:]) if len(_highs)>=48 else max(_highs)
            _l48 = min(_lows[-48:])  if len(_lows)>=48  else min(_lows)
            _feat = {
                'p_momentum': min(1.0, max(0.0, (_price - _closes[-5]) / (_closes[-5]+1e-9) / 0.05 + 0.5)),
                'p_ema':      float(_price > _ema14),
                'p_rsi':      _rsi_v,
                'p_candle':   1.0 if _closes[-1] > _closes[-2] else 0.0,
                'p_volume':   min(1.0, _vols[-1] / (_vol_avg+1e-9) / 2),
                'p_bos':      float((_price - _l48) / (_h48 - _l48 + 1e-9)),
                'regime':     {'BULL_TREND':0.9,'BEAR_TREND':0.1,'CHOP_MID':0.5,
                               'BULL_EARLY':0.75,'BEAR_RECOVERY':0.35}.get(regime, 0.5),
                'direction':  1.0 if direction == 'LONG' else 0.0,
                'lsr':        0.5,
                'fr':         0.5,
            }
            if btc_p_up is not None:
                _feat['p_bos'] = (_feat['p_bos'] + float(btc_p_up)) / 2  # BTC领先信号融入
            _p_up_lgbm = float(_pred_lgbm.predict(_feat))
            _CACHE[cache_key] = (now, _p_up_lgbm, 0.0)
            _raw_lgbm   = _p_up_to_score(_p_up_lgbm, direction)
            _coeff_lgbm = REGIME_COEFF.get(regime, 1.0)
            _score_lgbm = max(-12, min(15, int(_raw_lgbm * _coeff_lgbm)))
            _meta_lgbm  = {
                'p_up': _p_up_lgbm,
                'direction_conflict': (direction=='LONG' and _p_up_lgbm < 0.4) or
                                      (direction=='SHORT' and _p_up_lgbm > 0.6),
                'reason': f'lgbm_wf p_up={_p_up_lgbm:.3f} score={_score_lgbm}',
                'source': 'kronos_lgbm_wf',
            }
            print(f'[s23-Kronos] {symbol} {direction} p_up={_p_up_lgbm:.3f} score={_score_lgbm} src=kronos_lgbm_wf')
            return _score_lgbm, _meta_lgbm
    except Exception as _ke_e:
        pass  # lgbm失败就继续用lite

    # 尝试升级到完整 Kronos（仅当torch可用时才有效）
    try:
        _kronos_path = os.path.join(os.path.dirname(__file__), '..', 'external', 'Kronos')
        if os.path.exists(_kronos_path) and _kronos_path not in sys.path:
            sys.path.insert(0, _kronos_path)
        from kronos_engine import get_kronos_score as _full_score, _is_available as _kronos_ok
        if _kronos_ok():
            score, reason = _full_score(symbol, direction, klines_15m, regime)
            p_up_str = [x for x in reason.split(",") if x.startswith("p_up=")]
            p_up = float(p_up_str[0].split("=")[1]) if p_up_str else 0.5
            meta = {
                "p_up": p_up,
                "direction_conflict": (direction == "LONG" and p_up < 0.4) or
                                      (direction == "SHORT" and p_up > 0.6),
                "reason": reason,
                "source": "kronos_full",
            }
            _CACHE[cache_key] = (now, p_up, 0.0)
            return score, meta
    except Exception:
        pass

    # Kronos-Lite v2.0 计算
    try:
        p_up, debug = _compute_p_up(
            klines_15m,
            regime=regime,
            tf_hint=tf_hint,
            btc_p_up=btc_p_up,
        )
        _CACHE[cache_key] = (now, p_up, 0.0)

        raw_score = _p_up_to_score(p_up, direction)
        coeff = REGIME_COEFF.get(regime, 1.0)
        score = max(-12, min(12, int(raw_score * coeff)))

        direction_conflict = (direction == "LONG" and p_up < 0.40) or \
                             (direction == "SHORT" and p_up > 0.60)

        meta = {
            "p_up": round(p_up, 3),
            "direction_conflict": direction_conflict,
            "reason": f"lite_v2|p_up={p_up:.2f}|tf={tf_hint}|raw={raw_score:+d}|coeff={coeff:.1f}",
            "source": "kronos_lite_v2",
            "debug": debug,
        }
        return score, meta

    except Exception as e:
        logger.warning(f"[KronosLite-v2] {symbol} 计算异常: {e}")
        return 0, {**null_meta, "reason": f"lite_error:{str(e)[:40]}"}


# ── 测试入口 ──────────────────────────────────────────────────────
if __name__ == "__main__":
    import urllib.request, json

    print("=== Kronos-Lite v2.0 单元测试 ===\n")

    def fetch_klines(symbol, interval, limit=200):
        url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}"
        raw = json.loads(urllib.request.urlopen(url, timeout=10).read())
        return [[float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5])] for c in raw]

    print("获取 ETH/BTC K线...")
    eth_15m = fetch_klines("ETHUSDT", "15m", 200)
    eth_1h  = fetch_klines("ETHUSDT", "1h",  200)
    eth_4h  = fetch_klines("ETHUSDT", "4h",  100)
    btc_15m = fetch_klines("BTCUSDT", "15m", 200)
    print(f"ETH 15m={len(eth_15m)} 1h={len(eth_1h)} 4h={len(eth_4h)}\n")

    # BTC p_up（用于领先修正）
    btc_p, btc_dbg = _compute_p_up(btc_15m, regime="BEAR_TREND", tf_hint="15m")
    print(f"BTC p_up (15M) = {btc_p:.4f}\n")

    print("── ETH 多周期测试（BEAR_TREND体制）──")
    for tf, klines in [("15m", eth_15m), ("1h", eth_1h), ("4h", eth_4h)]:
        for direction in ["SHORT", "LONG"]:
            score, meta = get_s23_score(
                "ETHUSDT", direction, klines,
                regime="BEAR_TREND", tf_hint=tf,
                btc_p_up=btc_p if direction == "SHORT" else None
            )
            dc = "⚡冲突" if meta.get("direction_conflict") else ""
            print(f"  {tf} {direction:<6} score={score:+3d}  p_up={meta['p_up']:.3f}  {dc}")
            print(f"         → {meta['reason']}")
            if "debug" in meta:
                dbg = meta["debug"]
                print(f"         weights={dbg.get('weights_used',{})} bos={dbg.get('p_bos','?')}")
        print()

    print("── 回测验证（ETH 4H，n=50）──")
    hits_v1 = 0; hits_v2 = 0; total = 0
    from kronos_lite import _compute_p_up as _cp  # 用自身
    eth_4h_full = fetch_klines("ETHUSDT", "4h", 150)
    arr4h = np.array(eth_4h_full, dtype=float)
    c4h = arr4h[:, 3]
    for i in range(40, min(len(c4h)-1, 90)):
        window = eth_4h_full[:i]
        # v1（固定权重）
        p_v1, _ = _cp(window, regime="", tf_hint="15m")
        # v2（体制自适应+4H参数）
        p_v2, _ = _cp(window, regime="BEAR_TREND", tf_hint="4h")
        actual_up = c4h[i] > c4h[i-1]
        if (p_v1 > 0.5) == actual_up: hits_v1 += 1
        if (p_v2 > 0.5) == actual_up: hits_v2 += 1
        total += 1
    if total > 0:
        print(f"  v1（固定权重）: {hits_v1/total*100:.1f}%  ({total}样本)")
        print(f"  v2（体制自适应+4H参数）: {hits_v2/total*100:.1f}%  ({total}样本)")

    print("\n=== v2.0 测试完成 ===")
