"""

# ── STATUS: AUXILIARY ──────────────────────────────────────────
# Kronos完整版，训练时使用
# LAST_REVIEW: 2026-07-01 | 属于辅助计算层，修改前确认调用链
# ─────────────────────────────────────────────────────────────
brahma_brain/kronos_engine.py — Kronos s23 维度引擎 v1.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
设计院 × 达摩院联合设计 · 2026-06-17

职责：
  基于 Kronos K线基础模型，为梵天评分系统提供 s23 预测维度
  分数范围：-12 ~ +12（含体制降权后）

三院约束（必须遵守）：
  1. CHOP_MID/HIGH/LOW 体制下 s23 × 0.3（防误激活）
  2. 缓存TTL = 900s（15分钟），同一标的LONG/SHORT共享推理
  3. fail-safe：任何异常返回 (0, reason)，不影响主流程

模型配置：
  tokenizer: NeoQuasar/Kronos-Tokenizer-2k（2048上下文）
  model:     NeoQuasar/Kronos-mini（4.1M参数，CPU可用）
  pred_len:  16根15m = 4小时预测窗口
  samples:   20条采样路径
"""

import sys
import os
import time
import json
import logging
from typing import Tuple, Dict, Optional

logger = logging.getLogger("kronos_engine")

# Kronos repo 路径
_KRONOS_PATH = os.path.join(os.path.dirname(__file__), '..', 'external', 'Kronos')
if os.path.exists(_KRONOS_PATH) and _KRONOS_PATH not in sys.path:
    sys.path.insert(0, _KRONOS_PATH)

# ── 体制常量 ──────────────────────────────────────────────────────
CHOP_REGIMES    = {'CHOP_MID', 'CHOP_HIGH', 'CHOP_LOW'}
EARLY_REGIMES   = {'BULL_EARLY', 'BEAR_EARLY'}
TREND_REGIMES   = {'BULL_TREND', 'BEAR_TREND'}

# 体制系数（达摩院封印）
REGIME_COEFF = {
    'CHOP_MID':         0.3,   # 防误激活
    'CHOP_HIGH':        0.3,
    'CHOP_LOW':         0.3,
    'BULL_EARLY':       1.0,   # 全力
    'BEAR_EARLY':       1.0,
    'BULL_TREND':       0.7,   # 辅助
    'BEAR_TREND':       0.7,
    'BULL_CORRECTION':  0.8,
    'BEAR_RECOVERY':    0.8,
}

# ── 缓存 key：每个标的共享一次推理，LONG/SHORT用同一p_up ─────────
_CACHE: Dict[str, Tuple[float, float, float, str]] = {}
# {symbol: (timestamp, p_up, volatility, model_version)}
_CACHE_TTL = 900  # 15分钟


def _is_available() -> bool:
    """检查 Kronos 和 torch 是否可用"""
    try:
        import torch  # noqa
        from model import Kronos, KronosPredictor, KronosTokenizer  # noqa
        return True
    except ImportError:
        return False


# ── 单例模型持有者 ────────────────────────────────────────────────
_predictor: Optional[object] = None
_model_loaded = False
_model_load_attempted = False


def _load_model() -> bool:
    """懒加载模型（首次调用时初始化）"""
    global _predictor, _model_loaded, _model_load_attempted
    if _model_load_attempted:
        return _model_loaded
    _model_load_attempted = True

    try:
        import json as _json
        from model import Kronos, KronosPredictor, KronosTokenizer
        from safetensors.torch import load_file as _load_sf
        from huggingface_hub import hf_hub_download as _hf_dl

        _cache = os.path.join(_KRONOS_PATH, '..', 'data', 'kronos_cache')
        os.makedirs(_cache, exist_ok=True)

        logger.info('[Kronos] 加载模型中... device=cpu')

        # 加载 Kronos主模型
        cfg_path = _hf_dl('NeoQuasar/Kronos-mini', 'config.json', cache_dir=_cache)
        w_path   = _hf_dl('NeoQuasar/Kronos-mini', 'model.safetensors', cache_dir=_cache)
        cfg = _json.load(open(cfg_path))
        model = Kronos(
            d_model=cfg['d_model'], n_layers=cfg['n_layers'], n_heads=cfg['n_heads'],
            ff_dim=cfg['ff_dim'], s1_bits=cfg['s1_bits'], s2_bits=cfg['s2_bits'],
            learn_te=cfg['learn_te'], attn_dropout_p=0, ffn_dropout_p=0,
            resid_dropout_p=0, token_dropout_p=0,
        )
        model.load_state_dict(_load_sf(w_path))
        model.eval()

        # 加载 KronosTokenizer（珬立 HF repo）
        tokenizer = KronosTokenizer.from_pretrained(
            'NeoQuasar/Kronos-Tokenizer-base',
            cache_dir=_cache
        )

        _predictor = KronosPredictor(
            model=model,
            tokenizer=tokenizer,
            device='cpu',
            max_context=512,
        )
        _model_loaded = True
        logger.info('[Kronos] ✅ 模型加载完成 Kronos-mini + Tokenizer-base CPU')
        return True

    except Exception as e:
        logger.warning(f'[Kronos] ⚠️ 模型加载失败（s23将返回0）: {e}')
        _model_loaded = False
        return False


def _run_inference(klines_15m: list, symbol: str) -> Tuple[float, float]:
    """
    执行 Kronos 推理，返回 (p_up, volatility)
    p_up：未来16根K线中，收盘价 > 当前收盘价的概率（20条路径均值）
    volatility：预测路径收盘价标准差（归一化）
    """
    import pandas as pd
    import numpy as np
    from datetime import datetime, timedelta

    # 准备 DataFrame
    df = pd.DataFrame(
        klines_15m[-200:],
        columns=["open", "high", "low", "close", "volume"]
    ).astype(float)

    if df.isnull().values.any():
        raise ValueError("klines含NaN")

    current_close = df['close'].iloc[-1]

    # 构造时间戳（15m间隔，不需要精确，Kronos需要时间特征）
    base_ts = datetime(2026, 1, 1)
    x_timestamps = [base_ts + timedelta(minutes=15 * i) for i in range(len(df))]
    y_timestamps = [x_timestamps[-1] + timedelta(minutes=15 * (i + 1)) for i in range(16)]

    # 执行推理（20条路径）
    t0 = time.time()
    pred_df = _predictor.predict(
        df=df,
        x_timestamp=x_timestamps,
        y_timestamp=y_timestamps,
        pred_len=16,
        sample_count=20,
        verbose=False
    )
    elapsed_ms = (time.time() - t0) * 1000

    logger.debug(f"[Kronos] {symbol} 推理耗时 {elapsed_ms:.0f}ms")

    # 多路径 p_up（pred_df 是单路径均值，需要判断）
    # 当 sample_count>1 时，pred_df 是所有路径的均值
    # 用最后一根预测K线的close判断方向
    pred_close_final = pred_df['close'].iloc[-1]

    # 估算 p_up（基于预测路径均值 vs 当前价）
    # 以预测路径的偏移量估算概率分布
    mean_return = (pred_close_final - current_close) / current_close
    pred_std = pred_df['close'].std() / current_close  # 归一化波动率

    # 将连续预测转换为概率
    # 基于正态假设：p_up = Φ(mean/std)
    import math
    if pred_std > 1e-6:
        z = mean_return / pred_std
        # 近似正态CDF
        p_up = 0.5 * (1 + math.erf(z / math.sqrt(2)))
    else:
        p_up = 0.5 + (0.5 if mean_return > 0 else -0.5)

    p_up = max(0.05, min(0.95, p_up))

    return p_up, pred_std


def _p_up_to_score(p_up: float, direction: str) -> int:
    """将方向概率转换为原始分数（未乘体制系数）"""
    if direction == "LONG":
        if p_up > 0.70:   return +12
        elif p_up > 0.60: return +8
        elif p_up > 0.55: return +4
        elif p_up > 0.45: return 0
        elif p_up > 0.35: return -8
        else:              return -12
    else:  # SHORT
        p_down = 1.0 - p_up
        if p_down > 0.70:   return +12
        elif p_down > 0.60: return +8
        elif p_down > 0.55: return +4
        elif p_down > 0.45: return 0
        elif p_down > 0.35: return -8
        else:                return -12


def get_kronos_score(
    symbol: str,
    direction: str,
    klines_15m: list,
    regime: str = ""
) -> Tuple[int, str]:
    """
    对外主接口，供 brahma_core.py 调用

    Args:
        symbol:     "BTCUSDT"
        direction:  "LONG" | "SHORT"
        klines_15m: List[OHLCV]，最近N根15m K线
        regime:     当前体制标签（可选，不传则不做体制降权）

    Returns:
        (score, reason)
        score: -12 ~ +12（含体制系数）
        reason: 简短说明
    """
    # ① 数据量检查
    if len(klines_15m) < 100:
        return 0, "kronos_skip:insufficient_data"

    # ② 环境检查
    if not _is_available():
        return 0, "kronos_skip:not_installed"

    # ③ 模型加载
    if not _load_model():
        return 0, "kronos_skip:model_load_failed"

    now = time.time()

    # ④ 缓存命中（同一标的共享推理结果）
    cache_key = symbol
    if cache_key in _CACHE:
        ts, p_up, volatility, _ = _CACHE[cache_key]
        if now - ts < _CACHE_TTL:
            # 缓存命中
            raw_score = _p_up_to_score(p_up, direction)
        else:
            p_up = None  # 缓存过期
    else:
        p_up = None

    # ⑤ 未命中缓存 → 推理
    if p_up is None:
        try:
            p_up, volatility = _run_inference(klines_15m, symbol)
            _CACHE[cache_key] = (now, p_up, volatility, "v1.0")
        except Exception as e:
            logger.warning(f"[Kronos] {symbol} 推理异常: {e}")
            return 0, f"kronos_error:{str(e)[:40]}"

    # ⑥ 原始分数
    raw_score = _p_up_to_score(p_up, direction)

    # ⑦ 波动率惩罚（高不确定性降权）
    if volatility > 0.025:
        raw_score = int(raw_score * 0.6)

    # ⑧ 体制系数（达摩院封印）
    coeff = REGIME_COEFF.get(regime, 1.0) if regime else 1.0
    final_score = int(raw_score * coeff)
    final_score = max(-12, min(12, final_score))

    reason = (
        f"p_up={p_up:.2f},"
        f"raw={raw_score:+d},"
        f"vol={volatility:.4f},"
        f"regime_coeff={coeff:.1f}"
    )

    logger.debug(f"[Kronos] {symbol} {direction}: {final_score:+d} | {reason}")
    return final_score, reason


# ── 调试入口 ──────────────────────────────────────────────────────
if __name__ == "__main__":
    """Phase 0 延迟测试"""
    import urllib.request
    import sys

    print("=== Kronos s23 Phase 0 延迟测试 ===\n")

    # 获取 BTC 15m 历史K线
    print("获取 BTC 15m K线数据...")
    url = "https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=15m&limit=250"
    raw = json.loads(urllib.request.urlopen(url, timeout=10).read())
    klines = [[float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5])] for c in raw]
    print(f"K线数量: {len(klines)}")

    # 首次推理（包含模型加载）
    print("\n模型加载中（首次，含下载）...")
    t0 = time.time()
    score, reason = get_kronos_score("BTCUSDT", "LONG", klines, "CHOP_MID")
    t1 = time.time()
    print(f"首次调用: {(t1-t0)*1000:.0f}ms | score={score:+d} | {reason}")

    # 缓存命中测试
    t0 = time.time()
    score2, reason2 = get_kronos_score("BTCUSDT", "SHORT", klines, "CHOP_MID")
    t1 = time.time()
    print(f"缓存命中: {(t1-t0)*1000:.0f}ms | score={score2:+d} | {reason2}")

    # 第二次实际推理（无缓存）
    _CACHE.clear()
    t0 = time.time()
    score3, reason3 = get_kronos_score("BTCUSDT", "LONG", klines, "BULL_EARLY")
    t1 = time.time()
    print(f"热推理:   {(t1-t0)*1000:.0f}ms | score={score3:+d} | {reason3}")

    print(f"\n{'✅ 延迟正常' if (t1-t0)*1000 < 500 else '⚠️ 延迟偏高，需优化'}")
