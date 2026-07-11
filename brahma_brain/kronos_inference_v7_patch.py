"""
kronos_engine.py _run_inference v7.0 补丁
设计院 2026-07-11 六方联合自主决策
使用 monkey-patch 方式注入，不直接编辑 kronos_engine.py 核心逻辑
"""
import time
import math
import logging

logger = logging.getLogger("kronos_v7_patch")


def _run_inference_v7(klines_15m: list, symbol: str):
    """
    v7.0: 14条独立路径真实p_up（替换正态假设）
    - 每次 sample_count=1 循环14次
    - p_up = 上涨路径占比（非正态近似）
    - 附加：路径偏度、p50最大回撤、TP2触达概率
    返回 (p_up_blend, pred_std, position_hint_dict)
    """
    import pandas as pd
    import numpy as np
    from datetime import datetime, timedelta
    from scipy import stats as sp_stats

    # 导入模型（从 kronos_engine 取单例）
    import brahma_brain.kronos_engine as _ke
    if not _ke._load_model():
        return 0.5, 0.02, {}
    predictor = _ke._predictor

    # 准备 DataFrame（与原版相同）
    _raw = [k for k in klines_15m[-200:] if k]
    if _raw and isinstance(_raw[0], (list, tuple)):
        _ncols = len(_raw[0])
        if _ncols >= 6 and isinstance(_raw[0][0], (int, float)) and _raw[0][0] > 1e12:
            _raw = [[r[1], r[2], r[3], r[4], r[5]] for r in _raw]
        elif _ncols >= 5:
            _raw = [[r[0], r[1], r[2], r[3], r[4]] for r in _raw]
    df = pd.DataFrame(_raw, columns=["open", "high", "low", "close", "volume"]).astype(float)

    if df.isnull().values.any():
        return 0.5, 0.02, {}

    current_close = float(df['close'].iloc[-1])

    base_ts = datetime(2026, 1, 1)
    _x_list = [base_ts + timedelta(minutes=15 * i) for i in range(len(df))]
    _y_list = [_x_list[-1] + timedelta(minutes=15 * (i + 1)) for i in range(16)]
    x_timestamps = pd.Series(pd.to_datetime(_x_list))
    y_timestamps = pd.Series(pd.to_datetime(_y_list))

    # 14条独立路径
    paths_final = []
    paths_max_dd = []
    t0 = time.time()

    for _ in range(14):
        try:
            pred = predictor.predict(
                df=df,
                x_timestamp=x_timestamps,
                y_timestamp=y_timestamps,
                pred_len=16,
                sample_count=1,
                verbose=False
            )
            closes = pred['close'].values.astype(float)
            paths_final.append(float(closes[-1]))
            min_c = float(min(closes))
            paths_max_dd.append((current_close - min_c) / current_close if min_c < current_close else 0.0)
        except Exception:
            continue

    elapsed_ms = (time.time() - t0) * 1000
    logger.info(f"[Kronos v7.0] {symbol} 推理 {len(paths_final)} 路径 {elapsed_ms:.0f}ms")

    if not paths_final:
        return 0.5, 0.02, {}

    paths_arr = np.array(paths_final)

    # 真实p_up（路径统计）
    p_up_real = float(np.sum(paths_arr > current_close)) / len(paths_arr)

    # 正态近似 p_up（辅助）
    mean_ret = float((paths_arr.mean() - current_close) / current_close)
    pred_std = float(paths_arr.std() / current_close)
    if pred_std > 1e-6:
        z = mean_ret / pred_std
        p_up_gauss = 0.5 * (1 + math.erf(z / math.sqrt(2)))
    else:
        p_up_gauss = 0.5 + (0.5 if mean_ret > 0 else -0.5)

    # 混合（小样本下限制方差）
    p_up_blend = 0.6 * p_up_real + 0.4 * p_up_gauss
    p_up_blend = max(0.05, min(0.95, p_up_blend))

    # 路径统计
    path_skew = float(sp_stats.skew(paths_arr)) if len(paths_arr) >= 3 else 0.0
    p50_max_dd = float(np.median(paths_max_dd)) if paths_max_dd else 0.02
    tp2_prob = float(np.sum(paths_arr > current_close * 1.04) / len(paths_arr))

    hint = {
        'p_up_real':      p_up_real,
        'p_up_gauss':     p_up_gauss,
        'p_up_blend':     p_up_blend,
        'path_skew':      path_skew,
        'p50_max_dd':     p50_max_dd,
        'pred_std':       pred_std,
        'tp2_probability': tp2_prob,
        'n_paths':        len(paths_final),
        'elapsed_ms':     elapsed_ms,
        'model':          'kronos-mini-v7.0',
    }

    return p_up_blend, pred_std, hint
