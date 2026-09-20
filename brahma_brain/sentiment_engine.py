"""

# ── STATUS: AUXILIARY ──────────────────────────────────────────
# 情绪分析引擎，s8辅助
# LAST_REVIEW: 2026-07-01 | 属于辅助计算层，修改前确认调用链
# ─────────────────────────────────────────────────────────────
sentiment_engine.py · 梵天内置情绪引擎
[P0-B upgrade 2026-06-17] 新建 — 替代缺失的外部NLP引擎
[v1.2 upgrade 2026-06-29] FNG趋势增强 + 极度恐慌加分（设计院封印）

数据来源：
  - alternative.me FNG（实时拉取，30min缓存）
  - FNG 3日趋势（连续下降→做空加分）— 零API费用
  - 极度恐慌(FNG<15) + BEAR_TREND SHORT → 特别加分
    达摩院验证: FNG<15时做空WR+8%（迎侧探底失败率高）

接口：analyze(symbol, direction, regime=None) → dict
"""
import time
import urllib.request
import json
from typing import Optional
from data_cache import _SSL_CTX as _DC_SSL_CTX

_FG_CACHE      = {'value': 50, 'ts': 0}
_FG_HIST_CACHE = {'history': [], 'ts': 0}
_CACHE_TTL     = 1800   # 30分钟
_HIST_TTL      = 3600   # 1小时


def _get_fg() -> int:
    """get fg"""
    global _FG_CACHE
    now = time.time()
    if now - _FG_CACHE['ts'] < _CACHE_TTL:
        return _FG_CACHE['value']
    try:
        url = 'https://api.alternative.me/fng/?limit=1'
        with urllib.request.urlopen(url, timeout=4, context=_DC_SSL_CTX) as resp:
            data = json.loads(resp.read())
        val = int(data['data'][0]['value'])
        _FG_CACHE = {'value': val, 'ts': now}
        return val
    except Exception:
        return _FG_CACHE['value']


def _get_fg_history(days: int = 7) -> list:
    """FNG多日历史（用于趋势判断）"""
    global _FG_HIST_CACHE
    now = time.time()
    if now - _FG_HIST_CACHE['ts'] < _HIST_TTL and _FG_HIST_CACHE['history']:
        return _FG_HIST_CACHE['history']
    try:
        url = f'https://api.alternative.me/fng/?limit={days}'
        with urllib.request.urlopen(url, timeout=5, context=_DC_SSL_CTX) as resp:
            data = json.loads(resp.read())
        hist = [int(d['value']) for d in data.get('data', [])]
        _FG_HIST_CACHE = {'history': hist, 'ts': now}
        return hist
    except Exception:
        return _FG_HIST_CACHE['history'] or [50] * days


def _fg_trend_score(direction: str) -> float:
    """
    FNG趋势加分 v1.2 (设计院 2026-06-29)
    SHORT: FNG连续3天下降（市场情绪恶化）→ SHORT割势 +3分
           FNG已在极度恐慌区(FNG<15)并继续下降 → 额外+5分
           FNG在恐惧区(25-40)下降中 → +1.5分
    LONG:  FNG连续3天上升且>50 → LONG割势 +3分
    """
    hist = _get_fg_history(7)
    if len(hist) < 3:
        return 0.0
    recent3 = hist[:3]   # hist[0]=最新
    trend_score = 0.0

    if direction in ('SHORT', '做空'):
        if recent3[0] < recent3[1] < recent3[2]:
            trend_score += 3.0
        if recent3[0] < 15 and recent3[0] < recent3[1]:
            trend_score += 5.0
        elif 25 <= recent3[0] <= 40 and recent3[0] < recent3[1]:
            trend_score += 1.5
    else:  # LONG
        if recent3[0] > recent3[1] > recent3[2] and recent3[0] > 50:
            trend_score += 3.0

    return trend_score


def _fg_to_score(fg: int, direction: str) -> float:
    """F&G → 情绪分（设计院铁证映射表）"""
    if direction in ('SHORT', '做空'):
        if   fg <= 15: return 5.0
        elif fg <= 25: return 4.0
        elif fg <= 35: return 3.0
        elif fg <= 45: return 2.0
        elif fg <= 55: return 0.0
        elif fg <= 65: return -1.0
        elif fg <= 75: return -2.0
        else:          return -3.0
    else:  # LONG
        if   fg <= 15: return -3.0
        elif fg <= 25: return -2.0
        elif fg <= 35: return -1.0
        elif fg <= 45: return 0.0
        elif fg <= 55: return 1.0
        elif fg <= 65: return 2.0
        elif fg <= 75: return 3.0
        else:          return 4.0


def analyze(symbol: str, direction: str, regime: Optional[str] = None) -> dict:
    """主接口：返回 sentiment_nlp 标准字典"""
    fg      = _get_fg()
    base_s  = _fg_to_score(fg, direction)
    trend_s = _fg_trend_score(direction)
    total   = base_s + trend_s

    extreme_fear_bonus = (fg < 15 and direction in ('SHORT', '做空'))

    return {
        'score':          total,
        'score_base':     base_s,
        'score_trend':    trend_s,
        'fng_value':      fg,
        'extreme_fear':   extreme_fear_bonus,
        'news_count':     0,
        'source':         'fg_internal_v1.2',
        'symbol':         symbol,
        'direction':      direction,
    }


def get_sentiment_score(ms: dict, signal_dir: str) -> tuple:
    """
    兼容接口：供 brahma_core_step4.py 调用
    [P1-A修复 2026-09-03 苏摩111] 接入sentiment_engine
    [FIX-6 2026-09-18] 补 FR + LSR 评分逻辑，从 ms.sentiment 读取
    """
    symbol = ms.get('symbol', 'BTCUSDT') if isinstance(ms, dict) else 'BTCUSDT'
    regime = ms.get('regime', '') if isinstance(ms, dict) else ''

    # ── 从 ms.sentiment 读取 funding_rate / long_short_ratio ──
    sentiment = ms.get('sentiment', {}) if isinstance(ms, dict) else {}
    fr = sentiment.get('funding_rate', sentiment.get('funding', 0)) or 0
    lsr = sentiment.get('long_short_ratio', sentiment.get('lsr', 1.0)) or 1.0
    oi_mom = sentiment.get('oi_momentum', 'NEUTRAL')

    # ── FNG 基础分（离线时回退到缓存值）──
    fg = _get_fg()
    base_s = _fg_to_score(fg, signal_dir)
    trend_s = _fg_trend_score(signal_dir)
    fg_total = base_s + trend_s

    # ── FR 评分（资金费率）──
    fr_score = 0
    if signal_dir in ('SHORT', '做空'):
        if fr > 0.0003:   # >0.03% 多头拥挤，做空有利
            fr_score = 4
        elif fr > 0.0001:
            fr_score = 2
        elif fr < -0.0001:  # 负费率空头拥挤，做空不利
            fr_score = -2
    else:  # LONG
        if fr < -0.0001:  # 负费率空头拥挤，做多有利
            fr_score = 4
        elif fr < 0.0001:
            fr_score = 2
        elif fr > 0.0003:  # 高费率多头拥挤，做多不利
            fr_score = -2

    # ── LSR 评分（多空比拥挤度）──
    lsr_score = 0
    lsr_detail = ''
    if signal_dir in ('SHORT', '做空'):
        if lsr > 1.5:       # 多头拥挤，做空有利
            lsr_score = 3
            lsr_detail = 'lsr_crowded_long'
        elif lsr > 1.2:
            lsr_score = 1
    else:  # LONG
        if lsr < 0.7:       # 空头拥挤，做多有利
            lsr_score = 3
            lsr_detail = 'lsr_crowded_short'
        elif lsr < 0.9:
            lsr_score = 1

    # ── 合成 ──
    total = int(fg_total + fr_score + lsr_score)
    total = max(-8, min(8, total))  # 钳位到 [-8, 8]

    detail = (f"FnG={fg} base={base_s:+.1f} trend={trend_s:+.1f} "
              f"fr={fr:.4f}({fr_score:+d}) lsr={lsr:.2f}({lsr_score:+d}) {lsr_detail}".strip())

    return total, detail
