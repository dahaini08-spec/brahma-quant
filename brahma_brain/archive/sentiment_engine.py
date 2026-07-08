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

_FG_CACHE      = {'value': 50, 'ts': 0}
_FG_HIST_CACHE = {'history': [], 'ts': 0}
_CACHE_TTL     = 1800   # 30分钟
_HIST_TTL      = 3600   # 1小时


def _get_fg() -> int:
    global _FG_CACHE
    now = time.time()
    if now - _FG_CACHE['ts'] < _CACHE_TTL:
        return _FG_CACHE['value']
    try:
        url = 'https://api.alternative.me/fng/?limit=1'
        with urllib.request.urlopen(url, timeout=4) as resp:
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
        with urllib.request.urlopen(url, timeout=5) as resp:
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
