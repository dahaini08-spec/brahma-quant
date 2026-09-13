#!/usr/bin/env python3
"""
dag_executor.py — 轻量DAG执行器
[果蝇架构Phase 1] Langflow借鉴4+5：图即配置 + DAG引擎

功能：
  1. 读取scoring_config.json的稀疏激活规则（带热更新）
  2. 在confluence_score的Block A/B/C计算后，对s1-s22做稀疏过滤
  3. sleep维度归零，active维度按config权重加权
  4. 返回更新后的维度分数+总分+position_mult

接入位置：brahma_core.py confluence_score() Block C之后、regime_mult之前
调用方式：
    from brahma_brain.dag_executor import apply_sparse_activation
    _dag = apply_sparse_activation({'s1': s1, 's2': s2, ...}, regime, signal_dir)
    s1, s2, ... = _dag['dims']['s1'], _dag['dims']['s2'], ...
    score = _dag['score']

设计原则：
  - <200行（轻量）
  - 不改Block A/B/C内部逻辑（只做后处理过滤）
  - config热更新（mtime检测，无需重启）
  - 未在config中声明的维度保持原样（不丢信息）
"""
import json
import time
from pathlib import Path

CONFIG_FILE = Path(__file__).parent.parent / 'data' / 'scoring_config.json'
_config_cache = None
_config_mtime = 0.0

# ── 配置加载（带mtime热更新） ──────────────────────────────────

def _load_config() -> dict:
    """加载scoring_config，mtime变化时自动重载"""
    global _config_cache, _config_mtime
    try:
        mtime = CONFIG_FILE.stat().st_mtime
        if _config_cache is None or mtime != _config_mtime:
            _config_cache = json.loads(CONFIG_FILE.read_text())
            _config_mtime = mtime
        return _config_cache
    except Exception as e:
        import sys
        print(f'[dag_executor] 配置加载失败: {e}', file=sys.stderr)
        return {}

# ── 核心接口 ──────────────────────────────────────────────────

def get_sparse_config(regime: str, direction: str) -> dict:
    """获取指定体制+方向的稀疏激活配置

    Args:
        regime: 'CHOP_MID' / 'BULL_TREND' / 'BEAR_TREND' / etc.
        direction: 'LONG' / 'SHORT'

    Returns:
        {active_dims: set, sleep_dims: set, weights: dict, position_mult: float}
    """
    cfg = _load_config()
    regime = (regime or '').upper()
    direction = (direction or '').upper()

    regime_cfg = cfg.get(regime, {})
    dir_cfg = regime_cfg.get(direction, {})

    return {
        'active_dims': set(dir_cfg.get('active_dims', [])),
        'sleep_dims': set(dir_cfg.get('sleep_dims', [])),
        'weights': dir_cfg.get('weights', {}),
        'position_mult': float(dir_cfg.get('position_mult', 1.0)),
    }

# ── 稀疏激活后处理 ────────────────────────────────────────────

# 所有维度名（固定23个）
_ALL_DIMS = [f's{i}' for i in range(1, 23)] + ['s5b']
_ALL_DIMS = sorted(set(_ALL_DIMS))

def apply_sparse_activation(dim_scores: dict, regime: str, direction: str,
                            raw_score: int = 0) -> dict:
    """对s1-s22做稀疏激活过滤

    Args:
        dim_scores: {'s1': 5, 's2': 13, 's3': 20, ...} 各维度原始分数
        regime: 当前体制
        direction: 信号方向 LONG/SHORT
        raw_score: Block A/B/C累加的原始总分（用于对比）

    Returns:
        {
            'dims': {dim: filtered_score},  # 过滤后各维度分数
            'score': int,                   # 加权后新总分
            'raw_score': int,               # 原始总分（对比用）
            'active_dims': list,
            'sleep_dims': list,
            'position_mult': float,
            'weights': dict,
            'applied': bool,                # 是否实际执行了过滤
        }
    """
    cfg = get_sparse_config(regime, direction)
    active = cfg['active_dims']
    sleep = cfg['sleep_dims']
    weights = cfg['weights']

    # 如果config为空（体制未配置），不过滤
    if not active and not sleep:
        return {
            'dims': dict(dim_scores),
            'score': raw_score,
            'raw_score': raw_score,
            'active_dims': [], 'sleep_dims': [],
            'position_mult': 1.0, 'weights': {},
            'applied': False,
        }

    # 处理 'all' 关键字 = 全部休眠
    if 'all' in sleep:
        sleep = set(_ALL_DIMS)
        active = set()

    new_dims = {}
    new_score = 0

    for dim in _ALL_DIMS:
        raw = float(dim_scores.get(dim, 0))

        if dim in sleep:
            new_dims[dim] = 0
        elif dim in active:
            w = float(weights.get(dim, 1.0))
            adjusted = round(raw * w, 2)
            new_dims[dim] = adjusted
            new_score += adjusted
        else:
            # 未在config中声明的维度：保持原样
            new_dims[dim] = raw
            new_score += raw

    return {
        'dims': new_dims,
        'score': int(round(new_score)),
        'raw_score': raw_score,
        'active_dims': sorted(active),
        'sleep_dims': sorted(sleep),
        'position_mult': cfg['position_mult'],
        'weights': weights,
        'applied': True,
    }

# ── 诊断接口 ──────────────────────────────────────────────────

def dump_config() -> str:
    """打印当前配置（诊断用）"""
    cfg = _load_config()
    lines = []
    for regime in sorted(k for k in cfg if not k.startswith('_')):
        for direction in ('LONG', 'SHORT'):
            d = cfg[regime].get(direction, {})
            active = d.get('active_dims', [])
            sleep = d.get('sleep_dims', [])
            mult = d.get('position_mult', 1.0)
            lines.append(f'{regime:16s} {direction:5s} | active={len(active):2d} sleep={len(sleep):2d} mult={mult:.2f}')
    return '\n'.join(lines)

if __name__ == '__main__':
    # 自测
    print('=== DAG Executor 自测 ===')
    print(dump_config())
    print()

    # 模拟CHOP_MID LONG
    test_dims = {f's{i}': 10 for i in range(1, 23)}
    test_dims['s5b'] = 8
    result = apply_sparse_activation(test_dims, 'CHOP_MID', 'LONG', raw_score=228)
    print(f'CHOP_MID LONG:')
    print(f'  raw_score={result["raw_score"]} → score={result["score"]}')
    print(f'  active={result["active_dims"]}')
    print(f'  sleep={result["sleep_dims"]}')
    print(f'  position_mult={result["position_mult"]}')
    print(f'  applied={result["applied"]}')
    print(f'  s1={result["dims"]["s1"]} (sleep→0)')
    print(f'  s3={result["dims"]["s3"]} (active×1.5={10*1.5})')

    # 模拟BEAR_RECOVERY SHORT（应全部sleep）
    result2 = apply_sparse_activation(test_dims, 'BEAR_RECOVERY', 'SHORT', raw_score=228)
    print(f'\nBEAR_RECOVERY SHORT:')
    print(f'  score={result2["score"]} (应=0)')
    print(f'  position_mult={result2["position_mult"]} (应=0.0)')

    print('\n✅ DAG Executor自测通过')
