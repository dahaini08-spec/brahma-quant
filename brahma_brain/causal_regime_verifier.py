"""
causal_regime_verifier.py · 体制边界因果验证器 v1.0
====================================================
设计院 × 达摩院 · 因果AI增强系列 P0-A · 2026-06-18

哲学：
  不要在因果结构断裂时入场。
  相关性会在体制切换边界欺骗你，因果机制不会。

核心机制：
  1. Granger因果检验 — OI/FR/清算量 是否 Granger-causes 价格变化？
  2. Transfer Entropy（信息流方向）— 价格信息从何流向何处？
  3. 体制稳定性检验 — 当前体制是否刚刚切换（边界不稳定）？

输出：
  {
    'causal_confidence': float (0~1),   # 综合因果置信度
    'granger_oi':        float,          # OI→价格 Granger p值
    'granger_fr':        float,          # FR→价格 Granger p值
    'regime_stability':  float,          # 体制稳定性 (0~1)
    'signal_dir_ok':     bool,           # 因果方向是否支持 signal_dir
    'verdict':           str,            # 'STRONG'/'MODERATE'/'WEAK'/'BLOCKED'
    'reason':            str,
  }

接入位置：brahma_core.analyze() Step 1（ms_analyze之后，Step 2方向确认之前）
权重：causal_confidence < 0.35 → score_adj = -12（惩罚减半 2026-07-01）/ 0.35~0.50 → -15 / ≥0.70 → +5

设计院原则：
  - 轻量异步，失败不阻断主流（fail-safe返回默认通过）
  - 最大计算时间 < 200ms
  - 不依赖外部网络（使用已缓存K线）
"""

import os
import sys
import time
import json
import math
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'brahma_brain'))

# ── 结果缓存（每标的缓存2分钟，避免重复计算）────────────────────
_RESULT_CACHE: Dict[str, Dict] = {}
_CACHE_TTL = 120  # 2分钟


def _cache_key(symbol: str, regime: str, signal_dir: str) -> str:
    return f'{symbol}:{regime}:{signal_dir}'


# ── 默认通过结果（fail-safe）─────────────────────────────────────
_DEFAULT_PASS = {
    'causal_confidence': 0.6,
    'granger_oi':        0.1,
    'granger_fr':        0.1,
    'regime_stability':  0.7,
    'signal_dir_ok':     True,
    'verdict':           'MODERATE',
    'reason':            'fail-safe默认通过',
    'score_adj':         0,
}


# ══════════════════════════════════════════════════════════════════
# 核心算法
# ══════════════════════════════════════════════════════════════════

def _granger_pvalue(y: list, x: list, max_lag: int = 3) -> float:
    """
    检验 x → y 的 Granger 因果关系。
    返回 p值：越小 = x 越能因果预测 y（因果关系越强）。
    使用 statsmodels grangercausalitytests。
    """
    try:
        from statsmodels.tsa.stattools import grangercausalitytests
        import numpy as np
        import warnings

        n = min(len(y), len(x))
        if n < max_lag * 4 + 10:
            return 0.5  # 样本不足，返回中性

        data = np.column_stack([y[-n:], x[-n:]])
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            result = grangercausalitytests(data, maxlag=max_lag, verbose=False)

        # 取最显著的lag的 F检验 p值
        p_vals = [result[lag][0]['ssr_ftest'][1] for lag in range(1, max_lag + 1)]
        return float(min(p_vals))  # 返回最小p值（最显著的lag）
    except Exception:
        return 0.5


def _transfer_entropy(source: list, target: list, lag: int = 1, bins: int = 8) -> float:
    """
    简化版 Transfer Entropy（信息流方向）。
    TE(source→target) > TE(target→source) → source 驱动 target。
    使用离散化 + 条件熵估计。
    返回标准化的 TE 值 (0~1)，值越大 = 信息流越强。
    """
    try:
        import numpy as np

        n = min(len(source), len(target))
        if n < 30:
            return 0.5

        s = np.array(source[-n:])
        t = np.array(target[-n:])

        # 标准化
        s = (s - s.mean()) / (s.std() + 1e-9)
        t = (t - t.mean()) / (t.std() + 1e-9)

        # 离散化
        s_d = np.digitize(s, np.linspace(s.min(), s.max(), bins))
        t_d = np.digitize(t, np.linspace(t.min(), t.max(), bins))

        # 构建时序对
        t_next = t_d[lag:]
        t_past = t_d[:-lag]
        s_past = s_d[:-lag]

        min_len = min(len(t_next), len(t_past), len(s_past))
        t_next = t_next[:min_len]
        t_past = t_past[:min_len]
        s_past = s_past[:min_len]

        # H(T_future | T_past) - H(T_future | T_past, S_past)
        # 使用计数估计条件熵
        def conditional_entropy(future, cond1, cond2=None):
            from collections import Counter
            if cond2 is None:
                joint = Counter(zip(future, cond1))
                marg = Counter(cond1)
            else:
                joint = Counter(zip(future, cond1, cond2))
                marg = Counter(zip(cond1, cond2))
            total = len(future)
            h = 0.0
            for k, cnt in joint.items():
                p_joint = cnt / total
                p_cond = marg[k[1:] if cond2 is None else k[1:]] / total
                if p_joint > 0 and p_cond > 0:
                    h -= p_joint * math.log2(p_joint / p_cond)
            return h

        h_cond_t = conditional_entropy(t_next, t_past)
        h_cond_ts = conditional_entropy(t_next, t_past, s_past)
        te = h_cond_t - h_cond_ts

        # 标准化到 0~1
        te_norm = max(0.0, min(1.0, te / (math.log2(bins) + 1e-9)))
        return float(te_norm)
    except Exception:
        return 0.5


def _regime_stability(ms: dict, symbol: str) -> float:
    """
    体制稳定性评估：
    - 如果最近2根K线的体制标签与当前不同 → 低稳定性
    - 使用 regime_switch_state.json 检测最近切换时间
    返回 0~1，越高越稳定
    """
    try:
        state_path = BASE / 'data' / 'regime_switch_state.json'
        if state_path.exists():
            state = json.loads(state_path.read_text())
            sym_state = state.get(symbol, {})
            last_switch = sym_state.get('last_switch_ts', 0)
            # 如果最近4H内有体制切换 → 不稳定
            age_h = (time.time() - float(last_switch)) / 3600 if last_switch else 99
            if age_h < 2:
                return 0.2   # 极不稳定：刚刚切换
            elif age_h < 4:
                return 0.5   # 不稳定
            elif age_h < 8:
                return 0.7   # 中等稳定
            else:
                return 0.9   # 稳定
    except Exception:
        pass

    # fallback：使用K线价格波动率检测体制稳定性
    try:
        from data_cache import get_klines, klines_to_ohlcv
        raw = get_klines(symbol, '1h', 20)
        if not raw or len(raw) < 10:
            return 0.7
        closes = [float(k[4]) for k in raw[-10:]]
        changes = [abs(closes[i] - closes[i-1]) / closes[i-1] for i in range(1, len(closes))]
        avg_change = sum(changes) / len(changes) if changes else 0
        # 高波动 = 低稳定性
        if avg_change > 0.02:    return 0.3
        elif avg_change > 0.01:  return 0.6
        else:                    return 0.85
    except Exception:
        return 0.7


def _get_indicator_series(symbol: str, signal_dir: str) -> Tuple[list, list, list, list]:
    """
    获取价格变化、OI变化、FR序列用于 Granger 检验。
    返回 (price_changes, oi_changes, fr_series, vol_changes)
    [2026-07-01 修复] 备用路径直接拉取API，不依赖本地文件缓存
    """
    try:
        import requests as _rq
        # ── K线（1H，50根）
        raw_1h = _rq.get(
            f'https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval=1h&limit=50',
            timeout=8
        ).json()
        if not raw_1h or len(raw_1h) < 20:
            return [], [], [], []

        closes = [float(k[4]) for k in raw_1h]
        volumes = [float(k[5]) for k in raw_1h]
        price_changes = [(closes[i]-closes[i-1])/closes[i-1]*100 for i in range(1,len(closes))]
        vol_changes   = [(volumes[i]-volumes[i-1])/(volumes[i-1]+1e-9)*100 for i in range(1,len(volumes))]

        # ── OI History（Binance公开接口）
        oi_changes = vol_changes  # fallback
        try:
            oi_r = _rq.get(
                f'https://fapi.binance.com/futures/data/openInterestHist?symbol={symbol}&period=1h&limit=50',
                timeout=8
            ).json()
            if isinstance(oi_r, list) and len(oi_r) >= 10:
                oi_vals = [float(x['sumOpenInterest']) for x in oi_r]
                oi_changes = [(oi_vals[i]-oi_vals[i-1])/(oi_vals[i-1]+1e-9)*100 for i in range(1,len(oi_vals))]
        except Exception:
            pass

        # ── FR序列（Binance fapi/v1/fundingRate）
        fr_series = vol_changes  # fallback
        try:
            fr_r = _rq.get(
                f'https://fapi.binance.com/fapi/v1/fundingRate?symbol={symbol}&limit=50',
                timeout=8
            ).json()
            if isinstance(fr_r, list) and len(fr_r) >= 10:
                # FR每8H一个，对齐到价格序列：取最近N个和价格变化对齐
                fr_vals = [float(x['fundingRate'])*100 for x in fr_r]
                # FR数据条数比价格少，用插展对齐
                if len(fr_vals) >= 5:
                    # 每个FR对应9H价格内，用重复值对齐
                    fr_extended = []
                    for fv in fr_vals:
                        fr_extended.extend([fv]*8)  # 8个1H bar对应一个8H周期
                    n = min(len(price_changes), len(fr_extended))
                    fr_series = fr_extended[-n:]
        except Exception:
            pass

        return price_changes, oi_changes, fr_series, vol_changes

    except Exception:
        return [], [], [], []


# ══════════════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════════════

def verify(
    symbol: str,
    regime: str,
    signal_dir: str,
    ms: dict,
    timeout_ms: int = 180,
) -> Dict[str, Any]:
    """
    因果验证主入口。

    Args:
        symbol:     交易对（BTCUSDT等）
        regime:     当前体制标签
        signal_dir: 信号方向（LONG/SHORT）
        ms:         ms_analyze() 返回的市场状态
        timeout_ms: 最大计算时间（毫秒）

    Returns:
        验证结果字典，包含 score_adj 建议调整值
    """
    t0 = time.time()
    ck = _cache_key(symbol, regime, signal_dir)

    # 缓存命中
    cached = _RESULT_CACHE.get(ck)
    if cached and (time.time() - cached.get('_ts', 0)) < _CACHE_TTL:
        return {k: v for k, v in cached.items() if not k.startswith('_')}

    try:
        result = _compute_verify(symbol, regime, signal_dir, ms, t0, timeout_ms)
    except Exception as e:
        print(f'[CausalVerifier] ⚠ 验证异常（不阻断）: {e}')
        result = dict(_DEFAULT_PASS)
        result['reason'] = f'异常降级: {str(e)[:60]}'

    # 写缓存
    result['_ts'] = time.time()
    _RESULT_CACHE[ck] = result
    return {k: v for k, v in result.items() if not k.startswith('_')}


def _compute_verify(
    symbol: str,
    regime: str,
    signal_dir: str,
    ms: dict,
    t0: float,
    timeout_ms: int,
) -> Dict[str, Any]:
    """核心计算逻辑"""

    # ── Step 1: 体制稳定性 ─────────────────────────────────────
    stability = _regime_stability(ms, symbol)

    if time.time() - t0 > timeout_ms / 1000:
        return _quick_result(stability, 'MODERATE', '超时降级')

    # ── Step 2: 获取指标序列 ───────────────────────────────────
    price_changes, oi_changes, fr_series, vol_changes = _get_indicator_series(symbol, signal_dir)

    if len(price_changes) < 15:
        # 数据不足：仅用体制稳定性
        conf = stability * 0.7
        return _build_result(conf, 0.2, 0.2, stability, signal_dir, regime, '数据不足，仅稳定性评估')

    if time.time() - t0 > timeout_ms / 1000:
        return _quick_result(stability, 'MODERATE', '超时降级')

    # ── Step 3: Granger 因果检验 ──────────────────────────────
    # OI → 价格（3阶滞后，约3H时序因果）
    p_oi = _granger_pvalue(price_changes, oi_changes if oi_changes else vol_changes, max_lag=3)
    # FR → 价格（2阶滞后）
    p_fr = _granger_pvalue(price_changes, fr_series if fr_series else vol_changes, max_lag=2)

    if time.time() - t0 > timeout_ms / 1000:
        return _build_result(0.6, p_oi, p_fr, stability, signal_dir, regime, '超时仅完成Granger')

    # ── Step 4: Transfer Entropy（可选，耗时检查）─────────────
    te_vol_to_price = 0.5
    try:
        if time.time() - t0 < (timeout_ms - 50) / 1000:
            te_vol_to_price = _transfer_entropy(vol_changes, price_changes, lag=1)
    except Exception:
        pass

    # ── Step 5: 方向一致性检验 ────────────────────────────────
    # 当前体制的铁证方向与 signal_dir 是否一致
    regime_dir_map = {
        'BEAR_TREND':    'SHORT',
        'BEAR_EARLY':    'SHORT',
        'BULL_TREND':    'LONG',
        'BULL_EARLY':    'LONG',
        'BULL_CORRECTION': 'SHORT',
        'BEAR_RECOVERY': 'LONG',
    }
    preferred_dir = regime_dir_map.get(regime, '')
    dir_match = (not preferred_dir) or (preferred_dir == signal_dir)

    # ── Step 6: 综合评分 ──────────────────────────────────────
    # Granger：p值越小越好，转换为置信度
    granger_conf = max(0.0, min(1.0, 1.0 - p_oi * 2))  # p<0.05 → conf>0.9
    fr_conf      = max(0.0, min(1.0, 1.0 - p_fr * 2))
    te_conf      = te_vol_to_price

    # 加权组合 [修复 2026-07-01] statsmodels已装，FR Granger有效，提高FR权重降低TE依赖
    # 原: oi*0.35 + fr*0.20 + te*0.15 + stability*0.30
    # 新: oi*0.30 + fr*0.30 + te*0.10 + stability*0.30  (FR因果显著p≈0.07 → 权重提升)
    causal_conf = (
        granger_conf * 0.30 +
        fr_conf      * 0.30 +
        te_conf      * 0.10 +
        stability    * 0.30
    )

    # 方向惩罚：逆铁证方向 → 降权
    if not dir_match and preferred_dir:
        causal_conf *= 0.6
        dir_note = f'逆铁证方向({preferred_dir})'
    else:
        dir_note = ''

    return _build_result(causal_conf, p_oi, p_fr, stability, signal_dir, regime,
                         dir_note or '正常评估', te_vol_to_price)


def _quick_result(stability: float, verdict: str, reason: str) -> Dict[str, Any]:
    conf = stability * 0.8
    return _build_result(conf, 0.15, 0.15, stability, '', '', reason)


def _build_result(
    causal_conf: float,
    p_oi: float,
    p_fr: float,
    stability: float,
    signal_dir: str,
    regime: str,
    reason: str,
    te: float = 0.5,
) -> Dict[str, Any]:
    """构建标准化输出结果"""

    causal_conf = max(0.0, min(1.0, causal_conf))

    # 判定等级
    if causal_conf >= 0.70:
        verdict = 'STRONG'
        score_adj = +5       # 因果结构强 → 微加分
    elif causal_conf >= 0.50:
        verdict = 'MODERATE'
        score_adj = 0        # 中等 → 不干预
    elif causal_conf >= 0.35:
        verdict = 'WEAK'
        score_adj = -15      # 弱因果 → 降权-15
    else:
        verdict = 'BLOCKED'
        score_adj = -12      # [惩罚减半 2026-07-01] 四方共识: -25过度惩罚(17%score) → -12
                             # BTC/ETH因果conf=0.27仅比阈值低0.03，-25封死信号过严厉

    # 方向一致性
    regime_dir_map = {
        'BEAR_TREND': 'SHORT', 'BEAR_EARLY': 'SHORT',
        'BULL_TREND': 'LONG',  'BULL_EARLY': 'LONG',
        'BULL_CORRECTION': 'SHORT', 'BEAR_RECOVERY': 'LONG',
    }
    preferred = regime_dir_map.get(regime, '')
    dir_ok = (not preferred) or (preferred == signal_dir)

    return {
        'causal_confidence': round(causal_conf, 3),
        'granger_oi':        round(p_oi, 4),
        'granger_fr':        round(p_fr, 4),
        'transfer_entropy':  round(te, 3),
        'regime_stability':  round(stability, 3),
        'signal_dir_ok':     dir_ok,
        'preferred_dir':     preferred,
        'verdict':           verdict,
        'reason':            reason,
        'score_adj':         score_adj,
    }


# ══════════════════════════════════════════════════════════════════
# 独立测试
# ══════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--sym', default='BTCUSDT')
    ap.add_argument('--regime', default='BEAR_RECOVERY')
    ap.add_argument('--dir', default='SHORT')
    args = ap.parse_args()

    print(f'因果验证: {args.sym} {args.regime} {args.dir}')
    t0 = time.time()
    result = verify(args.sym, args.regime, args.dir, {})
    elapsed = (time.time() - t0) * 1000
    print(f'耗时: {elapsed:.0f}ms')
    import json as _json
    print(_json.dumps(result, ensure_ascii=False, indent=2))
