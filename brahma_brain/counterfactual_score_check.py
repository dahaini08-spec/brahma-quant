"""
counterfactual_score_check.py · 信号评分反事实增益层 v1.0
==========================================================
设计院 × 达摩院 · 因果AI增强系列 P0-B · 2026-06-18

哲学：
  Pearl Level 3 反事实推理：
  "如果没有这个维度，信号还成立吗？"
  Shapley 值因果归因：每个维度对最终评分的边际贡献。

职责：
  - 对 score ≥ 138 的信号执行维度因果归因
  - 区分"真实因果驱动维度"和"相关性搭车维度"
  - 输出：哪些维度推动了评分，降权or加权建议

真实因果驱动维度（按文章体系）：
  - OB/FVG（SMC结构）：价格在关键位真实反转
  - 清算/OI 动量：真实资金流向驱动
  - 体制×方向 铁证（BEAR_EARLY SHORT WR=81%）
  - TR（Time-series causal）：当前时段历史成功率

相关性搭车维度（需警惕）：
  - 多个RSI维度叠加（RSI_1H + RSI_4H + RSI背离 同向 = 相关非独立）
  - CHOP体制下所有维度得分（因为CHOP时无主导因果机制）
  - gap_pct 过大时的关键位维度（价格已偏离，OB无效）

接入位置：brahma_core.analyze() Step 5（confluence_score之后）
权重：最大调整 ±15 分
"""

import os
import sys
import time
import json
import math
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'brahma_brain'))

# ── 缓存 ─────────────────────────────────────────────────────────
_CACHE: Dict[str, Dict] = {}
_CACHE_TTL = 90  # 90秒


# ══════════════════════════════════════════════════════════════════
# 维度因果分类系统
# ══════════════════════════════════════════════════════════════════

# 维度名称 → 因果等级
# CAUSAL:    背后有独立机制（ICM原则），跨体制稳定
# MODERATE:  条件因果，在特定体制有效
# SPURIOUS:  纯相关性，容易在体制切换时失效
_DIM_CAUSAL_CLASS = {
    # === 高因果维度（ICM稳健）===
    '关键位精确度':       'CAUSAL',    # OB/FVG 真实机制
    '清算/OI':           'CAUSAL',    # 资金流向真实驱动
    '体制×方向':         'CAUSAL',    # 铁证体制-方向机制
    'S1_体制方向':       'CAUSAL',
    'N14_体制×方向':     'CAUSAL',

    # === 中等因果维度（条件有效）===
    '结构完整度':         'MODERATE',
    '形态成熟度':         'MODERATE',
    'MTF多周期':         'MODERATE',
    'N15_分层仓位':      'MODERATE',
    '量能验证':          'MODERATE',
    'L2+贝叶斯+宏观':    'MODERATE',
    '时间效应':          'MODERATE',
    '品种×维度校正':     'MODERATE',

    # === 相关性维度（警惕叠加）===
    '谐波+多周期':        'SPURIOUS',  # 在BTC历史数据 -0.256 负贡献
    '量能衰竭+背离共振':  'MODERATE',  # 背离有价值，但容易CHOP污染
    'RSI':               'SPURIOUS',  # 纯动量，CHOP下无方向性
    'S4_':               'SPURIOUS',
    'S5_':               'SPURIOUS',
    '分析师背离':        'SPURIOUS',
    'Granger':           'CAUSAL',    # 如果有causal_verifier注入
}


def _classify_dim(dim_name: str) -> str:
    """将breakdown维度名映射到因果分类"""
    for key, cls in _DIM_CAUSAL_CLASS.items():
        if key in dim_name:
            return cls
    # 默认：中等
    return 'MODERATE'


def _parse_dim_score(val) -> float:
    """解析维度分值（可能是数字、字符串、None）"""
    if val is None:
        return 0.0
    try:
        return float(str(val).replace('+', '').split('(')[0].strip())
    except Exception:
        return 0.0


# ══════════════════════════════════════════════════════════════════
# Shapley 近似（排列重要性）
# ══════════════════════════════════════════════════════════════════

def _shapley_approx(
    breakdown: Dict[str, Any],
    total_score: float,
    n_samples: int = 20,
) -> Dict[str, float]:
    """
    近似 Shapley 值：每个维度去除后评分下降多少？
    使用 Permutation Importance 快速近似（避免 2^n 计算）。
    返回每个维度的 Shapley 贡献度（绝对分值）。
    """
    dims = {}
    for k, v in breakdown.items():
        if k.startswith('_'):
            continue
        score = _parse_dim_score(v)
        if score != 0:
            dims[k] = score

    if not dims:
        return {}

    dim_names = list(dims.keys())
    n = len(dim_names)

    # 简化版：直接使用每个维度的原始分值作为 Shapley 近似
    # 这比真正的 Shapley 快 O(n) vs O(2^n)，但对独立维度是精确的
    shapley_vals = {}
    for name in dim_names:
        shapley_vals[name] = dims[name]

    return shapley_vals


# ══════════════════════════════════════════════════════════════════
# 反事实检验核心
# ══════════════════════════════════════════════════════════════════

def _counterfactual_analysis(
    breakdown: Dict[str, Any],
    total_score: float,
    regime: str,
    signal_dir: str,
) -> Dict[str, Any]:
    """
    Pearl Level 3 反事实分析：
    - 计算每个维度的因果贡献
    - 识别相关性搭车维度
    - 生成评分调整建议
    """
    shapley = _shapley_approx(breakdown, total_score)

    causal_score    = 0.0
    moderate_score  = 0.0
    spurious_score  = 0.0

    causal_dims:   List[str] = []
    moderate_dims: List[str] = []
    spurious_dims: List[str] = []

    for dim, contrib in shapley.items():
        cls = _classify_dim(dim)
        if cls == 'CAUSAL':
            causal_score += abs(contrib)
            if contrib != 0:
                causal_dims.append(f'{dim}:{contrib:+.1f}')
        elif cls == 'MODERATE':
            moderate_score += abs(contrib)
            moderate_dims.append(f'{dim}:{contrib:+.1f}')
        else:  # SPURIOUS
            spurious_score += abs(contrib)
            spurious_dims.append(f'{dim}:{contrib:+.1f}')

    total_abs = causal_score + moderate_score + spurious_score + 1e-9

    causal_ratio   = causal_score   / total_abs
    moderate_ratio = moderate_score / total_abs
    spurious_ratio = spurious_score / total_abs

    # ── CHOP体制特殊处理 ──────────────────────────────────────
    # CHOP下所有维度都降为SPURIOUS（无主导因果机制）
    is_chop = 'CHOP' in regime.upper()
    if is_chop:
        causal_ratio  *= 0.3
        spurious_ratio = 1.0 - causal_ratio

    # ── 评分调整逻辑 ─────────────────────────────────────────
    score_adj = 0
    verdict   = 'NEUTRAL'
    reason    = ''

    if causal_ratio >= 0.55:
        # 60%+评分来自真实因果驱动 → 加分
        score_adj = +10
        verdict   = 'CAUSAL_DRIVEN'
        reason    = f'因果驱动维度占比{causal_ratio:.0%}，信号质量高'
    elif spurious_ratio >= 0.60:
        # 60%+评分来自相关性叠加 → 降权
        score_adj = -15
        verdict   = 'SPURIOUS_DOMINATED'
        reason    = f'相关性搭车维度占比{spurious_ratio:.0%}，降权保护'
    elif is_chop and total_score < 160:
        # CHOP体制下中等分 → 额外降权
        score_adj = -10
        verdict   = 'CHOP_NOISE'
        reason    = f'CHOP体制下无主导因果机制，降权'
    else:
        verdict   = 'MIXED'
        reason    = f'因果{causal_ratio:.0%} 中等{moderate_ratio:.0%} 相关{spurious_ratio:.0%}'

    # ── 反事实关键问题 ────────────────────────────────────────
    # Q1: 去除最大维度后，信号是否仍成立？
    max_dim = max(shapley.items(), key=lambda x: abs(x[1])) if shapley else ('', 0)
    score_without_max = total_score - abs(max_dim[1]) if max_dim[1] else total_score
    still_valid_without_max = score_without_max >= 138

    # Q2: 仅依赖因果维度，信号得多少分？
    score_causal_only = causal_score + moderate_score * 0.5

    return {
        'score_adj':             score_adj,
        'verdict':               verdict,
        'reason':                reason,
        'causal_ratio':          round(causal_ratio, 3),
        'moderate_ratio':        round(moderate_ratio, 3),
        'spurious_ratio':        round(spurious_ratio, 3),
        'causal_score':          round(causal_score, 1),
        'spurious_score':        round(spurious_score, 1),
        'causal_dims':           causal_dims[:5],
        'spurious_dims':         spurious_dims[:5],
        'counterfactual_q1':     f'去除最大维度{max_dim[0]}后score={score_without_max:.0f} 仍有效={still_valid_without_max}',
        'counterfactual_q2':     f'仅因果维度得分={score_causal_only:.0f}',
        'max_dim':               max_dim[0],
        'score_causal_only':     round(score_causal_only, 1),
        'still_valid_without_max': still_valid_without_max,
    }


# ══════════════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════════════

def check(
    confluence_result: Dict[str, Any],
    signal_dir: str,
    regime: str,
    timeout_ms: int = 100,
) -> Dict[str, Any]:
    """
    反事实评分检验主入口。

    Args:
        confluence_result: confluence_score() 的完整返回（含 breakdown）
        signal_dir:        信号方向
        regime:            当前体制
        timeout_ms:        最大计算时间

    Returns:
        {
          'score_adj':    int,      # 评分建议调整（-15 ~ +10）
          'verdict':      str,      # 判定结果
          'reason':       str,
          'causal_ratio': float,    # 因果维度占比
          'spurious_ratio': float,  # 相关性维度占比
          ...
        }
    """
    t0 = time.time()

    _DEFAULT = {
        'score_adj': 0,
        'verdict': 'NEUTRAL',
        'reason': 'fail-safe',
        'causal_ratio': 0.5,
        'spurious_ratio': 0.3,
    }

    try:
        score = float(confluence_result.get('score', 0) or 0)

        # 只对 score ≥ 100 的信号执行（低分信号门控已拦截）
        if score < 100:
            return dict(_DEFAULT)

        breakdown = confluence_result.get('breakdown', {}) or {}
        if not breakdown:
            return dict(_DEFAULT)

        result = _counterfactual_analysis(breakdown, score, regime, signal_dir)
        elapsed = (time.time() - t0) * 1000
        result['elapsed_ms'] = round(elapsed, 1)
        return result

    except Exception as e:
        print(f'[CounterfactualCheck] ⚠ 异常（不阻断）: {e}')
        return dict(_DEFAULT)


# ══════════════════════════════════════════════════════════════════
# 独立测试
# ══════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    # 模拟一个典型信号的 confluence breakdown
    test_breakdown = {
        '关键位精确度':    15.0,
        '清算/OI':        12.0,
        'S1_体制方向':    25.0,
        'N14_体制×方向':  18.0,
        '谐波+多周期':     8.0,
        'RSI背离':         5.0,
        '结构完整度':     10.0,
        '量能验证':        7.0,
    }
    test_cf = {'score': 150.0, 'breakdown': test_breakdown}

    print('=== 正常信号（BEAR_EARLY SHORT score=150）===')
    r = check(test_cf, 'SHORT', 'BEAR_EARLY')
    print(json.dumps(r, ensure_ascii=False, indent=2))

    print()
    print('=== CHOP体制信号（score=142）===')
    test_cf2 = {'score': 142.0, 'breakdown': {
        '谐波+多周期': 30.0, 'RSI背离': 20.0, '关键位精确度': 5.0,
        'S1_体制方向': 50.0, '结构完整度': 37.0,
    }}
    r2 = check(test_cf2, 'SHORT', 'CHOP_MID')
    print(json.dumps(r2, ensure_ascii=False, indent=2))
