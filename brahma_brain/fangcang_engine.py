"""
fangcang_engine.py — 方仓经验引擎 v1.0
设计院封印 2026-08-07 · 苏摩111批准

功能：
  1. 读取方仓6.8年历史K线（4H为主）
  2. DTW相似度扫描：当前1周形态 → 历史最相似案例
  3. 输出概率矩阵（做多/做空/震荡概率 + 期望收益）
  4. 集成到 brahma_engine.analyze() → _result['fangcang'] 字段

设计原则（梵天宪法）：
  - 最简实现：纯stdlib + 已安装的gzip/json
  - 唯一入口：brahma_engine 调用 get_fangcang_context()
  - 结果缓存：TTL=30min（通过brahma_bus）
  - 失败降级：任何异常 → 返回 {'status': 'unavailable'}
"""

import gzip
import json
import math
import os
import time
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

# ── 路径配置（修复 2026-08-08 设计院）─────────────────────
_BASE = Path(__file__).parent.parent
_DATA_DIR_LEGACY = _BASE / "data" / "historical"   # 旧路径（保留兼容）
_DATA_DIR_BACKTEST = _BASE / "data" / "backtest"   # 新路径（实际数据在这里）


def _load_klines_native(symbol: str, tf: str) -> List[dict]:
    """
    读取 data/backtest/{symbol}_{tf}.json
    原生格式: [[ts_ms, o, h, l, c, v, ...], ...]
    转为 [{"ts": ms, "o": f, "h": f, "l": f, "c": f, "v": f}, ...]
    """
    path = _DATA_DIR_BACKTEST / f"{symbol}_{tf}.json"
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text())
        bars = []
        for r in raw:
            bars.append({
                "ts": int(r[0]),
                "o": float(r[1]),
                "h": float(r[2]),
                "l": float(r[3]),
                "c": float(r[4]),
                "v": float(r[5]),
            })
        return bars
    except Exception:
        return []

# ── 缓存层（内存级，TTL=30min）─────────────────────────────
_CACHE: Dict[str, dict] = {}
_CACHE_TTL = 1800  # 30分钟

# ── 参数常量 ────────────────────────────────────────────────
WEEK_BARS   = 42   # 1周 = 42根4H K线
FUTURE_BARS = 42   # 预测未来1周
SCAN_STEP   = 4    # 每4根滑动一次（减少重叠，提高速度）
TOP_N       = 20   # 取最相似TOP20
TP_PCT      = 3.0  # 标准TP%
SL_PCT      = 2.0  # 标准SL%


# ══════════════════════════════════════════════════════════
# 数据加载
# ══════════════════════════════════════════════════════════

def _load_klines(symbol: str, tf: str) -> List[dict]:
    """从方仓加载K线，优先 data/backtest/ 原生格式，失败返回[]"""
    # [设计院修复 2026-08-08] 优先读 data/backtest/ 原生格式
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


# ══════════════════════════════════════════════════════════
# 特征计算
# ══════════════════════════════════════════════════════════

def _calc_rsi(prices: List[float], period: int = 14) -> float:
    if len(prices) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(prices)):
        d = prices[i] - prices[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    if al == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + ag / al)


def _extract_features(bars: List[dict]) -> dict:
    """提取一段K线的10维特征向量"""
    closes = [float(b['c']) for b in bars]
    highs  = [float(b['h']) for b in bars]
    lows   = [float(b['l']) for b in bars]
    vols   = [float(b['v']) for b in bars]

    c0 = closes[0] if closes[0] != 0 else 1.0
    norm_closes = [(c - c0) / c0 * 100.0 for c in closes]

    amp_seq = [(h - l) / c0 * 100.0 for h, l in zip(highs, lows)]
    avg_amp = sum(amp_seq) / len(amp_seq) if amp_seq else 0
    amp_std = math.sqrt(sum((a - avg_amp) ** 2 for a in amp_seq) / len(amp_seq)) if amp_seq else 0

    avg_vol = sum(vols) / len(vols) if vols else 1.0
    vol_ratio_last = vols[-1] / avg_vol if avg_vol > 0 else 1.0

    rsi_end = _calc_rsi(closes)

    return {
        'norm_closes':  norm_closes,
        'total_move':   norm_closes[-1],
        'max_drawdown': min(norm_closes),
        'max_gain':     max(norm_closes),
        'amplitude':    (max(highs) - min(lows)) / c0 * 100.0,
        'amp_std':      amp_std,
        'rsi_end':      rsi_end,
        'vol_ratio':    vol_ratio_last,
        'n':            len(bars),
    }


# ══════════════════════════════════════════════════════════
# 相似度计算（快速欧式 + 关键因子加权）
# ══════════════════════════════════════════════════════════

def _similarity_score(feat_cur: dict, feat_hist: dict) -> float:
    """
    综合相似度得分（越小越相似）
    权重：价格形态50% + 振幅15% + 移动15% + RSI20%
    """
    # 价格形态：快速欧式距离（避免完整DTW的O(n²)开销）
    s1 = feat_cur['norm_closes']
    s2 = feat_hist['norm_closes']
    n  = min(len(s1), len(s2))
    price_dist = math.sqrt(sum((s1[i] - s2[i]) ** 2 for i in range(n))) / n

    # 其他因子
    amp_diff  = abs(feat_cur['amplitude']  - feat_hist['amplitude'])
    move_diff = abs(feat_cur['total_move'] - feat_hist['total_move'])
    rsi_diff  = abs(feat_cur['rsi_end']    - feat_hist['rsi_end']) / 100.0

    return (
        price_dist * 0.50
        + amp_diff  * 0.15
        + move_diff * 0.15
        + rsi_diff  * 20.0 * 0.20   # 归一到相近量级
    )


# ══════════════════════════════════════════════════════════
# 核心：历史扫描 + 概率矩阵
# ══════════════════════════════════════════════════════════

def _scan_history(
    klines: List[dict],
    regime_map: Dict[int, str],
    current_regime: str,
) -> List[dict]:
    """
    扫描历史，返回最相似TOP_N案例列表
    每条包含：dt / score / future_ret / future_max / future_min / regime
    """
    # 当前特征
    recent = klines[-WEEK_BARS:]
    feat_cur = _extract_features(recent)

    results = []
    total = len(klines)

    for start in range(100, total - WEEK_BARS - FUTURE_BARS, SCAN_STEP):
        hist_bars  = klines[start : start + WEEK_BARS]
        feat_hist  = _extract_features(hist_bars)
        score      = _similarity_score(feat_cur, feat_hist)

        # 未来结果
        future_bars   = klines[start + WEEK_BARS : start + WEEK_BARS + FUTURE_BARS]
        future_closes = [float(b['c']) for b in future_bars]
        entry_price   = float(hist_bars[-1]['c'])
        if entry_price == 0:
            continue

        future_ret = (future_closes[-1] - entry_price) / entry_price * 100.0
        future_max = (max(float(b['h']) for b in future_bars) - entry_price) / entry_price * 100.0
        future_min = (min(float(b['l']) for b in future_bars) - entry_price) / entry_price * 100.0

        ts     = hist_bars[-1]['ts']
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

    # 排序取TOP_N
    results.sort(key=lambda x: x['score'])
    return results[:TOP_N]


def _build_probability_matrix(top: List[dict]) -> dict:
    """从TOP_N历史案例构建概率矩阵"""
    if not top:
        return {'p_up': 0.5, 'p_down': 0.2, 'p_flat': 0.3, 'ev': 0.0, 'n': 0}

    rets = [s['future_ret'] for s in top]
    n    = len(rets)

    up   = sum(1 for r in rets if r >  2.0)
    dn   = sum(1 for r in rets if r < -2.0)
    flat = n - up - dn

    ev     = sum(rets) / n
    median = sorted(rets)[n // 2]
    tail_down = sum(1 for s in top if s['future_min'] < -10.0)

    return {
        'p_up':        round(up   / n, 3),
        'p_down':      round(dn   / n, 3),
        'p_flat':      round(flat / n, 3),
        'ev':          round(ev, 3),
        'median':      round(median, 3),
        'max_upside':  round(max(s['future_max'] for s in top), 2),
        'max_downside':round(min(s['future_min'] for s in top), 2),
        'tail_down_risk': round(tail_down / n, 3),
        'n':           n,
    }


# ══════════════════════════════════════════════════════════
# 公开接口
# ══════════════════════════════════════════════════════════

def get_fangcang_context(
    symbol: str = 'BTCUSDT',
    current_regime: Optional[str] = None,
) -> dict:
    """
    主接口：返回方仓经验引擎完整结果。
    供 brahma_engine.analyze() 调用，结果注入 _result['fangcang']

    返回结构：
    {
      'status': 'ok' | 'unavailable',
      'symbol': 'BTCUSDT',
      'run_at': ISO时间戳,
      'current_regime': str,
      'top_similar': [ {dt, score, future_ret, future_max, future_min, regime}, ... ],
      'prob_matrix': {p_up, p_down, p_flat, ev, median, max_upside, max_downside, tail_down_risk, n},
      'signal_hint': 'LONG_BIAS' | 'SHORT_BIAS' | 'NEUTRAL' | 'WAIT',
      'top3_summary': str,   # 给信号卡片展示的3行文字摘要
    }
    """
    cache_key = f"{symbol}:{current_regime}"
    now = time.time()

    # 检查缓存
    if cache_key in _CACHE:
        cached = _CACHE[cache_key]
        if now - cached.get('_ts', 0) < _CACHE_TTL:
            return cached

    try:
        # 加载数据
        klines     = _load_klines(symbol, '4h')
        regime_map = _load_regime_map(symbol)

        if len(klines) < WEEK_BARS + FUTURE_BARS + 100:
            return {'status': 'unavailable', 'reason': 'insufficient_data'}

        # 当前体制（外部传入优先，其次读 regime_state.json SSOT，最后fallback旧map）
        if not current_regime:
            try:
                import json as _json
                _rs = _json.loads((_BASE / 'data' / 'regime_state.json').read_text())
                _sym_state = _rs.get(symbol, {})
                current_regime = (
                    _sym_state.get('confirmed') or
                    _sym_state.get('regime') or
                    'UNKNOWN'
                )
            except Exception:
                last_ts = klines[-1]['ts']
                current_regime = regime_map.get(last_ts, 'UNKNOWN')

        # 扫描历史相似案例
        top_similar = _scan_history(klines, regime_map, current_regime)

        # 概率矩阵
        prob = _build_probability_matrix(top_similar)

        # 信号偏向
        if prob['p_up'] >= 0.60 and prob['ev'] > 0:
            hint = 'LONG_BIAS'
        elif prob['p_down'] >= 0.50 and prob['ev'] < 0:
            hint = 'SHORT_BIAS'
        elif prob['tail_down_risk'] >= 0.25:
            hint = 'WAIT'
        else:
            hint = 'NEUTRAL'

        # TOP3文字摘要（给信号卡片用）
        top3_lines = []
        for s in top_similar[:3]:
            arrow = '↑' if s['future_ret'] > 0 else '↓'
            top3_lines.append(
                f"  {s['dt']} [{s['regime'][:4]}] {arrow}{abs(s['future_ret']):.1f}% "
                f"(最高{s['future_max']:+.1f}% 最低{s['future_min']:+.1f}%)"
            )
        top3_summary = '\n'.join(top3_lines)

        result = {
            'status':         'ok',
            'symbol':         symbol,
            'run_at':         datetime.now(timezone.utc).isoformat(),
            'current_regime': current_regime,
            'top_similar':    top_similar,
            'prob_matrix':    prob,
            'signal_hint':    hint,
            'top3_summary':   top3_summary,
            '_ts':            now,
        }

        _CACHE[cache_key] = result
        return result

    except Exception as e:
        return {
            'status':  'unavailable',
            'reason':  str(e)[:120],
            '_ts':     now,
        }


def format_fangcang_card(fc: dict) -> str:
    """
    格式化方仓摘要，嵌入信号卡片（单行或多行）
    """
    if fc.get('status') != 'ok':
        return ''

    pm = fc.get('prob_matrix', {})
    hint = fc.get('signal_hint', 'NEUTRAL')
    hint_icons = {
        'LONG_BIAS':  '📈',
        'SHORT_BIAS': '📉',
        'WAIT':       '⏳',
        'NEUTRAL':    '⚖️',
    }
    icon = hint_icons.get(hint, '⚖️')

    lines = [
        f"━━ 🏛️ 方仓经验引擎 (6.8年/{pm.get('n', 0)}案例) {icon} ━━",
        f"  ↑{pm.get('p_up',0)*100:.0f}% ↓{pm.get('p_down',0)*100:.0f}% "
        f"↔{pm.get('p_flat',0)*100:.0f}%  EV={pm.get('ev',0):+.2f}%  "
        f"尾部风险={pm.get('tail_down_risk',0)*100:.0f}%",
        "  最相似历史案例:",
        fc.get('top3_summary', '  (无数据)'),
    ]
    return '\n'.join(lines)


# ══════════════════════════════════════════════════════════
# 冒烟测试
# ══════════════════════════════════════════════════════════

if __name__ == '__main__':
    print("=== fangcang_engine 冒烟测试 ===\n")

    import time as _t
    t0 = _t.time()

    result = get_fangcang_context('BTCUSDT')
    elapsed = _t.time() - t0

    print(f"状态: {result['status']}")
    if result['status'] == 'ok':
        pm = result['prob_matrix']
        print(f"体制: {result['current_regime']}")
        print(f"信号偏向: {result['signal_hint']}")
        print(f"概率矩阵: ↑{pm['p_up']*100:.0f}% ↓{pm['p_down']*100:.0f}% ↔{pm['p_flat']*100:.0f}%")
        print(f"期望收益: {pm['ev']:+.3f}%  中位: {pm['median']:+.3f}%")
        print(f"尾部风险(跌>10%): {pm['tail_down_risk']*100:.0f}%")
        print(f"\nTOP5相似案例:")
        for s in result['top_similar'][:5]:
            print(f"  {s['dt']} score={s['score']:.3f} ret={s['future_ret']:+.1f}% [{s['regime']}]")
        print(f"\n信号卡片格式:")
        print(format_fangcang_card(result))
    else:
        print(f"原因: {result.get('reason','?')}")

    print(f"\n耗时: {elapsed:.2f}s")
    print("\n✅ 冒烟测试完成")
