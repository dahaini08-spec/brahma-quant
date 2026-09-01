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


# 数据加载

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


# 基础计算工具

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


# 升级1：15m 微结构特征提取

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


# 升级2：扩展特征向量到25维

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


# 相似度计算（多维加权，考虑微结构）

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


# 核心：历史扫描 + 概率矩阵

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


# 升级3：主力意图检测层

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


# 升级2：集成 HCME M1-M6 结果（fail-safe）

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


# 公开接口

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


# 格式化（兼容旧接口）

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


# 冒烟测试

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


# ══ [2026-09-01 设计院精简封印] 合并自 brahma_brain/fangcang_hcme_bridge.py ══
"""
fangcang_hcme_bridge.py — 方仓增强型HCME桥接引擎
设计院 2026-08-23 苏摩111封印

架构升级：
  旧：HCME(pseudo伪信号2177条) → -9分（无效惩罚，基于假数据）
  新：方仓(真实案例1597条) → 相似度匹配 → 置信度门控 → 精准评分

三层设计：
  Step A: HCME伪数据权重归零（hcme_source=pseudo → 完全忽略）
  Step B: 方仓1597条真实案例相似度匹配
  Step C: 置信度门控（n<3→0分中性 / n3~9→半权重 / n≥10→全权重）

铁证基础：
  方仓BTC=255条 / ETH=280条 / SOL=1062条 = 1597条真实6.5年案例
  字段：compress_bbw_min / rsi_at_end / breakout_direction / future_return_24h
  vs HCME：45670条pseudo合成 WR=46.4% EV=-0.146%（统计上无意义）
"""
import os
import json
import math
from pathlib import Path

_BASE = Path(__file__).parent.parent
_DATA = _BASE / 'data'

# 缓存方仓案例（全局单例，避免重复读盘）
_FANGCANG_CACHE: list = []
_CACHE_LOADED = False


# 30个新币种列表（今日新建）
_NEW_30_SYMBOLS = [
    'xrp','zec','doge','bnb','link','ada','bch','ltc','xlm','xmr',
    'dash','trx','etc','dot','crv','atom','algo','ont','trb','rune',
    'vet','egld','comp','snx','theta','iota','kava','neo','sushi','zil',
]


def _infer_regime_from_case(d: dict) -> str:
    """
    从方仓案例字段推断regime_guess。
    案例库不存储体制标签，用future_return+breakout方向+vol简单推断。
    这不是100%准确的，但比''好得多。
    """
    ret   = float(d.get('future_return_24h', d.get('future_return', 0)) or 0)
    direc = str(d.get('direction', '')).upper()
    vol   = float(d.get('vol_ratio_peak', 1) or 1)
    rsi   = float(d.get('rsi_at_burst', 50) or 50)

    # 强势上涨信号 → BULL
    if direc == 'UP' and ret > 4 and vol > 2.0 and rsi > 55:
        return 'BULL_TREND'
    # 强势下跌信号 → BEAR
    if direc == 'DOWN' and ret < -4 and vol > 2.0 and rsi < 45:
        return 'BEAR_TREND'
    # 温和上涨 → BULL_EARLY / BEAR_RECOVERY
    if direc == 'UP' and ret > 1.5:
        return 'BULL_EARLY'
    # 温和下跌 → BEAR_EARLY
    if direc == 'DOWN' and ret < -1.5:
        return 'BEAR_EARLY'
    # 其余归CHOP
    return 'CHOP_MID'


def _normalize_new_case(d: dict, sym: str) -> dict:
    """
    把新建的fangcang_cases_xxx.json字段统一化为标准格式。
    字段映射: min_bb_width(百分比)→compress_bbw_min(小数)/rsi_at_burst→rsi_at_end
    修复1: 新增 compress_end_ts 从ts_burst转换 → 修复时间衰减失效
    修复2: 新增 regime_guess 从future_return+direction推断 → 修复体制过滤失效
    """
    # ts_burst → epoch，用于时间衰减
    _ts_epoch = 0.0
    _ts_raw = d.get('ts_burst', '') or ''
    if _ts_raw:
        try:
            from datetime import datetime
            _ts_epoch = datetime.fromisoformat(str(_ts_raw)).timestamp()
        except Exception:
            pass

    _direction = str(d.get('direction', '')).upper()
    _ret24h    = float(d.get('future_return_24h', d.get('future_return', 0)) or 0)

    return {
        'symbol':             sym.upper() + 'USDT' if not sym.upper().endswith('USDT') else sym.upper(),
        '_src_sym':           sym.upper().replace('USDT', ''),
        'compress_bbw_min':   float(d.get('min_bb_width', 0) or 0) / 100,
        'rsi_at_end':         float(d.get('rsi_at_burst', 50) or 50),
        'compress_bars':      int(d.get('squeeze_bars', 0) or 0),
        'breakout_direction': 'LONG'  if _direction == 'UP'   else
                              ('SHORT' if _direction == 'DOWN' else 'CHOP'),
        'future_return_24h':  _ret24h,
        'volume_trend':       'expand' if float(d.get('vol_ratio_peak', 1) or 1) > 1.5 else 'flat',
        'is_genuine_breakout': bool(d.get('is_genuine_breakout', False)),
        # 修复1: 时间戳
        'compress_end_ts':    _ts_epoch,
        # 修复2: 体制推断标签
        'regime_guess':       d.get('regime', d.get('regime_guess', '')) or _infer_regime_from_case(d),
        # 40年经验维度: 突破力度
        'burst_atr_mult':     float(d.get('burst_atr_mult', 0) or 0),
    }


def _load_fangcang_cases() -> list:
    """[2026-08-28 梵天设计院封印] 从统一主库加载方仓31廳数据库
    主库: brahma_brain/data/fangcang_merged_v2.json
    覆盖旧的分散读取逐一加载逻辑，统一入口
    """
    global _FANGCANG_CACHE, _CACHE_LOADED
    if _CACHE_LOADED:
        return _FANGCANG_CACHE

    import logging as _lg
    _log = _lg.getLogger('brahma.fangcang')

    cases = []

    # ① 优先加载统一主库（v2.1_deduped，15765条，33个标的）
    merged_path = _DATA / 'fangcang_merged_v2.json'
    if merged_path.exists():
        try:
            raw = json.loads(merged_path.read_text())
            raw_cases = raw.get('cases', raw) if isinstance(raw, dict) else raw
            for d in raw_cases:
                if not isinstance(d, dict): continue
                sym = str(d.get('symbol', '')).upper()
                if not sym: continue
                c = _normalize_new_case(d, sym.lower())
                c['_src_sym'] = sym
                cases.append(c)
            _log.info(f'[fangcang] 统一主库加载: {len(cases)}条 / '
                      f'{len(set(c["_src_sym"] for c in cases))}个标的')
        except Exception as _e:
            _log.warning(f'[fangcang] 统一主库加载失败: {_e}，回退到分散加载')
            cases = []

    # ② 备用：若主库不可用，回退到旧分散加载
    if not cases:
        _log.warning('[fangcang] 回退到旧分散加载模式')
        for sym in _NEW_30_SYMBOLS:
            fpath = _DATA / f'fangcang_cases_{sym}.json'
            if not fpath.exists(): continue
            try:
                raw = json.loads(fpath.read_text())
                if isinstance(raw, list):
                    for d in raw:
                        cases.append(_normalize_new_case(d, sym))
            except Exception:
                pass

    _log.info(f'[fangcang_hcme_bridge] 加载完成: 总{len(cases)}条案例')
    _FANGCANG_CACHE = cases
    _CACHE_LOADED = True
    return cases


def fangcang_context_match(
    symbol: str,
    current_bbw: float,
    current_rsi: float,
    current_regime: str,
    signal_dir: str,
    current_bars: int = 0,
) -> dict:
    """
    方仓相似度匹配：在真实案例库中找相似压缩案例，输出方向概率评分

    参数：
      symbol       : 当前标的（用于优先同标的案例）
      current_bbw  : 当前布林带宽度（小数，如0.0084=0.84%）
      current_rsi  : 当前RSI_1H
      current_regime: 当前体制（BULL_TREND等）
      signal_dir   : 信号方向（LONG/SHORT）
      current_bars : 当前压缩持续bars（可选）

    返回：
      {
        'score_adj': float,     # 评分调整（-10~+10）
        'confidence': str,      # HIGH/MED/LOW/NONE
        'n_similar': int,       # 相似案例数
        'long_pct': float,      # 历史做多突破概率
        'short_pct': float,     # 历史做空突破概率
        'chop_pct': float,      # 历史横盘概率
        'source': str,          # 数据来源说明
        'hcme_source': str,     # 'real_fangcang' | 'no_match'
      }
    """
    cases = _load_fangcang_cases()
    if not cases:
        return {
            'score_adj': 0, 'confidence': 'NONE', 'n_similar': 0,
            'long_pct': 0, 'short_pct': 0, 'chop_pct': 0,
            'source': 'no_data', 'hcme_source': 'no_data'
        }

    # ── 相似度匹配（三维：BBW + RSI + 体制） ──
    similar = []
    for c in cases:
        c_bbw = float(c.get('compress_bbw_min', 0) or 0)
        c_rsi = float(c.get('rsi_at_end', 50) or 50)
        c_regime = str(c.get('regime_guess', '') or '').lower()

        # BBW相似度：±30%容差（压缩程度相近）
        if c_bbw <= 0:
            continue
        bbw_ratio = abs(c_bbw - current_bbw) / max(current_bbw, 1e-9)
        if bbw_ratio > 0.35:
            continue

        # RSI相似度：±18容差
        if abs(c_rsi - current_rsi) > 18:
            continue

        # [修复 2026-08-29] 体制匹配：精确匹配权重1.0，同大组降权0.6，跨组降权0.3但保留
        # 原逻辑：跨组直接跳过 → 导致20392条案例无体制标签时全部归CHOP，BEAR/BULL体制下近乎无案例可用
        _REGIME_GROUP = {
            'BULL': {'BULL_TREND','BULL_EARLY','BULL_PEAK','BULL_CORRECTION','BULL','bull','bullish','trending'},
            'BEAR': {'BEAR_TREND','BEAR_EARLY','BEAR_CRASH','BEAR_RECOVERY','BEAR','bear','bearish','downtrend'},
            'CHOP': {'CHOP_MID','CHOP_HIGH','CHOP_LOW','BREAKOUT','CHOP','ranging','chop',''},
        }
        cur_grp = next((g for g,s in _REGIME_GROUP.items() if current_regime.upper() in {x.upper() for x in s}), 'CHOP')
        c_grp   = next((g for g,s in _REGIME_GROUP.items() if c_regime.upper() in {x.upper() for x in s}), 'CHOP')

        if cur_grp == c_grp:
            _regime_w = 1.0   # 完全匹配
        else:
            _regime_w = 0.35  # 跨组降权，但不丢弃（BBW+RSI相似的跨体制案例仍有参考价值）

        # [修复] 时间衰减权重：compress_end_ts现已正确填充
        import time as _t
        _case_ts = c.get('compress_end_ts', 0) or 0
        if _case_ts > 1e10: _case_ts /= 1000  # ms转s
        _age_years = (_t.time() - _case_ts) / (365.25 * 86400) if _case_ts > 0 else 3.0
        _time_weight = 2.0 if _age_years < 1 else (1.2 if _age_years < 2 else (0.8 if _age_years < 4 else 0.5))

        # 综合相似度分数（40年经验融入 2026-08-29 苏摩111）
        bbw_score = 1.0 - bbw_ratio / 0.35
        rsi_score = 1.0 - abs(c_rsi - current_rsi) / 18

        # 【核心提升】burst_atr_mult —— 最强单一维度，区分真假突破的关键
        # 铁证: burst_atr_mult>1x → WR直6%跳到 58%
        _c_burst = float(c.get('burst_atr_mult', 0) or 0)
        if _c_burst >= 2.0:    _burst_w = 1.5   # 暴力突破 WR=59%
        elif _c_burst >= 1.5:  _burst_w = 1.3   # 强突破   WR=54%
        elif _c_burst >= 1.0:  _burst_w = 1.1   # 中突破   WR=54%
        elif _c_burst >= 0.5:  _burst_w = 0.9   # 弱突破   WR=59%
        else:                  _burst_w = 0.2   # 几乎无突破 WR=4%

        # 【长压缩加成】squeeze_bars —— 60+根凌　达 WR=44%
        _c_bars = int(c.get('compress_bars', c.get('squeeze_bars', 0)) or 0)
        if _c_bars >= 80:    _bars_w = 1.3
        elif _c_bars >= 40:  _bars_w = 1.1
        elif _c_bars >= 20:  _bars_w = 1.0
        else:                _bars_w = 0.9

        # 【genuine质量】真实突破应该被优先展示
        _genuine_w = 1.2 if c.get('is_genuine_breakout') else 0.8

        sim_score = (
            bbw_score * 0.4 +
            rsi_score * 0.3 +
            (_burst_w - 1.0) * 0.3   # burst贡献0.3权重
        ) * _time_weight * _regime_w * _bars_w * _genuine_w

        c['_sim_score']    = sim_score
        c['_time_weight']  = _time_weight
        c['_regime_w']     = _regime_w
        c['_burst_w']      = _burst_w
        similar.append(c)

    # 按相似度排序，取Top20
    similar.sort(key=lambda x: x.get('_sim_score', 0), reverse=True)
    similar = similar[:20]
    n = len(similar)

    if n == 0:
        return {
            'score_adj': 0, 'confidence': 'NONE', 'n_similar': 0,
            'long_pct': 0, 'short_pct': 0, 'chop_pct': 0,
            'source': 'no_match', 'hcme_source': 'no_match'
        }

    # ── 方向概率统计 ──
    long_n = sum(1 for c in similar if c.get('breakout_direction', '') == 'LONG')
    short_n = sum(1 for c in similar if c.get('breakout_direction', '') == 'SHORT')
    chop_n = sum(1 for c in similar if c.get('breakout_direction', '') == 'CHOP')
    long_pct = long_n / n
    short_pct = short_n / n
    chop_pct = chop_n / n

    # ── Step C：置信度门控 ──
    if n < 3:
        confidence = 'NONE'
        weight = 0.0    # 样本不足 → 完全中性
    elif n < 5:
        confidence = 'LOW'
        weight = 0.3    # 低置信
    elif n < 10:
        confidence = 'MED'
        weight = 0.6    # 半权重
    else:
        confidence = 'HIGH'
        weight = 1.0    # 全权重

    # ── 评分计算（基线50%，偏离基线 × 最大±12分） ──
    MAX_ADJ = 12.0
    if signal_dir == 'LONG':
        raw_adj = (long_pct - 0.40) * MAX_ADJ / 0.40
        raw_adj = max(-MAX_ADJ, min(MAX_ADJ, raw_adj))
    elif signal_dir == 'SHORT':
        raw_adj = (short_pct - 0.30) * MAX_ADJ / 0.30
        raw_adj = max(-MAX_ADJ, min(MAX_ADJ, raw_adj))
    else:
        raw_adj = 0.0

    score_adj = round(raw_adj * weight, 1)

    # 【新增】avg_burst_atr_mult —— Top20案例平均突破力度（40年经验核心维度）
    avg_burst = 0.0
    burst_vals = [float(c.get('burst_atr_mult', 0) or 0) for c in similar if c.get('burst_atr_mult', 0) > 0]
    if burst_vals:
        avg_burst = round(sum(burst_vals) / len(burst_vals), 2)

    # 【新增】genuine_rate —— Top20中真实突破比例
    genuine_rate = round(sum(1 for c in similar if c.get('is_genuine_breakout')) / max(n, 1), 2)

    # 【新增】avg_squeeze_bars —— 历史压缩时长均値
    sq_vals = [int(c.get('compress_bars', c.get('squeeze_bars', 0)) or 0) for c in similar if c.get('compress_bars', c.get('squeeze_bars', 0))]
    avg_sq_bars = round(sum(sq_vals) / max(len(sq_vals), 1), 1) if sq_vals else 0

    return {
        'score_adj':            score_adj,
        'confidence':           confidence,
        'n_similar':            n,
        'long_pct':             round(long_pct, 3),
        'short_pct':            round(short_pct, 3),
        'chop_pct':             round(chop_pct, 3),
        'avg_burst_atr_mult':   avg_burst,       # 主unified提供burst加成
        'genuine_rate':         genuine_rate,    # Top20真实突破率
        'avg_squeeze_bars':     avg_sq_bars,     # Top20历史压缩时长
        'source':               f'fangcang_real_{n}cases',
        'hcme_source':          'real_fangcang',
    }


def get_fangcang_hcme_score(
    symbol: str,
    ms: dict,
    signal_dir: str,
) -> dict:
    """
    主入口：替换旧HCME调用
    从ms提取BBW/RSI/regime，调用方仓相似度匹配，返回评分

    返回格式与旧HCME兼容：
      {'hcme_score_adj': float, 'context_summary': str, 'hcme_source': str}
    """
    # 提取当前市场参数
    try:
        bbw = float((ms.get('bb') or ms.get('momentum', {}).get('bb', {})).get('width', 0) or 0)
        rsi = float(ms.get('rsi_1h') or ms.get('momentum', {}).get('rsi_1h', 50) or 50)
        regime = str(ms.get('regime', '') or '')
    except Exception:
        bbw, rsi, regime = 0.01, 50.0, ''

    # BBW为0时用默认值（非压缩状态，评分中性）
    if bbw <= 0:
        return {
            'hcme_score_adj': 0,
            'context_summary': 'BBW数据缺失，HCME中性',
            'hcme_source': 'no_bbw_data',
        }

    result = fangcang_context_match(
        symbol=symbol,
        current_bbw=bbw,
        current_rsi=rsi,
        current_regime=regime,
        signal_dir=signal_dir,
    )

    score_adj = result['score_adj']
    n = result['n_similar']
    conf = result['confidence']
    long_pct = result['long_pct']
    short_pct = result['short_pct']

    context_summary = (
        f"方仓匹配n={n}({conf}) "
        f"多={long_pct*100:.0f}%空={short_pct*100:.0f}% "
        f"adj={score_adj:+.1f}"
    )

    return {
        'hcme_score_adj': score_adj,
        'context_summary': context_summary,
        'hcme_source': result['hcme_source'],
        'n_similar': n,
        'confidence': conf,
        'long_pct': long_pct,
        'short_pct': short_pct,
    }


# ════════════════════════════════════════════════════════════════════
# P1-2: 方仓自学习反馈回路（设计院 2026-08-25 苏摩111）
# 信号结算后 → 对比方仓预测vs实际 → 更新案例权重
# ════════════════════════════════════════════════════════════════════
import json as _json
import time as _time
from pathlib import Path as _Path

_WEIGHT_FILE = _Path(__file__).parent.parent / 'data' / 'fangcang_case_weights.json'


def _load_weights() -> dict:
    try:
        if _WEIGHT_FILE.exists():
            return _json.loads(_WEIGHT_FILE.read_text())
    except Exception:
        pass
    return {}


def _save_weights(w: dict):
    try:
        _WEIGHT_FILE.parent.mkdir(exist_ok=True)
        _WEIGHT_FILE.write_text(_json.dumps(w, ensure_ascii=False))
    except Exception:
        pass


def feedback_settlement(symbol: str, signal_dir: str, predicted_hint: str,
                        actual_direction: str, pnl_pct: float) -> dict:
    """
    信号结算后调用：对比方仓预测 vs 实际结果，更新案例权重。

    参数：
      symbol:           交易标的
      signal_dir:       梵天信号方向（LONG/SHORT）
      predicted_hint:   方仓预测（LONG_BIAS/SHORT_BIAS/NEUTRAL）
      actual_direction: 实际市场方向（LONG=盈利/SHORT=亏损结算）
      pnl_pct:          实际PnL百分比

    逻辑：
      预测与实际一致 → 相关案例权重 × 1.1（正确案例加权）
      预测与实际相反 → 相关案例权重 × 0.9（错误案例降权）
      权重上限2.0，下限0.1（避免极端）
    """
    weights = _load_weights()
    key = f'{symbol}:{signal_dir}'

    predicted_correct = (
        (predicted_hint == 'LONG_BIAS' and actual_direction == 'LONG' and pnl_pct > 0) or
        (predicted_hint == 'SHORT_BIAS' and actual_direction == 'SHORT' and pnl_pct > 0)
    )

    current_weight = weights.get(key, {}).get('weight', 1.0)
    if predicted_correct:
        new_weight = min(2.0, current_weight * 1.1)
        outcome = 'CORRECT'
    else:
        new_weight = max(0.1, current_weight * 0.9)
        outcome = 'WRONG'

    weights[key] = {
        'weight':    round(new_weight, 4),
        'outcome':   outcome,
        'pnl_pct':   pnl_pct,
        'ts':        _time.time(),
        'predicted': predicted_hint,
        'actual':    actual_direction,
        'count':     weights.get(key, {}).get('count', 0) + 1,
    }
    _save_weights(weights)

    return {
        'ok':         True,
        'key':        key,
        'outcome':    outcome,
        'old_weight': current_weight,
        'new_weight': new_weight,
    }


def get_feedback_stats() -> dict:
    """获取方仓自学习统计"""
    weights = _load_weights()
    if not weights:
        return {'total': 0, 'correct': 0, 'wrong': 0, 'wr': 0.0}
    correct = sum(1 for v in weights.values() if v.get('outcome') == 'CORRECT')
    wrong   = sum(1 for v in weights.values() if v.get('outcome') == 'WRONG')
    total   = correct + wrong
    return {
        'total':   total,
        'correct': correct,
        'wrong':   wrong,
        'wr':      round(correct / total * 100, 1) if total > 0 else 0.0,
        'avg_weight': round(sum(v.get('weight',1) for v in weights.values()) / max(len(weights),1), 3),
    }

# ══ [2026-09-01 设计院精简封印] 合并自 brahma_brain/hcme_matcher.py ══
# ponytail: hcme_matcher 474行，有意为之，重构前先 grep 所有调用方
"""
HCME - Historical Context Matching Engine  (M3)
================================================
Matches current signal against 410 historical signals using cosine similarity.
Pure stdlib — no numpy, no sklearn.

Author: brahma-subagent / 2026-08-08
"""

import json
import math
import os
from datetime import datetime, timezone
from typing import Optional

# ── paths ────────────────────────────────────────────────────────────────────
_DIR = os.path.dirname(os.path.abspath(__file__))
_DATA = os.path.join(_DIR, "..", "data")
SIGNAL_LOG_PATH       = os.path.join(_DATA, "live_signal_log.jsonl")
HCME_INDEX_PATH       = os.path.join(_DATA, "hcme_index.json")
# Phase1升级：伪信号历史库（2177+条，6.5年历史回测生成）
HCME_PSEUDO_PATH      = os.path.join(_DATA, "hcme", "hcme_pseudo_signals.jsonl.gz")
HCME_PSEUDO_INDEX_PATH = os.path.join(_DATA, "hcme", "hcme_pseudo_index.json")

# ── regime encoder ───────────────────────────────────────────────────────────
REGIME_MAP = {
    "BULL_TREND":    1.0,
    "BULL_EARLY":    0.7,
    "CHOP_HIGH":     0.2,
    "CHOP_MID":      0.0,
    "CHOP_LOW":     -0.2,
    "BEAR_RECOVERY": -0.5,
    "BEAR_TREND":   -1.0,
}
DIRECTION_MAP = {"LONG": 1.0, "SHORT": -1.0}

# outcome → win?
WIN_OUTCOMES  = {"TP1", "TP2", "WIN"}
LOSS_OUTCOMES = {"SL", "LOSS", "STOPPED"}


def _safe_float(v, default: float = 0.0) -> float:
    """Coerce to float, fallback to default on None/empty."""
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _cosine(a: list, b: list) -> float:
    """Cosine similarity between two equal-length lists."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# ── ATH lookup (approximate, from 4H OHLCV tail scan) ────────────────────────
_ATH_CACHE: dict = {}

# ── 模块级单例缓存（避免同一进程内重复加载2365条数据）────────────────────────
_HCME_SINGLETON: Optional['HCMEMatcher'] = None


def get_hcme_matcher() -> 'HCMEMatcher':
    """Return a cached HCMEMatcher instance (loads data only once per process)."""
    global _HCME_SINGLETON
    if _HCME_SINGLETON is None:
        _HCME_SINGLETON = HCMEMatcher()
    return _HCME_SINGLETON


def _get_ath(symbol: str) -> float:
    """Return approximate all-time-high from backtest data (up to last bar)."""
    if symbol in _ATH_CACHE:
        return _ATH_CACHE[symbol]
    candidate_files = [
        os.path.join(_DATA, "backtest", f"{symbol}_4h.json"),
        os.path.join(_DATA, "backtest", f"{symbol}_1h.json"),
    ]
    for path in candidate_files:
        if os.path.exists(path):
            try:
                with open(path) as f:
                    rows = json.load(f)
                ath = max(_safe_float(r[2]) for r in rows)  # col-2 = high
                _ATH_CACHE[symbol] = ath
                return ath
            except Exception:
                pass
    _ATH_CACHE[symbol] = 0.0
    return 0.0


class HCMEMatcher:
    """
    Matches a live signal against historical context for confidence adjustment.

    Usage
    -----
    m = HCMEMatcher()
    result = m.find_similar(signal_dict, top_k=5)
    """

    def __init__(self):
        self.signals: list[dict] = self._load_signals()
        self.index: list[dict] = self._build_or_load_index()

    # ── data loading ──────────────────────────────────────────────────────────

    def _load_signals(self) -> list[dict]:
        signals = []
        # 1. 加载实盘信号（live_signal_log.jsonl）
        if os.path.exists(SIGNAL_LOG_PATH):
            with open(SIGNAL_LOG_PATH) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            signals.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
        # 2. Phase1伪信号历史库——[P0清洗 2026-08-29 苏摩111] 已全部删除
        # 根据: 2177条全郠score<80，系统门槛=80，这些信号就不应存在。实盘WR=46.6%接近随机噪音
        # 清洗后: HCME只使用372条真实实盘信号，WR反映真实执行胜率
        pseudo_count = 0  # 保留变量避免下游引用报错
        # [P0清洗 2026-08-29 苏摩111] 伪信号加载已禁用，只用真实实盘信号
        pass  # 伪信号块已清除
        return signals

    def _build_or_load_index(self) -> list[dict]:
        """Load pre-built index or rebuild from signals."""
        if os.path.exists(HCME_INDEX_PATH):
            try:
                with open(HCME_INDEX_PATH) as f:
                    existing = json.load(f)
                if len(existing) == len(self.signals):
                    return existing
            except Exception:
                pass
        return self._build_index()

    def _build_index(self) -> list[dict]:
        """Pre-compute feature vectors for all signals and persist."""
        index = []
        for sig in self.signals:
            vec  = self.build_feature_vector(sig)
            outcome = sig.get("outcome") or sig.get("result") or "UNKNOWN"
            is_win  = outcome in WIN_OUTCOMES
            is_loss = outcome in LOSS_OUTCOMES

            regime    = sig.get("regime") or sig.get("market_regime") or "CHOP_MID"
            direction = sig.get("direction") or sig.get("signal_dir") or "LONG"

            entry = {
                "signal_id": sig.get("signal_id", ""),
                "ts":        sig.get("ts", 0),
                "symbol":    sig.get("symbol", "BTCUSDT"),
                "regime":    regime,
                "direction": direction,
                "outcome":   outcome,
                "is_win":    is_win,
                "is_loss":   is_loss,
                "score":     _safe_float(sig.get("score")),
                "pnl_pct":   _safe_float(sig.get("pnl_pct")),
                "vec":       vec,
            }
            index.append(entry)

        # Persist
        try:
            os.makedirs(os.path.dirname(HCME_INDEX_PATH), exist_ok=True)
            with open(HCME_INDEX_PATH, "w") as f:
                json.dump(index, f, separators=(",", ":"))
        except Exception as e:
            print(f"[HCME] Warning: could not persist index: {e}")

        return index

    # ── feature engineering ───────────────────────────────────────────────────

    def build_feature_vector(self, signal: dict) -> list:
        """
        Convert signal → 15-dim normalized feature vector.

        Dims:
          0  regime_enc       [-1, +1]
          1  direction_enc    {-1, +1}
          2  score_norm       [0, 1]  (score / 130)
          3  rsi_norm         [0, 1]  (rsi_4h / 100)
          4  sl_pct           [0, 1]  (sl_pct / 10)
          5  vol_ratio        [0, 1]  placeholder (0.5 if absent)
          6  oi_chg           [0, 1]  placeholder (0.5 if absent)
          7  fr               [0, 1]  placeholder (0.5 if absent)
          8  dist_ath_norm    [0, 1]  (price / ATH)
          9  atr_pct          [0, 1]  derived from sl_pct proxy
          10 bb_width         [0, 1]  placeholder
          11 hour_of_day      [0, 1]  (hour / 23)
          12 day_of_week      [0, 1]  (dow / 6)
          13 month            [0, 1]  (month / 12)
          14 bull_bear_days   [0, 1]  placeholder (0.5)
        """
        regime    = signal.get("regime") or signal.get("market_regime") or "CHOP_MID"
        direction = signal.get("direction") or signal.get("signal_dir") or "LONG"
        score     = _safe_float(signal.get("score"),   default=80.0)
        rsi_4h    = _safe_float(signal.get("rsi_4h"),  default=50.0)
        sl_pct    = _safe_float(signal.get("sl_pct"),  default=2.0)
        price     = _safe_float(signal.get("price") or signal.get("generated_price"), default=0.0)
        symbol    = signal.get("symbol", "BTCUSDT")

        # temporal
        ts = signal.get("ts") or signal.get("timestamp") or 0
        try:
            if ts:
                dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
            elif signal.get("ts_iso"):
                dt = datetime.fromisoformat(signal["ts_iso"].replace("Z", "+00:00"))
            else:
                dt = datetime.now(tz=timezone.utc)
        except Exception:
            dt = datetime.now(tz=timezone.utc)

        # ATH proximity
        ath = _get_ath(symbol)
        dist_ath = (price / ath) if ath > 0 and price > 0 else 0.5

        # atr proxy from sl
        atr_pct = min(sl_pct / 3.0, 1.0)  # rough: SL ≈ 3×ATR

        vec = [
            (REGIME_MAP.get(regime, 0.0) + 1.0) / 2.0,   # 0  → [0,1]
            (DIRECTION_MAP.get(direction, 1.0) + 1.0) / 2.0,  # 1 → [0,1]
            min(score / 130.0, 1.0),                       # 2
            rsi_4h / 100.0,                                # 3
            min(sl_pct / 10.0, 1.0),                       # 4
            0.5,                                           # 5  vol_ratio (absent)
            0.5,                                           # 6  oi_chg    (absent)
            0.5,                                           # 7  fr        (absent)
            min(dist_ath, 1.0),                            # 8
            min(atr_pct, 1.0),                             # 9
            0.5,                                           # 10 bb_width  (absent)
            dt.hour / 23.0,                                # 11
            dt.weekday() / 6.0,                            # 12
            (dt.month - 1) / 11.0,                        # 13
            0.5,                                           # 14 bull_bear_days (absent)
        ]
        return vec

    # ── similarity search ─────────────────────────────────────────────────────

    def find_similar(self, current_signal: dict, top_k: int = 5) -> dict:
        """
        Find top-k most similar historical signals via cosine similarity.

        Returns
        -------
        {
          similar_cases      : list of dicts
          historical_wr      : float   win-rate among similar cases
          confidence         : float   average similarity of top-k
          regime_wr          : float   WR for same regime+direction globally
          hcme_score_adj     : int     score adjustment (-20 ~ +20)
          context_summary    : str
        }
        """
        cur_vec   = self.build_feature_vector(current_signal)
        cur_regime    = current_signal.get("regime") or current_signal.get("market_regime") or "CHOP_MID"
        cur_direction = current_signal.get("direction") or current_signal.get("signal_dir") or "LONG"

        # score every historical entry
        # [设计院修复 2026-08-12 苏摩111封印] 方向一致性校验
        # 修复前：所有历史案例参与匹配（UP/DOWN混用），SHORT信号可能被UP案例错误加分
        # 修复后：优先匹配同方向案例；同方向案例不足top_k时，降级为全量匹配
        scored_same_dir = []
        scored_all = []
        for entry in self.index:
            sim = _cosine(cur_vec, entry["vec"])
            scored_all.append((sim, entry))
            if entry["direction"] == cur_direction:
                scored_same_dir.append((sim, entry))

        scored_all.sort(key=lambda x: x[0], reverse=True)
        scored_same_dir.sort(key=lambda x: x[0], reverse=True)

        # 同方向案例足够时优先使用，不足时降级全量（记录标志供context_summary说明）
        _dir_filtered = len(scored_same_dir) >= top_k
        top = scored_same_dir[:top_k] if _dir_filtered else scored_all[:top_k]

        # stats on top-k
        decided = [(s, e) for s, e in top if e["is_win"] or e["is_loss"]]
        if decided:
            wins_in_top = sum(1 for _, e in decided if e["is_win"])
            historical_wr = wins_in_top / len(decided)
        else:
            historical_wr = 0.5  # unknown → neutral

        confidence = sum(s for s, _ in top) / len(top) if top else 0.0

        # regime+direction global WR
        global_decided = [e for e in self.index
                          if e["regime"] == cur_regime
                          and e["direction"] == cur_direction
                          and (e["is_win"] or e["is_loss"])]
        if global_decided:
            regime_wr = sum(1 for e in global_decided if e["is_win"]) / len(global_decided)
        else:
            regime_wr = 0.5

        # score adjustment: compare historical_wr vs baseline
        baseline = regime_wr if regime_wr > 0 else 0.5
        delta = historical_wr - baseline          # -1 .. +1
        # scale to -20 .. +20, weighted by confidence
        hcme_score_adj = int(round(delta * 20.0 * min(confidence, 1.0)))
        hcme_score_adj = max(-20, min(20, hcme_score_adj))

        # [P2 样本衰减系数 2026-08-13 苏摩111封印]
        # 修复前: 5条样本和200条样本给同等权重（选择偏误）
        # 修复后: n<20条时按sqrt(n/20)衰减，WR統计意义随样本增加而增强
        #   5条样本 → 衰减系数=0.50 （据半信）
        #  10条样本 → 衰减系数=0.71
        #  20条样本 → 衰减系数=1.00 （全信）
        #  50条样本 → 衰减系数=1.00 （不超过满分）
        import math as _math
        _MIN_RELIABLE = 20
        _n_same_dir = len([e for e in self.index if e['direction'] == cur_direction])
        _decay = min(1.0, _math.sqrt(max(_n_same_dir, 1) / _MIN_RELIABLE))
        hcme_score_adj = int(round(hcme_score_adj * _decay))

        # build similar_cases list
        similar_cases = []
        for sim, entry in top:
            similar_cases.append({
                "signal_id":    entry["signal_id"],
                "ts":           entry["ts"],
                "symbol":       entry["symbol"],
                "regime":       entry["regime"],
                "direction":    entry["direction"],
                "outcome":      entry["outcome"],
                "score":        entry["score"],
                "pnl_pct":      entry["pnl_pct"],
                "similarity":   round(sim, 4),
            })

        # natural language summary
        top_outcomes = [e["outcome"] for _, e in top]
        win_pct = int(historical_wr * 100)
        adj_word = "raise" if hcme_score_adj > 0 else ("lower" if hcme_score_adj < 0 else "keep")
        _dir_note = f"dir={cur_direction} filtered" if _dir_filtered else f"fallback all-dir (same-dir cases<{top_k})"
        context_summary = (
            f"Top-{top_k} similar cases [{_dir_note}]: outcomes={top_outcomes}. "
            f"Historical WR={win_pct}% vs regime baseline={int(regime_wr*100)}%. "
            f"Confidence={confidence:.2f}. "
            f"Suggestion: {adj_word} score by {abs(hcme_score_adj)} pts "
            f"(adj={hcme_score_adj:+d})."
        )

        return {
            "similar_cases":   similar_cases,
            "historical_wr":   round(historical_wr, 4),
            "confidence":      round(confidence, 4),
            "regime_wr":       round(regime_wr, 4),
            "hcme_score_adj":  hcme_score_adj,
            "context_summary": context_summary,
        }

    def get_price_context(self, symbol: str, current_price: float) -> dict:
        """
        Return historical structure background for a given price.
        Looks at all signals for the symbol, finds those near current price (±5%).
        """
        nearby = []
        for sig in self.signals:
            if sig.get("symbol") != symbol:
                continue
            sig_price = _safe_float(sig.get("price") or sig.get("generated_price"))
            if sig_price <= 0:
                continue
            pct_diff = abs(current_price - sig_price) / sig_price * 100
            if pct_diff <= 5.0:
                outcome = sig.get("outcome") or sig.get("result") or "UNKNOWN"
                nearby.append({
                    "signal_id": sig.get("signal_id", ""),
                    "price":     sig_price,
                    "direction": sig.get("direction") or sig.get("signal_dir") or "LONG",
                    "regime":    sig.get("regime") or "CHOP_MID",
                    "outcome":   outcome,
                    "score":     _safe_float(sig.get("score")),
                    "pct_diff":  round(pct_diff, 2),
                })

        nearby.sort(key=lambda x: x["pct_diff"])

        wins  = sum(1 for n in nearby if n["outcome"] in WIN_OUTCOMES)
        losses = sum(1 for n in nearby if n["outcome"] in LOSS_OUTCOMES)
        total = wins + losses
        price_wr = (wins / total) if total > 0 else None

        ath = _get_ath(symbol)
        dist_ath_pct = ((ath - current_price) / ath * 100) if ath > 0 else None

        return {
            "symbol":          symbol,
            "current_price":   current_price,
            "ath":             round(ath, 2),
            "dist_ath_pct":    round(dist_ath_pct, 2) if dist_ath_pct is not None else None,
            "nearby_signals":  nearby[:10],
            "nearby_count":    len(nearby),
            "price_zone_wr":   round(price_wr, 4) if price_wr is not None else None,
            "price_zone_wins": wins,
            "price_zone_losses": losses,
        }


# ── CLI / smoke test ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("[HCME] Building / loading index …")
    m = HCMEMatcher()
    print(f"[HCME] Index size: {len(m.index)} entries")

    # Smoke test with synthetic signal
    test_signal = {
        "symbol":    "BTCUSDT",
        "direction": "LONG",
        "regime":    "BULL_TREND",
        "score":     88.0,
        "rsi_4h":    62.0,
        "sl_pct":    2.1,
        "price":     64300.0,
        "ts":        1783641600,
    }
    result = m.find_similar(test_signal, top_k=5)
    print("\n── find_similar smoke test ──")
    print(json.dumps(result, indent=2, default=str))

    ctx = m.get_price_context("BTCUSDT", 64300.0)
    print("\n── get_price_context smoke test ──")
    print(json.dumps(ctx, indent=2, default=str))

# ══ [2026-09-01 设计院精简封印] 合并自 brahma_brain/fangcang_experience_engine.py ══
"""
fangcang_experience_engine.py — 40年方仓经验引擎
设计院封印 2026-08-29 苏摩111

使命：
  把20392条6.5年K线案例（BTC/ETH/SOL/美股/黄金/原油）蒸馏为
  「40年顶级交易员的条件反射式经验」，注入brahma_core评分主链。

经验矩阵来源：
  data/fangcang_experience_matrix_v2.json
  - 69条规律：体制×方向×周期×RSI×burst力度
  - 最强：BULL_EARLY:LONG:4h WR=100% n=1186
  - 最弱陷阱：BEAR_EARLY:SHORT:4h WR=0% n=1161

接入位置：
  brahma_core.py → fangcang层 → fangcang_experience_engine.get_exp_adj()

核心逻辑（40年经验的三层判断）：
  Layer1: 体制方向直觉 —— 这个体制/方向，历史WR是多少？
  Layer2: 周期共振感   —— 当前触发周期，同体制+方向历史表现？
  Layer3: RSI+burst    —— 当前技术状态与历史最相似案例的WR偏差
"""

import json
import time
import logging
from pathlib import Path
from typing import Optional

_log = logging.getLogger('brahma.fangcang_exp')

BASE      = Path(__file__).parent.parent
EXP_PATH  = BASE / 'data' / 'fangcang_experience_matrix_v2.json'

_EXP_CACHE: dict = {}
_EXP_LOADED_AT: float = 0.0
_EXP_TTL = 3600  # 1小时重载一次


def _load_matrix() -> dict:
    global _EXP_CACHE, _EXP_LOADED_AT
    if _EXP_CACHE and (time.time() - _EXP_LOADED_AT) < _EXP_TTL:
        return _EXP_CACHE
    try:
        data = json.loads(EXP_PATH.read_text())
        _EXP_CACHE = data.get('matrix', {})
        _EXP_LOADED_AT = time.time()
        _log.debug(f'[exp_engine] 经验矩阵加载: {len(_EXP_CACHE)}条规律')
    except Exception as e:
        _log.warning(f'[exp_engine] 矩阵加载失败: {e}')
        _EXP_CACHE = {}
    return _EXP_CACHE


def _rsi_bucket(rsi: float) -> str:
    if rsi < 30:   return '0_30'
    if rsi < 45:   return '30_45'
    if rsi < 55:   return '45_55'
    if rsi < 70:   return '55_70'
    return '70_100'


def _burst_bucket(burst: float) -> str:
    if burst < 0.5:  return '0_0.5'
    if burst < 1.5:  return '0.5_1.5'
    return '1.5_99'


def get_exp_adj(
    regime:      str,
    signal_dir:  str,
    timeframe:   str   = '4h',
    rsi:         float = 50.0,
    burst_mult:  float = 1.0,
    current_bbw: float = 0.01,
    n_min:       int   = 10,
) -> dict:
    """
    查询经验矩阵，返回基于40年历史的评分调整。

    参数:
      regime      : 当前体制 BULL_TREND / BEAR_TREND / CHOP_MID 等
      signal_dir  : LONG / SHORT
      timeframe   : 信号触发周期 15m/1h/4h/1d
      rsi         : 当前RSI_1H
      burst_mult  : 方仓突破ATR倍数（avg_burst_atr_mult）
      current_bbw : 当前BBW

    返回:
      {
        'adj':          float,  # 评分调整（-8 ~ +8）
        'confidence':   str,    # HIGH/MED/LOW
        'rule_hit':     str,    # 命中的规律键
        'wr':           float,  # 历史WR
        'n':            int,    # 样本数
        'reasoning':    str,    # 40年交易员的判断文字
      }
    """
    matrix = _load_matrix()
    if not matrix:
        return {'adj': 0.0, 'confidence': 'NONE', 'rule_hit': '', 'wr': 0.5, 'n': 0, 'reasoning': '矩阵未加载'}

    regime = regime.upper()
    signal_dir = signal_dir.upper()
    rsi_key    = _rsi_bucket(rsi)
    burst_key  = _burst_bucket(burst_mult)
    tf_norm    = timeframe.lower().replace('min', 'm').replace('h', 'h')

    # ── 陷阱拦截（回测铁证 2026-08-29 苏摩111 n=298）──────────────────
    # BEAR_TREND:SHORT:RSI<45 WR=35% n=298 → 严重陷阱
    # 原因：BEAR_TREND低RSI=超卖随时反弹，做空在最差位置
    if regime == 'BEAR_TREND' and signal_dir == 'SHORT' and rsi < 45:
        return {
            'adj': -6.0, 'confidence': 'HIGH',
            'rule_hit': 'TRAP:BEAR_TREND:SHORT:RSI<45',
            'wr': 0.35, 'ev': -0.3, 'n': 298,
            'reasoning': 'BEAR_TREND做空+RSI<45=超卖陷阱WR=35%(n=298铁证)，等RSI>50再进入',
        }

    # BULL_EARLY:LONG:RSI<30 小样本陷阱
    if regime == 'BULL_EARLY' and signal_dir == 'LONG' and rsi < 30:
        return {
            'adj': -4.0, 'confidence': 'LOW',
            'rule_hit': 'TRAP:BULL_EARLY:LONG:RSI<30',
            'wr': 0.25, 'ev': -0.2, 'n': 8,
            'reasoning': 'BULL_EARLY做多RSI<30=假突破风险WR=25%，小样本谨慎',
        }

    # ── 三层查询，优先级：精确 > 周期 > 体制方向 ──────────────────────
    candidate_keys = [
        # 精确：体制+方向+RSI+burst（最高权重）
        f"{regime}:{signal_dir}:RSI{rsi_key}",
        f"{regime}:{signal_dir}:burst{burst_key}",
        # 周期感知：体制+方向+时间周期
        f"{regime}:{signal_dir}:{tf_norm}",
        # 基础：体制+方向
        f"{regime}:{signal_dir}",
        # Elite精华组合
        f"ELITE:{regime}:{signal_dir}:RSI{rsi_key}:burst>1.5",
    ]

    best_hit = None
    best_wr  = 0.5
    best_n   = 0
    best_key = ''

    for key in candidate_keys:
        entry = matrix.get(key)
        if not entry:
            continue
        n  = entry.get('n', 0)
        wr = entry.get('wr', 0.5)
        if n >= n_min and abs(wr - 0.5) > abs(best_wr - 0.5):
            best_hit = entry
            best_wr  = wr
            best_n   = n
            best_key = key

    if not best_hit:
        return {'adj': 0.0, 'confidence': 'NONE', 'rule_hit': '', 'wr': 0.5, 'n': 0, 'reasoning': '无匹配规律'}

    # ── 评分计算 ──────────────────────────────────────────────────────
    # WR偏离0.5越大，adj越强
    # WR=1.0 → +8  WR=0.7 → +4.8  WR=0.5 → 0  WR=0.3 → -4.8  WR=0.0 → -8
    raw_adj = (best_wr - 0.5) * 16.0
    raw_adj = max(-8.0, min(8.0, raw_adj))

    # 样本量加权
    n_weight = min(1.0, best_n / 500.0)
    adj = round(raw_adj * n_weight, 2)

    # 置信度
    if best_n >= 500:   confidence = 'HIGH'
    elif best_n >= 100: confidence = 'MED'
    else:               confidence = 'LOW'

    # 40年交易员的判断文字
    dir_cn = '做多' if signal_dir == 'LONG' else '做空'
    if best_wr >= 0.85:
        reasoning = f'{regime}体制{dir_cn}，历史胜率极高={best_wr:.0%}(n={best_n})，机构方向明确'
    elif best_wr >= 0.65:
        reasoning = f'{regime}体制{dir_cn}，历史胜率偏高={best_wr:.0%}(n={best_n})，顺势'
    elif best_wr <= 0.15:
        reasoning = f'{regime}体制{dir_cn}，历史胜率极低={best_wr:.0%}(n={best_n})，逆势死穴'
    elif best_wr <= 0.35:
        reasoning = f'{regime}体制{dir_cn}，历史胜率偏低={best_wr:.0%}(n={best_n})，逆流'
    else:
        reasoning = f'{regime}体制{dir_cn}，历史胜率中性={best_wr:.0%}(n={best_n})'

    return {
        'adj':        adj,
        'confidence': confidence,
        'rule_hit':   best_key,
        'wr':         round(best_wr, 3),
        'ev':         round(best_hit.get('ev', 0.0), 3),
        'n':          best_n,
        'reasoning':  reasoning,
    }


# ── CLI验证 ───────────────────────────────────────────────────────────
if __name__ == '__main__':
    import sys
    regime    = sys.argv[1] if len(sys.argv) > 1 else 'BULL_EARLY'
    direction = sys.argv[2] if len(sys.argv) > 2 else 'LONG'
    tf        = sys.argv[3] if len(sys.argv) > 3 else '4h'
    rsi       = float(sys.argv[4]) if len(sys.argv) > 4 else 60.0
    burst     = float(sys.argv[5]) if len(sys.argv) > 5 else 1.8

    r = get_exp_adj(regime, direction, tf, rsi, burst)
    print(f'\n=== 40年经验判断 ===')
    print(f'输入: {regime} {direction} {tf} RSI={rsi} burst={burst}x')
    print(f'adj       = {r["adj"]:+.2f}')
    print(f'confidence= {r["confidence"]}')
    print(f'rule_hit  = {r["rule_hit"]}')
    print(f'WR        = {r["wr"]:.0%}  EV={r["ev"]:+.3f}  n={r["n"]}')
    print(f'判断      = {r["reasoning"]}')

# ══ [2026-09-01 设计院精简封印] 合并自 brahma_brain/fangcang_tradfi_db.py ══
"""
fangcang_tradfi_db.py — TradFi方仓向量检索库 v1.0
设计院封印 2026-08-10 苏摩111

S级(7只): GLD/QQQ/NVDA/MSTR/AAPL/MSFT/SLV
A级(7只): WDC/MU/INTC/TSLA/GOOGL/AMD/USO
总案例: 1556个  WR=55.6%  EV=+0.725%

核心设计:
  - 正股日线K线(yfinance) → BBW标准化 → 与BTC方仓统一尺度
  - 独立Qdrant内存集合，不污染BTC/ETH/SOL库
  - 查询接口与 fangcang_vector_db 保持一致
"""
import json, time, logging
from pathlib import Path
from typing import Optional, List

import numpy as np

logger = logging.getLogger(__name__)

BASE      = Path(__file__).parent.parent
DATA_PATH = BASE / 'data' / 'fangcang_cases_tradfi.json'

# BBW标准化参数
BTC_AVG_BBW = 0.89
STOCK_AVG_BBW = {
    'GLD':0.84,'QQQ':0.72,'NVDA':2.31,'MSTR':3.12,
    'AAPL':1.19,'MSFT':1.24,'SLV':1.52,
    'WDC':2.87,'MU':2.41,'INTC':2.18,'TSLA':3.44,
    'GOOGL':1.89,'AMD':2.73,'USO':2.34,
}
# 代币 → 正股映射
TOKEN_TO_STOCK = {
    'XAUUSDT':'GLD','QQQUSDT':'QQQ','NVDAUSDT':'NVDA','MSTRUSDT':'MSTR',
    'AAPLUSDT':'AAPL','MSFTUSDT':'MSFT','XAGUSDT':'SLV',
    'SNDKUSDT':'WDC','MUUSDT':'MU','INTCUSDT':'INTC','TSLAUSDT':'TSLA',
    'GOOGLUSDT':'GOOGL','AMDUSDT':'AMD','CLUSDT':'USO',
}

# 归一化边界（TradFi BBW标准化后）
NORM_BOUNDS = {
    'bb_norm':      (0.10, 1.30),
    'squeeze_bars': (8.0,  100.0),
    'burst_atr':    (0.5,  3.5),
    'vol_ratio':    (1.0,  10.0),
    'rsi':          (15.0, 85.0),
}

TIER_CODE = {'S':1.0,'A':0.67,'B':0.33}

_client    = None
_COLL      = 'tradfi_cases'
_build_ts  = 0.0
_cases_raw: List[dict] = []


def _clip(v, lo, hi):
    return max(0.0, min(1.0, (v-lo)/(hi-lo))) if hi != lo else 0.5


def _normalize_bbw(bb_raw: float, stock: str) -> float:
    avg = STOCK_AVG_BBW.get(stock, 2.0)
    return bb_raw * (BTC_AVG_BBW / avg)


def _to_vector(c: dict) -> List[float]:
    stock  = c.get('stock_ticker', 'NVDA')
    bb_raw = c.get('min_bb_width', 1.0)
    bb_n   = c.get('min_bb_width_norm') or _normalize_bbw(bb_raw, stock)
    return [
        _clip(bb_n,              *NORM_BOUNDS['bb_norm']),
        _clip(c['squeeze_bars'], *NORM_BOUNDS['squeeze_bars']),
        _clip(c['burst_atr_mult'],*NORM_BOUNDS['burst_atr']),
        _clip(c['vol_ratio_peak'],*NORM_BOUNDS['vol_ratio']),
        _clip(c['rsi_at_burst'], *NORM_BOUNDS['rsi']),
        1.0 if c['direction'] == 'UP' else 0.0,
        1.0 if c.get('is_genuine_breakout') else 0.0,
        TIER_CODE.get(c.get('tier','A'), 0.67),
    ]


def _build():
    global _client, _cases_raw, _build_ts
    try:
        from qdrant_client import QdrantClient
        from qdrant_client.models import (
            Distance, VectorParams, PointStruct, OptimizersConfigDiff
        )
    except ImportError:
        logger.warning('qdrant_client未安装，TradFi向量检索不可用')
        return False

    if not DATA_PATH.exists():
        logger.warning('TradFi案例库不存在: %s', DATA_PATH)
        return False

    t0 = time.time()
    cases = json.loads(DATA_PATH.read_text())
    if not cases:
        return False

    # BBW标准化写入
    for c in cases:
        stock = c.get('stock_ticker', 'NVDA')
        c['min_bb_width_norm'] = round(_normalize_bbw(c['min_bb_width'], stock), 4)

    client = QdrantClient(':memory:')
    client.create_collection(
        collection_name=_COLL,
        vectors_config=VectorParams(size=8, distance=Distance.COSINE),
        optimizers_config=OptimizersConfigDiff(indexing_threshold=0),
    )

    points = []
    for idx, c in enumerate(cases):
        points.append(PointStruct(
            id=idx, vector=_to_vector(c),
            payload={k: c[k] for k in (
                'symbol','stock_ticker','tier','direction',
                'min_bb_width','min_bb_width_norm','squeeze_bars',
                'burst_atr_mult','vol_ratio_peak','rsi_at_burst',
                'future_return_24h','is_genuine_breakout',
            ) if k in c}
        ))

    for i in range(0, len(points), 500):
        client.upsert(collection_name=_COLL, points=points[i:i+500])

    _client    = client
    _cases_raw = cases
    _build_ts  = time.time()
    logger.info('TradFi向量库建立: %d案例 %.2fs', len(cases), time.time()-t0)
    return True


def _ensure():
    global _client
    if _client is None:
        _build()


def query_tradfi(
    token:        str,
    bb_width_raw: float,
    squeeze_bars: float,
    burst_atr:    float,
    vol_ratio:    float,
    rsi:          float,
    direction:    str = 'UP',
    top_k:        int = 20,
) -> dict:
    """
    查询最相似的TradFi历史方仓案例。

    参数:
      token        — Binance代币符号（如 NVDAUSDT）
      bb_width_raw — 当前BB宽度（代币原始%）
      direction    — 'UP' or 'DOWN'

    返回:
      { n, wr, wr_directional, ev, median, cases }
    """
    _ensure()
    if _client is None:
        return {'n':0,'wr':0.5,'wr_directional':0.5,'ev':0.0,'median':0.0,'cases':[]}

    try:
        stock  = TOKEN_TO_STOCK.get(token, 'NVDA')
        bb_n   = _normalize_bbw(bb_width_raw, stock)
        tier_c = 1.0  # 查询时不限制tier

        qvec = [
            _clip(bb_n,       *NORM_BOUNDS['bb_norm']),
            _clip(squeeze_bars,*NORM_BOUNDS['squeeze_bars']),
            _clip(burst_atr,  *NORM_BOUNDS['burst_atr']),
            _clip(vol_ratio,  *NORM_BOUNDS['vol_ratio']),
            _clip(rsi,        *NORM_BOUNDS['rsi']),
            1.0 if direction=='UP' else 0.0,
            1.0,  # genuine
            tier_c,
        ]

        res = _client.query_points(
            collection_name=_COLL,
            query=qvec,
            limit=top_k,
            with_payload=True,
        )
        cases  = [r.payload for r in res.points]
        rets   = np.array([c['future_return_24h'] for c in cases])
        if len(rets) == 0:
            return {'n':0,'wr':0.5,'wr_directional':0.5,'ev':0.0,'median':0.0,'cases':[]}

        wr_long  = float((rets>0).mean())
        wr_short = float((rets<0).mean())
        wr_dir   = wr_long if direction=='UP' else wr_short
        return {
            'n':              len(cases),
            'wr':             round(wr_long, 3),
            'wr_directional': round(wr_dir, 3),
            'ev':             round(float(rets.mean()), 4),
            'median':         round(float(np.median(rets)), 4),
            'p10':            round(float(np.percentile(rets,10)), 3),
            'p90':            round(float(np.percentile(rets,90)), 3),
            'cases':          cases,
        }
    except Exception as e:
        logger.warning('TradFi查询失败: %s', e)
        return {'n':0,'wr':0.5,'wr_directional':0.5,'ev':0.0,'median':0.0,'cases':[]}


def get_index_info() -> dict:
    _ensure()
    if _client is None:
        return {'status':'unavailable','n':0}
    try:
        info = _client.get_collection(_COLL)
        return {'status':'ok','n':info.points_count,
                'build_age_s': round(time.time()-_build_ts,1)}
    except Exception as e:
        return {'status':'error','error':str(e)}


# ── CLI ──────────────────────────────────────────────────────
if __name__ == '__main__':
    import sys
    logging.basicConfig(level=logging.INFO)
    print('=== TradFi方仓向量库测试 ===')
    info = get_index_info()
    print('库状态:', info)

    tests = [
        ('NVDAUSDT', 3.0, 15, 0.9, 2.5, 65, 'UP'),
        ('XAUUSDT',  1.2, 12, 0.8, 2.0, 62, 'UP'),
        ('TSLAUSDT', 4.0, 20, 1.5, 3.0, 35, 'DOWN'),
        ('AAPLUSDT', 1.5, 18, 0.7, 1.8, 68, 'UP'),
    ]
    for token, bb, sq, burst, vol, rsi, d in tests:
        t0 = time.time()
        r = query_tradfi(token, bb, sq, burst, vol, rsi, d)
        ms = (time.time()-t0)*1000
        print('%-14s bb=%.1f%% rsi=%d %s: n=%d WR=%.0f%% EV=%+.3f%% %.0fms' % (
            token, bb, rsi, d, r['n'], 100*r['wr'], r['ev'], ms))

# ══ [2026-09-01 设计院精简封印] 合并自 brahma_brain/brahma_fangcang_unified.py ══
"""
brahma_fangcang_unified.py — 梵天统一方仓查询层 v1.0
══════════════════════════════════════════════════════
设计院 2026-08-25 苏摩111 封印

使命：合并两套方仓系统，让4627条案例库真正发挥价值

根因：
  系统1 fangcang_engine    — K线滑窗扫描14467根，brahma_core已接入
  系统2 fangcang_hcme_bridge — 案例JSON 4627条，几乎没用（hcme_wr_adj=0.5微弱）
  两套系统结论矛盾，没有合并机制

解决方案：
  unified_fangcang(symbol, ms, signal_dir, regime) → unified_adj
  ├─ 系统1结果: fangcang_engine (宏观K线结构相似度) → adj1
  ├─ 系统2结果: fangcang_hcme_bridge (案例库WR匹配) → adj2
  └─ 加权合并: unified_adj = adj1×0.4 + adj2×0.6（案例库权重更高）

输出 unified_adj 替代原来的 hcme_wr_adj=0.5
"""

import os
import sys
import time
import logging
from pathlib import Path

_BASE = Path(__file__).parent
_log  = logging.getLogger('brahma.fangcang_unified')

sys.path.insert(0, str(_BASE))

# ── 系统1权重 / 系统2权重 ────────────────────────────────────────────
W1 = 0.4   # fangcang_engine K线结构权重
W2 = 0.6   # fangcang_hcme_bridge 案例库权重（质量更高）

# 最大调整幅度（防止极端值）
MAX_ADJ = 15.0
MIN_ADJ = -15.0

# 案例库字段标准化映射
_DIR_MAP = {
    'UP': 'LONG', 'DOWN': 'SHORT', 'LONG': 'LONG', 'SHORT': 'SHORT',
    'BULL': 'LONG', 'BEAR': 'SHORT', 'FLAT': 'NEUTRAL', 'CHOP': 'NEUTRAL',
}


# ── 系统1：fangcang_engine 结果解析 ──────────────────────────────────
def _get_engine_adj(symbol: str, regime: str, signal_dir: str) -> tuple:
    """
    从 fangcang_engine 的输出解析方向信号，转换为 adj 分数。
    返回 (adj: float, confidence: str, n: int)
    """
    try:
        from fangcang_engine import get_fangcang_context
        result = get_fangcang_context(symbol, current_regime=regime)
        if not result or result.get('status') == 'unavailable':
            return 0.0, 'unavailable', 0

        hint = result.get('signal_hint', 'NEUTRAL')
        pm   = result.get('prob_matrix', {})
        p_up = float(pm.get('p_up', 0.33))
        p_dn = float(pm.get('p_down', 0.33))
        n    = int(pm.get('n', 0))

        if n < 5:
            return 0.0, 'insufficient', n

        # 方向一致性得分
        if signal_dir == 'LONG':
            direction_score = p_up - p_dn   # 正=利多，负=不利
        elif signal_dir == 'SHORT':
            direction_score = p_dn - p_up   # 正=利空，负=不利
        else:
            direction_score = 0.0

        # hint强化
        hint_bonus = 0.0
        if signal_dir == 'LONG'  and hint == 'LONG_BIAS':  hint_bonus = 0.1
        if signal_dir == 'SHORT' and hint == 'SHORT_BIAS': hint_bonus = 0.1
        if signal_dir == 'LONG'  and hint == 'SHORT_BIAS': hint_bonus = -0.1
        if signal_dir == 'SHORT' and hint == 'LONG_BIAS':  hint_bonus = -0.1

        raw = (direction_score + hint_bonus) * 12.0  # 放大到[-12, +12]
        adj = max(MIN_ADJ, min(MAX_ADJ, raw))

        confidence = 'HIGH' if n >= 15 else ('MEDIUM' if n >= 8 else 'LOW')
        return round(adj, 2), confidence, n

    except Exception as e:
        _log.debug(f'[unified·s1] {e}')
        return 0.0, 'error', 0


# ── 系统2：fangcang_hcme_bridge 案例库结果解析 ────────────────────────
def _get_cases_adj(symbol: str, ms: dict, signal_dir: str, regime: str) -> tuple:
    """
    从 fangcang_hcme_bridge 案例库匹配结果，转换为 adj 分数。
    同时查询 L3 TradFi 库（贝叶斯融合），扩充小样本置信度。

    L2加密库: weight=0.7（主信号）
    L3TradFi: weight=0.3（宏观参照，样本补充）

    返回 (adj: float, confidence: str, n: int, wr: float)
    """
    try:
        from fangcang_hcme_bridge import fangcang_context_match
        # [修复 2026-08-29] bb_width多路备用：主链传入ms里字段名不一致导致bbw=None全部过滤
        bbw = (
            ms.get('bb_width') or          # brahma_core传入的小数格式
            ms.get('bbw') or               # 短字段别名
            ms.get('bb_pct') or            # 百分比格式
            (ms.get('bb') or {}).get('width') or  # 嵌套格式
            0.01                           # 最终安全値（0.01=1%，普通压缩程度）
        )
        bbw = float(bbw)
        # [2026-08-29 苏摩111修复] bbw单位归一化
        # 案例库存储格式是小数（0.005~0.12）
        # brahma_core 传入格式可能是：0.84（百分比）或 0.0084（小数）
        # 判断逻辑：>0.1 = 百分比格式，除以100转为小数
        if bbw > 0.1:
            bbw = bbw / 100
        rsi = float(ms.get('rsi_1h', ms.get('rsi', 50)) or 50)

        result = fangcang_context_match(symbol, bbw, rsi, regime, signal_dir)
        n_crypto = result.get('n_similar', 0)

        if signal_dir == 'LONG':
            wr_crypto = float(result.get('long_pct', 0.5))
        elif signal_dir == 'SHORT':
            wr_crypto = float(result.get('short_pct', 0.5))
        else:
            wr_crypto = 0.5

        # ── L3 TradFi贝叶斯融合（仅当TradFi代币有映射时启用）────────────────
        wr_final = wr_crypto
        n_final  = n_crypto
        tradfi_note = ''
        try:
            from fangcang_tradfi_db import query_tradfi, TOKEN_TO_STOCK
            if symbol in TOKEN_TO_STOCK:
                # TradFi方向映射
                tf_dir = 'UP' if signal_dir == 'LONG' else 'DOWN'
                squeeze_bars = float(ms.get('squeeze_bars', ms.get('compress_bars', 20)) or 20)
                burst_atr    = float(ms.get('atr_ratio', ms.get('burst_atr', 1.5)) or 1.5)
                vol_ratio    = float(ms.get('vol_ratio', 2.0) or 2.0)
                tf_result = query_tradfi(
                    token=symbol,
                    bb_width_raw=bbw * 100 if bbw < 1 else bbw,  # 统一为%单位
                    squeeze_bars=squeeze_bars,
                    burst_atr=burst_atr,
                    vol_ratio=vol_ratio,
                    rsi=rsi,
                    direction=tf_dir,
                    top_k=20,
                )
                n_tf = tf_result.get('n', 0)
                wr_tf = tf_result.get('wr_directional', 0.5)
                if n_tf >= 3:
                    # 贝叶斯融合：L2加密0.7 + L3TradFi0.3
                    if n_crypto >= 3:
                        wr_final = wr_crypto * 0.7 + wr_tf * 0.3
                    else:
                        # 加密样本不足时，TradFi作主要参考
                        wr_final = wr_crypto * 0.4 + wr_tf * 0.6
                    n_final  = n_crypto + int(n_tf * 0.3)  # 有效样本折算
                    tradfi_note = f' L3={wr_tf:.0%}(n={n_tf})'
                    _log.debug(f'[unified·s2] TradFi融合 {symbol} wr_crypto={wr_crypto:.2f} wr_tf={wr_tf:.2f} → wr_final={wr_final:.2f}')
        except Exception as tf_err:
            _log.debug(f'[unified·s2] TradFi跳过: {tf_err}')
        # ────────────────────────────────────────────────────────────────────

        if n_final < 3:
            return 0.0, 'insufficient', n_final, 0.0

        # WR → adj 映射（40年经验升级版）
        # WR=0.7 → +8.4  WR=0.6 → +2.4  WR=0.5 → 0  WR=0.4 → -2.4  WR=0.3 → -8.4
        adj = (wr_final - 0.5) * 24.0

        # 【新增】burst_atr_mult 加成（Top20相似案例的平均突破力度）
        # 铁证: burst_atr_mult>1.5x + UP → WR=56%~80%，平均+1.28%
        try:
            from fangcang_hcme_bridge import _FANGCANG_CACHE, _load_fangcang_cases
            _fc = result  # fangcang_context_match返回的结果已包含样本信息
            # 从原始结果读取avg_burst（若有）
            _avg_burst = float(result.get('avg_burst_atr_mult', 0) or 0)
            if _avg_burst >= 1.5:
                adj += 3.0   # 平均强突破加分
            elif _avg_burst >= 1.0:
                adj += 1.5
            elif 0 < _avg_burst < 0.5:
                adj -= 3.0   # 平均弱突破惩罚
        except Exception:
            pass

        adj = max(MIN_ADJ, min(MAX_ADJ, adj))

        # 样本量权重（n越多越可信）
        n_weight = min(1.0, n_final / 20.0)
        adj *= n_weight

        confidence = 'HIGH' if n_final >= 15 else ('MEDIUM' if n_final >= 8 else 'LOW')
        if tradfi_note:
            confidence += '+TradFi'
        return round(adj, 2), confidence, n_final, round(wr_final, 3)

    except Exception as e:
        _log.debug(f'[unified·s2] {e}')
        return 0.0, 'error', 0, 0.0


# ── 假突破惩罚 ─────────────────────────────────────────────────────────
def _genuine_breakout_weight(symbol: str, signal_dir: str) -> float:
    """
    检查案例库中假突破比例，高假突破率降低整体可信度。
    返回权重乘数 [0.5, 1.0]
    """
    try:
        import json
        # [2026-08-28 梵天设计院封印] 优先读取统一主库
        data_dir = _BASE.parent / 'data'
        merged_path = data_dir / 'fangcang_merged_v2.json'
        sym_key = symbol.replace('USDT', '').upper()
        if merged_path.exists():
            raw = json.loads(merged_path.read_text())
            all_cases = raw.get('cases', raw) if isinstance(raw, dict) else raw
            cases = [c for c in all_cases if str(c.get('symbol','')).upper() == sym_key]
        else:
            # 备用：分散加载
            fpath = data_dir / f'fangcang_cases_{sym_key.lower()}.json'
            if not fpath.exists():
                return 1.0
            cases = json.loads(fpath.read_text())
            if isinstance(cases, dict):
                cases = cases.get('cases', [])
        n = len(cases)
        if n == 0:
            return 1.0
        fake_n = sum(1 for c in cases if c.get('is_genuine_breakout') is False)
        fake_rate = fake_n / n
        # 假突破率35%→权重0.65，假突破率0%→权重1.0
        return max(0.5, 1.0 - fake_rate * 1.0)
    except Exception:
        return 1.0


# ── 主入口：统一方仓查询 ───────────────────────────────────────────────
def unified_fangcang(
    symbol:     str,
    ms:         dict,
    signal_dir: str,
    regime:     str = 'UNKNOWN',
) -> dict:
    """
    梵天统一方仓查询层。
    合并系统1（K线结构）+ 系统2（案例库WR）→ unified_adj

    参数：
      symbol:     交易对，如 BTCUSDT
      ms:         market_state dict（含bb_width/rsi_1h等）
      signal_dir: LONG / SHORT
      regime:     当前体制

    返回：
      {
        unified_adj:   float,  # 注入 score_final（替代 hcme_wr_adj=0.5）
        s1_adj:        float,  # 系统1贡献
        s2_adj:        float,  # 系统2贡献
        s1_confidence: str,
        s2_confidence: str,
        s1_n:          int,    # 系统1匹配数
        s2_n:          int,    # 系统2匹配数（案例库）
        s2_wr:         float,  # 案例库胜率
        genuine_weight: float, # 真实突破权重
        summary:       str,    # 一句话总结
      }
    """
    t0 = time.time()

    # 并行获取两套系统结果
    import concurrent.futures as cf
    with cf.ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(_get_engine_adj, symbol, regime, signal_dir)
        f2 = pool.submit(_get_cases_adj, symbol, ms, signal_dir, regime)
        try:
            s1_adj, s1_conf, s1_n   = f1.result(timeout=15)
        except Exception:
            s1_adj, s1_conf, s1_n   = 0.0, 'timeout', 0
        try:
            s2_adj, s2_conf, s2_n, s2_wr = f2.result(timeout=10)
        except Exception:
            s2_adj, s2_conf, s2_n, s2_wr = 0.0, 'timeout', 0, 0.5

    # 假突破权重（降低高假突破率案例库的影响）
    genuine_w = _genuine_breakout_weight(symbol, signal_dir)

    # 加权合并
    raw_adj = s1_adj * W1 + s2_adj * W2 * genuine_w
    unified_adj = round(max(MIN_ADJ, min(MAX_ADJ, raw_adj)), 2)

    # 置信度降级（两套都insufficient时，adj归零）
    both_low = s1_conf in ('insufficient', 'error', 'unavailable') and \
               s2_conf in ('insufficient', 'error', 'unavailable')
    if both_low:
        unified_adj = 0.0

    # 一句话总结
    dir_cn = '做多' if signal_dir == 'LONG' else '做空'
    if unified_adj > 5:
        summary = f'方仓强力确认{dir_cn}(adj={unified_adj:+.1f}): K线相似+案例库WR={s2_wr:.0%}'
    elif unified_adj > 1:
        summary = f'方仓轻微支持{dir_cn}(adj={unified_adj:+.1f}): 案例库n={s2_n} WR={s2_wr:.0%}'
    elif unified_adj < -5:
        summary = f'方仓强力反对{dir_cn}(adj={unified_adj:+.1f}): 历史数据不支持'
    elif unified_adj < -1:
        summary = f'方仓轻微反对{dir_cn}(adj={unified_adj:+.1f}): 案例库WR={s2_wr:.0%}偏低'
    else:
        summary = f'方仓中性(adj={unified_adj:+.1f}): 信号不明确'

    return {
        'unified_adj':    unified_adj,
        's1_adj':         s1_adj,
        's2_adj':         s2_adj,
        's1_confidence':  s1_conf,
        's2_confidence':  s2_conf,
        's1_n':           s1_n,
        's2_n':           s2_n,
        's2_wr':          s2_wr,
        'genuine_weight': genuine_w,
        'summary':        summary,
        'elapsed_ms':     int((time.time() - t0) * 1000),
    }


# ── CLI 验证 ─────────────────────────────────────────────────────────
if __name__ == '__main__':
    import sys
    sym = sys.argv[1] if len(sys.argv) > 1 else 'ETHUSDT'
    dr  = sys.argv[2] if len(sys.argv) > 2 else 'SHORT'
    ms_fake = {'bb_width': 0.008, 'rsi_1h': 57.0, 'rsi_4h': 68.0}
    print(f'[unified] {sym} {dr} CHOP_MID...')
    result = unified_fangcang(sym, ms_fake, dr, 'CHOP_MID')
    print(f'  unified_adj  = {result["unified_adj"]:+.2f}')
    print(f'  s1(engine)   = {result["s1_adj"]:+.2f}  conf={result["s1_confidence"]} n={result["s1_n"]}')
    print(f'  s2(cases)    = {result["s2_adj"]:+.2f}  conf={result["s2_confidence"]} n={result["s2_n"]} WR={result["s2_wr"]:.0%}')
    print(f'  genuine_w    = {result["genuine_weight"]:.2f}')
    print(f'  summary      = {result["summary"]}')
    print(f'  elapsed      = {result["elapsed_ms"]}ms')