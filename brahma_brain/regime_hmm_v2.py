"""
regime_hmm_v2.py — 梵天Regime HMM概率模型 v2.0
设计院 P3-A | 2026-07-08

升级: 规则硬分类 → HMM概率分布输出
输出: {BEAR: 0.65, BULL: 0.22, CHOP: 0.13} 概率向量
应用: 信号乘数按概率加权，替代当前硬切换逻辑

架构:
  1. 特征提取: RSI_4H / 收益率4H / 波动率4H / EMA偏离度
  2. HMM拟合: 3状态(BEAR/CHOP/BULL) 高斯HMM
  3. 概率输出: predict_proba → 当前状态概率分布
  4. 平滑: 指数加权平均避免噪声切换
  5. 降级: hmmlearn未安装时回退到规则基线
"""
import json, time, sys
import numpy as np
from pathlib import Path
from datetime import datetime, timezone

DATA_DIR = Path(__file__).parent.parent / 'data'
sys.path.insert(0, str(Path(__file__).parent.parent))

# 状态映射
STATES = ['BEAR_TREND', 'CHOP_MID', 'BULL_TREND']
STATE_IDX = {s: i for i, s in enumerate(STATES)}

# 平滑系数（防止高频切换）
SMOOTH_ALPHA = 0.3  # 越小越稳定


def _extract_features(klines_4h: list) -> np.ndarray:
    """从4H K线提取特征矩阵 [returns, volatility, rsi_norm, ema_dev]"""
    if len(klines_4h) < 20:
        return np.array([])

    closes  = np.array([float(k[4]) for k in klines_4h])
    volumes = np.array([float(k[5]) for k in klines_4h])

    # 1. 对数收益率
    returns = np.diff(np.log(closes))

    # 2. 滚动波动率(5周期)
    volatility = np.array([
        returns[max(0,i-5):i+1].std() if i >= 5 else returns[:i+1].std()
        for i in range(len(returns))
    ])

    # 3. RSI归一化(0-1)
    gains  = np.where(returns > 0, returns, 0)
    losses = np.where(returns < 0, -returns, 0)
    n = 14
    rsi_vals = []
    for i in range(len(returns)):
        if i < n:
            rsi_vals.append(0.5)
            continue
        ag = gains[i-n:i].mean()
        al = losses[i-n:i].mean()
        rs = ag / al if al > 0 else 100
        rsi_vals.append((100 - 100/(1+rs)) / 100)
    rsi_norm = np.array(rsi_vals)

    # 4. EMA20偏离度
    ema20 = closes.copy().astype(float)
    k_val = 2/21
    for i in range(1, len(ema20)):
        ema20[i] = closes[i]*k_val + ema20[i-1]*(1-k_val)
    ema_dev = (closes[1:] - ema20[1:]) / ema20[1:]

    # 对齐长度
    min_len = min(len(returns), len(rsi_norm), len(ema_dev))
    features = np.column_stack([
        returns[-min_len:],
        volatility[-min_len:],
        rsi_norm[-min_len:],
        ema_dev[-min_len:],
    ])
    return features


def fit_hmm(features: np.ndarray) -> object:
    """训练3状态高斯HMM"""
    try:
        from hmmlearn import hmm
        model = hmm.GaussianHMM(
            n_components=3,
            covariance_type='diag',
            n_iter=100,
            random_state=42,
        )
        model.fit(features)
        return model
    except ImportError:
        return None
    except Exception:
        return None


def predict_regime_proba(symbol: str, klines_4h: list = None) -> dict:
    """
    主入口: 返回当前体制概率分布
    
    Returns:
        {
          'BEAR_TREND': 0.65,
          'CHOP_MID':   0.22,
          'BULL_TREND': 0.13,
          'dominant':   'BEAR_TREND',
          'confidence': 0.65,
          'method':     'hmm' | 'rule_fallback',
          'ts':         1234567890.0,
        }
    """
    ts_now = time.time()

    # 检查缓存(30分钟)
    cache_path = DATA_DIR / f'hmm_cache_{symbol}.json'
    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text())
            if ts_now - cached.get('ts', 0) < 1800:
                return cached
        except Exception:
            pass

    # 获取K线
    if klines_4h is None:
        try:
            sys.path.insert(0, str(Path(__file__).parent.parent))
            from scripts.auto_executor import _signed
            klines_4h = _signed('GET', '/fapi/v1/klines',
                                 {'symbol': symbol, 'interval': '4h', 'limit': 100})
        except Exception:
            return _rule_fallback(symbol, ts_now)

    features = _extract_features(klines_4h)
    if len(features) < 30:
        return _rule_fallback(symbol, ts_now)

    # 训练HMM
    model = fit_hmm(features)
    if model is None:
        return _rule_fallback(symbol, ts_now, features)

    try:
        # 预测当前状态概率
        log_proba = model.predict_proba(features)
        current_proba = log_proba[-1]  # 最新时间点

        # 按均值排序映射状态(均值最高=BULL, 最低=BEAR)
        means = model.means_[:, 0]  # 收益率维度
        order = np.argsort(means)   # [BEAR_idx, CHOP_idx, BULL_idx]

        result_proba = {
            'BEAR_TREND': float(current_proba[order[0]]),
            'CHOP_MID':   float(current_proba[order[1]]),
            'BULL_TREND': float(current_proba[order[2]]),
        }

        dominant = max(result_proba, key=result_proba.get)
        confidence = result_proba[dominant]

        result = {
            **result_proba,
            'dominant':   dominant,
            'confidence': round(confidence, 3),
            'method':     'hmm',
            'symbol':     symbol,
            'ts':         ts_now,
        }
    except Exception:
        result = _rule_fallback(symbol, ts_now, features)

    # 写入缓存
    try:
        cache_path.write_text(json.dumps(result, ensure_ascii=False))
    except Exception:
        pass

    return result


def _rule_fallback(symbol: str, ts: float, features: np.ndarray = None) -> dict:
    """HMM不可用时的规则基线"""
    try:
        with open(DATA_DIR / 'regime_state.json') as f:
            rs = json.load(f)
        regime = rs.get(symbol, {}).get('confirmed', 'BULL_TREND')
    except Exception:
        regime = 'BULL_TREND'

    proba_map = {
        'BULL_TREND':    {'BULL_TREND': 0.70, 'CHOP_MID': 0.20, 'BEAR_TREND': 0.10},
        'BEAR_TREND':    {'BULL_TREND': 0.10, 'CHOP_MID': 0.20, 'BEAR_TREND': 0.70},
        'BEAR_RECOVERY': {'BULL_TREND': 0.45, 'CHOP_MID': 0.30, 'BEAR_TREND': 0.25},
        'CHOP_MID':      {'BULL_TREND': 0.30, 'CHOP_MID': 0.50, 'BEAR_TREND': 0.20},
    }
    proba = proba_map.get(regime, proba_map['CHOP_MID'])
    return {
        **proba,
        'dominant':   regime,
        'confidence': proba.get(regime.replace('BEAR_RECOVERY','BULL_TREND'), 0.45),
        'method':     'rule_fallback',
        'symbol':     symbol,
        'ts':         ts,
    }


def get_weighted_multiplier(symbol: str, direction: str) -> float:
    """
    根据HMM概率分布返回加权乘数
    替代当前硬切换的 REGIME_MULT 矩阵
    
    Example:
      BEAR=0.65, CHOP=0.22, BULL=0.13
      LONG乘数: 0.65×0.10 + 0.22×0.50 + 0.13×1.60 = 0.482
    """
    proba = predict_regime_proba(symbol)

    # 各体制各方向乘数（来自 brahma_core _REGIME_MULT）
    MULT = {
        'BEAR_TREND': {'LONG': 0.10, 'SHORT': 1.60},
        'CHOP_MID':   {'LONG': 0.50, 'SHORT': 0.88},
        'BULL_TREND': {'LONG': 1.60, 'SHORT': 0.15},
    }
    dir_key = 'LONG' if direction in ('LONG', 'BUY') else 'SHORT'

    weighted = (
        proba.get('BEAR_TREND', 0) * MULT['BEAR_TREND'][dir_key] +
        proba.get('CHOP_MID',   0) * MULT['CHOP_MID'][dir_key]   +
        proba.get('BULL_TREND', 0) * MULT['BULL_TREND'][dir_key]
    )
    return round(weighted, 3)


if __name__ == '__main__':
    for sym in ['BTCUSDT', 'ETHUSDT']:
        r = predict_regime_proba(sym)
        print(f"\n{sym} Regime概率分布:")
        for k in ['BEAR_TREND','CHOP_MID','BULL_TREND']:
            bar = '█' * int(r.get(k,0)*20)
            print(f"  {k:<16} {r.get(k,0):.3f} {bar}")
        print(f"  dominant={r['dominant']} conf={r['confidence']:.3f} method={r['method']}")
        wm_long  = get_weighted_multiplier(sym, 'LONG')
        wm_short = get_weighted_multiplier(sym, 'SHORT')
        print(f"  加权乘数: LONG={wm_long} SHORT={wm_short}")
