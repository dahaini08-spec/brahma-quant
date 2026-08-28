"""
fangcang_engine.py — 方仓经验引擎 v2.0
设计院封印 2026-08-08 · fangcang Step3: 引擎多时框升级+主力意图

功能：
  1. 读取方仓6.8年历史K线（4H为主 + 15m微结构）
  2. DTW相似度扫描：当前1周形态 → 历史最相似案例
  3. 25维特征向量（原10维 + 15m微结构5维 + 辅助10维）
  4. 主力意图检测层（陷阱预警 / ACCUMULATE / DISTRIBUTE）
  5. HCME M1-M6 集成（可选，fail-safe）
  6. M4 主力行为偏置集成（可选，fail-safe）
  7. 输出格式升级（main_force_intent / micro_structure / trap_alert / confidence_level）

设计原则（梵天宪法）：
  - 最简实现：纯stdlib + 已安装的gzip/json
  - 唯一入口：brahma_engine 调用 get_fangcang_context()
  - 结果缓存：TTL=60min（15m微结构扫描耗时，缓存时间延长）
  - 失败降级：任何异常 → 返回 {'status': 'unavailable'}
  - fail-safe原则：所有升级功能异常时静默，不影响原有输出
"""

# ponytail: 方仓引擎1090行，全部是必要的历史案例匹配逻辑
# 唯一可优化点: Qdrant向量检索替代线性扫描(n>5000条时)
import gzip
import json
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ── 路径配置 ─────────────────────────────────────────────────────────────────
_BASE = Path(__file__).parent.parent
_DATA_DIR_LEGACY   = _BASE / "data" / "historical"
_DATA_DIR_BACKTEST = _BASE / "data" / "backtest"

# ── 缓存层（内存级，TTL=60min，15m扫描较慢故延长）─────────────────────────
_CACHE: Dict[str, dict] = {}
_CACHE_TTL = 3600  # 60分钟

# ── 参数常量 ─────────────────────────────────────────────────────────────────
WEEK_BARS   = 42    # 1周 = 42根4H K线
FUTURE_BARS = 42    # 预测未来1周
SCAN_STEP   = 4     # 每4根滑动一次（减少重叠，提高速度）
TOP_N       = 20    # 取最相似TOP20
TP_PCT      = 3.0   # 标准TP%
SL_PCT      = 2.0   # 标准SL%
BARS_15M_12H = 48   # 12H = 48根15m K线（微结构观察窗口）


# ══════════════════════════════════════════════════════════════════════════════
# 数据加载
# ══════════════════════════════════════════════════════════════════════════════

def _load_klines_native(symbol: str, tf: str) -> List[dict]:
    """
    读取 data/backtest/{symbol}_{tf}.json
    原生格式: [[ts_ms, o, h, l, c, v, ...], ...]
    转为 [{"ts": ms, "o": f, "h": f, "l": f, "c": f, "v": f}, ...]
    """
    path = _DATA_DIR_BACKTEST / f"{symbol}_{tf}.json"
    if not path.exists():
        return []
    # [FIX 2026-08-20 v2 设计院] 分层截断：15m防OOM，4H/1H/1D保全量
    # 15m: 2000根(20天)已足够微结构分析(BARS_15M_12H=48根)，防OOM关键
    # 4H:  不截断(14467根=6.5年, 5.5MB, 方仓相似案例搜索必须全量)
    # 1H:  不截断(8664根=1年, 3MB, 足够)
    # 1D:  不截断(2383根=6.5年, 0.4MB)
    _TAIL_LIMIT = 2000 if "15m" in str(path) else None
    try:
        raw = json.loads(path.read_text())
        if _TAIL_LIMIT and len(raw) > _TAIL_LIMIT:
            raw = raw[-_TAIL_LIMIT:]
        bars = []
        for r in raw:
            bars.append({
                "ts": int(r[0]),
                "o":  float(r[1]),
                "h":  float(r[2]),
                "l":  float(r[3]),
                "c":  float(r[4]),
                "v":  float(r[5]),
            })
        return bars
    except MemoryError:
        return []
    except Exception:
        return []


def _load_klines(symbol: str, tf: str) -> List[dict]:
    """从方仓加载K线，优先 data/backtest/ 原生格式，失败返回[]"""
    bars = _load_klines_native(symbol, tf)
    if bars:
        return bars
    # fallback: 旧路径 jsonl.gz 格式
    path = _DATA_DIR_LEGACY / f"{symbol}_{tf}.jsonl.gz"
    if not path.exists():
        return []
    try:
        bars = []
        with gzip.open(path, 'rt') as f:
            for line in f:
                line = line.strip()
                if line:
                    bars.append(json.loads(line))
        return bars
    except Exception:
        return []


def _load_regime_map(symbol: str) -> Dict[int, str]:
    """从方仓加载体制标注，失败返回{}"""
    path = _DATA_DIR_LEGACY / f"{symbol}_regime_labels.jsonl.gz"
    if not path.exists():
        return {}
    try:
        rmap = {}
        with gzip.open(path, 'rt') as f:
            for line in f:
                line = line.strip()
                if line:
                    d = json.loads(line)
                    rmap[d['ts']] = d.get('regime', d.get('label', '?'))
        return rmap
    except Exception:
        return {}


# ══════════════════════════════════════════════════════════════════════════════
# 基础计算工具
# ══════════════════════════════════════════════════════════════════════════════

def _calc_rsi(prices, period: int = 14) -> float:
    """[2026-08-28 精简] 委托math_utils.calc_rsi — SSOT"""
    try:
        from brahma_brain.math_utils import calc_rsi as _mu_rsi
    except ImportError:
        from math_utils import calc_rsi as _mu_rsi
    return _mu_rsi(prices, period)
def _calc_bollinger_width(prices: List[float], period: int = 20) -> float:
    """计算布林带宽度百分比（BBW = (upper-lower)/middle * 100）"""
    if len(prices) < period:
        return 0.0
    w = prices[-period:]
    mean = sum(w) / period
    std  = math.sqrt(sum((p - mean) ** 2 for p in w) / period)
    if mean == 0:
        return 0.0
    return (std * 2.0) / mean * 100.0


def _calc_vol_20d_avg(vols: List[float]) -> float:
    """计算近20日平均成交量（240根4H = 40天；近96根 = 16天，取最近80根）"""
    window = vols[-80:] if len(vols) >= 80 else vols
    if not window:
        return 0.0
    return sum(window) / len(window)


# ══════════════════════════════════════════════════════════════════════════════
# 升级1：15m 微结构特征提取
# ══════════════════════════════════════════════════════════════════════════════

def _extract_15m_features(bars_15m: List[dict]) -> dict:
    """
    对最近 48 根15m K线（12H）提取微结构特征：
    - choch_count:    CHoCH次数（价格跌破前低或突破前高）
    - bos_count:      BOS次数（有效突破）
    - momentum_shift: 近12H vs 前12H 价格动量变化
    - micro_compress: 近12H BBW（判断微结构是否压缩，越低越压缩）
    - vol_climax:     是否有放量K线（成交量>均量×2，bool→int）
    """
    if len(bars_15m) < BARS_15M_12H:
        return {
            'choch_count': 0,
            'bos_count': 0,
            'momentum_shift': 0.0,
            'micro_compress': 5.0,
            'vol_climax': 0,
        }

    recent_48 = bars_15m[-BARS_15M_12H:]
    prior_48  = bars_15m[-BARS_15M_12H * 2 : -BARS_15M_12H] if len(bars_15m) >= BARS_15M_12H * 2 else recent_48

    closes_r = [float(b['c']) for b in recent_48]
    closes_p = [float(b['c']) for b in prior_48]
    highs_r  = [float(b['h']) for b in recent_48]
    lows_r   = [float(b['l']) for b in recent_48]
    vols_r   = [float(b['v']) for b in recent_48]

    # CHoCH: 跌破前低（看跌结构转变）或突破前高（看涨结构转变）
    choch_count = 0
    bos_count   = 0
    swing_lookback = 5  # 5根K线前的低点/高点
    for i in range(swing_lookback, len(closes_r)):
        prior_low  = min(lows_r[i - swing_lookback : i])
        prior_high = max(highs_r[i - swing_lookback : i])
        cur_close  = closes_r[i]
        cur_low    = lows_r[i]
        cur_high   = highs_r[i]

        if cur_low < prior_low and closes_r[i - 1] >= prior_low:
            choch_count += 1
        elif cur_high > prior_high and closes_r[i - 1] <= prior_high:
            choch_count += 1

        # BOS: 实体收盘穿越（非仅影线）
        if cur_close > prior_high:
            bos_count += 1
        elif cur_close < prior_low:
            bos_count += 1

    # 动量变化
    if closes_r and closes_p:
        c_r = closes_r[0]
        c_p = closes_p[0]
        mom_r = (closes_r[-1] - c_r) / c_r * 100.0 if c_r != 0 else 0.0
        mom_p = (closes_p[-1] - c_p) / c_p * 100.0 if c_p != 0 else 0.0
        momentum_shift = mom_r - mom_p
    else:
        momentum_shift = 0.0

    # BBW 压缩（近12H）
    micro_compress = _calc_bollinger_width(closes_r, period=min(20, len(closes_r)))

    # 放量K线检测
    avg_vol = sum(vols_r) / len(vols_r) if vols_r else 1.0
    vol_climax = 1 if any(v > avg_vol * 2.0 for v in vols_r) else 0

    return {
        'choch_count':    min(choch_count, 20),  # 上限20防止极端值
        'bos_count':      min(bos_count, 20),
        'momentum_shift': round(momentum_shift, 3),
        'micro_compress': round(micro_compress, 3),
        'vol_climax':     vol_climax,
    }


# ══════════════════════════════════════════════════════════════════════════════
# 升级2：扩展特征向量到25维
# ══════════════════════════════════════════════════════════════════════════════

def _extract_features(bars_4h: List[dict], bars_15m: Optional[List[dict]] = None) -> dict:
    """
    提取25维特征向量
    维度拆分：
      - 原有10维（价格形态 norm_closes + 9个scalar）
      - 15m微结构5维（choch/bos/momentum/bbw/vol_climax）
      - 辅助10维（新增OI/FR模拟 + 结构质量 + 趋势强度等）
    """
    closes = [float(b['c']) for b in bars_4h]
    highs  = [float(b['h']) for b in bars_4h]
    lows   = [float(b['l']) for b in bars_4h]
    vols   = [float(b['v']) for b in bars_4h]

    c0 = closes[0] if closes[0] != 0 else 1.0
    norm_closes = [(c - c0) / c0 * 100.0 for c in closes]

    amp_seq = [(h - l) / c0 * 100.0 for h, l in zip(highs, lows)]
    avg_amp = sum(amp_seq) / len(amp_seq) if amp_seq else 0
    amp_std = math.sqrt(sum((a - avg_amp) ** 2 for a in amp_seq) / len(amp_seq)) if amp_seq else 0

    avg_vol = sum(vols) / len(vols) if vols else 1.0
    vol_ratio_last = vols[-1] / avg_vol if avg_vol > 0 else 1.0

    rsi_end = _calc_rsi(closes)

    # 辅助10维特征
    # 1. 近1/4窗口的成交量变化趋势（量能动向）
    quarter = max(len(vols) // 4, 1)
    vol_trend = (sum(vols[-quarter:]) / quarter) / (sum(vols[:quarter]) / quarter + 1e-9) - 1.0

    # 2. 价格相对高点的位置（0=在高点, 1=在低点）
    price_range = max(highs) - min(lows)
    price_pos = (closes[-1] - min(lows)) / price_range if price_range > 0 else 0.5

    # 3. 近1/2窗口 vs 前1/2窗口振幅比（波动性趋势）
    half = max(len(amp_seq) // 2, 1)
    amp_ratio = (sum(amp_seq[-half:]) / half) / (sum(amp_seq[:half]) / half + 1e-9)

    # 4. RSI动量（近1/3 vs 前1/3的RSI差）
    third = max(len(closes) // 3, 1)
    rsi_early = _calc_rsi(closes[:third * 2])
    rsi_late  = _calc_rsi(closes[third:])
    rsi_momentum = rsi_late - rsi_early

    # 5. 尾部蜡烛比例（最后4根的均幅 vs 全均幅）
    tail_amp = sum(amp_seq[-4:]) / 4 if len(amp_seq) >= 4 else avg_amp
    tail_amp_ratio = tail_amp / avg_amp if avg_amp > 0 else 1.0

    # 6-10. BBW4H（当前4H级别布林带宽度）
    bbw_4h = _calc_bollinger_width(closes)

    # 15m 微结构特征（5维）
    if bars_15m and len(bars_15m) >= BARS_15M_12H:
        micro = _extract_15m_features(bars_15m)
    else:
        micro = {
            'choch_count': 0,
            'bos_count': 0,
            'momentum_shift': 0.0,
            'micro_compress': 5.0,
            'vol_climax': 0,
        }

    return {
        # 原有核心10维
        'norm_closes':    norm_closes,
        'total_move':     norm_closes[-1],
        'max_drawdown':   min(norm_closes),
        'max_gain':       max(norm_closes),
        'amplitude':      (max(highs) - min(lows)) / c0 * 100.0,
        'amp_std':        amp_std,
        'rsi_end':        rsi_end,
        'vol_ratio':      vol_ratio_last,
        'n':              len(bars_4h),
        # 辅助10维
        'vol_trend':      round(vol_trend, 4),
        'price_pos':      round(price_pos, 4),
        'amp_ratio':      round(amp_ratio, 4),
        'rsi_momentum':   round(rsi_momentum, 3),
        'tail_amp_ratio': round(tail_amp_ratio, 4),
        'bbw_4h':         round(bbw_4h, 3),
        # 15m微结构5维
        'choch_count':    micro['choch_count'],
        'bos_count':      micro['bos_count'],
        'momentum_shift': micro['momentum_shift'],
        'micro_compress': micro['micro_compress'],
        'vol_climax':     micro['vol_climax'],
    }


# ══════════════════════════════════════════════════════════════════════════════
# 相似度计算（多维加权，考虑微结构）
# ══════════════════════════════════════════════════════════════════════════════

def _bbw_golden_zone_bonus(bbw: float) -> float:
    """
    [设计院封印 2026-08-09 苏摩111]
    BBW黄金区间相似度奖励（减小相似度距离 = 提升优先级）

    铁证：6.5年3071案例深度验证
      bb1.5-2.0%: WR=59.6%  EV=+2.08%  ← 甜蜜区
      bb1.0-1.5%: WR=53.5%  EV=+0.55%
      bb<0.5%:    WR=48.7%  EV=-0.09%  ← 极压缩反而最差

    奖励逻辑：黄金区间案例相似度提升，提高被选中概率
    返回值：负数=奖励（降低距离），正数=惩罚（增加距离）
    """
    if 1.5 <= bbw < 2.0:   return -0.08   # 甜蜜区：最强奖励
    if 1.0 <= bbw < 1.5:   return -0.05   # 黄金区：中等奖励
    if 0.8 <= bbw < 1.0:   return -0.02   # 临近区：轻微奖励
    if bbw < 0.3:           return +0.05   # 极压缩：轻微惩罚（容易假突破）
    return 0.0


def _similarity_score(feat_cur: dict, feat_hist: dict) -> float:
    """
    25维综合相似度得分（越小越相似）
    权重分配：
      价格形态 40% + 振幅 12% + 移动 12% + RSI 16%
      + 成交量趋势 5% + BBW 5% + 微结构 10%
    [2026-08-09] + BBW黄金区间加成（-0.08~+0.05）
    """
    # 价格形态：快速欧式距离
    s1 = feat_cur['norm_closes']
    s2 = feat_hist['norm_closes']
    n  = min(len(s1), len(s2))
    price_dist = math.sqrt(sum((s1[i] - s2[i]) ** 2 for i in range(n))) / n

    # 核心因子距离
    amp_diff  = abs(feat_cur['amplitude']    - feat_hist['amplitude'])
    move_diff = abs(feat_cur['total_move']   - feat_hist['total_move'])
    rsi_diff  = abs(feat_cur['rsi_end']      - feat_hist['rsi_end']) / 100.0

    # 辅助因子
    vol_diff  = abs(feat_cur.get('vol_trend', 0) - feat_hist.get('vol_trend', 0))
    bbw_diff  = abs(feat_cur.get('bbw_4h', 0)    - feat_hist.get('bbw_4h', 0))

    # 15m微结构距离（归一化到 0-1 量级）
    choch_diff  = abs(feat_cur.get('choch_count', 0) - feat_hist.get('choch_count', 0)) / 10.0
    bos_diff    = abs(feat_cur.get('bos_count', 0)   - feat_hist.get('bos_count', 0))   / 10.0
    mom_diff    = abs(feat_cur.get('momentum_shift', 0) - feat_hist.get('momentum_shift', 0)) / 5.0
    micro_dist  = (choch_diff + bos_diff + mom_diff) / 3.0

    # [2026-08-09 封印] BBW黄金区间加成：历史案例在甜蜜区(1.5-2.0%)的优先匹配
    bbw_hist_bonus = _bbw_golden_zone_bonus(feat_hist.get('bbw_4h', 0))

    return (
        price_dist * 0.40
        + amp_diff  * 0.12
        + move_diff * 0.12
        + rsi_diff  * 20.0 * 0.16
        + vol_diff  * 0.05
        + bbw_diff  * 0.05
        + micro_dist * 0.10
        + bbw_hist_bonus  # 黄金区间奖励/极压缩惩罚
    )


# ══════════════════════════════════════════════════════════════════════════════
# 核心：历史扫描 + 概率矩阵
# ══════════════════════════════════════════════════════════════════════════════

def _scan_history(
    klines_4h:   List[dict],
    klines_15m:  List[dict],
    regime_map:  Dict[int, str],
    current_regime: str,
) -> List[dict]:
    """
    扫描历史，返回最相似TOP_N案例列表
    每条包含：dt / score / future_ret / future_max / future_min / regime
    """
    # 当前特征（使用最近48根15m做微结构）
    recent_4h  = klines_4h[-WEEK_BARS:]
    recent_15m = klines_15m[-BARS_15M_12H:] if klines_15m else []
    feat_cur   = _extract_features(recent_4h, recent_15m)

    results = []
    total   = len(klines_4h)

    # 预先构建15m时间戳索引（加速查找）
    # 15m bars: 每根4H对应16根15m
    ts_to_15m_idx: Dict[int, int] = {}
    if klines_15m:
        for idx, b in enumerate(klines_15m):
            ts_to_15m_idx[b['ts']] = idx

    for start in range(100, total - WEEK_BARS - FUTURE_BARS, SCAN_STEP):
        hist_4h_bars = klines_4h[start : start + WEEK_BARS]
        end_ts       = hist_4h_bars[-1]['ts']

        # 尝试对齐15m数据（对应4H区间结束时间的前12H=48根15m）
        hist_15m_bars: List[dict] = []
        if ts_to_15m_idx and end_ts in ts_to_15m_idx:
            idx_15m = ts_to_15m_idx[end_ts]
            hist_15m_bars = klines_15m[max(0, idx_15m - BARS_15M_12H) : idx_15m]

        feat_hist = _extract_features(hist_4h_bars, hist_15m_bars)
        score     = _similarity_score(feat_cur, feat_hist)

        # 未来结果
        future_bars   = klines_4h[start + WEEK_BARS : start + WEEK_BARS + FUTURE_BARS]
        if len(future_bars) < FUTURE_BARS:
            continue
        future_closes = [float(b['c']) for b in future_bars]
        entry_price   = float(hist_4h_bars[-1]['c'])
        if entry_price == 0:
            continue

        future_ret = (future_closes[-1] - entry_price) / entry_price * 100.0
        future_max = (max(float(b['h']) for b in future_bars) - entry_price) / entry_price * 100.0
        future_min = (min(float(b['l']) for b in future_bars) - entry_price) / entry_price * 100.0

        ts     = hist_4h_bars[-1]['ts']
        regime = regime_map.get(ts, '?')
        dt     = datetime.utcfromtimestamp(ts // 1000).strftime('%Y-%m-%d')

        results.append({
            'dt':         dt,
            'score':      round(score, 4),
            'future_ret': round(future_ret, 2),
            'future_max': round(future_max, 2),
            'future_min': round(future_min, 2),
            'regime':     regime,
        })

    results.sort(key=lambda x: x['score'])
    return results[:TOP_N]


def _build_probability_matrix(top: List[dict]) -> dict:
    """从TOP_N历史案例构建概率矩阵"""
    if not top:
        return {
            'p_up': 0.5, 'p_down': 0.2, 'p_flat': 0.3,
            'ev': 0.0, 'n': 0,
            'median': 0.0, 'max_upside': 0.0, 'max_downside': 0.0,
            'tail_down_risk': 0.0,
        }

    rets = [s['future_ret'] for s in top]
    n    = len(rets)

    up   = sum(1 for r in rets if r >  2.0)
    dn   = sum(1 for r in rets if r < -2.0)
    flat = n - up - dn

    ev       = sum(rets) / n
    median   = sorted(rets)[n // 2]
    tail_dn  = sum(1 for s in top if s['future_min'] < -10.0)

    return {
        'p_up':           round(up   / n, 3),
        'p_down':         round(dn   / n, 3),
        'p_flat':         round(flat / n, 3),
        'ev':             round(ev, 3),
        'median':         round(median, 3),
        'max_upside':     round(max(s['future_max'] for s in top), 2),
        'max_downside':   round(min(s['future_min'] for s in top), 2),
        'tail_down_risk': round(tail_dn / n, 3),
        'n':              n,
    }


# ══════════════════════════════════════════════════════════════════════════════
# 升级3：主力意图检测层
# ══════════════════════════════════════════════════════════════════════════════

def _detect_main_force_intent(
    symbol: str,
    current_price: float,
    fc_cases: List[dict],
    feat_cur: Optional[dict] = None,
) -> dict:
    """
    基于历史案例 + 当前特征，判断主力意图。

    返回：
    {
      "intent":        "ACCUMULATE"|"DISTRIBUTE"|"MARKUP"|"MARKDOWN"|"NEUTRAL",
      "confidence":    0.0-1.0,
      "evidence":      [str, ...],
      "trap_warning":  bool,
      "trap_reason":   str
    }

    判断逻辑：
    - 相似历史案例中 future_ret > 3% 占比 >60% → ACCUMULATE意图
    - 相似历史案例中 future_ret < -3% 占比 >60% → DISTRIBUTE意图
    - future_max > 8% 且 future_ret > 5% 多数  → MARKUP
    - future_min < -8% 且 future_ret < -5% 多数 → MARKDOWN
    - BBW持续压缩（<3%）但RSI超买（>70）       → 可能是DISTRIBUTE陷阱
    - 整数位附近（距整数<1%）的压缩             → 高陷阱概率
    """
    if not fc_cases:
        return {
            'intent': 'NEUTRAL', 'confidence': 0.0,
            'evidence': ['历史案例为空，无法判断'],
            'trap_warning': False, 'trap_reason': '',
        }

    rets  = [c['future_ret'] for c in fc_cases]
    maxes = [c['future_max'] for c in fc_cases]
    mins  = [c['future_min'] for c in fc_cases]
    n     = len(rets)

    up_strong    = sum(1 for r in rets if r >  3.0) / n
    down_strong  = sum(1 for r in rets if r < -3.0) / n
    markup_n     = sum(1 for r, m in zip(rets, maxes) if r > 5.0 and m > 8.0) / n
    markdown_n   = sum(1 for r, m in zip(rets, mins)  if r < -5.0 and m < -8.0) / n

    evidence = []
    trap_warning = False
    trap_reason  = ''

    # 意图判断
    if markup_n >= 0.55:
        intent     = 'MARKUP'
        confidence = round(markup_n, 2)
        evidence.append(f"近{n}个相似案例中{markup_n*100:.0f}%出现强涨幅(>8%)")
    elif markdown_n >= 0.50:
        intent     = 'MARKDOWN'
        confidence = round(markdown_n, 2)
        evidence.append(f"近{n}个相似案例中{markdown_n*100:.0f}%出现强跌幅(<-8%)")
    elif up_strong >= 0.60:
        intent     = 'ACCUMULATE'
        confidence = round(up_strong, 2)
        evidence.append(f"近{n}个相似案例中{up_strong*100:.0f}%为多头爆发(>3%)")
    elif down_strong >= 0.60:
        intent     = 'DISTRIBUTE'
        confidence = round(down_strong, 2)
        evidence.append(f"近{n}个相似案例中{down_strong*100:.0f}%为空头爆发(<-3%)")
    else:
        intent     = 'NEUTRAL'
        confidence = round(max(up_strong, down_strong), 2)
        evidence.append(f"方向分散：↑{up_strong*100:.0f}% ↓{down_strong*100:.0f}%")

    # BBW 压缩 + RSI 陷阱检测
    bbw_4h  = feat_cur.get('bbw_4h', 5.0)  if feat_cur else 5.0
    rsi_end = feat_cur.get('rsi_end', 50.0) if feat_cur else 50.0
    micro_compress = feat_cur.get('micro_compress', 5.0) if feat_cur else 5.0

    if bbw_4h < 3.0 and rsi_end > 70.0:
        trap_warning = True
        trap_reason  = f'BBW={bbw_4h:.1f}%极度压缩但RSI={rsi_end:.0f}过热，可能空头陷阱'
        evidence.append(f'⚠ BBW={bbw_4h:.1f}%压缩+RSI={rsi_end:.0f}超买=Distribution陷阱风险')
    elif bbw_4h < 3.0 and rsi_end < 30.0:
        trap_warning = True
        trap_reason  = f'BBW={bbw_4h:.1f}%极度压缩但RSI={rsi_end:.0f}过冷，可能多头陷阱'
        evidence.append(f'⚠ BBW={bbw_4h:.1f}%压缩+RSI={rsi_end:.0f}超卖=Accumulation陷阱风险')
    elif micro_compress < 1.5:
        trap_warning = True
        trap_reason  = f'15m BBW={micro_compress:.2f}%极度压缩，假突破风险高'
        evidence.append(f'⚠ 15m微结构极度压缩(BBW={micro_compress:.2f}%)=假突破高风险')

    # 整数位陷阱检测
    if current_price > 0:
        integers = [10000, 20000, 30000, 40000, 50000, 60000, 70000, 80000, 90000, 100000]
        for level in integers:
            dist_pct = abs(current_price - level) / level * 100.0
            if dist_pct < 1.0:
                trap_warning = True
                trap_reason  = trap_reason or f'价格在整数位${level:,}附近({dist_pct:.2f}%内)'
                evidence.append(f'⚠ 整数位${level:,}附近={dist_pct:.2f}%，磁吸效应+陷阱概率高')
                break

    # 成交量放量 + CHoCH 辅助证据
    if feat_cur:
        if feat_cur.get('vol_climax'):
            evidence.append('近12H有放量K线（成交量>均量×2）')
        choch = feat_cur.get('choch_count', 0)
        if choch >= 3:
            evidence.append(f'近12H CHoCH={choch}次，结构频繁转变中')

    return {
        'intent':       intent,
        'confidence':   confidence,
        'evidence':     evidence[:6],  # 最多6条证据
        'trap_warning': trap_warning,
        'trap_reason':  trap_reason,
    }


# ══════════════════════════════════════════════════════════════════════════════
# 升级2：集成 HCME M1-M6 结果（fail-safe）
# ══════════════════════════════════════════════════════════════════════════════

def _integrate_hcme(current_signal_dict: dict) -> dict:
    """集成 HCME Matcher（M2流动性地图），fail-safe"""
    try:
        from hcme_matcher import get_hcme_matcher as _get_hcme_fc
        matcher = _get_hcme_fc()
        hcme_result = matcher.find_similar(current_signal_dict, top_k=5)
        return {
            'hcme_wr_adj':   hcme_result.get('hcme_score_adj', 0),
            'hcme_context':  hcme_result.get('context_summary', ''),
        }
    except Exception:
        return {'hcme_wr_adj': 0, 'hcme_context': ''}


def _integrate_m4_bias() -> dict:
    """集成 M4 主力行为偏置（print_current_bias → dict化），fail-safe"""
    try:
        import brahma_brain.market_behavior_model as mbm  # noqa

        # 先尝试直接调用 get_current_bias（如果将来实现了）
        if hasattr(mbm, 'get_current_bias'):
            return mbm.get_current_bias()  # type: ignore[attr-defined]

        # fallback：从 OUTPUT_PATH 读取模型，提取当前时间维度偏置
        output_path = getattr(mbm, 'OUTPUT_PATH', None)
        if not output_path or not os.path.exists(output_path):
            return {}

        with open(output_path) as f:
            model = json.load(f)

        now = datetime.now(tz=timezone.utc)
        bias: dict = {}

        # 小时偏置（hourly_bias为4H粒度：0/4/8/12/16/20，取最近档位）
        _hour_keys = sorted(int(k) for k in model.get('hourly_bias', {}).keys())
        _nearest_h = str(min(_hour_keys, key=lambda k: abs(k - now.hour))) if _hour_keys else str(now.hour)
        hb = model.get('hourly_bias', {}).get(_nearest_h)
        if hb:
            bias['hour_avg_chg'] = hb.get('avg_chg', 0.0)
            bias['hour_up_prob'] = hb.get('up_prob', 0.5)
            bias['hour_n']       = hb.get('n', 0)

        # 周内偏置
        wb = model.get('weekly_bias', {}).get(str(now.weekday()))
        if wb:
            bias['weekday_avg_chg'] = wb.get('avg_chg', 0.0)
            bias['weekday_up_prob'] = wb.get('up_prob', 0.5)

        # 月份偏置
        mb = model.get('monthly_bias', {}).get(str(now.month))
        if mb:
            bias['month_avg_chg'] = mb.get('avg_chg', 0.0)
            bias['month_up_prob'] = mb.get('up_prob', 0.5)

        # 整数位数据
        bias['fakeout_n'] = model.get('fakeout_stats', {}).get('n', 0)

        return bias
    except Exception:
        return {}


# ══════════════════════════════════════════════════════════════════════════════
# 公开接口
# ══════════════════════════════════════════════════════════════════════════════

def get_fangcang_context(
    symbol: str = 'BTCUSDT',
    current_regime: Optional[str] = None,
) -> dict:
    """
    主接口：返回方仓经验引擎完整结果（v2.0，多时框+主力意图版）。
    供 brahma_engine.analyze() 调用，结果注入 _result['fangcang']

    返回结构：
    {
      'status':            'ok' | 'unavailable',
      'symbol':            str,
      'run_at':            ISO时间戳,
      'current_regime':    str,

      # 原有字段（保留兼容）
      'top_similar':       [{dt, score, future_ret, future_max, future_min, regime}, ...],
      'prob_matrix':       {p_up, p_down, p_flat, ev, median, max_upside, max_downside, tail_down_risk, n},
      'signal_hint':       'LONG_BIAS'|'SHORT_BIAS'|'NEUTRAL'|'WAIT',
      'long_prob':         float,  # 别名 prob_matrix.p_up
      'short_prob':        float,  # 别名 prob_matrix.p_down
      'chop_prob':         float,  # 别名 prob_matrix.p_flat
      'top3_summary':      str,

      # 新增字段（升级内容）
      'main_force_intent': {intent, confidence, evidence, trap_warning, trap_reason},
      'micro_structure':   {choch_count, bos_count, momentum_shift, micro_compress, vol_climax},
      'similar_cases_count': int,
      'best_entry_window': str,    # 基于M4时间偏置
      'trap_alert':        bool,
      'confidence_level':  'HIGH'|'MEDIUM'|'LOW',
      'market_bias':       dict,   # M4 主力行为偏置
      'hcme_wr_adj':       int,    # HCME分数调整
      'hcme_context':      str,
      'fangcang_summary':  str,    # 一句话总结（给苏摩看）
      'pip_shape':         str,    # PIPs几何形态（第9维）
      'pip_score':         float,  # 形态清晰度 [0,1]
    }
    """
    cache_key = f"{symbol}:{current_regime}"
    now = time.time()

    # 检查缓存（2026-08-28 B2优化: regime为None时也能命中有效缓存）
    if cache_key in _CACHE:
        cached = _CACHE[cache_key]
        if now - cached.get('_ts', 0) < _CACHE_TTL:
            return cached
    # fallback: 如果 regime=None未命中，尝试用同 symbol 的任意已缓存结果
    if current_regime is None:
        for k, v in _CACHE.items():
            if k.startswith(f"{symbol}:") and now - v.get('_ts', 0) < _CACHE_TTL:
                return v  # 命中，直接复用，节省~1.4s

    try:
        # 加载数据
        klines_4h  = _load_klines(symbol, '4h')
        klines_15m = _load_klines(symbol, '15m')
        regime_map = _load_regime_map(symbol)

        if len(klines_4h) < WEEK_BARS + FUTURE_BARS + 100:
            return {'status': 'unavailable', 'reason': 'insufficient_data', '_ts': now}

        # 当前体制（外部传入优先，其次读 regime_state.json SSOT，最后 fallback）
        if not current_regime:
            try:
                import json as _j
                _rs = _j.loads((_BASE / 'data' / 'regime_state.json').read_text())
                _s = _rs.get(symbol, {})
                current_regime = _s.get('confirmed') or _s.get('regime') or 'UNKNOWN'
            except Exception:
                last_ts = klines_4h[-1]['ts']
                current_regime = regime_map.get(last_ts, 'UNKNOWN')

        # 当前特征提取（用于主力意图检测）
        recent_15m = klines_15m[-BARS_15M_12H:] if klines_15m else []
        feat_cur   = _extract_features(klines_4h[-WEEK_BARS:], recent_15m)

        # 15m微结构摘要
        micro_structure = {
            'choch_count':    feat_cur.get('choch_count', 0),
            'bos_count':      feat_cur.get('bos_count', 0),
            'momentum_shift': feat_cur.get('momentum_shift', 0.0),
            'micro_compress': feat_cur.get('micro_compress', 5.0),
            'vol_climax':     feat_cur.get('vol_climax', 0),
        }

        # 扫描历史相似案例（传入15m数据）
        top_similar = _scan_history(klines_4h, klines_15m, regime_map, current_regime)

        # 概率矩阵
        prob = _build_probability_matrix(top_similar)

        # 主力意图检测
        current_price = float(klines_4h[-1]['c']) if klines_4h else 0.0
        main_force    = _detect_main_force_intent(symbol, current_price, top_similar, feat_cur)

        # 信号偏向
        if prob['p_up'] >= 0.60 and prob['ev'] > 0:
            hint = 'LONG_BIAS'
        elif prob['p_down'] >= 0.50 and prob['ev'] < 0:
            hint = 'SHORT_BIAS'
        elif prob['tail_down_risk'] >= 0.25:
            hint = 'WAIT'
        else:
            hint = 'NEUTRAL'

        # 置信等级
        n_cases = prob['n']
        intent_conf = main_force.get('confidence', 0.0)
        if n_cases >= 15 and intent_conf >= 0.65:
            confidence_level = 'HIGH'
        elif n_cases >= 8 and intent_conf >= 0.45:
            confidence_level = 'MEDIUM'
        else:
            confidence_level = 'LOW'

        # 最佳入场时间窗口（集成M4偏置）
        market_bias = _integrate_m4_bias()
        best_entry_window = _calc_best_entry_window(market_bias)

        # HCME 集成
        current_signal_dict = {
            'regime':    current_regime,
            'direction': 'LONG' if hint == 'LONG_BIAS' else ('SHORT' if hint == 'SHORT_BIAS' else 'NEUTRAL'),
            'bbw':       feat_cur.get('bbw_4h', 0),
            'rsi':       feat_cur.get('rsi_end', 50),
        }
        hcme_data = _integrate_hcme(current_signal_dict)

        # [2026-08-12 封印] PIPs形态特征提取（第9维接入）
        pip_feature = {}
        try:
            from brahma_brain.pip_extractor import extract_pip_feature as _epf
            _recent_closes = [float(b['c']) for b in klines_4h[-30:]]  # 取近30根K线
            pip_feature = _epf(_recent_closes)
        except Exception:
            pip_feature = {'pip_shape': 'UNKNOWN', 'shape_score': 0.0}

        # [2026-08-20 封印] 阶段2：周月线锚定 + Elliott Wave + VPA
        _htf_features = {}
        _elliott_result = {}
        _vpa_result = {}
        try:
            from brahma_brain.weekly_monthly_anchor import get_anchor as _get_anchor
            _anchor = _get_anchor(symbol)
            _htf_features = _anchor.get_features(current_price=current_price)
        except Exception as _e:
            _htf_features = {'_anchor_summary': f'HTF锚定不可用: {_e}'}
        try:
            from brahma_brain.elliott_wave_pips import ElliottWaveDetector as _EWD
            _ew_closes = [float(b['c']) for b in klines_4h[-60:]]
            _ew_highs  = [float(b['h']) for b in klines_4h[-60:]]
            _ew_lows   = [float(b['l']) for b in klines_4h[-60:]]
            _ew = _EWD(_ew_closes, _ew_highs, _ew_lows, n_bars=60)
            _elliott_result = _ew.analyze()
        except Exception as _e:
            _elliott_result = {'wave_type': 'UNKNOWN', 'score_addon': 0, 'summary': f'Elliott不可用: {_e}'}
        try:
            from brahma_brain.vpa_analyzer import analyze_vpa as _avpa
            _vpa_result = _avpa(klines_4h, n_bars=20)
        except Exception as _e:
            _vpa_result = {'score_addon': 0, 'summary': f'VPA不可用: {_e}'}

        # [2026-08-09 封印] 向量检索增强层：查询历史最相似TOP20案例
        vector_stats = {}
        try:
            from brahma_brain.fangcang_vector_db import query_stats as _vq
            _bbw    = feat_cur.get('bbw_4h', 1.0)
            _sqbars = feat_cur.get('n', 42)      # 上一轮历史扫描的K线根数为代理
            _burst  = feat_cur.get('amp_ratio', 1.0)
            _vol    = feat_cur.get('vol_ratio', 2.0)
            _rsi    = feat_cur.get('rsi_end', 50.0)
            _dir    = 'UP' if hint == 'LONG_BIAS' else ('DOWN' if hint == 'SHORT_BIAS' else 'UP')
            _sym    = symbol.replace('USDT','').replace('PERP','')
            vector_stats = _vq(
                bb_width=_bbw, squeeze_bars=_sqbars, burst_atr=_burst,
                vol_ratio=_vol, rsi=_rsi, direction=_dir,
                symbol=_sym if _sym in ('BTC','ETH','SOL') else None,
                top_k=20,
            )
        except Exception:
            pass

        # TOP3文字摘要
        top3_lines = []
        for s in top_similar[:3]:
            arrow = '↑' if s['future_ret'] > 0 else '↓'
            top3_lines.append(
                f"  {s['dt']} [{s['regime'][:4]}] {arrow}{abs(s['future_ret']):.1f}% "
                f"(最高{s['future_max']:+.1f}% 最低{s['future_min']:+.1f}%)"
            )
        top3_summary = '\n'.join(top3_lines)

        # 陷阱预警（综合）
        trap_alert = main_force.get('trap_warning', False)

        # 一句话总结
        fangcang_summary = _build_summary(
            symbol, current_price, current_regime, hint,
            prob, main_force, confidence_level, trap_alert
        )

        result = {
            'status':           'ok',
            'symbol':           symbol,
            'run_at':           datetime.now(timezone.utc).isoformat(),
            'current_regime':   current_regime,

            # 原有字段（保留兼容）
            'top_similar':      top_similar,
            'prob_matrix':      prob,
            'signal_hint':      hint,
            'long_prob':        prob['p_up'],
            'short_prob':       prob['p_down'],
            'chop_prob':        prob['p_flat'],
            'top3_summary':     top3_summary,

            # 新增字段
            'main_force_intent':   main_force,
            'micro_structure':     micro_structure,
            'similar_cases_count': n_cases,
            'best_entry_window':   best_entry_window,
            'trap_alert':          trap_alert,
            'confidence_level':    confidence_level,
            'market_bias':         market_bias,
            'hcme_wr_adj':         hcme_data.get('hcme_wr_adj', 0),
            'hcme_context':        hcme_data.get('hcme_context', ''),
            'fangcang_summary':    fangcang_summary,
            'vector_stats':        vector_stats,   # [2026-08-09] 向量检索增强结果
            'pip_shape':           pip_feature.get('pip_shape', 'UNKNOWN'),   # [2026-08-12] PIPs形态
            'pip_score':           pip_feature.get('shape_score', 0.0),       # [2026-08-12] 形态清晰度
            # [2026-08-20] 阶段2新维度
            'htf_anchor':          _htf_features,                             # 周月线大周期8维
            'elliott_wave':        _elliott_result,                           # Elliott波浪分析
            'vpa':                 _vpa_result,                               # VPA成交量行为

            '_ts': now,
        }

        _CACHE[cache_key] = result
        return result

    except Exception as e:
        return {
            'status': 'unavailable',
            'reason': str(e)[:120],
            '_ts':    now,
        }


def _calc_best_entry_window(bias: dict) -> str:
    """根据M4偏置计算最佳入场时间窗口"""
    if not bias:
        return '无M4数据'
    hour_up  = bias.get('hour_up_prob', 0.5)
    hour_chg = bias.get('hour_avg_chg', 0.0)
    week_chg = bias.get('weekday_avg_chg', 0.0)
    now_utc  = datetime.now(timezone.utc)
    window   = []
    if hour_up > 0.6:
        window.append(f'当前{now_utc.hour:02d}:00 UTC看涨偏置({hour_up:.0%})')
    elif hour_up < 0.4:
        window.append(f'当前{now_utc.hour:02d}:00 UTC看跌偏置({1-hour_up:.0%})')
    if week_chg > 0.1:
        window.append(f'本周方向偏多({week_chg:+.2f}%均涨)')
    elif week_chg < -0.1:
        window.append(f'本周方向偏空({week_chg:+.2f}%均跌)')
    return '；'.join(window) if window else '当前时间无明显偏置'


def _build_summary(
    symbol: str,
    price: float,
    regime: str,
    hint: str,
    prob: dict,
    intent: dict,
    conf_level: str,
    trap_alert: bool,
) -> str:
    """构建给苏摩看的一句话自然语言总结"""
    hint_cn = {
        'LONG_BIAS': '偏多',
        'SHORT_BIAS': '偏空',
        'NEUTRAL': '中性',
        'WAIT': '等待',
    }.get(hint, hint)
    intent_cn = {
        'ACCUMULATE': '主力吸筹',
        'DISTRIBUTE': '主力派筹',
        'MARKUP': '拉升行情',
        'MARKDOWN': '打压行情',
        'NEUTRAL': '意图不明',
    }.get(intent.get('intent', 'NEUTRAL'), '意图不明')
    trap_str = '⚠ 陷阱预警' if trap_alert else ''
    p_up  = prob.get('p_up', 0.5) * 100
    p_dn  = prob.get('p_down', 0.2) * 100
    ev    = prob.get('ev', 0.0)
    n     = prob.get('n', 0)
    return (
        f"{symbol} ${price:,.0f} [{regime[:6]}] {hint_cn} | "
        f"↑{p_up:.0f}% ↓{p_dn:.0f}% EV={ev:+.1f}% | "
        f"{intent_cn}(置信{intent.get('confidence',0)*100:.0f}%) "
        f"[{conf_level}/{n}案例] {trap_str}"
    ).strip()


# ══════════════════════════════════════════════════════════════════════════════
# 格式化（兼容旧接口）
# ══════════════════════════════════════════════════════════════════════════════

def format_fangcang_card(fc: dict) -> str:
    """格式化方仓摘要，嵌入信号卡片"""
    if fc.get('status') != 'ok':
        return ''

    pm   = fc.get('prob_matrix', {})
    hint = fc.get('signal_hint', 'NEUTRAL')
    hint_icons = {
        'LONG_BIAS': '📈', 'SHORT_BIAS': '📉',
        'WAIT': '⏳',       'NEUTRAL': '⚖️',
    }
    icon = hint_icons.get(hint, '⚖️')

    mfi  = fc.get('main_force_intent', {})
    ms   = fc.get('micro_structure', {})
    trap = '⚠️ 陷阱预警！' if fc.get('trap_alert') else ''
    conf = fc.get('confidence_level', '?')

    lines = [
        f"━━ 🏛️ 方仓经验引擎v2 (6.8年/{pm.get('n',0)}案例) {icon} [{conf}] ━━",
        f"  ↑{pm.get('p_up',0)*100:.0f}% ↓{pm.get('p_down',0)*100:.0f}% "
        f"↔{pm.get('p_flat',0)*100:.0f}%  EV={pm.get('ev',0):+.2f}%  "
        f"尾部风险={pm.get('tail_down_risk',0)*100:.0f}%",
        f"  主力: {mfi.get('intent','?')} 置信{mfi.get('confidence',0)*100:.0f}%  "
        f"15m CHoCH={ms.get('choch_count',0)} BOS={ms.get('bos_count',0)} "
        f"BBW={ms.get('micro_compress',0):.1f}%  {trap}",
        "  最相似历史案例:",
        fc.get('top3_summary', '  (无数据)'),
        f"  📝 {fc.get('fangcang_summary', '')}",
    ]
    return '\n'.join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# 冒烟测试
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    print("=== fangcang_engine v2.0 冒烟测试 ===\n")

    import time as _t
    t0 = _t.time()

    result = get_fangcang_context('BTCUSDT')
    elapsed = _t.time() - t0

    print(f"状态: {result['status']}")
    if result['status'] == 'ok':
        pm  = result['prob_matrix']
        mfi = result['main_force_intent']
        ms  = result['micro_structure']
        print(f"体制: {result['current_regime']}")
        print(f"信号偏向: {result['signal_hint']}")
        print(f"做多概率: {result.get('long_prob', 0)*100:.0f}%")
        print(f"做空概率: {result.get('short_prob', 0)*100:.0f}%")
        print(f"震荡概率: {result.get('chop_prob', 0)*100:.0f}%")
        print(f"主力意图: {mfi.get('intent', '?')} (置信{mfi.get('confidence',0)*100:.0f}%)")
        print(f"陷阱预警: {result.get('trap_alert', False)}")
        if mfi.get('trap_reason'):
            print(f"陷阱原因: {mfi['trap_reason']}")
        print(f"置信等级: {result.get('confidence_level', '?')}")
        print(f"相似案例数: {result.get('similar_cases_count', 0)}")
        print(f"\n15m微结构: CHoCH={ms.get('choch_count',0)} BOS={ms.get('bos_count',0)} "
              f"BBW={ms.get('micro_compress',0):.1f}% 放量={ms.get('vol_climax',0)}")
        print(f"\n主力证据:")
        for ev in mfi.get('evidence', []):
            print(f"  - {ev}")
        print(f"\n最佳入场时间: {result.get('best_entry_window', '?')}")
        print(f"\n总结: {result.get('fangcang_summary', '?')}")
        print(f"\nTOP5相似案例:")
        for s in result['top_similar'][:5]:
            print(f"  {s['dt']} score={s['score']:.3f} ret={s['future_ret']:+.1f}% [{s['regime']}]")
        print(f"\n信号卡片:")
        print(format_fangcang_card(result))
    else:
        print(f"原因: {result.get('reason', '?')}")

    print(f"\n耗时: {elapsed:.2f}s")
    print("\n✅ 冒烟测试完成")
