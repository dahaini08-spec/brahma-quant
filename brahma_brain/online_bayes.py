#!/usr/bin/env python3
"""

# ── STATUS: AUXILIARY ──────────────────────────────────────────
# 在线贝叶斯更新，s14维度
# LAST_REVIEW: 2026-07-01 | 属于辅助计算层，修改前确认调用链
# ─────────────────────────────────────────────────────────────
online_bayes.py · 梵天在线贝叶斯引擎
[P0-B upgrade 2026-06-17]

原理：贝叶斯后验 WR = (alpha + wins) / (alpha + beta + total)
  - 先验：来自8年回测铁证矩阵（alpha/beta）
  - 似然：来自实盘 live_signal_log 经验池（wins/total）
  - 后验评分：(posterior_wr - prior_wr) × sensitivity → 贝叶斯增量分

接口：score(symbol, regime, direction, score_raw) → (adj_score, detail)
"""
import json, os, time
from pathlib import Path
from collections import defaultdict

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_FILE = os.path.join(BASE_DIR, 'data', 'live_signal_log.jsonl')
CACHE_FILE = os.path.join(BASE_DIR, 'data', 'bayes_cache.json')
CACHE_TTL = 3600  # 1小时刷新一次经验池

# ── 先验矩阵（8年回测铁证，n≥1000）──────────────────────────
# (alpha, beta) = (prior_wins, prior_losses)，等价样本量=alpha+beta
# WR先验 = alpha / (alpha + beta)
PRIOR = {
    'BULL_TREND_LONG':      (703, 297),   # WR=70.3% n=3046铁证 → 等价n=1000
    'BULL_TREND_SHORT':     (477, 523),   # WR=47.7% 死穴
    'BULL_EARLY_LONG':      (644, 356),   # WR=64.4% n=5396铁证
    'BULL_EARLY_SHORT':     (519, 481),   # WR=51.9% 负期望
    'BULL_CORRECTION_LONG': (461, 539),   # WR=46.1% 死穴
    'BULL_CORRECTION_SHORT':(739, 261),   # WR=73.9% n=494
    'BEAR_TREND_LONG':      (450, 550),   # WR=45.0% 最惨死穴
    'BEAR_TREND_SHORT':     (718, 282),   # WR=71.8% n=2413铁证
    'BEAR_EARLY_LONG':      (504, 496),   # WR=50.4% 负期望
    'BEAR_EARLY_SHORT':     (665, 335),   # WR=66.5% n=5896铁证
    'BEAR_RECOVERY_LONG':   (725, 275),   # WR=72.5% n=430
    'BEAR_RECOVERY_SHORT':  (479, 521),   # WR=47.9% 死穴
    'CHOP_MID_LONG':        (572, 428),   # WR=57% 负期望（手续费）
    'CHOP_MID_SHORT':       (572, 428),
    'CHOP_HIGH_LONG':       (500, 500),
    'CHOP_HIGH_SHORT':      (500, 500),
}
DEFAULT_PRIOR = (550, 450)  # 未知体制/方向：中性先验

# 评分灵敏度：后验WR偏离先验WR每1%对应多少分
SENSITIVITY = 0.3  # 最大贡献：±8分（后验偏离±27%时）
MAX_ADJ = 8.0
MIN_POOL_N = 5  # 经验池至少5条才参与后验


_cache = {'data': {}, 'ts': 0}

def _load_experience_pool():
    """从 live_signal_log.jsonl 构建经验池"""
    global _cache
    now = time.time()
    if now - _cache['ts'] < CACHE_TTL and _cache['data']:
        return _cache['data']

    pool = defaultdict(lambda: {'wins': 0, 'total': 0})
    try:
        if not os.path.exists(LOG_FILE):
            return {}
        for line in open(LOG_FILE):
            line = line.strip()
            if not line:
                continue
            try:
                s = json.loads(line)
            except:
                continue
            # 排除 LEGACY 污染数据
            if s.get('_data_quality'):
                continue
            outcome = s.get('outcome', '')
            if not outcome:
                continue
            regime    = s.get('regime', '')
            direction = s.get('direction', '') or s.get('signal_dir', '')
            if not regime or not direction:
                continue
            key = f"{regime}_{direction}"
            # [P0 2026-06-30] 扩展胜利outcome识别，覆盖全部真实结算格式
            WIN_OUTCOMES  = ('TP1','TP2','TP3','WIN','HIT_TP1','HIT_TP2',
                             'MISS_WIN')                  # MISS_WIN=方向正确未入场
            LOSS_OUTCOMES = ('SL','LOSS','SL_BREACHED',
                             'MISS_LOSS')                 # MISS_LOSS=方向错误未入场
            if outcome in WIN_OUTCOMES:
                pool[key]['total'] += 1
                pool[key]['wins']  += 1
            elif outcome in LOSS_OUTCOMES:
                pool[key]['total'] += 1
            # REGIME_EXPIRED/EXPIRED/PRICE_EXPIRED → 不计入经验池（方向无法判断）
    except Exception as e:
        pass  # [静默]
        return {}

    _cache = {'data': dict(pool), 'ts': now}
    return _cache['data']


def score(symbol: str, regime: str, direction: str, score_raw: float = 0) -> tuple:
    """
    贝叶斯增量评分
    Returns: (adj_score, detail_dict)
    """
    pool = _load_experience_pool()
    key = f"{regime}_{direction}"

    # 先验
    alpha, beta = PRIOR.get(key, DEFAULT_PRIOR)
    prior_wr = alpha / (alpha + beta)

    # 似然（经验池）
    exp = pool.get(key, {})
    exp_wins  = exp.get('wins', 0)
    exp_total = exp.get('total', 0)

    # 后验：贝叶斯更新
    post_alpha = alpha + exp_wins
    post_beta  = beta  + (exp_total - exp_wins)
    post_wr    = post_alpha / (post_alpha + post_beta)

    # 增量分
    delta_wr  = post_wr - prior_wr          # 后验偏离先验
    adj_score = round(delta_wr * 100 * SENSITIVITY, 1)
    adj_score = max(-MAX_ADJ, min(adj_score, MAX_ADJ))

    # 经验池不足时降权
    if exp_total < MIN_POOL_N:
        adj_score *= 0.3  # 先验主导，增量贡献小
        adj_score = round(adj_score, 1)

    detail = {
        'key':        key,
        'prior_wr':   round(prior_wr * 100, 1),
        'post_wr':    round(post_wr * 100, 1),
        'exp_n':      exp_total,
        'exp_wr':     round(exp_wins / exp_total * 100, 1) if exp_total > 0 else None,
        'adj_score':  adj_score,
        'confidence': 'HIGH' if exp_total >= 30 else ('MED' if exp_total >= MIN_POOL_N else 'LOW'),
    }
    return adj_score, detail


def get_regime_prior_wr(regime: str, direction: str) -> float:
    """仅返回先验WR（用于门控判断）"""
    key = f"{regime}_{direction}"
    alpha, beta = PRIOR.get(key, DEFAULT_PRIOR)
    return alpha / (alpha + beta) * 100
