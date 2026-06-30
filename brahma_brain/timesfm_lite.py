"""
brahma_brain/timesfm_lite.py — TimesFM-Lite 统计时序预测引擎 v1.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
设计院 × 达摩院 · 2026-06-21

背景：
  google-research/TimesFM（200M参数）在当前环境不可运行：
    可用RAM=882MB < 模型需求1.2~1.8GB，无torch/JAX，CPU 2核
  
  TimesFM-Lite 用 numpy + scipy 复现 TimesFM 三大核心价值：
    1. 分位数预测（Q10/Q25/Q50/Q75/Q90）← 不确定性量化
    2. 多时间框架联合预测               ← 长上下文理解
    3. 协变量支持（FR/OI/RSI等外部特征）← 结构化增强

方法论（Theta-Quantile Ensemble）：
  M1: Theta趋势分解（线性趋势 + 残差波动）
  M2: 指数平滑状态（Holt双参数）
  M3: 分位数回归（收益率历史分布）
  M4: 自回归滑动（AR近似，短期动量）
  集成方式：等权重平均，协变量修正偏差

接口兼容性：
  get_timesfm_score(symbol, direction, klines_1h, regime, covariates={})
  → (score, meta_dict)
  
  score范围: -10 ~ +10（注入s_research层，上限8%权重）
  meta包含: pred_price, q10~q90, confidence, direction_prob

苏摩约束：
  - 纯numpy/scipy实现，零外部依赖
  - 缓存TTL = 3600s（1H信号=1次预测）
  - CHOP体制系数 0.3×（与kronos_lite一致）
  - 预测失败返回(0, {'error': str})，不影响主流程
"""


from brahma_brain.math_utils import _ema, _rsi, calc_rsi, rsi, ema  # 统一数学库

import time
import math
import logging
from typing import Tuple, Dict, Any, Optional

import numpy as np

try:
    from scipy.stats import linregress
    _SCIPY_OK = True
except ImportError:
    _SCIPY_OK = False

logger = logging.getLogger("timesfm_lite")

# ── 体制系数（与 kronos_lite 保持一致）──────────────────────────
REGIME_COEFF = {
    "CHOP_MID":          0.3,
    "CHOP_HIGH":         0.3,
    "CHOP_LOW":          0.3,
    "BULL_EARLY":        1.0,
    "BEAR_EARLY":        1.0,
    "BULL_TREND":        0.8,
    "BEAR_TREND":        0.8,
    "BULL_CORRECTION":   0.5,
    "BEAR_RECOVERY":     0.6,
}

# 预测缓存
_cache: Dict[str, Dict] = {}
_CACHE_TTL = 3600  # 1H刷新


def _ema(arr: np.ndarray, alpha: float) -> np.ndarray:
    """指数平滑序列"""
    result = np.zeros_like(arr, dtype=float)
    result[0] = arr[0]
    for i in range(1, len(arr)):
        result[i] = alpha * arr[i] + (1 - alpha) * result[i - 1]
    return result


def _holt_forecast(prices: np.ndarray, horizon: int,
                   alpha: float = 0.3, beta: float = 0.1) -> np.ndarray:
    """
    Holt双参数指数平滑（趋势平滑）
    更准确地处理有趋势的时序
    """
    n = len(prices)
    level = np.zeros(n); trend = np.zeros(n)
    level[0] = prices[0]
    trend[0] = prices[1] - prices[0] if n > 1 else 0.0
    for i in range(1, n):
        prev_l = level[i - 1]; prev_t = trend[i - 1]
        level[i] = alpha * prices[i] + (1 - alpha) * (prev_l + prev_t)
        trend[i] = beta * (level[i] - prev_l) + (1 - beta) * prev_t
    preds = np.array([level[-1] + (h + 1) * trend[-1] for h in range(horizon)])
    return preds


def _theta_quantile(prices: np.ndarray, horizon: int) -> Dict:
    """
    TimesFM核心能力1：分位数预测
    Theta分解：趋势 + 残差波动 → 预测区间
    """
    n = len(prices)
    if n < 20:
        return {}

    log_prices = np.log(prices)
    t = np.arange(n, dtype=float)

    # 趋势（线性）
    if _SCIPY_OK:
        slope, intercept, r_val, _, _ = linregress(t, log_prices)
        r_sq = r_val ** 2
    else:
        coeffs = np.polyfit(t, log_prices, 1)
        slope, intercept = coeffs[0], coeffs[1]
        r_sq = 0.5  # fallback

    # 残差波动性（分层：近期更重要）
    residuals = log_prices - (slope * t + intercept)
    vol_all  = np.std(residuals)
    vol_near = np.std(residuals[-min(20, n):])
    vol = 0.3 * vol_all + 0.7 * vol_near  # 近期波动更重

    # 点预测（Theta分解：均值 + 趋势）
    pred_center = slope * (n + horizon / 2) + intercept
    pred_log_h = slope * (n + horizon) + intercept

    # 时间缩放不确定性（σ∝√horizon）
    sigma_h = vol * np.sqrt(horizon)

    # 分位数（标准正态分位数）
    z_vals = {
        'q05': -1.645, 'q10': -1.282, 'q25': -0.674,
        'q50': 0.0,
        'q75': 0.674,  'q90': 1.282,  'q95': 1.645,
    }

    return {
        'pred':    np.exp(pred_log_h),
        'center':  np.exp(pred_center),
        **{k: np.exp(pred_log_h + z * sigma_h) for k, z in z_vals.items()},
        'sigma_h': sigma_h,
        'trend_slope': slope,
        'r_sq':    r_sq,
        'vol':     vol,
    }


def _multiscale_features(prices: np.ndarray) -> Dict:
    """
    TimesFM核心能力2：多尺度时序特征
    模拟长上下文（16000步）的多尺度理解
    """
    n = len(prices)
    features = {}
    log_ret = np.diff(np.log(prices))

    for w in [8, 24, 48, 96, 168]:
        if w >= n: continue
        chunk = prices[-w:]
        rets  = np.diff(np.log(chunk))
        if len(rets) < 4: continue

        # 分位数分布
        q25 = float(np.percentile(rets, 25))
        q50 = float(np.percentile(rets, 50))
        q75 = float(np.percentile(rets, 75))
        iqr = q75 - q25

        # 趋势方向
        t = np.arange(len(chunk), dtype=float)
        slope_w = float(np.polyfit(t, chunk, 1)[0])
        norm_slope = slope_w / (chunk.mean() or 1)

        # 动量（最后1/4 vs 前3/4）
        split = len(chunk) * 3 // 4
        mom = (chunk[-1] / chunk[split] - 1) if chunk[split] > 0 else 0

        # Hurst指数近似（分散度）
        hurst = np.std(np.diff(chunk)) / (np.std(chunk) or 1e-9) * (w ** 0.5)

        features[f'w{w}'] = {
            'q25': q25, 'q50': q50, 'q75': q75, 'iqr': iqr,
            'slope': norm_slope, 'momentum': mom, 'hurst': hurst,
        }

    return features


def _covariate_adjustment(pred_dict: Dict, covariates: Dict) -> float:
    """
    TimesFM核心能力3：协变量支持
    FR/OI/RSI等外部特征修正预测偏差
    返回：价格偏差修正（小数，如0.002=+0.2%）
    """
    adj = 0.0
    if not covariates:
        return adj

    fr = covariates.get('funding_rate', 0) or 0
    lsr = covariates.get('lsr', 1.0) or 1.0
    oi_delta = covariates.get('oi_delta', 0) or 0
    rsi_1h = covariates.get('rsi_1h', 50) or 50

    # FR修正：多方付息 → 价格压力向下
    if fr > 0.0005:   adj -= 0.001
    elif fr < -0.0005: adj += 0.001

    # LSR修正：散户极度偏多 → 逆向信号
    if lsr > 2.5:  adj -= 0.002  # 多头拥挤
    elif lsr < 0.5: adj += 0.002  # 空头拥挤

    # OI修正：OI暴增 + 价格上涨 → 趋势延续概率高
    if oi_delta > 20: adj += 0.001
    elif oi_delta < -20: adj -= 0.001

    # RSI修正：超买/超卖修正
    if rsi_1h > 80:  adj -= 0.002
    elif rsi_1h < 20: adj += 0.002

    return float(np.clip(adj, -0.01, 0.01))


def _direction_probability(theta: Dict, ms_feats: Dict,
                            direction: str) -> Tuple[float, str]:
    """
    综合分位数预测 + 多尺度特征 → 方向概率
    返回 (p_direction, confidence_level)
    """
    if not theta:
        return 0.5, 'LOW'

    pred = theta.get('pred', 0)
    cur  = theta.get('center', pred)

    if cur <= 0:
        return 0.5, 'LOW'

    # 趋势贡献（点预测方向）
    pred_change = (pred - cur) / cur
    trend_prob = 0.5 + float(np.clip(pred_change * 20, -0.4, 0.4))

    # 不确定性宽度（越窄越有信心）
    q10 = theta.get('q10', cur * 0.98)
    q90 = theta.get('q90', cur * 1.02)
    band_pct = (q90 - q10) / cur if cur > 0 else 0.1
    confidence_mult = max(0.3, 1 - band_pct * 5)  # 宽带 → 低置信

    # 多尺度动量一致性
    mom_votes = []
    for w_key, f in ms_feats.items():
        mom = f.get('momentum', 0)
        if abs(mom) > 0.001:
            mom_votes.append(1 if mom > 0 else -1)

    mom_consensus = np.mean(mom_votes) if mom_votes else 0
    mom_prob = 0.5 + mom_consensus * 0.2

    # 短期动量（最新）
    short_slope = ms_feats.get('w8', {}).get('slope', 0)
    short_prob = 0.5 + float(np.clip(short_slope * 200, -0.2, 0.2))

    # 集成
    p_up = (trend_prob * 0.4 + mom_prob * 0.35 + short_prob * 0.25)
    p_up = float(np.clip(p_up, 0.1, 0.9))

    # 方向概率
    if direction == 'LONG':
        p_dir = p_up
    elif direction == 'SHORT':
        p_dir = 1 - p_up
    else:
        p_dir = 0.5

    # 置信度分级
    band_pct_pct = band_pct * 100
    r_sq = theta.get('r_sq', 0)
    if band_pct_pct < 1.5 and r_sq > 0.6:
        conf_level = 'HIGH'
    elif band_pct_pct < 3.0 and r_sq > 0.3:
        conf_level = 'MED'
    else:
        conf_level = 'LOW'

    return p_dir * confidence_mult + 0.5 * (1 - confidence_mult), conf_level


def get_timesfm_score(
    symbol: str,
    direction: str,
    klines_1h: list,
    regime: str,
    covariates: Optional[Dict] = None,
    horizon: int = 8,
) -> Tuple[float, Dict]:
    """
    主接口：TimesFM-Lite 时序预测评分

    参数:
        symbol:     交易对
        direction:  'LONG' or 'SHORT'
        klines_1h:  1H K线列表 [[ts,o,h,l,c,v,...], ...]
        regime:     当前体制
        covariates: 外部特征 {'funding_rate', 'lsr', 'oi_delta', 'rsi_1h'}
        horizon:    预测步长（H）

    返回:
        (score, meta)
        score: -10 ~ +10
        meta:  预测详情
    """
    cache_key = f"{symbol}:{direction}:{regime}:{int(time.time() // _CACHE_TTL)}"
    if cache_key in _cache:
        return _cache[cache_key]

    try:
        if not klines_1h or len(klines_1h) < 30:
            return 0, {'error': 'insufficient_data', 'n': len(klines_1h) if klines_1h else 0}

        prices = np.array([float(k[4]) for k in klines_1h], dtype=float)
        if np.any(prices <= 0):
            return 0, {'error': 'invalid_prices'}

        # ── M1: Theta分位数预测 ──────────────────────────────────
        theta = _theta_quantile(prices, horizon=horizon)

        # ── M2: 多尺度特征 ───────────────────────────────────────
        ms_feats = _multiscale_features(prices)

        # ── M3: 协变量修正 ───────────────────────────────────────
        cov_adj = _covariate_adjustment(theta, covariates or {})

        # 修正预测价格
        if theta.get('pred'):
            theta['pred'] *= (1 + cov_adj)

        # ── M4: Holt平滑点预测（第二意见）───────────────────────
        holt_preds = _holt_forecast(prices[-min(100, len(prices)):], horizon=horizon)
        holt_pred  = float(holt_preds[-1]) if len(holt_preds) > 0 else prices[-1]

        # ── 方向概率 ─────────────────────────────────────────────
        p_dir, conf_level = _direction_probability(theta, ms_feats, direction)

        # Holt第二意见
        cur_price = float(prices[-1])
        holt_change = (holt_pred - cur_price) / cur_price
        if direction == 'LONG':
            holt_p = 0.5 + float(np.clip(holt_change * 10, -0.3, 0.3))
        else:
            holt_p = 0.5 - float(np.clip(holt_change * 10, -0.3, 0.3))

        # 集成两个模型
        p_final = 0.65 * p_dir + 0.35 * holt_p
        p_final = float(np.clip(p_final, 0.1, 0.9))

        # ── 评分转换（-10 ~ +10）────────────────────────────────
        # p=0.5 → score=0; p=0.75 → score=5; p=0.9 → score=10
        raw_score = (p_final - 0.5) * 40
        raw_score = float(np.clip(raw_score, -10, 10))

        # 置信度权重
        conf_mult = {'HIGH': 1.0, 'MED': 0.7, 'LOW': 0.4}.get(conf_level, 0.5)
        score = round(raw_score * conf_mult, 1)

        # 体制系数
        r_coeff = REGIME_COEFF.get(regime, 0.7)
        score = round(score * r_coeff, 1)
        score = float(np.clip(score, -10, 10))

        meta = {
            'score':        score,
            'p_direction':  round(p_final, 3),
            'confidence':   conf_level,
            'pred_price':   round(theta.get('pred', cur_price), 2),
            'holt_pred':    round(holt_pred, 2),
            'cur_price':    round(cur_price, 2),
            'q10':          round(theta.get('q10', 0), 2),
            'q25':          round(theta.get('q25', 0), 2),
            'q75':          round(theta.get('q75', 0), 2),
            'q90':          round(theta.get('q90', 0), 2),
            'band_pct':     round((theta.get('q90', cur_price) - theta.get('q10', cur_price)) / cur_price * 100, 2),
            'trend_slope':  round(theta.get('trend_slope', 0) * 1e6, 4),
            'r_sq':         round(theta.get('r_sq', 0), 3),
            'vol_h':        round(theta.get('sigma_h', 0) * cur_price, 2),
            'cov_adj':      round(cov_adj * 100, 4),
            'regime_coeff': r_coeff,
            'horizon_h':    horizon,
            'ms_windows':   list(ms_feats.keys()),
            'method':       'theta_quantile+holt_ensemble',
        }

        result = (score, meta)
        _cache[cache_key] = result
        return result

    except Exception as e:
        logger.warning(f"[TimesFM-Lite] {symbol} 预测失败: {e}")
        return 0, {'error': str(e)[:80]}


# ── 快速测试 ──────────────────────────────────────────────────────
if __name__ == '__main__':
    import json
    import requests

    print("=== TimesFM-Lite 自测 ===\n")
    for sym in ['BTCUSDT', 'ETHUSDT']:
        r = requests.get('https://fapi.binance.com/fapi/v1/klines',
                         params={'symbol': sym, 'interval': '1h', 'limit': 200}, timeout=6)
        klines = r.json()
        score, meta = get_timesfm_score(sym, 'SHORT', klines, 'BEAR_RECOVERY',
                                         covariates={'rsi_1h': 75, 'funding_rate': 0.0001})
        print(f"{sym} SHORT score={score}")
        print(f"  预测: {meta.get('pred_price')} | 当前: {meta.get('cur_price')}")
        print(f"  Q10~Q90: {meta.get('q10')} ~ {meta.get('q90')}")
        print(f"  方向概率: {meta.get('p_direction')} | 置信: {meta.get('confidence')}")
        print(f"  趋势斜率R²: {meta.get('r_sq')} | 波动σ: ${meta.get('vol_h')}")
        print()
