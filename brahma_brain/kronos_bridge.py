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
from __future__ import annotations
import os, sys, time, json, logging

STATUS = 'SHADOW'  # 对外导出状态标识（360评估用）
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
MODE          = os.environ.get('KRONOS_BRIDGE_MODE', 'shadow')
BLEND_WEIGHT  = 0.5      # blend模式下 Kronos 权重
PRED_LEN      = 12       # 预测未来12根K线
SAMPLE_COUNT  = 5        # 采样路径数（精度 vs 速度）
CACHE_TTL     = 900      # 15分钟缓存

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
        if now - ts < CACHE_TTL:
            return p_up, vol, 'cache'

    predictor = _get_predictor()
    if predictor is None:
        return 0.5, 0.0, 'fallback:no_model'

    # [设计院 Phase3-1 2026-07-06] LightGBM专用路径
    # 当predictor是lgbm_walkforward时，从klines计算特征而非传OHLCV DataFrame
    if getattr(predictor, 'model_type', '') == 'lgbm_walkforward':
        try:
            if klines and len(klines) >= 15:
                closes = [float(k[4]) for k in klines]
                vols   = [float(k[5]) for k in klines]
                highs  = [float(k[2]) for k in klines]
                lows   = [float(k[3]) for k in klines]
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

        # 写缓存
        _cache[symbol] = (now, p_up, volatility)

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
    if MODE == 'shadow':
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
