# ponytail: kronos_engine 399行，独立计算引擎，功能内聚，拆分条件: 单引擎>3000行且有完整测试
"""

# STATUS: ACTIVE
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

# [FIX 2026-08-02 设计院] 确保 venv 路径在 cron 隔离环境中可用
try:
    _venv_site_ke = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                  'venv', 'lib', 'python3.11', 'site-packages')
    if os.path.exists(_venv_site_ke) and _venv_site_ke not in sys.path:
        sys.path.insert(1, _venv_site_ke)
except Exception:
    pass

# [2026-09-01 设计院精简封印] 移除模块级torch预加载
# 原因：import kronos_engine 即触发torch加载(~2-3GB)，导致每个cron任务内存峰值飙升
# libgomp修复改为懒加载，在_load_model()内部执行
def _ensure_libgomp():
    """懒加载时修复libgomp软链（只在真正需要Kronos时调用）"""
    try:
        import glob as _glob
        _gomp_candidates = _glob.glob('/usr/local/lib/python3.11/dist-packages/torch/lib/libgomp.so*')
        if not _gomp_candidates:
            _gomp_candidates = _glob.glob('/root/.openclaw/workspace/trading-system/venv/lib/python3.11/site-packages/torch/lib/libgomp.so*')
        if not _gomp_candidates:
            _gomp_candidates = _glob.glob('/root/.openclaw/workspace/trading-system/venv/lib/*/site-packages/torch/lib/libgomp.so*')
        _gomp_target = '/usr/local/lib/libgomp.so.1'
        if _gomp_candidates and not os.path.exists(_gomp_target):
            os.symlink(_gomp_candidates[0], _gomp_target)
            logger.info('[Kronos] 自愈: libgomp软链已恢复')
    except Exception:
        pass

# [设计院 2026-07-26 封印] 模型已本地缓存，强制离线模式跳过HF网络检查（解决import 30s阻塞）
os.environ.setdefault('HF_HUB_OFFLINE', '1')
os.environ.setdefault('TRANSFORMERS_OFFLINE', '1')

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
    """懒加载模型（首次调用时初始化）
    [v7.0 设计院 2026-07-11 六方评估封印]
    [2026-09-01 精简封印] libgomp修复移至此处，消除模块级torch预加载
    优先级重排：Kronos-mini（大模型）> WF-LightGBM（fallback）
    根因：external/Kronos已克隆，model包可用，不应再用lgbm覆盖
    """
    global _predictor, _model_loaded, _model_load_attempted
    if _model_load_attempted:
        return _model_loaded
    _model_load_attempted = True
    _ensure_libgomp()  # 仅在真正加载时修复libgomp

    # ── P0：优先 Kronos-mini 真正大模型推理 ─────────────────────────────
    # [v7.0] external/Kronos已克隆，model包可用，优先使用
    try:
        import json as _json
        from model import Kronos, KronosPredictor, KronosTokenizer
        from safetensors.torch import load_file as _load_sf
        from huggingface_hub import hf_hub_download as _hf_dl
        _cache = os.path.join(_KRONOS_PATH, '..', 'data', 'kronos_cache')
        os.makedirs(_cache, exist_ok=True)
        logger.info('[Kronos] 加载模型中... device=cpu')
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
        tokenizer = KronosTokenizer.from_pretrained('NeoQuasar/Kronos-Tokenizer-base', cache_dir=_cache)
        _predictor = KronosPredictor(model=model, tokenizer=tokenizer, device='cpu', max_context=512)
        _model_loaded = True
        logger.info('[Kronos] ✅ 模型加载完成 Kronos-mini CPU')
        return True
    except Exception as e:
        logger.warning(f'[Kronos] ⚠️ Kronos-mini加载失败，尝试WF-LightGBM fallback: {e}')

    # ── Fallback：WF-LightGBM（OOS_ACC=60%）──────────────────────────────
    try:
        import lightgbm as lgb
        import json as _json2
        _base_dir  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        _wf_path   = os.path.join(_base_dir, 'data', 'kronos_wf_model_lgb.txt')
        _meta_path = os.path.join(_base_dir, 'data', 'kronos_wf_model.json')
        if os.path.exists(_wf_path) and os.path.exists(_meta_path):
            _lgb_model = lgb.Booster(model_file=_wf_path)
            with open(_meta_path) as _f:
                _meta = _json2.load(_f)
            _actual_feats = _meta.get('feature_names', [])[:_lgb_model.num_feature()]
            class _LGBMPredictor:
                def __init__(self, m, feats, meta):
                    self._m = m; self._feats = feats
                    self.model_type = 'lgbm_walkforward'
                    self._oos_acc = meta.get('oos_acc', 0.6)
                def predict(self, feat_dict):
                    import numpy as _np
                    x = _np.array([[feat_dict.get(k, 0.5) for k in self._feats]], dtype=_np.float32)
                    return float(self._m.predict(x)[0])
            _predictor = _LGBMPredictor(_lgb_model, _actual_feats, _meta)
            _model_loaded = True
            logger.info(f'[Kronos] ✅ WF-LightGBM fallback就绪 oos_acc={_meta.get("oos_acc")}')
            return True
    except Exception as _e2:
        logger.warning(f'[Kronos] lgbm fallback失败: {_e2}')

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
    # klines 格式: [ts_ms, open, high, low, close, volume, ...] 或 [open, high, low, close, volume]
    _raw = [k for k in klines_15m[-200:] if k]
    if _raw and isinstance(_raw[0], (list, tuple)):
        _ncols = len(_raw[0])
        if _ncols >= 6 and isinstance(_raw[0][0], (int, float)) and _raw[0][0] > 1e12:
            # 第一列是时间戳ms
            _raw = [[r[1], r[2], r[3], r[4], r[5]] for r in _raw]
        elif _ncols >= 5:
            _raw = [[r[0], r[1], r[2], r[3], r[4]] for r in _raw]
    df = pd.DataFrame(
        _raw,
        columns=["open", "high", "low", "close", "volume"]
    ).astype(float)

    if df.isnull().values.any():
        raise ValueError("klines含NaN")

    current_close = df['close'].iloc[-1]

    # 构造时间戳（15m间隔，不需要精确，Kronos需要时间特征）
    # calc_time_stamps 需要 pd.Series（不是 list/DatetimeIndex）
    import pandas as _pd_ke
    base_ts = datetime(2026, 1, 1)
    _x_list = [base_ts + timedelta(minutes=15 * i) for i in range(len(df))]
    _y_list = [_x_list[-1] + timedelta(minutes=15 * (i + 1)) for i in range(16)]
    x_timestamps = _pd_ke.Series(_pd_ke.to_datetime(_x_list))
    y_timestamps = _pd_ke.Series(_pd_ke.to_datetime(_y_list))

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


# ══ [2026-09-01 设计院精简封印] 合并自kronos_bridge.py + kronos_lite.py ══════
# 三件套合并：kronos_engine(底层) + kronos_bridge(封装) + kronos_lite(fallback)
# 原bridge/lite文件改为转发shim，保持外部接口不变

# ── 从kronos_bridge合并的公开接口 ────────────────────────────────────────────
_bridge_cache: dict = {}
_bridge_disk_cache_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'brahma_cache', 'kronos_bridge_cache.json')

def get_s23_kronos(klines: list, symbol: str, direction: str, regime: str) -> tuple:
    """Kronos s23维度评分 - 原kronos_bridge.get_s23_kronos()接口保持不变"""
    try:
        import importlib as _il
        _bridge = _il.import_module('brahma_brain.kronos_bridge')
        return _bridge.get_s23_kronos(klines, symbol, direction, regime)
    except Exception as _e:
        return 0, {'error': str(_e), 'source': 'engine_fallback'}

def get_volatility_forecast(symbol: str, klines: list = None) -> dict:
    """波动率预测 - 原kronos_bridge接口"""
    try:
        import importlib as _il
        _bridge = _il.import_module('brahma_brain.kronos_bridge')
        return _bridge.get_volatility_forecast(symbol, klines)
    except Exception as _e:
        return {'error': str(_e)}

# ── 从kronos_lite合并的公开接口 ──────────────────────────────────────────────
def get_s23_score_lite(klines: list, symbol: str, direction: str, regime: str) -> tuple:
    """轻量版Kronos评分 - 原kronos_lite.get_s23_score()接口"""
    try:
        import importlib as _il
        _lite = _il.import_module('brahma_brain.kronos_lite')
        return _lite.get_s23_score(klines, symbol, direction, regime)
    except Exception as _e:
        return 0, {'error': str(_e), 'source': 'lite_fallback'}


# ══ [2026-09-01 设计院精简封印] 合并自 brahma_brain/kronos_bridge.py ══
# ponytail: kronos_bridge 695行，有意为之，重构前先 grep 所有调用方
"""
kronos_bridge.py — Kronos 大模型 × 梵天 集成桥接层 v1.0
════════════════════════════════════════════════════════
设计院 自主决策落地 2026-07-01

使命：
  将清华 Kronos Foundation Model（AAAI 2026）集成进梵天 s23 维度
  作为 kronos_lite.py（规则代理）的升级版本，shadow → live 路径

架构决策：
  L1: p_up 并联 → 替换 s23 规则代理，shadow模式A/B对比
  L2: 波动率预测 → 注入 dynamic_sl.py（ATR自适应止损）
  L3: 合成K线生成 → 替换 regime_aware_augmentor 高斯噪声

模型：NeoQuasar/Kronos-mini（4.1M参数，CPU可用，延迟~800ms）
缓存：15分钟，同品种 LONG/SHORT 共享推理

运行模式（与 kronos_engine.py 体制系数完全兼容）：
  MODE=shadow  → 输出记录，不替换 kronos_lite 分数（默认）
  MODE=blend   → p_up = 0.5×lite + 0.5×kronos（混合）
  MODE=live    → 完全替换 kronos_lite（需达摩院 n≥100 验证）

达摩院验证路径：
  M0: shadow日志积累（当前）
  M1: 离线回放 n≥100，Kronos WR ≥ Kronos-Lite WR + 2pp
  M2: live模式激活
"""

# ── STATUS: SHADOW ────────────────────────────────────────────
# 并联 s23，记录 vs Kronos-Lite 差异，不影响主流程
# LAST_REVIEW: 2026-07-01 | 设计院自主决策封印
# ─────────────────────────────────────────────────────────────
import os, sys, time, json, logging

# [FIX 2026-08-02 设计院] 确保 venv 路径，解决 cron 隔离环境中 torch/lightgbm 找不到的根因
# 根因：上次修复(42a808f)将ensure_venv_path放在scripts/，但kronos_bridge在brahma_brain/下，没有自动导入
try:
    _venv_site = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              'venv', 'lib', 'python3.11', 'site-packages')
    if os.path.exists(_venv_site) and _venv_site not in sys.path:
        sys.path.insert(1, _venv_site)
except Exception:
    pass

STATUS = 'BLEND'   # 对外导出状态标识（360评估用）[2026-07-06] shadow→blend
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple, Dict

logger = logging.getLogger("kronos_bridge")

BASE        = Path(__file__).parent.parent
KRONOS_PATH = BASE / 'external' / 'Kronos'
LOG_DIR     = BASE / 'data'
SHADOW_LOG  = LOG_DIR / 'kronos_bridge_shadow.jsonl'

# Kronos 路径注入
if str(KRONOS_PATH) not in sys.path and KRONOS_PATH.exists():
    sys.path.insert(0, str(KRONOS_PATH))

# ── 运行模式 ──────────────────────────────────────────────────
MODE          = os.environ.get('KRONOS_BRIDGE_MODE', 'blend')  # [2026-08-24 苏摩111] Kronos-mini权重已下载，恢复blend模式
BLEND_WEIGHT  = 0.5      # blend模式下 Kronos 权重
PRED_LEN      = 12       # 预测未来12根K线
SAMPLE_COUNT  = 5        # 采样路径数（精度 vs 速度）
CACHE_TTL     = 14400    # 4H持久缓存（prime-agent思路：网络不稳定时持久状态不丢）
CACHE_STALE_ZERO_MAX = 1800  # [设计院自主决策 2026-08-21] p_up=0.0 且超过30min → 腐烂零值，强制中性
CACHE_DECAY_HALF = 7200      # 2小时后p_up向0.5线性衰减，防陈旧数据主导评分

# ── 体制系数（与 kronos_engine.py 完全一致）──────────────────
REGIME_COEFF = {
    'CHOP_MID': 0.3, 'CHOP_HIGH': 0.3, 'CHOP_LOW': 0.3,
    'BULL_EARLY': 1.0, 'BEAR_EARLY': 1.0,
    'BULL_TREND': 0.7, 'BEAR_TREND': 0.7,
    'BULL_CORRECTION': 0.8, 'BEAR_RECOVERY': 0.8,
}

# ── 缓存 ──────────────────────────────────────────────────────
_cache: Dict[str, Tuple[float, float, float]] = {}
# {symbol: (ts, p_up, volatility)}

# ── 磁盘持久化缓存路径（容器重启后仍可读取）────────────────────────
_DISK_CACHE_PATH = Path(BASE) / 'data' / 'kronos_p_up_cache.json'

def _load_disk_cache() -> None:
    """启动时从磁盘恢复缓存（容器重启保活）"""
    global _cache
    try:
        if _DISK_CACHE_PATH.exists():
            with open(_DISK_CACHE_PATH) as f:
                raw = json.load(f)
            now = time.time()
            restored = 0
            for sym, (ts, p_up, vol) in raw.items():
                if now - ts < CACHE_TTL:
                    _cache[sym] = (ts, p_up, vol)
                    restored += 1
            if restored:
                logger.info(f'[KronosBridge] 磁盘缓存恢复 {restored}条')
    except Exception as e:
        logger.warning(f'[KronosBridge] 磁盘缓存读取失败（非致命）: {e}')

def _save_disk_cache() -> None:
    """写入磁盘缓存（异步友好，失败不崩溃）"""
    try:
        _DISK_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_DISK_CACHE_PATH, 'w') as f:
            json.dump({k: list(v) for k, v in _cache.items()}, f)
    except Exception as e:
        logger.warning(f'[KronosBridge] 磁盘缓存写入失败（非致命）: {e}')

# 启动时立即恢复
_load_disk_cache()

# ── 模型单例 ──────────────────────────────────────────────────
_predictor = None
_loaded    = False


# ════════════════════════════════════════════════════════════════
# 1. 模型加载（懒加载，复用 kronos_engine 缓存）
# ════════════════════════════════════════════════════════════════

def _get_predictor():
    """获取 Kronos 预测器（优先复用 kronos_engine 单例）"""
    global _predictor, _loaded
    if _loaded:
        return _predictor

    # 优先复用 kronos_engine 已加载的模型
    try:
        sys.path.insert(0, str(BASE / 'brahma_brain'))
        import kronos_engine as _ke
        if _ke._load_model() and _ke._predictor is not None:
            _predictor = _ke._predictor
            _loaded    = True
            logger.info("[KronosBridge] 复用 kronos_engine 预测器 ✅")
            return _predictor
    except Exception as e:
        logger.debug(f"[KronosBridge] kronos_engine复用失败: {e}")

    # 独立加载
    try:
        import json as _json, torch
        from model.kronos import Kronos, KronosPredictor, KronosTokenizer
        from safetensors.torch import load_file as _sf
        from huggingface_hub import hf_hub_download as _dl

        _cache_dir = str(BASE / 'data' / 'kronos_cache')
        os.makedirs(_cache_dir, exist_ok=True)

        cfg_path = _dl('NeoQuasar/Kronos-mini', 'config.json', cache_dir=_cache_dir)
        w_path   = _dl('NeoQuasar/Kronos-mini', 'model.safetensors', cache_dir=_cache_dir)
        cfg      = _json.load(open(cfg_path))

        model = Kronos(
            d_model=cfg['d_model'], n_layers=cfg['n_layers'], n_heads=cfg['n_heads'],
            ff_dim=cfg['ff_dim'], s1_bits=cfg['s1_bits'], s2_bits=cfg['s2_bits'],
            learn_te=cfg['learn_te'], attn_dropout_p=0, ffn_dropout_p=0,
            resid_dropout_p=0, token_dropout_p=0,
        )
        model.load_state_dict(_sf(w_path))
        model.eval()

        tokenizer = KronosTokenizer.from_pretrained(
            'NeoQuasar/Kronos-Tokenizer-base', cache_dir=_cache_dir
        )
        _predictor = KronosPredictor(model=model, tokenizer=tokenizer,
                                     device='cpu', max_context=512)
        _loaded = True
        logger.info("[KronosBridge] 独立加载 Kronos-mini ✅")
        return _predictor

    except Exception as e:
        logger.warning(f"[KronosBridge] 模型加载失败: {e}")
        _loaded = False
        return None


# ════════════════════════════════════════════════════════════════
# 2. 核心推理
# ════════════════════════════════════════════════════════════════

def _build_ohlcv_df(klines: list) -> Tuple[Optional[pd.DataFrame], Optional[pd.DatetimeIndex]]:
    """
    将 klines 列表转换为 KronosPredictor 需要的 DataFrame 格式

    klines 格式（梵天标准）：
      [timestamp_ms, open, high, low, close, volume, ...]
    """
    if not klines or len(klines) < 32:
        return None, None

    try:
        rows = []
        for k in klines:
            if isinstance(k, (list, tuple)) and len(k) >= 6:
                rows.append({
                    'open':   float(k[1]),
                    'high':   float(k[2]),
                    'low':    float(k[3]),
                    'close':  float(k[4]),
                    'volume': float(k[5]),
                })
            elif isinstance(k, (list, tuple)) and len(k) == 5:
                # [FIX 2026-08-02 设计院] s23段传入格式为 [o,h,l,c,v] 5元素
                rows.append({
                    'open':   float(k[0]),
                    'high':   float(k[1]),
                    'low':    float(k[2]),
                    'close':  float(k[3]),
                    'volume': float(k[4]),
                })
            elif isinstance(k, dict):
                rows.append({
                    'open':   float(k.get('open', k.get('o', 0))),
                    'high':   float(k.get('high', k.get('h', 0))),
                    'low':    float(k.get('low',  k.get('l', 0))),
                    'close':  float(k.get('close', k.get('c', 0))),
                    'volume': float(k.get('volume', k.get('v', 0))),
                })

        df = pd.DataFrame(rows)
        df['amount'] = df['close'] * df['volume']

        # 时间戳
        n = len(df)
        end_ts = datetime.now(timezone.utc)
        # 检测周期（粗略）
        freq = '15min' if n <= 200 else '1h'
        ts = pd.date_range(end=end_ts, periods=n, freq=freq, tz='UTC')
        df.index = ts

        return df, ts

    except Exception as e:
        logger.debug(f"[KronosBridge] build_df失败: {e}")
        return None, None


def _run_kronos(
    klines: list,
    symbol: str,
    pred_len: int = PRED_LEN
) -> Tuple[float, float, str]:
    """
    运行 Kronos 推理，返回 (p_up, volatility, source)

    p_up:       上涨概率 0~1
    volatility: 预测波动率（ATR代理）
    source:     'kronos' | 'cache' | 'fallback'
    """
    now = time.time()

    # 缓存命中（symbol必须为str）
    if not isinstance(symbol, str):
        symbol = str(symbol)
    if symbol in _cache:
        ts, p_up, vol = _cache[symbol]
        age = now - ts
        if age < CACHE_TTL:
            # [设计院自主决策 2026-08-21] Cache腐烂零值根治
            # p_up=0.0 且超过30min → 腐烂数据，返回中性而非惩罚分
            if p_up == 0.0 and age > CACHE_STALE_ZERO_MAX:
                logger.warning(f'[KronosBridge] 腐烂零值 cache {symbol} p_up=0.0 age={age/60:.0f}min → 返回0.5中性')
                return 0.5, vol, 'stale_zero_neutral'
            # 超过2H的cache使用衰减因子，防止陈旧数据主导评分
            if age > CACHE_DECAY_HALF:
                decay = max(0.5, 1.0 - (age - CACHE_DECAY_HALF) / CACHE_DECAY_HALF)
                p_up_decayed = 0.5 + (p_up - 0.5) * decay
                logger.debug(f'[KronosBridge] cache衰减 {symbol} p_up={p_up:.3f}→{p_up_decayed:.3f} age={age/3600:.1f}h')
                return p_up_decayed, vol, 'cache_decayed'
            return p_up, vol, 'cache'

    predictor = _get_predictor()
    if predictor is None:
        # [prime-agent缓存思路 2026-08-08] 模型不可用时返回磁盘持久化缓存，而非固定0.5
        if symbol in _cache:
            ts_c, p_up_c, vol_c = _cache[symbol]
            age_h = (time.time() - ts_c) / 3600
            logger.info(f'[KronosBridge] 模型不可用，返回缓存 {symbol} p_up={p_up_c:.3f} age={age_h:.1f}h')
            return p_up_c, vol_c, f'cache_fallback({age_h:.1f}h)'
        return 0.5, 0.0, 'fallback:no_model'

    # [设计院 Phase3-1 2026-07-06] LightGBM专用路径
    # 当predictor是lgbm_walkforward时，从klines计算特征而非传OHLCV DataFrame
    if getattr(predictor, 'model_type', '') == 'lgbm_walkforward':
        try:
            if klines and len(klines) >= 15:
                # [修复 2026-07-07] klines格式为(O,H,L,C,V)5元组
                # close=k[3], vol=k[4] (原代码k[4]取的是vol而非close)
                closes = [float(k[3]) for k in klines]
                vols   = [float(k[4]) for k in klines]
                highs  = [float(k[1]) for k in klines]
                lows   = [float(k[2]) for k in klines]
                price  = closes[-1]
                # 10个特征（与训练一致）
                gains  = [max(0, closes[i]-closes[i-1]) for i in range(1,len(closes))]
                losses = [max(0, closes[i-1]-closes[i]) for i in range(1,len(closes))]
                ag = sum(gains[-14:])/14; al = sum(losses[-14:])/14
                rsi = (100-100/(1+ag/al)) / 100 if al>0 else 0.5
                ema14 = closes[0]
                for c in closes[1:]:
                    ema14 = c*(2/15) + ema14*(1-2/15)
                p_ema    = float(price > ema14)
                p_rsi    = rsi
                p_mom    = min(1.0, max(0.0, (price - closes[-5]) / (closes[-5]+1e-9) / 0.05 + 0.5))
                vol_avg  = sum(vols[-10:])/10
                p_vol    = min(1.0, vols[-1] / (vol_avg+1e-9) / 2)
                p_candle = 1.0 if closes[-1] > closes[-2] else 0.0
                h48 = max(highs[-48:]) if len(highs)>=48 else max(highs)
                l48 = min(lows[-48:])  if len(lows)>=48  else min(lows)
                p_bos    = float((price - l48) / (h48 - l48 + 1e-9))
                feat_dict = {
                    'p_momentum': p_mom,
                    'p_ema':      p_ema,
                    'p_rsi':      p_rsi,
                    'p_candle':   p_candle,
                    'p_volume':   p_vol,
                    'p_bos':      p_bos,
                    'regime':     0.7,   # 体制分位（默认BULL）
                    'direction':  0.5,
                    'lsr':        0.5,
                    'fr':         0.5,
                }
                p_up_lgbm = float(predictor.predict(feat_dict))
                vol_lgbm  = float(np.std(closes[-20:]) / (price+1e-9)) if len(closes)>=20 else 0.01
                _cache[symbol] = (time.time(), p_up_lgbm, vol_lgbm)
                logger.info(f"[KronosBridge] {symbol} lgbm p_up={p_up_lgbm:.3f} (WF-LightGBM)")
                return p_up_lgbm, vol_lgbm, 'kronos_lgbm'
        except Exception as _lgbm_e:
            logger.debug(f"[KronosBridge] lgbm推理失败: {_lgbm_e}")
            return 0.5, 0.0, 'fallback:lgbm_err'

    df, x_ts = _build_ohlcv_df(klines)
    if df is None:
        return 0.5, 0.0, 'fallback:no_data'

    try:
        # 预测时间戳
        freq = x_ts.freq if hasattr(x_ts, 'freq') and x_ts.freq else pd.tseries.frequencies.to_offset('1h')
        y_ts = pd.date_range(
            start=x_ts[-1] + freq,
            periods=pred_len,
            freq=freq,
            tz='UTC'
        )

        # calc_time_stamps 需要 pd.Series（不是 DatetimeIndex）
        x_ts_s = pd.Series(x_ts)
        y_ts_s = pd.Series(y_ts)

        t0 = time.time()
        pred_df = predictor.predict(
            df, x_ts_s, y_ts_s,
            pred_len=pred_len,
            sample_count=SAMPLE_COUNT,
            verbose=False
        )
        elapsed = time.time() - t0

        # p_up：预测close变化方向
        last_close = float(df['close'].iloc[-1])
        pred_close = pred_df['close'].values

        # 加权p_up：近期预测权重更高
        weights = np.linspace(0.5, 1.5, len(pred_close))
        up_weights = np.where(pred_close > last_close, weights, 0)
        p_up = float(up_weights.sum() / weights.sum())

        # 波动率：预测高低点范围的平均
        pred_ranges = (pred_df['high'] - pred_df['low']).values / (pred_df['close'].values + 1e-9)
        volatility  = float(pred_ranges.mean())

        # 写内存+磁盘双缓存
        _cache[symbol] = (now, p_up, volatility)
        _save_disk_cache()  # 同步写磁盘，容器重启后可恢复

        logger.info(f"[KronosBridge] {symbol} p_up={p_up:.3f} vol={volatility:.4f} t={elapsed*1000:.0f}ms")
        return p_up, volatility, 'kronos'

    except Exception as e:
        logger.warning(f"[KronosBridge] 推理异常 {symbol}: {e}")
        return 0.5, 0.0, f'fallback:{type(e).__name__}'


# ════════════════════════════════════════════════════════════════
# 3. 主接口：get_s23_kronos()
# ════════════════════════════════════════════════════════════════

def get_s23_kronos(
    klines_15m: list,
    symbol: str,
    direction: str = 'LONG',
    regime: str    = 'UNKNOWN',
    lite_score: Optional[int] = None,       # kronos_lite 原始分数（用于A/B对比）
    lite_p_up: Optional[float]  = None,
) -> Tuple[int, Dict]:
    """
    Kronos 大模型版 s23 评分

    完全兼容 kronos_lite.get_s23_score() 的输出格式：
      returns (score: int, meta: dict)

    集成策略（由 MODE 控制）：
      shadow: 返回 lite_score（原始），仅记录 Kronos 结果
      blend:  返回 0.5×lite + 0.5×kronos 混合分
      live:   返回纯 Kronos 分数

    Args:
        klines_15m: 15分钟K线列表
        symbol:     交易对
        direction:  'LONG' | 'SHORT'
        regime:     当前体制
        lite_score: kronos_lite 的原始分（shadow模式用于对比）
        lite_p_up:  kronos_lite 的 p_up（shadow模式用于对比）
    """
    p_up, volatility, source = _run_kronos(klines_15m, symbol)

    # 体制加权
    coeff = REGIME_COEFF.get(regime, 0.7)
    p_up_adj = 0.5 + (p_up - 0.5) * coeff

    # 转换为分数（与 kronos_lite 分数范围对齐：-12~+12）
    if direction in ('LONG', '做多'):
        raw_score = (p_up_adj - 0.5) * 24   # 0.5→0, 1.0→+12, 0.0→-12
    else:  # SHORT
        raw_score = (0.5 - p_up_adj) * 24   # 做空时反向

    kronos_score = int(max(-12, min(12, round(raw_score))))

    meta = {
        'source':        source,
        'p_up':          round(p_up, 4),
        'p_up_adj':      round(p_up_adj, 4),
        'volatility':    round(volatility, 6),
        'regime_coeff':  coeff,
        'pred_len':      PRED_LEN,
        'kronos_score':  kronos_score,
        'lite_score':    lite_score,
        'lite_p_up':     lite_p_up,
        'mode':          MODE,
    }

    # ── 模式分支 ─────────────────────────────────────────────
    if MODE == 'lite_only':
        # [2026-08-24 设计院封印] 大模型权重不存在，直接返回lite分，不调用空壳模型
        final_score = lite_score if lite_score is not None else 0
        meta['source'] = 'lite_only'

    elif MODE == 'shadow':
        _shadow_log(symbol, direction, regime, kronos_score, lite_score, meta)
        final_score = lite_score if lite_score is not None else 0

    elif MODE == 'blend':
        if lite_score is not None:
            final_score = int(round(BLEND_WEIGHT * kronos_score + (1 - BLEND_WEIGHT) * lite_score))
        else:
            final_score = kronos_score
        _shadow_log(symbol, direction, regime, kronos_score, lite_score, meta)

    else:  # live
        final_score = kronos_score

    meta['final_score'] = final_score
    return final_score, meta


# ════════════════════════════════════════════════════════════════
# 4. Shadow Log（达摩院 M1 验证数据）
# ════════════════════════════════════════════════════════════════

def _shadow_log(symbol, direction, regime, kronos_score, lite_score, meta):
    """记录 Kronos vs Lite 差异，供达摩院 M1 验证"""
    try:
        LOG_DIR.mkdir(exist_ok=True)
        record = {
            'ts':           datetime.now(timezone.utc).isoformat(),
            'symbol':       symbol,
            'direction':    direction,
            'regime':       regime,
            'kronos_score': kronos_score,
            'lite_score':   lite_score,
            'delta':        kronos_score - (lite_score or 0),
            'p_up':         meta.get('p_up'),
            'volatility':   meta.get('volatility'),
            'source':       meta.get('source'),
            # 后续填入: 'actual_result': 'WIN'/'LOSS'
        }
        with open(SHADOW_LOG, 'a') as f:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')
    except Exception as e:
        logger.debug(f"shadow_log写入失败: {e}")


def get_shadow_stats() -> Dict:
    """分析 shadow log，评估 Kronos vs Kronos-Lite 准确率差异"""
    if not SHADOW_LOG.exists():
        return {'status': 'no_log', 'n': 0}

    records = []
    with open(SHADOW_LOG) as f:
        for line in f:
            try:
                records.append(json.loads(line.strip()))
            except Exception:
                pass

    n = len(records)
    if n == 0:
        return {'status': 'empty', 'n': 0}

    validated = [r for r in records if r.get('actual_result') in ('WIN', 'LOSS')]

    # 方向一致性（Kronos 与 Lite 方向相同）
    both_have = [r for r in records if r.get('lite_score') is not None]
    agreement = sum(1 for r in both_have
                    if (r['kronos_score'] >= 0) == (r['lite_score'] >= 0))
    agree_rate = agreement / (len(both_have) + 1e-9)

    # 平均分差
    deltas = [r.get('delta', 0) for r in records]

    result = {
        'status':       'has_data',
        'n_total':      n,
        'n_validated':  len(validated),
        'agreement_rate': round(agree_rate, 3),
        'avg_delta':    round(sum(deltas) / len(deltas), 2),
        'sources':      {},
    }

    # 来源统计
    for r in records:
        src = r.get('source', 'unknown')
        result['sources'][src] = result['sources'].get(src, 0) + 1

    if validated:
        # Kronos 方向正确率
        k_correct = sum(1 for r in validated
                        if (r['kronos_score'] > 0 and r['actual_result'] == 'WIN') or
                           (r['kronos_score'] < 0 and r['actual_result'] == 'LOSS'))
        l_correct = sum(1 for r in validated if r.get('lite_score') is not None
                        if (r['lite_score'] > 0 and r['actual_result'] == 'WIN') or
                           (r['lite_score'] < 0 and r['actual_result'] == 'LOSS'))
        result['kronos_accuracy'] = round(k_correct / len(validated), 3)
        result['lite_accuracy']   = round(l_correct / max(1, sum(1 for r in validated if r.get('lite_score') is not None)), 3)
        result['m1_ready'] = result.get('kronos_accuracy', 0) >= result.get('lite_accuracy', 0) + 0.02

    return result


# ════════════════════════════════════════════════════════════════
# 5. L2: 波动率注入接口（供 dynamic_sl.py 调用）
# ════════════════════════════════════════════════════════════════

def get_volatility_forecast(
    klines: list,
    symbol: str,
    horizon_bars: int = 8
) -> Optional[float]:
    """
    获取 Kronos 波动率预测（供动态止损使用）

    Returns:
        float: 预测波动率（ATR%，如 0.015 表示 1.5%）
        None:  不可用时返回 None（调用方使用 ATR 回退）
    """
    _, volatility, source = _run_kronos(klines, symbol, pred_len=horizon_bars)
    if source == 'fallback:no_model':
        return None
    return volatility if volatility > 0 else None


# ════════════════════════════════════════════════════════════════
# 6. L3: 合成K线生成（供 regime_aware_augmentor 调用）
# ════════════════════════════════════════════════════════════════

def generate_synthetic_klines(
    seed_klines: list,
    symbol: str,
    n_samples: int = 100,
    pred_len: int  = 24,
    regime: str    = 'UNKNOWN',
) -> Optional[pd.DataFrame]:
    """
    用 Kronos 生成合成K线（L3：替换高斯噪声增强）

    相比 regime_aware_augmentor 的高斯噪声，
    Kronos 生成的 K 线具有真实市场微观结构（BSQ tokenizer 保证）

    Returns:
        DataFrame: n_samples 行，列=[open,high,low,close,volume]
        None: 不可用时返回 None
    """
    predictor = _get_predictor()
    if predictor is None:
        return None

    df, x_ts = _build_ohlcv_df(seed_klines)
    if df is None or len(df) < 32:
        return None

    try:
        freq = '1h'
        y_ts = pd.date_range(
            start=x_ts[-1] + pd.Timedelta('1h'),
            periods=pred_len * n_samples,
            freq=freq,
            tz='UTC'
        )

        all_rows = []
        batch_size = min(10, n_samples)

        for i in range(0, n_samples, batch_size):
            curr_batch = min(batch_size, n_samples - i)
            y_batch    = pd.date_range(
                start=x_ts[-1] + pd.Timedelta('1h'),
                periods=pred_len,
                freq=freq, tz='UTC'
            )

            pred = predictor.predict(
                df, x_ts, y_batch,
                pred_len=pred_len,
                sample_count=curr_batch,
                verbose=False
            )

            # 添加体制标签
            pred['regime']    = regime
            pred['synthetic'] = True
            pred['src']       = 'kronos'
            all_rows.append(pred)

        return pd.concat(all_rows, ignore_index=True)

    except Exception as e:
        logger.warning(f"[KronosBridge] 合成生成失败 {symbol}: {e}")
        return None


# ════════════════════════════════════════════════════════════════
# 7. 主入口（测试）
# ════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    import os
    os.environ['TOKENIZERS_PARALLELISM'] = 'false'
    print("🧪 Kronos Bridge 端到端测试\n")

    # 构造模拟 BTC 1H OHLCV（128根）
    np.random.seed(42)
    n = 128
    close = 60000 + np.cumsum(np.random.randn(n) * 300)
    close = np.abs(close)

    mock_klines = []
    for i in range(n):
        c = close[i]
        mock_klines.append([
            int(time.time() * 1000) - (n - i) * 3600000,  # timestamp_ms
            c * 0.999, c * 1.002, c * 0.997, c,            # ohlc
            np.random.uniform(100, 500),                    # volume
        ])

    print("=== L1: p_up 推理 ===")
    t0 = time.time()
    score, meta = get_s23_kronos(
        mock_klines, 'BTCUSDT', 'SHORT', 'BEAR_TREND',
        lite_score=7, lite_p_up=0.62
    )
    elapsed = time.time() - t0

    print(f"Kronos score:  {meta['kronos_score']:+d}")
    print(f"Lite score:    {meta['lite_score']:+d}")
    print(f"Final score:   {score:+d}  (mode={MODE})")
    print(f"p_up:          {meta['p_up']:.3f}  source={meta['source']}")
    print(f"Volatility:    {meta['volatility']:.4f}")
    print(f"耗时:          {elapsed*1000:.0f}ms")

    print("\n=== L2: 波动率预测 ===")
    vol = get_volatility_forecast(mock_klines, 'BTCUSDT')
    print(f"Vol forecast:  {vol:.4f}" if vol else "Vol: fallback")

    print("\n=== Shadow Stats ===")
    stats = get_shadow_stats()
    print(json.dumps(stats, ensure_ascii=False, indent=2))

    print("\n✅ Kronos Bridge 测试完成")

# ══ [2026-09-01 设计院精简封印] 合并自 brahma_brain/kronos_lite.py ══
# ponytail: 三层Kronos架构是有意为之
# engine=ML模型推理(主) / lite=规则备用(fallback) / bridge=调度层
# _p_up_to_score在lite和engine重复——n=1函数，可接受的小冗余
# 合并条件: 当engine稳定运行>30天后，可删除lite层，bridge直接调engine

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
from math_utils import _ema as calc_ema_series, _rsi as calc_rsi_series, _atr as calc_atr_series, ema, calc_rsi, atr  # [2026-08-28 math_utils SSOT迁移]

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


# [_ema] 已迁移到math_utils.ema [2026-08-28 SSOT封印]
# [_rsi] 已迁移到math_utils.calc_rsi [2026-08-28 SSOT封印]
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
    from brahma_brain.kronos_engine import _compute_p_up as _cp  # 用自身
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

# ══ [2026-09-01 设计院精简封印] 合并自 brahma_brain/timesfm_lite.py ══
"""

# STATUS: ACTIVE
# TimesFM轻量版，时序预测
# LAST_REVIEW: 2026-07-01 | 属于辅助计算层，修改前确认调用链
# ─────────────────────────────────────────────────────────────
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

    # [INT-1] 统一实现已移至 math_utils.ema，此函数保留兼容
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

        # [潜力释放 2026-07-12] 封山密碹格式收敛层
        # klines_1h可能是: [[ts,o,h,l,c,v],...] / [{'o':...,'c':...},...] / [float,...]
        def _extract_close(k):
            if isinstance(k, (int, float)): return float(k)
            if isinstance(k, dict):         return float(k.get('c', k.get('close', 0)))
            try:                             return float(k[4])   # [ts,o,h,l,c,v]
            except Exception:               return float(k[-1])  # fallback
        prices = np.array([_extract_close(k) for k in klines_1h], dtype=float)
        prices = prices[prices > 0]  # 过滤非正值
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