"""
fangcang_vector_db.py — 历史引擎向量统计接口
设计院封印 2026-09-03 苏摩111

接入位置：fangcang_engine.py line 964（query_stats调用）
          brahma_core.py line 964（vector_stats增强层）

功能：用已有的历史引擎（_scan_history / HCME）提供向量统计，
     不依赖qdrant_client（未安装），直接从方仓JSON数据计算。

接口与原fangcang_vector_db保持一致（兼容brahma_wiring_check注册表）。
"""

import os
import json
import math
import time
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / 'data'
FANG_SIGNALS_PATH = DATA_DIR / 'fangcang_signals.json'
HCME_INDEX_PATH   = DATA_DIR / 'hcme_index_cache.json'

# ── 简单余弦相似度（无qdrant） ───────────────────────────────
def _cosine(a: list, b: list) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na  = math.sqrt(sum(x * x for x in a)) or 1e-9
    nb  = math.sqrt(sum(x * x for x in b)) or 1e-9
    return dot / (na * nb)

def _build_query_vector(
    bb_width: float = 1.0,
    squeeze_bars: int = 42,
    burst_atr: float = 1.0,
    vol_ratio: float = 2.0,
    rsi: float = 50.0,
    direction: str = 'UP',
) -> list:
    """构造与 hcme_matcher.build_feature_vector 兼容的查询向量（15维）"""
    dir_enc = 1.0 if direction == 'UP' else -1.0
    return [
        0.0,                          # regime_enc (未知)
        dir_enc,                      # direction
        0.5,                          # score_norm (未知)
        rsi / 100.0,                  # rsi_norm
        0.02,                         # sl_pct proxy
        min(vol_ratio / 5.0, 1.0),   # vol_ratio
        0.5,                          # oi_chg
        0.5,                          # fr
        0.5,                          # dist_ath
        min(burst_atr / 5.0, 1.0),   # atr_pct
        min(bb_width / 0.1, 1.0),    # bb_width
        0.5,                          # hour
        0.5,                          # dow
        0.5,                          # month
        0.5,                          # bull_bear_days
    ]

def query_stats(
    bb_width: float = 1.0,
    squeeze_bars: int = 42,
    burst_atr: float = 1.0,
    vol_ratio: float = 2.0,
    rsi: float = 50.0,
    direction: str = 'UP',
    symbol: Optional[str] = None,
    top_k: int = 20,
) -> dict:
    """
    从方仓历史索引中查询最相似TOP-K案例，返回统计摘要。

    Returns:
        {
            'top_k': int,
            'matched': int,
            'win_rate': float,
            'avg_ret': float,
            'max_ret': float,
            'min_ret': float,
            'cases': [{'score': float, 'regime': str, 'direction': str, 'pnl_pct': float}],
            'source': 'hcme_index' | 'empty',
        }
    """
    empty = {
        'top_k': top_k, 'matched': 0, 'win_rate': 0.0,
        'avg_ret': 0.0, 'max_ret': 0.0, 'min_ret': 0.0,
        'cases': [], 'source': 'empty',
    }

    # 尝试加载HCME索引
    index = []
    if HCME_INDEX_PATH.exists():
        try:
            age = time.time() - HCME_INDEX_PATH.stat().st_mtime
            if age < 86400 * 7:  # 7天内有效
                with open(HCME_INDEX_PATH) as f:
                    index = json.load(f)
        except Exception:
            pass

    if not index:
        return empty

    query_vec = _build_query_vector(bb_width, squeeze_bars, burst_atr, vol_ratio, rsi, direction)

    # 计算相似度
    scored = []
    for entry in index:
        vec = entry.get('vec')
        if not vec or len(vec) != 15:
            continue
        if symbol and entry.get('symbol', '').replace('USDT', '') != symbol:
            continue
        sim = _cosine(query_vec, vec)
        scored.append((sim, entry))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:top_k]

    if not top:
        return empty

    wins = sum(1 for _, e in top if e.get('is_win'))
    pnls = [e.get('pnl_pct', 0.0) for _, e in top if e.get('pnl_pct') is not None]

    return {
        'top_k':     top_k,
        'matched':   len(top),
        'win_rate':  round(wins / len(top), 3) if top else 0.0,
        'avg_ret':   round(sum(pnls) / len(pnls), 4) if pnls else 0.0,
        'max_ret':   round(max(pnls), 4) if pnls else 0.0,
        'min_ret':   round(min(pnls), 4) if pnls else 0.0,
        'cases':     [
            {
                'score':     round(sim, 4),
                'regime':    e.get('regime', ''),
                'direction': e.get('direction', ''),
                'pnl_pct':   e.get('pnl_pct', 0.0),
                'outcome':   e.get('outcome', ''),
            }
            for sim, e in top[:5]
        ],
        'source': 'hcme_index',
    }
